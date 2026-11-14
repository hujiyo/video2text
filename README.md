# Video2Text — 视频/音频转录工具

中文视频/音频转录 GUI 工具，拖入文件即可得到分好段的文本。

推理由 llama.cpp 承担（CUDA 加速），模型使用 Qwen3-ASR。

## 功能

- 支持 mp4 / mkv / mov / avi / flv / wmv / webm / wav / mp3 / m4a / aac / flac
- 自动提取音频并调用 Qwen3-ASR 进行中文语音识别
- 一次拖入多个文件或整个文件夹，单个失败不中断整批
- 长音频在低能量处切块，多路并发推理
- 输出按句号自动分段，可直接阅读
- 界面只有一页：投放区、输出目录、开始按钮

## 模型档位

| 档位 | 模型 | 下载 | 显存 | 相对实时速度 |
|---|---|---|---|---|
| 极速 | 0.6B Q8_0 | 1.0 GB | 约 3 GB | 约 120x |
| 推荐 | 1.7B Q8_0 | 2.5 GB | 约 5.5 GB | 约 75x |
| 高精 | 1.7B bf16 | 4.7 GB | 约 7.3 GB | 约 70x |

模型由程序首次启动时下载，界面里选择档位即可切换。

## 打包分发

用户拿到的产物是 `installer/Video2Text-Setup-<版本>.exe`，装完即用，不需要 Python 环境。

首次启动会弹出模型下载向导，从国内镜像拉取权重到默认模型目录（优先选剩余空间充足的 D/E/F 盘，如 `D:\Video2Text\models`）。模型不放在安装目录，因为装到 `Program Files` 时该目录对普通用户只读，且卸载重装后不必重新下载。

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

1. 把视频/音频文件或文件夹拖进窗口，也可以点投放区选文件
2. 需要时改输出目录（留空则与源文件同目录）
3. 点「开始转录」

输出文件名与源文件同名，后缀换成 `.txt`，正文按句号自动分段。
模型档位与模型目录在右上角「设置」里。

## 依赖

- Python 3.11+
- PyAV、SoundFile、NumPy
- llama.cpp 运行时（随 `runtime/` 分发）

## 许可证

MIT
