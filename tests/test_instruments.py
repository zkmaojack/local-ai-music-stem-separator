from pathlib import Path

import pytest

from stem_separator.application.instruments import HEAD_IDS, validate_selection
from stem_separator.application.types import SeparationError
from stem_separator.storage.model_cache import assets_for_targets


def test_rejects_overlapping_instrument_group() -> None:
    assert len(HEAD_IDS) == 53
    validate_selection(("violin", "cello", "riser"))
    with pytest.raises(SeparationError, match="包含"):
        validate_selection(("strings", "violin"))


def test_only_required_models_are_downloaded(tmp_path: Path) -> None:
    assert len(assets_for_targets(tmp_path, ())) == 1
    assert {item.path.parent.name for item in assets_for_targets(tmp_path, ("violin",))} == {"mega53"}
    assert {item.path.parent.name for item in assets_for_targets(tmp_path, ("impact",))} == {"audiosep"}
    assert len(assets_for_targets(tmp_path, ("riser", "cello"))) == 5
