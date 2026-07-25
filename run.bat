@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "ENV_NAME=video"

rem Try conda from PATH first
where conda >nul 2>&1
if %errorlevel% equ 0 (
    echo [INFO] conda found in PATH.
    goto :activate_env
)

rem Try common install locations
for %%P in (
    "%USERPROFILE%\anaconda3"
    "%USERPROFILE%\miniconda3"
    "%LOCALAPPDATA%\anaconda3"
    "%LOCALAPPDATA%\miniconda3"
    "C:\anaconda3"
    "C:\miniconda3"
    "C:\ProgramData\anaconda3"
    "C:\ProgramData\miniconda3"
    "%USERPROFILE%\Anaconda3"
    "%USERPROFILE%\Miniconda3"
) do (
    if exist "%%~P\Scripts\activate.bat" (
        echo [INFO] Found conda at: %%~P
        call "%%~P\Scripts\activate.bat" "%%~P"
        goto :activate_env
    )
)

echo [ERROR] conda not found.
echo Please add conda to your PATH, or install Miniconda/Anaconda first:
echo   https://docs.conda.io/en/latest/miniconda.html
pause
exit /b 1

:activate_env
call conda activate %ENV_NAME%
if errorlevel 1 (
    echo [ERROR] environment "%ENV_NAME%" not found.
    echo Create it first:  conda create -n %ENV_NAME% python=3.10
    pause
    exit /b 1
)

cd /d "%SCRIPT_DIR%"
start "" pythonw.exe "%SCRIPT_DIR%\gui_app.py"
endlocal