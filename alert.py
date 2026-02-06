import base64
import hmac
import json
import time
import hashlib
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG_PATH = Path("alert_config.json")
TEMPLATE_PATH = Path("alert_config.template.json")
DEFAULT_TIMEOUT_SECONDS = 10


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        if TEMPLATE_PATH.exists():
            print(f"[ALERT] Config missing: {CONFIG_PATH}. Using template defaults.")
            try:
                return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"[ALERT] Failed to parse {TEMPLATE_PATH}: {exc}")
                return {}
        print(f"[ALERT] Config missing: {CONFIG_PATH}")
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[ALERT] Failed to parse {CONFIG_PATH}: {exc}")
        return {}


def _post_json(url: str, payload: dict, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as _:
        pass


def _send_wecom_bot(cfg: dict, text: str) -> bool:
    url = cfg.get("webhook_url") or ""
    key = cfg.get("key") or ""
    if not url and key:
        url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}"
    if not url:
        print("[ALERT] WeCom bot missing webhook_url/key.")
        return False
    payload = {"msgtype": "text", "text": {"content": text}}
    _post_json(url, payload)
    return True


def _send_dingtalk_bot(cfg: dict, text: str) -> bool:
    url = cfg.get("webhook_url") or ""
    token = cfg.get("access_token") or ""
    if not url and token:
        url = f"https://oapi.dingtalk.com/robot/send?access_token={token}"
    if not url:
        print("[ALERT] DingTalk bot missing webhook_url/access_token.")
        return False
    secret = cfg.get("secret") or ""
    if secret:
        timestamp = str(int(time.time() * 1000))
        to_sign = f"{timestamp}\n{secret}".encode("utf-8")
        sign = base64.b64encode(
            hmac.new(secret.encode("utf-8"), to_sign, hashlib.sha256).digest()
        ).decode("utf-8")
        url = f"{url}&timestamp={timestamp}&sign={urllib.parse.quote(sign)}"
    keyword = cfg.get("keyword") or ""
    if keyword and keyword not in text:
        text = f"{keyword} {text}"
    payload = {"msgtype": "text", "text": {"content": text}}
    _post_json(url, payload)
    return True


def _send_bark(cfg: dict, title: str, message: str) -> bool:
    server = (cfg.get("server") or "https://api.day.app").rstrip("/")
    key = cfg.get("device_key") or ""
    if not key:
        print("[ALERT] Bark missing device_key.")
        return False
    title_q = urllib.parse.quote(title)
    msg_q = urllib.parse.quote(message)
    url = f"{server}/{key}/{title_q}/{msg_q}"
    params = {}
    for field in ("group", "url", "sound", "icon", "isArchive"):
        value = cfg.get(field)
        if value:
            params[field] = value
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=DEFAULT_TIMEOUT_SECONDS) as _:
        pass
    return True


def _send_telegram_bot(cfg: dict, text: str) -> bool:
    token = cfg.get("bot_token") or ""
    chat_id = cfg.get("chat_id") or ""
    if not token or not chat_id:
        print("[ALERT] Telegram bot missing bot_token/chat_id.")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    _post_json(url, payload)
    return True


def _send_ntfy(cfg: dict, title: str, message: str) -> bool:
    server = (cfg.get("server") or "https://ntfy.sh").rstrip("/")
    topic = cfg.get("topic") or ""
    if not topic:
        print("[ALERT] Ntfy missing topic.")
        return False
    url = f"{server}/{topic}"
    data = message.encode("utf-8")
    headers = {"Title": title}
    token = cfg.get("token") or ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    username = cfg.get("username") or ""
    password = cfg.get("password") or ""
    if username and password:
        basic = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("utf-8")
        headers["Authorization"] = f"Basic {basic}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT_SECONDS) as _:
        pass
    return True


def send_alert(event: str, title: str, message: str) -> None:
    cfg = _load_config()
    if not cfg:
        return
    if cfg.get("enabled") is False:
        return
    events = cfg.get("events", {})
    if events and events.get(event) is False:
        return
    channels = cfg.get("channels", {})
    if not isinstance(channels, dict):
        return
    text = f"{title}\n{message}"
    failures = []
    successes = []

    def _safe(name: str, fn):
        try:
            if fn():
                successes.append(name)
            else:
                failures.append(name)
        except Exception as exc:
            failures.append(name)
            print(f"[ALERT] {name} failed: {exc}")

    wecom = channels.get("wecom_bot", {})
    if wecom.get("enabled"):
        _safe("wecom_bot", lambda: _send_wecom_bot(wecom, text))

    dingtalk = channels.get("dingtalk_bot", {})
    if dingtalk.get("enabled"):
        _safe("dingtalk_bot", lambda: _send_dingtalk_bot(dingtalk, text))

    bark = channels.get("bark", {})
    if bark.get("enabled"):
        _safe("bark", lambda: _send_bark(bark, title, message))

    telegram = channels.get("telegram_bot", {})
    if telegram.get("enabled"):
        _safe("telegram_bot", lambda: _send_telegram_bot(telegram, text))

    ntfy = channels.get("ntfy", {})
    if ntfy.get("enabled"):
        _safe("ntfy", lambda: _send_ntfy(ntfy, title, message))

    if successes:
        print(f"[ALERT] Sent via: {', '.join(successes)}")
    if failures:
        print(f"[ALERT] Failed via: {', '.join(failures)}")
