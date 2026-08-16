import json
from argparse import Namespace
from types import SimpleNamespace

import pytest
import yaml

from politics_tracker import cli
from politics_tracker.enrich.predictions import (
    propose_predictions_claude,
    propose_predictions_rules,
)
from politics_tracker.models import Person, Prediction, Utterance
from politics_tracker.site.build import build_site
from politics_tracker.storage import SqliteStore, Store


def _utterance(uid="utt_1", person_id="p1"):
    return Utterance(
        utterance_id=uid,
        speaker_name="이가상",
        speaker_role="의원",
        spoken_at="2026-08-16",
        venue={"type": "assembly_plenary"},
        text="2027년 경제성장률은 3%가 될 것입니다.",
        source={"kind": "assembly_minutes", "url": "https://example.invalid/minutes/1"},
        person_id=person_id,
    )


class FakeClient:
    def __init__(self, results=None, *, refusal=False):
        self.calls = []
        self.response = SimpleNamespace(
            stop_reason="refusal" if refusal else "end_turn",
            model="claude-test",
            content=[
                SimpleNamespace(
                    type="text", text=json.dumps({"results": results or []})
                )
            ],
        )
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _llm_result(**updates):
    result = {
        "utterance_id": "utt_1",
        "claim": "2027년 경제성장률은 3%가 될 것입니다.",
        "deadline_hint": "2027년",
        "criteria_draft": "2027년 공식 경제성장률이 3%인지 확인한다.",
        "rationale_quote": "2027년 경제성장률은 3%가 될 것입니다.",
        "verifiable": True,
        "confidence": 0.9,
    }
    result.update(updates)
    return result


def test_prediction_rules_are_conservative_and_deterministic():
    prediction = _utterance()
    promise = _utterance("utt_2")
    promise.text = "내년까지 새 제도를 마련할 것입니다."
    unmatched = _utterance("utt_3", person_id=None)
    historical = _utterance("utt_4")
    historical.text = "그는 2024년에 큰 문제가 될 것이라고 지시했습니다."
    first, stats = propose_predictions_rules(
        [prediction, promise, unmatched, historical]
    )
    second, _ = propose_predictions_rules(
        [prediction, promise, unmatched, historical]
    )
    assert first == second
    assert len(first) == 1
    assert first[0]["utterance_id"] == "utt_1"
    assert first[0]["rationale_quote"] in prediction.text
    assert first[0]["extractor"]["prompt_version"] == "prediction_rules_v2"
    assert stats == {"scanned": 3, "proposed": 1}


def test_prediction_claude_validates_confidence_quote_and_refusal():
    utterance = _utterance()
    client = FakeClient(
        [
            _llm_result(),
            _llm_result(claim="저신뢰", confidence=0.4),
            _llm_result(claim="가짜 인용", rationale_quote="원문에 없는 문장"),
        ]
    )
    candidates, stats = propose_predictions_claude([utterance], client=client)
    assert len(candidates) == 1
    assert candidates[0]["extractor"] == {
        "backend": "claude",
        "model": "claude-test",
        "prompt_version": "prediction_v1",
    }
    assert stats["held_low_confidence"] == 1
    assert stats["held_invalid_quote"] == 1
    call = client.calls[0]
    assert call["fallbacks"] == "default"
    assert call["output_config"]["format"]["type"] == "json_schema"

    held, held_stats = propose_predictions_claude(
        [utterance], client=FakeClient(refusal=True)
    )
    assert held == []
    assert held_stats["held_refusal"] == 1


