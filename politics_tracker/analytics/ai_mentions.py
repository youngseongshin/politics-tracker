"""국회 발언의 인공지능 언급을 결정적으로 탐지하고 요약한다."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Pattern

from ..models import Person, Utterance

_BARE_AI = re.compile(
    r"(?i)(?<![A-Za-z])A\.?\s*I\.?(?![A-Za-z])"
)
_UNAMBIGUOUS_AI = re.compile(
    r"(?i)인공\s*지능|에이아이|artificial\s+intelligence|open\s*ai|오픈\s*AI|"
    r"chat\s*gpt|챗\s*(?:gpt|지피티)|(?<![A-Za-z])LLM(?![A-Za-z])|"
    r"(?:거대|대규모)\s*언어\s*모델|머신\s*러닝|기계\s*학습|딥\s*러닝|딥\s*페이크"
)
_AVIAN_INFLUENZA = re.compile(
    r"조류\s*(?:독감|인플루엔자)|고병원성|가축\s*(?:질병|전염병)|방역|살처분|"
    r"야생조류|가금|양계|산란계|육용종계|HPAI|ASF",
    re.IGNORECASE,
)
_TECH_AI_COLLOCATION = re.compile(
    r"(?i)(?<![A-Za-z])AI(?:가|를|로|와|의|에|는|도|만)?\s*"
    r"(?:모델|기술|산업|시대|혁명|전환|로봇|데이터|시스템|프로그램|서비스|활용|"
    r"도입|학습|인재|교육|반도체|컴퓨팅|플랫폼|기업|스타트업|고속도로|수석|"
    r"기본법|윤리|안전|광고|워터마크|친화|예산|강국|허브|주권|센터|정책|"
    r"인프라|생태계|에이전트|소프트웨어|의료|규제|국가|대전환|3강|대체)|"
    r"(?:피지컬|소버린|생성형|범용)\s*AI"
)

_TERM_PATTERNS: tuple[tuple[str, Pattern[str]], ...] = (
    ("AI", _BARE_AI),
    ("인공지능", re.compile(r"인공\s*지능")),
    ("에이아이", re.compile(r"에이아이")),
    (
        "Artificial Intelligence",
        re.compile(r"artificial\s+intelligence", re.IGNORECASE),
    ),
    ("OpenAI", re.compile(r"(?i)open\s*ai|오픈\s*AI")),
    ("ChatGPT", re.compile(r"(?i)chat\s*gpt|챗\s*(?:gpt|지피티)")),
    (
        "LLM",
        re.compile(
            r"(?i)(?<![A-Za-z])LLM(?![A-Za-z])|(?:거대|대규모)\s*언어\s*모델"
        ),
    ),
    (
        "머신러닝·딥러닝",
        re.compile(r"머신\s*러닝|기계\s*학습|딥\s*러닝"),
    ),
    ("딥페이크", re.compile(r"딥\s*페이크", re.IGNORECASE)),
)


def is_ai_candidate(text: str) -> bool:
    """AI 약어 또는 명시적인 인공지능 용어가 있는지 확인한다."""
    return bool(_BARE_AI.search(text) or _UNAMBIGUOUS_AI.search(text))


def is_ai_mention(text: str) -> bool:
    """인공지능 언급만 남기고 조류인플루엔자 AI 문맥은 제외한다."""
    if _UNAMBIGUOUS_AI.search(text):
        return True
    if not _BARE_AI.search(text):
        return False
    if _AVIAN_INFLUENZA.search(text) and not _TECH_AI_COLLOCATION.search(text):
        return False
    return True


def matched_ai_terms(text: str) -> list[str]:
    return [label for label, pattern in _TERM_PATTERNS if pattern.search(text)]


def ai_mention_contexts(
    text: str, *, before: int = 140, after: int = 280
) -> list[str]:
    """모든 AI 표현을 포함하는 겹치지 않는 주변 문맥을 반환한다."""
    spans = sorted(
        {
            (match.start(), match.end())
            for pattern in (_BARE_AI, _UNAMBIGUOUS_AI)
            for match in pattern.finditer(text)
        }
    )
    if not spans:
        return [text]

    windows: list[list[int]] = []
    for start, end in spans:
        window = [max(0, start - before), min(len(text), end + after)]
        if windows and window[0] <= windows[-1][1]:
            windows[-1][1] = max(windows[-1][1], window[1])
        else:
            windows.append(window)

    contexts = []
    for start, end in windows:
        fragment = text[start:end].strip()
        contexts.append(
            ("…" if start else "")
            + fragment
            + ("…" if end < len(text) else "")
        )
    return contexts


def build_ai_analysis(
    people: list[Person], utterances: list[Utterance]
) -> dict[str, Any]:
    """AI 언급 범위와 전건 근거 행을 만든다.

    이 함수의 규칙은 정성 분석의 모집단만 결정한다. 공개 페이지의 논점과
    해석은 규칙 기반 주제 점수나 언급량으로 만들지 않는다.
    """
    people_by_id = {person.person_id: person for person in people}
    rows = []
    excluded_avian = 0
    for utterance in utterances:
        if not is_ai_candidate(utterance.text):
            continue
        if not is_ai_mention(utterance.text):
            excluded_avian += 1
            continue
        person = people_by_id.get(utterance.person_id or "")
        rows.append(
            {
                "utterance": utterance,
                "person": person,
                "speaker_name": person.name if person else utterance.speaker_name,
                "speaker_role": utterance.speaker_role,
                "month": utterance.spoken_at[:7],
                "contexts": ai_mention_contexts(utterance.text),
            }
        )

    rows.sort(
        key=lambda row: (
            row["utterance"].spoken_at,
            row["utterance"].source.get("url", ""),
            -row["utterance"].order,
            row["utterance"].utterance_id,
        ),
        reverse=True,
    )

    speaker_keys: set[str] = set()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        utterance = row["utterance"]
        grouped[row["month"]].append(row)

        speaker_key = utterance.person_id or (
            f"raw:{utterance.speaker_name}:{utterance.speaker_role or ''}"
        )
        speaker_keys.add(speaker_key)

    timeline = [
        {
            "key": key,
            "label": f"{key[:4]}년 {int(key[5:7])}월",
            "rows": grouped[key],
        }
        for key in sorted(grouped, reverse=True)
    ]

    unmatched = sum(row["person"] is None for row in rows)
    source_count = len(
        {row["utterance"].source["url"] for row in rows}
    )
    return {
        "total_corpus": len(utterances),
        "total": len(rows),
        "attributed": len(rows) - unmatched,
        "unmatched": unmatched,
        "speaker_count": len(speaker_keys),
        "source_count": source_count,
        "date_count": len({row["utterance"].spoken_at for row in rows}),
        "first_date": rows[-1]["utterance"].spoken_at if rows else None,
        "last_date": rows[0]["utterance"].spoken_at if rows else None,
        "excluded_avian": excluded_avian,
        "timeline": timeline,
    }
