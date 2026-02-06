import csv
import ctypes
import json
import os
import subprocess
import sys
import time
import winreg
from pathlib import Path
from ctypes import wintypes

from bilibili_api import sync, user as bili_user

from bilibili.bili_auth import (
    BILI_LOGIN_INFO,
    load_credential,
    refresh_credential,
    save_credential,
)

# Shared config file (alert_config.json)
CONFIG_PATH = Path("alert_config.json")
TEMPLATE_PATH = Path("alert_config.template.json")

# Step 1: locate Bilibili Live Helper (直播姬) executable path.
# Executable name(s) to search for.
EXE_NAMES = ["livehime.exe"]
EXPLICIT_EXE_PATH = ""

# DisplayName keywords used when scanning registry uninstall keys.
DISPLAY_NAME_KEYWORDS = ["哔哩哔哩直播姬"]

# Window matching (editable).
WINDOW_TITLE = "哔哩哔哩直播姬"
WINDOW_CLASS = "Bilibili_Livehime_Chrome_WidgetWin_0"
HOTKEY_SEND_METHOD = "scancode"  # "scancode" or "vk"
DELAY_SECONDS = 3.0
PAUSE_ON_EXIT = True
POST_LIVE_PROGRAMS: list[dict] = []


def _normalize_path(value: str) -> str:
    value = value.strip().strip('"').strip()
    if "," in value:
        value = value.split(",", 1)[0].strip().strip('"')
    return value


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[WARN] Failed to parse {CONFIG_PATH}: {exc}")
    if TEMPLATE_PATH.exists():
        try:
            return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[WARN] Failed to parse {TEMPLATE_PATH}: {exc}")
    return {}


def _apply_config() -> None:
    global EXE_NAMES, DISPLAY_NAME_KEYWORDS, WINDOW_TITLE, WINDOW_CLASS
    global HOTKEY_SEND_METHOD, PAUSE_ON_EXIT, DELAY_SECONDS, EXPLICIT_EXE_PATH
    global POST_LIVE_PROGRAMS

    cfg = _load_config().get("one_key_live", {})
    if not isinstance(cfg, dict):
        return

    exe_path = cfg.get("exe_path")
    if isinstance(exe_path, str):
        EXPLICIT_EXE_PATH = exe_path.strip()
    names = cfg.get("exe_names")
    if isinstance(names, list) and names:
        EXE_NAMES = [str(x) for x in names if x]
    keywords = cfg.get("display_name_keywords")
    if isinstance(keywords, list) and keywords:
        DISPLAY_NAME_KEYWORDS = [str(x) for x in keywords if x]
    title = cfg.get("window_title")
    if isinstance(title, str) and title:
        WINDOW_TITLE = title
    cls = cfg.get("window_class")
    if isinstance(cls, str) and cls:
        WINDOW_CLASS = cls
    method = cfg.get("hotkey_send_method")
    if isinstance(method, str) and method:
        HOTKEY_SEND_METHOD = method
    delay = cfg.get("delay_seconds")
    if isinstance(delay, (int, float)) and delay >= 0:
        DELAY_SECONDS = float(delay)
    pause = cfg.get("pause_on_exit")
    if isinstance(pause, bool):
        PAUSE_ON_EXIT = pause
    programs = cfg.get("post_live_programs")
    if isinstance(programs, list):
        POST_LIVE_PROGRAMS = programs


def _launch_post_programs() -> None:
    if not POST_LIVE_PROGRAMS:
        return
    print("[INFO] Launching post-live programs...")
    for item in POST_LIVE_PROGRAMS:
        if isinstance(item, str):
            path = item
            args = []
            cwd = None
        elif isinstance(item, dict):
            path = item.get("path") or item.get("program") or ""
            args = item.get("args") or []
            cwd = item.get("cwd")
        else:
            continue
        if not path:
            continue
        cmd = [str(path)] + [str(a) for a in args]
        try:
            subprocess.Popen(cmd, cwd=cwd or None)
            print(f"[INFO] Started: {cmd}")
        except Exception as exc:
            print(f"[WARN] Failed to start {path}: {exc}")


