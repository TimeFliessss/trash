@echo off
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

echo.
echo [INFO] 准备运行和平精英精彩集锦脚本...

call :find_python
if errorlevel 1 (
    echo [ERROR] 未找到可用的 Python。请先安装 Python 3.10+，并确认可在命令行执行 ^`py --version^`。
    pause
    exit /b 1
)

if not exist "venv\Scripts\python.exe" (
    echo [INFO] 正在创建虚拟环境...
    %PY_CMD% -m venv "venv"
    if errorlevel 1 (
        echo [ERROR] 创建虚拟环境失败，请确认 Python 安装完整。
        pause
        exit /b 1
    )
)

set "VENV_PY=%SCRIPT_DIR%venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo [ERROR] 虚拟环境中没有 python.exe，请删除 venv 重新尝试。
    pause
    exit /b 1
)

set "PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple"
echo [INFO] 安装 Python 依赖（仅首次或版本变更时会稍慢）...
"%VENV_PY%" -m pip install --upgrade pip -i "%PIP_INDEX%" >nul 2>&1
if errorlevel 1 goto :pip_fail
"%VENV_PY%" -m pip install -r requirements.txt -i "%PIP_INDEX%" >nul 2>&1
if errorlevel 1 goto :pip_fail

set "COOKIE_PATH=%SCRIPT_DIR%cookie.txt"
if not exist "%COOKIE_PATH%" (
    >"%COOKIE_PATH%" echo PUT_YOUR_BILIBILI_COOKIE_HERE
    echo [WARN] 已生成 cookie.txt，请按照 README 步骤粘贴哔哩哔哩 Cookie 后重新运行。
    goto :need_cookie
)
findstr /C:"PUT_YOUR_BILIBILI_COOKIE_HERE" "%COOKIE_PATH%" >nul 2>&1
if not errorlevel 1 (
    echo [WARN] 检测到 cookie.txt 仍为占位内容，请粘贴真实的哔哩哔哩 Cookie。
    goto :need_cookie
)

echo [INFO] 启动本地 Web 服务，稍后会自动打开浏览器 http://127.0.0.1:8000 （按 Ctrl+C 可停止）。
start "" cmd /c "timeout /t 2 >nul & start \"\" \"http://127.0.0.1:8000\""
"%VENV_PY%" "%SCRIPT_DIR%main.py"
if errorlevel 1 goto :run_fail
exit /b 0

:pip_fail
echo [ERROR] 安装依赖失败，请检查网络或稍后重试。
pause
exit /b 1

:need_cookie
pause
exit /b 1

:run_fail
echo [ERROR] 脚本运行出错，详情请参考上方输出。
pause
exit /b 1

:find_python
for %%C in (py python python3) do (
    where %%C >nul 2>&1
    if not errorlevel 1 (
        set "PY_CMD=%%C"
        exit /b 0
    )
)
exit /b 1

