"""정책 입장 축 로딩과 입장 추출.

축 정의는 공개 설정 ``config/stance_axes.yaml``이 정본이다. 이 모듈은 설정을
검증하고 이후 추출기가 같은 방향 정의를 쓰도록 한다.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..models import Stance, Utterance, stance_id_for
from .topics import TOPICS


DEFAULT_AXES_PATH = Path("config/stance_axes.yaml")
DEFAULT_MODEL = "claude-opus-5"
DEFAULT_PROMPT_VERSION = "stance_v1"
DEFAULT_CONFIDENCE_THRESHOLD = 0.7
DEFAULT_BATCH_SIZE = 20


_RULE_PHRASES: dict[str, dict[int, tuple[str, ...]]] = {
    "housing_regulation": {
        -1: ("규제를 완화", "재건축 규제 완화", "주택 공급을 확대", "공급 확대"),
        1: ("규제를 강화", "투기를 억제", "다주택자 규제"),
    },
    "fiscal_policy": {
        -1: ("재정 건전성", "감세", "세 부담을 낮"),
        1: ("확장 재정", "재정을 확대", "증세"),
    },
    "labor_hours": {
        -1: ("근로시간 유연화", "근로시간을 유연"),
        1: ("근로시간을 단축", "주 4.5일", "주 4일"),
    },
    "nuclear_energy": {
        -1: ("탈원전", "원전을 축소"),
        1: ("원전을 확대", "원전 확대", "원전 생태계"),
    },
    "prosecution_reform": {
        -1: ("검찰 권한을 유지", "검찰 수사권을 보장"),
        1: ("검찰 권한을 축소", "검찰개혁", "수사와 기소를 분리", "검찰청 폐지"),
    },
    "north_korea": {
        -1: ("대북 제재", "압박과 억지", "강력한 억지"),
        1: ("남북 대화", "대화와 교류", "대북 교류"),
    },
}


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


def _candidate_pairs(
    utterances: list[Utterance], axes: list[StanceAxis]
) -> list[tuple[Utterance, StanceAxis]]:
    pairs = []
    for utterance in utterances:
        if not utterance.person_id or not utterance.topics:
            continue
        topic_set = set(utterance.topics)
        for axis in axes:
            if topic_set.intersection(axis.topic_keys):
                pairs.append((utterance, axis))
    return pairs


def _first_phrase(text: str, phrases: tuple[str, ...]) -> str | None:
    found = [(text.find(phrase), phrase) for phrase in phrases if phrase in text]
    if not found:
        return None
    return min(found, key=lambda item: item[0])[1]


def normalized_quote_exists(text: str, quote: str) -> bool:
    normalized_text = " ".join(text.split())
    normalized_quote = " ".join(quote.split())
    return bool(normalized_quote) and normalized_quote in normalized_text


def extract_stances_rules(
    utterances: list[Utterance], axes: list[StanceAxis]
) -> tuple[list[Stance], dict[str, int]]:
    """명시적 방향 문구가 한쪽에만 있을 때만 입장을 만든다."""
    stances: list[Stance] = []
    stats = {"candidates": 0, "extracted": 0, "held": 0}
    for utterance, axis in _candidate_pairs(utterances, axes):
        stats["candidates"] += 1
        phrases = _RULE_PHRASES.get(axis.key, {})
        negative = _first_phrase(utterance.text, phrases.get(-1, ()))
        positive = _first_phrase(utterance.text, phrases.get(1, ()))
        if not negative and not positive:
            continue
        held_reason = "conflicting_rule_phrases" if negative and positive else None
        direction = 0 if held_reason else (-1 if negative else 1)
        quote = negative or positive or ""
        stance = Stance(
            stance_id=stance_id_for(
                utterance.utterance_id, axis.key, "stance_rules_v1"
            ),
            utterance_id=utterance.utterance_id,
            person_id=utterance.person_id,
            axis=axis.key,
            value=float(direction) * 0.7,
            confidence=0.4 if held_reason else 0.86,
            rationale_quote=quote,
            extractor={
                "backend": "rules",
                "model": "deterministic",
                "prompt_version": "stance_rules_v1",
            },
            held_reason=held_reason,
        )
        stances.append(stance)
        stats["held" if held_reason else "extracted"] += 1
    return stances, stats


def _output_schema(axes: list[StanceAxis]) -> dict:
    return {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "utterance_id": {"type": "string"},
                        "axis": {
                            "type": "string",
                            "enum": [axis.key for axis in axes],
                        },
                        "value": {"type": "number", "minimum": -1, "maximum": 1},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "rationale_quote": {"type": "string"},
                    },
                    "required": [
                        "utterance_id",
                        "axis",
                        "value",
                        "confidence",
                        "rationale_quote",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["results"],
        "additionalProperties": False,
    }


def _prompt(
    pairs: list[tuple[Utterance, StanceAxis]], axes: list[StanceAxis]
) -> tuple[str, str]:
    definitions = "\n".join(
        f"- {axis.key}: -1={axis.negative_pole}; +1={axis.positive_pole}"
        for axis in axes
    )
    system = f"""국회 발언의 정책 입장을 지정된 축 위 -1부터 +1 사이 값으로 추출한다.

