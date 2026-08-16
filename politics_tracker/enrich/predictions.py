"""예측성 발언 후보 생성. 후보는 전건 사람 검수를 거쳐야 등록된다."""

from __future__ import annotations

import json
import os
import re
from datetime import date
from typing import Any

from ..models import Utterance, prediction_candidate_id_for
from .stances import normalized_quote_exists


DEFAULT_MODEL = "claude-opus-5"
DEFAULT_PROMPT_VERSION = "prediction_v1"
RULES_PROMPT_VERSION = "prediction_rules_v2"

_DATE_HINT = re.compile(
    r"(?:20\d{2}년(?:\s*\d{1,2}월(?:\s*\d{1,2}일)?)?|"
    r"올해|내년|내후년|향후\s*\d+\s*(?:년|개월)|"
    r"\d+\s*(?:년|개월)\s*(?:안|내)|\d{1,2}월까지)"
)
_PREDICTIVE = re.compile(
    r"(?:될\s*것|전망(?:합니다|된다)|예상(?:합니다|된다)|"
    r"증가할|감소할|오를\s*것|내릴\s*것|달성될|완료될|시행될)"
)
_NON_AUTHORIAL_OR_SCHEDULED = re.compile(
    r"(?:라는\s*취지|는\s*취지|라고\s*(?:지시|발표|응답|진술)|"
    r"응답한\s*비율|시행될\s*예정|시행\s*예정|만약)"
)


def _sentences(text: str) -> list[str]:
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?。])\s+|[\r\n]+", text)
        if sentence.strip()
    ]


def _future_hint_is_possible(hint: str, spoken_at: str) -> bool:
    spoken = date.fromisoformat(spoken_at)
    explicit = re.search(
        r"(?P<year>20\d{2})년(?:\s*(?P<month>\d{1,2})월(?:\s*(?P<day>\d{1,2})일)?)?",
        hint,
    )
    if explicit:
        year = int(explicit.group("year"))
        month = int(explicit.group("month") or 12)
        day = int(explicit.group("day") or 28)
        try:
            hinted = date(year, month, day)
        except ValueError:
            return False
        return hinted > spoken
    month_only = re.search(r"(\d{1,2})월까지", hint)
    if month_only:
        return int(month_only.group(1)) > spoken.month
    return True


def _candidate_payload(
    utterance: Utterance,
    *,
    claim: str,
    deadline_hint: str,
    criteria_draft: str,
    rationale_quote: str,
    confidence: float,
    extractor: dict[str, str],
) -> dict:
    return {
        "candidate_id": prediction_candidate_id_for(
            utterance.utterance_id, claim, extractor["prompt_version"]
        ),
        "utterance_id": utterance.utterance_id,
        "person_id": utterance.person_id,
        "claim": claim.strip(),
        "deadline_hint": deadline_hint.strip(),
        "criteria_draft": criteria_draft.strip(),
        "rationale_quote": rationale_quote.strip(),
        "verifiable": True,
        "confidence": confidence,
        "extractor": extractor,
    }


def propose_predictions_rules(
    utterances: list[Utterance], *, candidate_limit: int = 500
) -> tuple[list[dict], dict[str, int]]:
    candidates = []
    seen = set()
    eligible = [utterance for utterance in utterances if utterance.person_id]
    for utterance in eligible:
        for sentence in _sentences(utterance.text):
            deadline_match = _DATE_HINT.search(sentence)
            if (
                not deadline_match
                or not _PREDICTIVE.search(sentence)
                or _NON_AUTHORIAL_OR_SCHEDULED.search(sentence)
                or not _future_hint_is_possible(
                    deadline_match.group(0), utterance.spoken_at
                )
            ):
                continue
            payload = _candidate_payload(
                utterance,
                claim=sentence,
                deadline_hint=deadline_match.group(0),
                criteria_draft=(
                    "마감일까지 공개된 공식 통계 또는 공식 시행 자료로 주장 결과를 "
                    "확인한다."
                ),
                rationale_quote=sentence,
                confidence=0.85,
                extractor={
                    "backend": "rules",
                    "model": "deterministic",
                    "prompt_version": RULES_PROMPT_VERSION,
                },
            )
            if payload["candidate_id"] in seen:
                continue
            seen.add(payload["candidate_id"])
            candidates.append(payload)
            if len(candidates) >= candidate_limit:
                return candidates, {"scanned": len(eligible), "proposed": len(candidates)}
    return candidates, {"scanned": len(eligible), "proposed": len(candidates)}


