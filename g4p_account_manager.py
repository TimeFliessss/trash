from pathlib import Path

from g4p_accounts import list_account_paths, make_account_path, ensure_accounts_dir
from g4p_battles import g4p_login


def _prompt(text: str, default: str | None = None) -> str:
    if default is None:
        return input(text).strip()
    value = input(f"{text} (default: {default}) ").strip()
    return value or default


def _list_accounts():
    paths = list_account_paths()
    if not paths:
        print("[INFO] No accounts found.")
        return []
    print("\n[INFO] Accounts:")
    for idx, p in enumerate(paths, 1):
        print(f"  [{idx}] {p}")
    return paths


def _add_account():
    ensure_accounts_dir()
    name = _prompt("Account name", "account")
    path = make_account_path(name)
    print(f"[INFO] Login to add account: {path.name}")
    g4p_login(account_path=path)
    print(f"[OK] Account added: {path}")


def _remove_account():
    paths = _list_accounts()
    if not paths:
        return
    raw = _prompt("Select account index to remove", "1")
    try:
        idx = int(raw)
    except ValueError:
        print("[ERROR] Invalid index.")
        return
    if not (1 <= idx <= len(paths)):
        print("[ERROR] Index out of range.")
        return
    path = paths[idx - 1]
    confirm = _prompt(f"Delete {path.name}? (y/N)", "n").lower()
    if confirm != "y":
        print("[INFO] Cancelled.")
        return
    path.unlink(missing_ok=True)
    print(f"[OK] Removed: {path}")


def main():
    while True:
        print("\nG4P Account Manager:")
        print("  [1] List accounts")
        print("  [2] Add account (login)")
        print("  [3] Remove account")
        print("  [0] Exit")
        choice = _prompt("Your choice", "0")
        if choice == "1":
            _list_accounts()
        elif choice == "2":
            _add_account()
        elif choice == "3":
            _remove_account()
        elif choice == "0":
            return 0
        else:
            print("[WARN] Invalid choice.")


if __name__ == "__main__":
    raise SystemExit(main())
