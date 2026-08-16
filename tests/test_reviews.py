from argparse import Namespace

import pytest

from politics_tracker import cli
from politics_tracker.enrich import topics as topics_module
from politics_tracker.models import (
    Person,
    ReviewItem,
    Stance,
    Utterance,
    review_id_for,
    stance_id_for,
)
from politics_tracker.site.build import build_site
from politics_tracker.storage import SqliteStore


def _utterance() -> Utterance:
    return Utterance(
        utterance_id="utt_held",
        speaker_name="이가상",
        speaker_role="의원",
        spoken_at="2026-07-15",
        venue={"type": "assembly_plenary", "session": "가상 본회의"},
        text="부동산과 주택 공급에 관한 발언입니다.",
        source={"kind": "assembly_minutes", "url": "https://example.invalid/minutes/1"},
        person_id="p1",
        topics=[],
        topic_source="held:low_confidence",
    )


def _review(payload=None) -> ReviewItem:
    candidate = payload or {"topics": [], "topic_source": "held:low_confidence"}
    return ReviewItem(
        review_id=review_id_for("topic", "utt_held", "held:low_confidence", candidate),
        kind="topic",
        target_id="utt_held",
        payload=candidate,
        reason="held:low_confidence",
        status="pending",
        created_at="2026-08-16T00:00:00Z",
    )


def test_review_id_is_deterministic_for_payload_key_order():
    first = review_id_for("topic", "utt_held", "held", {"topics": ["housing"], "confidence": 0.4})
    second = review_id_for("topic", "utt_held", "held", {"confidence": 0.4, "topics": ["housing"]})
    assert first == second
    assert first.startswith("rev_")


def test_review_queue_is_idempotent_and_decision_is_immutable(tmp_path):
    store = SqliteStore(tmp_path / "db.sqlite")
    review = _review()
    assert store.enqueue_review(review)
    assert not store.enqueue_review(review)
    assert len(store.load_reviews(status="pending")) == 1

    decided = store.decide_review(
        review.review_id,
        status="rejected",
        decided_at="2026-08-16T01:00:00Z",
        note="근거 부족",
    )
    assert decided.status == "rejected"
    with pytest.raises(ValueError, match="immutable"):
        store.decide_review(
            review.review_id,
            status="approved",
            decided_at="2026-08-16T02:00:00Z",
        )


def test_held_claude_topic_is_queued_once(tmp_path, monkeypatch):
    db_path = tmp_path / "db.sqlite"
    store = SqliteStore(db_path)
    store.save_people([Person(person_id="p1", name="이가상")])
    store.save_utterances([_utterance()])

    def fake_classify(utterances, **kwargs):
        utterances[0].topics = []
        utterances[0].topic_source = "held:low_confidence"
        return {"total": 1, "with_topics": 0, "held_low_confidence": 1, "held_refusal": 0}

    monkeypatch.setattr(topics_module, "classify_claude", fake_classify)
    args = Namespace(
        db=str(db_path),
        backend="claude",
        model="fake-model",
        batch_size=20,
        confidence_threshold=0.6,
    )
    assert cli.cmd_classify_topics(args) == 0
    assert cli.cmd_classify_topics(args) == 0
    assert len(store.load_reviews(status="pending")) == 1


def test_topic_review_approval_updates_record_and_site(tmp_path):
    db_path = tmp_path / "db.sqlite"
    store = SqliteStore(db_path)
    people = [Person(person_id="p1", name="이가상")]
    store.save_people(people)
    store.save_utterances([_utterance()])
    review = _review()
    store.enqueue_review(review)

    args = Namespace(
        db=str(db_path),
        review_id=review.review_id,
        edit=["topics=housing"],
        note="원문 대조 완료",
    )
    assert cli.cmd_review_approve(args) == 0
    utterance = store.load_utterances()[0]
    assert utterance.topics == ["housing"]
    assert utterance.topic_source == "human_reviewed"
    assert utterance.human_reviewed is True
    assert store.get_review(review.review_id).status == "approved"
    assert cli.cmd_review_approve(args) == 1

    site = tmp_path / "site"
    build_site(people, [utterance], site)
    housing = (site / "topic" / "housing.html").read_text(encoding="utf-8")
    assert "부동산과 주택 공급에 관한 발언입니다." in housing


def test_stance_review_requires_original_quote_and_marks_reviewed(tmp_path):
    db_path = tmp_path / "db.sqlite"
    store = SqliteStore(db_path)
    store.save_people([Person(person_id="p1", name="이가상")])
    utterance = _utterance()
    store.save_utterances([utterance])
    stance = Stance(
        stance_id=stance_id_for("utt_held", "housing_regulation", "stance_v1"),
        utterance_id="utt_held",
        person_id="p1",
        axis="housing_regulation",
        value=-0.4,
        confidence=0.4,
        rationale_quote="부동산과 주택 공급",
        extractor={
            "backend": "claude",
            "model": "claude-test",
            "prompt_version": "stance_v1",
        },
        held_reason="low_confidence",
    )
    store.save_stances([stance])
    payload = stance.to_dict()
    review = ReviewItem(
        review_id=review_id_for("stance", stance.stance_id, "held:low_confidence", payload),
        kind="stance",
        target_id=stance.stance_id,
        payload=payload,
        reason="held:low_confidence",
        status="pending",
        created_at="2026-08-16T00:00:00Z",
    )
    store.enqueue_review(review)
    args = Namespace(
        db=str(db_path),
        review_id=review.review_id,
        edit=["value=-0.8", "confidence=1", "rationale_quote=부동산과 주택 공급"],
        note="원문 대조 완료",
    )
    assert cli.cmd_review_approve(args) == 0
    approved = store.load_stances()[0]
    assert approved.value == -0.8
    assert approved.confidence == 1
    assert approved.human_reviewed is True
    assert approved.held_reason is None