def _find_exe_in_dir(folder: Path, exe_names: list[str]) -> Path | None:
    if not folder.exists() or not folder.is_dir():
        return None
    for name in exe_names:
        candidate = folder / name
        if candidate.exists():
            return candidate
    return None


def _iter_uninstall_keys() -> list[tuple[winreg.HKEYType, str]]:
    return [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]


def _search_registry_for_install() -> Path | None:
    keywords = [k.lower() for k in DISPLAY_NAME_KEYWORDS if k]
    if not keywords:
        return None
    for root, path in _iter_uninstall_keys():
        try:
            with winreg.OpenKey(root, path) as key:
                subkey_count, _, _ = winreg.QueryInfoKey(key)
                for i in range(subkey_count):
                    try:
                        subkey_name = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, subkey_name) as subkey:
                            try:
                                display_name, _ = winreg.QueryValueEx(subkey, "DisplayName")
                            except OSError:
                                continue
                            if not isinstance(display_name, str):
                                continue
                            name_lower = display_name.lower()
                            if not any(k in name_lower for k in keywords):
                                continue

                            # Try DisplayIcon
                            try:
                                display_icon, _ = winreg.QueryValueEx(subkey, "DisplayIcon")
                                icon_path = _normalize_path(str(display_icon))
                                if icon_path.lower().endswith(".exe") and Path(icon_path).exists():
                                    return Path(icon_path)
                            except OSError:
                                pass

                            # Try InstallLocation
                            try:
                                install_location, _ = winreg.QueryValueEx(subkey, "InstallLocation")
                                folder = Path(str(install_location))
                                exe = _find_exe_in_dir(folder, EXE_NAMES)
                                if exe:
                                    return exe
                            except OSError:
                                pass
                    except OSError:
                        continue
        except OSError:
            continue
    return None


def find_live_helper_path() -> Path | None:
    if EXPLICIT_EXE_PATH:
        exe = Path(EXPLICIT_EXE_PATH)
        if exe.exists():
            return exe
    return _search_registry_for_install()


def _prompt_exe_path() -> Path | None:
    while True:
        raw = input("Please input Live Helper exe path (or blank to quit): ").strip().strip('"')
        if not raw:
            return None
        exe = Path(raw)
        if exe.exists():
            return exe
        print(f"[ERROR] File not found: {exe}")


def _query_tasklist_pids(image_name: str) -> list[int]:
    result = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    stdout = (result.stdout or "").strip()
    if not stdout or "No tasks are running" in stdout:
        return []
    pids = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = next(csv.reader([line]))
        except Exception:
            continue
        if len(row) >= 2:
            try:
                pids.append(int(row[1]))
            except ValueError:
                continue
    return pids


def _get_running_pids() -> list[int]:
    for name in EXE_NAMES:
        pids = _query_tasklist_pids(name)
        if pids:
            return pids
    return []


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
EnumWindows = user32.EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
GetWindowThreadProcessId = user32.GetWindowThreadProcessId
IsWindowVisible = user32.IsWindowVisible
IsIconic = user32.IsIconic
GetWindowTextLengthW = user32.GetWindowTextLengthW
GetWindowTextW = user32.GetWindowTextW
GetClassNameW = user32.GetClassNameW
ShowWindowAsync = user32.ShowWindowAsync
BringWindowToTop = user32.BringWindowToTop
SetForegroundWindow = user32.SetForegroundWindow
SetActiveWindow = user32.SetActiveWindow
SetFocus = user32.SetFocus
GetForegroundWindow = user32.GetForegroundWindow
AttachThreadInput = user32.AttachThreadInput
GetCurrentThreadId = kernel32.GetCurrentThreadId
MapVirtualKeyW = user32.MapVirtualKeyW
GetCurrentProcess = kernel32.GetCurrentProcess
OpenProcessToken = advapi32.OpenProcessToken
GetTokenInformation = advapi32.GetTokenInformation
CloseHandle = kernel32.CloseHandle

