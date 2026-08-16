from argparse import Namespace
from pathlib import Path

import pytest
import yaml

from politics_tracker import cli
from politics_tracker.audit import build_balance_report
from politics_tracker.factchecks import load_factchecks
from politics_tracker.models import (
    Correction,
    FactCheckLink,
    Person,
    ReviewItem,
    Stance,
    Utterance,
    correction_id_for,
)
from politics_tracker.site.build import build_site
from politics_tracker.storage import SqliteStore, Store


def _utterance(uid="utt_1", person_id="p1", *, topics=None):
    return Utterance(
        utterance_id=uid,
        speaker_name="이가상",
        speaker_role="의원",
        spoken_at="2026-08-16",
        venue={"type": "assembly_plenary", "session": "가상 본회의"},
        text="가상 검증 대상 발언입니다.",
        source={
            "kind": "assembly_minutes",
            "url": "https://example.invalid/minutes/1",
            "title": "가상 회의록",
        },
        person_id=person_id,
        topics=topics or [],
        topic_source="rules",
        topic_model="deterministic",
        topic_prompt_version="topic_rules_v1",
    )


def _pending_correction() -> Correction:
    requested_at = "2026-08-16T09:00:00Z"
    return Correction(
        correction_id=correction_id_for(
            "utterance", "utt_1", "issue #12", requested_at
        ),
        target_kind="utterance",
        target_id="utt_1",
        requested_at=requested_at,
        request_summary="회의록 표기와 다른 문구를 확인해 달라는 요청",
        channel="github_issue",
        channel_ref="issue #12",
        resolution=None,
        resolved_at=None,
        public_note=None,
    )


def test_correction_cli_is_append_only_and_idempotent(tmp_path):
    store = SqliteStore(tmp_path / "db.sqlite")
    store.save_people([Person(person_id="p1", name="이가상")])
    store.save_utterances([_utterance()])
    add_args = Namespace(
        db=str(store.db_path),
        target_kind="utterance",
        target_id="utt_1",
        request_summary="회의록 표기와 다른 문구를 확인해 달라는 요청",
        channel_ref="issue #12",
        requested_at="2026-08-16T09:00:00Z",
    )
    assert cli.cmd_correction_add(add_args) == 0
    assert cli.cmd_correction_add(add_args) == 0
    correction = store.load_corrections()[0]

    resolve_args = Namespace(
        db=str(store.db_path),
        correction_id=correction.correction_id,
        resolution="반영",
        public_note="회의록 원문에 맞춰 문구를 바로잡았습니다.",
        resolved_at="2026-08-17T03:00:00Z",
    )
    assert cli.cmd_correction_resolve(resolve_args) == 0
    assert cli.cmd_correction_resolve(resolve_args) == 0
    resolved = store.get_correction(correction.correction_id)
    assert resolved.status_label == "반영"

    changed = Correction.from_dict(
        {**resolved.to_dict(), "public_note": "사후에 바꾼 처리 결과"}
    )
    with pytest.raises(ValueError, match="immutable"):
        store.upsert_corrections([changed])
    with pytest.raises(ValueError, match="immutable"):
        resolved.with_resolution(
            resolution="기각",
            resolved_at="2026-08-18T03:00:00Z",
            public_note="다른 결과",
        )


def test_correction_yaml_import_preserves_resolved_record(tmp_path):
    store = SqliteStore(tmp_path / "db.sqlite")
    store.save_people([Person(person_id="p1", name="이가상")])
    store.save_utterances([_utterance()])
    pending = _pending_correction()
    yaml_path = tmp_path / "corrections.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {"corrections": [pending.to_dict()]},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    args = Namespace(path=str(yaml_path), db=str(store.db_path))
    assert cli.cmd_correction_import(args) == 0
    resolved = pending.with_resolution(
        resolution="부분 반영",
        resolved_at="2026-08-17T03:00:00Z",
        public_note="요청 중 한 항목을 반영했습니다.",
    )
    store.upsert_corrections([resolved])
    assert cli.cmd_correction_import(args) == 0
    assert store.get_correction(pending.correction_id) == resolved


def test_factcheck_correction_and_methodology_are_published(tmp_path):
    person = Person(person_id="p1", name="이가상", party="가상당")
    utterance = _utterance(topics=["housing"])
    factcheck = FactCheckLink(
        utterance_id="utt_1",
        organization="가상 팩트체크 기관",
        verdict_quote="사실 아님",
        url="https://example.invalid/factcheck/1",
        checked_at="2026-08-17",
    )
    correction = _pending_correction().with_resolution(
        resolution="반영",
        resolved_at="2026-08-17T03:00:00Z",
        public_note="회의록 원문에 맞춰 문구를 바로잡았습니다.",
    )
    out = tmp_path / "site"
    build_site(
        [person],
        [utterance],
        out,
        factchecks=[factcheck],
        corrections=[correction],
    )
    person_page = (out / "person" / "p1.html").read_text(encoding="utf-8")
    assert "가상 팩트체크 기관 “사실 아님”" in person_page
    assert "https://example.invalid/factcheck/1" in person_page
    assert "정정 반영" in person_page
    assert "회의록 원문에 맞춰" in person_page

    topic_page = (out / "topic" / "housing.html").read_text(encoding="utf-8")
    assert "가상 팩트체크 기관" in topic_page and "정정 반영" in topic_page
    corrections_page = (out / "corrections.html").read_text(encoding="utf-8")
    assert 'id="' + correction.correction_id + '"' in corrections_page
    assert "issues/12" in corrections_page
    assert "person/p1.html#utt_1" in corrections_page
    about = (out / "about.html").read_text(encoding="utf-8")
    assert "접수 후 7일 안에 1차 회신" in about
    assert "issues/new?template=correction.yml" in about
    methodology = (out / "methodology.html").read_text(encoding="utf-8")
    assert "말과 표결 기록 산식" in methodology
    assert "종합 점수, 순위, 등급은 만들지 않습니다" in methodology
    assert "topic_rules_v1" in methodology


