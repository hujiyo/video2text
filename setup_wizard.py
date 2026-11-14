# -*- coding: utf-8 -*-
"""首次启动的模型下载向导。

用户没模型时先显示这个窗口；下载完成后进入主界面。
"""
import os
import threading

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from model_downloader import MODEL_FILES, TIER_SIZES, download_tier

TIER_INFO = [
    ("balanced", "推荐 · 1.7B Q8_0",
     "识别准确率最高，速度约为实时的 75 倍。适合绝大多数场景。"),
    ("fast", "极速 · 0.6B Q8_0",
     "速度最快、显存占用最低，适合老显卡或大批量快速处理。"),
    ("precise", "高精 · 1.7B bf16",
     "不量化，理论精度上限最高。实测与 Q8_0 结果一致，体积与显存占用更大。"),
]

STYLE = """
QDialog { background: #1e1f22; }
QLabel { color: #dfe1e5; }
QLabel#title { font-size: 20px; font-weight: 600; }
QLabel#sub { color: #9aa0a6; font-size: 12px; }
QRadioButton { color: #dfe1e5; font-size: 14px; padding: 4px 0; }
QLabel#desc { color: #9aa0a6; font-size: 12px; }
QPushButton {
    background: #2d2f34; border: 1px solid #3a3d43; border-radius: 6px;
    padding: 8px 20px; color: #dfe1e5;
}
QPushButton:hover { background: #383b41; }
QPushButton#primary { background: #2f6fed; border: none; color: white; font-weight: 600; }
QPushButton#primary:hover { background: #3d7bf0; }
QPushButton#primary:disabled { background: #2a3446; color: #6b7a94; }
QProgressBar {
    background: #26282c; border: none; border-radius: 4px; height: 8px;
    text-align: center; color: transparent;
}
QProgressBar::chunk { background: #2f6fed; border-radius: 4px; }
"""


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


class ProgressBridge(QObject):
    progress = Signal(float, float, str)
    finished = Signal(str)
    failed = Signal(str)


class DownloadDialog(QDialog):
    def __init__(self, models_dir: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("首次启动 · 选择模型")
        self.setMinimumWidth(640)
        self.setStyleSheet(STYLE)
        self.models_dir = models_dir
        self.selected_tier = "balanced"
        self.worker = None
        self.cancel_event = threading.Event()
        self.done_tier = None

        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(10)

        title = QLabel("选择一个模型")
        title.setObjectName("title")
        lay.addWidget(title)

        sub = QLabel("模型只下载一次，之后启动会直接使用。下载源为国内镜像，无需代理。")
        sub.setObjectName("sub")
        lay.addWidget(sub)
        lay.addSpacing(10)

        self.group = QButtonGroup(self)
        for i, (key, name, desc) in enumerate(TIER_INFO):
            rb = QRadioButton(f"{name}　·　{human(TIER_SIZES[key])}")
            rb.setProperty("tier", key)
            self.group.addButton(rb)
            lay.addWidget(rb)

            d = QLabel(desc)
            d.setObjectName("desc")
            d.setContentsMargins(26, 0, 0, 6)
            lay.addWidget(d)

        # 默认选推荐档（1.7B Q8_0）
        self.group.buttons()[0].setChecked(True)

        lay.addSpacing(12)
        # 模型目录：默认在用户数据目录，允许改到别的盘（模型体积大，C 盘常不够）
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("保存到"))
        self.edit_dir = QLineEdit(self.models_dir)
        path_row.addWidget(self.edit_dir, 1)
        btn_browse = QPushButton("选择…")
        btn_browse.clicked.connect(self._pick_dir)
        path_row.addWidget(btn_browse)
        lay.addLayout(path_row)

        self.lbl_disk = QLabel("")
        self.lbl_disk.setObjectName("desc")
        self.lbl_disk.setContentsMargins(52, 0, 0, 0)
        lay.addWidget(self.lbl_disk)
        self._update_disk_hint()

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        lay.addWidget(self.progress)

        self.lbl_stat = QLabel("")
        self.lbl_stat.setObjectName("sub")
        lay.addWidget(self.lbl_stat)

        lay.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        self.btn_start = QPushButton("下载并开始")
        self.btn_start.setObjectName("primary")
        self.btn_start.clicked.connect(self._start)
        row.addWidget(self.btn_start)
        lay.addLayout(row)

        self.bridge = ProgressBridge()
        self.bridge.progress.connect(self._on_progress)
        self.bridge.finished.connect(self._on_finished)
        self.bridge.failed.connect(self._on_failed)

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择模型保存目录", self.edit_dir.text())
        if d:
            self.edit_dir.setText(d)
            self._update_disk_hint()

    def _update_disk_hint(self):
        """显示目标盘剩余空间，避免用户选了个不够的盘。"""
        try:
            import shutil
            target = self.edit_dir.text().strip() or self.models_dir
            # 目录可能还不存在，沿父级找到第一个存在的
            while target and not os.path.isdir(target):
                parent = os.path.dirname(target)
                if parent == target:
                    break
                target = parent
            free = shutil.disk_usage(target).free
            need = max(TIER_SIZES.values())
            warn = "　⚠️ 空间可能不足" if free < need else ""
            self.lbl_disk.setText(f"该盘剩余 {human(free)}，最大档需 {human(need)}{warn}")
        except Exception:
            self.lbl_disk.setText("")

    def _start(self):
        btn = self.group.checkedButton()
        if btn is None:
            QMessageBox.warning(self, "提示", "请先选择一个模型。")
            return
        self.selected_tier = btn.property("tier")

        target = self.edit_dir.text().strip()
        if not target:
            QMessageBox.warning(self, "提示", "请填写模型保存目录。")
            return
        try:
            os.makedirs(target, exist_ok=True)
            probe = os.path.join(target, ".write_test")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
        except Exception as e:
            QMessageBox.critical(
                self, "目录不可用",
                f"无法写入该目录：\n{target}\n\n{type(e).__name__}: {e}\n\n"
                "请换一个目录，或换一个盘。",
            )
            return
        self.models_dir = target

        self.btn_start.setEnabled(False)
        self.group.setEnabled(False)
        self.edit_dir.setEnabled(False)
        self.progress.setVisible(True)
        self.lbl_stat.setText("正在连接下载源…")

        def run():
            try:
                def cb(done, total, name):
                    self.bridge.progress.emit(done, total, name)

                download_tier(self.selected_tier, self.models_dir, cb, self.cancel_event)
                self.bridge.finished.emit(self.selected_tier)
            except Exception as e:
                self.bridge.failed.emit(f"{type(e).__name__}: {e}")

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _on_progress(self, done, total, name):
        self.progress.setValue(int(done / total * 1000) if total else 0)
        pct = done / total * 100 if total else 0
        self.lbl_stat.setText(f"{human(done)} / {human(total)}　{pct:.1f}%　{name}")

    def _on_finished(self, tier):
        self.done_tier = tier
        from asr_server import save_config

        save_config(models_dir=self.models_dir)
        self.lbl_stat.setText("下载完成。")
        self.accept()

    def _on_failed(self, msg):
        self.btn_start.setEnabled(True)
        self.group.setEnabled(True)
        self.lbl_stat.setText("")
        QMessageBox.critical(self, "下载失败", msg)

    def reject(self):
        if self.worker and self.worker.is_alive():
            self.cancel_event.set()
        super().reject()
