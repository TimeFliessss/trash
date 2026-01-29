import base64
import time
from pathlib import Path
from pyqrcode import QRCode
import sys

from game_for_peace.account import get_account_manager
from game_for_peace.gp_client import GpRequestClient

try:
    b = u'\u2588\u2588'
    sys.stdout.write(b + '\r')
    sys.stdout.flush()
except UnicodeEncodeError:
    BLOCK = 'MM'
    USE_UNICODE_BLOCK = False
else:
    BLOCK = b
    USE_UNICODE_BLOCK = True

def _compact_qr(qr_text):
    lines = [line for line in qr_text.splitlines() if line]
    if not lines:
        return ""
    width = max(len(line) for line in lines)
    lines = [line.ljust(width, "0") for line in lines]
    compact_lines = []
    for i in range(0, len(lines), 2):
        upper = lines[i]
        lower = lines[i + 1] if i + 1 < len(lines) else "0" * width
        row = []
        for u, l in zip(upper, lower):
            if u == "1" and l == "1":
                row.append("\u2588")  # full block
            elif u == "1" and l == "0":
                row.append("\u2580")  # upper half block
            elif u == "0" and l == "1":
                row.append("\u2584")  # lower half block
            else:
                row.append(" ")
        compact_lines.append("".join(row))
    return "\n".join(compact_lines)


def print_cmd_qr(qrText, white=BLOCK, black='  ', enableCmdQR=True):
    if USE_UNICODE_BLOCK:
        sys.stdout.write(' ' * 50 + '\r')
        sys.stdout.flush()
        sys.stdout.write(_compact_qr(qrText) + "\n")
        sys.stdout.flush()
        return

    blockCount = int(enableCmdQR)
    if abs(blockCount) == 0:
        blockCount = 1
    white *= abs(blockCount)
    if blockCount < 0:
        white, black = black, white
    sys.stdout.write(' ' * 50 + '\r')
    sys.stdout.flush()
    qr = qrText.replace('0', white).replace('1', black)
    sys.stdout.write(qr)
    sys.stdout.flush()


def save_qr_image(qr_data, path):
    code = qr_data["qrcode"]["qrcodebase64"]
    raw = base64.b64decode(code)
    Path(path).write_bytes(raw)
    return Path(path)


def wait_for_open_id(client, uuid, timeout=120):
    last_status = None
    end_time = time.time() + timeout
    while time.time() < end_time:
        scan = client.request_qr_code_scan_status(uuid, last_status)
        if scan:
            last_status = scan.get("wx_errcode", last_status)
            code = scan.get("wx_code")
            if code:
                return code
        time.sleep(1)
    raise RuntimeError("qr scan timed out")


def login_flow(client, account):
    print("requesting wx sdk ticket...")
    ticket = client.request_wx_sdk_ticket()
    print("requesting qr code...")
    qr_result = client.request_wx_login_qr_code(ticket)
    
    qr_content = f"https://open.weixin.qq.com/connect/confirm?uuid={qr_result['uuid']}"
    qrCode = QRCode(qr_content)
    print_cmd_qr(qrCode.text(1))
    open_id = wait_for_open_id(client, qr_result["uuid"])
    print("wechat scan confirmed. requesting auth...")
    client.get_personal_auth(open_id)
    info = client.login(open_id)
    info["_wx_code"] = open_id
    account.save_login_info(info)
    print("login success. info cached.")
    return info


def g4p_login(account_path=None):
    account = get_account_manager(path=account_path)
    client = GpRequestClient(account)
    if not account.is_valid_login():
        login_flow(client, account)
    roles = client.get_all_roles()
    account.role_list = roles
    account.game_open_id = roles[0]['openid']
    return client

def is_g4p_logged_in(account_path=None) -> bool:
    """Return True if cached login data is still valid."""
    account = get_account_manager(path=account_path)
    return bool(account.is_valid_login())


