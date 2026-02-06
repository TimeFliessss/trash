import json
import os
import time
from pathlib import Path

from bilibili_api import request_log, sync, video_uploader

from bilibili.bili_auth import (
    BILI_LOGIN_INFO,
    load_credential,
    refresh_credential,
    save_credential,
    write_cookie_file,
    cookie_str_from_credential,
)

TEMPLATE_PATH = Path("bilibili") / "upload_template.json"
PROBE_TIMEOUT_SECONDS = 30
MAX_UPLOAD_RETRIES = 14
LINE_FAILURE_THRESHOLD = 3


def _prompt(text: str, default: str | None = None) -> str:
    if default is None:
        return input(text).strip()
    value = input(f"{text} (default: {default}) ").strip()
    return value or default


def _prompt_non_empty(text: str) -> str:
    while True:
        value = input(text).strip()
        if value:
            return value
        print("[WARN] Input required.")


def _prompt_tags() -> list[str]:
    while True:
        raw = input("Tags (comma-separated): ").strip()
        if not raw:
            print("[WARN] At least one tag is required.")
            continue
        tags = [t.strip() for t in raw.split(",") if t.strip()]
        if not tags:
            print("[WARN] At least one tag is required.")
            continue
        if len(tags) > 10:
            print("[WARN] Too many tags (max 10).")
            continue
    return tags


def _load_template() -> dict:
    if not TEMPLATE_PATH.exists():
        return {}
    try:
        return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        print(f"[WARN] Failed to parse {TEMPLATE_PATH}. Using defaults.")
        return {}


def _get_default(template: dict, key: str, fallback):
    if isinstance(template, dict) and key in template:
        value = template.get(key)
        return value if value is not None else fallback
    return fallback


def _ensure_credential():
    credential = load_credential()
    if not credential:
        print(f"[ERROR] Missing {BILI_LOGIN_INFO}. Run run_bili_login.bat first.")
        return None
    ok, err = refresh_credential(credential)
    if not ok:
        print(f"[ERROR] Bilibili login invalid: {err}")
        print("[ERROR] Run run_bili_login.bat to re-login.")
        return None
    save_credential(credential)
    write_cookie_file(cookie_str_from_credential(credential))
    print("[INFO] Bilibili credential OK.")
    return credential


async def _probe_lines_async(blacklist: set[str]) -> tuple[str | None, float | None]:
    min_cost = PROBE_TIMEOUT_SECONDS
    fastest_key = None
    fastest_cost = None
    legacy_timeout = video_uploader.request_settings.get_timeout()
    video_uploader.request_settings.set_timeout(PROBE_TIMEOUT_SECONDS)
    try:
        for key, line in video_uploader.LINES_INFO.items():
            if key in blacklist:
                print(f"[LINE] {key}: skipped (blacklisted)")
                continue
            start = time.perf_counter()
            data = bytes(int(1024 * 0.1 * 1024))
            client = video_uploader.get_client()
            ok = True
            try:
                await client.request(
                    method="POST",
                    url=f'https:{line["probe_url"]}',
                    data=data,
                )
                cost_time = time.perf_counter() - start
            except Exception:
                ok = False
                cost_time = PROBE_TIMEOUT_SECONDS
            status = "OK" if ok else "FAILED"
            print(f"[LINE] {key}: {status} {cost_time:.2f}s")
            if cost_time < min_cost:
                min_cost = cost_time
                fastest_key = key
                fastest_cost = cost_time
    finally:
        video_uploader.request_settings.set_timeout(legacy_timeout)
    return fastest_key, fastest_cost


def _choose_best_line(blacklist: set[str]) -> tuple[str | None, float | None]:
    if blacklist:
        print(f"[INFO] Line blacklist: {', '.join(sorted(blacklist))}")
    else:
        print("[INFO] Line blacklist: (empty)")
    print("[INFO] Probing upload lines...")
    return sync(_probe_lines_async(blacklist))


