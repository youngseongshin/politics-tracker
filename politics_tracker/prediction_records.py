"""사람이 확정한 예측 등록·판정을 배포용 YAML에서 읽는다."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path

import yaml

from .models import Prediction, prediction_id_for


def prediction_from_dict(data: dict) -> Prediction:
    utterance_id = str(data.get("utterance_id") or "").strip()
    claim = str(data.get("claim") or "").strip()
    deadline = data.get("deadline")
    if isinstance(deadline, date):
        deadline = deadline.isoformat()
    deadline = str(deadline or "").strip()
    resolved_at = data.get("resolved_at")
    if isinstance(resolved_at, date):
        resolved_at = resolved_at.isoformat()
    prediction_id = str(data.get("prediction_id") or "").strip() or prediction_id_for(
        utterance_id, claim, deadline
    )
    return Prediction(
        prediction_id=prediction_id,
        utterance_id=utterance_id,
        person_id=str(data.get("person_id") or "").strip(),
        claim=claim,
        deadline=deadline,
        criteria=str(data.get("criteria") or "").strip(),
        status=str(data.get("status") or "").strip(),
        resolution=deepcopy(data.get("resolution")),
        registered_by=str(data.get("registered_by") or "").strip(),
        resolved_at=str(resolved_at).strip() if resolved_at else None,
    )


def load_prediction_yaml(path: str | Path) -> list[Prediction]:
    source_path = Path(path)
    document = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    if document is None:
        return []
    rows = document.get("predictions") if isinstance(document, dict) else document
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Prediction YAML must contain an object list: {source_path}")
    return [prediction_from_dict(row) for row in rows]


def load_prediction_path(path: str | Path) -> list[Prediction]:
    source_path = Path(path)
    if source_path.is_file():
        paths = [source_path]
    elif source_path.is_dir():
        paths = sorted(
            list(source_path.glob("*.yaml")) + list(source_path.glob("*.yml"))
        )
    else:
        raise FileNotFoundError(f"Prediction input does not exist: {source_path}")
    records: list[Prediction] = []
    seen: dict[str, Prediction] = {}
    for yaml_path in paths:
        for prediction in load_prediction_yaml(yaml_path):
            old = seen.get(prediction.prediction_id)
            if old and old != prediction:
                raise ValueError(
                    f"Conflicting duplicate prediction: {prediction.prediction_id}"
                )
            if not old:
                seen[prediction.prediction_id] = prediction
                records.append(prediction)
    return records
