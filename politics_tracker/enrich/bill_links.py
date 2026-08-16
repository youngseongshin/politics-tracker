"""발언과 의안의 결정적 연결 및 LLM 검수 후보 생성."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from ..models import Bill, Utterance, UtteranceBillLink, bill_link_id_for


DEFAULT_MODEL = "claude-opus-5"
DEFAULT_PROMPT_VERSION = "bill_link_v1"
DEFAULT_BATCH_SIZE = 10
_GENERIC_TITLE_TOKENS = {
    "개정법률안",
    "일부개정법률안",
    "전부개정법률안",
    "법률안",
    "특별법",
    "개정안",
    "설치",
    "관한",
    "등에",
}


def extract_bill_links_rules(
    utterances: list[Utterance], bills: list[Bill]
) -> list[UtteranceBillLink]:
    links = []
    for utterance in utterances:
        for bill in bills:
            if bill.title not in utterance.text and bill.assembly_bill_no not in utterance.text:
                continue
            method = "rule:title_match"
            links.append(
                UtteranceBillLink(
                    link_id=bill_link_id_for(
                        utterance.utterance_id, bill.bill_id, method
                    ),
                    utterance_id=utterance.utterance_id,
                    bill_id=bill.bill_id,
                    method=method,
                    confidence=1.0,
                    extractor={
                        "backend": "rules",
                        "model": "deterministic",
                        "prompt_version": "bill_link_rules_v1",
                    },
                )
            )
    return links


def _title_tokens(title: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[0-9A-Za-z가-힣]+", title)
        if len(token) >= 3 and token not in _GENERIC_TITLE_TOKENS
    }


def lexical_candidate_pairs(
    utterances: list[Utterance],
    bills: list[Bill],
    *,
    limit: int = 500,
) -> list[tuple[Utterance, Bill]]:
    pairs = []
    for utterance in utterances:
        for bill in bills:
            if bill.title in utterance.text or bill.assembly_bill_no in utterance.text:
                continue
            if not any(token in utterance.text for token in _title_tokens(bill.title)):
                continue
            pairs.append((utterance, bill))
            if len(pairs) >= limit:
                return pairs
    return pairs


_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "links": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "utterance_id": {"type": "string"},
                    "bill_id": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["utterance_id", "bill_id", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["links"],
    "additionalProperties": False,
}


def _prompt(pairs: list[tuple[Utterance, Bill]]) -> tuple[str, str]:
    system = """국회 발언이 특정 의안을 직접 지칭하는지 판정한다.

규칙:
- 의안명이 축약되었더라도 같은 의안을 분명히 뜻할 때만 연결한다.
- 정책 주제만 비슷하거나 다른 개정안을 말하면 결과에서 생략한다.
- 배경지식으로 의안을 추정하지 않고 입력된 발언과 의안명만 사용한다.
- 결과는 후보이며 사람이 원문을 다시 검수한다."""
    items = "\n\n".join(
        f"[{utterance.utterance_id} | {bill.bill_id}]\n"
        f"의안: {bill.title} ({bill.assembly_bill_no})\n발언: {utterance.text}"
        for utterance, bill in pairs
    )
    return system, f"다음 발언·의안 조합을 판정하라:\n\n{items}"


def extract_bill_links_claude(
    utterances: list[Utterance],
    bills: list[Bill],
    *,
    model: str = DEFAULT_MODEL,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    batch_size: int = DEFAULT_BATCH_SIZE,
    candidate_limit: int = 500,
    client: Any = None,
) -> tuple[list[UtteranceBillLink], dict[str, int]]:
    if client is None:
        import anthropic

        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY 환경변수가 필요합니다.")
        client = anthropic.Anthropic()

    pairs = lexical_candidate_pairs(utterances, bills, limit=candidate_limit)
    allowed = {
        (utterance.utterance_id, bill.bill_id): (utterance, bill)
        for utterance, bill in pairs
    }
    links = []
    seen = set()
    stats = {"candidates": len(pairs), "proposed": 0, "held_refusal": 0}
    for start in range(0, len(pairs), batch_size):
        batch = pairs[start : start + batch_size]
        system, user = _prompt(batch)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": 3000,
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
        batch_keys = {
            (utterance.utterance_id, bill.bill_id) for utterance, bill in batch
        }
        for result in json.loads(text)["links"]:
            key = (result["utterance_id"], result["bill_id"])
            if key not in allowed or key not in batch_keys or key in seen:
                continue
            seen.add(key)
            confidence = float(result["confidence"])
            if not 0 <= confidence <= 1:
                continue
            method = "llm:candidate"
            links.append(
                UtteranceBillLink(
                    link_id=bill_link_id_for(key[0], key[1], method),
                    utterance_id=key[0],
                    bill_id=key[1],
                    method=method,
                    confidence=confidence,
                    extractor={
                        "backend": "claude",
                        "model": response_model,
                        "prompt_version": prompt_version,
                    },
                )
            )
            stats["proposed"] += 1
    return links, stats