def main() -> int:
    if os.getenv("BILI_API_DEBUG", 1) == "1":
        request_log.set_on(True)
        request_log.set_on_events(["API_REQUEST", "API_RESPONSE"])

    credential = _ensure_credential()
    if not credential:
        return 1

    template = _load_template()

    title = _prompt_non_empty("Title: ")
    video_path = _prompt_non_empty("Video file path: ")
    if not Path(video_path).is_file():
        print(f"[ERROR] Video file not found: {video_path}")
        return 1

    tid_default = str(_get_default(template, "tid", 4))
    tid_raw = _prompt("Zone tid", tid_default)
    try:
        tid = int(tid_raw)
    except ValueError:
        print("[ERROR] tid must be a number.")
        return 1

    tags_default = _get_default(template, "tags", [])
    if tags_default and isinstance(tags_default, list):
        tags_raw = _prompt("Tags (comma-separated)", ",".join(tags_default))
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        if not tags:
            tags = _prompt_tags()
    else:
        tags = _prompt_tags()

    cover_default = _get_default(template, "cover_path", "")
    cover_path = _prompt("Cover image path", cover_default).strip()
    if not cover_path:
        cover_path = _prompt_non_empty("Cover image path: ")
    if not Path(cover_path).is_file():
        print(f"[ERROR] Cover file not found: {cover_path}")
        return 1

    desc_default = _get_default(template, "description", "")
    desc = _prompt("Description", desc_default)

    dynamic_default = _get_default(template, "dynamic", "")
    dynamic = _prompt("Dynamic (optional)", dynamic_default)

    page = video_uploader.VideoUploaderPage(video_path, title)
    meta = video_uploader.VideoMeta(
        tid=tid,
        title=title,
        desc=desc,
        cover=cover_path,
        tags=tags,
        original=bool(_get_default(template, "original", True)),
        no_reprint=bool(_get_default(template, "no_reprint", False)),
        recreate=bool(_get_default(template, "recreate", False)),
        open_elec=bool(_get_default(template, "open_elec", False)),
        up_selection_reply=bool(_get_default(template, "up_selection_reply", False)),
        up_close_danmu=bool(_get_default(template, "up_close_danmu", False)),
        up_close_reply=bool(_get_default(template, "up_close_reply", False)),
        lossless_music=bool(_get_default(template, "lossless_music", False)),
        dolby=bool(_get_default(template, "dolby", False)),
        watermark=bool(_get_default(template, "watermark", False)),
        dynamic=dynamic or None,
        delay_time=_get_default(template, "delay_time", None) or None,
    )
    line_failures: dict[str, int] = {}
    blacklist: set[str] = set()

    def _log_event(payload):
        if not isinstance(payload, dict):
            return
        name = payload.get("name")
        data = payload.get("data")
        if name in {
            "PREUPLOAD",
            "PREUPLOAD_FAILED",
            "PRE_COVER",
            "AFTER_COVER",
            "PAGE_SUBMIT_FAILED",
            "SUBMIT_FAILED",
            "FAILED",
            "COMPLETE",
        }:
            print(f"[INFO] Upload event: {name} {data}")

    result = None
    for attempt in range(1, MAX_UPLOAD_RETRIES + 1):
        print(f"[INFO] Upload attempt {attempt}/{MAX_UPLOAD_RETRIES}")
        line_key, line_cost = _choose_best_line(blacklist)
        if not line_key:
            print("[ERROR] No available upload line found (all failed or blacklisted).")
            return 1

        try:
            line_enum = video_uploader.Lines(line_key)
        except Exception:
            print(f"[ERROR] Unknown upload line: {line_key}")
            return 1

        if line_cost is not None:
            print(f"[INFO] Selected line: {line_key} ({line_cost:.2f}s)")
        else:
            print(f"[INFO] Selected line: {line_key}")

        uploader = video_uploader.VideoUploader(
            pages=[page],
            meta=meta,
            credential=credential,
            line=line_enum,
        )
        uploader.add_event_listener("__ALL__", _log_event)

        print("[INFO] Uploading...")
        try:
            result = sync(uploader.start())
            break
        except Exception as exc:
            failures = line_failures.get(line_key, 0) + 1
            line_failures[line_key] = failures
            print(f"[ERROR] Upload failed on line {line_key}: {exc}")
            print(f"[INFO] Line {line_key} failures: {failures}/{LINE_FAILURE_THRESHOLD}")
            if failures >= LINE_FAILURE_THRESHOLD:
                if line_key not in blacklist:
                    blacklist.add(line_key)
                    print(f"[WARN] Line {line_key} added to blacklist.")
            if attempt >= MAX_UPLOAD_RETRIES:
                print(f"[ERROR] Upload failed after {MAX_UPLOAD_RETRIES} attempts. Exiting.")
                print("[HINT] '获取 upload_id 错误' usually means preupload failed.")
                print("[HINT] Common causes: invalid login, network/proxy issue, or upload line blocked.")
                print("[HINT] Try: run_bili_login.bat, disable proxy/VPN, then retry.")
                return 1
            print("[INFO] Retrying with a new line selection...")
            continue
    if result is None:
        return 1

    if isinstance(result, dict):
        bvid = result.get("bvid")
        aid = result.get("aid")
        if bvid:
            print(f"[OK] Upload completed. bvid={bvid} aid={aid}")
        else:
            print(f"[OK] Upload completed. result={result}")
    else:
        print(f"[OK] Upload completed. result={result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