_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "utterance_id": {"type": "string"},
                    "claim": {"type": "string"},
                    "deadline_hint": {"type": "string"},
                    "criteria_draft": {"type": "string"},
                    "rationale_quote": {"type": "string"},
                    "verifiable": {"type": "boolean"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": [
                    "utterance_id",
                    "claim",
                    "deadline_hint",
                    "criteria_draft",
                    "rationale_quote",
                    "verifiable",
                    "confidence",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def _prompt(batch: list[Utterance]) -> tuple[str, str]:
    system = """국회 발언에서 사후에 참·거짓을 판정할 수 있는 예측 후보만 찾는다.

규칙:
- 미래 시한과 그 시한에 확인 가능한 결과가 모두 있는 주장만 제안한다.
- 가치 판단, 희망, 공약, 일반적 전망처럼 판정 기준을 고정할 수 없는 문장은 생략한다.
- claim과 rationale_quote는 입력 발언에 실제로 있는 문장 또는 구절을 사용한다.
- deadline_hint는 발언에 나온 시한 단서를 그대로 적는다.
- criteria_draft는 외부 자료로 재현 가능한 판정 방법을 짧게 제안한다.
- 후보는 사람이 원문을 검수하고 claim, 마감일, 판정 기준을 확정하기 전에는 등록되지 않는다."""
    items = "\n\n".join(
        f"[{utterance.utterance_id}]\n{utterance.text}" for utterance in batch
    )
    return system, f"다음 발언에서 예측 후보를 제안하라:\n\n{items}"


def propose_predictions_claude(
    utterances: list[Utterance],
    *,
    model: str = DEFAULT_MODEL,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    batch_size: int = 20,
    confidence_threshold: float = 0.7,
    candidate_limit: int = 500,
    client: Any = None,
) -> tuple[list[dict], dict[str, int]]:
    if client is None:
        import anthropic

        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY 환경변수가 필요합니다.")
        client = anthropic.Anthropic()

    inputs = [
        utterance
        for utterance in utterances
        if utterance.person_id
        and (_DATE_HINT.search(utterance.text) or _PREDICTIVE.search(utterance.text))
    ][:candidate_limit]
    by_id = {utterance.utterance_id: utterance for utterance in inputs}
    candidates = []
    seen = set()
    stats = {
        "scanned": len(inputs),
        "proposed": 0,
        "held_low_confidence": 0,
        "held_invalid_quote": 0,
        "held_refusal": 0,
    }
    for start in range(0, len(inputs), batch_size):
        batch = inputs[start : start + batch_size]
        system, user = _prompt(batch)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": 4000,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {
                "format": {"type": "json_schema", "schema": _OUTPUT_SCHEMA}
            },
        }
        if model.startswith(("claude-opus-5", "claude-fable-5")):
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        response = client.beta.messages.create(**kwargs)
        if response.stop_reason == "refusal":
            stats["held_refusal"] += len(batch)
            continue
        response_model = getattr(response, "model", model)
        text = next(block.text for block in response.content if block.type == "text")
        batch_ids = {utterance.utterance_id for utterance in batch}
        for result in json.loads(text)["results"]:
            utterance = by_id.get(result["utterance_id"])
            if not utterance or utterance.utterance_id not in batch_ids:
                continue
            confidence = float(result["confidence"])
            if not result["verifiable"] or confidence < confidence_threshold:
                stats["held_low_confidence"] += 1
                continue
            quote = str(result["rationale_quote"])
            if not normalized_quote_exists(utterance.text, quote):
                stats["held_invalid_quote"] += 1
                continue
            payload = _candidate_payload(
                utterance,
                claim=str(result["claim"]),
                deadline_hint=str(result["deadline_hint"]),
                criteria_draft=str(result["criteria_draft"]),
                rationale_quote=quote,
                confidence=confidence,
                extractor={
                    "backend": "claude",
                    "model": response_model,
                    "prompt_version": prompt_version,
                },
            )
            if not all(
                payload[key] for key in ("claim", "deadline_hint", "criteria_draft")
            ) or payload["candidate_id"] in seen:
                continue
            seen.add(payload["candidate_id"])
            candidates.append(payload)
            stats["proposed"] += 1
    return candidates, stats
