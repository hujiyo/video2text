# Video2Text — 视频/音频 转录 & 文本精校工具

中文视频/音频转录 GUI 工具，一键完成 **语音识别 → 文本精校分段**。

推理由 llama.cpp 承担（CUDA 加速），模型使用 Qwen3-ASR。

## 功能

- 支持 mp4 / mkv / mov / avi / flv / wav / mp3 / m4a / aac / flac
- 自动提取音频并调用 Qwen3-ASR 进行中文语音识别
- 支持一次选中多个文件批量转录，单个失败不中断整批
- 长音频在低能量处切块，多路并发推理
- 精校模式：按句号分段落
- 一键流程：转录 + 精校一步到位

## 模型档位

| 档位 | 模型 | 下载 | 显存 | 相对实时速度 |
|---|---|---|---|---|
| 极速 | 0.6B Q8_0 | 1.0 GB | 约 3 GB | 约 120x |
| 推荐 | 1.7B Q8_0 | 2.5 GB | 约 5.5 GB | 约 75x |
| 高精 | 1.7B bf16 | 4.7 GB | 约 7.3 GB | 约 70x |

模型由程序首次启动时下载，界面里选择档位即可切换。

## 打包分发

用户拿到的产物是 `installer/Video2Text-Setup-<版本>.exe`，装完即用，不需要 Python 环境。

首次启动会弹出模型下载向导，从国内镜像拉取权重到 `%LOCALAPPDATA%\Video2Text\models`。模型放在用户目录而非安装目录，因为装到 `Program Files` 时该目录对普通用户只读，且卸载重装后不必重新下载。

构建：

```
build.bat
```

依次执行：PyInstaller 打包主程序 → 复制推理运行时 → Inno Setup 生成安装包。

需要本机具备：

- Python 环境（含 PyInstaller、PySide6）
- llama.cpp 运行时放在 `runtime/`
- Inno Setup 7
- CUDA Toolkit 12.x（打包时从其中取 `cublas64_12.dll`、`cublasLt64_12.dll`、`cudart64_12.dll`）

## 开发运行

```bash
conda env create -f environment.yml
conda activate video
python gui_app.py
```

或直接 `run.bat`。

## 目录结构

- `runtime/`：llama-server 及其依赖的 DLL、CUDA 运行库（不入库）
- `models/`：本地调试用的模型文件（不入库）
- `dist/`、`installer/`：构建产物（不入库）

## 使用

1. 选择模型档位
2. 选择视频/音频文件（可多选）→ 点击「① 开始转录」
3. 选择转录结果 txt（可多选）→ 点击「② 开始精校」
4. 或直接点击「⭐ 一键：转录 + 精校」

输出文件名与源文件同名，后缀换成 `.txt`。默认生成在源文件同目录下，也可以指定输出目录。

## 依赖

- Python 3.11+
- PyAV、SoundFile、NumPy
- llama.cpp 运行时（随 `runtime/` 分发）

## 许可证

MIT
