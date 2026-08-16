from politics_tracker.analytics.ai_mentions import (
    ai_mention_contexts,
    build_ai_analysis,
    is_ai_mention,
    matched_ai_terms,
)
from politics_tracker.models import Person, Utterance
from politics_tracker.site.build import build_site


def _utterance(
    utterance_id: str,
    text: str,
    *,
    spoken_at: str = "2026-04-01",
    person_id: str | None = None,
    speaker_name: str = "이가상",
) -> Utterance:
    return Utterance(
        utterance_id=utterance_id,
        speaker_name=speaker_name,
        speaker_role="의원",
        spoken_at=spoken_at,
        venue={"type": "assembly_committee", "session": "가상 회의"},
        text=text,
        source={
            "kind": "assembly_minutes",
            "url": f"https://example.invalid/minutes/{utterance_id}",
            "title": "가상 회의록",
        },
        person_id=person_id,
    )


def test_ai_detection_excludes_avian_influenza_but_keeps_technology_context():
    assert not is_ai_mention("조류독감(AI)과 고병원성 AI 방역 대책을 점검합니다.")
    assert not is_ai_mention("양계농장의 AI 백신 개발이 늦어지고 있습니다.")
    assert is_ai_mention("축산농가가 AI 모델을 구축하고 스마트팜에 AI를 도입해야 합니다.")
    assert is_ai_mention("AI와 로봇을 중심으로 산업 전환을 준비해야 합니다.")
    assert is_ai_mention("딥페이크 피해와 생성물 표시 문제를 다뤄야 합니다.")
    assert is_ai_mention("에이아이와 Artificial Intelligence 연구를 지원합니다.")
    assert not is_ai_mention("디지털 전환을 준비해야 합니다.")


def test_ai_term_detection_is_deterministic():
    text = (
        "ChatGPT와 AI 데이터센터를 위한 전력망 투자, 반도체 연구개발, "
        "개인정보 보호 법안을 함께 논의합니다."
    )
    assert matched_ai_terms(text) == ["AI", "ChatGPT"]
    assert matched_ai_terms("에이아이와 Artificial Intelligence") == [
        "에이아이",
        "Artificial Intelligence",
    ]


def test_ai_contexts_cover_every_mention_without_rendering_unrelated_full_text():
    text = "앞부분" * 80 + " AI 산업을 논의합니다. " + "중간내용" * 100 + " 딥페이크 대책입니다. " + "끝부분" * 80
    contexts = ai_mention_contexts(text, before=30, after=50)
    assert len(contexts) == 2
    assert "AI 산업" in contexts[0]
    assert "딥페이크" in contexts[1]
    assert all(context.startswith("…") and context.endswith("…") for context in contexts)
    assert sum(map(len, contexts)) < len(text) // 2


def test_build_ai_analysis_defines_scope_without_automated_public_topics():
    people = [Person(person_id="p1", name="이가상", party="가상당")]
    utterances = [
        _utterance(
            "u1",
            "AI 산업 투자와 데이터센터 전력 공급을 확대해야 합니다.",
            spoken_at="2026-04-01",
            person_id="p1",
        ),
        _utterance(
            "u2",
            "조류독감(AI) 방역과 살처분 대책이 필요합니다.",
            spoken_at="2026-04-02",
            speaker_name="장외인",
        ),
        _utterance(
            "u3",
            "딥페이크 피해와 개인정보 보호 대책을 마련해야 합니다.",
            spoken_at="2026-06-02",
            speaker_name="참고인",
        ),
        _utterance(
            "u4",
            "AI 대전환의 방향을 논의합니다.",
            spoken_at="2026-06-01",
            speaker_name="장외인",
        ),
        _utterance(
            "u5",
            "일반적인 정책 발언입니다.",
            spoken_at="2026-05-01",
            person_id="p1",
        ),
    ]

    analysis = build_ai_analysis(people, utterances)

    assert analysis["total_corpus"] == 5
    assert analysis["total"] == 3
    assert analysis["attributed"] == 1
    assert analysis["unmatched"] == 2
    assert analysis["speaker_count"] == 3
    assert analysis["excluded_avian"] == 1
    assert analysis["first_date"] == "2026-04-01"
    assert analysis["last_date"] == "2026-06-02"
    assert [group["key"] for group in analysis["timeline"]] == [
        "2026-06",
        "2026-04",
    ]
    all_rows = [row for group in analysis["timeline"] for row in group["rows"]]
    assert [row["utterance"].utterance_id for row in all_rows] == ["u3", "u4", "u1"]
    assert all("topics" not in row and "topic_keys" not in row for row in all_rows)
    assert "topics" not in analysis
    assert "months" not in analysis
    assert "top_dates" not in analysis
    assert analysis["source_count"] == 3


def test_site_build_renders_ai_scope_and_all_evidence_without_rule_charts(tmp_path):
    people = [Person(person_id="p1", name="이가상", party="가상당")]
    utterances = [
        _utterance(
            "u1",
            "AI 산업 투자와 데이터센터 전력 공급을 확대해야 합니다.",
            spoken_at="2026-04-01",
            person_id="p1",
        ),
        _utterance(
            "u2",
            "조류독감(AI) 방역과 살처분 대책이 필요합니다.",
            spoken_at="2026-04-02",
            speaker_name="장외인",
        ),
        _utterance(
            "u3",
            "딥페이크 피해와 개인정보 보호 대책을 마련해야 합니다.",
            spoken_at="2026-06-02",
            speaker_name="참고인",
        ),
    ]

    build_site(people, utterances, tmp_path)

    page = (tmp_path / "analysis" / "ai.html").read_text(encoding="utf-8")
    assert "국회가 AI를 말할 때" in page
    assert '국회 발언 <span class="num">3</span>건 가운데' in page
    assert 'AI를 직접 언급한 발언 <span class="num">2</span>건' in page
    assert "현재 자료에는 정성 보고서가 인용하는 원문 근거가 모두 들어 있지 않아" in page
    assert 'data-ai-topic-filter=' not in page
    assert 'data-ai-month-filter=' not in page
    assert "주제별 언급" not in page
    assert "월별 언급량" not in page
    assert page.count('class="ai-mention"') == 2
    assert "조류독감(AI)" not in page
    assert "AI 산업 투자" in page
    assert "딥페이크 피해" in page
    assert "../person/p1.html#u1" in page
    assert "인물 미귀속" in page
    assert "https://example.invalid/minutes/u3" in page
    assert "ai-filter-reset" in page
    assert "scrollIntoView" not in page
    assert 'id="ai-query"' in page

    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'href="analysis/ai.html"' in index

    methodology = (tmp_path / "methodology.html").read_text(encoding="utf-8")
    assert 'href="analysis/ai.html"' in methodology
    assert "조류인플루엔자 또는 조류독감의 약자인 AI" in methodology
    assert "키워드 빈도, 자동 점수, 발언량 순위로 만들지 않습니다" in methodology
