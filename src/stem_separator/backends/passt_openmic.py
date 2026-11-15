"""Inference for the Apache-2.0 PaSST OpenMIC model (kkoutini/PaSST v0.0.5).

Architecture and log-mel settings follow the upstream models/passt.py,
models/preprocess.py and ex_openmic.py. Training-only Patchout is inactive.
"""

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from torchaudio.compliance.kaldi import get_mel_banks

from stem_separator.application.types import Progress
from stem_separator.audio.decode import has_signal

RATE = 32000
# Index order from OpenMIC class-map.json. Cymbals and mallet percussion have no exact
# selectable model output: a detection must not be relabelled as a specific cymbal.
CLASSES = ("accordion", "banjo", "bass", "cello", "clarinet", "cymbals", "drums", "flute", "guitar",
           "mallet-percussion", "mandolin", "organ", "piano", "saxophone", "synth", "trombone", "trumpet",
           "ukulele", "violin", "vocal")
WIDTH = 768


class Attention(nn.Module):
    def __init__(self):
        super().__init__()
        self.qkv = nn.Linear(WIDTH, 3 * WIDTH)
        self.proj = nn.Linear(WIDTH, WIDTH)

    def forward(self, x):
        batch, length, _ = x.shape
        q, k, v = self.qkv(x).reshape(batch, length, 3, 12, 64).permute(2, 0, 3, 1, 4).unbind(0)
        attended = F.scaled_dot_product_attention(q, k, v)
        return self.proj(attended.transpose(1, 2).reshape(batch, length, WIDTH))


class Mlp(nn.Module):
    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(WIDTH, 4 * WIDTH)
        self.fc2 = nn.Linear(4 * WIDTH, WIDTH)

    def forward(self, x):
        return self.fc2(F.gelu(self.fc1(x)))


class Block(nn.Module):
    def __init__(self):
        super().__init__()
        self.norm1 = nn.LayerNorm(WIDTH, eps=1e-6)
        self.attn = Attention()
        self.norm2 = nn.LayerNorm(WIDTH, eps=1e-6)
        self.mlp = Mlp()

    def forward(self, x):
        x = x + self.attn(self.norm1(x))
        return x + self.mlp(self.norm2(x))


class OpenMicPaSST(nn.Module):
    def __init__(self):
        super().__init__()
        self.patch_embed = nn.Module()
        self.patch_embed.proj = nn.Conv2d(1, WIDTH, 16, stride=10)
        self.cls_token = nn.Parameter(torch.empty(1, 1, WIDTH))
        self.dist_token = nn.Parameter(torch.empty(1, 1, WIDTH))
        self.new_pos_embed = nn.Parameter(torch.empty(1, 2, WIDTH))
        self.freq_new_pos_embed = nn.Parameter(torch.empty(1, WIDTH, 12, 1))
        self.time_new_pos_embed = nn.Parameter(torch.empty(1, WIDTH, 1, 99))
        self.blocks = nn.Sequential(*(Block() for _ in range(12)))
        self.norm = nn.LayerNorm(WIDTH, eps=1e-6)
        self.head = nn.Sequential(nn.LayerNorm(WIDTH), nn.Linear(WIDTH, len(CLASSES)))
        self.head_dist = nn.Linear(WIDTH, len(CLASSES))  # Present in the published checkpoint.

    def forward(self, mel):
        patches = self.patch_embed.proj(mel[:, None])[..., :99]
        patches = patches + self.freq_new_pos_embed + self.time_new_pos_embed[..., :patches.shape[-1]]
        special = torch.cat((self.cls_token, self.dist_token), dim=1) + self.new_pos_embed
        tokens = torch.cat((special.expand(len(mel), -1, -1), patches.flatten(2).transpose(1, 2)), dim=1)
        return self.head(self.norm(self.blocks(tokens))[:, :2].mean(dim=1))


def segment_starts(frames: int, limit: int) -> list[int]:
    end = max(0, frames - 10 * RATE)
    count = min(limit, 1 + (end + 5 * RATE - 1) // (5 * RATE))
    return np.linspace(0, end, count, dtype=np.int64).tolist()


def predict(audio, cache, progress, check_cancel, *, limit: int, device_name: str = "auto"):
    device = torch.device("cuda" if torch.cuda.is_available() and device_name != "cpu" else "cpu")
    if device.type == "cpu":
        torch.set_num_threads(min(torch.get_num_threads(), 4))
    label = "CUDA" if device.type == "cuda" else "CPU"
    model_name = "PaSST / OpenMIC"
    progress(Progress("load", "正在加载乐器识别模型…", model=model_name, device=label))
    with cache.open_checkpoint() as file:
        state = torch.load(file, map_location="cpu", weights_only=True)
    with torch.device("meta"):
        model = OpenMicPaSST()
    model.load_state_dict(state, strict=True, assign=True)
    del state
    model.eval().to(device)
    mel, _ = get_mel_banks(128, 1024, RATE, 0., 15000., 100., -500., 1.)
    mel = F.pad(mel, (0, 1)).to(device)
    window = torch.hann_window(800, periodic=False, device=device)
    starts = segment_starts(len(audio), limit)
    result = []
    with torch.inference_mode():
        for index, start in enumerate(starts):
            check_cancel()
            clip = audio[start:start + 10 * RATE]
            if has_signal(clip):
                samples = torch.from_numpy(clip).to(device)
                samples = F.pad(samples, (0, 10 * RATE - len(clip)))
                spectrum = torch.stft(samples[1:] - .97 * samples[:-1], 1024, 320, 800, window,
                                      return_complex=True).abs().square()
                features = ((mel @ spectrum + 1e-5).log() + 4.5) / 5.
                with torch.autocast(device.type, dtype=torch.bfloat16,
                                    enabled=device.type == "cuda" and torch.cuda.is_bf16_supported()):
                    logits = model(features.unsqueeze(0))
                result.append((start / RATE, (start + len(clip)) / RATE,
                               logits.float().sigmoid().cpu().numpy()[0]))
            progress(Progress("analysis", "正在识别多个音频片段…", index + 1, len(starts),
                              model=model_name, device=label))
    return result
