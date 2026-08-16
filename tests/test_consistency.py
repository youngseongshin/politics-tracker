from argparse import Namespace

from politics_tracker import cli
from politics_tracker.analytics.consistency import (
    compute_consistency_pairs,
    consistency_summaries,
)
from politics_tracker.enrich.stances import load_stance_axes
from politics_tracker.models import (
    Bill,
    Stance,
    Utterance,
    UtteranceBillLink,
    Vote,
    bill_link_id_for,
    consistency_id_for,
    stance_id_for,
    vote_id_for,
)
from politics_tracker.storage import SqliteStore


def _records(*, confidence=0.9, reviewed=False, method="rule:title_match"):
    bill = Bill(
        bill_id="bill_1",
        assembly_bill_no="2200001",
        title="주택법 일부개정법률안",
        proposed_at="2026-04-01",
        link_url="https://example.invalid/bill/1",
    )
    utterance = Utterance(
        utterance_id="utt_1",
        speaker_name="이가상",
        speaker_role="의원",
        spoken_at="2026-04-20",
        venue={"type": "assembly_plenary"},
        text="투기를 억제해야 하므로 주택법 개정안에 찬성합니다.",
        source={"kind": "assembly_minutes", "url": "https://example.invalid/minutes"},
        person_id="p1",
        topics=["housing"],
    )
    stance = Stance(
        stance_id=stance_id_for("utt_1", "housing_regulation", "stance_v1"),
        utterance_id="utt_1",
        person_id="p1",
        axis="housing_regulation",
        value=0.7,
        confidence=confidence,
        rationale_quote="투기를 억제해야",
        extractor={"backend": "test", "model": "test", "prompt_version": "stance_v1"},
        human_reviewed=reviewed,
    )
    link = UtteranceBillLink(
        link_id=bill_link_id_for("utt_1", "bill_1", method),
        utterance_id="utt_1",
        bill_id="bill_1",
        method=method,
        confidence=1.0 if method == "rule:title_match" else 0.8,
        extractor={"backend": "test", "model": "test", "prompt_version": "link_v1"},
        human_reviewed=reviewed,
    )
    vote = Vote(
        vote_id=vote_id_for("bill_1", "p1", "2026-04-23"),
        bill_id="bill_1",
        person_id="p1",
        decision="찬성",
        voted_at="2026-04-23",
        source={"kind": "assembly_vote_api", "url": "https://example.invalid/vote"},
    )
    return bill, utterance, stance, link, vote


def test_consistency_applies_eligibility_and_direction_mapping():
    bill, utterance, stance, link, vote = _records()
    axes = load_stance_axes()
    assert axes[0].bill_direction == {"positive": "찬성", "negative": "반대"}
    pairs = compute_consistency_pairs(
        [stance], [utterance], [bill], [vote], [link], axes
    )
    assert len(pairs) == 1
    assert pairs[0].expected_decision == "찬성"
    assert pairs[0].consistent is True
    assert pairs[0].consistency_id == consistency_id_for(
        stance.stance_id, vote.vote_id, "consistency_v1"
    )
    assert consistency_summaries(pairs) == {
        "p1": {"consistent": 1, "eligible": 1}
    }


def test_consistency_excludes_low_confidence_unreviewed_link_and_same_day():
    bill, utterance, stance, link, vote = _records(
        confidence=0.84, method="llm:candidate"
    )
    axes = load_stance_axes()
    assert compute_consistency_pairs(
        [stance], [utterance], [bill], [vote], [link], axes
    ) == []

    stance.human_reviewed = True
    link.human_reviewed = True
    utterance.spoken_at = vote.voted_at
    assert compute_consistency_pairs(
        [stance], [utterance], [bill], [vote], [link], axes
    ) == []


def test_compute_consistency_cli_replaces_derived_rows_deterministically(tmp_path):
    bill, utterance, stance, link, vote = _records()
    store = SqliteStore(tmp_path / "db.sqlite")
    store.save_utterances([utterance])
    store.save_bills([bill])
    store.save_stances([stance])
    store.save_bill_links([link])
    store.save_votes([vote])
    args = Namespace(db=str(store.db_path), axes="config/stance_axes.yaml")
    assert cli.cmd_compute_consistency(args) == 0
    first = store.load_consistency_pairs()
    assert len(first) == 1
    assert cli.cmd_compute_consistency(args) == 0
    assert store.load_consistency_pairs() == first
