@echo off
rem 一键构建：PyInstaller 打包主程序 -> 复制运行时 -> Inno Setup 生成安装包
rem 用法：build.bat
setlocal enabledelayedexpansion

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

echo [1/4] 清理旧产物...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "installer" rmdir /s /q "installer"

echo [2/4] PyInstaller 打包...
python -m PyInstaller --clean --noconfirm build.spec
if errorlevel 1 (
    echo [ERROR] PyInstaller 失败
    exit /b 1
)
echo [3/4] 复制推理运行时...
if not exist "dist\Video2Text\runtime" mkdir "dist\Video2Text\runtime"
xcopy /Y /Q "runtime\llama-server.exe" "dist\Video2Text\runtime\" >nul
xcopy /Y /Q "runtime\*.dll" "dist\Video2Text\runtime\" >nul
rem CUDA 运行库来自本机 CUDA Toolkit，发行版需要一并带上
for %%F in (cublas64_12.dll cublasLt64_12.dll cudart64_12.dll) do (
    if exist "%CUDA_HOME%\bin\%%F" (
        xcopy /Y /Q "%CUDA_HOME%\bin\%%F" "dist\Video2Text\runtime\" >nul
    ) else if exist "D:\cuda-v12.9\bin\%%F" (
        xcopy /Y /Q "D:\cuda-v12.9\bin\%%F" "dist\Video2Text\runtime\" >nul
    )
)

echo [4/4] Inno Setup 生成安装包...
set "ISCC=C:\Program Files (x86)\Inno Setup 7\ISCC.exe"
if not exist "%ISCC%" set "ISCC=D:\Inno Setup 7\ISCC.exe"
if not exist "%ISCC%" (
    echo [ERROR] 未找到 ISCC.exe，请安装 Inno Setup
    exit /b 1
)
"%ISCC%" installer.iss
if errorlevel 1 (
    echo [ERROR] Inno Setup 失败
    exit /b 1
)

echo.
echo 完成。安装包在 installer\ 目录。
endlocal
