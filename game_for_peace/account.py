import json
from pathlib import Path

LOGIN_INFO_PATH = Path("LoginInfo.txt")


class AccountManager:
    def __init__(self, path=LOGIN_INFO_PATH):
        self.path = Path(path)
        self.login_info = None
        self.role_list = []
        self.game_open_id = ""
        self.game_id = "20004"
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            text = self.path.read_text(encoding="utf-8").strip()
            if text:
                self.login_info = json.loads(text)
        except Exception:
            self.login_info = None

    def is_valid_login(self):
        info = self.login_info or {}
        return bool(info.get("token") and info.get("userName") and info.get("userId"))

    def save_login_info(self, info):
        self.login_info = info
        self.path.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")

    def logout(self):
        self.login_info = None
        self.path.write_text("{}", encoding="utf-8")


def get_account_manager(path=None):
    global _ACCOUNT_SINGLETON
    if path is not None:
        return AccountManager(path=path)
    try:
        return _ACCOUNT_SINGLETON
    except NameError:
        _ACCOUNT_SINGLETON = AccountManager()
        return _ACCOUNT_SINGLETON
