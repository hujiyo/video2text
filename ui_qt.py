# -*- coding: utf-8 -*-
"""应用界面（PySide6）。

主窗口负责：选模型、选文件、跑转录/精校、显示日志。
推理与音频处理全部复用 gui_app 里的既有逻辑，界面层只做交互。
"""
import os
import platform
import subprocess
import sys
import threading

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gui_app import (
    TIER_LABELS,
    available_tiers,
    close_server,
    pipeline_polish,
    pipeline_transcribe_many,
)

AUDIO_VIDEO_FILTER = "音视频 (*.mp4 *.mkv *.mov *.avi *.flv *.wav *.mp3 *.m4a *.aac *.flac);;所有文件 (*.*)"
TEXT_FILTER = "文本 (*.txt);;所有文件 (*.*)"

STYLE = """
QMainWindow { background: #1e1f22; }
QWidget { color: #dfe1e5; font-size: 13px; }
QGroupBox {
    border: 1px solid #3a3d43; border-radius: 8px;
    margin-top: 14px; padding-top: 14px; font-weight: 600;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #9aa0a6; }
QPushButton {
    background: #2d2f34; border: 1px solid #3a3d43; border-radius: 6px;
    padding: 7px 16px;
}
QPushButton:hover { background: #383b41; }
QPushButton:disabled { color: #5f6368; background: #26282c; }
QPushButton#primary {
    background: #2f6fed; border: none; color: white; font-weight: 600;
}
QPushButton#primary:hover { background: #3d7bf0; }
QPushButton#primary:disabled { background: #2a3446; color: #6b7a94; }
QComboBox, QLineEdit {
    background: #2a2c30; border: 1px solid #3a3d43; border-radius: 6px;
    padding: 6px 8px; selection-background-color: #2f6fed;
}
QComboBox::drop-down { border: none; width: 22px; }
QListWidget {
    background: #26282c; border: 1px solid #3a3d43; border-radius: 6px;
    padding: 4px;
}
QPlainTextEdit {
    background: #17181b; border: 1px solid #3a3d43; border-radius: 6px;
    font-family: Consolas, monospace; font-size: 12px; color: #c8cdd4;
}
QProgressBar {
    background: #26282c; border: none; border-radius: 4px; height: 6px;
    text-align: center; color: transparent;
}
QProgressBar::chunk { background: #2f6fed; border-radius: 4px; }
QLabel#status { color: #9aa0a6; }
QSplitter::handle { background: #2a2c30; height: 6px; }
"""


