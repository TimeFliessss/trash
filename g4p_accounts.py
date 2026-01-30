from pathlib import Path


ACCOUNTS_DIR = Path("g4p_accounts")


def ensure_accounts_dir() -> Path:
    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    return ACCOUNTS_DIR


def list_account_paths() -> list[Path]:
    if not ACCOUNTS_DIR.exists():
        return []
    files = sorted([p for p in ACCOUNTS_DIR.iterdir() if p.is_file() and p.suffix == ".txt"])
    return files


def is_multi_account_enabled() -> bool:
    return ACCOUNTS_DIR.exists() and any(ACCOUNTS_DIR.iterdir())


def make_account_path(name: str) -> Path:
    safe = "".join(c for c in name.strip() if c.isalnum() or c in ("-", "_"))
    if not safe:
        safe = "account"
    ensure_accounts_dir()
    return ACCOUNTS_DIR / f"{safe}.txt"
