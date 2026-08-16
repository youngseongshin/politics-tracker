from politics_tracker.enrich.stances import load_stance_axes
from politics_tracker.models import Stance, stance_id_for
from politics_tracker.storage import SqliteStore


def _stance(*, reviewed=False, value=-0.7):
    return Stance(
        stance_id=stance_id_for("utt_1", "housing_regulation", "stance_rules_v1"),
        utterance_id="utt_1",
        person_id="p1",
        axis="housing_regulation",
        value=value,
        confidence=0.86,
        rationale_quote="공급 확대",
        extractor={
            "backend": "rules",
            "model": "deterministic",
            "prompt_version": "stance_rules_v1",
        },
        human_reviewed=reviewed,
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
