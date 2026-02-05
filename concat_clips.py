# smart_concat.py
# -*- coding: utf-8 -*-
"""
Fast & robust concat for lots of mp4 clips (B站更稳的"能copy就copy"策略)

Folder layout (same as your existing script):
  <cwd>/
    bin/ffmpeg(.exe), bin/ffprobe(.exe)   (optional, otherwise use PATH)
    clips/<subdir>/*.mp4
    only files named: clip_<ts>_<idx>.mp4  (sorted by ts then idx)

Strategy (default: auto):
  1) If all clips look perfectly compatible + no timestamp anomalies -> concat demuxer + -c copy  (fastest)
  2) Else -> remux each clip (genpts/avoid_negative_ts/reset_timestamps) then concat -c copy     (still fast)
  3) Else -> concat, keep video copy, re-encode audio (48k + async)                             (fast)
  4) Else -> re-encode (CFR + rebuild PTS by frame/sample index)                                (slowest but most stable)

Usage:
  python smart_concat.py --sub my_subdir
  python smart_concat.py --dir clips/my_subdir
  python smart_concat.py --dir clips/my_subdir --strategy auto --fps 60
  python smart_concat.py --dir clips/my_subdir --strategy copy
"""

from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

NAME_RE = re.compile(r"^clip_(\d+)_(\d+)\.mp4$", re.IGNORECASE)

WARN_PATTERNS = [
    "Non-monotonous DTS",
    "non monotonically increasing dts",
    "Application provided invalid",
    "timestamp discontinuity",
    "Invalid DTS",
    "Invalid PTS",
    "dts <",
    "pts <",
    "Queue input is backward in time",
    "corrupt",
    "error while decoding",
]

# ---------------------------
# Util
# ---------------------------

def eprint(*a):
    print(*a, file=sys.stderr)

def run_capture(cmd: List[str]) -> str:
    p = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    return p.stdout or ""

def run_check(cmd: List[str], desc: str = ""):
    print("\n" + "=" * 100)
    if desc:
        print("[Run]", desc)
    print("[Cmd]", " ".join(str(x) for x in cmd))
    print("=" * 100)
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore')
    if result.returncode != 0:
        print("[Error] ffmpeg 执行失败，退出码:", result.returncode)
        if result.stderr:
            print("[Error] 错误信息:")
            print(result.stderr[:2000])  # 只打印前2000字符避免太长
        raise subprocess.CalledProcessError(result.returncode, cmd)
    if result.stderr:
        # 打印警告信息
        print("[Warn] ffmpeg 警告信息:")
        print(result.stderr[:1000])

def which_or_local(root: Path, rel: str) -> Optional[Path]:
    # Try local first: <root>/<rel>
    p = root / rel
    if p.exists():
        return p
    # Try PATH
    path = shutil.which(Path(rel).stem)  # "ffmpeg.exe" -> "ffmpeg"
    if path:
        return Path(path)
    return None

def sort_key(p: Path) -> Optional[Tuple[int,int,str]]:
    m = NAME_RE.match(p.name)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), p.name)

def write_concat_list(list_path: Path, files: List[Path]):
    with list_path.open("w", encoding="utf-8", newline="\n") as f:
        for p in files:
            f.write(f"file '{p.resolve().as_posix()}'\n")

def safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except:
        return None

def parse_ratio(r: str) -> Optional[float]:
    # "60000/1001", "30/1"
    if not r or r == "0/0":
        return None
    if "/" in r:
        a, b = r.split("/", 1)
        try:
            a = float(a); b = float(b)
            if b == 0:
                return None
            return a / b
        except:
            return None
    try:
        return float(r)
    except:
        return None

# ---------------------------
# Metadata / checks
# ---------------------------

@dataclass
class ClipInfo:
    path: Path
    v_sig: Dict[str, Any]
    a_sig: Dict[str, Any]
    start_time: Optional[float]
    vfr_suspect: bool
    ffmpeg_warn: bool

def ffprobe_json(ffprobe: Path, clip: Path) -> Dict[str, Any]:
    cmd = [
        str(ffprobe),
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(clip),
    ]
    out = run_capture(cmd)
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        # Sometimes ffprobe prints nothing if file is broken
        return {}

