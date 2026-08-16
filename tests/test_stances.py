import json
from argparse import Namespace
from types import SimpleNamespace

from politics_tracker import cli
from politics_tracker.enrich.stances import (
    RULES_PROMPT_VERSION,
    detect_stance_changes,
    extract_stances_claude,
    extract_stances_rules,
    load_stance_axes,
    normalized_quote_exists,
)
from politics_tracker.models import Person, Stance, Utterance, stance_id_for
from politics_tracker.storage import SqliteStore


def _stance(*, reviewed=False, value=-0.7):
    return Stance(
        stance_id=stance_id_for("utt_1", "housing_regulation", RULES_PROMPT_VERSION),
        utterance_id="utt_1",
        person_id="p1",
        axis="housing_regulation",
        value=value,
        confidence=0.86,
        rationale_quote="공급 확대",
        extractor={
            "backend": "rules",
            "model": "deterministic",
            "prompt_version": RULES_PROMPT_VERSION,
        },
        human_reviewed=reviewed,
    )


def _utterance(text="주택 공급을 확대해야 합니다."):
    return Utterance(
        utterance_id="utt_1",
        speaker_name="이가상",
        speaker_role="의원",
        spoken_at="2026-07-15",
        venue={"type": "assembly_plenary", "session": "가상 본회의"},
        text=text,
        source={"kind": "assembly_minutes", "url": "https://example.invalid/1"},
        person_id="p1",
        topics=["housing"],
        topic_source="rules",
    )


def test_load_initial_six_stance_axes():
    axes = load_stance_axes()
    assert [axis.key for axis in axes] == [
        "housing_regulation",
        "fiscal_policy",
        "labor_hours",
        "nuclear_energy",
        "prosecution_reform",
        "north_korea",
    ]
    housing = axes[0]
    assert housing.negative_pole == "규제 완화·공급 확대 우선"
    assert housing.positive_pole == "규제 강화·투기 억제 우선"
    assert housing.topic_keys == ("housing",)


def test_stance_id_records_prompt_version():
    first = stance_id_for("utt_1", "housing_regulation", "stance_v1")
    assert first == stance_id_for("utt_1", "housing_regulation", "stance_v1")
    assert first != stance_id_for("utt_1", "housing_regulation", "stance_v2")


def test_sqlite_stance_roundtrip_and_reviewed_result_is_not_overwritten(tmp_path):
    store = SqliteStore(tmp_path / "db.sqlite")
    stance = _stance()
    assert store.upsert_stances([stance]) == 1
    assert store.upsert_stances([stance]) == 0
    assert store.load_stances() == [stance]

    reviewed = _stance(reviewed=True)
    store.upsert_stances([reviewed])
    store.upsert_stances([_stance(value=0.7)])
    loaded = store.load_stances(published_only=True)
    assert loaded == [reviewed]
    assert loaded[0].value == -0.7


def test_rules_extract_direction_and_hold_conflicting_phrases():
    axes = load_stance_axes()
    extracted, stats = extract_stances_rules([_utterance()], axes)
    assert len(extracted) == 1
    assert extracted[0].axis == "housing_regulation"
    assert extracted[0].value == -0.7
    assert extracted[0].rationale_quote == "주택 공급을 확대"
    assert extracted[0].held_reason is None
    assert stats["extracted"] == 1

    conflict = _utterance("공급 확대가 필요하고 투기를 억제해야 합니다.")
    held, held_stats = extract_stances_rules([conflict], axes)
    assert held[0].value == 0
    assert held[0].held_reason == "conflicting_rule_phrases"
    assert held_stats["held"] == 1


def test_rules_require_an_explicit_position_and_read_contextual_direction():
    axes = load_stance_axes()
    mere_mentions = [
        (_utterance("불안 증세를 보이며 폭압정치를 이어 가고 있습니다."), "economy"),
        (_utterance("검찰개혁 하겠다고 외치면서 무엇을 했습니까?"), "justice"),
        (_utterance("수사와 기소를 분리하겠다라고 해서 시작됐습니다."), "justice"),
        (
            _utterance("피해자 권리 보장은 검찰개혁을 완성시킬 필수 조건입니다."),
            "justice",
        ),
        (_utterance("원전 확대 답을 정해 놓은 여론조사입니다."), "environment_energy"),
        (
            _utterance("검찰청 폐지를 추진한 정부와 여당의 논리는 부족합니다."),
            "justice",
        ),
    ]
    for utterance, topic in mere_mentions:
        utterance.topics = [topic]
    extracted, _ = extract_stances_rules(
        [utterance for utterance, _ in mere_mentions], axes
    )
    assert extracted == []

    critique = _utterance(
        "탈원전 정책으로 2030년까지 47조 원의 비용이 발생한 것으로 추정했습니다."
    )
    critique.topics = ["environment_energy"]
    extracted, _ = extract_stances_rules([critique], axes)
    assert len(extracted) == 1
    assert extracted[0].axis == "nuclear_energy"
    assert extracted[0].value == 0.7
    assert "비용" in extracted[0].rationale_quote

    opposed = _utterance(
        "정부는 원전 건설계획을 강행하겠다고 밝혔습니다. 심히 유감입니다."
    )
    opposed.topics = ["environment_energy"]
    extracted, _ = extract_stances_rules([opposed], axes)
    assert len(extracted) == 1
    assert extracted[0].value == -0.7
    assert "유감" in extracted[0].rationale_quote


