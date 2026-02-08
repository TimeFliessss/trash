import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from bilibili_api import sync, video_uploader

import alert

from bilibili.bili_auth import (
    BILI_LOGIN_INFO,
    cookie_str_from_credential,
    load_credential,
    refresh_credential,
    save_credential,
    write_cookie_file,
)
from bilibili import bili_login
from bili_replay_min import init, get_replay_list
from g4p_battles import g4p_login
from main import run_highlight_pipeline

TEMPLATE_PATH = Path("bilibili") / "upload_template.json"


def _prompt(text: str, default: str | None = None) -> str:
    if default is None:
        return input(text).strip()
    value = input(f"{text} (default: {default}) ").strip()
    return value or default


def _ensure_bili_credential():
    credential = load_credential()
    if not credential:
        print(f"[ERROR] Missing {BILI_LOGIN_INFO}. Please login Bilibili first.")
        return None
    ok, err = refresh_credential(credential)
    if not ok:
        print(f"[ERROR] Bilibili login invalid: {err}")
        return None
    save_credential(credential)
    write_cookie_file(cookie_str_from_credential(credential))
    return credential


def _load_template() -> dict:
    if not TEMPLATE_PATH.exists():
        return {}
    try:
        return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        print(f"[WARN] Failed to parse {TEMPLATE_PATH}.")
        return {}


def _choose_replays():
    credential = _ensure_bili_credential()
    if not credential:
        return []
    cookie_str = cookie_str_from_credential(credential)
    init(cookie_str)
    replays = get_replay_list()
    if not replays:
        print("[ERROR] No Bilibili replays found.")
        return []

    print("\n[INFO] Recent Bilibili replays:")
    for idx, r in enumerate(replays, 1):
        start_ts = r.get("start_time", 0)
        start_text = datetime.fromtimestamp(start_ts).strftime("%Y-%m-%d %H:%M:%S") if start_ts else "unknown"
        print(f"  [{idx}] live_key={r.get('live_key')} start={start_text}")

    raw = _prompt("Select replay indices (comma, default: 1)", "1")
    indices = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            indices.append(int(part))
        except ValueError:
            print(f"[WARN] Invalid index: {part}")
    indices = [i for i in indices if 1 <= i <= len(replays)]
    if not indices:
        print("[ERROR] No valid replay selected.")
        return []
    return [str(replays[i - 1].get("live_key")) for i in indices]


def _get_replay_start_time_by_live_key(live_key: str | None) -> int | None:
    if not live_key:
        return None
    credential = _ensure_bili_credential()
    if not credential:
        return None
    cookie_str = cookie_str_from_credential(credential)
    init(cookie_str)
    replays = get_replay_list()
    for r in replays or []:
        if str(r.get("live_key")) == str(live_key):
            return r.get("start_time")
    return None


def _run_concat(output_dir: Path):
    script = Path("concat_clips.py")
    if not script.exists():
        print("[ERROR] concat_clips.py not found.")
        return False
    cmd = [sys.executable, str(script), "--dir", str(output_dir), "--out", "clips_all.mp4"]
    # cmd = [sys.executable, str(script), "--dir", str(output_dir), "--out", "clips_all.mp4", "--strategy", "copy"]
    print(f"[INFO] Merging clips in {output_dir} ...")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"[ERROR] Merge failed: {exc}")
        return False
    return True


def _cleanup_concat_artifacts(output_dir: Path) -> None:
    if not output_dir.exists():
        print(f"[WARN] Cleanup skipped, directory missing: {output_dir}")
        return
    removed_files = []
    removed_dirs = []

    for path in output_dir.glob("_ffmpeg_concat_list*.txt"):
        if path.is_file():
            try:
                path.unlink()
                removed_files.append(path.name)
            except Exception as exc:
                print(f"[WARN] Failed to remove {path}: {exc}")

    clips_all = output_dir / "clips_all.mp4"
    if clips_all.exists():
        try:
            clips_all.unlink()
            removed_files.append(clips_all.name)
        except Exception as exc:
            print(f"[WARN] Failed to remove {clips_all}: {exc}")

    for dir_name in ("_fixed", "_norm"):
        dir_path = output_dir / dir_name
        if dir_path.exists() and dir_path.is_dir():
            try:
                shutil.rmtree(dir_path)
                removed_dirs.append(dir_name)
            except Exception as exc:
                print(f"[WARN] Failed to remove {dir_path}: {exc}")

    if removed_files or removed_dirs:
        print(f"[INFO] Cleanup done in {output_dir}.")
        if removed_files:
            print(f"[INFO] Removed files: {', '.join(removed_files)}")
        if removed_dirs:
            print(f"[INFO] Removed dirs: {', '.join(removed_dirs)}")
    else:
        print(f"[INFO] Cleanup skipped, nothing to remove in {output_dir}.")


def do_login():
    print("[INFO] Logging into G4P (WeChat QR)...")
    g4p_login()
    print("[INFO] Logging into Bilibili (QR)...")
    return bili_login.main()