def test_prediction_review_register_resolve_and_site_flow(tmp_path):
    store = SqliteStore(tmp_path / "db.sqlite")
    store.save_people([Person(person_id="p1", name="이가상")])
    store.save_utterances([_utterance()])
    propose_args = Namespace(
        db=str(store.db_path),
        backend="rules",
        model="unused",
        prompt_version="unused",
        batch_size=20,
        confidence_threshold=0.7,
        candidate_limit=500,
    )
    assert cli.cmd_prediction_propose(propose_args) == 0
    assert cli.cmd_prediction_propose(propose_args) == 0
    reviews = store.load_reviews(kind="prediction", status="pending")
    assert len(reviews) == 1

    approve_args = Namespace(
        db=str(store.db_path),
        review_id=reviews[0].review_id,
        edit=None,
        note="발언 원문과 시한 단서를 확인했습니다.",
    )
    assert cli.cmd_review_approve(approve_args) == 0
    register_args = Namespace(
        db=str(store.db_path),
        review_id=reviews[0].review_id,
        claim="2027년 경제성장률이 3%가 된다.",
        deadline="2027-12-31",
        criteria="한국은행이 발표한 2027년 연간 실질 GDP 성장률이 3.0%이면 적중이다.",
    )
    assert cli.cmd_prediction_register(register_args) == 0
    assert cli.cmd_prediction_register(register_args) == 0
    prediction = store.load_predictions()[0]
    assert prediction.registered_by == "human"
    assert prediction.status == "open"
    open_site = tmp_path / "open-site"
    build_site(
        store.load_people(),
        store.load_utterances(),
        open_site,
        predictions=[prediction],
    )
    open_page = (open_site / "person" / "p1.html").read_text(encoding="utf-8")
    assert "진행 중" in open_page and "2027-12-31" in open_page

    too_early = Namespace(
        db=str(store.db_path),
        prediction_id=prediction.prediction_id,
        status="correct",
        evidence="https://example.invalid/statistics/2027",
        note="공식 연간 성장률",
        resolved_at="2027-12-30",
    )
    assert cli.cmd_prediction_resolve(too_early) == 1
    resolve_args = Namespace(**{**vars(too_early), "resolved_at": "2028-01-15"})
    assert cli.cmd_prediction_resolve(resolve_args) == 0
    assert cli.cmd_prediction_resolve(resolve_args) == 0
    resolved = store.get_prediction(prediction.prediction_id)
    assert resolved.status == "correct"

    changed = Prediction.from_dict({**resolved.to_dict(), "claim": "사후에 바꾼 주장"})
    with pytest.raises(ValueError, match="immutable"):
        store.upsert_predictions([changed])
    with pytest.raises(ValueError, match="immutable"):
        resolved.with_resolution(
            status="incorrect",
            resolved_at="2028-01-16",
            evidence=[{"url": "https://example.invalid/other", "note": "다른 근거"}],
        )

    site_dir = tmp_path / "site"
    build_site(
        store.load_people(),
        store.load_utterances(),
        site_dir,
        predictions=store.load_predictions(),
    )
    page = (site_dir / "person" / "p1.html").read_text(encoding="utf-8")
    assert "예측성 발언 판정" in page
    assert "적중 <span class=\"num\">1</span>건 / 판정 완료" in page
    assert "2027년 경제성장률이 3%가 된다." in page
    assert "한국은행이 발표한" in page
    assert "https://example.invalid/statistics/2027" in page
    assert 'href="#utt_1"' in page


def test_predictions_survive_jsonl_sqlite_exchange(tmp_path):
    source = Store(tmp_path / "source")
    source.save_people([Person(person_id="p1", name="이가상")])
    source.save_utterances([_utterance()])
    prediction = Prediction(
        prediction_id="pred_test",
        utterance_id="utt_1",
        person_id="p1",
        claim="2027년 경제성장률이 3%가 된다.",
        deadline="2027-12-31",
        criteria="공식 연간 통계가 3.0%인지 확인한다.",
        status="open",
        resolution=None,
        registered_by="human",
        resolved_at=None,
    )
    source.save_predictions([prediction])
    db_path = tmp_path / "db.sqlite"
    assert cli.cmd_migrate_store(Namespace(store=str(source.root), db=str(db_path))) == 0
    output = tmp_path / "output"
    assert cli.cmd_export_jsonl(Namespace(db=str(db_path), out=str(output))) == 0
    assert Store(output).load_predictions() == [prediction]


def test_prediction_yaml_import_is_idempotent_and_preserves_resolution(tmp_path):
    store = SqliteStore(tmp_path / "db.sqlite")
    store.save_people([Person(person_id="p1", name="이가상")])
    store.save_utterances([_utterance()])
    prediction = Prediction(
        prediction_id="pred_yaml",
        utterance_id="utt_1",
        person_id="p1",
        claim="2027년 경제성장률이 3%가 된다.",
        deadline="2027-12-31",
        criteria="공식 연간 통계가 3.0%인지 확인한다.",
        status="open",
        resolution=None,
        registered_by="human",
        resolved_at=None,
    )
    yaml_path = tmp_path / "predictions.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {"predictions": [prediction.to_dict()]},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    import_args = Namespace(path=str(yaml_path), db=str(store.db_path))
    assert cli.cmd_prediction_import(import_args) == 0
    assert cli.cmd_prediction_import(import_args) == 0
    resolved = prediction.with_resolution(
        status="correct",
        resolved_at="2028-01-15",
        evidence=[{"url": "https://example.invalid/stat", "note": "공식 통계"}],
    )
    store.upsert_predictions([resolved])
    assert cli.cmd_prediction_import(import_args) == 0
    assert store.get_prediction(prediction.prediction_id) == resolved


def test_prediction_cli_defaults_to_rules():
    args = cli.build_parser().parse_args(["prediction", "propose"])
    assert args.backend == "rules"
    assert args.candidate_limit == 500
    assert args.db == "data/db.sqlite"
    import_args = cli.build_parser().parse_args(["prediction", "import"])
    assert import_args.path == "data/predictions"
