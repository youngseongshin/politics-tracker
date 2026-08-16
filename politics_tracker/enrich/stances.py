"""정책 입장 축 로딩과 입장 추출.

축 정의는 공개 설정 ``config/stance_axes.yaml``이 정본이다. 이 모듈은 설정을
검증하고 이후 추출기가 같은 방향 정의를 쓰도록 한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
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


RULES_PROMPT_VERSION = "stance_rules_v2"


# 단어가 등장했다는 사실만으로 입장을 만들지 않는다. 아래 패턴은 주체가 정책을
# 추진·유지·폐기해야 한다고 명시한 문형만 포함한다. 인용이나 반대 논거에서 자주
# 등장하는 "검찰개혁", "탈원전", "증세" 같은 단독 명사는 의도적으로 제외한다.
_RULE_PATTERNS: dict[str, dict[int, tuple[str, ...]]] = {
    "housing_regulation": {
        -1: (
            r"재건축\s*규제(?:를)?\s*완화",
            r"주택\s*공급(?:을)?\s*(?:대폭\s*)?(?:확대|늘려|늘리)",
            r"공급\s*확대(?:가|를)?\s*(?:필요|우선|해야|하겠|추진)",
            r"공급대책(?:도|이)\s*있어야",
            r"규제(?:를)?\s*완화(?:해야|하겠|할\s*필요)",
        ),
        1: (
            r"투기(?:를)?\s*억제(?:해야|하겠|할\s*필요)",
            r"규제(?:를)?\s*강화(?:해야|하겠|할\s*필요)",
            r"다주택자\s*규제(?:를)?\s*(?:강화|유지)",
        ),
    },
    "fiscal_policy": {
        -1: (
            r"재정\s*건전성(?:을)?\s*(?:지켜|지키|확보|우선)",
            r"감세(?:를)?\s*(?:추진|확대|해야|하겠)",
            r"세\s*부담(?:을)?\s*(?:낮추|줄이|완화)",
        ),
        1: (
            r"확장\s*재정(?:을|으로)?\s*(?:추진|전환|해야|하겠|필요)",
            r"재정(?:을)?\s*(?:확대|확장)(?:해야|하겠|할\s*필요)",
            r"증세(?:가)?\s*(?:필요|불가피|해야)",
            r"세율(?:을)?\s*(?:인상|올려|올리)",
        ),
    },
    "labor_hours": {
        -1: (
            r"근로시간(?:을)?\s*(?:유연화|유연하게)",
            r"근로시간\s*유연화(?:가)?\s*(?:필요|추진|해야)",
        ),
        1: (
            r"근로시간(?:을)?\s*(?:단축|줄여|줄이)",
            r"주\s*4(?:\.5)?일제?(?:를)?\s*(?:도입|추진|시행|해야)",
        ),
    },
    "nuclear_energy": {
        -1: (
            r"원전(?:을|의\s*비중을)?\s*(?:축소|줄여|줄이)",
            r"탈원전(?:을)?\s*(?:추진|지속|완수|해야)",
            r"재생에너지(?:로의)?\s*전환(?:을)?\s*(?:추진|가속|해야)",
            r"원전\s*건설계획을\s*강행[\s\S]{0,80}?유감",
        ),
        1: (
            r"원전(?:을|의\s*비중을)?\s*(?:확대|늘려|늘리)(?:야|겠|할\s*필요|해야)",
            r"원전\s*확대(?:가)?\s*(?:필요|추진해야|해야)",
            r"원전\s*생태계(?:를)?\s*(?:복원|회복|재건|살리)",
            r"원전\s*생태계\s*활성화에\s*정책을\s*집중하겠습니다",
            r"원전과\s*재생에너지를\s*믹스해서\s*가야",
            r"원전(?:이)?\s*적절하게\s*기저전원\s*역할을\s*해야",
            r"원전\s*없이\s*할\s*수\s*있다고\s*얘기해\s*본\s*적\s*없",
            r"신규\s*원전(?:을)?\s*포함하겠다고\s*말씀하셔야",
            r"원전\s*폐기\s*정책이\s*얼마나\s*위험한\s*정책",
            r"탈원전\s*정책(?:을)?\s*(?:폐기|철회|중단)",
            r"탈원전\s*정책으로[^.!?\n]{0,80}(?:비용|손실|붕괴|무너)",
        ),
    },
    "prosecution_reform": {
        -1: (
            r"검찰\s*권한(?:을|은)?\s*(?:유지|보강)",
            r"검찰\s*수사권(?:을|은)?\s*(?:보장|유지)",
            r"보완수사권(?:의)?\s*(?:존치|유지)(?:가|는|를)?\s*(?:필요|해야)",
            r"보완수사권마저\s*없어진다면",
            r"모든\s*권한을\s*박탈하는\s*것은\s*잘못",
            r"수사와\s*기소를\s*분리하는\s*것이\s*절대\s*진리",
            r"왜[^.!?\n]{0,80}보완수사권을\s*완전히\s*폐지해야",
            r"왜[^.!?\n]{0,80}검찰청(?:\s*자체)?를\s*폐지해야",
            r"검찰청(?:을|\s*자체를)?\s*폐지[^.!?\n]{0,100}분노하지\s*않을\s*수\s*없",
        ),
        1: (
            r"검찰\s*권한(?:을)?\s*(?:축소|분산)(?:해야|하겠|할\s*필요)",
            r"수사와\s*기소를\s*분리하고[^.!?\n]{0,240}(?:권한[^.!?\n]{0,40}새롭게\s*정립|인권\s*보장을\s*강화)",
            r"검찰개혁\s*완수",
            r"검찰개혁의\s*완성을\s*목전[^.!?\n]{0,120}찬성토론",
            r"검찰청(?:을)?\s*폐지(?:해야|하겠|에\s*찬성)",
        ),
    },
    "north_korea": {
        -1: (
            r"대북\s*제재(?:를)?\s*(?:강화|유지|해야)",
            r"대북\s*(?:압박|억지)(?:를)?\s*(?:강화|우선|해야)",
            r"강력한\s*억지(?:가|를)?\s*(?:필요|구축|유지)",
        ),
        1: (
            r"남북\s*대화(?:를)?\s*(?:재개|확대|추진|해야)",
            r"(?:대화|교류)(?:와|·)\s*(?:교류|협력)(?:을)?\s*(?:확대|추진|강화|해야)",
            r"대북\s*교류(?:를)?\s*(?:확대|추진|재개|해야)",
            r"북한과\s*교류\s*협력하면서",
            r"북한과의\s*교류·협력이[^.!?\n]{0,180}확신",
            r"북한과\s*문화재\s*교류\s*사업들[^.!?\n]{0,120}적극적으로",
            r"남북\s*대화를\s*견인",
        ),
    },
}


@dataclass(frozen=True)
class StanceAxis:
    key: str
    label: str
    negative_pole: str
    positive_pole: str
    topic_keys: tuple[str, ...]
    bill_direction: dict[str, str]
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
        bill_direction = item.get("bill_direction")
        if (
            not isinstance(bill_direction, dict)
            or set(bill_direction) != {"positive", "negative"}
            or any(
                decision not in {"찬성", "반대"}
                for decision in bill_direction.values()
            )
        ):
            raise ValueError(f"invalid bill_direction for {key}")
        axes.append(
            StanceAxis(
                key=key,
                label=str(item["label"]).strip(),
                negative_pole=str(item["negative_pole"]).strip(),
                positive_pole=str(item["positive_pole"]).strip(),
                topic_keys=topic_keys,
                bill_direction=dict(bill_direction),
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


def _first_match(text: str, patterns: tuple[str, ...]) -> str | None:
    found = []
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            found.append((match.start(), match.group(0)))
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
        patterns = _RULE_PATTERNS.get(axis.key, {})
        negative = _first_match(utterance.text, patterns.get(-1, ()))
        positive = _first_match(utterance.text, patterns.get(1, ()))
        if not negative and not positive:
            continue
        held_reason = "conflicting_rule_phrases" if negative and positive else None
        direction = 0 if held_reason else (-1 if negative else 1)
        quote = negative or positive or ""
        stance = Stance(
            stance_id=stance_id_for(
                utterance.utterance_id, axis.key, RULES_PROMPT_VERSION
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
                "prompt_version": RULES_PROMPT_VERSION,
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


def select_best_stances(
    stances: list[Stance], utterances: list[Utterance]
) -> list[Stance]:
    """발언·축별로 사람 확정, 신뢰도, ID 순서의 최선 공개 버전을 고른다."""
    utterance_ids = {utterance.utterance_id for utterance in utterances}
    best: dict[tuple[str, str, str], Stance] = {}
    for stance in stances:
        if stance.held_reason or stance.utterance_id not in utterance_ids:
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
    return list(best.values())


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

    grouped: dict[tuple[str, str], list[Stance]] = defaultdict(list)
    for stance in select_best_stances(stances, utterances):
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
