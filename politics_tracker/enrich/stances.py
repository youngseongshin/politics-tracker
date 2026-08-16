"""정책 입장 축 로딩과 입장 추출.

축 정의는 공개 설정 ``config/stance_axes.yaml``이 정본이다. 이 모듈은 설정을
검증하고 이후 추출기가 같은 방향 정의를 쓰도록 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .topics import TOPICS


DEFAULT_AXES_PATH = Path("config/stance_axes.yaml")


@dataclass(frozen=True)
class StanceAxis:
    key: str
    label: str
    negative_pole: str
    positive_pole: str
    topic_keys: tuple[str, ...]
    notes: str


def load_stance_axes(path: str | Path = DEFAULT_AXES_PATH) -> list[StanceAxis]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("stance axis config must be a non-empty list")

    axes: list[StanceAxis] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("each stance axis must be an object")
        key = str(item.get("key", "")).strip()
        if not key or key in seen:
            raise ValueError(f"stance axis key is missing or duplicated: {key!r}")
        topic_keys = tuple(item.get("topic_keys") or ())
        unknown_topics = [topic for topic in topic_keys if topic not in TOPICS]
        if not topic_keys or unknown_topics:
            raise ValueError(f"invalid topic_keys for {key}: {unknown_topics or topic_keys}")
        required_text = ["label", "negative_pole", "positive_pole", "notes"]
        missing = [field for field in required_text if not str(item.get(field, "")).strip()]
        if missing:
            raise ValueError(f"missing stance axis fields for {key}: {missing}")
        axes.append(
            StanceAxis(
                key=key,
                label=str(item["label"]).strip(),
                negative_pole=str(item["negative_pole"]).strip(),
                positive_pole=str(item["positive_pole"]).strip(),
                topic_keys=topic_keys,
                notes=str(item["notes"]).strip(),
            )
        )
        seen.add(key)
    return axes
