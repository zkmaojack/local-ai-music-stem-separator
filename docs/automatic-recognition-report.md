# 自动识别与分离验证报告

## 自动分离修复

旧版快速识别在 `koi is love.flac` 上返回空列表，均衡档只提出合成器。旧路由把空列表改成 `remainder`，工作进程的推理时间为 **0.0 秒**，只写出原混音。固定四轨模型的筛选也可能把合成器等候选丢弃。

现在先解码并确认输入有声音，再单独决定可使用的分离模型。「自动选择模型」优先运行对应档位的标准四轨，缺权重或运行失败时尝试已缓存的备用模型。明确指定的模型沿用用户选择。未单独提取的乐器仍可在选择菜单查看。指定细分模型时优先提取有本地权重的候选，必要时使用该模型自身的常用声部。

验轨会拒绝长度、声道或数值不正确的模型输出，并记录峰值、RMS、静音窗口、削波样本和相对原混音的能量。自动分离会将未提取的内容保留在「其他」或「剩余声音」。数字静音轨会移除，较弱且缺乏识别支持的常用声部会先折叠，仍可试听与导出。整首音频静音、解码失败和工作进程失败分别处理。

## 识别对比

用 [BabySlakh](https://zenodo.org/records/4603870) 提供的音轨重新混合 20 首歌各自的 16 秒片段。前 8 首用于比较模型和检查阈值，后 12 首留出。歌曲分组互斥。只有实际提供且片段内有足够能量的参考轨才生成标签。MIDI 音色是合成音色，标签覆盖及音色分布有限。另用 [MRDLA 的三个 MUSDB18 公开试听片段](https://tomohikonakamura.github.io/Tomohiko-Nakamura/demo/MRDLA/) 验证常用声部，并用 [URMP 的中提琴与长笛研究片段](https://github.com/MasayaKawamura/DDSP_Mixture_Model/tree/main/demo/urmp_Sample_Va_Fl) 做古典对照。

下表只计混音端识别器的直接候选。单独统计每个可比较类别的结果见评测 JSON。未标注的类别不参与真实录音样本计分。

| BabySlakh 留出 12 首 | 精确率 | 召回率 | 微平均 F1 | 误报 / 漏报 | 单片段耗时中位数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| YAMNet 快速 | 无候选 | 0% | 0.000 | 0 / 54 | 0.16 s |
| YAMNet + CLAP | 100% | 1.9% | 0.036 | 0 / 53 | 0.98 s |
| PaSST OpenMIC，CUDA | 20% | 3.7% | 0.062 | 8 / 52 | 0.35 s |
| EfficientAT mn04，CPU | 100% | 1.9% | 0.036 | 0 / 53 | 0.16 s |

PaSST 的 CPU 中位数为 6.74 秒，峰值进程工作集约 922 MiB。CUDA 评测的峰值约为 1,598 MiB RAM 和 373 MiB PyTorch 已分配显存。EfficientAT 的峰值工作集约 612 MiB，耗时表不含一次性加载 0.11 秒。PaSST 在这个留出集多找回一个真候选，但误报明显增加，因此高质量档仅用它补充「待确认」。EfficientAT 的正式发布未找到可用的 OpenMIC 分类权重，实测的是官方 AudioSet 权重，桌面版继续用 YAMNet。还用 PaSST 复核了分离后的人声估计，一个无真人唱声的留出片段仍得到了 0.895 的原始分数，因此没有用该模型自动判定声部有无。

三个真实录音的 9 个已标注常用声部，现有 YAMNet、YAMNet + CLAP 和 PaSST 在混音端都漏检。新路线运行标准四轨后，三首都得到并显示了有参考音轨可验证的人声、鼓和贝斯。这里改善的是**端到端可用结果**。在两个合成片段，Demucs 仍把部分声音串入了人声轨，且有一次把确实存在的弱贝斯先折叠。折叠规则只用于初始显示。

## 分离质量与资源

SI-SDR 越高越接近对应参考轨。旧路线如果直接输出原混音，没有可单独试听的对应音轨。表中使用未分离混音作数值基线。正式新版的三段真实录音各 10 秒。

| 三段真实录音，SI-SDR 中位数 | 原混音 | 均衡档 SubRoformer |
| --- | ---: | ---: |
| 人声 | −6.28 dB | 16.93 dB |
| 鼓 | −0.66 dB | 16.94 dB |
| 贝斯 | −8.00 dB | 11.89 dB |
| 其他 | −7.65 dB | 13.21 dB |

新版三个片段的立体声重混 RMS 误差均约 `2.4e-7`，接近 24-bit WAV 量化误差。用已知参考音轨做线性投影估计串音，人声的目标与干扰能量比中位数从 −6.34 dB 提升至 38.21 dB，鼓从 −0.59 dB 提升至 29.13 dB，贝斯从 −8.21 dB 提升至 26.82 dB。该投影只估计能由已知参考轨解释的干扰，没有做正式盲听评分。另一组分轨试验覆盖以下实际表现：

- 两段有参考鼓轨的 BabySlakh 片段，快速档鼓轨 SI-SDR 从 −13.92 / −20.56 dB 升到 12.47 / 7.84 dB。贝斯三段分别从 −12.77 / −2.55 / −2.45 dB 变成 −10.04 / −3.79 / 7.48 dB。
- Mega 53 提取中提琴时从 −0.74 dB 升到 8.76 dB。同一段长笛从 0.69 dB 降到 −0.50 dB。合成器从 −11.72 dB 降到 −25.35 dB。两个细分任务的进程峰值约 2.45–2.52 GiB RAM，PyTorch 已分配显存 258–400 MiB。显存数字只代表本项目的少数目标分块推理。
- AudioSep 对一个叠加到真实录音上的合成升频效果，从 −16.80 dB 提升至 −4.95 dB，仍有明显误差。耗时 26.0 秒，峰值工作集约 2.38 GiB，PyTorch 已分配显存 563 MiB。
- 同一段真实录音上，快速档与均衡档的分离服务耗时分别约 18.3 秒和 22.7 秒，峰值工作集分别约 1.29 和 1.98 GiB，PyTorch 已分配显存约 783 和 934 MiB。高质量双模型用时 57.0 秒，峰值约 2.41 GiB RAM 和 1,104 MiB 已分配显存，四条轨的 SI-SDR 与均衡档相差不到 0.2 dB。冷启动与本机负载会影响耗时。
- 强制 Demucs 使用 CPU 的 10 秒样本成功导出，分离服务耗时约 4.0 秒，峰值工作集 1.52 GiB。CPU 和 CUDA 的冷启动不同，这一段不能据此比较长期吞吐量。

运行环境为 Windows，RTX 5060 Laptop GPU（可见显存 8,151 MiB），NVIDIA 驱动 621.01，PyTorch `2.11.0+cu128`，CUDA 12.8。显存为 PyTorch 的峰值分配量。工作集为分离子进程的 Windows 峰值，不含独立桌面进程。正常推理只打开本地缓存。

## 桌面验证

用 Qt Windows 平台启动了实际桌面版。`koi is love.flac` 快速档走完导入、自动识别、Demucs 分轨、播放、定位、独听与 WAV 导出。结果生成四条音轨，默认显示鼓、贝斯和其他，较弱人声轨可从菜单恢复。分离耗时 20.7 秒，峰值约 1.65 GiB RAM / 783 MiB 显存。另一首 CloudMusic 中的管弦乐录音在均衡档识别到弦乐组和小号候选，随后完成 SubRoformer 分离及导出。分离耗时 136.1 秒，峰值约 2.66 GiB RAM / 934 MiB 显存。两个桌面测试的 100 ms 界面定时器最大回调间隔约 0.29 秒。原曲仅作为只读输入。桌面自动验收使用测试目录替代了导出文件夹选择器。

建议默认使用「自动选择模型 → 均衡」。需要低配置或较快处理时使用快速。需要特定的乐器时，从选择菜单点选较少目标试听，分离质量随曲目、音色和串音程度变化。

47 项自动化测试通过，覆盖空候选、固定模型限制、备用模型、缺权重、静音、坏文件、识别或分离报错、可选 PaSST、验轨、取消和换歌。高质量档在 `koi is love.flac` 的单独识别验证中确实运行了 YAMNet、CLAP 和 PaSST，候选为合成器，长笛保留待确认。原始窗口证据包含时间、模型与分数，可在工作进程事件中检查。

本次主要修改了 `application/planning.py`、`application/recognition.py`、`application/separation.py`、`application/stem_validation.py` 与 PaSST 适配器。桌面接入位于 `gui/app.py`、`gui/song_setup.py` 和 `gui/worker.py`。模型校验与显式下载入口位于 `storage/model_cache.py` 和 `cli.py`。评测脚本位于 `tools/`，本机保留的逐类指标、桌面检查报告和资源统计位于 `build/verification/`。测试没有将参考音频或私人音乐加入安装包。

## 复现与模型来源

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=build/recognition-qa/pytest-local
.\.venv\Scripts\stem-separator.exe setup-model --passt
.\.venv\Scripts\python.exe tools/benchmark_recognition.py build/recognition-qa/excerpts --prepare 'build/recognition-qa/slakh/babyslakh_16k' --engine yamnet
.\.venv\Scripts\python.exe tools/benchmark_recognition.py build/recognition-qa/excerpts --engine clap
.\.venv\Scripts\python.exe tools/benchmark_recognition.py build/recognition-qa/excerpts --engine passt
.\.venv\Scripts\python.exe tools/benchmark_separation.py build/recognition-qa/mrdla-0/mix.wav build/recognition-qa/score-retest --quality balanced --reference 'vocals=build/recognition-qa/mrdla-0/gt_vocals.wav'
.\.venv\Scripts\python.exe tools/check_automatic_gui.py 'koi is love.flac' build/recognition-qa/gui-retest
```

- [PaSST v0.0.5](https://github.com/kkoutini/PaSST/releases/tag/v0.0.5) 的 20 类 `openmic-passt-s-f128-10sec-p16-s10-ap.85.pt`，SHA-256 `dc229428753176e8be0373d25887116fc15b490af86f671cecf9ed76a0f287da`。推理适配器使用 Apache-2.0 上游代码的结构与预处理。作者说明权重适用与代码相同的许可，但以训练数据条款允许为前提，权重由用户单独下载。[作者说明](https://github.com/kkoutini/PaSST/issues/51) · [OpenMIC 数据说明](https://zenodo.org/records/1432913)
- [EfficientAT v0.0.1](https://github.com/fschmid56/EfficientAT/releases/tag/v0.0.1) 的 `mn04_as_mAP_432.pt`，SHA-256 `899a8c6217063f6941793102a2780b3dd788ab11fce468d9cf5f0577c453321c`。评测代码来自上游 `a425fdce92572e602a1d5634799bd9f1f2efa806`（MIT），使用独立的 torchvision `0.26.0+cpu` 和 Pillow `13.0.0`，未加入应用依赖。可用 `tools/benchmark_efficientat.py <excerpts> <reviewed-checkout> <weights>` 复现。
- 现有 YAMNet、LAION CLAP、Demucs、SubRoformer、Mega 53 v1.0.21 与 AudioSep 的下载来源及 SHA-256 在 [`model_cache.py`](../src/stem_separator/storage/model_cache.py) 中固定。程序通过本地文件校验后加载权重。
