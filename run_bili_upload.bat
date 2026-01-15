@echo off
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

echo.
echo [INFO] Starting Bilibili upload helper...

call :find_python
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.10+ and ensure ^`py --version^` works.
    pause
    exit /b 1
)

if not exist "venv\Scripts\python.exe" (
    echo [INFO] Creating virtual environment...
    %PY_CMD% -m venv "venv"
    if errorlevel 1 (
        echo [ERROR] Failed to create venv. Please reinstall Python.
        pause
        exit /b 1
    )
)

set "VENV_PY=%SCRIPT_DIR%venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo [ERROR] python.exe not found in venv. Please delete venv and retry.
    pause
    exit /b 1
)

set "PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple"
echo [INFO] Installing dependencies (first run may take longer)...
"%VENV_PY%" -m pip install --upgrade pip -i "%PIP_INDEX%" >nul 2>&1
if errorlevel 1 goto :pip_fail
"%VENV_PY%" -m pip install -r requirements.txt -i "%PIP_INDEX%" >nul 2>&1
if errorlevel 1 goto :pip_fail

echo [INFO] Running Bilibili upload...
"%VENV_PY%" -m bilibili.bili_upload
if errorlevel 1 goto :run_fail
exit /b 0

:pip_fail
echo [ERROR] Failed to install dependencies. Check network and retry.
pause
exit /b 1

:run_fail
echo [ERROR] Script failed. See output above.
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
