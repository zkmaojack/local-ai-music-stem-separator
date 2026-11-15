# 开发与排查指南

Windows 安装包和日常操作见[图文使用说明](../README.md)。

## 从源码安装

1. 安装 64 位 [Python 3.11](https://www.python.org/downloads/windows/)。在 GitHub 仓库页面点击 **Code → Download ZIP**，解压后在包含 `pyproject.toml` 的目录打开 PowerShell。已安装 Git 的用户也可以克隆仓库。

   ```powershell
   git clone https://github.com/zkmaojack/local-ai-music-stem-separator.git
   cd local-ai-music-stem-separator
   ```

2. 创建虚拟环境。在 PowerShell 运行以下命令。

   ```powershell
   py -3.11 -m venv .venv
   ```

3. 安装 PyTorch。只有 CPU 或没有可用的 NVIDIA CUDA 显卡时，运行第一条。使用兼容的 NVIDIA 驱动和显卡时，可用第二条启用 CUDA 12.8。更多版本见 [PyTorch 安装说明](https://pytorch.org/get-started/previous-versions/#v2110)。

   ```powershell
   # CPU
   .\.venv\Scripts\python.exe -m pip install torch==2.11.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cpu

   # NVIDIA CUDA 12.8
   .\.venv\Scripts\python.exe -m pip install torch==2.11.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu128
   ```

4. 安装桌面依赖并准备本地模型，然后启动应用。

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -e '.[gui]'
   .\.venv\Scripts\stem-separator.exe setup-model --all
   .\.venv\Scripts\stem-separator-gui.exe
   ```

安装依赖和显式准备模型时需要联网。日常使用可直接在项目目录启动 `.venv\Scripts\stem-separator-gui.exe`，识别、分离和导出均在本机完成。

## 选择特定乐器和音效

导入歌曲后，点「选择乐器」可搜索或按「常用」「古典」「电子」多选。自选模式使用 MVSep Mega 53 的 53 个乐器和声部，以及 AudioSep 的三种实验性电子音效（升调、冲击、呼啸）。可同时选择乐器和音效，分离后的未提取部分保存在「剩余声音」。合并声部和它包含的单件乐器（如弦乐组和小提琴）需要二选一，避免叠加。菜单会提示重叠项。

自选模式标为实验性。混音中相近乐器可能串音，缺席乐器也可能有输出。不同录音中同种乐器的多个声部通常会出现在同一条轨里。音效模型按菜单顺序从剩余混音提取，两个音效声部重叠时，先提取的声部可能带走后续音效的一部分。

自选模式需要本地对应权重。扩展乐器约 1.27 GB，电子音效约 0.9 GB。大量音轨会增加临时磁盘占用，分离阶段的系统内存也随曲目长度和选轨数量增长。

## 分离质量

| 档位 | 适用场景 | 处理方式 |
| --- | --- | --- |
| 快速 | CPU 或显存较少的电脑 | YAMNet 扫描多个片段，默认用 Demucs 分离标准声部。 |
| 均衡 | 日常使用，默认 | 默认用单个 SubRoformer 分离标准声部。已缓存的 CLAP 可补充复核 YAMNet 候选。 |
| 高质量 | 愿意花更长时间处理 | 标准声部先后运行两个 SubRoformer 权重并融合。可选的 PaSST 提供待确认候选。手动细分时使用更密集的分块交叠。 |

自动选择模型时，即使识别没有可靠候选，仍会尝试分离人声、鼓、贝斯和其他。首选模型缺失或失败时可使用已安装的备用模型。显式指定模型时会保留该选择，并提示哪些候选没有单独提取。自动结果中较弱且缺少识别支持的音轨可能折叠，可从「音轨」菜单显示或导出。识别到的细分候选可在「选择乐器」中查看。

YAMNet 权重约 4 MB，CLAP 约 615 MB，SubRoformer 每份权重约 207 MB。更换档位后，点击「重新分离」可应用到当前歌曲。正式分离的耗时还取决于曲目长度、音轨数和设备。

## 离线模型

应用在本地运行 YAMNet。均衡和高质量会在 CLAP 文件齐备时复核低置信度的乐器，即使 YAMNet 已识别到部分音轨。缺少 CLAP 时使用 YAMNet，待确认项可在「选择乐器」中查看。

可使用 `stem-separator setup-model --passt` 下载可选的 PaSST OpenMIC 权重（约 342 MB）。高质量档会用缓存中的 PaSST 扫描多个窗口，额外发现的乐器进入「待确认」。评测方法、模型来源和实测限制见 [自动识别与分离验证报告](automatic-recognition-report.md)。

可以在联网电脑上执行 `setup-model --all`，再把整个 `.model-cache` 目录复制到离线电脑的项目目录。命令会逐个校验模型的 SHA-256。也可通过 `--model-dir <目录>` 指定已经存放模型的目录。

## 命令行用法

在项目目录运行。以下以放在项目目录的 `dd-groove.ogg` 为例，也可以把文件名替换为任意受支持的音频路径。

```powershell
.\.venv\Scripts\stem-separator.exe setup-model
.\.venv\Scripts\stem-separator.exe doctor
.\.venv\Scripts\stem-separator.exe separate '.\dd-groove.ogg' --device auto --output .\output
```

选择小提琴、大提琴和升调音效的示例。

```powershell
.\.venv\Scripts\stem-separator.exe setup-model --instruments --effects
.\.venv\Scripts\stem-separator.exe list-instruments
.\.venv\Scripts\stem-separator.exe separate '.\dd-groove.ogg' --instrument violin --instrument cello --instrument riser --output .\output\custom
```

标准四轨输出到 `output/dd-groove/`，文件名为 `vocals.wav`、`drums.wav`、`bass.wav` 和 `other.wav`。以上自选命令的输出位于 `output/custom/dd-groove/`。

- `--device auto` 优先使用 CUDA。显存不足时先缩短分块重试，仍失败时切换到 CPU。`--device cuda` 要求使用 GPU，`--device cpu` 强制使用 CPU。
- 输出是 44.1 kHz、24-bit PCM、立体声 WAV。各轨共享相同起点和帧数。源文件采样率不同会重采样。防止削波时会对四轨施加相同的增益，并在日志中显示该增益。
- 分离后打印模型、实际设备、耗时、帧数和 PyTorch 峰值显存。需要详细错误时，把 `--verbose` 放在 `separate` 前面。再次分离同名歌曲时可用 `--output` 指定另一个目录。

## 常见问题

- **缺少本地模型**。桌面提示会列出缺失文件的完整路径。将所需模型复制到对应目录后重试。命令行默认使用当前目录下的 `.model-cache`。桌面版优先使用当前目录中已存在的 `.model-cache`，否则使用用户本地应用数据目录。使用命令行指定模型目录时，请将 `--model-dir <目录>` 放在子命令之前。
- **CUDA 不可用**。运行 `doctor` 检查显卡、FFmpeg 和标准模型。默认模式会在没有可用 CUDA 时使用 CPU。
- **音轨有串音**。相近音色、混响或重叠音效可能分到同一轨，自选模式尤其容易出现这种情况。用「独听」确认再导出。

## 验证

Windows 安装器源码位于 `installer`。构建需要 Windows x64、项目的 Python 环境和带许可证文件的 uv Python 包。

```powershell
.\.venv\Scripts\python.exe -m pip install --target .runtime\uv uv==0.13.8
.\installer\build.ps1
```

构建产物和 SHA-256 校验文件位于 `build/release`。

可复现 AudioSep 分块接缝、导出与试听混音的幅度检查。下面的命令使用本地模型和《koi is love》前 24 秒，在新目录写入诊断 JSON 和比较用 WAV。短时响度用 3 秒未加权 RMS 表示。

```powershell
.\.venv\Scripts\python.exe tools/diagnose_stems.py '.\koi is love.flac' --effect whoosh --output .\output\audio-diagnostics
```

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[gui,test]'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

基础测试覆盖 WAV、MP3、FLAC 解码与重采样、无效输入、音轨导出、写入失败清理和权重校验。真实推理验收使用已缓存模型，在断网环境执行 `separate`，再用播放器或音频编辑器打开四个 WAV。比较各轨的起点、帧数和内容，并用一首 3–6 分钟的歌曲记录显存和耗时。

### v0.2.0 开发期间的本机实测

Windows 11，Python 3.11.18，PyTorch 2.11.0 `cu128`，RTX 5060 Laptop GPU，NVIDIA 驱动 621.01。本机使用的《Koi is Love》测试音频全长 108.719 秒，首次分离用时 23.43 秒。PyTorch 峰值分配显存 783 MiB，峰值保留显存 926 MiB。四条 WAV 均已重新解码，均为 4,794,523 帧。

- 使用本地权重并阻止 Python 网络访问后，3 分 41 秒的完整歌曲 `DD Groove` 在 CUDA 上用时 26.70 秒，峰值分配显存仍为 783 MiB。四个导出文件都通过了完整解码检查。 [Kevin MacLeod《DD Groove》，CC BY 3.0](https://commons.wikimedia.org/wiki/File:Kevin_MacLeod_-_04_-_DD_Groove.ogg)
- 屏蔽 CUDA 并阻止 Python 网络访问后，`--device auto` 自动切换到 CPU，原测试曲全长用时 34.66 秒。强制 CPU 时，同一曲目的 10 秒片段用时 5.23 秒。
- 模拟首次 CUDA 显存不足后，实际模型分块从 7.8 秒缩短至 3.9 秒，随后完成真实 GPU 分离，峰值分配显存为 363 MiB。
- 扩展模式在同一块 RTX 5060 Laptop GPU 上从 3 分 41 秒的 `DD Groove` 选取 9 项，连同剩余声音导出 10 条同步 WAV。禁止 Python 网络访问后完成，耗时 73.64 秒，PyTorch 峰值分配 867 MiB，峰值保留 1262 MiB。用这 10 条轨道短时测试了 Windows 播放和跳转，音频回调未报告缓冲欠载。
- URMP 中提琴和长笛双重奏的 12 秒样本中，中提琴的 SDR 提升 3.56 dB，长笛提升 0.21 dB。样本没有小提琴，模型仍给出了能量相对原混音约 −15 dB 的小提琴估计。这个结果只代表该测试样本。 [URMP 样本出处](https://github.com/MasayaKawamura/DDSP_Mixture_Model/blob/main/index.html)
- 叠加三种已知短音效的 14 秒测试混音中，升调、冲击和呼啸的 SDR 分别提升约 7.7、10.0、15.4 dB。冲击轨仍有较多串音。 [音效样本出处](https://sfxmint.com/sets/video-editing)

## 限制和依赖授权

- `htdemucs` 估计四种音源。串音和伪影会随曲目变化，`other` 包含其他乐器。长文件的输入和合成结果都在系统内存中，内存占用随时长增长。CPU 推理的耗时取决于设备和曲目长度。
- Demucs 源代码采用 MIT。官方作者说明预训练权重仅供科研用途，应用从模型发布地址下载权重用于本地技术验证。 [Demucs 许可证](https://github.com/facebookresearch/demucs/blob/main/LICENSE) · [预训练权重说明](https://github.com/facebookresearch/demucs/issues/327)
- PyTorch 和 TorchAudio 使用 BSD-3-Clause。SoundFile 使用 BSD-3-Clause，其 Windows 安装包包含 LGPL 的 libsndfile。本机使用的 `imageio-ffmpeg` Windows 安装包内 FFmpeg 为 GPL v3 或更新版本。后续 Windows 打包时应核对选定二进制、CUDA 运行库及全部依赖的再分发条款。 [PyTorch](https://github.com/pytorch/pytorch/blob/main/LICENSE) · [TorchAudio](https://github.com/pytorch/audio/blob/main/LICENSE) · [SoundFile](https://github.com/bastibe/python-soundfile) · [FFmpeg 法律说明](https://ffmpeg.org/legal.html)
- 桌面界面使用 PySide6 Essentials，播放使用 sounddevice 和 PortAudio。 [Qt for Python 授权](https://doc.qt.io/qtforpython-6/licenses.html) · [sounddevice 许可证](https://github.com/spatialaudio/python-sounddevice/blob/master/LICENSE) · [PortAudio 许可证](https://www.portaudio.com/license.html)
- 扩展乐器使用 ZFTurbo 发布的 MVSep Mega 53 v1.0.21，推理源码及该发布的权重采用 MIT。电子音效使用 Audio-AGI 的 MIT AudioSep 推理源码，权重来自其文档链接的 Apache-2.0 模型卡，分词使用 RoBERTa 的 MIT 文件。权重和配置均校验 SHA-256。 [MVSep 发布](https://github.com/ZFTurbo/Music-Source-Separation-Training/releases/tag/v1.0.21) · [MVSep 权重授权](https://github.com/ZFTurbo/Music-Source-Separation-Training/issues/245) · [AudioSep 源码](https://github.com/Audio-AGI/AudioSep) · [AudioSep 模型卡](https://huggingface.co/nielsr/audiosep-demo) · [RoBERTa](https://huggingface.co/FacebookAI/roberta-base)
- 自动识别使用 YAMNet 和 LAION CLAP。均衡和高质量档的标准声部可使用 ArchiMickey 的 SubRoformer，采用上游提交 `bf4e35aff7905c8cd43b8d7365ffd1bcc7d1df02` 的 PyTorch 推理模型源码，所附 MIT 许可证位于源码目录。 [YAMNet](https://github.com/tensorflow/models/tree/master/research/audioset/yamnet) · [CLAP 权重](https://huggingface.co/laion/clap-htsat-unfused) · [SubRoformer](https://github.com/ArchiMickey/SubRoformer)
