import json

import politics_tracker.site.build as site_build_module
from politics_tracker.enrich.stances import load_stance_axes
from politics_tracker.matching import match_utterances
from politics_tracker.models import (
    Bill,
    ConsistencyPair,
    Person,
    ReviewItem,
    Stance,
    Utterance,
    Vote,
    stance_id_for,
)
from politics_tracker.site.build import build_site


def test_build_site_renders_person_timeline_with_source_links(tmp_path):
    people = [
        Person(
            person_id="p1",
            name="이가상",
            party="가상당",
            district="서울 예시구갑",
            era="제20대, 제21대, 제22대",
        ),
        Person(person_id="p2", name="박사례", party="예시당"),
    ]
    utterances = [
        Utterance(
            utterance_id="u1",
            speaker_name="이가상",
            speaker_role="의원",
            spoken_at="2026-07-15",
            venue={"type": "assembly_plenary", "session": "가상 본회의"},
            text="공급 확대가 필요합니다.",
            source={"kind": "assembly_minutes", "url": "https://example.invalid/minutes/1",
                    "title": "가상 회의록"},
            topics=["housing"],
            topic_source="rules",
        ),
        Utterance(
            utterance_id="u3",
            speaker_name="이가상",
            speaker_role="위원",
            spoken_at="2026-07-14",
            venue={
                "type": "assembly_committee",
                "session": "제400회 국회 제2차 회의",
                "committee": "국토교통위원회",
            },
            text="위원회 발언입니다.",
            source={
                "kind": "assembly_minutes",
                "url": "https://example.invalid/minutes/committee-1",
                "title": "가상 위원회 회의록",
            },
        ),
        Utterance(
            utterance_id="u2",
            speaker_name="장외인",
            speaker_role="참고인",
            spoken_at="2026-07-15",
            venue={"type": "assembly_plenary", "session": "가상 본회의"},
            text="명부에 없는 화자의 발언.",
            source={"kind": "assembly_minutes", "url": "https://example.invalid/minutes/1"},
        ),
    ]
    match_utterances(utterances, people)
    before = Stance(
        stance_id=stance_id_for("u3", "housing_regulation", "stance_v1"),
        utterance_id="u3",
        person_id="p1",
        axis="housing_regulation",
        value=-0.6,
        confidence=0.9,
        rationale_quote="위원회 발언",
        extractor={"backend": "test", "model": "test", "prompt_version": "stance_v1"},
    )
    after = Stance(
        stance_id=stance_id_for("u1", "housing_regulation", "stance_v1"),
        utterance_id="u1",
        person_id="p1",
        axis="housing_regulation",
        value=0.3,
        confidence=0.9,
        rationale_quote="공급 확대",
        extractor={"backend": "test", "model": "test", "prompt_version": "stance_v1"},
    )
    change = ReviewItem(
        review_id="rev_change",
        kind="stance_change",
        target_id="stchg_1",
        payload={
            "person_id": "p1",
            "axis": "housing_regulation",
            "before_stance_id": before.stance_id,
            "after_stance_id": after.stance_id,
            "context_note": "두 원문과 당시 회의 맥락을 확인했습니다.",
        },
        reason="held:stance_change_requires_context",
        status="approved",
        created_at="2026-08-16T00:00:00Z",
        decided_at="2026-08-16T01:00:00Z",
    )
    bill = Bill(
        bill_id="bill_1",
        assembly_bill_no="2200001",
        title="주택법 일부개정법률안",
        proposed_at="2026-07-01",
        link_url="https://example.invalid/bill/1",
    )
    vote = Vote(
        vote_id="vote_1",
        bill_id=bill.bill_id,
        person_id="p1",
        decision="찬성",
        voted_at="2026-07-16",
        source={"kind": "assembly_vote_api", "url": "https://example.invalid/vote/1"},
    )
    consistency_pair = ConsistencyPair(
        consistency_id="cons_1",
        person_id="p1",
        bill_id=bill.bill_id,
        utterance_id="u1",
        stance_id=after.stance_id,
        vote_id=vote.vote_id,
        axis="housing_regulation",
        stance_value=after.value,
        expected_decision="찬성",
        vote_decision=vote.decision,
        consistent=True,
    )
    stats = build_site(
        people,
        utterances,
        tmp_path,
        stances=[before, after],
        stance_axes=load_stance_axes(),
        reviews=[change],
        bills=[bill],
        votes=[vote],
        consistency_pairs=[consistency_pair],
    )

    assert stats.people_pages == 2
    assert stats.utterances_rendered == 2
    assert stats.unmatched_utterances == 1

    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "이가상" in index and "person/p1.html" in index

    p1 = (tmp_path / "person" / "p1.html").read_text(encoding="utf-8")
    assert "공급 확대가 필요합니다." in p1
    assert 'id="u1"' in p1
    assert 'href="#u1"' in p1
    assert "이 발언의 고유 링크" in p1
    assert 'data-topics="housing"' in p1
    assert 'data-topic="housing"' in p1
    assert "reset-topic-filter" in p1
    assert "timeline-group" in p1
    assert "제제20대" not in p1
    assert "제20대, 제21대, 제22대" in p1
    assert "국토교통위원회" in p1
    assert "위원회 발언입니다." in p1
    assert "정책 축별 입장 이력" in p1
    assert "규제 완화·공급 확대 우선" in p1
    assert 'style="left: 20.0%"' in p1
    assert 'style="left: 65.0%"' in p1
    assert "확인된 입장 변화" in p1
    assert "두 원문과 당시 회의 맥락을 확인했습니다." in p1
    assert "말과 표결 기록" in p1
    assert "일치 <span class=\"num\">1</span>건 / 판정 가능" in p1
    assert "주택법 일부개정법률안" in p1
    assert "https://example.invalid/bill/1" in p1
    assert "https://example.invalid/vote/1" in p1
    assert "https://example.invalid/minutes/1" in p1  # 발언마다 원문 링크

    p2 = (tmp_path / "person" / "p2.html").read_text(encoding="utf-8")
    assert "아직 수록된 발언이 없습니다" in p2
    assert "말과 표결 기록" not in p2
    assert (tmp_path / "about.html").exists()
    assert 'topic/housing.html' in index
    housing = (tmp_path / "topic" / "housing.html").read_text(encoding="utf-8")
    assert "부동산·주거" in housing
    assert "공급 확대가 필요합니다." in housing
    assert 'data-person-id="p1"' in housing
    assert "person-filter" in housing
    assert "person/p1.html#u1" in housing
    search_page = (tmp_path / "search.html").read_text(encoding="utf-8")
    assert "발언 검색" in search_page
    assert 'fetch("search/" + name)' in search_page
    shard = json.loads((tmp_path / "search" / "index-2026.json").read_text(encoding="utf-8"))
    assert [row["utterance_id"] for row in shard] == ["u1", "u3"]
    assert shard[0]["person_name"] == "이가상"
    assert shard[0]["source_url"] == "https://example.invalid/minutes/1"


def test_search_index_splits_into_half_year_shards_over_size_limit(tmp_path, monkeypatch):
    person = Person(person_id="p1", name="이가상")
    utterances = [
        Utterance(
            utterance_id="first-half",
            speaker_name="이가상",
            speaker_role="의원",
            spoken_at="2026-03-01",
            venue={"type": "assembly_plenary", "session": "회의"},
            text="상반기 발언",
            source={"kind": "assembly_minutes", "url": "https://example.invalid/first"},
            person_id="p1",
        ),
        Utterance(
            utterance_id="second-half",
            speaker_name="이가상",
            speaker_role="의원",
            spoken_at="2026-09-01",
            venue={"type": "assembly_plenary", "session": "회의"},
            text="하반기 발언",
            source={"kind": "assembly_minutes", "url": "https://example.invalid/second"},
            person_id="p1",
        ),
    ]
    monkeypatch.setattr(site_build_module, "_MAX_SEARCH_INDEX_BYTES", 1)
    build_site([person], utterances, tmp_path)
    assert sorted(path.name for path in (tmp_path / "search").glob("*.json")) == [
        "index-2026-h1.json",
        "index-2026-h2.json",
    ]
