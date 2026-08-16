"""SQLite 운영 저장소와 JSONL 교환 저장소.

운영 CLI는 ``SqliteStore``를 사용한다. ``Store``는 마이그레이션·백업·diff를 위한
JSONL 교환 포맷으로 유지한다. 두 구현은 현재 파이프라인이 쓰는 메서드 시그니처가
같다.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import (
    Bill,
    ConsistencyPair,
    Person,
    Pledge,
    ReviewItem,
    Stance,
    Utterance,
    UtteranceBillLink,
    Vote,
)


class Store:
    """기존 JSONL 교환 저장소."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.people_path = self.root / "people.jsonl"
        self.utterances_path = self.root / "utterances.jsonl"
        self.reviews_path = self.root / "reviews.jsonl"
        self.stances_path = self.root / "stances.jsonl"
        self.bills_path = self.root / "bills.jsonl"
        self.votes_path = self.root / "votes.jsonl"
        self.bill_links_path = self.root / "utterance_bill_links.jsonl"
        self.consistency_pairs_path = self.root / "consistency_pairs.jsonl"
        self.pledges_path = self.root / "pledges.jsonl"

    # -- people ---------------------------------------------------------
    def save_people(self, people: list[Person]) -> None:
        _write_jsonl(self.people_path, [p.to_dict() for p in people])

    def load_people(self) -> list[Person]:
        return [Person.from_dict(d) for d in _read_jsonl(self.people_path)]

    # -- utterances -----------------------------------------------------
    def save_utterances(self, utterances: list[Utterance]) -> None:
        _write_jsonl(self.utterances_path, [u.to_dict() for u in utterances])

    def load_utterances(self) -> list[Utterance]:
        return [Utterance.from_dict(d) for d in _read_jsonl(self.utterances_path)]

    def upsert_utterances(self, new: list[Utterance]) -> int:
        """utterance_id 기준 병합. 재수집 시 중복 없이 갱신된다. 추가된 건수를 반환."""
        existing = {u.utterance_id: u for u in self.load_utterances()}
        added = sum(1 for u in new if u.utterance_id not in existing)
        existing.update({u.utterance_id: u for u in new})
        merged = sorted(existing.values(), key=lambda u: (u.spoken_at, u.source.get("url", ""), u.order))
        self.save_utterances(merged)
        return added

    # -- reviews --------------------------------------------------------
    def save_reviews(self, reviews: list[ReviewItem]) -> None:
        _write_jsonl(self.reviews_path, [review.to_dict() for review in reviews])

    def load_reviews(self) -> list[ReviewItem]:
        return [ReviewItem.from_dict(data) for data in _read_jsonl(self.reviews_path)]

    # -- stances --------------------------------------------------------
    def save_stances(self, stances: list[Stance]) -> None:
        _write_jsonl(self.stances_path, [stance.to_dict() for stance in stances])

    def load_stances(self) -> list[Stance]:
        return [Stance.from_dict(data) for data in _read_jsonl(self.stances_path)]

    # -- bills and votes ------------------------------------------------
    def save_bills(self, bills: list[Bill]) -> None:
        _write_jsonl(self.bills_path, [bill.to_dict() for bill in bills])

    def load_bills(self) -> list[Bill]:
        return [Bill.from_dict(data) for data in _read_jsonl(self.bills_path)]

    def save_votes(self, votes: list[Vote]) -> None:
        _write_jsonl(self.votes_path, [vote.to_dict() for vote in votes])

    def load_votes(self) -> list[Vote]:
        return [Vote.from_dict(data) for data in _read_jsonl(self.votes_path)]

    def save_bill_links(self, links: list[UtteranceBillLink]) -> None:
        _write_jsonl(self.bill_links_path, [link.to_dict() for link in links])

    def load_bill_links(self) -> list[UtteranceBillLink]:
        return [
            UtteranceBillLink.from_dict(data)
            for data in _read_jsonl(self.bill_links_path)
        ]

    def save_consistency_pairs(self, pairs: list[ConsistencyPair]) -> None:
        _write_jsonl(
            self.consistency_pairs_path, [pair.to_dict() for pair in pairs]
        )

    def load_consistency_pairs(self) -> list[ConsistencyPair]:
        return [
            ConsistencyPair.from_dict(data)
            for data in _read_jsonl(self.consistency_pairs_path)
        ]

    # -- pledges --------------------------------------------------------
    def save_pledges(self, pledges: list[Pledge]) -> None:
        existing = {pledge.pledge_id: pledge for pledge in self.load_pledges()}
        incoming = {pledge.pledge_id: pledge for pledge in pledges}
        if len(incoming) != len(pledges):
            raise ValueError("Duplicate pledge_id in pledge collection")
        missing = sorted(set(existing) - set(incoming))
        if missing:
            raise ValueError(f"Pledge records cannot be removed: {', '.join(missing)}")
        for pledge_id, old in existing.items():
            _validate_pledge_update(old, incoming[pledge_id])
        _write_jsonl(self.pledges_path, [pledge.to_dict() for pledge in pledges])

    def load_pledges(self) -> list[Pledge]:
        return [Pledge.from_dict(data) for data in _read_jsonl(self.pledges_path)]

    def upsert_pledges(self, pledges: list[Pledge]) -> int:
        if len({pledge.pledge_id for pledge in pledges}) != len(pledges):
            raise ValueError("Duplicate pledge_id in pledge collection")
        existing = {pledge.pledge_id: pledge for pledge in self.load_pledges()}
        added = sum(pledge.pledge_id not in existing for pledge in pledges)
        for pledge in pledges:
            old = existing.get(pledge.pledge_id)
            if old:
                _validate_pledge_update(old, pledge)
            existing[pledge.pledge_id] = pledge
        self.save_pledges(list(existing.values()))
        return added


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    person_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    list_order INTEGER NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json))
);