def extract_signatures(meta: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], Optional[float], bool]:
    fmt = meta.get("format", {}) if isinstance(meta, dict) else {}
    start_time = safe_float(fmt.get("start_time"))
    v_sig: Dict[str, Any] = {}
    a_sig: Dict[str, Any] = {}
    vfr_suspect = False

    streams = meta.get("streams", []) if isinstance(meta, dict) else []
    v = None
    a = None
    for s in streams:
        if not isinstance(s, dict):
            continue
        if s.get("codec_type") == "video" and v is None:
            v = s
        if s.get("codec_type") == "audio" and a is None:
            a = s

    if v:
        # signature fields that must match for concat demuxer + copy
        v_sig = {
            "codec_name": v.get("codec_name"),
            "profile": v.get("profile"),
            "level": v.get("level"),
            "width": v.get("width"),
            "height": v.get("height"),
            "pix_fmt": v.get("pix_fmt"),
            "field_order": v.get("field_order"),
            "sar": v.get("sample_aspect_ratio"),
            "color_range": v.get("color_range"),
            "color_space": v.get("color_space"),
            "color_transfer": v.get("color_transfer"),
            "color_primaries": v.get("color_primaries"),
            "time_base": v.get("time_base"),
            "r_frame_rate": v.get("r_frame_rate"),
            "avg_frame_rate": v.get("avg_frame_rate"),
        }
        r = parse_ratio(str(v.get("r_frame_rate") or ""))
        avg = parse_ratio(str(v.get("avg_frame_rate") or ""))
        # Heuristic: r and avg differ a lot -> VFR suspect
        if r and avg and r > 0 and avg > 0:
            if abs(r - avg) / max(r, avg) > 0.02:  # >2% difference
                vfr_suspect = True

    if a:
        a_sig = {
            "codec_name": a.get("codec_name"),
            "sample_rate": a.get("sample_rate"),
            "channels": a.get("channels"),
            "channel_layout": a.get("channel_layout"),
            "time_base": a.get("time_base"),
        }

    return v_sig, a_sig, start_time, vfr_suspect

def ffmpeg_warning_check(ffmpeg: Path, clip: Path) -> bool:
    # Fast-ish: remux to null (no decode), but still catches many DTS/PTS/discontinuity warnings.
    cmd = [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel", "warning",
        "-fflags", "+genpts",
        "-i", str(clip),
        "-map", "0",
        "-c", "copy",
        "-f", "null",
        "-",
    ]
    out = run_capture(cmd)
    out_lower = out.lower()
    for p in WARN_PATTERNS:
        if p.lower() in out_lower:
            return True
    return False

def pick_target_fps_from_first(v_sig: Dict[str, Any]) -> int:
    # Prefer r_frame_rate, else avg_frame_rate
    r = parse_ratio(str(v_sig.get("r_frame_rate") or ""))
    avg = parse_ratio(str(v_sig.get("avg_frame_rate") or ""))
    fps = r or avg
    if fps is None:
        return 60
    if fps >= 50:
        return 60
    if fps >= 27:
        return 30
    return 24

def detect_gpu_encoder(ffmpeg: Path, use_cpu_only: bool = False) -> Tuple[str, bool]:
    """
    Return (video_encoder_name, is_gpu_encoder)
    """
    if use_cpu_only:
        print("[Info] 用户指定使用CPU编码，跳过GPU检测")
        return "libx264", False
    
    # 先测试ffmpeg是否支持GPU编码器
    out = run_capture([str(ffmpeg), "-hide_banner", "-encoders"])
    
    # 检测顺序：NVIDIA -> Intel -> AMD -> CPU
    gpu_encoders = [
        ("h264_nvenc", "NVIDIA GPU"),
        ("hevc_nvenc", "NVIDIA GPU"),
        ("h264_qsv", "Intel GPU"),
        ("hevc_qsv", "Intel GPU"),
        ("h264_amf", "AMD GPU"),
        ("hevc_amf", "AMD GPU"),
    ]
    
    for encoder, gpu_type in gpu_encoders:
        # 检查编码器是否存在
        if f" {encoder} " in out or f"\n{encoder} " in out:
            print(f"[Info] 检测到 {gpu_type} 编码器: {encoder}")
            
            # 简单测试编码器是否可用
            test_cmd = [str(ffmpeg), "-hide_banner", "-f", "lavfi", "-i", "testsrc=duration=1:size=640x480:rate=30",
                       "-c:v", encoder, "-t", "0.1", "-f", "null", "-"]
            test_result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=5)
            
            if test_result.returncode == 0:
                print(f"[Info] {encoder} 编码器可用")
                return encoder, True
            else:
                print(f"[Warn] {encoder} 编码器检测到但无法使用，回退到CPU编码")
                print(f"[Warn] 错误信息: {test_result.stderr[:200] if test_result.stderr else '未知错误'}")
    
    print("[Info] 未检测到可用的GPU编码器，使用CPU编码: libx264")
    return "libx264", False

