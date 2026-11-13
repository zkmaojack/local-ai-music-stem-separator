from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import file_digest
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO
from urllib.request import urlopen

from stem_separator.application.instruments import EFFECT_PROMPTS, HEAD_IDS
from stem_separator.application.types import SeparationError

CHECKPOINT_NAME = "955717e8-8726e21a.th"
CHECKPOINT_SHA256 = "8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4"
CHECKPOINT_URL = f"https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/{CHECKPOINT_NAME}"
MEGA_RELEASE = "https://github.com/ZFTurbo/Music-Source-Separation-Training/releases/download/v1.0.21"


@dataclass(frozen=True)
class ModelAsset:
    name: str
    url: str
    sha256: str


MEGA_WEIGHTS = ModelAsset(
    "mvsep_mega_model_bs_roformer_53_stems_v1.ckpt", MEGA_RELEASE + "/mvsep_mega_model_bs_roformer_53_stems_v1.ckpt",
    "c62820893bbf86d4e734f966bd142d9157cfc8bb8e79e9d8f9ea553f3ff3519f",
)
MEGA_CONFIG = ModelAsset(
    "mvsep_mega_model_bs_roformer_53_stems.yaml", MEGA_RELEASE + "/mvsep_mega_model_bs_roformer_53_stems.yaml",
    "7e198062a251587088adb91215a4f44ab59e67bd62fcc805cf54d6e7dfc51103",
)
AUDIOSEP_WEIGHTS = ModelAsset(
    "pytorch_model.bin", "https://huggingface.co/nielsr/audiosep-demo/resolve/66ffec9/pytorch_model.bin?download=true",
    "37f1691fb067e2575f1ad1cfbfe44b7b3da18e52f33fcb2b0937b72952f11ba1",
)
ROBERTA_SOURCE = "https://huggingface.co/FacebookAI/roberta-base/resolve/e2da8e2f811d1448a5b465c236feacd80ffbac7b"
AUDIOSEP_VOCAB = ModelAsset("vocab.json", ROBERTA_SOURCE + "/vocab.json",
                            "9e7f63c2d15d666b52e21d250d2e513b87c9b713cfa6987a82ed89e5e6e50655")
AUDIOSEP_MERGES = ModelAsset("merges.txt", ROBERTA_SOURCE + "/merges.txt",
                             "1ce1664773c50f3e0cc8842619a93edc4624525b728b188a9e0be33b7726adc5")


class ModelCache:
    def __init__(self, root: Path, asset: ModelAsset | None = None) -> None:
        self.asset = asset or ModelAsset(CHECKPOINT_NAME, CHECKPOINT_URL, CHECKPOINT_SHA256)
        self.path = root.resolve() / self.asset.name

    @contextmanager
    def open_checkpoint(self) -> Iterator[BinaryIO]:
        """Verify and open the official checkpoint on the same file handle."""
        try:
            checkpoint = self.path.open("rb")
        except FileNotFoundError as error:
            raise SeparationError(
                f"Model checkpoint is missing: {self.path}. Run `stem-separator setup-model`."
            ) from error
        with checkpoint:
            self._verify(checkpoint)
            yield checkpoint

    def download(self, progress: Callable[[int, int | None], None] | None = None) -> Path:
        if self.path.exists():
            try:
                with self.open_checkpoint():
                    return self.path
            except SeparationError:
                pass  # Replace a corrupt cache only after verifying the new download.

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(dir=self.path.parent, suffix=".part", delete=False) as file:
                temporary_path = Path(file.name)
                with urlopen(self.asset.url, timeout=60) as response:
                    total = response.length
                    completed = 0
                    while data := response.read(1024 * 1024):
                        file.write(data)
                        completed += len(data)
                        if progress is not None:
                            progress(completed, total)
            with temporary_path.open("rb") as file:
                self._verify(file)
            temporary_path.replace(self.path)
        except OSError as error:
            raise SeparationError(f"Could not download {self.asset.name}: {error}") from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return self.path

    def _verify(self, checkpoint: BinaryIO) -> None:
        digest = file_digest(checkpoint, "sha256").hexdigest()
        if digest != self.asset.sha256:
            raise SeparationError(
                f"Model checkpoint SHA-256 mismatch: {self.path.name}. Run `stem-separator setup-model` "
                "to replace the damaged checkpoint."
            )
        checkpoint.seek(0)


def model_assets(root: Path, expanded: bool) -> tuple[ModelCache, ...]:
    if expanded:
        return tuple(ModelCache(root / "mega53", asset) for asset in (MEGA_CONFIG, MEGA_WEIGHTS))
    return (ModelCache(root),)


def effect_assets(root: Path) -> tuple[ModelCache, ...]:
    return tuple(ModelCache(root / "audiosep", asset) for asset in (AUDIOSEP_WEIGHTS, AUDIOSEP_VOCAB, AUDIOSEP_MERGES))


def assets_for_targets(root: Path, targets: tuple[str, ...]) -> tuple[ModelCache, ...]:
    if not targets:
        return model_assets(root, False)
    instruments = model_assets(root, True) if any(name in HEAD_IDS for name in targets) else ()
    effects = effect_assets(root) if any(name in EFFECT_PROMPTS for name in targets) else ()
    return instruments + effects
