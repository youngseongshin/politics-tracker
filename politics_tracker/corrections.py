"""정정 접수·처리 기록의 배포용 YAML 로더."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml

from .models import Correction, correction_id_for


def _datetime_string(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value).strip()


def correction_from_dict(data: dict) -> Correction:
    requested_at = _datetime_string(data.get("requested_at")) or ""
    target_kind = str(data.get("target_kind") or "").strip()
    target_id = str(data.get("target_id") or "").strip()
    channel_ref = str(data.get("channel_ref") or "").strip()
    correction_id = str(data.get("correction_id") or "").strip() or correction_id_for(
        target_kind, target_id, channel_ref, requested_at
    )
    return Correction(
        correction_id=correction_id,
        target_kind=target_kind,
        target_id=target_id,
        requested_at=requested_at,
        request_summary=str(data.get("request_summary") or "").strip(),
        channel=str(data.get("channel") or "").strip(),
        channel_ref=channel_ref,
        resolution=data.get("resolution"),
        resolved_at=_datetime_string(data.get("resolved_at")),
        public_note=data.get("public_note"),
    )


def load_correction_yaml(path: str | Path) -> list[Correction]:
    source_path = Path(path)
    document = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    if document is None:
        return []
    rows = document.get("corrections") if isinstance(document, dict) else document
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Correction YAML must contain an object list: {source_path}")
    return [correction_from_dict(row) for row in rows]


def load_correction_path(path: str | Path) -> list[Correction]:
    source_path = Path(path)
    if source_path.is_file():
        paths = [source_path]
    elif source_path.is_dir():
        paths = sorted(
            list(source_path.glob("*.yaml")) + list(source_path.glob("*.yml"))
        )
    else:
        raise FileNotFoundError(f"Correction input does not exist: {source_path}")
    records: list[Correction] = []
    seen: dict[str, Correction] = {}
    for yaml_path in paths:
        for correction in load_correction_yaml(yaml_path):
            old = seen.get(correction.correction_id)
            if old and old != correction:
                raise ValueError(
                    f"Conflicting duplicate correction: {correction.correction_id}"
                )
            if not old:
                seen[correction.correction_id] = correction
                records.append(correction)
    return records
