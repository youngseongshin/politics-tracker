import pytest
import sqlite3

from politics_tracker import cli
from politics_tracker.matching import match_utterances
from politics_tracker.models import Person, Utterance
from politics_tracker.storage import SqliteStore, Store


def make_utterance(uid: str, speaker: str) -> Utterance:
    return Utterance(
        utterance_id=uid,
        speaker_name=speaker,
        speaker_role="의원",
        spoken_at="2026-07-15",
        venue={"type": "assembly_plenary", "session": "가상"},
        text="발언",
        source={"kind": "assembly_minutes", "url": "https://example.invalid/m/1"},
    )


def test_source_url_is_required():
    with pytest.raises(ValueError):
        Utterance(
            utterance_id="u1",
            speaker_name="이가상",
            speaker_role=None,
            spoken_at="2026-07-15",
            venue={},
            text="출처 없는 발언",
            source={},
        )


def test_match_unique_ambiguous_unmatched():
    people = [
        Person(person_id="p1", name="이가상"),
        Person(person_id="p2", name="김중복"),
        Person(person_id="p3", name="김중복"),
    ]
    utts = [make_utterance("u1", "이가상"), make_utterance("u2", "김중복"), make_utterance("u3", "장외인")]
    stats = match_utterances(utts, people)

    assert (stats.matched, stats.ambiguous, stats.unmatched) == (1, 1, 1)
    assert utts[0].person_id == "p1"
    assert utts[1].person_id is None  # 동명이인은 확정하지 않는다
    assert utts[2].person_id is None


def test_store_roundtrip_and_upsert(tmp_path):
    store = Store(tmp_path)
    store.save_people([Person(person_id="p1", name="이가상", party="가상당")])
    store.save_utterances([make_utterance("u1", "이가상")])

    assert store.load_people()[0].party == "가상당"
    assert store.load_utterances()[0].utterance_id == "u1"

    added = store.upsert_utterances([make_utterance("u1", "이가상"), make_utterance("u2", "박사례")])
    assert added == 1
    assert {u.utterance_id for u in store.load_utterances()} == {"u1", "u2"}


def test_sqlite_store_roundtrip_upsert_and_indexes(tmp_path):
    db_path = tmp_path / "db.sqlite"
    store = SqliteStore(db_path)
    people = [Person(person_id="p1", name="이가상", party="가상당")]
    first = make_utterance("u1", "이가상")
    first.person_id = "p1"
    first.topics = ["housing"]
    first.topic_source = "rules"
    store.save_people(people)
    store.save_utterances([first])

    assert store.load_people() == people
    assert store.load_utterances() == [first]
    assert store.upsert_utterances([first, make_utterance("u2", "박사례")]) == 1
    assert {utterance.utterance_id for utterance in store.load_utterances()} == {"u1", "u2"}
    updated = make_utterance("u1", "이가상")
    updated.person_id = "p1"
    updated.topics = ["economy"]
    assert store.upsert_utterances([updated]) == 0
    assert next(
        item.topics for item in store.load_utterances() if item.utterance_id == "u1"
    ) == ["economy"]

    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        topics_json = conn.execute(
            "SELECT topics_json FROM utterances WHERE utterance_id = 'u1'"
        ).fetchone()[0]
    assert {"people", "utterances"}.issubset(tables)
    assert {"idx_utterances_person_spoken_at", "idx_utterances_spoken_at"}.issubset(indexes)
    assert topics_json == '["economy"]'


def test_jsonl_sqlite_export_roundtrip_is_lossless(tmp_path):
    jsonl = Store(tmp_path / "jsonl")
    people = [Person(person_id="p1", name="이가상", committees=["국토교통위원회"])]
    utterance = make_utterance("u1", "이가상")
    utterance.person_id = "p1"
    utterance.topics = ["housing"]
    jsonl.save_people(people)
    jsonl.save_utterances([utterance])

    db_path = tmp_path / "db.sqlite"
    assert cli.cmd_migrate_store(
        type("Args", (), {"store": str(jsonl.root), "db": str(db_path)})()
    ) == 0
    exported = tmp_path / "exported"
    assert cli.cmd_export_jsonl(
        type("Args", (), {"db": str(db_path), "out": str(exported)})()
    ) == 0

    output = Store(exported)
    assert [person.to_dict() for person in output.load_people()] == [
        person.to_dict() for person in people
    ]
    assert [item.to_dict() for item in output.load_utterances()] == [utterance.to_dict()]


def test_cli_data_commands_default_to_sqlite():
    parser = cli.build_parser()
    assert parser.parse_args(["build-site"]).db == "data/db.sqlite"
    assert parser.parse_args(["classify-topics"]).db == "data/db.sqlite"
    assert parser.parse_args(["fetch-members"]).db == "data/db.sqlite"


def test_migrate_store_rejects_missing_jsonl_without_touching_database(tmp_path):
    db_path = tmp_path / "db.sqlite"
    assert cli.cmd_migrate_store(
        type(
            "Args",
            (),
            {"store": str(tmp_path / "missing"), "db": str(db_path)},
        )()
    ) == 1
    assert not db_path.exists()