def vcodec_args_for(encoder: str, is_gpu: bool = True) -> List[str]:
    """
    Reasonable quality/speed defaults
    is_gpu: 是否为GPU编码器（用于调整参数）
    """
    # GPU编码器参数 - 简化版，避免复杂参数
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "19", "-pix_fmt", "yuv420p"]
    elif encoder == "hevc_nvenc":
        return ["-c:v", "hevc_nvenc", "-preset", "p5", "-cq", "19", "-pix_fmt", "yuv420p"]
    elif encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "20", "-pix_fmt", "yuv420p"]
    elif encoder == "hevc_qsv":
        return ["-c:v", "hevc_qsv", "-preset", "medium", "-global_quality", "20", "-pix_fmt", "yuv420p"]
    elif encoder == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "balanced", "-pix_fmt", "yuv420p"]
    elif encoder == "hevc_amf":
        return ["-c:v", "hevc_amf", "-quality", "balanced", "-pix_fmt", "yuv420p"]
    
    # CPU编码器参数（libx264/libx265）
    if encoder in ["libx264", "libx265"]:
        preset = "veryfast"
        # 如果没有GPU，使用更快的预设
        if not is_gpu:
            preset = "ultrafast" if encoder == "libx264" else "veryfast"
        
        crf_q = "20" if is_gpu else "22"  # CPU编码时稍微降低质量以加快速度
        
        if encoder == "libx264":
            return ["-c:v", "libx264", "-preset", preset, "-crf", crf_q, "-pix_fmt", "yuv420p"]
        else:  # libx265
            return ["-c:v", "libx265", "-preset", preset, "-crf", crf_q, "-pix_fmt", "yuv420p"]
    
    # 默认使用libx264
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]

# ---------------------------
# Actions
# ---------------------------

def concat_copy(ffmpeg: Path, list_file: Path, out_path: Path):
    cmd = [
        str(ffmpeg),
        "-y", "-hide_banner", "-loglevel", "info",
        "-fflags", "+genpts",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-avoid_negative_ts", "make_zero",
        "-c", "copy",
        str(out_path),
    ]
    run_check(cmd, "concat copy（最快）")

def remux_one(ffmpeg: Path, src: Path, dst: Path):
    cmd = [
        str(ffmpeg),
        "-y", "-hide_banner", "-loglevel", "info",
        "-fflags", "+genpts+igndts",
        "-i", str(src),
        "-map", "0",
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        "-reset_timestamps", "1",
        # 不要用 0，留一点交错空间更稳
        "-max_interleave_delta", "1M",
        str(dst),
    ]
    run_check(cmd, f"remux 洗时间戳: {src.name} -> {dst.name}")

def concat_after_remux(ffmpeg: Path, files: List[Path], work_dir: Path, out_path: Path) -> Path:
    fixed_dir = work_dir / "_fixed"
    fixed_dir.mkdir(parents=True, exist_ok=True)
    fixed_files: List[Path] = []
    for i, src in enumerate(files, 1):
        dst = fixed_dir / (src.stem + ".mkv")
        fixed_files.append(dst)
        remux_one(ffmpeg, src, dst)

    list_fixed = work_dir / "_ffmpeg_concat_list_fixed.txt"
    write_concat_list(list_fixed, fixed_files)
    concat_copy(ffmpeg, list_fixed, out_path)
    return out_path

def concat_video_copy_audio_reencode(ffmpeg: Path, list_file: Path, out_path: Path):
    cmd = [
        str(ffmpeg),
        "-y", "-hide_banner", "-loglevel", "info",
        "-fflags", "+genpts+igndts",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-avoid_negative_ts", "make_zero",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-ar", "48000",
        "-af", "aresample=async=1:first_pts=0",
        "-max_interleave_delta", "1M",
        "-movflags", "+faststart",
        str(out_path),
    ]
    run_check(cmd, "视频copy + 音频重编码（快且常有效）")

