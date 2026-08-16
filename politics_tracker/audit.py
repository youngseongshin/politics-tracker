"""정당별 수집량과 보류·검수 편향을 재현 가능한 표로 집계한다."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date

from .models import Person, ReviewItem, Stance, Utterance


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def build_balance_report(
    people: list[Person],
    utterances: list[Utterance],
    stances: list[Stance],
    reviews: list[ReviewItem],
    *,
    as_of: str,
    sample_size: int | None = None,
    sample_errors: int | None = None,
    sample_checked_at: str | None = None,
    sample_note: str | None = None,
) -> dict:
    date.fromisoformat(as_of)
    if (sample_size is None) != (sample_errors is None):
        raise ValueError("sample_size and sample_errors must be provided together")
    if sample_size is not None and (
        sample_size <= 0 or sample_errors < 0 or sample_errors > sample_size
    ):
        raise ValueError("sample errors must be between 0 and sample size")
    if sample_size is None and (sample_checked_at or sample_note):
        raise ValueError("sample metadata requires sample counts")
    if sample_checked_at:
        date.fromisoformat(sample_checked_at)

    people_by_id = {person.person_id: person for person in people}
    attributed = [utterance for utterance in utterances if utterance.person_id]
    party_people: dict[str, set[str]] = defaultdict(set)
    party_utterances: Counter[str] = Counter()
    for person in people:
        party_people[person.party or "정당 정보 없음"].add(person.person_id)
    for utterance in attributed:
        person = people_by_id.get(utterance.person_id or "")
        party_utterances[person.party if person and person.party else "정당 정보 없음"] += 1

    topic_held = sum(
        (utterance.topic_source or "").startswith("held:")
        for utterance in utterances
    )
    topic_classified = sum(bool(utterance.topics) for utterance in utterances)
    stance_held = sum(bool(stance.held_reason) for stance in stances)

    review_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for review in reviews:
        review_counts[review.kind][review.status] += 1
    review_rows = []
    for kind in sorted(review_counts):
        counts = review_counts[kind]
        decided = counts["approved"] + counts["rejected"]
        review_rows.append(
            {
                "kind": kind,
                "pending": counts["pending"],
                "approved": counts["approved"],
                "rejected": counts["rejected"],
                "approval_rate": _rate(counts["approved"], decided),
            }
        )

    return {
        "as_of": as_of,
        "corpus": {
            "people": len(people),
            "utterances": len(utterances),
            "attributed": len(attributed),
            "unmatched": len(utterances) - len(attributed),
            "matching_hold_rate": _rate(
                len(utterances) - len(attributed), len(utterances)
            ),
        },
        "parties": [
            {
                "party": party,
                "people": len(party_people[party]),
                "utterances": party_utterances[party],
            }
            for party in sorted(
                party_people,
                key=lambda name: (-party_utterances[name], name),
            )
        ],
        "topics": {
            "classified": topic_classified,
            "held": topic_held,
            "hold_rate": _rate(topic_held, len(utterances)),
        },
        "stances": {
            "records": len(stances),
            "published": len(stances) - stance_held,
            "held": stance_held,
            "hold_rate": _rate(stance_held, len(stances)),
        },
        "reviews": review_rows,
        "sample_error_audit": {
            "sample_size": sample_size,
            "errors": sample_errors,
            "error_rate": (
                _rate(sample_errors or 0, sample_size) if sample_size is not None else None
            ),
            "checked_at": sample_checked_at,
            "note": sample_note,
        },
    }
