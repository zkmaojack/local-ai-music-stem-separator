from dataclasses import dataclass

from stem_separator.application.types import ModelChoice, SeparationError

STANDARD = ("vocals", "drums", "bass", "other")
REMAINDER = "remainder"
FOUR_STEM_TARGETS = {"vocal": "vocals", "drums": "drums", "bass": "bass"}


def uses_four_stems(targets: tuple[str, ...]) -> bool:
    return FOUR_STEM_TARGETS.keys() >= set(targets)


@dataclass(frozen=True)
class Instrument:
    id: str
    label: str
    category: str


# Order is the order of the output heads in the v1.0.21 checkpoint.
INSTRUMENTS = tuple(Instrument(*row.split("|")) for row in """
accordion|手风琴|古典
acoustic-guitar|木吉他|常用
back-vocal|和声|常用
banjo|班卓琴|常用
bass|贝斯|常用
bassoon|巴松管|古典
bells|钟铃|古典
bowed_strings|弓弦乐组|古典
brass|铜管组|古典
cello|大提琴|古典
clarinet|单簧管|古典
congas|康加鼓|常用
digital-piano|电钢琴|电子
dobro|共鸣吉他|常用
double-bass|低音提琴|古典
drums|鼓|常用
electric-guitar|电吉他|常用
flute|长笛|古典
french-horn|圆号|古典
glockenspiel|钟琴|古典
guitar|吉他|常用
harmonica|口琴|常用
harp|竖琴|古典
harpsichord|羽管键琴|古典
hh|踩镲|电子
keys|键盘组|电子
kick|底鼓|电子
lead-vocal|主唱|常用
mandolin|曼陀林|常用
marimba|马林巴|古典
oboe|双簧管|古典
organ|风琴|古典
percussion|打击乐组|常用
piano|钢琴|常用
saxophone|萨克斯|古典
sitar|西塔琴|常用
snare|军鼓|电子
strings|弦乐组|古典
synth|合成器|电子
tambourine|铃鼓|常用
timpani|定音鼓|古典
toms|通鼓|电子
triangle|三角铁|古典
trombone|长号|古典
trumpet|小号|古典
tuba|大号|古典
ukulele|尤克里里|常用
viola|中提琴|古典
violin|小提琴|古典
vocal|人声|常用
wind|管乐组|古典
wind-chimes|风铃|古典
woodwind|木管组|古典
""".strip().splitlines())

HEAD_IDS = {item.id: index for index, item in enumerate(INSTRUMENTS)}
EFFECT_PROMPTS = {
    "riser": "an electronic rising sweep sound effect",
    "impact": "a cinematic electronic impact sound effect",
    "whoosh": "a synthetic whoosh sound effect",
}
INSTRUMENTS += (
    Instrument("riser", "升调音效", "电子"),
    Instrument("impact", "冲击音效", "电子"),
    Instrument("whoosh", "呼啸音效", "电子"),
)
LABELS = {item.id: item.label for item in INSTRUMENTS} | {"vocals": "人声", "other": "其他", REMAINDER: "剩余声音"}

_BOWED = {"violin", "viola", "cello", "double-bass"}
_WOODWIND = {"flute", "oboe", "clarinet", "bassoon", "saxophone"}
_BRASS = {"french-horn", "trumpet", "trombone", "tuba"}
_DRUMS = {"kick", "snare", "hh", "toms"}
GROUPS = {
    "vocal": {"lead-vocal", "back-vocal"},
    "guitar": {"acoustic-guitar", "electric-guitar", "dobro"},
    "keys": {"piano", "digital-piano", "harpsichord", "organ", "synth"},
    "bowed_strings": _BOWED,
    "strings": _BOWED | {"bowed_strings", "harp"},
    "woodwind": _WOODWIND,
    "brass": _BRASS,
    "wind": _WOODWIND | _BRASS | {"woodwind", "brass"},
    "drums": _DRUMS,
    "percussion": _DRUMS | {"drums", "congas", "tambourine", "timpani", "triangle", "bells",
                              "glockenspiel", "marimba", "wind-chimes"},
}


def validate_selection(targets: tuple[str, ...]) -> None:
    chosen = set(targets)
    if not chosen <= HEAD_IDS.keys() | EFFECT_PROMPTS.keys() or len(chosen) != len(targets):
        raise SeparationError("乐器选择含有未知或重复的音轨。")
    for target in targets:
        overlap = GROUPS.get(target, set()) & chosen
        if overlap:
            name = LABELS[min(overlap, key=HEAD_IDS.__getitem__)]
            raise SeparationError(f"「{LABELS[target]}」包含「{name}」，请选择其中一种。")


def validate_model_selection(targets: tuple[str, ...], model: ModelChoice) -> None:
    if targets == (REMAINDER,):
        return
    validate_selection(targets)
    if model in ("demucs", "subroformer") and not uses_four_stems(targets):
        raise SeparationError("此模型支持人声、鼓和贝斯。请选择其他模型以分离细分乐器或音效。")
    if model == "detailed" and not targets:
        raise SeparationError("请选择至少一件乐器或音效，或使用自动识别。")
