"""외부 팩트체크 기관의 판정을 발언에 수동 연결한다."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from .models import FactCheckLink


def load_factchecks(path: str | Path) -> list[FactCheckLink]:
    source_path = Path(path)
    document = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    if document is None:
        return []
    rows = document.get("factchecks") if isinstance(document, dict) else document
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Factcheck YAML must contain an object list: {source_path}")
    records = []
    seen = set()
    for row in rows:
        normalized = dict(row)
        if isinstance(normalized.get("checked_at"), date):
            normalized["checked_at"] = normalized["checked_at"].isoformat()
        record = FactCheckLink.from_dict(normalized)
        key = (record.utterance_id, record.organization, record.url)
        if key in seen:
            raise ValueError(f"Duplicate factcheck link: {record.utterance_id}, {record.url}")
        seen.add(key)
        records.append(record)
    return records
