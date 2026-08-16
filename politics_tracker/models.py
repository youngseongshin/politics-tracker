"""핵심 도메인 모델.

설계 문서(docs/design.md) 6장의 도메인 모델 중 Phase 0에 필요한 최소 집합:
Person(인물), Utterance(발언), 그리고 발언에 내장되는 source(출처) dict.

원칙: 출처 없는 발언은 만들 수 없다. Utterance.source는 필수다.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any


@dataclass
class Person:
    person_id: str
    name: str
    party: str | None = None
    district: str | None = None
    era: str | None = None  # 대수 (예: "22")
    committees: list[str] = field(default_factory=list)
    profile_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)  # 수집 원본 레코드 보존

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Person":
        return cls(
            person_id=d["person_id"],
            name=d["name"],
            party=d.get("party"),
            district=d.get("district"),
            era=d.get("era"),
            committees=list(d.get("committees") or []),
            profile_url=d.get("profile_url"),
            raw=dict(d.get("raw") or {}),
        )


@dataclass
class Utterance:
    utterance_id: str
    speaker_name: str
    speaker_role: str | None
    spoken_at: str  # ISO date "YYYY-MM-DD"
    venue: dict[str, Any]  # {"type": "assembly_plenary", "session": "..."}
    text: str
    source: dict[str, Any]  # {"kind", "url", "title", "retrieved_at"} — 필수
    order: int = 0  # 같은 회의록 안에서의 발언 순서
    person_id: str | None = None  # 화자 매칭 결과 (미확정이면 None)
    topics: list[str] = field(default_factory=list)  # 주제 키 (enrich.topics.TOPICS)
    topic_source: str | None = None  # "rules" | "llm:<model>" | "held:<사유>" — 분류 방식 공개
    human_reviewed: bool = False

    def __post_init__(self) -> None:
        if not self.source or not self.source.get("url"):
            raise ValueError("Utterance.source.url is required: 출처 없는 발언은 저장하지 않는다")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Utterance":
        return cls(
            utterance_id=d["utterance_id"],
            speaker_name=d["speaker_name"],
            speaker_role=d.get("speaker_role"),
            spoken_at=d["spoken_at"],
            venue=dict(d.get("venue") or {}),
            text=d["text"],
            source=dict(d.get("source") or {}),
            order=int(d.get("order") or 0),
            person_id=d.get("person_id"),
            topics=list(d.get("topics") or []),
            topic_source=d.get("topic_source"),
            human_reviewed=bool(d.get("human_reviewed", False)),
        )


@dataclass
class ReviewItem:
    review_id: str
    kind: str
    target_id: str
    payload: dict[str, Any]
    reason: str
    status: str
    created_at: str
    decided_at: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        allowed_kinds = {
            "topic",
            "stance",
            "match",
            "bill_link",
            "stance_change",
            "prediction",
        }
        if not self.review_id.startswith("rev_"):
            raise ValueError("ReviewItem.review_id must start with rev_")
        if self.kind not in allowed_kinds:
            raise ValueError(f"Invalid review kind: {self.kind}")
        if self.status not in {"pending", "approved", "rejected"}:
            raise ValueError(f"Invalid review status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReviewItem":
        return cls(
            review_id=data["review_id"],
            kind=data["kind"],
            target_id=data["target_id"],
            payload=dict(data.get("payload") or {}),
            reason=data["reason"],
            status=data.get("status", "pending"),
            created_at=data["created_at"],
            decided_at=data.get("decided_at"),
            note=data.get("note"),
        )


def review_id_for(kind: str, target_id: str, reason: str, payload: dict[str, Any]) -> str:
    """같은 대상·사유·후보 payload에 대해 항상 같은 검수 ID를 만든다."""
    canonical = repr(
        (kind, target_id, reason, _canonical_value(payload))
    ).encode("utf-8")
    return "rev_" + hashlib.sha256(canonical).hexdigest()[:16]


def _canonical_value(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple((key, _canonical_value(value[key])) for key in sorted(value))
    if isinstance(value, list):
        return tuple(_canonical_value(item) for item in value)
    return value


@dataclass
class Stance:
    stance_id: str
    utterance_id: str
    person_id: str
    axis: str
    value: float
    confidence: float
    rationale_quote: str
    extractor: dict[str, str]
    human_reviewed: bool = False
    held_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.stance_id.startswith("stance_"):
            raise ValueError("Stance.stance_id must start with stance_")
        if not -1 <= self.value <= 1:
            raise ValueError("Stance.value must be between -1 and 1")
        if not 0 <= self.confidence <= 1:
            raise ValueError("Stance.confidence must be between 0 and 1")
        required_extractor = {"backend", "model", "prompt_version"}
        if not required_extractor.issubset(self.extractor):
            raise ValueError("Stance.extractor requires backend, model, prompt_version")
        if not self.held_reason and not self.rationale_quote:
            raise ValueError("Published stance requires rationale_quote")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Stance":
        return cls(
            stance_id=data["stance_id"],
            utterance_id=data["utterance_id"],
            person_id=data["person_id"],
            axis=data["axis"],
            value=float(data["value"]),
            confidence=float(data["confidence"]),
            rationale_quote=data.get("rationale_quote", ""),
            extractor=dict(data["extractor"]),
            human_reviewed=bool(data.get("human_reviewed", False)),
            held_reason=data.get("held_reason"),
        )


def stance_id_for(utterance_id: str, axis: str, prompt_version: str) -> str:
    canonical = f"{utterance_id}\0{axis}\0{prompt_version}".encode("utf-8")
    return "stance_" + hashlib.sha256(canonical).hexdigest()[:16]


@dataclass
class Bill:
    bill_id: str
    assembly_bill_no: str
    title: str
    proposed_at: str | None
    link_url: str
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.bill_id.startswith("bill_"):
            raise ValueError("Bill.bill_id must start with bill_")
        if not self.assembly_bill_no or not self.title or not self.link_url:
            raise ValueError("Bill requires assembly_bill_no, title, and link_url")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Bill":
        return cls(
            bill_id=data["bill_id"],
            assembly_bill_no=data["assembly_bill_no"],
            title=data["title"],
            proposed_at=data.get("proposed_at"),
            link_url=data["link_url"],
            raw=dict(data.get("raw") or {}),
        )


@dataclass
class Vote:
    vote_id: str
    bill_id: str
    person_id: str
    decision: str
    voted_at: str
    source: dict[str, Any]
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.vote_id.startswith("vote_"):
            raise ValueError("Vote.vote_id must start with vote_")
        if not self.bill_id.startswith("bill_"):
            raise ValueError("Vote.bill_id must start with bill_")
        if self.decision not in {"찬성", "반대", "기권", "불참"}:
            raise ValueError(f"Invalid vote decision: {self.decision}")
        if not self.source or not self.source.get("url"):
            raise ValueError("Vote.source.url is required")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Vote":
        return cls(
            vote_id=data["vote_id"],
            bill_id=data["bill_id"],
            person_id=data["person_id"],
            decision=data["decision"],
            voted_at=data["voted_at"],
            source=dict(data.get("source") or {}),
            raw=dict(data.get("raw") or {}),
        )


def bill_id_for(assembly_bill_id: str) -> str:
    return "bill_" + hashlib.sha256(assembly_bill_id.encode("utf-8")).hexdigest()[:16]


def vote_id_for(bill_id: str, person_id: str, voted_at: str) -> str:
    canonical = f"{bill_id}\0{person_id}\0{voted_at}".encode("utf-8")
    return "vote_" + hashlib.sha256(canonical).hexdigest()[:16]


@dataclass
class UtteranceBillLink:
    link_id: str
    utterance_id: str
    bill_id: str
    method: str
    confidence: float
    extractor: dict[str, str]
    human_reviewed: bool = False

    def __post_init__(self) -> None:
        if not self.link_id.startswith("ubl_"):
            raise ValueError("UtteranceBillLink.link_id must start with ubl_")
        if self.method not in {"rule:title_match", "llm:candidate"}:
            raise ValueError(f"Invalid bill link method: {self.method}")
        if not 0 <= self.confidence <= 1:
            raise ValueError("UtteranceBillLink.confidence must be between 0 and 1")
        required = {"backend", "model", "prompt_version"}
        if not required.issubset(self.extractor):
            raise ValueError("UtteranceBillLink.extractor is incomplete")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UtteranceBillLink":
        return cls(
            link_id=data["link_id"],
            utterance_id=data["utterance_id"],
            bill_id=data["bill_id"],
            method=data["method"],
            confidence=float(data["confidence"]),
            extractor=dict(data["extractor"]),
            human_reviewed=bool(data.get("human_reviewed", False)),
        )


def bill_link_id_for(utterance_id: str, bill_id: str, method: str) -> str:
    canonical = f"{utterance_id}\0{bill_id}\0{method}".encode("utf-8")
    return "ubl_" + hashlib.sha256(canonical).hexdigest()[:16]


@dataclass
class ConsistencyPair:
    consistency_id: str
    person_id: str
    bill_id: str
    utterance_id: str
    stance_id: str
    vote_id: str
    axis: str
    stance_value: float
    expected_decision: str
    vote_decision: str
    consistent: bool
    formula_version: str = "consistency_v1"

    def __post_init__(self) -> None:
        if not self.consistency_id.startswith("cons_"):
            raise ValueError("ConsistencyPair.consistency_id must start with cons_")
        if self.expected_decision not in {"찬성", "반대"}:
            raise ValueError("ConsistencyPair.expected_decision is invalid")
        if self.vote_decision not in {"찬성", "반대", "기권", "불참"}:
            raise ValueError("ConsistencyPair.vote_decision is invalid")
        if self.formula_version != "consistency_v1":
            raise ValueError("Unsupported consistency formula version")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConsistencyPair":
        return cls(
            consistency_id=data["consistency_id"],
            person_id=data["person_id"],
            bill_id=data["bill_id"],
            utterance_id=data["utterance_id"],
            stance_id=data["stance_id"],
            vote_id=data["vote_id"],
            axis=data["axis"],
            stance_value=float(data["stance_value"]),
            expected_decision=data["expected_decision"],
            vote_decision=data["vote_decision"],
            consistent=bool(data["consistent"]),
            formula_version=data.get("formula_version", "consistency_v1"),
        )


def consistency_id_for(stance_id: str, vote_id: str, formula_version: str) -> str:
    canonical = f"{stance_id}\0{vote_id}\0{formula_version}".encode("utf-8")
    return "cons_" + hashlib.sha256(canonical).hexdigest()[:16]


PLEDGE_STATUSES = {"이행", "부분 이행", "미이행", "검증 불가"}


@dataclass
class Pledge:
    pledge_id: str
    person_id: str
    text: str
    source: dict[str, Any]
    criteria: str
    status_history: list[dict[str, Any]]

    def __post_init__(self) -> None:
        if not self.pledge_id.startswith("pledge_"):
            raise ValueError("Pledge.pledge_id must start with pledge_")
        if not self.person_id or not self.text.strip() or not self.criteria.strip():
            raise ValueError("Pledge requires person_id, text, and criteria")
        if not all(self.source.get(key) for key in ("kind", "url", "title")):
            raise ValueError("Pledge.source requires kind, url, and title")
        if not self.status_history:
            raise ValueError("Pledge requires at least one status history entry")

        previous_date: date | None = None
        for entry in self.status_history:
            status = entry.get("status")
            if status not in PLEDGE_STATUSES:
                raise ValueError(f"Invalid pledge status: {status}")
            try:
                decided_at = date.fromisoformat(entry.get("decided_at", ""))
            except (TypeError, ValueError) as exc:
                raise ValueError("Pledge status decided_at must be an ISO date") from exc
            if previous_date and decided_at < previous_date:
                raise ValueError("Pledge status history must be chronological")
            previous_date = decided_at
            evidence = entry.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                raise ValueError("Pledge status requires evidence")
            for item in evidence:
                if not isinstance(item, dict) or not item.get("url") or not item.get("note"):
                    raise ValueError("Pledge evidence requires url and note")

    @property
    def current_status(self) -> str:
        return self.status_history[-1]["status"]

    def with_status(
        self,
        *,
        status: str,
        decided_at: str,
        evidence: list[dict[str, str]],
    ) -> "Pledge":
        entry = {
            "status": status,
            "decided_at": decided_at,
            "evidence": deepcopy(evidence),
        }
        if self.status_history[-1] == entry:
            return self
        return Pledge(
            pledge_id=self.pledge_id,
            person_id=self.person_id,
            text=self.text,
            source=deepcopy(self.source),
            criteria=self.criteria,
            status_history=deepcopy(self.status_history) + [entry],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Pledge":
        return cls(
            pledge_id=data["pledge_id"],
            person_id=data["person_id"],
            text=data["text"],
            source=deepcopy(data.get("source") or {}),
            criteria=data["criteria"],
            status_history=deepcopy(data.get("status_history") or []),
        )


def pledge_id_for(person_id: str, text: str, source_url: str) -> str:
    canonical = f"{person_id}\0{text.strip()}\0{source_url}".encode("utf-8")
    return "pledge_" + hashlib.sha256(canonical).hexdigest()[:16]


PREDICTION_STATUSES = {"open", "correct", "incorrect", "unresolvable"}


@dataclass
class Prediction:
    prediction_id: str
    utterance_id: str
    person_id: str
    claim: str
    deadline: str
    criteria: str
    status: str
    resolution: dict[str, Any] | None
    registered_by: str
    resolved_at: str | None

    def __post_init__(self) -> None:
        if not self.prediction_id.startswith("pred_"):
            raise ValueError("Prediction.prediction_id must start with pred_")
        if not all(
            (
                self.utterance_id,
                self.person_id,
                self.claim.strip(),
                self.criteria.strip(),
            )
        ):
            raise ValueError("Prediction requires utterance, person, claim, and criteria")
        try:
            deadline = date.fromisoformat(self.deadline)
        except (TypeError, ValueError) as exc:
            raise ValueError("Prediction.deadline must be an ISO date") from exc
        if self.status not in PREDICTION_STATUSES:
            raise ValueError(f"Invalid prediction status: {self.status}")
        if self.registered_by != "human":
            raise ValueError("Prediction.registered_by must be human")
        if self.status == "open":
            if self.resolution is not None or self.resolved_at is not None:
                raise ValueError("Open prediction cannot have a resolution")
            return
        if not self.resolution or not self.resolved_at:
            raise ValueError("Resolved prediction requires resolution and resolved_at")
        evidence = self.resolution.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("Prediction resolution requires evidence")
        for item in evidence:
            if not isinstance(item, dict) or not item.get("url") or not item.get("note"):
                raise ValueError("Prediction evidence requires url and note")
        try:
            resolved_at = date.fromisoformat(self.resolved_at)
        except (TypeError, ValueError) as exc:
            raise ValueError("Prediction.resolved_at must be an ISO date") from exc
        if resolved_at < deadline:
            raise ValueError("Prediction cannot be resolved before its deadline")

    def with_resolution(
        self,
        *,
        status: str,
        resolved_at: str,
        evidence: list[dict[str, str]],
    ) -> "Prediction":
        if self.status != "open":
            raise ValueError("Resolved prediction is immutable")
        if status == "open":
            raise ValueError("Resolution status cannot be open")
        return Prediction(
            prediction_id=self.prediction_id,
            utterance_id=self.utterance_id,
            person_id=self.person_id,
            claim=self.claim,
            deadline=self.deadline,
            criteria=self.criteria,
            status=status,
            resolution={"evidence": deepcopy(evidence)},
            registered_by=self.registered_by,
            resolved_at=resolved_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Prediction":
        return cls(
            prediction_id=data["prediction_id"],
            utterance_id=data["utterance_id"],
            person_id=data["person_id"],
            claim=data["claim"],
            deadline=data["deadline"],
            criteria=data["criteria"],
            status=data["status"],
            resolution=deepcopy(data["resolution"]),
            registered_by=data["registered_by"],
            resolved_at=data["resolved_at"],
        )


def prediction_id_for(utterance_id: str, claim: str, deadline: str) -> str:
    canonical = f"{utterance_id}\0{claim.strip()}\0{deadline}".encode("utf-8")
    return "pred_" + hashlib.sha256(canonical).hexdigest()[:16]


def prediction_candidate_id_for(
    utterance_id: str, claim: str, prompt_version: str
) -> str:
    canonical = f"{utterance_id}\0{claim.strip()}\0{prompt_version}".encode("utf-8")
    return "predcand_" + hashlib.sha256(canonical).hexdigest()[:16]


def utterance_id_for(spoken_at: str, source_url: str, order: int) -> str:
    """회의록(출처)과 날짜, 순서로 결정되는 안정적 ID. 재수집해도 같은 ID가 나온다."""
    h = hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:8]
    return f"utt_{spoken_at.replace('-', '')}_{h}_{order:04d}"
