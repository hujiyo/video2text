# Video2Text — 视频/音频 转录 & 文本精校工具

基于 FunASR（Paraformer）的中文视频/音频转录 GUI 工具，支持一键完成 **语音识别 → 添加时间戳 → 去时间戳精校分段**。

## 功能

- 支持 mp4 / mkv / mov / avi / flv / wav / mp3 / m4a / aac / flac
- 自动提取音频并调用 Paraformer 模型进行中文语音识别
- 输出带时间戳的逐句转录结果
- 精校模式：去除时间戳，按句号分段落
- 一键流程：转录 + 精校一步到位

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

> 首次运行需下载模型（约 2GB），程序会自动从 ModelScope / HuggingFace 镜像下载，请保持网络通畅。

## 使用

1. 选择视频/音频文件 → 点击「① 开始转录」
2. 选择转录结果 txt → 点击「② 开始精校」
3. 或直接点击「⭐ 一键：转录 + 精校」

输出文件生成在源文件同目录下，命名格式为 `源文件名_转录结果.txt`、`源文件名_精校.txt`。

## 依赖

- Python 3.10+
- PyAV
- SoundFile
- NumPy
- FunASR (Paraformer-Large)

## 许可证

MIT