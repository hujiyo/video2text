# -*- coding: utf-8 -*-
"""应用界面（PySide6）。

单页：拖入文件、选输出目录、开始转录。转录结果直接分好段。
模型档位与目录、运行日志都收在「设置」里，平时不出现。
"""
import os
import platform
import subprocess
import sys
import threading
import time

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui_app import (
    TIER_LABELS,
    available_tiers,
    close_server,
    pipeline_transcribe_many,
)

EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".avi", ".flv", ".wmv", ".webm", ".ts", ".m4v",
    ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".wma",
}
AUDIO_VIDEO_FILTER = (
    "音视频 (*.mp4 *.mkv *.mov *.avi *.flv *.wmv *.webm *.ts *.m4v "
    "*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.wma);;所有文件 (*.*)"
)

STYLE = """
QMainWindow { background: #1e1f22; }
QWidget { color: #dfe1e5; font-size: 14px; }
QLabel#title { font-size: 19px; font-weight: 600; }
QLabel#hint { color: #9aa0a6; font-size: 13px; }
QLabel#status { color: #9aa0a6; font-size: 13px; }
QLabel#dropLabel { font-size: 16px; color: #cfd3d8; }
QLabel#dropSub { font-size: 12px; color: #7f8489; }
QFrame#drop {
    background: #23252a; border: 1px dashed #45494f; border-radius: 8px;
}
QFrame#drop[drag="true"] { background: #272b33; border-color: #2f6fed; }
QPushButton {
    background: #2d2f34; border: 1px solid #3a3d43; border-radius: 6px;
    padding: 7px 16px;
}
QPushButton:hover { background: #383b41; }
QPushButton:disabled { color: #5f6368; background: #26282c; }
QPushButton#primary {
    background: #2f6fed; border: none; color: white; font-weight: 600;
    padding: 11px 16px; font-size: 15px;
}
QPushButton#primary:hover { background: #3d7bf0; }
QPushButton#primary:disabled { background: #2a3446; color: #6b7a94; }
QPushButton#ghost {
    background: transparent; border: none; color: #9aa0a6; padding: 6px 10px;
}
QPushButton#ghost:hover { color: #dfe1e5; background: #2d2f34; }
QComboBox, QLineEdit {
    background: #2a2c30; border: 1px solid #3a3d43; border-radius: 6px;
    padding: 6px 8px; selection-background-color: #2f6fed;
}
QComboBox::drop-down { border: none; width: 22px; }
QPlainTextEdit {
    background: #17181b; border: 1px solid #3a3d43; border-radius: 6px;
    font-family: Consolas, monospace; font-size: 12px; color: #c8cdd4;
}
QProgressBar {
    background: #26282c; border: none; border-radius: 4px; height: 6px;
    text-align: center; color: transparent;
}
QProgressBar::chunk { background: #2f6fed; border-radius: 4px; }
"""


def human_time(sec: float) -> str:
    if sec < 60:
        return "<1 分钟"
    if sec < 3600:
        return f"约 {int(sec / 60)} 分钟"
    return f"约 {sec / 3600:.1f} 小时"


