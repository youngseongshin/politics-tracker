"""SQLite 운영 저장소와 JSONL 교환 저장소.

운영 CLI는 ``SqliteStore``를 사용한다. ``Store``는 마이그레이션·백업·diff를 위한
JSONL 교환 포맷으로 유지한다. 두 구현은 현재 파이프라인이 쓰는 메서드 시그니처가
같다.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import Person, Utterance


class Store:
    """기존 JSONL 교환 저장소."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.people_path = self.root / "people.jsonl"
        self.utterances_path = self.root / "utterances.jsonl"

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
"""


def _json(value) -> str:
    # raw 응답의 필드 순서까지 JSONL export에서 보존한다.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


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


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
