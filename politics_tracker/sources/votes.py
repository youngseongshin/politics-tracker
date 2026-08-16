"""열린국회정보 의안과 국회의원 본회의 표결 정규화."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..models import Bill, Person, Vote, bill_id_for, vote_id_for


# 처리의안은 의원 발의안뿐 아니라 위원회 대안까지 포함한다. 의원 발의법률안
# ``nzmimeepazxkubdpn``은 보조 조회에만 사용한다.
DEFAULT_BILL_SERVICE_ID = "nzpltgfqabtcpsmai"
DEFAULT_VOTE_SERVICE_ID = "nojepdqqaweusdfbi"
VOTED_RESULTS = {"원안가결", "수정가결", "부결", "가결"}


@dataclass
class VoteCollection:
    bills: list[Bill]
    votes: list[Vote]
    bills_scanned: int
    bills_with_votes: int
    unmatched_people: int
    unknown_decisions: int


def _https(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme == "http":
        parts = parts._replace(scheme="https")
    return urlunsplit(parts)


def normalize_bill(row: dict[str, Any]) -> Bill:
    external_id = str(row.get("BILL_ID") or "").strip()
    bill_no = str(row.get("BILL_NO") or "").strip()
    title = str(row.get("BILL_NAME") or "").strip()
    link = str(
        row.get("LINK_URL") or row.get("DETAIL_LINK") or row.get("BILL_URL") or ""
    ).strip()
    if not external_id or not bill_no or not title or not link:
        raise ValueError("bill row requires BILL_ID, BILL_NO, BILL_NAME, and DETAIL_LINK")
    proposed_at = str(row.get("PROPOSE_DT") or "").strip() or None
    return Bill(
        bill_id=bill_id_for(external_id),
        assembly_bill_no=bill_no,
        title=title,
        proposed_at=proposed_at,
        link_url=_https(link),
        raw=dict(row),
    )


def normalize_decision(value: Any) -> str | None:
    text = "" if value is None else str(value).strip().replace(" ", "")
    aliases = {
        "찬성": "찬성",
        "반대": "반대",
        "기권": "기권",
        "불참": "불참",
        "미참여": "불참",
    }
    return aliases.get(text)


def match_vote_person(
    row: dict[str, Any], people: list[Person]
) -> tuple[str | None, str]:
    """공식 의원 코드를 우선하고, 코드가 없을 때만 고유 이름을 사용한다."""
    by_id = {person.person_id: person for person in people}
    official_id = str(row.get("MONA_CD") or "").strip()
    if official_id:
        if official_id in by_id:
            return official_id, "api:MONA_CD"
        return None, "held:unknown_MONA_CD"

    by_name: dict[str, list[Person]] = defaultdict(list)
    for person in people:
        by_name[person.name].append(person)
    name = str(row.get("HG_NM") or "").strip()
    matches = by_name.get(name, [])
    if len(matches) == 1:
        return matches[0].person_id, "name:unique"
    return None, "held:ambiguous_or_unknown_name"


def normalize_vote(row: dict[str, Any], bill: Bill, person_id: str) -> Vote:
    decision = normalize_decision(row.get("RESULT_VOTE_MOD"))
    if decision is None:
        raise ValueError(f"unknown vote decision: {row.get('RESULT_VOTE_MOD')!r}")
    raw_date = "".join(character for character in str(row.get("VOTE_DATE") or "") if character.isdigit())
    if len(raw_date) < 8:
        raise ValueError("vote row requires VOTE_DATE")
    voted_at = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
    source_url = str(row.get("BILL_URL") or row.get("BILL_NAME_URL") or bill.link_url).strip()
    return Vote(
        vote_id=vote_id_for(bill.bill_id, person_id, voted_at),
        bill_id=bill.bill_id,
        person_id=person_id,
        decision=decision,
        voted_at=voted_at,
        source={"kind": "assembly_vote_api", "url": _https(source_url)},
        raw=dict(row),
    )


def collect_votes(
    api: Any,
    people: list[Person],
    *,
    era: str,
    limit: int | None = 20,
    assembly_bill_id: str | None = None,
    year: str | None = None,
    bill_service_id: str = DEFAULT_BILL_SERVICE_ID,
    vote_service_id: str = DEFAULT_VOTE_SERVICE_ID,
) -> VoteCollection:
    """표결이 확인된 의안만 정규화한다. API 객체는 테스트에서 페이크로 주입한다."""
    bill_filters = {"AGE": era}
    if assembly_bill_id:
        bill_filters["BILL_ID"] = assembly_bill_id

    bills: list[Bill] = []
    votes: list[Vote] = []
    scanned = unmatched = unknown_decisions = 0
    for row in api.rows(bill_service_id, **bill_filters):
        scanned += 1
        if assembly_bill_id and str(row.get("BILL_ID")) != assembly_bill_id:
            continue
        process_result = row.get("PROC_RESULT_CD") or row.get("PROC_RESULT")
        if not assembly_bill_id and process_result not in VOTED_RESULTS:
            continue
        if year and not str(row.get("PROC_DT") or "").startswith(year):
            continue
        try:
            bill = normalize_bill(row)
        except ValueError:
            continue
        vote_rows = list(
            api.rows(
                vote_service_id,
                AGE=era,
                BILL_ID=str(row.get("BILL_ID")),
            )
        )
        normalized: list[Vote] = []
        for vote_row in vote_rows:
            person_id, _ = match_vote_person(vote_row, people)
            if person_id is None:
                unmatched += 1
                continue
            try:
                normalized.append(normalize_vote(vote_row, bill, person_id))
            except ValueError:
                unknown_decisions += 1
        if not normalized:
            continue
        bills.append(bill)
        votes.extend(normalized)
        if limit is not None and len(bills) >= limit:
            break

    return VoteCollection(
        bills=bills,
        votes=votes,
        bills_scanned=scanned,
        bills_with_votes=len(bills),
        unmatched_people=unmatched,
        unknown_decisions=unknown_decisions,
    )
