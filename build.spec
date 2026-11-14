# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

产物是 onedir（程序目录），不是 onefile：CUDA 库和 Qt 体积大，
onefile 每次启动都要解压到临时目录，用户会明显感到卡顿。
"""

block_cipher = None

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["rthook_dll_path.py"],
    # 只保留用得到的 Qt 模块，PySide6 默认会带上 WebEngine、3D、Charts 等
    # 只排除确定用不到的 Qt 模块与调试工具。
    # 标准库不能排除：urllib.request 依赖 email 与 http，排掉会让
    # 打包后的程序启动就报 ModuleNotFoundError。
    #
    # setuptools / pkg_resources 必须排除：PyInstaller 会为它们自动加
    # pyi_rth_pkgres 运行时钩子，该钩子在程序启动最早期执行，那时
    # Windows 的 DLL 目录还没登记，import plistlib 会连到 pyexpat，
    # 直接报 "DLL load failed while importing pyexpat"。本程序不用
    # 这两个包，去掉即可根除。
    excludes=[
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineQuick",
        "PySide6.QtQuick",
        "PySide6.QtQuick3D",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DRender",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtMultimedia",
        "PySide6.QtNetworkAuth",
        "PySide6.QtPositioning",
        "PySide6.QtSensors",
        "PySide6.QtSerialPort",
        "PySide6.QtTest",
        "PySide6.QtWebSockets",
        "PySide6.QtBluetooth",
        "PySide6.QtNfc",
        "PySide6.QtSql",
        "PySide6.QtSvgWidgets",
        "PySide6.QtOpenGLWidgets",
        "tkinter",
        "pytest",
        "setuptools",
        "pkg_resources",
        "distutils",
        "pip",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# qt.conf 让 Qt 在自己所在目录里找子模块。
# 缺了它，Qt6Core 会按编译期路径去找，找不到就报
# "DLL load failed while importing QtCore: 找不到指定的程序"（WinError 127）。
# PyInstaller 的 pyside6 钩子本应生成，实际漏了，这里显式补上。
import os

a.datas.append(("PySide6/qt.conf", os.path.join(SPECPATH, "qt.conf"), "DATA"))

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Video2Text",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX 会破坏 CUDA DLL 和 Qt 插件，必须关闭
    console=False,      # 打包后不显示黑框
    disable_windowed_traceback=True,  # 启动异常不弹窗，改由程序写入日志
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Video2Text",
)
