import sys
from pathlib import Path

from game_for_peace.account import get_account_manager, LOGIN_INFO_PATH
from game_for_peace.gp_client import GpRequestClient
from g4p_battles import login_flow


def _show_message(title: str, message: str) -> None:
    print(f"{title}: {message}")


def _refresh_login(client: GpRequestClient, account) -> bool:
    info = account.login_info or {}
    open_id = (
        info.get("_wx_code")
        or info.get("wx_code")
        or info.get("openid")
        or info.get("open_id")
    )
    if not open_id:
        return False
    client.get_personal_auth(open_id)
    refreshed = client.login(open_id)
    refreshed["_wx_code"] = open_id
    account.save_login_info(refreshed)
    return True


def main() -> int:
    account = get_account_manager()
    client = GpRequestClient(account)

    login_file_exists = Path(LOGIN_INFO_PATH).exists()
    has_valid_login = login_file_exists and account.is_valid_login()

    if not has_valid_login:
        print("[INFO] 未检测到有效登录，将启动扫码登录流程。")
        try:
            login_flow(client, account)
        except Exception as exc:
            _show_message("登录失败", f"扫码登录失败：{exc}")
            return 1
        _show_message("登录成功", "扫码登录完成，LoginInfo.txt 已更新。")
        return 0

    print("[INFO] 检测到已有登录，尝试刷新登录信息...")
    try:
        refreshed = _refresh_login(client, account)
    except Exception as exc:
        print(f"[WARN] 刷新登录失败：{exc}")
        refreshed = False

    if refreshed:
        _show_message("登录已更新", "已刷新登录信息，LoginInfo.txt 已更新。")
        return 0

    print("[WARN] 未找到可用的 wx_code，改为扫码登录。")
    try:
        login_flow(client, account)
    except Exception as exc:
        _show_message("登录失败", f"扫码登录失败：{exc}")
        return 1
    _show_message("登录成功", "扫码登录完成，LoginInfo.txt 已更新。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
