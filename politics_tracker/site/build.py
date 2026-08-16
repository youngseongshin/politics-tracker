"""정적 사이트 빌더.

Phase 0 원칙 (docs/design.md 10장): 데이터 갱신이 일 1회 배치이므로 전부 정적
HTML로 생성한다. 이 파이썬 빌더는 walking skeleton이며, Phase 1에서 Next.js
SSG로 렌더 레이어만 교체한다 — 데이터 레이어(JSONL/스키마)는 그대로 간다.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..analytics.ai_mentions import build_ai_analysis
from ..enrich.topics import TOPIC_LABELS
from ..enrich.stances import StanceAxis, select_best_stances
from ..audit import build_balance_report
from ..models import (
    Bill,
    ConsistencyPair,
    Correction,
    FactCheckLink,
    Person,
    Pledge,
    Prediction,
    ReviewItem,
    Stance,
    Utterance,
    UtteranceBillLink,
    Vote,
)

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_REPOSITORY_URL = "https://github.com/youngseongshin/politics-tracker"
_CORRECTION_FORM_URL = f"{_REPOSITORY_URL}/issues/new?template=correction.yml"


@dataclass
class BuildStats:
    people_pages: int
    utterances_rendered: int
    unmatched_utterances: int


_MAX_SEARCH_INDEX_BYTES = 5 * 1024 * 1024


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["comma"] = lambda value: f"{int(value):,}"
    return env


def _timeline_groups(utterances: list[Utterance]) -> list[dict]:
    """발언을 (날짜, 회의) 단위로 묶어 최신순 타임라인으로 만든다."""
    groups: dict[tuple[str, str, str], list[Utterance]] = defaultdict(list)
    for u in utterances:
        groups[
            (
                u.spoken_at,
                u.venue.get("session", ""),
                u.venue.get("committee", ""),
            )
        ].append(u)

    ordered = []
    for (spoken_at, session, committee), items in sorted(
        groups.items(), key=lambda kv: kv[0][0], reverse=True
    ):
        ordered.append(
            {
                "spoken_at": spoken_at,
                "session": session,
                "committee": committee,
                "utterances": sorted(items, key=lambda u: u.order),
            }
        )
    return ordered


def _search_row(utterance: Utterance, person: Person) -> dict:
    return {
        "utterance_id": utterance.utterance_id,
        "person_id": person.person_id,
        "person_name": person.name,
        "spoken_at": utterance.spoken_at,
        "text": utterance.text,
        "source_url": utterance.source["url"],
        "source_title": utterance.source.get("title") or "회의록 원문",
    }


def _encoded_search_rows(rows: list[dict]) -> bytes:
    return json.dumps(
        rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _write_search_shards(
    out: Path,
    people: list[Person],
    utterances: list[Utterance],
) -> list[str]:
    """연도별 검색 JSON을 쓰고 전체가 5MB를 넘으면 반기 단위로 나눈다."""
    people_by_id = {person.person_id: person for person in people}
    rows = [
        _search_row(utterance, people_by_id[utterance.person_id])
        for utterance in utterances
        if utterance.person_id in people_by_id
    ]
    rows.sort(key=lambda row: (row["spoken_at"], row["utterance_id"]), reverse=True)
    split_half_year = len(_encoded_search_rows(rows)) > _MAX_SEARCH_INDEX_BYTES

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        year = row["spoken_at"][:4]
        key = year
        if split_half_year:
            month = int(row["spoken_at"][5:7])
            key = f"{year}-h{1 if month <= 6 else 2}"
        grouped[key].append(row)

    search_dir = out / "search"
    search_dir.mkdir(parents=True, exist_ok=True)
    for existing in search_dir.glob("index-*.json"):
        existing.unlink()
    shard_names: list[str] = []
    for key in sorted(grouped, reverse=True):
        name = f"index-{key}.json"
        (search_dir / name).write_bytes(_encoded_search_rows(grouped[key]))
        shard_names.append(name)
    return shard_names


def _topic_rows(
    people: list[Person], utterances: list[Utterance]
) -> tuple[list[dict], dict[str, list[dict]]]:
    people_by_id = {person.person_id: person for person in people}
    by_topic: dict[str, list[dict]] = {key: [] for key in TOPIC_LABELS}
    for utterance in utterances:
        person = people_by_id.get(utterance.person_id or "")
        if not person:
            continue
        row = {"utterance": utterance, "person": person}
        for topic in utterance.topics:
            if topic in by_topic:
                by_topic[topic].append(row)
    for rows in by_topic.values():
        rows.sort(
            key=lambda row: (
                row["utterance"].spoken_at,
                row["utterance"].source.get("url", ""),
                -row["utterance"].order,
            ),
            reverse=True,
        )
    summaries = [
        {"key": key, "label": label, "count": len(by_topic[key])}
        for key, label in TOPIC_LABELS.items()
    ]
    return summaries, by_topic


def _stance_histories(
    person_id: str,
    stances: list[Stance],
    utterances: list[Utterance],
    axes: list[StanceAxis],
) -> list[dict]:
    utterance_by_id = {utterance.utterance_id: utterance for utterance in utterances}
    grouped: dict[str, list[Stance]] = defaultdict(list)
    for stance in select_best_stances(stances, utterances):
        if stance.person_id == person_id:
            grouped[stance.axis].append(stance)

    histories = []
    for axis in axes:
        items = grouped.get(axis.key, [])
        if not items:
            continue
        items.sort(
            key=lambda stance: (
                utterance_by_id[stance.utterance_id].spoken_at,
                utterance_by_id[stance.utterance_id].order,
                stance.stance_id,
            )
        )
        histories.append(
            {
                "axis": axis,
                "points": [
                    {
                        "stance": stance,
                        "utterance": utterance_by_id[stance.utterance_id],
                        "position": round((stance.value + 1) * 50, 3),
                        "value_label": f"{stance.value:+.1f}",
                    }
                    for stance in items
                ],
            }
        )
    return histories


def _approved_stance_changes(
    person_id: str,
    reviews: list[ReviewItem],
    stances: list[Stance],
    utterances: list[Utterance],
    axes: list[StanceAxis],
) -> list[dict]:
    stance_by_id = {stance.stance_id: stance for stance in stances}
    utterance_by_id = {utterance.utterance_id: utterance for utterance in utterances}
    axes_by_key = {axis.key: axis for axis in axes}
    changes = []
    for review in reviews:
        payload = review.payload
        if (
            review.kind != "stance_change"
            or review.status != "approved"
            or payload.get("person_id") != person_id
        ):
            continue
        before = stance_by_id.get(payload.get("before_stance_id"))
        after = stance_by_id.get(payload.get("after_stance_id"))
        if not before or not after:
            continue
        before_utterance = utterance_by_id.get(before.utterance_id)
        after_utterance = utterance_by_id.get(after.utterance_id)
        axis = axes_by_key.get(payload.get("axis"))
        if not before_utterance or not after_utterance or not axis:
            continue
        changes.append(
            {
                "review": review,
                "axis": axis,
                "before": {"stance": before, "utterance": before_utterance},
                "after": {"stance": after, "utterance": after_utterance},
            }
        )
    changes.sort(key=lambda change: change["after"]["utterance"].spoken_at, reverse=True)
    return changes


def _consistency_by_person(
    pairs: list[ConsistencyPair],
    bills: list[Bill],
    votes: list[Vote],
    utterances: list[Utterance],
    stances: list[Stance],
    axes: list[StanceAxis],
) -> dict[str, dict]:
    bill_by_id = {bill.bill_id: bill for bill in bills}
    vote_by_id = {vote.vote_id: vote for vote in votes}
    utterance_by_id = {utterance.utterance_id: utterance for utterance in utterances}
    stance_by_id = {stance.stance_id: stance for stance in stances}
    axis_by_key = {axis.key: axis for axis in axes}
    rows_by_person: dict[str, list[dict]] = defaultdict(list)
    for pair in pairs:
        bill = bill_by_id.get(pair.bill_id)
        vote = vote_by_id.get(pair.vote_id)
        utterance = utterance_by_id.get(pair.utterance_id)
        stance = stance_by_id.get(pair.stance_id)
        axis = axis_by_key.get(pair.axis)
        if not all((bill, vote, utterance, stance, axis)):
            continue
        rows_by_person[pair.person_id].append(
            {
                "pair": pair,
                "bill": bill,
                "vote": vote,
                "utterance": utterance,
                "stance": stance,
                "axis": axis,
                "value_label": f"{pair.stance_value:+.1f}",
                "result_label": "일치" if pair.consistent else "불일치",
            }
        )

    result = {}
    for person_id, rows in rows_by_person.items():
        rows.sort(
            key=lambda row: (
                row["vote"].voted_at,
                row["bill"].assembly_bill_no,
                row["pair"].consistency_id,
            ),
            reverse=True,
        )
        consistent = sum(int(row["pair"].consistent) for row in rows)
        result[person_id] = {
            "consistent": consistent,
            "eligible": len(rows),
            "percentage": round(consistent / len(rows) * 100, 1),
            "rows": rows,
        }
    return result


_PLEDGE_STATUS_ORDER = ("이행", "부분 이행", "미이행", "검증 불가")


def _pledges_by_person(pledges: list[Pledge]) -> dict[str, dict]:
    grouped: dict[str, list[Pledge]] = defaultdict(list)
    for pledge in pledges:
        grouped[pledge.person_id].append(pledge)

    summaries = {}
    for person_id, rows in grouped.items():
        rows.sort(key=lambda pledge: (pledge.current_status, pledge.text, pledge.pledge_id))
        summaries[person_id] = {
            "counts": [
                {
                    "status": status,
                    "count": sum(pledge.current_status == status for pledge in rows),
                }
                for status in _PLEDGE_STATUS_ORDER
            ],
            "rows": rows,
        }
    return summaries


_PREDICTION_STATUS_LABELS = {
    "open": "진행 중",
    "correct": "적중",
    "incorrect": "빗나감",
    "unresolvable": "판정 불가",
}


def _predictions_by_person(
    predictions: list[Prediction], utterances: list[Utterance]
) -> dict[str, dict]:
    utterance_by_id = {utterance.utterance_id: utterance for utterance in utterances}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for prediction in predictions:
        utterance = utterance_by_id.get(prediction.utterance_id)
        if not utterance or utterance.person_id != prediction.person_id:
            continue
        grouped[prediction.person_id].append(
            {
                "prediction": prediction,
                "utterance": utterance,
                "status_label": _PREDICTION_STATUS_LABELS[prediction.status],
            }
        )

    summaries = {}
    for person_id, rows in grouped.items():
        rows.sort(
            key=lambda row: (
                row["prediction"].deadline,
                row["prediction"].prediction_id,
            ),
            reverse=True,
        )
        open_rows = [row for row in rows if row["prediction"].status == "open"]
        resolved_rows = [row for row in rows if row["prediction"].status != "open"]
        summaries[person_id] = {
            "correct": sum(
                row["prediction"].status == "correct" for row in resolved_rows
            ),
            "resolved": len(resolved_rows),
            "open_rows": open_rows,
            "resolved_rows": resolved_rows,
        }
    return summaries


def _factchecks_by_utterance(
    factchecks: list[FactCheckLink],
) -> dict[str, list[FactCheckLink]]:
    grouped: dict[str, list[FactCheckLink]] = defaultdict(list)
    for factcheck in factchecks:
        grouped[factcheck.utterance_id].append(factcheck)
    for rows in grouped.values():
        rows.sort(key=lambda row: (row.checked_at, row.organization, row.url), reverse=True)
    return grouped


def _corrections_by_target(
    corrections: list[Correction],
) -> dict[str, list[Correction]]:
    grouped: dict[str, list[Correction]] = defaultdict(list)
    for correction in corrections:
        grouped[correction.target_id].append(correction)
    for rows in grouped.values():
        rows.sort(key=lambda row: (row.requested_at, row.correction_id), reverse=True)
    return grouped


def _channel_url(channel_ref: str) -> str | None:
    if channel_ref.startswith(("https://", "http://")):
        return channel_ref
    match = re.search(r"#(\d+)", channel_ref)
    return f"{_REPOSITORY_URL}/issues/{match.group(1)}" if match else None


def _correction_page_rows(
    corrections: list[Correction],
    people: list[Person],
    utterances: list[Utterance],
    stances: list[Stance],
    axes: list[StanceAxis],
    pledges: list[Pledge],
    predictions: list[Prediction],
) -> list[dict]:
    people_by_id = {person.person_id: person for person in people}
    utterance_by_id = {utterance.utterance_id: utterance for utterance in utterances}
    axis_by_key = {axis.key: axis for axis in axes}
    routes: dict[tuple[str, str], dict] = {}
    for utterance in utterances:
        person = people_by_id.get(utterance.person_id or "")
        if person:
            routes[("utterance", utterance.utterance_id)] = {
                "href": f"person/{person.person_id}.html#{utterance.utterance_id}",
                "label": f"{person.name} · {utterance.spoken_at} 발언",
            }
    for stance in stances:
        utterance = utterance_by_id.get(stance.utterance_id)
        person = people_by_id.get(stance.person_id)
        axis = axis_by_key.get(stance.axis)
        if utterance and person:
            routes[("stance", stance.stance_id)] = {
                "href": f"person/{person.person_id}.html#{utterance.utterance_id}",
                "label": f"{person.name} · {axis.label if axis else stance.axis} 입장",
            }
    for pledge in pledges:
        person = people_by_id.get(pledge.person_id)
        if person:
            routes[("pledge", pledge.pledge_id)] = {
                "href": f"person/{person.person_id}.html#{pledge.pledge_id}",
                "label": f"{person.name} · 공약",
            }
    for prediction in predictions:
        person = people_by_id.get(prediction.person_id)
        if person:
            routes[("prediction", prediction.prediction_id)] = {
                "href": f"person/{person.person_id}.html#{prediction.prediction_id}",
                "label": f"{person.name} · 예측성 발언",
            }

    rows = [
        {
            "correction": correction,
            "target": routes.get((correction.target_kind, correction.target_id)),
            "channel_url": _channel_url(correction.channel_ref),
        }
        for correction in corrections
    ]
    rows.sort(
        key=lambda row: (
            row["correction"].requested_at,
            row["correction"].correction_id,
        ),
        reverse=True,
    )
    return rows


def _extractor_versions(
    utterances: list[Utterance],
    stances: list[Stance],
    bill_links: list[UtteranceBillLink],
    reviews: list[ReviewItem],
) -> list[dict]:
    versions = {
        ("주제", "rules", "deterministic", "topic_rules_v1"),
        ("주제", "claude", "실행 시 선택", "topic_v1"),
        ("입장", "rules", "deterministic", "stance_rules_v2"),
        ("입장", "claude", "실행 시 선택", "stance_v1"),
        ("발언·의안", "rules", "deterministic", "bill_link_rules_v1"),
        ("발언·의안", "claude", "실행 시 선택", "bill_link_v1"),
        ("예측 후보", "rules", "deterministic", "prediction_rules_v2"),
        ("예측 후보", "claude", "실행 시 선택", "prediction_v1"),
    }
    for utterance in utterances:
        if utterance.topic_model and utterance.topic_prompt_version:
            backend = "rules" if utterance.topic_model == "deterministic" else "claude"
            versions.add(
                (
                    "주제",
                    backend,
                    utterance.topic_model,
                    utterance.topic_prompt_version,
                )
            )
    for stance in stances:
        extractor = stance.extractor
        versions.add(
            (
                "입장",
                extractor["backend"],
                extractor["model"],
                extractor["prompt_version"],
            )
        )
    for link in bill_links:
        extractor = link.extractor
        versions.add(
            (
                "발언·의안",
                extractor["backend"],
                extractor["model"],
                extractor["prompt_version"],
            )
        )
    for review in reviews:
        extractor = review.payload.get("extractor")
        if review.kind != "prediction" or not isinstance(extractor, dict):
            continue
        if {"backend", "model", "prompt_version"}.issubset(extractor):
            versions.add(
                (
                    "예측 후보",
                    extractor["backend"],
                    extractor["model"],
                    extractor["prompt_version"],
                )
            )
    return [
        {
            "stage": stage,
            "backend": backend,
            "model": model,
            "prompt_version": prompt_version,
        }
        for stage, backend, model, prompt_version in sorted(versions)
    ]


def build_site(
    people: list[Person],
    utterances: list[Utterance],
    out_dir: str | Path,
    *,
    stances: list[Stance] | None = None,
    stance_axes: list[StanceAxis] | None = None,
    reviews: list[ReviewItem] | None = None,
    bills: list[Bill] | None = None,
    votes: list[Vote] | None = None,
    consistency_pairs: list[ConsistencyPair] | None = None,
    pledges: list[Pledge] | None = None,
    predictions: list[Prediction] | None = None,
    factchecks: list[FactCheckLink] | None = None,
    corrections: list[Correction] | None = None,
    bill_links: list[UtteranceBillLink] | None = None,
    audit_report: dict | None = None,
) -> BuildStats:
    out = Path(out_dir)
    (out / "person").mkdir(parents=True, exist_ok=True)
    env = _env()
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    stances = stances or []
    stance_axes = stance_axes or []
    reviews = reviews or []
    bills = bills or []
    votes = votes or []
    consistency_pairs = consistency_pairs or []
    pledges = pledges or []
    predictions = predictions or []
    factchecks = factchecks or []
    corrections = corrections or []
    bill_links = bill_links or []
    consistency = _consistency_by_person(
        consistency_pairs, bills, votes, utterances, stances, stance_axes
    )
    pledge_summaries = _pledges_by_person(pledges)
    prediction_summaries = _predictions_by_person(predictions, utterances)
    factchecks_by_utterance = _factchecks_by_utterance(factchecks)
    corrections_by_target = _corrections_by_target(corrections)
    if audit_report is None:
        audit_report = build_balance_report(
            people,
            utterances,
            stances,
            reviews,
            as_of=generated_at[:10],
        )

    by_person: dict[str, list[Utterance]] = defaultdict(list)
    unmatched = 0
    for u in utterances:
        if u.person_id:
            by_person[u.person_id].append(u)
        else:
            unmatched += 1

    # 인물 페이지
    person_tpl = env.get_template("person.html")
    rendered_utterances = 0
    for person in people:
        person_utts = by_person.get(person.person_id, [])
        rendered_utterances += len(person_utts)
        html = person_tpl.render(
            root="../",
            person=person,
            groups=_timeline_groups(person_utts),
            utterance_count=len(person_utts),
            topic_labels=TOPIC_LABELS,
            stance_histories=_stance_histories(
                person.person_id, stances, person_utts, stance_axes
            ),
            stance_changes=_approved_stance_changes(
                person.person_id, reviews, stances, utterances, stance_axes
            ),
            consistency=consistency.get(person.person_id),
            pledge_summary=pledge_summaries.get(person.person_id),
            prediction_summary=prediction_summaries.get(person.person_id),
            factchecks_by_utterance=factchecks_by_utterance,
            corrections_by_target=corrections_by_target,
            generated_at=generated_at,
        )
        (out / "person" / f"{person.person_id}.html").write_text(html, encoding="utf-8")

    # 인덱스 (인물 목록 + 클라이언트 검색)
    index_rows = sorted(
        (
            {"person": p, "count": len(by_person.get(p.person_id, []))}
            for p in people
        ),
        key=lambda r: (-r["count"], r["person"].name),
    )
    topic_summaries, utterances_by_topic = _topic_rows(people, utterances)
    index_html = env.get_template("index.html").render(
        root="",
        rows=index_rows,
        topics=topic_summaries,
        total_utterances=len(utterances),
        generated_at=generated_at,
    )
    (out / "index.html").write_text(index_html, encoding="utf-8")

    about_html = env.get_template("about.html").render(
        root="",
        correction_form_url=_CORRECTION_FORM_URL,
        generated_at=generated_at,
    )
    (out / "about.html").write_text(about_html, encoding="utf-8")

    correction_html = env.get_template("corrections.html").render(
        root="",
        rows=_correction_page_rows(
            corrections,
            people,
            utterances,
            stances,
            stance_axes,
            pledges,
            predictions,
        ),
        correction_form_url=_CORRECTION_FORM_URL,
        generated_at=generated_at,
    )
    (out / "corrections.html").write_text(correction_html, encoding="utf-8")

    methodology_html = env.get_template("methodology.html").render(
        root="",
        axes=stance_axes,
        audit=audit_report,
        extractor_versions=_extractor_versions(
            utterances, stances, bill_links, reviews
        ),
        generated_at=generated_at,
    )
    (out / "methodology.html").write_text(methodology_html, encoding="utf-8")

    analysis_dir = out / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    ai_html = env.get_template("ai_analysis.html").render(
        root="../",
        analysis=build_ai_analysis(people, utterances),
        generated_at=generated_at,
    )
    (analysis_dir / "ai.html").write_text(ai_html, encoding="utf-8")

    search_shards = _write_search_shards(out, people, utterances)
    search_html = env.get_template("search.html").render(
        root="", search_shards=search_shards, generated_at=generated_at
    )
    (out / "search.html").write_text(search_html, encoding="utf-8")

    topic_dir = out / "topic"
    topic_dir.mkdir(parents=True, exist_ok=True)
    topic_tpl = env.get_template("topic.html")
    for topic in topic_summaries:
        rows = utterances_by_topic[topic["key"]]
        topic_people = sorted(
            {row["person"].person_id: row["person"] for row in rows}.values(),
            key=lambda person: person.name,
        )
        html = topic_tpl.render(
            root="../",
            topic=topic,
            rows=rows,
            people=topic_people,
            factchecks_by_utterance=factchecks_by_utterance,
            corrections_by_target=corrections_by_target,
            generated_at=generated_at,
        )
        (topic_dir / f"{topic['key']}.html").write_text(html, encoding="utf-8")

    return BuildStats(
        people_pages=len(people),
        utterances_rendered=rendered_utterances,
        unmatched_utterances=unmatched,
    )