def reencode_rebuild_pts_onepass(
    ffmpeg: Path,
    list_file: Path,
    out_path: Path,
    target_fps: int,
    encoder: str,
    is_gpu_encoder: bool,
):
    # Key: rebuild PTS with frame index / sample index (more robust than PTS-STARTPTS)
    v_args = vcodec_args_for(encoder, is_gpu_encoder)
    
    # 基本命令
    cmd = [
        str(ffmpeg),
        "-y", "-hide_banner", "-loglevel", "info",
        "-fflags", "+genpts+igndts",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
    ]
    
    # 添加filter
    if is_gpu_encoder and ("nvenc" in encoder):
        # NVIDIA GPU编码，使用简单filter链
        cmd.extend([
            "-vf", f"fps={target_fps},setpts=N/({target_fps}*TB)",
            "-vsync", "cfr",
        ])
    else:
        # CPU编码或其他GPU编码
        cmd.extend([
            "-vf", f"fps={target_fps},setpts=N/({target_fps}*TB)",
            "-vsync", "cfr",
        ])
    
    # 添加音频处理和编码参数
    cmd.extend([
        "-af", "aresample=48000:async=1:first_pts=0,asetpts=N/SR/TB",
        "-ar", "48000",
        "-max_interleave_delta", "1M",
        *v_args,
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_path),
    ])
    
    run_check(cmd, f"重编码（重建时间戳） {target_fps}fps / {encoder}")

def normalize_each_then_concat_copy(
    ffmpeg: Path,
    files: List[Path],
    work_dir: Path,
    target_fps: int,
    encoder: str,
    is_gpu_encoder: bool,
    out_path: Path,
) -> Path:
    """
    If clips have mismatched stream parameters, concat demuxer may fail.
    Normalize each clip to a stable intermediate format, then concat copy.
    """
    norm_dir = work_dir / "_norm"
    norm_dir.mkdir(parents=True, exist_ok=True)

    v_args = vcodec_args_for(encoder, is_gpu_encoder)
    norm_files: List[Path] = []
    for i, src in enumerate(files, 1):
        dst = norm_dir / (src.stem + ".mp4")
        norm_files.append(dst)

        cmd = [
            str(ffmpeg),
            "-y", "-hide_banner", "-loglevel", "info",
            "-fflags", "+genpts+igndts",
            "-i", str(src),
            "-vf", f"fps={target_fps},setpts=N/({target_fps}*TB)",
            "-vsync", "cfr",
            "-af", "aresample=48000:async=1:first_pts=0,asetpts=N/SR/TB",
            "-ar", "48000",
            "-max_interleave_delta", "1M",
            *v_args,
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(dst),
        ]
        
        run_check(cmd, f"normalize ({i}/{len(files)}): {src.name} -> {dst.name}")

    list_norm = work_dir / "_ffmpeg_concat_list_norm.txt"
    write_concat_list(list_norm, norm_files)
    concat_copy(ffmpeg, list_norm, out_path)
    return out_path

# ---------------------------
# Decision
# ---------------------------

