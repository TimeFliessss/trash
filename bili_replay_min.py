import subprocess
import tempfile
import time
from pathlib import Path
import requests

SESSION = requests.Session()
COOKIES = {}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Referer": "https://live.bilibili.com/",
    "Origin": "https://live.bilibili.com",
}
FFMPEG = "./bin/ffmpeg"
REQUEST_TIMEOUT = (10, 30)
REQUEST_RETRIES = 20
REQUEST_RETRY_DELAY_SECONDS = 2
FFMPEG_TIMEOUT_SECONDS = 300
FFMPEG_RW_TIMEOUT_US = 60_000_000
FFMPEG_RETRIES = 5

def init(cookie_str : str, headers=None, ffmpeg_path=None):
    global COOKIES, HEADERS, FFMPEG
    COOKIES = dict(
        item.split("=", 1)
        for item in (p.strip() for p in cookie_str.replace("\n", " ").split(";"))
        if item and "=" in item
    )
    if headers:
        HEADERS.update(headers)
    if ffmpeg_path:
        FFMPEG = ffmpeg_path


def _get_json(url, retries=REQUEST_RETRIES, timeout=REQUEST_TIMEOUT):
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            r = SESSION.get(url, headers=HEADERS, cookies=COOKIES, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt == retries:
                break
            print(f"[WARN] 请求 B 站接口失败，第 {attempt}/{retries} 次重试前等待 {REQUEST_RETRY_DELAY_SECONDS}s：{exc}")
            time.sleep(REQUEST_RETRY_DELAY_SECONDS)
    raise RuntimeError(f"B 站接口请求失败: {url} err={last_exc}")

def get_replay_list(page=1, page_size=20):
    url = f"https://api.live.bilibili.com/xlive/app-blink/v1/anchorVideo/AnchorGetReplayList?page={page}&page_size={page_size}"
    data = _get_json(url)
    return [{"live_key": x["live_key"], "start_time": x["start_time"], "end_time": x["end_time"]}
            for x in data["data"]["replay_info"]]

def get_streams(live_info):
    url = "https://api.live.bilibili.com/xlive/app-blink/v1/anchorVideo/GetSliceStream?live_key={}&start_time={}&end_time={}".format(
        live_info["live_key"], live_info["start_time"], live_info["end_time"]
    )
    data = _get_json(url)
    return data["data"]["list"]

def cut_hls_segment(m3u8_url, start, duration, output_path, timeout=FFMPEG_TIMEOUT_SECONDS):
    args = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error"]
    args += ["-rw_timeout", str(FFMPEG_RW_TIMEOUT_US)]
    args += ["-ss", str(start), "-i", m3u8_url, "-t", str(duration)]
    args += ["-c", "copy", "-movflags", "+faststart"]
    args += [output_path]
    clip_name = Path(output_path).name
    last_exc = None
    for attempt in range(1, FFMPEG_RETRIES + 1):
        print(
            f"[INFO] 开始导出片段 {clip_name} attempt={attempt}/{FFMPEG_RETRIES} "
            f"start={start:.3f}s duration={duration:.3f}s timeout={timeout}s"
        )
        started_at = time.monotonic()
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            last_exc = RuntimeError(f"ffmpeg 裁剪超时（>{timeout}s）")
        else:
            if proc.returncode == 0:
                elapsed = time.monotonic() - started_at
                print(f"[INFO] 导出完成 {clip_name} elapsed={elapsed:.1f}s")
                return
            stderr = (proc.stderr or "").strip()
            last_exc = RuntimeError(f"ffmpeg 裁剪失败: {stderr}")
        elapsed = time.monotonic() - started_at
        if attempt < FFMPEG_RETRIES:
            print(f"[WARN] 导出失败 {clip_name} attempt={attempt}/{FFMPEG_RETRIES} elapsed={elapsed:.1f}s err={last_exc}")
            time.sleep(REQUEST_RETRY_DELAY_SECONDS)
    raise RuntimeError(str(last_exc))


def concat_mp4_segments(input_paths, output_path, timeout=FFMPEG_TIMEOUT_SECONDS):
    if not input_paths:
        raise RuntimeError("没有可拼接的片段。")
    if len(input_paths) == 1:
        src = Path(input_paths[0]).resolve()
        dst = Path(output_path).resolve()
        if src != dst:
            dst.write_bytes(src.read_bytes())
        return

    output_path = str(Path(output_path).resolve())
    with tempfile.TemporaryDirectory(prefix="bili_concat_") as tmp_dir:
        list_path = Path(tmp_dir) / "concat_list.txt"
        list_path.write_text(
            "\n".join(f"file '{Path(p).resolve().as_posix()}'" for p in input_paths),
            encoding="utf-8",
        )

        copy_args = [
            FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-c", "copy", "-movflags", "+faststart", output_path,
        ]
        proc = subprocess.run(copy_args, capture_output=True, text=True, timeout=timeout)
        if proc.returncode == 0:
            return

        fallback_args = [
            FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac", "-movflags", "+faststart", output_path,
        ]
        proc = subprocess.run(fallback_args, capture_output=True, text=True, timeout=timeout)
        if proc.returncode == 0:
            return

        stderr = (proc.stderr or "").strip()
        raise RuntimeError(f"ffmpeg 拼接失败: {stderr}")