class DropZone(QFrame):
    """可点击可拖入的投放区。空态显示提示，有文件时切换成文件清单。"""

    clicked = Signal()
    dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setObjectName("drop")
        self.setAcceptDrops(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)

        self.empty = QWidget()
        empty_lay = QVBoxLayout(self.empty)
        empty_lay.addStretch()
        lbl = QLabel("拖入视频 / 音频，或点击选择")
        lbl.setObjectName("dropLabel")
        lbl.setAlignment(Qt.AlignCenter)
        empty_lay.addWidget(lbl)
        sub = QLabel("支持 mp4、mkv、mov、mp3、wav 等常见格式，可一次拖入多个或整个文件夹")
        sub.setObjectName("dropSub")
        sub.setAlignment(Qt.AlignCenter)
        empty_lay.addWidget(sub)
        empty_lay.addStretch()
        self.empty.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(self.empty)

        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        self.list.setFrameShape(QFrame.NoFrame)
        # 文件名常带长串 ID，宁可截断也不要横向滚动条
        self.list.setTextElideMode(Qt.ElideRight)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setWordWrap(False)
        self.list.setStyleSheet(
            "QListWidget { background: transparent; border: none; }"
            "QListWidget::item { padding: 4px 2px; }"
            "QListWidget::item:selected { background: #2f6fed; color: white; }"
        )
        self.list.setVisible(False)
        lay.addWidget(self.list)

    def set_files(self, paths: list[str]):
        """重画清单。空列表时回到提示态。"""
        self.empty.setVisible(not paths)
        self.list.setVisible(bool(paths))
        if not paths:
            self.list.clear()
            return
        # 整表重建只在增删时发生，量级是文件数，不涉及逐项查找
        self.list.clear()
        for p in paths:
            name = os.path.basename(p)
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, p)
            item.setToolTip(p)
            self.list.addItem(item)

    def _set_drag(self, on: bool):
        self.setProperty("drag", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event):
        # 清单有内容时，点击落在清单上由清单自己处理
        if event.button() == Qt.LeftButton and not self.list.isVisible():
            self.clicked.emit()
        super().mousePressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_drag(True)

    def dragLeaveEvent(self, event):
        self._set_drag(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._set_drag(False)
        paths = [u.toLocalFile() for u in event.mimeData().urls()]
        self.dropped.emit([p for p in paths if p])


class LogDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("运行日志")
        self.resize(760, 460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        self.box = QPlainTextEdit()
        self.box.setReadOnly(True)
        self.box.setMaximumBlockCount(5000)
        lay.addWidget(self.box)

    def append(self, line: str):
        self.box.appendPlainText(line)


class SettingsDialog(QDialog):
    """模型档位与目录。"""

    def __init__(self, win: "MainWindow", parent=None):
        super().__init__(parent or win)
        self.win = win
        self.setWindowTitle("设置")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)

        lay.addWidget(QLabel("识别模型"))
        self.combo = QComboBox()
        self._keys = win._tier_keys
        self.combo.addItems([TIER_LABELS[t] for t in self._keys])
        if win._tier_keys:
            self.combo.setCurrentIndex(max(0, win.combo_tier.currentIndex()))
        lay.addWidget(self.combo)

        hint = QLabel("档位越高越准、也越慢；切换后首次转录会重新加载模型，约需 10 秒。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        lay.addSpacing(6)
        lay.addWidget(QLabel("模型所在目录"))
        row = QHBoxLayout()
        from asr_server import models_dir

        self.edit_dir = QLineEdit(models_dir())
        self.edit_dir.setReadOnly(True)
        row.addWidget(self.edit_dir, 1)
        btn = QPushButton("选择…")
        btn.clicked.connect(self._pick_dir)
        row.addWidget(btn)
        lay.addLayout(row)

        lay.addSpacing(6)
        row2 = QHBoxLayout()
        btn_log = QPushButton("查看运行日志")
        btn_log.setObjectName("ghost")
        btn_log.clicked.connect(win.show_log)
        row2.addWidget(btn_log)
        row2.addStretch()
        btn_ok = QPushButton("完成")
        btn_ok.clicked.connect(self.accept)
        row2.addWidget(btn_ok)
        lay.addLayout(row2)

    def _pick_dir(self):
        from asr_server import save_config
        from gui_app import available_tiers

        cur = self.edit_dir.text()
        d = QFileDialog.getExistingDirectory(self, "选择模型目录", cur)
        if not d:
            return
        save_config(models_dir=d)
        found = available_tiers()
        if not found:
            QMessageBox.warning(
                self, "该目录没有模型",
                f"{d}\n\n里面没有可用的模型文件。请选择包含 "
                "Qwen3-ASR-*.gguf 与 mmproj-*.gguf 的目录。",
            )
            save_config(models_dir=cur)
            return

        self.edit_dir.setText(d)
        close_server()
        self.win.reload_tiers(found)

    def apply(self):
        idx = self.combo.currentIndex()
        if idx >= 0:
            self.win.combo_tier.setCurrentIndex(idx)


class MainWindow(QMainWindow):
    message = Signal(str)
    chunk_progress = Signal(int, int, int, int, str)   # 第几个, 总数, 已完成块, 总块, 文件
    finished = Signal(int, int, str)                  # 成功数, 总数, 最后结果路径

    def __init__(self):
        super().__init__()
        self.setWindowTitle("视频转录")
        self.resize(760, 720)
        self.setAcceptDrops(True)

        self._files: list[str] = []
        self._busy = False
        self._t0 = 0.0
        self._last_output: str | None = None

        self.combo_tier = QComboBox()   # 只在设置里出现，这里作为状态持有
        self._tier_keys = available_tiers()
        self.combo_tier.addItems([TIER_LABELS[t] for t in self._tier_keys])
        if not self._tier_keys:
            self.combo_tier.setEnabled(False)

        self.log_dialog = LogDialog(self)
        self.message.connect(self.log_dialog.append)
        self.chunk_progress.connect(self._on_progress)
        self.finished.connect(self._on_finished)

        self._build_ui()
        self.setStyleSheet(STYLE)
        self._refresh_files()

    # ---------- 构建 ----------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel("视频转录")
        title.setObjectName("title")
        head.addWidget(title)
        head.addStretch()
        btn_set = QPushButton("设置")
        btn_set.setObjectName("ghost")
        btn_set.clicked.connect(self._open_settings)
        head.addWidget(btn_set)
        root.addLayout(head)

        self.drop = DropZone()
        self.drop.clicked.connect(self._pick_files)
        self.drop.dropped.connect(self._add_paths)
        root.addWidget(self.drop, 1)

        row_files = QHBoxLayout()
        self.lbl_files = QLabel("")
        self.lbl_files.setObjectName("hint")
        row_files.addWidget(self.lbl_files, 1)
        btn_add = QPushButton("添加文件")
        btn_add.setObjectName("ghost")
        btn_add.clicked.connect(self._pick_files)
        row_files.addWidget(btn_add)
        self.btn_remove = QPushButton("移除选中")
        self.btn_remove.setObjectName("ghost")
        self.btn_remove.clicked.connect(self._remove_selected)
        row_files.addWidget(self.btn_remove)
        self.btn_clear = QPushButton("清空")
        self.btn_clear.setObjectName("ghost")
        self.btn_clear.clicked.connect(self._clear_files)
        row_files.addWidget(self.btn_clear)
        self.btn_open = QPushButton("打开结果")
        self.btn_open.setObjectName("ghost")
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self._open_result)
        row_files.addWidget(self.btn_open)
        root.addLayout(row_files)

        row_out = QHBoxLayout()
        row_out.addWidget(QLabel("输出到"))
        self.edit_outdir = QLineEdit()
        self.edit_outdir.setPlaceholderText("留空则与源文件放在同一目录")
        row_out.addWidget(self.edit_outdir, 1)
        btn_pick_out = QPushButton("选择…")
        btn_pick_out.clicked.connect(self._pick_outdir)
        row_out.addWidget(btn_pick_out)
        root.addLayout(row_out)

        self.btn_start = QPushButton("开始转录")
        self.btn_start.setObjectName("primary")
        self.btn_start.clicked.connect(self._start)
        root.addWidget(self.btn_start)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFixedHeight(6)
        root.addWidget(self.progress)

        self.lbl_status = QLabel("就绪")
        self.lbl_status.setObjectName("status")
        root.addWidget(self.lbl_status)

    # ---------- 文件列表 ----------

    def _refresh_files(self):
        n = len(self._files)
        if n == 0:
            self.lbl_files.setText("尚未添加文件")
        else:
            self.lbl_files.setText(f"已添加 {n} 个文件")
        self.drop.set_files(self._files)
        self.btn_clear.setEnabled(n > 0)
        self.btn_remove.setEnabled(n > 0)
        self.btn_start.setEnabled(n > 0 and not self._busy)

    def _remove_selected(self):
        items = self.drop.list.selectedItems()
        if not items:
            return
        gone = {it.data(Qt.UserRole) for it in items}
        self._files = [f for f in self._files if f not in gone]
        self._refresh_files()

    def _clear_files(self):
        self._files = []
        self._refresh_files()

    def _pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择视频/音频", "", AUDIO_VIDEO_FILTER)
        self._add_paths(files)

    def _add_paths(self, paths: list[str]):
        added = 0
        for p in paths:
            if os.path.isdir(p):
                for name in sorted(os.listdir(p)):
                    f = os.path.join(p, name)
                    if os.path.isfile(f) and os.path.splitext(f)[1].lower() in EXTENSIONS:
                        if f not in self._files:
                            self._files.append(f)
                            added += 1
            elif os.path.isfile(p) and p not in self._files:
                self._files.append(p)
                added += 1
        if added:
            self._refresh_files()
        elif paths:
            self.lbl_status.setText("没有可用的音视频文件。")

    def _pick_outdir(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if d:
            self.edit_outdir.setText(d)

    def _open_result(self):
        if not self._last_output:
            return
        if not os.path.exists(self._last_output):
            self.lbl_status.setText("结果文件已不在原位置。")
            return
        if platform.system() == "Windows":
            os.startfile(self._last_output)  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.run(["open", self._last_output])
        else:
            subprocess.run(["xdg-open", self._last_output])

    # ---------- 设置 ----------

    def _open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec() == QDialog.Accepted:
            dlg.apply()

    def reload_tiers(self, keys: list[str]):
        self._tier_keys = keys
        self.combo_tier.clear()
        self.combo_tier.addItems([TIER_LABELS[t] for t in keys])
        self.combo_tier.setEnabled(bool(keys))

    def show_log(self):
        self.log_dialog.show()
        self.log_dialog.raise_()
        self.log_dialog.activateWindow()

    def log(self, msg: str):
        """可从任意线程调用。"""
        self.message.emit(str(msg))

    # ---------- 转录 ----------

    def _current_tier(self) -> str:
        idx = self.combo_tier.currentIndex()
        if idx < 0:
            raise RuntimeError("没有可用模型，请先在设置里选择模型目录。")
        return self._tier_keys[idx]

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.btn_start.setEnabled(not busy and bool(self._files))
        self.btn_clear.setEnabled(not busy and bool(self._files))
        self.btn_remove.setEnabled(not busy and bool(self._files))
        self.btn_open.setEnabled(not busy and self._last_output is not None)
        self.btn_start.setText("转录中…" if busy else "开始转录")

    def _start(self):
        if self._busy or not self._files:
            return
        try:
            tier = self._current_tier()
        except RuntimeError as e:
            QMessageBox.warning(self, "提示", str(e))
            return

        files = list(self._files)
        out_dir = self.edit_outdir.text().strip() or None

        self._last_output = None
        self._t0 = time.time()
        self.lbl_status.setText("准备中…")
        self.progress.setValue(0)
        self._set_busy(True)

        def on_progress(i, n, d, t, name):
            self.chunk_progress.emit(i, n, d, t, name)

        def job():
            outs = []
            try:
                outs = pipeline_transcribe_many(files, tier, self.log, out_dir, on_progress)
            except Exception as e:
                self.log(f"💥 出错：{type(e).__name__}: {e}")
            finally:
                last = outs[-1] if outs else ""
                self.finished.emit(len(outs), len(files), last)

        threading.Thread(target=job, daemon=True).start()

    def _on_progress(self, i, n, d, t, name):
        frac = ((i - 1) + (d / t if t else 0.0)) / n
        self.progress.setRange(0, 1000)
        self.progress.setValue(int(frac * 1000))
        elapsed = time.time() - self._t0
        eta = f"　{human_time(elapsed / frac - elapsed)}" if frac > 0.02 else ""
        self.lbl_status.setText(f"{i} / {n}　{os.path.basename(name)}{eta}")

    def _on_finished(self, done, total, last):
        self._set_busy(False)
        self._last_output = last or None
        self.btn_open.setEnabled(bool(self._last_output))
        if done == 0 and total > 0:
            self.lbl_status.setText("全部失败，详见运行日志")
        elif done < total:
            self.lbl_status.setText(f"完成 {done} / {total}，有失败项，详见运行日志")
        else:
            self.lbl_status.setText(f"完成 {total} 个文件")
        self.log(f"🎯 完成 {done} / {total}")

    def closeEvent(self, event):
        close_server()
        super().closeEvent(event)


def run_gui():
    app = QApplication(sys.argv)
    app.setApplicationName("视频转录")
    app.setStyle("Fusion")

    if not available_tiers():
        from asr_server import models_dir
        from setup_wizard import DownloadDialog

        dlg = DownloadDialog(models_dir())
        if dlg.exec() != QDialog.Accepted:
            sys.exit(0)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())
