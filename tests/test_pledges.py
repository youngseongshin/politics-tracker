from argparse import Namespace

import pytest
import yaml

from politics_tracker import cli
from politics_tracker.models import Person, Pledge, Utterance, pledge_id_for
from politics_tracker.site.build import build_site
from politics_tracker.storage import SqliteStore, Store


def _pledge(index: int, status: str = "미이행") -> Pledge:
    text = f"가상 공약 {index}"
    source_url = f"https://example.invalid/pledges/{index}.pdf"
    return Pledge(
        pledge_id=pledge_id_for("p1", text, source_url),
        person_id="p1",
        text=text,
        source={
            "kind": "nec_pledge_book",
            "url": source_url,
            "title": "가상 정책공약집",
        },
        criteria=f"가상 공약 {index}의 완료를 확인할 공식 문서가 공개된다.",
        status_history=[
            {
                "status": status,
                "decided_at": "2026-08-01",
                "evidence": [
                    {
                        "url": f"https://example.invalid/evidence/{index}/initial",
                        "note": "최초 판정 근거",
                    }
                ],
            }
        ],
    )


def _utterance() -> Utterance:
    return Utterance(
        utterance_id="utt_1",
        speaker_name="이가상",
        speaker_role="의원",
        spoken_at="2026-07-01",
        venue={"type": "assembly_plenary"},
        text="가상 발언",
        source={"kind": "assembly_minutes", "url": "https://example.invalid/minutes"},
        person_id="p1",
    )


def test_pledge_requires_source_evidence_and_chronological_history():
    pledge = _pledge(1)
    assert pledge.current_status == "미이행"
    with pytest.raises(ValueError, match="source"):
        Pledge(
            pledge_id=pledge.pledge_id,
            person_id="p1",
            text=pledge.text,
            source={},
            criteria=pledge.criteria,
            status_history=pledge.status_history,
        )
    with pytest.raises(ValueError, match="chronological"):
        Pledge(
            pledge_id=pledge.pledge_id,
            person_id="p1",
            text=pledge.text,
            source=pledge.source,
            criteria=pledge.criteria,
            status_history=pledge.status_history
            + [
                {
                    "status": "이행",
                    "decided_at": "2026-07-31",
                    "evidence": [{"url": "https://example.invalid/e", "note": "근거"}],
                }
            ],
        )


def test_pledge_store_enforces_immutable_registration_and_append_only_history(tmp_path):
    store = SqliteStore(tmp_path / "db.sqlite")
    original = _pledge(1)
    assert store.upsert_pledges([original]) == 1

    updated = original.with_status(
        status="부분 이행",
        decided_at="2026-08-16",
        evidence=[{"url": "https://example.invalid/evidence/1/update", "note": "일부 완료"}],
    )
    assert store.upsert_pledges([updated]) == 0
    assert store.get_pledge(original.pledge_id) == updated
    assert updated.with_status(
        status="부분 이행",
        decided_at="2026-08-16",
        evidence=[{"url": "https://example.invalid/evidence/1/update", "note": "일부 완료"}],
    ) is updated

    changed_criteria = Pledge.from_dict({**updated.to_dict(), "criteria": "바뀐 기준"})
    with pytest.raises(ValueError, match="immutable"):
        store.upsert_pledges([changed_criteria])
    with pytest.raises(ValueError, match="append-only"):
        store.upsert_pledges([original])


def test_pledge_yaml_import_status_cli_and_site_for_three_records(tmp_path):
    db_path = tmp_path / "db.sqlite"
    store = SqliteStore(db_path)
    store.save_people([Person(person_id="p1", name="이가상")])
    pledges = [_pledge(1), _pledge(2, "이행"), _pledge(3, "검증 불가")]
    yaml_path = tmp_path / "pledges.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {"pledges": [pledge.to_dict() for pledge in pledges]},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    import_args = Namespace(path=str(yaml_path), db=str(db_path))
    assert cli.cmd_pledge_import(import_args) == 0
    assert cli.cmd_pledge_import(import_args) == 0
    assert len(store.load_pledges(person_id="p1")) == 3

    status_args = Namespace(
        pledge_id=pledges[0].pledge_id,
        status="부분 이행",
        evidence="https://example.invalid/evidence/1/update",
        note="일부 완료",
        decided_at="2026-08-16",
        db=str(db_path),
    )
    assert cli.cmd_pledge_set_status(status_args) == 0
    assert cli.cmd_pledge_set_status(status_args) == 0
    assert cli.cmd_pledge_import(import_args) == 0
    assert len(store.get_pledge(pledges[0].pledge_id).status_history) == 2

    site_dir = tmp_path / "site"
    build_site(
        store.load_people(),
        [],
        site_dir,
        pledges=store.load_pledges(),
    )
    page = (site_dir / "person" / "p1.html").read_text(encoding="utf-8")
    assert "공약 이행 기록" in page
    assert "가상 공약 1" in page and "가상 공약 2" in page and "가상 공약 3" in page
    assert "판정 기준:" in page
    assert "부분 이행" in page and "일부 완료" in page
    assert "https://example.invalid/evidence/1/update" in page


def test_pledges_survive_jsonl_sqlite_exchange(tmp_path):
    source = Store(tmp_path / "source")
    source.save_people([Person(person_id="p1", name="이가상")])
    source.save_utterances([_utterance()])
    source.save_pledges([_pledge(1)])

    db_path = tmp_path / "db.sqlite"
    assert cli.cmd_migrate_store(
        Namespace(store=str(source.root), db=str(db_path))
    ) == 0
    export_path = tmp_path / "export"
    assert cli.cmd_export_jsonl(
        Namespace(db=str(db_path), out=str(export_path))
    ) == 0
    assert Store(export_path).load_pledges() == [_pledge(1)]


def test_pledge_cli_defaults_to_canonical_directory():
    args = cli.build_parser().parse_args(["pledge", "import"])
    assert args.path == "data/pledges"
    assert args.db == "data/db.sqlite"