CREATE TABLE IF NOT EXISTS utterances (
    utterance_id TEXT PRIMARY KEY,
    person_id TEXT,
    spoken_at TEXT NOT NULL,
    source_url TEXT NOT NULL,
    speech_order INTEGER NOT NULL,
    list_order INTEGER NOT NULL,
    topics_json TEXT NOT NULL CHECK (json_valid(topics_json)),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json))
);

CREATE INDEX IF NOT EXISTS idx_utterances_person_spoken_at
    ON utterances(person_id, spoken_at);
CREATE INDEX IF NOT EXISTS idx_utterances_spoken_at
    ON utterances(spoken_at);

CREATE TABLE IF NOT EXISTS stances (
    stance_id TEXT PRIMARY KEY,
    utterance_id TEXT NOT NULL,
    person_id TEXT NOT NULL,
    axis TEXT NOT NULL,
    value REAL NOT NULL CHECK (value >= -1 AND value <= 1),
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    human_reviewed INTEGER NOT NULL CHECK (human_reviewed IN (0, 1)),
    held_reason TEXT,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json))
);

CREATE INDEX IF NOT EXISTS idx_stances_person_axis
    ON stances(person_id, axis);
CREATE INDEX IF NOT EXISTS idx_stances_utterance
    ON stances(utterance_id);

CREATE TABLE IF NOT EXISTS bills (
    bill_id TEXT PRIMARY KEY,
    assembly_bill_no TEXT NOT NULL,
    proposed_at TEXT,
    list_order INTEGER NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_bills_assembly_bill_no
    ON bills(assembly_bill_no);

CREATE TABLE IF NOT EXISTS votes (
    vote_id TEXT PRIMARY KEY,
    bill_id TEXT NOT NULL,
    person_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('찬성', '반대', '기권', '불참')),
    voted_at TEXT NOT NULL,
    source_url TEXT NOT NULL,
    list_order INTEGER NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json))
);

CREATE INDEX IF NOT EXISTS idx_votes_bill ON votes(bill_id);
CREATE INDEX IF NOT EXISTS idx_votes_person_voted_at ON votes(person_id, voted_at);

CREATE TABLE IF NOT EXISTS utterance_bill_links (
    link_id TEXT PRIMARY KEY,
    utterance_id TEXT NOT NULL,
    bill_id TEXT NOT NULL,
    method TEXT NOT NULL CHECK (method IN ('rule:title_match', 'llm:candidate')),
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    human_reviewed INTEGER NOT NULL CHECK (human_reviewed IN (0, 1)),
    list_order INTEGER NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json))
);

