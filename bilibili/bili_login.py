import argparse
import os
import tempfile
import time
import webbrowser

import qrcode
from bilibili_api import Credential, login_v2, sync, user

from bilibili.bili_auth import (
    BILI_LOGIN_INFO,
    save_credential,
    write_cookie_file,
    cookie_str_from_credential,
    load_credential,
    refresh_credential,
)


def _supports_unicode() -> bool:
    try:
        test = "\u2580\u2584\u2588"
        print(test, end="\r")
        return True
    except UnicodeEncodeError:
        return False


def _render_half_block(matrix) -> str:
    lines = []
    height = len(matrix)
    width = len(matrix[0]) if height else 0
    for y in range(0, height, 2):
        upper = matrix[y]
        lower = matrix[y + 1] if y + 1 < height else [False] * width
        row = []
        for u, l in zip(upper, lower):
            if u and l:
                row.append("\u2588")
            elif u and not l:
                row.append("\u2580")
            elif (not u) and l:
                row.append("\u2584")
            else:
                row.append(" ")
        lines.append("".join(row))
    return "\n".join(lines)


def _print_qr(link: str) -> None:
    qr = qrcode.QRCode(border=1)
    qr.add_data(link)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    if _supports_unicode():
        print(_render_half_block(matrix))
    else:
        for row in matrix:
            print("".join("██" if cell else "  " for cell in row))


def _open_qr_image(qr_login: login_v2.QrCodeLogin) -> None:
    picture = qr_login.get_qrcode_picture()
    if not picture:
        return
    temp_path = os.path.join(tempfile.gettempdir(), "bili_qrcode.png")
    picture.to_file(temp_path)
    try:
        os.startfile(temp_path)  # type: ignore[attr-defined]
    except Exception:
        webbrowser.open(temp_path)


def _qr_login(no_open: bool) -> Credential:
    qr_login = login_v2.QrCodeLogin()
    sync(qr_login.generate_qrcode())

    link = getattr(qr_login, "_QrCodeLogin__qr_link", "")
    if link:
        print("[INFO] Scan the QR code with Bilibili app to login.")
        _print_qr(link)
        if not no_open:
            try:
                webbrowser.open(link)
            except Exception:
                _open_qr_image(qr_login)
    else:
        print("[INFO] Scan the QR code with Bilibili app to login.")
        print(qr_login.get_qrcode_terminal())
        if not no_open:
            _open_qr_image(qr_login)

    while True:
        state = sync(qr_login.check_state())
        if state == login_v2.QrCodeLoginEvents.SCAN:
            time.sleep(1)
            continue
        if state == login_v2.QrCodeLoginEvents.CONF:
            time.sleep(1)
            continue
        if state == login_v2.QrCodeLoginEvents.TIMEOUT:
            raise RuntimeError("QR code expired. Please retry.")
        if state == login_v2.QrCodeLoginEvents.DONE:
            return qr_login.get_credential()
        time.sleep(1)


def _extract_videos(data, max_items=5):
    if not isinstance(data, dict):
        return []
    for key in ("list", "data"):
        block = data.get(key)
        if isinstance(block, dict) and "vlist" in block:
            return block.get("vlist")[:max_items]
    if "vlist" in data:
        return data.get("vlist")[:max_items]
    if "videos" in data:
        return data.get("videos")[:max_items]
    return []


def _print_profile(credential: Credential) -> None:
    try:
        info = sync(user.get_self_info(credential))
    except Exception:
        print("[WARN] Unable to fetch user profile info.")
        return

    uid = None
    name = None
    if isinstance(info, dict):
        uid = info.get("mid") or info.get("uid")
        name = info.get("name") or info.get("uname") or info.get("username")
        if not uid and isinstance(info.get("data"), dict):
            uid = info["data"].get("mid") or info["data"].get("uid")
            name = name or info["data"].get("name") or info["data"].get("uname")

    if name:
        print(f"[INFO] User: {name}")
    if uid:
        print(f"[INFO] UID: {uid}")

    try:
        coins = sync(user.get_self_coins(credential))
        print(f"[INFO] Coins: {coins}")
    except Exception:
        print("[WARN] Unable to fetch coin info.")

    if not uid:
        print("[WARN] Unable to fetch recent videos (missing UID).")
        return

    try:
        u = user.User(int(uid), credential=credential)
        videos = _extract_videos(sync(u.get_videos(ps=5)))
        if videos:
            print("[INFO] Recent videos:")
            for v in videos:
                title = v.get("title") or "Untitled"
                like = None
                if isinstance(v.get("stat"), dict):
                    like = v["stat"].get("like")
                if like is None:
                    like = v.get("like") or v.get("likes")
                suffix = f" (likes: {like})" if like is not None else ""
                print(f"  - {title}{suffix}")
        else:
            print("[INFO] Recent videos: none")
    except Exception:
        print("[WARN] Unable to fetch recent videos.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="force QR login")
    ap.add_argument("--no-open", action="store_true", help="do not open QR in browser")
    args = ap.parse_args()

    if not args.force:
        credential = load_credential()
        if credential:
            ok, err = refresh_credential(credential)
            if ok:
                save_credential(credential)
                cookie_str = cookie_str_from_credential(credential)
                write_cookie_file(cookie_str)
                print(f"[OK] Refreshed login. Updated {BILI_LOGIN_INFO}.")
                _print_profile(credential)
                return 0
            print(f"[WARN] Refresh failed ({err}), switching to QR login.")

    try:
        credential = _qr_login(args.no_open)
    except Exception as exc:
        print(f"[ERROR] QR login failed: {exc}")
        return 1

    save_credential(credential)
    cookie_str = cookie_str_from_credential(credential)
    write_cookie_file(cookie_str)
    print(f"[OK] Login success. Updated {BILI_LOGIN_INFO} and cookie.txt.")
    _print_profile(credential)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
