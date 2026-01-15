import json
from pathlib import Path
from typing import Optional, Tuple

from bilibili_api import Credential, sync
from bilibili_api.exceptions import CredentialNoAcTimeValueException


BILI_LOGIN_INFO = Path("BiliLoginInfo.json")
COOKIE_FILE = Path("cookie.txt")


class BiliAuthError(RuntimeError):
    """Raised when Bilibili auth/cookie cannot be prepared."""


def _credential_from_data(data: dict) -> Optional[Credential]:
    if not isinstance(data, dict):
        return None
    if "cookies" in data and isinstance(data["cookies"], dict):
        return Credential.from_cookies(data["cookies"])
    return Credential(
        sessdata=data.get("sessdata"),
        bili_jct=data.get("bili_jct"),
        dedeuserid=data.get("dedeuserid"),
        buvid3=data.get("buvid3"),
        buvid4=data.get("buvid4"),
        ac_time_value=data.get("ac_time_value"),
    )


def load_credential() -> Optional[Credential]:
    if not BILI_LOGIN_INFO.exists():
        return None
    try:
        data = json.loads(BILI_LOGIN_INFO.read_text(encoding="utf-8").strip())
    except Exception:
        return None
    return _credential_from_data(data)


def save_credential(credential: Credential) -> None:
    data = {
        "sessdata": credential.sessdata or "",
        "bili_jct": credential.bili_jct or "",
        "dedeuserid": credential.dedeuserid or "",
        "buvid3": credential.buvid3 or "",
        "buvid4": credential.buvid4 or "",
        "ac_time_value": credential.ac_time_value or "",
    }
    BILI_LOGIN_INFO.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def cookie_str_from_credential(credential: Credential) -> str:
    parts = []
    if credential.sessdata:
        parts.append(f"SESSDATA={credential.sessdata}")
    if credential.bili_jct:
        parts.append(f"bili_jct={credential.bili_jct}")
    if credential.dedeuserid:
        parts.append(f"DedeUserID={credential.dedeuserid}")
    if credential.buvid3:
        parts.append(f"buvid3={credential.buvid3}")
    if credential.buvid4:
        parts.append(f"buvid4={credential.buvid4}")
    if credential.ac_time_value:
        parts.append(f"ac_time_value={credential.ac_time_value}")
    return "; ".join(parts)


def write_cookie_file(cookie_str: str) -> None:
    if not cookie_str:
        return
    COOKIE_FILE.write_text(cookie_str, encoding="utf-8")
    print(f"[INFO] Updated {COOKIE_FILE}.")


def _read_cookie_file() -> Optional[str]:
    if not COOKIE_FILE.exists():
        return None
    text = COOKIE_FILE.read_text(encoding="utf-8").lstrip("\ufeff").strip()
    if not text or "PUT_YOUR_BILIBILI_COOKIE_HERE" in text:
        return None
    return text


def refresh_credential(credential: Credential) -> Tuple[bool, Optional[str]]:
    try:
        needs_refresh = sync(credential.check_refresh())
        if needs_refresh:
            print("[INFO] Bilibili cookie needs refresh. Refreshing...")
            sync(credential.refresh())
            print("[INFO] Bilibili cookie refresh done.")
        if not sync(credential.check_valid()):
            return False, "credential invalid"
        return True, None
    except CredentialNoAcTimeValueException:
        return False, "missing ac_time_value"
    except Exception as exc:
        return False, str(exc)


def ensure_cookie(prefer_login_info: bool = True) -> str:
    if prefer_login_info:
        credential = load_credential()
        if credential:
            print(f"[INFO] Loaded Bilibili credential from {BILI_LOGIN_INFO}.")
            ok, err = refresh_credential(credential)
            if ok:
                save_credential(credential)
                cookie_str = cookie_str_from_credential(credential)
                write_cookie_file(cookie_str)
                return cookie_str
            fallback = _read_cookie_file()
            if fallback:
                print("[WARN] Using cookie.txt fallback.")
                return fallback
            raise BiliAuthError(
                f"Bilibili login invalid ({err}). Run run_bili_login.bat to re-login."
            )

    cookie_str = _read_cookie_file()
    if cookie_str:
        print("[INFO] Using cookie.txt.")
        return cookie_str

    raise BiliAuthError(
        "cookie.txt missing/placeholder and no BiliLoginInfo.json found. Run run_bili_login.bat."
    )