def do_download():
    selected = _choose_replays()
    if not selected:
        return 1
    result = run_highlight_pipeline(show_progress=True, selected_live_keys=selected)
    replay_results = result.get("replay_results") or []
    if not replay_results:
        print("[ERROR] No replay results.")
        return 1
    print("[INFO] Download done.")
    return 0


def _list_clip_dirs():
    clip_root = Path("clips")
    if not clip_root.exists():
        return []
    return [p for p in clip_root.iterdir() if p.is_dir()]


def do_merge():
    dirs = _list_clip_dirs()
    if not dirs:
        print("[ERROR] No clips directories found.")
        return 1

    print("\n[INFO] Available clip folders:")
    for idx, p in enumerate(dirs, 1):
        print(f"  [{idx}] {p.name}")

    raw = _prompt("Select folders to merge (comma, default: 1)", "1")
    indices = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            indices.append(int(part))
        except ValueError:
            print(f"[WARN] Invalid index: {part}")
    indices = [i for i in indices if 1 <= i <= len(dirs)]
    if not indices:
        print("[ERROR] No valid folder selected.")
        return 1

    for idx in indices:
        _run_concat(dirs[idx - 1])
    print("[INFO] Merge done.")
    return 0


def _find_merged_candidates(limit: int = 5) -> list[Path]:
    clip_root = Path("clips")
    if not clip_root.exists():
        return []
    candidates = []
    for folder in clip_root.iterdir():
        if not folder.is_dir():
            continue
        if not folder.name.isdigit():
            continue
        merged = folder / "clips_all.mp4"
        if merged.exists():
            candidates.append(merged)
    candidates.sort(key=lambda p: int(p.parent.name), reverse=True)
    return candidates[:limit]


def do_upload():
    template = _load_template()

    candidates = _find_merged_candidates()
    if not candidates:
        print("[ERROR] No clips_all.mp4 found under clips/.")
        return 1

    print("\n[INFO] Merged videos:")
    for idx, p in enumerate(candidates, 1):
        created = datetime.fromtimestamp(p.parent.stat().st_ctime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"  [{idx}] {p} (folder created: {created})")

    raw = _prompt("Select video index to upload (default: 1)", "1")
    try:
        index = int(raw)
    except ValueError:
        print("[ERROR] Invalid index.")
        return 1
    if not (1 <= index <= len(candidates)):
        print("[ERROR] Index out of range.")
        return 1

    video_path = candidates[index - 1]
    replay_start_time = _get_replay_start_time_by_live_key(video_path.parent.name)
    return _upload_video_path(video_path, template, replay_start_time=replay_start_time)


def _upload_video_path(video_path: Path, template: dict, replay_start_time: int | None = None) -> int:
    tid = template.get("tid", 4)
    tags = template.get("tags", [])
    cover_path = template.get("cover_path", "")

    if not tags:
        print("[ERROR] Template tags missing.")
        return 1
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    if not cover_path:
        print("[ERROR] Template cover_path missing.")
        return 1
    if not Path(cover_path).is_file():
        print(f"[ERROR] Cover file not found: {cover_path}")
        return 1

    credential = _ensure_bili_credential()
    if not credential:
        print("[ERROR] Bilibili login missing/invalid.")
        return 1

    if replay_start_time:
        title_time = datetime.fromtimestamp(replay_start_time)
    else:
        title_time = datetime.now(timezone.utc)
    title = f"回放 {title_time:%Y年%m月%d日}"
    desc = ""

    page = video_uploader.VideoUploaderPage(str(video_path), title)
    meta = video_uploader.VideoMeta(
        tid=int(tid),
        title=title,
        desc=desc,
        cover=str(cover_path),
        tags=tags,
        original=bool(template.get("original", True)),
        no_reprint=bool(template.get("no_reprint", False)),
        recreate=bool(template.get("recreate", False)),
        open_elec=bool(template.get("open_elec", False)),
        up_selection_reply=bool(template.get("up_selection_reply", False)),
        up_close_danmu=bool(template.get("up_close_danmu", False)),
        up_close_reply=bool(template.get("up_close_reply", False)),
        lossless_music=bool(template.get("lossless_music", False)),
        dolby=bool(template.get("dolby", False)),
        watermark=bool(template.get("watermark", False)),
        dynamic=None,
        delay_time=template.get("delay_time") or None,
    )

    uploader = video_uploader.VideoUploader(
        pages=[page],
        meta=meta,
        credential=credential,
        line=video_uploader.Lines.QN
    )

    def _log_upload_event(payload):
        if not isinstance(payload, dict):
            return
        name = payload.get("name")
        data = payload.get("data")
        if name in {"PREUPLOAD", "PRE_COVER", "AFTER_COVER", "PRE_SUBMIT"}:
            print(f"[INFO] {name}")
            return
        if name in {"CHUNK_FAILED", "PAGE_SUBMIT_FAILED", "SUBMIT_FAILED", "FAILED"}:
            print(f"[ERROR] {name}: {data}")
            return
        if name == "AFTER_CHUNK" and isinstance(data, (list, tuple)) and data:
            info = data[0] if isinstance(data[0], dict) else None
            if info:
                idx = info.get("chunk_number", 0) + 1
                total = info.get("total_chunk_count", 0)
                print(f"[INFO] Upload chunk {idx}/{total}")

    uploader.add_event_listener("__ALL__", _log_upload_event)

    print("[INFO] Uploading...")
    try:
        result = sync(uploader.start())
    except Exception as exc:
        print(f"[ERROR] Upload failed: {exc}")
        return 1

    if isinstance(result, dict):
        bvid = result.get("bvid")
        aid = result.get("aid")
        print(f"[OK] Upload completed. bvid={bvid} aid={aid}")
        alert.send_alert(
            "upload_complete",
            "Bilibili upload completed",
            f"bvid={bvid} aid={aid} file={video_path}",
        )
    else:
        print(f"[OK] Upload completed. result={result}")
        alert.send_alert(
            "upload_complete",
            "Bilibili upload completed",
            f"result={result} file={video_path}",
        )
    return 0