def test_factcheck_yaml_loader_preserves_external_verdict(tmp_path):
    path = tmp_path / "factchecks.yaml"
    path.write_text(
        """factchecks:
  - utterance_id: utt_1
    organization: 가상 팩트체크 기관
    verdict_quote: 사실 아님
    url: https://example.invalid/factcheck/1
    checked_at: 2026-08-17
""",
        encoding="utf-8",
    )
    records = load_factchecks(path)
    assert records == [
        FactCheckLink(
            utterance_id="utt_1",
            organization="가상 팩트체크 기관",
            verdict_quote="사실 아님",
            url="https://example.invalid/factcheck/1",
            checked_at="2026-08-17",
        )
    ]


def test_balance_audit_reports_party_and_hold_denominators(tmp_path):
    people = [
        Person(person_id="p1", name="이가상", party="가상당"),
        Person(person_id="p2", name="박사례", party="예시당"),
    ]
    attributed = _utterance()
    held = _utterance("utt_2", person_id=None)
    held.topic_source = "held:low_confidence"
    stance_public = Stance(
        stance_id="stance_public",
        utterance_id="utt_1",
        person_id="p1",
        axis="housing_regulation",
        value=0.5,
        confidence=0.9,
        rationale_quote="가상 검증",
        extractor={"backend": "rules", "model": "deterministic", "prompt_version": "stance_rules_v2"},
    )
    stance_held = Stance(
        stance_id="stance_held",
        utterance_id="utt_1",
        person_id="p1",
        axis="housing_regulation",
        value=0,
        confidence=0.5,
        rationale_quote="",
        extractor={"backend": "claude", "model": "fake", "prompt_version": "stance_v1"},
        held_reason="low_confidence",
    )
    review = ReviewItem(
        review_id="rev_1",
        kind="stance",
        target_id="stance_held",
        payload={},
        reason="held:low_confidence",
        status="approved",
        created_at="2026-08-16T00:00:00Z",
        decided_at="2026-08-17T00:00:00Z",
    )
    report = build_balance_report(
        people,
        [attributed, held],
        [stance_public, stance_held],
        [review],
        as_of="2026-08-17",
        sample_size=20,
        sample_errors=1,
    )
    assert report["corpus"]["matching_hold_rate"] == 0.5
    assert report["topics"]["hold_rate"] == 0.5
    assert report["stances"]["hold_rate"] == 0.5
    assert report["sample_error_audit"]["error_rate"] == 0.05
    assert report["parties"][0] == {
        "party": "가상당",
        "people": 1,
        "utterances": 1,
    }
    assert report["reviews"][0]["approval_rate"] == 1.0

    store = SqliteStore(tmp_path / "audit.sqlite")
    store.save_people(people)
    store.save_utterances([attributed, held])
    store.save_stances([stance_public, stance_held])
    store.save_reviews([review])
    out = tmp_path / "audit.json"
    assert cli.cmd_audit_balance(
        Namespace(
            db=str(store.db_path),
            as_of="2026-08-17",
            sample_size=20,
            sample_errors=1,
            out=str(out),
        )
    ) == 0
    assert out.is_file() and '"error_rate": 0.05' in out.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        build_balance_report(
            people,
            [],
            [],
            [],
            as_of="2026-08-17",
            sample_size=5,
            sample_errors=6,
        )


def test_corrections_survive_jsonl_sqlite_exchange(tmp_path):
    source = Store(tmp_path / "source")
    source.save_people([Person(person_id="p1", name="이가상")])
    source.save_utterances([_utterance()])
    source.save_corrections([_pending_correction()])
    db_path = tmp_path / "db.sqlite"
    assert cli.cmd_migrate_store(Namespace(store=str(source.root), db=str(db_path))) == 0
    output = tmp_path / "output"
    assert cli.cmd_export_jsonl(Namespace(db=str(db_path), out=str(output))) == 0
    assert Store(output).load_corrections() == [_pending_correction()]


def test_trust_feature_cli_defaults_and_issue_template():
    parser = cli.build_parser()
    correction = parser.parse_args(["correction", "import"])
    assert correction.path == "data/corrections"
    audit = parser.parse_args(["audit-balance"])
    assert audit.out == "data/audits/balance-latest.json"
    site = parser.parse_args(["build-site"])
    assert site.factchecks == "data/factchecks.yaml"
    assert site.audit_report == "data/audits/balance-latest.json"
    issue_template = Path(".github/ISSUE_TEMPLATE/correction.yml").read_text(
        encoding="utf-8"
    )
    assert "대상 URL" in issue_template and "요청 요지" in issue_template and "근거" in issue_template