CREATE INDEX IF NOT EXISTS idx_bill_links_utterance ON utterance_bill_links(utterance_id);
CREATE INDEX IF NOT EXISTS idx_bill_links_bill ON utterance_bill_links(bill_id);

CREATE TABLE IF NOT EXISTS consistency_pairs (
    consistency_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL,
    bill_id TEXT NOT NULL,
    utterance_id TEXT NOT NULL,
    consistent INTEGER NOT NULL CHECK (consistent IN (0, 1)),
    list_order INTEGER NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json))
);

CREATE INDEX IF NOT EXISTS idx_consistency_person ON consistency_pairs(person_id);
CREATE INDEX IF NOT EXISTS idx_consistency_bill ON consistency_pairs(bill_id);

CREATE TABLE IF NOT EXISTS pledges (
    pledge_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL,
    current_status TEXT NOT NULL
        CHECK (current_status IN ('이행', '부분 이행', '미이행', '검증 불가')),
    list_order INTEGER NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json))
);

CREATE INDEX IF NOT EXISTS idx_pledges_person ON pledges(person_id);
CREATE INDEX IF NOT EXISTS idx_pledges_status ON pledges(current_status);

CREATE TABLE IF NOT EXISTS reviews (
    review_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at TEXT NOT NULL,
    decided_at TEXT,
    note TEXT,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json))
);

CREATE INDEX IF NOT EXISTS idx_reviews_status_kind_created
    ON reviews(status, kind, created_at);