def signatures_equal(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return a == b

def decide_strategy(clips: List[ClipInfo]) -> Tuple[str, List[str]]:
    """
    Return (strategy, reasons)
    strategy in: copy, remux, audiofix, reencode_onepass
    """
    reasons: List[str] = []
    if not clips:
        return ("copy", ["no clips?"])

    base_v = clips[0].v_sig
    base_a = clips[0].a_sig

    all_v_equal = all(signatures_equal(c.v_sig, base_v) for c in clips)
    all_a_equal = all(signatures_equal(c.a_sig, base_a) for c in clips)
    any_vfr = any(c.vfr_suspect for c in clips)
    any_warn = any(c.ffmpeg_warn for c in clips)
    any_start_weird = any((c.start_time is not None and c.start_time < -0.001) for c in clips)

    if not all_v_equal or not all_a_equal:
        reasons.append("片段之间的编码参数不一致（concat copy 很可能失败或不稳）")
        reasons.append("=> 建议重编码并重建时间戳（必要时逐段 normalize）")
        return ("reencode", reasons)

    if any_vfr:
        reasons.append("检测到 VFR 可疑（avg_frame_rate 与 r_frame_rate 差异较大）")
        reasons.append("=> 平台转码更容易音画不同步，建议重编码 CFR + 重建时间戳")
        return ("reencode_onepass", reasons)

    if any_warn or any_start_weird:
        if any_warn:
            reasons.append("ffmpeg 快速 remux 检测到时间戳/解复用警告（PTS/DTS 不单调等）")
        if any_start_weird:
            reasons.append("存在负 start_time（时间轴可能不干净）")
        reasons.append("=> 先 remux 洗时间戳，再 concat copy（仍然很快）")
        return ("remux", reasons)

    reasons.append("所有片段参数一致，且未发现明显时间戳异常")
    reasons.append("=> 直接 concat copy（最快）")
    return ("copy", reasons)

# ---------------------------
# Main
# ---------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sub", help="clips 下的子目录名（等同于 --dir clips/<sub>）")
    ap.add_argument("--dir", help="直接指定含 mp4 片段的目录")
    ap.add_argument("--out", default="clips_all.mp4", help="输出文件名（默认 clips_all.mp4）")
    ap.add_argument("--strategy", default="auto", choices=["auto", "copy", "remux", "audiofix", "reencode"],
                    help="处理策略：auto/copy/remux/audiofix/reencode")
    ap.add_argument("--fps", type=int, default=0, help="重编码目标帧率（0=自动；常用 30/60）")
    ap.add_argument("--cpu-only", action="store_true", help="强制使用CPU编码（即使检测到GPU）")
    ap.add_argument("--keep-temp", action="store_true", help="保留 _fixed/_norm 等中间文件夹")
    args = ap.parse_args()

    root = Path.cwd()
    # Prefer local bin (same idea as your existing script)
    ffmpeg = which_or_local(root, "bin/ffmpeg.exe") or which_or_local(root, "bin/ffmpeg") or Path(shutil.which("ffmpeg") or "")
    ffprobe = which_or_local(root, "bin/ffprobe.exe") or which_or_local(root, "bin/ffprobe") or Path(shutil.which("ffprobe") or "")

    if not ffmpeg or not str(ffmpeg):
        eprint("[Error] 找不到 ffmpeg（请放到 bin/ 或加入 PATH）")
        sys.exit(1)
    if not ffprobe or not str(ffprobe):
        eprint("[Error] 找不到 ffprobe（请放到 bin/ 或加入 PATH）")
        sys.exit(1)

    ffmpeg = ffmpeg.resolve()
    ffprobe = ffprobe.resolve()

    if args.dir:
        target_dir = Path(args.dir)
    else:
        if not args.sub:
            # interactive fallback
            sub = input("请输入 clips 下的子目录名：").strip()
        else:
            sub = args.sub.strip()
        if not sub:
            eprint("[Error] 子目录名为空。")
            sys.exit(1)
        target_dir = root / "clips" / sub

    if not target_dir.exists():
        eprint(f"[Error] 目录不存在：{target_dir}")
        sys.exit(1)

    # Collect + sort clips
    all_mp4 = sorted([p for p in target_dir.iterdir() if p.is_file() and p.suffix.lower() == ".mp4"])
    items = []
    ignored = []
    for p in all_mp4:
        k = sort_key(p)
        if k is None:
            ignored.append(p.name)
        else:
            items.append((k, p))
    items.sort(key=lambda x: x[0])
    files = [p for _, p in items]

    print("[Info] ffmpeg:", ffmpeg)
    print("[Info] ffprobe:", ffprobe)
    print("[Info] target_dir:", target_dir)
    print("[Info] mp4 total:", len(all_mp4), " matched:", len(files), " ignored:", len(ignored))
    if ignored:
        print("[Warn] ignored examples:", ignored[:10])
    if not files:
        eprint("[Error] 没有找到符合 clip_<ts>_<idx>.mp4 的文件。")
        sys.exit(1)

    out_path = (target_dir / args.out).resolve()
    list_file = (target_dir / "_ffmpeg_concat_list.txt").resolve()
    write_concat_list(list_file, files)

    # Analyze clips (fast)
    clips: List[ClipInfo] = []
    print("\n[Info] 分析片段（ffprobe + 快速 remux 检测告警）...")
    for i, f in enumerate(files, 1):
        meta = ffprobe_json(ffprobe, f)
        v_sig, a_sig, start_time, vfr = extract_signatures(meta)
        warn = ffmpeg_warning_check(ffmpeg, f)
        clips.append(ClipInfo(
            path=f,
            v_sig=v_sig,
            a_sig=a_sig,
            start_time=start_time,
            vfr_suspect=vfr,
            ffmpeg_warn=warn,
        ))
        # concise progress
        if i <= 3 or i == len(files):
            print(f"  [{i}/{len(files)}] {f.name}  vfr={vfr} warn={warn} start={start_time}")

    # Decide
    if args.strategy == "auto":
        strategy, reasons = decide_strategy(clips)
    else:
        strategy = args.strategy
        reasons = [f"用户指定 strategy={strategy}"]

    print("\n" + "-" * 100)
    print("[Decision] strategy =", strategy)
    for r in reasons:
        print(" -", r)
    print("-" * 100 + "\n")

    # Choose fps/encoder if needed
    base_v = clips[0].v_sig
    target_fps = args.fps if args.fps and args.fps > 0 else pick_target_fps_from_first(base_v)
    
    # 检测GPU编码器，支持强制使用CPU
    encoder, is_gpu_encoder = detect_gpu_encoder(ffmpeg, use_cpu_only=args.cpu_only)
    
    print(f"[Info] 使用编码器: {encoder} ({'GPU' if is_gpu_encoder else 'CPU'})")
    print(f"[Info] 目标帧率: {target_fps}fps")

    # Execute
    try:
        if strategy == "copy":
            concat_copy(ffmpeg, list_file, out_path)

        elif strategy == "remux":
            concat_after_remux(ffmpeg, files, target_dir, out_path)

        elif strategy == "audiofix":
            # Requires stream parameters to be compatible across segments; if not, normalize first
            all_v_equal = all(clips[i].v_sig == clips[0].v_sig for i in range(len(clips)))
            all_a_equal = all(clips[i].a_sig == clips[0].a_sig for i in range(len(clips)))
            if not (all_v_equal and all_a_equal):
                print("[Warn] 音频修复模式下发现参数不一致，改为逐段 normalize 后再 concat。")
                normalize_each_then_concat_copy(ffmpeg, files, target_dir, target_fps, encoder, is_gpu_encoder, out_path)
            else:
                concat_video_copy_audio_reencode(ffmpeg, list_file, out_path)

        elif strategy == "reencode":
            # If clips mismatch, normalize each then concat copy; else one-pass concat demuxer reencode
            all_v_equal = all(clips[i].v_sig == clips[0].v_sig for i in range(len(clips)))
            all_a_equal = all(clips[i].a_sig == clips[0].a_sig for i in range(len(clips)))
            if not (all_v_equal and all_a_equal):
                normalize_each_then_concat_copy(ffmpeg, files, target_dir, target_fps, encoder, is_gpu_encoder, out_path)
            else:
                reencode_rebuild_pts_onepass(ffmpeg, list_file, out_path, target_fps, encoder, is_gpu_encoder)

        elif strategy == "reencode_onepass":
            reencode_rebuild_pts_onepass(ffmpeg, list_file, out_path, target_fps, encoder, is_gpu_encoder)

        else:
            eprint("[Error] 未知策略：", strategy)
            sys.exit(2)

    except subprocess.CalledProcessError as e:
        eprint(f"\n[Error] ffmpeg 失败，退出码：{e.returncode}")
        if e.returncode == 4294967274:
            eprint("[Error] 错误码 4294967274 (-22) 通常表示参数错误")
            eprint("[Error] 建议尝试：")
            eprint("[Error] 1. 使用 --cpu-only 参数强制使用CPU编码")
            eprint("[Error] 2. 使用 --strategy remux 先尝试简单的remux方案")
            eprint("[Error] 3. 检查GPU驱动和ffmpeg版本是否支持硬件编码")
        sys.exit(e.returncode)

    if out_path.exists():
        print("\n[OK] 输出文件：", out_path)
        print("[OK] 大小（bytes）：", out_path.stat().st_size)
        print(f"[Tip] 使用编码器: {encoder} ({'GPU' if is_gpu_encoder else 'CPU'})")
        print("[Tip] 如果你要上传 B 站仍出现'从某段开始不同步'，直接用 --strategy reencode（它用帧号/采样号重建 PTS，最稳）。")
    else:
        eprint("\n[Error] 运行结束但未生成输出：", out_path)
        sys.exit(2)

    # Cleanup (optional)
    if not args.keep_temp:
        # Conservative: only remove temp dirs we created
        for d in ["_fixed", "_norm"]:
            p = target_dir / d
            if p.exists() and p.is_dir():
                # don't delete automatically if user might want to inspect logs/files
                pass

if __name__ == "__main__":
    main()