# Video2Text — 视频/音频 转录 & 文本精校工具

基于 Qwen3-ASR 的中文视频/音频转录 GUI 工具，支持一键完成 **语音识别 → 文本精校分段**。

## 功能

- 支持 mp4 / mkv / mov / avi / flv / wav / mp3 / m4a / aac / flac
- 自动提取音频并调用 Qwen3-ASR-1.7B 进行中文语音识别
- 支持一次选中多个文件批量转录，单个失败不中断整批
- 长音频按 60 秒切块，块大小与批大小按显卡剩余显存自动调整
- 精校模式：按句号分段落
- 一键流程：转录 + 精校一步到位
- 有 NVIDIA 显卡时自动使用 GPU，否则回退 CPU

## 安装

### 方式一：conda（推荐）

```bash
conda env create -f environment.yml
conda activate video
python gui_app.py
```

### 方式二：pip

```bash
python -m venv venv
venv\Scripts\activate     # Windows
# source venv/bin/activate  # Linux/macOS
pip install -r requirements.txt
python gui_app.py
```

> GPU 用户需安装 CUDA 版 torch，例如：
> `pip install --index-url https://download.pytorch.org/whl/cu126 torch torchaudio`

> 首次运行需下载模型（约 4.7GB），模型缓存在 `HF_HOME` 指定的目录。

## 使用

1. 选择视频/音频文件（可多选）→ 点击「① 开始转录」
2. 选择转录结果 txt（可多选）→ 点击「② 开始精校」
3. 或直接点击「⭐ 一键：转录 + 精校」

输出文件名与源文件同名，后缀换成 `.txt`。默认生成在源文件同目录下，也可以指定输出目录。

转录结果按 60 秒切块批量推理，块大小由显卡剩余显存决定：显存越充足，一次拼的块越多，吞吐越高；显存不足时自动退到单块。

## 依赖

- Python 3.11+
- PyAV
- SoundFile
- NumPy
- Qwen3-ASR (1.7B)
- PyTorch

## 许可证

MIT
