from copy import deepcopy

import pytest

from politics_tracker.analytics.ai_qualitative import (
    load_ai_qualitative_report,
    resolve_ai_qualitative_report,
    validate_ai_qualitative_report,
)
from politics_tracker.models import Utterance
from politics_tracker.site.build import build_site


def _analysis_for_report(report):
    rows = []
    for theme in report["themes"]:
        for index, item in enumerate(theme["evidence"]):
            utterance = Utterance(
                utterance_id=item["utterance_id"],
                speaker_name=f"{theme['key']}-화자-{index}",
                speaker_role="의원",
                spoken_at=f"2026-{index + 1:02d}-01",
                venue={
                    "type": "assembly_committee",
                    "session": f"{theme['key']} 회의 {index}",
                    "committee": f"{theme['eyebrow']}위원회",
                },
                text=f"AI 논의 앞 문맥 {item['focus']} 뒤 문맥",
                source={
                    "kind": "assembly_minutes",
                    "url": f"https://example.invalid/{theme['key']}/{index}",
                    "title": f"{theme['title']} 근거 회의 {index}",
                },
                order=index,
            )
            rows.append(
                {
                    "utterance": utterance,
                    "person": None,
                    "speaker_name": utterance.speaker_name,
                    "speaker_role": utterance.speaker_role,
                    "month": utterance.spoken_at[:7],
                    "terms": ["AI"],
                    "contexts": [utterance.text],
                }
            )
    return {"timeline": [{"key": "2026", "label": "2026년", "rows": rows}]}


def test_curated_ai_report_has_editorial_depth_and_no_em_dash():
    report = load_ai_qualitative_report()

    assert report["reviewed_through"] == "2026-08-12"
    assert len(report["themes"]) == 12
    assert sum(len(theme["evidence"]) for theme in report["themes"]) == 47
    assert len(report["chronology"]) == 4
    assert len(report["cross_cutting"]) >= 4
    assert all(len(theme["discussions"]) >= 2 for theme in report["themes"])
    assert "—" not in repr(report)


def test_resolver_uses_original_context_and_requires_complete_evidence():
    report = load_ai_qualitative_report()
    analysis = _analysis_for_report(report)

    resolved = resolve_ai_qualitative_report(analysis, report)

    assert resolved["available"] is True
    first = resolved["themes"][0]["evidence"][0]
    assert first["focus"] in first["focus_context"]
    assert first["utterance"].source["url"].startswith("https://example.invalid/")

    partial = deepcopy(analysis)
    partial["timeline"][0]["rows"].pop()
    with pytest.raises(ValueError, match="missing evidence IDs"):
        resolve_ai_qualitative_report(partial, report)


def test_resolver_hides_report_when_no_curated_evidence_is_present():
    report = load_ai_qualitative_report()
    empty = resolve_ai_qualitative_report({"timeline": []}, report)

    assert empty["available"] is False
    assert empty["missing_evidence_count"] > 0


def test_resolver_rejects_quote_absent_from_original():
    report = load_ai_qualitative_report()
    analysis = _analysis_for_report(report)
    analysis["timeline"][0]["rows"][0]["utterance"].text = "AI만 있고 선정 근거는 없습니다."

    with pytest.raises(ValueError, match="Evidence focus is absent"):
        resolve_ai_qualitative_report(analysis, report)


def test_report_validator_rejects_site_copy_with_em_dash():
    report = load_ai_qualitative_report()
    report["central_judgment"] += " — 금지된 문장부호"

    with pytest.raises(ValueError, match="em dash"):
        validate_ai_qualitative_report(report)


def test_full_curated_page_is_answer_first_and_keeps_complete_finder(tmp_path):
    report = load_ai_qualitative_report()
    analysis = _analysis_for_report(report)
    utterances = [
        row["utterance"]
        for group in analysis["timeline"]
        for row in group["rows"]
    ]

    build_site([], utterances, tmp_path)
    page = (tmp_path / "analysis" / "ai.html").read_text(encoding="utf-8")

    assert "AI 논의의 중심은 기술 가능성에서 집행 조건과 사회적 비용으로 이동했습니다" in page
    assert page.count('class="ai-theme"') == len(report["themes"])
    assert "구체적으로 다뤄진 논의" in page
    assert "충돌하는 관점" in page
    assert "정책적 의미" in page
    assert "원문 문맥 확인" in page
    assert page.count('class="ai-mention"') == len(utterances)
    assert 'id="ai-query"' in page
    assert 'data-ai-topic-filter=' not in page
    assert "월별 언급량" not in page
