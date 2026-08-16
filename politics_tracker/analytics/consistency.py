"""발언 입장과 본회의 표결의 판정 가능 근거 쌍 계산."""

from __future__ import annotations

from collections import defaultdict

from ..enrich.stances import StanceAxis, select_best_stances
from ..models import (
    Bill,
    ConsistencyPair,
    Stance,
    Utterance,
    UtteranceBillLink,
    Vote,
    consistency_id_for,
)


FORMULA_VERSION = "consistency_v1"


def compute_consistency_pairs(
    stances: list[Stance],
    utterances: list[Utterance],
    bills: list[Bill],
    votes: list[Vote],
    links: list[UtteranceBillLink],
    axes: list[StanceAxis],
) -> list[ConsistencyPair]:
    """계획서 T5.3의 판정 가능 조건을 모두 만족하는 쌍만 반환한다."""
    utterance_by_id = {utterance.utterance_id: utterance for utterance in utterances}
    bill_ids = {bill.bill_id for bill in bills}
    axes_by_key = {axis.key: axis for axis in axes}
    bills_by_utterance: dict[str, set[str]] = defaultdict(set)
    for link in links:
        if link.method == "rule:title_match" or link.human_reviewed:
            bills_by_utterance[link.utterance_id].add(link.bill_id)
    votes_by_bill_person: dict[tuple[str, str], list[Vote]] = defaultdict(list)
    for vote in votes:
        if vote.bill_id in bill_ids:
            votes_by_bill_person[(vote.bill_id, vote.person_id)].append(vote)

    pairs = []
    for stance in select_best_stances(stances, utterances):
        if abs(stance.value) < 0.3:
            continue
        if not stance.human_reviewed and stance.confidence < 0.85:
            continue
        utterance = utterance_by_id[stance.utterance_id]
        axis = axes_by_key.get(stance.axis)
        if axis is None:
            continue
        for bill_id in sorted(bills_by_utterance.get(stance.utterance_id, set())):
            for vote in votes_by_bill_person.get((bill_id, stance.person_id), []):
                # 표결 시각을 날짜까지만 공개하므로 같은 날 발언은 순서를 추정하지 않는다.
                if utterance.spoken_at >= vote.voted_at:
                    continue
                expected = axis.bill_direction[
                    "positive" if stance.value > 0 else "negative"
                ]
                pairs.append(
                    ConsistencyPair(
                        consistency_id=consistency_id_for(
                            stance.stance_id, vote.vote_id, FORMULA_VERSION
                        ),
                        person_id=stance.person_id,
                        bill_id=bill_id,
                        utterance_id=stance.utterance_id,
                        stance_id=stance.stance_id,
                        vote_id=vote.vote_id,
                        axis=stance.axis,
                        stance_value=stance.value,
                        expected_decision=expected,
                        vote_decision=vote.decision,
                        consistent=vote.decision == expected,
                    )
                )
    unique = {pair.consistency_id: pair for pair in pairs}
    return sorted(
        unique.values(),
        key=lambda pair: (
            pair.person_id,
            pair.bill_id,
            pair.utterance_id,
            pair.stance_id,
        ),
    )


def consistency_summaries(
    pairs: list[ConsistencyPair],
) -> dict[str, dict[str, int]]:
    summaries: dict[str, dict[str, int]] = defaultdict(
        lambda: {"consistent": 0, "eligible": 0}
    )
    for pair in pairs:
        summaries[pair.person_id]["eligible"] += 1
        summaries[pair.person_id]["consistent"] += int(pair.consistent)
    return dict(summaries)
