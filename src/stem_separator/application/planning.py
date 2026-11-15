from dataclasses import dataclass
from pathlib import Path

from stem_separator.application.instruments import FOUR_STEM_TARGETS, GROUPS, LABELS
from stem_separator.application.types import MODEL_LABELS, ModelChoice, Quality, SeparationError
from stem_separator.storage.model_cache import assets_for_targets


@dataclass(frozen=True)
class AutomaticPlan:
    model: ModelChoice
    quality: Quality
    targets: tuple[str, ...]
    recognized: tuple[str, ...]
    unresolved: tuple[str, ...]
    notice: str
    attempted: tuple[tuple[ModelChoice, Quality], ...] = ()


def plan_automatic(root: Path, quality: Quality, choice: ModelChoice, candidates: tuple[str, ...], *,
                   attempted: tuple[tuple[ModelChoice, Quality], ...] = ()) -> AutomaticPlan:
    """Choose a useful installed route even if the classifier has no reliable labels."""
    standard: tuple[str, ...] = ()  # Request all four broad stems; validate each after extraction.
    routes: list[tuple[ModelChoice, Quality, tuple[str, ...]]]
    if choice == "auto":
        routes = [("auto", quality, standard)]
        if quality == "high":
            routes.append(("subroformer", "balanced", standard))
        routes.append(("demucs" if quality != "fast" else "subroformer", quality, standard))
    elif choice == "detailed":
        # The explicitly selected model can also run its broad heads.
        routes = [("detailed", quality, candidates)] if candidates else []
        available = tuple(name for name in candidates if all(
            asset.path.is_file() for asset in assets_for_targets(root, (name,), quality, "detailed")))
        if available and available != candidates:
            routes.append(("detailed", quality, available))
        routes.append(("detailed", quality, tuple(FOUR_STEM_TARGETS)))
    else:
        routes = [(choice, quality, standard)]

    missing = set()
    for model, mode, targets in routes:
        if (model, mode) in attempted:
            continue
        needed = {asset.path for asset in assets_for_targets(root, targets, mode, model) if not asset.path.is_file()}
        if needed:
            missing.update(needed)
            continue
        covered = set(targets) if model == "detailed" else FOUR_STEM_TARGETS.keys()
        unresolved = tuple(name for name in candidates if name not in covered)
        broad = [(name, group) for name in unresolved for group in covered if name in GROUPS.get(group, ())]
        messages = []
        if not candidates:
            messages.append("乐器识别未得出可靠结果，已尝试分离常用声部。")
        if unresolved:
            labels = '、'.join(LABELS[name] for name in unresolved)
            messages.append(f"识别候选「{labels}」未单独提取，相关声音可能在较宽的声部或剩余声音中。")
        if broad:
            mapped = '、'.join(f"{LABELS[name]}归入{LABELS[group]}" for name, group in broad)
            messages.append(f"按较宽声部处理：{mapped}。")
        if model != choice and choice == "auto":
            messages.append(f"按已安装的本地模型使用 {MODEL_LABELS[model]}。")
        return AutomaticPlan(model, mode, targets, candidates, unresolved, ''.join(messages), attempted)
    paths = '\n'.join(map(str, sorted(missing)))
    raise SeparationError(f"缺少所选路线的本地分离模型。请运行 stem-separator setup-model --all。\n{paths}")