"""


def _json(value) -> str:
    # raw 응답의 필드 순서까지 JSONL export에서 보존한다.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _validate_pledge_update(old: Pledge, new: Pledge) -> None:
    immutable_old = (old.person_id, old.text, old.source, old.criteria)
    immutable_new = (new.person_id, new.text, new.source, new.criteria)
    if immutable_old != immutable_new:
        raise ValueError("Registered pledge text, source, and criteria are immutable")
    if new.status_history[: len(old.status_history)] != old.status_history:
        raise ValueError("Pledge status history is append-only")


class SqliteStore:
    """현재 운영 저장소. JSON payload를 보존하면서 조회 키를 별도 색인한다."""

    def __init__(self, path: str | Path) -> None:
        self.db_path = Path(path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.executescript(_SQLITE_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # -- people ---------------------------------------------------------
    def save_people(self, people: list[Person]) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute("DELETE FROM people")
                conn.executemany(
                    """
                    INSERT INTO people(person_id, name, list_order, payload_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        (person.person_id, person.name, index, _json(person.to_dict()))
                        for index, person in enumerate(people)
                    ],
                )
        finally:
            conn.close()

    def load_people(self) -> list[Person]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT payload_json FROM people ORDER BY list_order"
            ).fetchall()
            return [Person.from_dict(json.loads(row["payload_json"])) for row in rows]
        finally:
            conn.close()

    # -- utterances -----------------------------------------------------
    def save_utterances(self, utterances: list[Utterance]) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute("DELETE FROM utterances")
                conn.executemany(
                    """
                    INSERT INTO utterances(
                        utterance_id, person_id, spoken_at, source_url, speech_order,
                        list_order, topics_json, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            utterance.utterance_id,
                            utterance.person_id,
                            utterance.spoken_at,
                            utterance.source["url"],
                            utterance.order,
                            index,
                            _json(utterance.topics),
                            _json(utterance.to_dict()),
                        )
                        for index, utterance in enumerate(utterances)
                    ],
                )
        finally:
            conn.close()

    def load_utterances(self) -> list[Utterance]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT topics_json, payload_json FROM utterances ORDER BY list_order"
            ).fetchall()
            utterances: list[Utterance] = []
            for row in rows:
                payload = json.loads(row["payload_json"])
                payload["topics"] = json.loads(row["topics_json"])
                utterances.append(Utterance.from_dict(payload))
            return utterances
        finally:
            conn.close()

    def upsert_utterances(self, new: list[Utterance]) -> int:
        """기존 payload 전체를 읽지 않고 ID 충돌만 SQLite에서 갱신한다."""
        ordered = sorted(
            new,
            key=lambda utterance: (
                utterance.spoken_at,
                utterance.source.get("url", ""),
                utterance.order,
            ),
        )
        conn = self._connect()
        try:
            with conn:
                before = conn.execute("SELECT COUNT(*) FROM utterances").fetchone()[0]
                max_order = conn.execute(
                    "SELECT COALESCE(MAX(list_order), -1) FROM utterances"
                ).fetchone()[0]
                conn.executemany(
                    """
                    INSERT INTO utterances(
                        utterance_id, person_id, spoken_at, source_url, speech_order,
                        list_order, topics_json, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(utterance_id) DO UPDATE SET
                        person_id = excluded.person_id,
                        spoken_at = excluded.spoken_at,
                        source_url = excluded.source_url,
                        speech_order = excluded.speech_order,
                        topics_json = excluded.topics_json,
                        payload_json = excluded.payload_json
                    """,
                    [
                        (
                            utterance.utterance_id,
                            utterance.person_id,
                            utterance.spoken_at,
                            utterance.source["url"],
                            utterance.order,
                            max_order + index + 1,
                            _json(utterance.topics),
                            _json(utterance.to_dict()),
                        )
                        for index, utterance in enumerate(ordered)
                    ],
                )
                after = conn.execute("SELECT COUNT(*) FROM utterances").fetchone()[0]
            return after - before
        finally:
            conn.close()

    # -- stances --------------------------------------------------------
    @staticmethod
    def _stance_values(stance: Stance) -> tuple:
        return (
            stance.stance_id,
            stance.utterance_id,
            stance.person_id,
            stance.axis,
            stance.value,
            stance.confidence,
            int(stance.human_reviewed),
            stance.held_reason,
            _json(stance.to_dict()),
        )

    def save_stances(self, stances: list[Stance]) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute("DELETE FROM stances")
                conn.executemany(
                    """
                    INSERT INTO stances(
                        stance_id, utterance_id, person_id, axis, value, confidence,
                        human_reviewed, held_reason, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [self._stance_values(stance) for stance in stances],
                )
        finally:
            conn.close()

    def load_stances(
        self,
        *,
        person_id: str | None = None,
        published_only: bool = False,
    ) -> list[Stance]:
        where: list[str] = []
        values: list[str] = []
        if person_id:
            where.append("person_id = ?")
            values.append(person_id)
        if published_only:
            where.append("held_reason IS NULL")
        sql = "SELECT payload_json FROM stances"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY person_id, axis, stance_id"
        conn = self._connect()
        try:
            rows = conn.execute(sql, values).fetchall()
            return [Stance.from_dict(json.loads(row["payload_json"])) for row in rows]
        finally:
            conn.close()

    def upsert_stances(self, stances: list[Stance]) -> int:
        conn = self._connect()
        try:
            with conn:
                before = conn.execute("SELECT COUNT(*) FROM stances").fetchone()[0]
                conn.executemany(
                    """
                    INSERT INTO stances(
                        stance_id, utterance_id, person_id, axis, value, confidence,
                        human_reviewed, held_reason, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(stance_id) DO UPDATE SET
                        utterance_id = excluded.utterance_id,
                        person_id = excluded.person_id,
                        axis = excluded.axis,
                        value = excluded.value,
                        confidence = excluded.confidence,
                        human_reviewed = excluded.human_reviewed,
                        held_reason = excluded.held_reason,
                        payload_json = excluded.payload_json
                    WHERE stances.human_reviewed = 0
                    """,
                    [self._stance_values(stance) for stance in stances],
                )
                after = conn.execute("SELECT COUNT(*) FROM stances").fetchone()[0]
            return after - before
        finally:
            conn.close()

    def sync_unreviewed_stances(
        self, stances: list[Stance], *, backend: str
    ) -> tuple[int, int]:
        """결정적 추출기의 공개 결과를 현재 규칙 버전과 정확히 맞춘다.

        사람이 승인한 레코드와 검수 대기 중인 held 레코드는 보존한다. 공개 가능한
        자동 결과만 교체하므로 규칙 버전이 바뀌어도 과거 자동 판정이 사이트에 남지
        않는다. 반환값은 ``(신규 ID 수, 제거한 구버전 수)``다.
        """
        if any(stance.extractor.get("backend") != backend for stance in stances):
            raise ValueError("all synced stances must use the requested backend")
        desired = {
            stance.stance_id
            for stance in stances
            if not stance.human_reviewed and stance.held_reason is None
        }
        conn = self._connect()
        try:
            with conn:
                all_existing = {
                    row[0] for row in conn.execute("SELECT stance_id FROM stances")
                }
                existing_public = {
                    row[0]
                    for row in conn.execute(
                        """
                        SELECT stance_id FROM stances
                        WHERE human_reviewed = 0 AND held_reason IS NULL
                          AND json_extract(payload_json, '$.extractor.backend') = ?
                        """,
                        (backend,),
                    )
                }
                conn.execute(
                    """
                    DELETE FROM stances
                    WHERE human_reviewed = 0 AND held_reason IS NULL
                      AND json_extract(payload_json, '$.extractor.backend') = ?
                    """,
                    (backend,),
                )
                conn.executemany(
                    """
                    INSERT INTO stances(
                        stance_id, utterance_id, person_id, axis, value, confidence,
                        human_reviewed, held_reason, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(stance_id) DO UPDATE SET
                        utterance_id = excluded.utterance_id,
                        person_id = excluded.person_id,
                        axis = excluded.axis,
                        value = excluded.value,
                        confidence = excluded.confidence,
                        human_reviewed = excluded.human_reviewed,
                        held_reason = excluded.held_reason,
                        payload_json = excluded.payload_json
                    WHERE stances.human_reviewed = 0
                    """,
                    [self._stance_values(stance) for stance in stances],
                )
            return len(desired - all_existing), len(existing_public - desired)
        finally:
            conn.close()

    # -- bills ----------------------------------------------------------
    @staticmethod
    def _bill_values(bill: Bill, list_order: int) -> tuple:
        return (
            bill.bill_id,
            bill.assembly_bill_no,
            bill.proposed_at,
            list_order,
            _json(bill.to_dict()),
        )

    def save_bills(self, bills: list[Bill]) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute("DELETE FROM bills")
                conn.executemany(
                    """
                    INSERT INTO bills(
                        bill_id, assembly_bill_no, proposed_at, list_order, payload_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [self._bill_values(bill, index) for index, bill in enumerate(bills)],
                )
        finally:
            conn.close()

    def load_bills(self) -> list[Bill]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT payload_json FROM bills ORDER BY list_order, bill_id"
            ).fetchall()
            return [Bill.from_dict(json.loads(row["payload_json"])) for row in rows]
        finally:
            conn.close()

    def upsert_bills(self, bills: list[Bill]) -> int:
        conn = self._connect()
        try:
            with conn:
                before = conn.execute("SELECT COUNT(*) FROM bills").fetchone()[0]
                max_order = conn.execute(
                    "SELECT COALESCE(MAX(list_order), -1) FROM bills"
                ).fetchone()[0]
                conn.executemany(
                    """
                    INSERT INTO bills(
                        bill_id, assembly_bill_no, proposed_at, list_order, payload_json
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(bill_id) DO UPDATE SET
                        assembly_bill_no = excluded.assembly_bill_no,
                        proposed_at = excluded.proposed_at,
                        payload_json = excluded.payload_json
                    """,
                    [
                        self._bill_values(bill, max_order + index + 1)
                        for index, bill in enumerate(bills)
                    ],
                )
                after = conn.execute("SELECT COUNT(*) FROM bills").fetchone()[0]
            return after - before
        finally:
            conn.close()

    # -- votes ----------------------------------------------------------
    @staticmethod
    def _vote_values(vote: Vote, list_order: int) -> tuple:
        return (
            vote.vote_id,
            vote.bill_id,
            vote.person_id,
            vote.decision,
            vote.voted_at,
            vote.source["url"],
            list_order,
            _json(vote.to_dict()),
        )

    def save_votes(self, votes: list[Vote]) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute("DELETE FROM votes")
                conn.executemany(
                    """
                    INSERT INTO votes(
                        vote_id, bill_id, person_id, decision, voted_at, source_url,
                        list_order, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [self._vote_values(vote, index) for index, vote in enumerate(votes)],
                )
        finally:
            conn.close()

    def load_votes(
        self, *, person_id: str | None = None, bill_id: str | None = None
    ) -> list[Vote]:
        where: list[str] = []
        values: list[str] = []
        if person_id:
            where.append("person_id = ?")
            values.append(person_id)
        if bill_id:
            where.append("bill_id = ?")
            values.append(bill_id)
        sql = "SELECT payload_json FROM votes"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY list_order, vote_id"
        conn = self._connect()
        try:
            rows = conn.execute(sql, values).fetchall()
            return [Vote.from_dict(json.loads(row["payload_json"])) for row in rows]
        finally:
            conn.close()

    def upsert_votes(self, votes: list[Vote]) -> int:
        conn = self._connect()
        try:
            with conn:
                before = conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0]
                max_order = conn.execute(
                    "SELECT COALESCE(MAX(list_order), -1) FROM votes"
                ).fetchone()[0]
                conn.executemany(
                    """
                    INSERT INTO votes(
                        vote_id, bill_id, person_id, decision, voted_at, source_url,
                        list_order, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(vote_id) DO UPDATE SET
                        bill_id = excluded.bill_id,
                        person_id = excluded.person_id,
                        decision = excluded.decision,
                        voted_at = excluded.voted_at,
                        source_url = excluded.source_url,
                        payload_json = excluded.payload_json
                    """,
                    [
                        self._vote_values(vote, max_order + index + 1)
                        for index, vote in enumerate(votes)
                    ],
                )
                after = conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0]
            return after - before
        finally:
            conn.close()

    # -- utterance-bill links ------------------------------------------
    @staticmethod
    def _bill_link_values(link: UtteranceBillLink, list_order: int) -> tuple:
        return (
            link.link_id,
            link.utterance_id,
            link.bill_id,
            link.method,
            link.confidence,
            int(link.human_reviewed),
            list_order,
            _json(link.to_dict()),
        )

    def save_bill_links(self, links: list[UtteranceBillLink]) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute("DELETE FROM utterance_bill_links")
                conn.executemany(
                    """
                    INSERT INTO utterance_bill_links(
                        link_id, utterance_id, bill_id, method, confidence,
                        human_reviewed, list_order, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        self._bill_link_values(link, index)
                        for index, link in enumerate(links)
                    ],
                )
        finally:
            conn.close()

    def load_bill_links(
        self,
        *,
        utterance_id: str | None = None,
        bill_id: str | None = None,
        usable_only: bool = False,
    ) -> list[UtteranceBillLink]:
        where: list[str] = []
        values: list[str] = []
        if utterance_id:
            where.append("utterance_id = ?")
            values.append(utterance_id)
        if bill_id:
            where.append("bill_id = ?")
            values.append(bill_id)
        if usable_only:
            where.append("(method = 'rule:title_match' OR human_reviewed = 1)")
        sql = "SELECT payload_json FROM utterance_bill_links"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY list_order, link_id"
        conn = self._connect()
        try:
            rows = conn.execute(sql, values).fetchall()
            return [
                UtteranceBillLink.from_dict(json.loads(row["payload_json"]))
                for row in rows
            ]
        finally:
            conn.close()

    def upsert_bill_links(self, links: list[UtteranceBillLink]) -> int:
        conn = self._connect()
        try:
            with conn:
                before = conn.execute(
                    "SELECT COUNT(*) FROM utterance_bill_links"
                ).fetchone()[0]
                max_order = conn.execute(
                    "SELECT COALESCE(MAX(list_order), -1) FROM utterance_bill_links"
                ).fetchone()[0]
                conn.executemany(
                    """
                    INSERT INTO utterance_bill_links(
                        link_id, utterance_id, bill_id, method, confidence,
                        human_reviewed, list_order, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(link_id) DO UPDATE SET
                        utterance_id = excluded.utterance_id,
                        bill_id = excluded.bill_id,
                        method = excluded.method,
                        confidence = excluded.confidence,
                        human_reviewed = excluded.human_reviewed,
                        payload_json = excluded.payload_json
                    WHERE utterance_bill_links.human_reviewed = 0
                    """,
                    [
                        self._bill_link_values(link, max_order + index + 1)
                        for index, link in enumerate(links)
                    ],
                )
                after = conn.execute(
                    "SELECT COUNT(*) FROM utterance_bill_links"
                ).fetchone()[0]
            return after - before
        finally:
            conn.close()

    # -- consistency pairs ---------------------------------------------
    def save_consistency_pairs(self, pairs: list[ConsistencyPair]) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute("DELETE FROM consistency_pairs")
                conn.executemany(
                    """
                    INSERT INTO consistency_pairs(
                        consistency_id, person_id, bill_id, utterance_id,
                        consistent, list_order, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            pair.consistency_id,
                            pair.person_id,
                            pair.bill_id,
                            pair.utterance_id,
                            int(pair.consistent),
                            index,
                            _json(pair.to_dict()),
                        )
                        for index, pair in enumerate(pairs)
                    ],
                )
        finally:
            conn.close()

    def load_consistency_pairs(
        self, *, person_id: str | None = None
    ) -> list[ConsistencyPair]:
        sql = "SELECT payload_json FROM consistency_pairs"
        values: tuple[str, ...] = ()
        if person_id:
            sql += " WHERE person_id = ?"
            values = (person_id,)
        sql += " ORDER BY list_order, consistency_id"
        conn = self._connect()
        try:
            rows = conn.execute(sql, values).fetchall()
            return [
                ConsistencyPair.from_dict(json.loads(row["payload_json"]))
                for row in rows
            ]
        finally:
            conn.close()

    # -- pledges --------------------------------------------------------
    @staticmethod
    def _pledge_values(pledge: Pledge, list_order: int) -> tuple:
        return (
            pledge.pledge_id,
            pledge.person_id,
            pledge.current_status,
            list_order,
            _json(pledge.to_dict()),
        )

    def save_pledges(self, pledges: list[Pledge]) -> None:
        existing = {pledge.pledge_id: pledge for pledge in self.load_pledges()}
        incoming = {pledge.pledge_id: pledge for pledge in pledges}
        if len(incoming) != len(pledges):
            raise ValueError("Duplicate pledge_id in pledge collection")
        missing = sorted(set(existing) - set(incoming))
        if missing:
            raise ValueError(f"Pledge records cannot be removed: {', '.join(missing)}")
        for pledge_id, old in existing.items():
            _validate_pledge_update(old, incoming[pledge_id])

        conn = self._connect()
        try:
            with conn:
                conn.execute("DELETE FROM pledges")
                conn.executemany(
                    """
                    INSERT INTO pledges(
                        pledge_id, person_id, current_status, list_order, payload_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        self._pledge_values(pledge, index)
                        for index, pledge in enumerate(pledges)
                    ],
                )
        finally:
            conn.close()

    def load_pledges(self, *, person_id: str | None = None) -> list[Pledge]:
        sql = "SELECT payload_json FROM pledges"
        values: tuple[str, ...] = ()
        if person_id:
            sql += " WHERE person_id = ?"
            values = (person_id,)
        sql += " ORDER BY list_order, pledge_id"
        conn = self._connect()
        try:
            rows = conn.execute(sql, values).fetchall()
            return [Pledge.from_dict(json.loads(row["payload_json"])) for row in rows]
        finally:
            conn.close()

    def get_pledge(self, pledge_id: str) -> Pledge | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT payload_json FROM pledges WHERE pledge_id = ?", (pledge_id,)
            ).fetchone()
            return Pledge.from_dict(json.loads(row["payload_json"])) if row else None
        finally:
            conn.close()

    def upsert_pledges(self, pledges: list[Pledge]) -> int:
        if len({pledge.pledge_id for pledge in pledges}) != len(pledges):
            raise ValueError("Duplicate pledge_id in pledge collection")
        conn = self._connect()
        try:
            existing_rows = conn.execute(
                "SELECT pledge_id, payload_json FROM pledges"
            ).fetchall()
            existing = {
                row["pledge_id"]: Pledge.from_dict(json.loads(row["payload_json"]))
                for row in existing_rows
            }
            known = dict(existing)
            for pledge in pledges:
                old = known.get(pledge.pledge_id)
                if old:
                    _validate_pledge_update(old, pledge)
                known[pledge.pledge_id] = pledge

            with conn:
                before = len(existing)
                max_order = conn.execute(
                    "SELECT COALESCE(MAX(list_order), -1) FROM pledges"
                ).fetchone()[0]
                next_order = max_order + 1
                for pledge in pledges:
                    old = existing.get(pledge.pledge_id)
                    list_order = (
                        conn.execute(
                            "SELECT list_order FROM pledges WHERE pledge_id = ?",
                            (pledge.pledge_id,),
                        ).fetchone()[0]
                        if old
                        else next_order
                    )
                    if not old:
                        next_order += 1
                    conn.execute(
                        """
                        INSERT INTO pledges(
                            pledge_id, person_id, current_status, list_order, payload_json
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(pledge_id) DO UPDATE SET
                            current_status = excluded.current_status,
                            payload_json = excluded.payload_json
                        """,
                        self._pledge_values(pledge, list_order),
                    )
                after = conn.execute("SELECT COUNT(*) FROM pledges").fetchone()[0]
            return after - before
        finally:
            conn.close()

    # -- reviews --------------------------------------------------------
    @staticmethod
    def _review_values(review: ReviewItem) -> tuple:
        return (
            review.review_id,
            review.kind,
            review.target_id,
            review.reason,
            review.status,
            review.created_at,
            review.decided_at,
            review.note,
            _json(review.payload),
        )

    def save_reviews(self, reviews: list[ReviewItem]) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute("DELETE FROM reviews")
                conn.executemany(
                    """
                    INSERT INTO reviews(
                        review_id, kind, target_id, reason, status, created_at,
                        decided_at, note, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [self._review_values(review) for review in reviews],
                )
        finally:
            conn.close()

    def load_reviews(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
    ) -> list[ReviewItem]:
        where: list[str] = []
        values: list[str] = []
        if kind:
            where.append("kind = ?")
            values.append(kind)
        if status:
            where.append("status = ?")
            values.append(status)
        sql = "SELECT * FROM reviews"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at, review_id"
        conn = self._connect()
        try:
            rows = conn.execute(sql, values).fetchall()
            return [
                ReviewItem(
                    review_id=row["review_id"],
                    kind=row["kind"],
                    target_id=row["target_id"],
                    payload=json.loads(row["payload_json"]),
                    reason=row["reason"],
                    status=row["status"],
                    created_at=row["created_at"],
                    decided_at=row["decided_at"],
                    note=row["note"],
                )
                for row in rows
            ]
        finally:
            conn.close()

    def get_review(self, review_id: str) -> ReviewItem | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM reviews WHERE review_id = ?", (review_id,)
            ).fetchone()
            if row is None:
                return None
            return ReviewItem(
                review_id=row["review_id"],
                kind=row["kind"],
                target_id=row["target_id"],
                payload=json.loads(row["payload_json"]),
                reason=row["reason"],
                status=row["status"],
                created_at=row["created_at"],
                decided_at=row["decided_at"],
                note=row["note"],
            )
        finally:
            conn.close()

    def enqueue_review(self, review: ReviewItem) -> bool:
        """결정적 ID가 이미 있으면 기존 생성·판정 이력을 보존한다."""
        conn = self._connect()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO reviews(
                        review_id, kind, target_id, reason, status, created_at,
                        decided_at, note, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._review_values(review),
                )
            return cursor.rowcount == 1
        finally:
            conn.close()

    def decide_review(
        self,
        review_id: str,
        *,
        status: str,
        decided_at: str,
        note: str | None = None,
        payload: dict | None = None,
    ) -> ReviewItem:
        if status not in {"approved", "rejected"}:
            raise ValueError("review decision must be approved or rejected")
        conn = self._connect()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    UPDATE reviews
                    SET status = ?, decided_at = ?, note = ?,
                        payload_json = COALESCE(?, payload_json)
                    WHERE review_id = ? AND status = 'pending'
                    """,
                    (
                        status,
                        decided_at,
                        note,
                        _json(payload) if payload is not None else None,
                        review_id,
                    ),
                )
                if cursor.rowcount != 1:
                    current = conn.execute(
                        "SELECT status FROM reviews WHERE review_id = ?", (review_id,)
                    ).fetchone()
                    if current is None:
                        raise KeyError(f"review not found: {review_id}")
                    raise ValueError(
                        f"review decision is immutable: {review_id} is {current['status']}"
                    )
        finally:
            conn.close()
        decided = self.get_review(review_id)
        assert decided is not None
        return decided


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
