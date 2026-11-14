# -*- coding: utf-8 -*-
"""PyInstaller 运行时钩子：把打包目录加入 DLL 搜索路径。

PySide6 的 Qt6*.dll 放在 _internal/PySide6/ 下，而它们依赖的 icuuc.dll
等公共库在 _internal/ 根目录。Windows 加载子目录里的 DLL 时，默认只在
该子目录和系统目录里找依赖，找不到就报
"DLL load failed while importing QtCore"。
表现是时好时坏：偶尔系统缓存或 PATH 恰好命中。

必须在任何 Qt 模块导入之前执行，所以用 runtime hook 而不是普通模块。
"""
import os
import sys

if getattr(sys, "frozen", False):
    base = os.path.dirname(sys.executable)
    internal = os.path.join(base, "_internal")
    for d in (internal, os.path.join(internal, "PySide6")):
        if os.path.isdir(d):
            # Windows 8+ 需要显式登记，PATH 不再隐式生效
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(d)
                except OSError:
                    pass
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
