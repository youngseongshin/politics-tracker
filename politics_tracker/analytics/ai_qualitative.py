"""사람이 읽고 작성한 AI 국회 논의 보고서를 원문 발언에 연결한다."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

import yaml


_REPORT_PATH = Path(__file__).parent / "data" / "ai_qualitative.yaml"


def _normalized(value: str) -> str:
    return " ".join(value.split())


def _all_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _all_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_strings(item)


def _require_text(mapping: dict[str, Any], key: str, location: str) -> None:
    if not isinstance(mapping.get(key), str) or not mapping[key].strip():
        raise ValueError(f"{location}.{key} must be a non-empty string")


def _require_text_list(
    mapping: dict[str, Any], key: str, location: str, minimum: int
) -> None:
    value = mapping.get(key)
    if (
        not isinstance(value, list)
        or len(value) < minimum
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError(
            f"{location}.{key} must contain at least {minimum} non-empty strings"
        )


def validate_ai_qualitative_report(report: dict[str, Any]) -> None:
    """정성 보고서가 공개에 필요한 구조와 근거 수를 갖췄는지 확인한다."""
    if not isinstance(report, dict):
        raise ValueError("AI qualitative report must be a mapping")

    for key in (
        "report_version",
        "reviewed_through",
        "title",
        "lead",
        "central_judgment",
    ):
        _require_text(report, key, "report")
    _require_text_list(report, "core_findings", "report", 3)
    _require_text_list(report, "open_questions", "report", 3)
    _require_text_list(report, "caveats", "report", 2)

    chronology = report.get("chronology")
    if not isinstance(chronology, list) or len(chronology) < 3:
        raise ValueError("report.chronology must contain at least 3 periods")
    for index, period in enumerate(chronology):
        location = f"report.chronology[{index}]"
        if not isinstance(period, dict):
            raise ValueError(f"{location} must be a mapping")
        for key in ("period", "title", "summary"):
            _require_text(period, key, location)
        _require_text_list(period, "issues", location, 2)

    themes = report.get("themes")
    if not isinstance(themes, list) or len(themes) < 7:
        raise ValueError("report.themes must contain at least 7 qualitative themes")
    theme_keys: set[str] = set()
    for index, theme in enumerate(themes):
        location = f"report.themes[{index}]"
        if not isinstance(theme, dict):
            raise ValueError(f"{location} must be a mapping")
        for key in (
            "key",
            "eyebrow",
            "title",
            "summary",
            "implication",
            "open_question",
        ):
            _require_text(theme, key, location)
        _require_text_list(theme, "discussions", location, 2)
        _require_text_list(theme, "tensions", location, 1)
        if theme["key"] in theme_keys:
            raise ValueError(f"Duplicate qualitative theme key: {theme['key']}")
        theme_keys.add(theme["key"])

        evidence = theme.get("evidence")
        if not isinstance(evidence, list) or len(evidence) < 3:
            raise ValueError(f"{location}.evidence must contain at least 3 records")
        evidence_ids: set[str] = set()
        for evidence_index, item in enumerate(evidence):
            item_location = f"{location}.evidence[{evidence_index}]"
            if not isinstance(item, dict):
                raise ValueError(f"{item_location} must be a mapping")
            for key in ("utterance_id", "focus", "note"):
                _require_text(item, key, item_location)
            if item["utterance_id"] in evidence_ids:
                raise ValueError(
                    f"Duplicate evidence in theme {theme['key']}: "
                    f"{item['utterance_id']}"
                )
            evidence_ids.add(item["utterance_id"])

    cross_cutting = report.get("cross_cutting")
    if not isinstance(cross_cutting, list) or len(cross_cutting) < 4:
        raise ValueError("report.cross_cutting must contain at least 4 tensions")
    for index, tension in enumerate(cross_cutting):
        location = f"report.cross_cutting[{index}]"
        if not isinstance(tension, dict):
            raise ValueError(f"{location} must be a mapping")
        for key in ("title", "summary"):
            _require_text(tension, key, location)

    if any("—" in value for value in _all_strings(report)):
        raise ValueError("Site copy must not contain an em dash")


def load_ai_qualitative_report(path: Path | None = None) -> dict[str, Any]:
    report_path = path or _REPORT_PATH
    with report_path.open(encoding="utf-8") as handle:
        report = yaml.safe_load(handle)
    validate_ai_qualitative_report(report)
    return report


def _evidence_ids(report: dict[str, Any]) -> set[str]:
    return {
        item["utterance_id"]
        for theme in report["themes"]
        for item in theme["evidence"]
    }


def resolve_ai_qualitative_report(
    analysis: dict[str, Any], report: dict[str, Any] | None = None
) -> dict[str, Any]:
    """정성 보고서의 모든 근거를 현재 수집된 원문에 대조한다.

    샘플 데이터처럼 선정 근거가 하나도 없는 자료에서는 보고서를 숨긴다. 근거가
    일부만 존재하는 경우에는 오래되거나 불완전한 배포로 보고 빌드를 중단한다.
    """
    resolved = deepcopy(report or load_ai_qualitative_report())
    validate_ai_qualitative_report(resolved)

    rows_by_id = {
        row["utterance"].utterance_id: row
        for group in analysis.get("timeline", [])
        for row in group.get("rows", [])
    }
    expected_ids = _evidence_ids(resolved)
    present_ids = expected_ids.intersection(rows_by_id)
    if not present_ids:
        resolved["available"] = False
        resolved["missing_evidence_count"] = len(expected_ids)
        return resolved

    missing_ids = sorted(expected_ids - present_ids)
    if missing_ids:
        preview = ", ".join(missing_ids[:5])
        raise ValueError(
            "Qualitative AI report has missing evidence IDs: "
            f"{preview}{' ...' if len(missing_ids) > 5 else ''}"
        )

    for theme in resolved["themes"]:
        resolved_evidence = []
        speaker_names: set[str] = set()
        source_urls: set[str] = set()
        for item in theme["evidence"]:
            row = rows_by_id[item["utterance_id"]]
            utterance = row["utterance"]
            focus = _normalized(item["focus"])
            if focus not in _normalized(utterance.text):
                raise ValueError(
                    f"Evidence focus is absent from {utterance.utterance_id}: "
                    f"{item['focus']}"
                )
            context = next(
                (
                    candidate
                    for candidate in row["contexts"]
                    if focus in _normalized(candidate)
                ),
                None,
            )
            if context is None:
                raise ValueError(
                    f"Evidence focus has no rendered context: {utterance.utterance_id}"
                )
            speaker_names.add(row["speaker_name"])
            source_urls.add(utterance.source["url"])
            resolved_evidence.append({**item, **row, "focus_context": context})

        if len(speaker_names) < 3:
            raise ValueError(
                f"Theme {theme['key']} requires evidence from at least 3 speakers"
            )
        if len(source_urls) < 3:
            raise ValueError(
                f"Theme {theme['key']} requires evidence from at least 3 meetings"
            )
        theme["evidence"] = resolved_evidence

    resolved["available"] = True
    resolved["missing_evidence_count"] = 0
    return resolved
