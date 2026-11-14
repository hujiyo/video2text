# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

产物是 onedir（程序目录），不是 onefile：CUDA 库和 Qt 体积大，
onefile 每次启动都要解压到临时目录，用户会明显感到卡顿。
"""

block_cipher = None

a = Analysis(
    ["gui_app.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 只保留用得到的 Qt 模块，PySide6 默认会带上 WebEngine、3D、Charts 等
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
        "PySide6.QtPdf",
        "PySide6.QtSql",
        "PySide6.QtSvgWidgets",
        "PySide6.QtOpenGLWidgets",
        "tkinter",
        "unittest",
        "pydoc",
        "email",
        "http",
        "xml",
        "pytest",
        "setuptools",
        "pip",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# 界面与音频用到的数据文件
a.datas += []

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
    disable_windowed_traceback=False,
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