SW_RESTORE = 9
SW_SHOW = 5
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_SCANCODE = 0x0008

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LWIN = 0x5B

TOKEN_QUERY = 0x0008
TokenElevation = 20

EXTENDED_KEYS = {
    0x21,  # PageUp
    0x22,  # PageDown
    0x23,  # End
    0x24,  # Home
    0x25,  # Left
    0x26,  # Up
    0x27,  # Right
    0x28,  # Down
    0x2D,  # Insert
    0x2E,  # Delete
    0x6F,  # Numpad divide
    0x90,  # NumLock
    0xA3,  # Right Ctrl
    0xA5,  # Right Alt
}


def _get_window_title(hwnd: wintypes.HWND) -> str:
    length = GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _get_window_class(hwnd: wintypes.HWND) -> str:
    buf = ctypes.create_unicode_buffer(256)
    length = GetClassNameW(hwnd, buf, 256)
    if length <= 0:
        return ""
    return buf.value


def _matches_window(hwnd: wintypes.HWND) -> bool:
    title = _get_window_title(hwnd)
    cls = _get_window_class(hwnd)
    if WINDOW_TITLE and WINDOW_TITLE not in title:
        return False
    if WINDOW_CLASS and WINDOW_CLASS != cls:
        return False
    return True


def _find_visible_window_by_pid(pid: int) -> wintypes.HWND | None:
    found = {"hwnd": None}

    def _callback(hwnd: wintypes.HWND, lparam: wintypes.LPARAM) -> wintypes.BOOL:
        if not IsWindowVisible(hwnd) and not IsIconic(hwnd):
            return True
        length = GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        proc_id = wintypes.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
        if proc_id.value == pid:
            if not _matches_window(hwnd):
                return True
            found["hwnd"] = hwnd
            return False
        return True

    EnumWindows(EnumWindowsProc(_callback), 0)
    return found["hwnd"]


def _force_foreground(hwnd: wintypes.HWND) -> None:
    if IsIconic(hwnd):
        ShowWindowAsync(hwnd, SW_RESTORE)
    else:
        ShowWindowAsync(hwnd, SW_SHOW)
    BringWindowToTop(hwnd)
    SetActiveWindow(hwnd)
    SetFocus(hwnd)

    fg = GetForegroundWindow()
    if fg:
        current_tid = GetCurrentThreadId()
        fg_tid = GetWindowThreadProcessId(fg, None)
        target_tid = GetWindowThreadProcessId(hwnd, None)
        AttachThreadInput(current_tid, fg_tid, True)
        AttachThreadInput(current_tid, target_tid, True)
        SetForegroundWindow(hwnd)
        AttachThreadInput(current_tid, fg_tid, False)
        AttachThreadInput(current_tid, target_tid, False)
    else:
        SetForegroundWindow(hwnd)


def _activate_window_for_pid(pid: int) -> bool:
    hwnd = _find_visible_window_by_pid(pid)
    if not hwnd:
        return False
    _force_foreground(hwnd)
    title = _get_window_title(hwnd)
    cls = _get_window_class(hwnd)
    print(f"[INFO] Found target window. title={title} class={cls}")
    if title:
        print(f"[INFO] Activated window: {title}")
    return True


ULONG_PTR = getattr(wintypes, "ULONG_PTR", ctypes.c_size_t)


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


def _wait_for_target_window(timeout_seconds: int = 60, poll_interval: float = 0.5) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        pids = _get_running_pids()
        for pid in pids:
            if _activate_window_for_pid(pid):
                return True
        time.sleep(poll_interval)
    return False


