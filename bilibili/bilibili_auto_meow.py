import sys
import time
from datetime import datetime

from bilibili_api import Credential, live, sync

from bilibili.bili_auth import BiliAuthError, ensure_cookie


def _parse_cookie(cookie_str: str) -> dict:
    cookies = {}
    for part in cookie_str.split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        k, v = item.split("=", 1)
        cookies[k.strip()] = v.strip()
    return cookies


def _build_credential(cookie_str: str) -> Credential:
    cookies = _parse_cookie(cookie_str)
    return Credential(
        sessdata=cookies.get("SESSDATA"),
        bili_jct=cookies.get("bili_jct"),
        dedeuserid=cookies.get("DedeUserID"),
        buvid3=cookies.get("buvid3"),
        buvid4=cookies.get("buvid4"),
        ac_time_value=cookies.get("ac_time_value"),
    )


def _send_danmaku(room: live.LiveRoom, text: str) -> None:
    try:
        danmaku = live.Danmaku(text)
        sync(room.send_danmaku(danmaku))
    except Exception:
        sync(room.send_danmaku(text))


def main() -> int:
    try:
        cookie_str = ensure_cookie(prefer_login_info=True)
    except BiliAuthError as exc:
        print(f"[ERROR] {exc}")
        return 1

    credential = _build_credential(cookie_str)
    try:
        if not sync(credential.check_valid()):
            print("[ERROR] Bilibili cookie invalid. Run run_bili_login.bat or update cookie.txt.")
            return 1
    except Exception:
        print("[WARN] Unable to validate cookie, continue anyway.")

    room_id_raw = input("请输入直播间 ID：").strip()
    if not room_id_raw.isdigit():
        print("[ERROR] 直播间 ID 需要是数字。")
        return 1

    room_id = int(room_id_raw)
    room = live.LiveRoom(room_id, credential=credential)

    interval = 300  # 5 minutes
    next_ts = time.time()
    print("[INFO] Start sending danmaku. Press Ctrl+C to stop.")

    try:
        while True:
            now = time.time()
            if now < next_ts:
                time.sleep(next_ts - now)
            now_dt = datetime.now()
            msg_time = f"现在是{now_dt:%H:%M}"
            # _send_danmaku(room, msg_time)
            print(f"[INFO] Sent: {msg_time}")
            time.sleep(5)
            _send_danmaku(room, "喵")
            print("[INFO] Sent: 喵")
            next_ts += interval
    except KeyboardInterrupt:
        print("\n[INFO] Stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