def _get_latest_replay_key():
    credential = _ensure_bili_credential()
    if not credential:
        return None
    cookie_str = cookie_str_from_credential(credential)
    init(cookie_str)
    replays = get_replay_list()
    if not replays:
        return None
    return str(replays[0].get("live_key"))


def do_all_in_one():
    print("[INFO] Logging into G4P (WeChat QR)...")
    # g4p_login()
    print("[INFO] Logging into Bilibili (QR)...")
    if bili_login.main() != 0:
        return 1

    latest_key = _get_latest_replay_key()
    if not latest_key:
        print("[ERROR] No Bilibili replay found.")
        return 1

    result = run_highlight_pipeline(show_progress=True, selected_live_keys=[latest_key])
    replay_results = result.get("replay_results") or []
    if not replay_results:
        print("[ERROR] No replay results.")
        return 1

    output_dir = Path(replay_results[0].get("output_dir") or "")
    if not output_dir.exists():
        print("[ERROR] Output directory missing.")
        return 1

    if not _run_concat(output_dir):
        return 1

    merged_path = output_dir / "clips_all.mp4"
    if not merged_path.exists():
        print("[ERROR] Merged video missing.")
        return 1

    template = _load_template()
    replay_start_time = _get_replay_start_time_by_live_key(latest_key)
    result = _upload_video_path(merged_path, template, replay_start_time=replay_start_time)
    if result == 0:
        alert.send_alert(
            "all_in_one_complete",
            "All-in-one completed",
            f"merged={merged_path}",
        )
        _cleanup_concat_artifacts(output_dir)
    return result


def do_cleanup():
    dirs = _list_clip_dirs()
    if not dirs:
        print("[ERROR] No clips directories found.")
        return 1

    print("\n[INFO] Available clip folders:")
    for idx, p in enumerate(dirs, 1):
        print(f"  [{idx}] {p.name}")

    raw = _prompt("Select folders to cleanup (comma, default: 1)", "1")
    indices = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            indices.append(int(part))
        except ValueError:
            print(f"[WARN] Invalid index: {part}")
    indices = [i for i in indices if 1 <= i <= len(dirs)]
    if not indices:
        print("[ERROR] No valid folder selected.")
        return 1

    for idx in indices:
        _cleanup_concat_artifacts(dirs[idx - 1])
    print("[INFO] Cleanup done.")
    return 0


def _schedule_shutdown(delay_seconds: int = 180) -> None:
    minutes = max(1, int(round(delay_seconds / 60)))
    print(f"[INFO] Task completed. Scheduling shutdown in {minutes} minute(s).")
    print("[INFO] To cancel: run 'shutdown /a' in a new terminal.")
    try:
        subprocess.run(["shutdown", "/s", "/t", str(int(delay_seconds))], check=False)
    except Exception as exc:
        print(f"[ERROR] Failed to schedule shutdown: {exc}")


def main():
    while True:
        print("\nSelect action:")
        print("  [1] Login (G4P + Bilibili)")
        print("  [2] Download clips")
        print("  [3] Merge clips")
        print("  [4] Upload to Bilibili")
        print("  [5] All-in-one (login + download + merge + upload)")
        print("  [6] All-in-one + auto shutdown (3 minutes)")
        print("  [7] Cleanup merged artifacts")
        print("  [0] Exit")
        choice = _prompt("Your choice", "0")
        if choice == "1":
            do_login()
        elif choice == "2":
            do_download()
        elif choice == "3":
            do_merge()
        elif choice == "4":
            do_upload()
        elif choice == "5":
            do_all_in_one()
        elif choice == "6":
            result = do_all_in_one()
            if result == 0:
                _schedule_shutdown(180)
                return 0
            print("[WARN] Task failed. Shutdown skipped.")
        elif choice == "7":
            do_cleanup()
        elif choice == "0":
            return 0
        else:
            print("[WARN] Invalid choice.")


if __name__ == "__main__":
    raise SystemExit(main())