def _get_bili_uid() -> str | None:
    credential = load_credential()
    if not credential:
        print(f"[ERROR] Missing {BILI_LOGIN_INFO}. Run run_bili_login.bat first.")
        return None
    ok, err = refresh_credential(credential)
    if not ok:
        print(f"[ERROR] Bilibili login invalid: {err}")
        print("[HINT] Run run_bili_login.bat to re-login.")
        return None
    save_credential(credential)
    if credential.dedeuserid:
        return str(credential.dedeuserid)
    try:
        info = sync(bili_user.get_self_info(credential))
        mid = info.get("mid")
        if mid:
            return str(mid)
    except Exception as exc:
        print(f"[ERROR] Failed to fetch Bilibili UID: {exc}")
    return None


def _get_preferences_path(uid: str) -> Path:
    base = os.environ.get("LOCALAPPDATA", "")
    if not base:
        return Path("C:/Users") / "Public"
    return Path(base) / "bililive" / "User Data" / uid / "Preferences"


def _load_live_switch_hotkey(pref_path: Path) -> int | None:
    if not pref_path.exists():
        print(f"[ERROR] Preferences not found: {pref_path}")
        return None
    try:
        data = json.loads(pref_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[ERROR] Failed to parse Preferences: {exc}")
        return None
    value = None
    if isinstance(data, dict):
        hotkey = data.get("hotkey")
        if isinstance(hotkey, dict):
            value = hotkey.get("live_switch")
        if value is None:
            value = data.get("hotkey.live_switch")
    try:
        return int(value)
    except Exception:
        return None


def _parse_hotkey(value: int) -> tuple[list[int], int, list[str]]:
    key = value & 0xFFFF
    mods = (value >> 16) & 0xFFFF
    vk_mods = []
    labels = []
    if mods & 0x2:
        vk_mods.append(VK_CONTROL)
        labels.append("Ctrl")
    if mods & 0x4:
        vk_mods.append(VK_SHIFT)
        labels.append("Shift")
    if mods & 0x1:
        vk_mods.append(VK_MENU)
        labels.append("Alt")
    if mods & 0x8:
        vk_mods.append(VK_LWIN)
        labels.append("Win")
    labels.append(f"VK_{key:02X}")
    return vk_mods, key, labels


def _send_input(inputs: list[INPUT]) -> bool:
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    sent = ctypes.windll.user32.SendInput(n, arr, ctypes.sizeof(INPUT))
    if sent != n:
        err = ctypes.get_last_error()
        print(f"[WARN] SendInput sent {sent}/{n} events (err={err}).")
        return False
    return True


def _key_input(vk: int, keyup: bool = False) -> INPUT:
    flags = 0
    scan = 0
    use_scancode = HOTKEY_SEND_METHOD.lower() == "scancode"
    if vk in EXTENDED_KEYS:
        flags |= KEYEVENTF_EXTENDEDKEY
    if keyup:
        flags |= KEYEVENTF_KEYUP
    if use_scancode:
        scan = MapVirtualKeyW(vk, 0)
        if scan:
            flags |= KEYEVENTF_SCANCODE
            return INPUT(
                type=INPUT_KEYBOARD,
                union=INPUT_UNION(
                    ki=KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
                ),
            )
        # Fallback to VK if scancode is not available.
    return INPUT(
        type=INPUT_KEYBOARD,
        union=INPUT_UNION(
            ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)
        ),
    )


def _send_hotkey(modifiers: list[int], key: int) -> bool:
    seq: list[INPUT] = []
    for vk in modifiers:
        seq.append(_key_input(vk, keyup=False))
    seq.append(_key_input(key, keyup=False))
    seq.append(_key_input(key, keyup=True))
    for vk in reversed(modifiers):
        seq.append(_key_input(vk, keyup=True))
    if _send_input(seq):
        return True
    # Fallback: keybd_event
    try:
        for vk in modifiers:
            user32.keybd_event(vk, MapVirtualKeyW(vk, 0), 0, 0)
        user32.keybd_event(key, MapVirtualKeyW(key, 0), 0, 0)
        user32.keybd_event(key, MapVirtualKeyW(key, 0), KEYEVENTF_KEYUP, 0)
        for vk in reversed(modifiers):
            user32.keybd_event(vk, MapVirtualKeyW(vk, 0), KEYEVENTF_KEYUP, 0)
        return True
    except Exception:
        return False

