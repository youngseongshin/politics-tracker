import json
from argparse import Namespace
from types import SimpleNamespace

from politics_tracker import cli
from politics_tracker.enrich.bill_links import (
    extract_bill_links_claude,
    extract_bill_links_rules,
    lexical_candidate_pairs,
)
from politics_tracker.models import (
    Bill,
    ReviewItem,
    Utterance,
    UtteranceBillLink,
    review_id_for,
)
from politics_tracker.storage import SqliteStore


def _bill():
    return Bill(
        bill_id="bill_test",
        assembly_bill_no="2218438",
        title="세종특별자치시 설치 등에 관한 특별법 일부개정법률안",
        proposed_at="2026-04-01",
        link_url="https://example.invalid/bill/1",
        raw={"BILL_ID": "PRC_TEST"},
    )


def _utterance(text, uid="utt_1"):
    return Utterance(
        utterance_id=uid,
        speaker_name="이가상",
        speaker_role="의원",
        spoken_at="2026-04-20",
        venue={"type": "assembly_plenary"},
        text=text,
        source={"kind": "assembly_minutes", "url": "https://example.invalid/minutes"},
        person_id="p1",
    )


class FakeClient:
    def __init__(self, links=None, *, refusal=False):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.response = SimpleNamespace(
            stop_reason="refusal" if refusal else "end_turn",
            model="claude-test",
            content=[
                SimpleNamespace(
                    type="text", text=json.dumps({"links": links or []})
                )
            ],
        )

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_exact_bill_title_or_number_is_linked_deterministically():
    bill = _bill()
    title_link, number_link = extract_bill_links_rules(
        [
            _utterance(f"{bill.title}을 의결하겠습니다."),
            _utterance("의안번호 2218438에 반대합니다.", uid="utt_2"),
        ],
        [bill],
    )
    assert title_link.method == number_link.method == "rule:title_match"
    assert title_link.confidence == number_link.confidence == 1.0
    assert title_link.link_id != number_link.link_id


def test_claude_link_is_only_a_review_candidate_and_refusal_is_counted():
    bill = _bill()
    utterance = _utterance("세종특별자치시 개정안의 본회의 통과를 요청합니다.")
    assert lexical_candidate_pairs([utterance], [bill]) == [(utterance, bill)]
    client = FakeClient(
        [
            {
                "utterance_id": utterance.utterance_id,
                "bill_id": bill.bill_id,
                "confidence": 0.88,
            }
        ]
    )
    links, stats = extract_bill_links_claude(
        [utterance], [bill], client=client
    )
    assert len(links) == 1
    assert links[0].method == "llm:candidate"
    assert links[0].human_reviewed is False
    assert stats["proposed"] == 1

    refused, refused_stats = extract_bill_links_claude(
        [utterance], [bill], client=FakeClient(refusal=True)
    )
    assert refused == []
    assert refused_stats["held_refusal"] == 1


def test_bill_link_review_approval_controls_usable_query(tmp_path):
    store = SqliteStore(tmp_path / "db.sqlite")
    store.save_bills([_bill()])
    store.save_utterances([_utterance("세종특별자치시 개정안을 처리합니다.")])
    link = UtteranceBillLink(
        link_id="ubl_candidate",
        utterance_id="utt_1",
        bill_id="bill_test",
        method="llm:candidate",
        confidence=0.8,
        extractor={
            "backend": "claude",
            "model": "claude-test",
            "prompt_version": "bill_link_v1",
        },
    )
    store.save_bill_links([link])
    reason = "held:bill_link_requires_review"
    review = ReviewItem(
        review_id=review_id_for("bill_link", link.link_id, reason, link.to_dict()),
        kind="bill_link",
        target_id=link.link_id,
        payload=link.to_dict(),
        reason=reason,
        status="pending",
        created_at="2026-08-16T00:00:00Z",
    )
    store.enqueue_review(review)
    assert store.load_bill_links(usable_only=True) == []

    args = Namespace(
        db=str(store.db_path),
        review_id=review.review_id,
        edit=None,
        note="발언과 의안 원문 대조",
    )
    assert cli.cmd_review_approve(args) == 0
    approved = store.load_bill_links(usable_only=True)
    assert len(approved) == 1
    assert approved[0].human_reviewed is True


def test_rule_link_cli_is_idempotent(tmp_path):
    store = SqliteStore(tmp_path / "db.sqlite")
    bill = _bill()
    store.save_bills([bill])
    store.save_utterances([_utterance(f"{bill.title}에 찬성합니다.")])
    args = Namespace(
        db=str(store.db_path),
        backend="rules",
        model="unused",
        prompt_version="unused",
        batch_size=10,
        candidate_limit=500,
    )
    assert cli.cmd_link_bills(args) == 0
    assert cli.cmd_link_bills(args) == 0
    assert len(store.load_bill_links(usable_only=True)) == 1
