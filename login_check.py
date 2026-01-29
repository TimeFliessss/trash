import sys
from pathlib import Path

from game_for_peace.account import get_account_manager
from game_for_peace.gp_client import GpRequestClient
from g4p_battles import login_flow
from g4p_accounts import list_account_paths


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
    paths = list_account_paths()
    if not paths:
        print("[ERROR] No G4P accounts found. Please add one in g4p_accounts/.")
        return 1

    any_failed = False
    for path in paths:
        account = get_account_manager(path=path)
        client = GpRequestClient(account)

        login_file_exists = Path(path).exists()
        has_valid_login = login_file_exists and account.is_valid_login()
        label = path.name

        if not has_valid_login:
            print(f"[INFO] {label}: 未检测到有效登录，将启动扫码登录流程。")
            try:
                login_flow(client, account)
            except Exception as exc:
                _show_message("登录失败", f"{label} 扫码登录失败：{exc}")
                any_failed = True
                continue
            _show_message("登录成功", f"{label} 扫码登录完成，登录信息已更新。")
            continue

        print(f"[INFO] {label}: 检测到已有登录，尝试刷新登录信息...")
        try:
            refreshed = _refresh_login(client, account)
        except Exception as exc:
            print(f"[WARN] {label}: 刷新登录失败：{exc}")
            refreshed = False

        if refreshed:
            _show_message("登录已更新", f"{label} 已刷新登录信息。")
            continue

        print(f"[WARN] {label}: 未找到可用的 wx_code，改为扫码登录。")
        try:
            login_flow(client, account)
        except Exception as exc:
            _show_message("登录失败", f"{label} 扫码登录失败：{exc}")
            any_failed = True
            continue
        _show_message("登录成功", f"{label} 扫码登录完成，登录信息已更新。")

    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
