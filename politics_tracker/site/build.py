"""정적 사이트 빌더.

Phase 0 원칙 (docs/design.md 10장): 데이터 갱신이 일 1회 배치이므로 전부 정적
HTML로 생성한다. 이 파이썬 빌더는 walking skeleton이며, Phase 1에서 Next.js
SSG로 렌더 레이어만 교체한다 — 데이터 레이어(JSONL/스키마)는 그대로 간다.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..enrich.topics import TOPIC_LABELS
from ..models import Person, Utterance

_TEMPLATE_DIR = Path(__file__).parent / "templates"


@dataclass
class BuildStats:
    people_pages: int
    utterances_rendered: int
    unmatched_utterances: int


_MAX_SEARCH_INDEX_BYTES = 5 * 1024 * 1024


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


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


def build_site(people: list[Person], utterances: list[Utterance], out_dir: str | Path) -> BuildStats:
    out = Path(out_dir)
    (out / "person").mkdir(parents=True, exist_ok=True)
    env = _env()
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

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

    about_html = env.get_template("about.html").render(root="", generated_at=generated_at)
    (out / "about.html").write_text(about_html, encoding="utf-8")

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
            generated_at=generated_at,
        )
        (topic_dir / f"{topic['key']}.html").write_text(html, encoding="utf-8")

    return BuildStats(
        people_pages=len(people),
        utterances_rendered=rendered_utterances,
        unmatched_utterances=unmatched,
    )