def _is_admin() -> bool:
    try:
        token = wintypes.HANDLE()
        if not OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
            return False
        elevation = wintypes.DWORD()
        size = wintypes.DWORD()
        ok = GetTokenInformation(
            token,
            TokenElevation,
            ctypes.byref(elevation),
            ctypes.sizeof(elevation),
            ctypes.byref(size),
        )
        CloseHandle(token)
        if not ok:
            return False
        return elevation.value != 0
    except Exception:
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False


def _run_as_admin() -> bool:
    exe = sys.executable
    script = Path(__file__).resolve()
    args = [f'"{script}"', "--elevated"] + [
        f'"{arg}"' if " " in arg or "\t" in arg else arg for arg in sys.argv[1:]
    ]
    params = " ".join(args)
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    return result > 32


def _ensure_admin() -> None:
    if "--elevated" in sys.argv:
        return
    if _is_admin():
        return
    print("[INFO] Requesting administrator privileges...")
    if _run_as_admin():
        sys.exit(0)
    print("[ERROR] Administrator privileges required.")
    sys.exit(1)


def main() -> int:
    _ensure_admin()
    _apply_config()
    print("[INFO] Step 1: locating Bilibili Live Helper executable...")
    exe = find_live_helper_path()
    if not exe:
        print("[WARN] Live Helper executable not found in registry.")
        exe = _prompt_exe_path()
        if not exe:
            print("[ERROR] Live Helper executable not provided.")
            return 1
    print(f"[OK] Live Helper path: {exe}")

    uid = _get_bili_uid()
    if not uid:
        return 1
    pref_path = _get_preferences_path(uid)
    hotkey_value = _load_live_switch_hotkey(pref_path)
    if not hotkey_value:
        print("[ERROR] Live switch hotkey not set.")
        print("[HINT] Open Live Helper settings and set a hotkey for 'Start/Stop live'.")
        return 1
    modifiers, key, labels = _parse_hotkey(hotkey_value)
    print(f"[INFO] Live switch hotkey: {' + '.join(labels)} (0x{hotkey_value:08X})")
    delay_seconds = DELAY_SECONDS

    pids = _get_running_pids()
    if pids:
        print(f"[INFO] Live Helper already running. PIDs: {pids}")
        if not _wait_for_target_window(timeout_seconds=15):
            print("[WARN] Target window not found for existing process.")
            return 1
        print(f"[INFO] Sending live switch hotkey after {delay_seconds:.1f}s...")
        time.sleep(delay_seconds)
        if not _send_hotkey(modifiers, key):
            print("[WARN] Hotkey injection failed.")
        else:
            _launch_post_programs()
        return 0

    print("[INFO] Live Helper not running. Launching...")
    try:
        subprocess.Popen([str(exe)], cwd=str(exe.parent))
    except Exception as exc:
        print(f"[ERROR] Failed to launch Live Helper: {exc}")
        return 1
    print("[INFO] Live Helper launched. Waiting for target window...")
    if not _wait_for_target_window(timeout_seconds=60):
        print("[WARN] Target window not found within timeout.")
        return 1
    print(f"[INFO] Sending live switch hotkey after {delay_seconds:.1f}s...")
    time.sleep(delay_seconds)
    if not _send_hotkey(modifiers, key):
        print("[WARN] Hotkey injection failed.")
    else:
        _launch_post_programs()
    return 0


if __name__ == "__main__":
    if "--no-pause" in sys.argv:
        PAUSE_ON_EXIT = False
    code = 0
    try:
        code = main()
    except Exception as exc:
        import traceback

        print(f"[ERROR] Unhandled exception: {exc}")
        traceback.print_exc()
        code = 1
    if PAUSE_ON_EXIT:
        try:
            input("[INFO] Done. Press Enter to exit...")
        except Exception:
            pass
    raise SystemExit(code)
