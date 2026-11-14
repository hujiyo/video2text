# -*- coding: utf-8 -*-
"""程序入口。

打包后启动阶段的任何异常都写进日志文件，不弹窗。
用户遇到问题时只需把日志发来，不必手动截图或描述现象。
"""
import os
import sys
import traceback


def _log_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Video2Text", "startup_error.log")


def _record(exc_type, exc_value, exc_tb):
    try:
        path = _log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
            f.write("\n--- 环境 ---\n")
            f.write(f"frozen={getattr(sys, 'frozen', False)}\n")
            f.write(f"executable={sys.executable}\n")
            f.write(f"meipass={getattr(sys, '_MEIPASS', None)}\n")
            f.write(f"cwd={os.getcwd()}\n")
    except Exception:
        pass


def main():
    sys.excepthook = lambda *a: (_record(*a), sys.__excepthook__(*a))
    from gui_app import main as app_main

    app_main()


if __name__ == "__main__":
    main()