class LogBridge(QObject):
    """把工作线程里的日志送进界面线程。"""

    message = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self, log_fn):
        super().__init__()
        self.setWindowTitle("视频转录 · 文本精校")
        self.resize(1000, 780)

        self.log_fn = log_fn
        self.worker: threading.Thread | None = None
        self.last_transcript: str | None = None
        self.last_polished: str | None = None

        self._build_ui()
        self.setStyleSheet(STYLE)

    # ---------- 构建 ----------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(12)

        root.addWidget(self._build_model_bar())
        root.addWidget(self._build_transcribe_group())
        root.addWidget(self._build_polish_group())

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self._build_log_panel())
        splitter.addWidget(self._build_status_bar())
        splitter.setSizes([500, 60])
        root.addWidget(splitter, 1)

    def _build_model_bar(self):
        box = QGroupBox("模型")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(12, 8, 12, 8)

        self.combo_tier = QComboBox()
        self._tier_keys = available_tiers()
        self.combo_tier.addItems([TIER_LABELS[t] for t in self._tier_keys])
        self.combo_tier.setMinimumWidth(420)
        lay.addWidget(self.combo_tier)

        self.lbl_tier_hint = QLabel("切换模型会重新加载推理服务，首次约需 10 秒。")
        self.lbl_tier_hint.setObjectName("status")
        lay.addWidget(self.lbl_tier_hint, 1)

        btn_dir = QPushButton("模型目录…")
        btn_dir.clicked.connect(self._pick_model_dir)
        lay.addWidget(btn_dir)

        if not self._tier_keys:
            self.combo_tier.setEnabled(False)
            self.lbl_tier_hint.setText("未找到模型文件，请检查模型目录。")
        return box

    def _pick_model_dir(self):
        from asr_server import models_dir, save_config
        from gui_app import available_tiers

        cur = models_dir()
        d = QFileDialog.getExistingDirectory(self, "选择模型目录", cur)
        if not d:
            return
        save_config(models_dir=d)
        found = available_tiers()
        if not found:
            QMessageBox.warning(
                self, "提示",
                f"该目录下没有可用的模型：\n{d}\n\n"
                "请重新选择包含 Qwen3-ASR-*.gguf 与 mmproj-*.gguf 的目录，"
                "或重启程序重新下载。",
            )
            save_config(models_dir=cur)
            return

        # 换目录后刷新档位列表
        self._tier_keys = found
        self.combo_tier.clear()
        self.combo_tier.addItems([TIER_LABELS[t] for t in found])
        close_server()
        self.lbl_tier_hint.setText(f"已切换到 {d}，推理服务将在下次转录时重启。")

    def _build_transcribe_group(self):
        box = QGroupBox("步骤 1 · 转录")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 8, 12, 12)
        lay.setSpacing(8)

        row1 = QHBoxLayout()
        self.list_src = QListWidget()
        self.list_src.setSelectionMode(QListWidget.ExtendedSelection)
        self.list_src.setMinimumHeight(90)
        row1.addWidget(self.list_src, 1)

        col = QVBoxLayout()
        btn_add_src = QPushButton("添加文件")
        btn_add_src.clicked.connect(self._add_src)
        btn_del_src = QPushButton("移除选中")
        btn_del_src.clicked.connect(lambda: self._remove_selected(self.list_src))
        col.addWidget(btn_add_src)
        col.addWidget(btn_del_src)
        col.addStretch()
        row1.addLayout(col)
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("输出目录"))
        self.edit_outdir = QLineEdit()
        self.edit_outdir.setPlaceholderText("留空则与源文件放在同一目录")
        row2.addWidget(self.edit_outdir, 1)
        btn_pick = QPushButton("选择…")
        btn_pick.clicked.connect(self._pick_outdir)
        row2.addWidget(btn_pick)
        lay.addLayout(row2)

        row3 = QHBoxLayout()
        self.btn_transcribe = QPushButton("开始转录")
        self.btn_transcribe.setObjectName("primary")
        self.btn_transcribe.clicked.connect(self._on_transcribe)
        row3.addWidget(self.btn_transcribe, 1)

        self.btn_open_transcript = QPushButton("打开结果")
        self.btn_open_transcript.setEnabled(False)
        self.btn_open_transcript.clicked.connect(lambda: self._open(self.last_transcript))
        row3.addWidget(self.btn_open_transcript)
        lay.addLayout(row3)
        return box

    def _build_polish_group(self):
        box = QGroupBox("步骤 2 · 精校")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 8, 12, 12)
        lay.setSpacing(8)

        row1 = QHBoxLayout()
        self.list_txt = QListWidget()
        self.list_txt.setSelectionMode(QListWidget.ExtendedSelection)
        self.list_txt.setMinimumHeight(80)
        row1.addWidget(self.list_txt, 1)

        col = QVBoxLayout()
        btn_add_txt = QPushButton("添加 txt")
        btn_add_txt.clicked.connect(self._add_txt)
        btn_del_txt = QPushButton("移除选中")
        btn_del_txt.clicked.connect(lambda: self._remove_selected(self.list_txt))
        col.addWidget(btn_add_txt)
        col.addWidget(btn_del_txt)
        col.addStretch()
        row1.addLayout(col)
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        self.btn_polish = QPushButton("开始精校（按句分段）")
        self.btn_polish.clicked.connect(self._on_polish)
        row2.addWidget(self.btn_polish, 1)

        self.btn_open_polished = QPushButton("打开结果")
        self.btn_open_polished.setEnabled(False)
        self.btn_open_polished.clicked.connect(lambda: self._open(self.last_polished))
        row2.addWidget(self.btn_open_polished)
        lay.addLayout(row2)
        return box

    def _build_log_panel(self):
        box = QGroupBox("运行日志")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 8, 12, 12)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(5000)
        lay.addWidget(self.log_box)
        return box

    def _build_status_bar(self):
        frame = QFrame()
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(0, 0, 0, 0)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFixedHeight(6)
        lay.addWidget(self.progress, 1)

        self.lbl_status = QLabel("就绪")
        self.lbl_status.setObjectName("status")
        self.lbl_status.setMinimumWidth(140)
        lay.addWidget(self.lbl_status)

        btn_clear = QPushButton("清空日志")
        btn_clear.clicked.connect(self.log_box.clear)
        lay.addWidget(btn_clear)
        return frame

    # ---------- 交互 ----------

    def _add_src(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择视频/音频", "", AUDIO_VIDEO_FILTER)
        for f in files:
            if not self._list_has(self.list_src, f):
                self.list_src.addItem(f)

    def _add_txt(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择转录结果", "", TEXT_FILTER)
        for f in files:
            if not self._list_has(self.list_txt, f):
                self.list_txt.addItem(f)

    @staticmethod
    def _list_has(widget: QListWidget, text: str) -> bool:
        for i in range(widget.count()):
            if widget.item(i).text() == text:
                return True
        return False

    @staticmethod
    def _remove_selected(widget: QListWidget):
        for item in widget.selectedItems():
            widget.takeItem(widget.row(item))

    @staticmethod
    def _items(widget: QListWidget) -> list[str]:
        return [widget.item(i).text() for i in range(widget.count())]

    def _pick_outdir(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if d:
            self.edit_outdir.setText(d)

    def _open(self, path: str | None):
        if not path or not os.path.isfile(path):
            return
        if platform.system() == "Windows":
            os.startfile(path)  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.run(["open", path])
        else:
            subprocess.run(["xdg-open", path])

    def _set_busy(self, busy: bool, status: str = ""):
        for w in (
            self.combo_tier, self.btn_transcribe, self.btn_polish,
            self.btn_open_transcript, self.btn_open_polished,
        ):
            w.setEnabled(not busy)
        self.progress.setRange(0, 0) if busy else self.progress.setRange(0, 1)
        if busy:
            self.progress.setValue(0)
        self.lbl_status.setText(status or ("处理中…" if busy else "就绪"))

    def _current_tier(self) -> str:
        idx = self.combo_tier.currentIndex()
        if idx < 0:
            raise RuntimeError("请先选择一个模型。")
        return self._tier_keys[idx]

    def _run_job(self, job, done_msg: str):
        def runner():
            try:
                job()
            except Exception as e:
                self.log_fn(f"💥 出错：{type(e).__name__}: {e}")
            finally:
                self.progress.setRange(0, 1)
                self.progress.setValue(0)
                self.lbl_status.setText(done_msg)
                self._set_busy(False, done_msg)

        self._set_busy(True)
        self.worker = threading.Thread(target=runner, daemon=True)
        self.worker.start()

    def _on_transcribe(self):
        srcs = self._items(self.list_src)
        missing = [s for s in srcs if not os.path.isfile(s)]
        if not srcs or missing:
            QMessageBox.warning(
                self, "提示",
                "请先添加有效的视频/音频文件。\n找不到：\n" + "\n".join(missing)
                if missing else "请先添加有效的视频/音频文件。",
            )
            return
        tier = self._current_tier()

        def job():
            out_dir = self.edit_outdir.text().strip() or None
            outs = pipeline_transcribe_many(srcs, tier, self.log_fn, out_dir)
            if outs:
                self.last_transcript = outs[-1]
                self.btn_open_transcript.setEnabled(True)
                for o in outs:
                    if not self._list_has(self.list_txt, o):
                        self.list_txt.addItem(o)
            self.log_fn(f"🎯 共完成 {len(outs)}/{len(srcs)} 个文件。")

        self._run_job(job, "转录完成")

    def _on_polish(self):
        srcs = self._items(self.list_txt)
        missing = [s for s in srcs if not os.path.isfile(s)]
        if not srcs or missing:
            QMessageBox.warning(
                self, "提示",
                "请先添加有效的转录结果 txt。\n找不到：\n" + "\n".join(missing)
                if missing else "请先添加有效的转录结果 txt。",
            )
            return

        def job():
            outs = []
            for i, src in enumerate(srcs, 1):
                try:
                    outs.append(pipeline_polish(src, self.log_fn))
                except Exception as e:
                    self.log_fn(f"💥 第 {i} 个失败：{type(e).__name__}: {e}")
            if outs:
                self.last_polished = outs[-1]
                self.btn_open_polished.setEnabled(True)
            self.log_fn(f"🎯 共完成 {len(outs)}/{len(srcs)} 个文件。")

        self._run_job(job, "精校完成")

    def closeEvent(self, event):
        close_server()
        super().closeEvent(event)


def run_gui():
    app = QApplication(sys.argv)
    app.setApplicationName("视频转录 · 文本精校")
    app.setStyle("Fusion")

    if not available_tiers():
        from asr_server import models_dir
        from setup_wizard import DownloadDialog

        dlg = DownloadDialog(models_dir())
        if dlg.exec() != DownloadDialog.Accepted:
            sys.exit(0)

    bridge = LogBridge()

    def log(msg):
        bridge.message.emit(str(msg))

    window = MainWindow(log)
    bridge.message.connect(window.log_box.appendPlainText)
    window.show()
    sys.exit(app.exec())