규칙:
- 입력에 지정된 발언·축 조합만 평가한다.
- 명시된 입장이 없으면 해당 조합을 결과에서 생략한다.
- rationale_quote는 판단 근거가 되는 발언 원문의 연속 구절을 그대로 복사한다.
- 배경지식, 정당, 인물에 대한 추정은 사용하지 않는다.

축 정의:
{definitions}"""
    items = "\n\n".join(
        f"[{utterance.utterance_id} | {axis.key}]\n{utterance.text}"
        for utterance, axis in pairs
    )
    return system, f"다음 발언·축 조합을 평가하라:\n\n{items}"


def _held_stance(
    utterance: Utterance,
    axis: StanceAxis,
    *,
    model: str,
    prompt_version: str,
    reason: str,
    value: float = 0,
    confidence: float = 0,
    quote: str = "",
) -> Stance:
    return Stance(
        stance_id=stance_id_for(utterance.utterance_id, axis.key, prompt_version),
        utterance_id=utterance.utterance_id,
        person_id=utterance.person_id or "",
        axis=axis.key,
        value=value,
        confidence=confidence,
        rationale_quote=quote,
        extractor={
            "backend": "claude",
            "model": model,
            "prompt_version": prompt_version,
        },
        held_reason=reason,
    )


def extract_stances_claude(
    utterances: list[Utterance],
    axes: list[StanceAxis],
    *,
    model: str = DEFAULT_MODEL,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    batch_size: int = DEFAULT_BATCH_SIZE,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    client: Any = None,
) -> tuple[list[Stance], dict[str, int]]:
    if client is None:
        import anthropic

        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY 환경변수가 필요합니다.")
        client = anthropic.Anthropic()

    pairs = _candidate_pairs(utterances, axes)
    allowed = {(utterance.utterance_id, axis.key): (utterance, axis) for utterance, axis in pairs}
    stances: list[Stance] = []
    stats = {
        "candidates": len(pairs),
        "extracted": 0,
        "held_low_confidence": 0,
        "held_invalid_quote": 0,
        "held_invalid_value": 0,
        "held_invalid_confidence": 0,
        "held_refusal": 0,
    }
    seen_results: set[tuple[str, str]] = set()
    for start in range(0, len(pairs), batch_size):
        batch = pairs[start : start + batch_size]
        system, user = _prompt(batch, axes)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": 4000,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {
                "format": {"type": "json_schema", "schema": _output_schema(axes)}
            },
        }
        if model.startswith(("claude-opus-5", "claude-fable-5")):
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        response = client.beta.messages.create(**kwargs)
        response_model = getattr(response, "model", model)
        if response.stop_reason == "refusal":
            for utterance, axis in batch:
                stances.append(
                    _held_stance(
                        utterance,
                        axis,
                        model=response_model,
                        prompt_version=prompt_version,
                        reason="refusal",
                    )
                )
            stats["held_refusal"] += len(batch)
            continue

        text = next(block.text for block in response.content if block.type == "text")
        for result in json.loads(text)["results"]:
            result_key = (result["utterance_id"], result["axis"])
            pair = allowed.get(result_key)
            if pair is None or pair not in batch or result_key in seen_results:
                continue
            seen_results.add(result_key)
            utterance, axis = pair
            value = float(result["value"])
            confidence = float(result["confidence"])
            quote = str(result["rationale_quote"])
            reason = None
            if not -1 <= value <= 1:
                reason = "invalid_value"
                value = 0
                stats["held_invalid_value"] += 1
            elif not 0 <= confidence <= 1:
                reason = "invalid_confidence"
                confidence = 0
                stats["held_invalid_confidence"] += 1
            elif not normalized_quote_exists(utterance.text, quote):
                reason = "invalid_quote"
                quote = ""
                stats["held_invalid_quote"] += 1
            elif confidence < confidence_threshold:
                reason = "low_confidence"
                stats["held_low_confidence"] += 1

            stance = Stance(
                stance_id=stance_id_for(utterance.utterance_id, axis.key, prompt_version),
                utterance_id=utterance.utterance_id,
                person_id=utterance.person_id or "",
                axis=axis.key,
                value=value,
                confidence=confidence,
                rationale_quote=quote,
                extractor={
                    "backend": "claude",
                    "model": response_model,
                    "prompt_version": prompt_version,
                },
                held_reason=reason,
            )
            stances.append(stance)
            if reason is None:
                stats["extracted"] += 1
    return stances, stats


def stance_change_id_for(before_stance_id: str, after_stance_id: str) -> str:
    canonical = f"{before_stance_id}\0{after_stance_id}".encode("utf-8")
    return "stchg_" + hashlib.sha256(canonical).hexdigest()[:16]


def detect_stance_changes(
    stances: list[Stance],
    utterances: list[Utterance],
    *,
    threshold: float = 0.8,
) -> list[dict[str, Any]]:
    """동일 인물·축의 시간순 인접 공개 입장 사이 큰 변화 후보를 만든다."""
    if threshold <= 0 or threshold > 2:
        raise ValueError("stance change threshold must be between 0 and 2")
    utterance_by_id = {utterance.utterance_id: utterance for utterance in utterances}

    # 같은 발언·축을 여러 추출기 버전이 평가했다면 사람 확정, 신뢰도, ID 순으로
    # 하나만 선택해 추출기 교체를 입장 변화로 오인하지 않는다.
    best: dict[tuple[str, str, str], Stance] = {}
    for stance in stances:
        if stance.held_reason or stance.utterance_id not in utterance_by_id:
            continue
        key = (stance.person_id, stance.axis, stance.utterance_id)
        current = best.get(key)
        rank = (int(stance.human_reviewed), stance.confidence, stance.stance_id)
        if current is None or rank > (
            int(current.human_reviewed),
            current.confidence,
            current.stance_id,
        ):
            best[key] = stance

    grouped: dict[tuple[str, str], list[Stance]] = defaultdict(list)
    for stance in best.values():
        grouped[(stance.person_id, stance.axis)].append(stance)

    changes: list[dict[str, Any]] = []
    for (person_id, axis), items in sorted(grouped.items()):
        items.sort(
            key=lambda stance: (
                utterance_by_id[stance.utterance_id].spoken_at,
                utterance_by_id[stance.utterance_id].source.get("url", ""),
                utterance_by_id[stance.utterance_id].order,
                stance.stance_id,
            )
        )
        for before, after in zip(items, items[1:]):
            delta = after.value - before.value
            if abs(delta) < threshold:
                continue
            before_utterance = utterance_by_id[before.utterance_id]
            after_utterance = utterance_by_id[after.utterance_id]
            changes.append(
                {
                    "change_id": stance_change_id_for(before.stance_id, after.stance_id),
                    "person_id": person_id,
                    "axis": axis,
                    "before_stance_id": before.stance_id,
                    "after_stance_id": after.stance_id,
                    "before_utterance_id": before.utterance_id,
                    "after_utterance_id": after.utterance_id,
                    "before_spoken_at": before_utterance.spoken_at,
                    "after_spoken_at": after_utterance.spoken_at,
                    "before_value": before.value,
                    "after_value": after.value,
                    "delta": round(delta, 6),
                    "context_note": None,
                }
            )
    return changes
