"""국회 발언의 인공지능 언급을 결정적으로 탐지하고 요약한다."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Pattern

from ..models import Person, Utterance


@dataclass(frozen=True)
class AiTopic:
    key: str
    label: str
    description: str
    rules: tuple[tuple[Pattern[str], int], ...]


def _rule(pattern: str, weight: int = 1) -> tuple[Pattern[str], int]:
    return re.compile(pattern, re.IGNORECASE), weight


AI_TOPICS: tuple[AiTopic, ...] = (
    AiTopic(
        key="industry",
        label="산업·투자",
        description="기업, 투자, 시장, 창업과 국가 산업 경쟁력",
        rules=(
            _rule(
                r"산업|기업|투자|경쟁력|성장|시장|스타트업|창업|벤처|수출|"
                r"생산성|제조|사업|상용화|매출"
            ),
        ),
    ),
    AiTopic(
        key="infrastructure_energy",
        label="데이터센터·에너지",
        description="데이터센터, 전력망, 전력·용수와 에너지 공급",
        rules=(
            _rule(r"데이터\s*센터", 4),
            _rule(
                r"전력망|전력|전기|에너지|용수|냉각|원전|재생에너지|PPA|LNG|탄소"
            ),
        ),
    ),
    AiTopic(
        key="talent_labor",
        label="인재·교육·노동",
        description="인재 양성, 교육, 일자리 변화와 노동 전환",
        rules=(
            _rule(
                r"일자리|고용|노동|근로|직업|업무|대체|실업|청년|인재|교육|"
                r"대학|학생|교사|훈련|숙련|채용|취업"
            ),
        ),
    ),
    AiTopic(
        key="rights_safety",
        label="권리·안전·신뢰",
        description="개인정보, 저작권, 딥페이크, 보안과 AI 책임",
        rules=(
            _rule(r"개인정보|저작권|초상권|딥\s*페이크|프라이버시", 3),
            _rule(
                r"허위|가짜|보안|해킹|유출|윤리|안전|위험|감시|차별|편향|"
                r"책임|기본권|피해|범죄|사기"
            ),
        ),
    ),
    AiTopic(
        key="public_service",
        label="공공서비스·행정",
        description="정부 행정, 공공서비스, 의료·복지와 현장 적용",
        rules=(
            _rule(
                r"행정|공공|공무원|민원|복지|의료|보건|돌봄|세무|국세|지자체|"
                r"지방자치|재난|교통|치안"
            ),
        ),
    ),
    AiTopic(
        key="security_sovereignty",
        label="외교·안보·주권",
        description="외교·국방, 국제 경쟁, 공급망과 기술 주권",
        rules=(
            _rule(
                r"안보|국방|군사|전쟁|무기|미사일|외교|국제|글로벌|미국|중국|"
                r"미중|주권|소버린|공급망|G7|유엔|UN"
            ),
        ),
    ),
    AiTopic(
        key="law_governance",
        label="법·제도·거버넌스",
        description="법률, 규율 체계, 특례와 공적 의사결정 절차",
        rules=(
            _rule(
                r"법안|법률|특별법|기본법|입법|거버넌스|가이드라인|표결|의결|"
                r"시행령|법제|규율|규정|특례"
            ),
        ),
    ),
    AiTopic(
        key="technology_research",
        label="기술·연구",
        description="모델·알고리즘, 연구개발, 반도체와 로봇 기술",
        rules=(
            _rule(
                r"기술|연구|개발|모델|알고리즘|컴퓨팅|소프트웨어|클라우드|GPU|"
                r"반도체|로봇|자율주행|양자|R&D|과학"
            ),
        ),
    ),
)

GENERAL_TOPIC = {
    "key": "general",
    "label": "총론·정책 방향",
    "description": "위 세부 맥락에 속하지 않는 AI 전환의 방향과 총론",
}

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


def classify_ai_topics(text: str, limit: int = 3) -> list[dict[str, str]]:
    """발언에 나타난 AI 정책 맥락을 점수순으로 최대 ``limit``개 반환한다."""
    scored: list[tuple[int, int, AiTopic]] = []
    for order, topic in enumerate(AI_TOPICS):
        score = sum(
            len(pattern.findall(text)) * weight
            for pattern, weight in topic.rules
        )
        if score:
            scored.append((score, -order, topic))
    scored.sort(reverse=True, key=lambda item: (item[0], item[1]))
    selected = [topic for _, _, topic in scored[:limit]]
    if not selected:
        return [dict(GENERAL_TOPIC)]
    return [
        {
            "key": topic.key,
            "label": topic.label,
            "description": topic.description,
        }
        for topic in selected
    ]


def _month_keys(first: str, last: str) -> list[str]:
    year, month = map(int, first.split("-")[:2])
    last_year, last_month = map(int, last.split("-")[:2])
    keys = []
    while (year, month) <= (last_year, last_month):
        keys.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return keys


def build_ai_analysis(
    people: list[Person], utterances: list[Utterance]
) -> dict[str, Any]:
    """AI 언급의 주제 분포, 월별 추이와 전건 근거 행을 만든다."""
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
        topics = classify_ai_topics(utterance.text)
        rows.append(
            {
                "utterance": utterance,
                "person": person,
                "speaker_name": person.name if person else utterance.speaker_name,
                "speaker_role": utterance.speaker_role,
                "month": utterance.spoken_at[:7],
                "topics": topics,
                "topic_keys": " ".join(topic["key"] for topic in topics),
                "terms": matched_ai_terms(utterance.text),
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

    topic_counts: Counter[str] = Counter()
    month_counts: Counter[str] = Counter()
    date_counts: Counter[str] = Counter()
    term_counts: Counter[str] = Counter()
    speaker_keys: set[str] = set()
    sources_by_date: dict[str, Counter[str]] = defaultdict(Counter)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        utterance = row["utterance"]
        for topic in row["topics"]:
            topic_counts[topic["key"]] += 1
        month_counts[row["month"]] += 1
        date_counts[utterance.spoken_at] += 1
        term_counts.update(row["terms"])
        source_title = utterance.source.get("title") or "회의록 원문"
        sources_by_date[utterance.spoken_at][source_title] += 1
        grouped[row["month"]].append(row)

        speaker_key = utterance.person_id or (
            f"raw:{utterance.speaker_name}:{utterance.speaker_role or ''}"
        )
        speaker_keys.add(speaker_key)

    total = len(rows)
    topic_definitions = [
        {
            "key": topic.key,
            "label": topic.label,
            "description": topic.description,
        }
        for topic in AI_TOPICS
    ] + [dict(GENERAL_TOPIC)]
    max_topic_count = max(topic_counts.values(), default=1)
    topic_summaries = []
    for topic in topic_definitions:
        count = topic_counts[topic["key"]]
        topic_summaries.append(
            {
                **topic,
                "count": count,
                "share": round(count / total * 100, 1) if total else 0.0,
                "bar_width": round(count / max_topic_count * 100, 2),
            }
        )
    topic_summaries.sort(key=lambda row: (-row["count"], row["label"]))

    months = []
    if rows:
        chronological_months = _month_keys(
            rows[-1]["utterance"].spoken_at,
            rows[0]["utterance"].spoken_at,
        )
        max_month_count = max(month_counts.values(), default=1)
        for key in chronological_months:
            count = month_counts[key]
            year, month = key.split("-")
            months.append(
                {
                    "key": key,
                    "label": f"{year}년 {int(month)}월",
                    "short_label": f"{int(month)}월",
                    "count": count,
                    "height": round(count / max_month_count * 100, 2),
                }
            )

    timeline = [
        {
            "key": key,
            "label": f"{key[:4]}년 {int(key[5:7])}월",
            "rows": grouped[key],
        }
        for key in sorted(grouped, reverse=True)
    ]

    top_dates = []
    for spoken_at, count in sorted(
        date_counts.items(), key=lambda item: (item[1], item[0]), reverse=True
    )[:8]:
        source_title, source_count = sorted(
            sources_by_date[spoken_at].items(),
            key=lambda item: (-item[1], item[0]),
        )[0]
        top_dates.append(
            {
                "spoken_at": spoken_at,
                "count": count,
                "source_title": source_title,
                "source_count": source_count,
            }
        )

    unmatched = sum(row["person"] is None for row in rows)
    source_count = len(
        {row["utterance"].source["url"] for row in rows}
    )
    return {
        "total_corpus": len(utterances),
        "total": total,
        "attributed": total - unmatched,
        "unmatched": unmatched,
        "speaker_count": len(speaker_keys),
        "source_count": source_count,
        "date_count": len(date_counts),
        "first_date": rows[-1]["utterance"].spoken_at if rows else None,
        "last_date": rows[0]["utterance"].spoken_at if rows else None,
        "excluded_avian": excluded_avian,
        "topics": topic_summaries,
        "months": months,
        "timeline": timeline,
        "top_dates": top_dates,
        "terms": [
            {"label": label, "count": term_counts[label]}
            for label, _ in _TERM_PATTERNS
            if term_counts[label]
        ],
    }