def test_rules_sync_removes_stale_public_results_but_preserves_human_review(tmp_path):
    store = SqliteStore(tmp_path / "db.sqlite")
    stale = Stance(
        stance_id=stance_id_for("utt_old", "housing_regulation", "stance_rules_v1"),
        utterance_id="utt_old",
        person_id="p1",
        axis="housing_regulation",
        value=0.7,
        confidence=0.86,
        rationale_quote="규제 강화",
        extractor={
            "backend": "rules",
            "model": "deterministic",
            "prompt_version": "stance_rules_v1",
        },
    )
    reviewed = _stance(reviewed=True)
    store.save_stances([stale, reviewed])
    replacement = _stance(value=-0.7)

    assert store.sync_unreviewed_stances([replacement], backend="rules") == (0, 1)
    assert store.load_stances() == [reviewed]
    assert store.sync_unreviewed_stances([replacement], backend="rules") == (0, 0)


def test_normalized_quote_guard_accepts_only_original_text():
    assert normalized_quote_exists("주택  공급을\n확대합니다.", "주택 공급을 확대합니다.")
    assert not normalized_quote_exists("주택 공급을 확대합니다.", "규제를 강화합니다")


class FakeClient:
    def __init__(self, results=None, *, refusal=False):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.response = SimpleNamespace(
            stop_reason="refusal" if refusal else "end_turn",
            model="claude-test",
            content=[
                SimpleNamespace(
                    type="text", text=json.dumps({"results": results or []})
                )
            ],
        )

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_claude_holds_low_confidence_invalid_quote_and_refusal():
    axes = load_stance_axes()
    utterance = _utterance()
    low = FakeClient(
        [
            {
                "utterance_id": "utt_1",
                "axis": "housing_regulation",
                "value": -0.8,
                "confidence": 0.4,
                "rationale_quote": "주택 공급을 확대",
            }
        ]
    )
    low_stances, low_stats = extract_stances_claude(
        [utterance], axes, client=low
    )
    assert low_stances[0].held_reason == "low_confidence"
    assert low_stats["held_low_confidence"] == 1

    invalid = FakeClient(
        [
            {
                "utterance_id": "utt_1",
                "axis": "housing_regulation",
                "value": -0.8,
                "confidence": 0.9,
                "rationale_quote": "원문에 없는 문장",
            }
        ]
    )
    invalid_stances, invalid_stats = extract_stances_claude(
        [utterance], axes, client=invalid
    )
    assert invalid_stances[0].held_reason == "invalid_quote"
    assert invalid_stances[0].rationale_quote == ""
    assert invalid_stats["held_invalid_quote"] == 1

    refused, refused_stats = extract_stances_claude(
        [utterance], axes, client=FakeClient(refusal=True)
    )
    assert refused[0].held_reason == "refusal"
    assert refused_stats["held_refusal"] == 1


def test_extract_stances_cli_is_idempotent_and_queues_held(tmp_path):
    db_path = tmp_path / "db.sqlite"
    store = SqliteStore(db_path)
    store.save_people([Person(person_id="p1", name="이가상")])
    store.save_utterances(
        [_utterance("공급 확대가 필요하고 투기를 억제해야 합니다.")]
    )
    args = Namespace(
        db=str(db_path),
        axes="config/stance_axes.yaml",
        backend="rules",
        model="unused",
        prompt_version="unused",
        batch_size=20,
        confidence_threshold=0.7,
    )
    assert cli.cmd_extract_stances(args) == 0
    assert cli.cmd_extract_stances(args) == 0
    assert len(store.load_stances()) == 1
    assert len(store.load_reviews(kind="stance", status="pending")) == 1


def test_change_detection_uses_adjacent_values_and_requires_context_review(tmp_path):
    first_utterance = _utterance("공급 확대가 필요합니다.")
    second_utterance = _utterance("투기를 억제해야 합니다.")
    second_utterance.utterance_id = "utt_2"
    second_utterance.spoken_at = "2026-08-15"

    first = Stance(
        stance_id=stance_id_for("utt_1", "housing_regulation", "stance_v1"),
        utterance_id="utt_1",
        person_id="p1",
        axis="housing_regulation",
        value=-0.6,
        confidence=0.9,
        rationale_quote="공급 확대",
        extractor={"backend": "test", "model": "test", "prompt_version": "stance_v1"},
    )
    second = Stance(
        stance_id=stance_id_for("utt_2", "housing_regulation", "stance_v1"),
        utterance_id="utt_2",
        person_id="p1",
        axis="housing_regulation",
        value=0.3,
        confidence=0.9,
        rationale_quote="투기를 억제",
        extractor={"backend": "test", "model": "test", "prompt_version": "stance_v1"},
    )
    changes = detect_stance_changes(
        [first, second], [first_utterance, second_utterance]
    )
    assert len(changes) == 1
    assert changes[0]["delta"] == 0.9
    assert changes[0]["before_utterance_id"] == "utt_1"
    assert changes[0]["after_utterance_id"] == "utt_2"

    db_path = tmp_path / "db.sqlite"
    store = SqliteStore(db_path)
    store.save_people([Person(person_id="p1", name="이가상")])
    store.save_utterances([first_utterance, second_utterance])
    store.save_stances([first, second])
    detect_args = Namespace(db=str(db_path), threshold=0.8)
    assert cli.cmd_detect_stance_changes(detect_args) == 0
    assert cli.cmd_detect_stance_changes(detect_args) == 0
    pending = store.load_reviews(kind="stance_change", status="pending")
    assert len(pending) == 1

    missing_context = Namespace(
        db=str(db_path), review_id=pending[0].review_id, edit=None, note=None
    )
    assert cli.cmd_review_approve(missing_context) == 1
    approved_args = Namespace(
        db=str(db_path),
        review_id=pending[0].review_id,
        edit=["context_note=당적 변경 전후 발언"],
        note="두 원문 확인",
    )
    assert cli.cmd_review_approve(approved_args) == 0
    approved = store.get_review(pending[0].review_id)
    assert approved.status == "approved"
    assert approved.payload["context_note"] == "당적 변경 전후 발언"
