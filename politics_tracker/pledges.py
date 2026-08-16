"""공식 출처를 사람이 대조한 공약 YAML을 도메인 레코드로 읽는다."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path

import yaml

from .models import Pledge, pledge_id_for


def pledge_from_dict(data: dict) -> Pledge:
    source = dict(data.get("source") or {})
    person_id = str(data.get("person_id") or "").strip()
    text = str(data.get("text") or "").strip()
    source_url = str(source.get("url") or "").strip()
    pledge_id = str(data.get("pledge_id") or "").strip() or pledge_id_for(
        person_id, text, source_url
    )
    status_history = deepcopy(data.get("status_history") or [])
    for entry in status_history:
        if isinstance(entry.get("decided_at"), date):
            entry["decided_at"] = entry["decided_at"].isoformat()
    return Pledge(
        pledge_id=pledge_id,
        person_id=person_id,
        text=text,
        source=source,
        criteria=str(data.get("criteria") or "").strip(),
        status_history=status_history,
    )


def load_pledge_yaml(path: str | Path) -> list[Pledge]:
    source_path = Path(path)
    document = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    if document is None:
        return []
    if isinstance(document, dict):
        rows = document.get("pledges")
    else:
        rows = document
    if not isinstance(rows, list):
        raise ValueError(f"Pledge YAML must contain a list: {source_path}")
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Every pledge YAML row must be an object: {source_path}")
    return [pledge_from_dict(row) for row in rows]


def load_pledge_path(path: str | Path) -> list[Pledge]:
    source_path = Path(path)
    if source_path.is_file():
        paths = [source_path]
    elif source_path.is_dir():
        paths = sorted(
            list(source_path.glob("*.yaml")) + list(source_path.glob("*.yml"))
        )
    else:
        raise FileNotFoundError(f"Pledge input does not exist: {source_path}")

    pledges: list[Pledge] = []
    seen: dict[str, Pledge] = {}
    for yaml_path in paths:
        for pledge in load_pledge_yaml(yaml_path):
            old = seen.get(pledge.pledge_id)
            if old and old != pledge:
                raise ValueError(f"Conflicting duplicate pledge: {pledge.pledge_id}")
            if not old:
                seen[pledge.pledge_id] = pledge
                pledges.append(pledge)
    return pledges
