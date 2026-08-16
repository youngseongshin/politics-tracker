"""politics-tracker CLI.

핵심 루프:
  수집(fetch-members / fetch-minutes) → 파싱 → 매칭 → 주제분류(classify-topics)
  → 게시(build-site)

quickstart는 번들된 가상 샘플로 위 루프 전체를 네트워크 없이 재현한다.
verify-api는 실제 API 키로 연결·필드매핑을 점검한다 (로컬에서 실행).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from .enrich.topics import TOPICS, classify_rules
from .matching import match_utterances
from .models import Person, ReviewItem, review_id_for
from .site.build import build_site
from .sources.assembly_api import (
    DEFAULT_COMMITTEE_MINUTES_SERVICE_ID,
    DEFAULT_MEMBER_SERVICE_ID,
    DEFAULT_PLENARY_MINUTES_SERVICE_ID,
)
from .sources.minutes_parser import parse_minutes_text, speeches_to_utterances
from .storage import SqliteStore, Store


DEFAULT_DB_PATH = "data/db.sqlite"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sample_text(name: str) -> str:
    return resources.files("politics_tracker").joinpath(f"samples/{name}").read_text(encoding="utf-8")


# -- commands -----------------------------------------------------------


def cmd_quickstart(args: argparse.Namespace) -> int:
    out = Path(args.out)
    people = [Person.from_dict(d) for d in json.loads(_sample_text("members.json"))]

    speeches = parse_minutes_text(_sample_text("minutes_sample.txt"))
    utterances = speeches_to_utterances(
        speeches,
        spoken_at="2026-07-15",
        venue={"type": "assembly_plenary", "session": "제400회 국회(임시회) 제1차 본회의 (가상)"},
        source={
            "kind": "assembly_minutes",
            "url": "https://likms.assembly.go.kr/record/#sample-fictional",
            "title": "가상 본회의 회의록 (데모)",
            "retrieved_at": _now_iso(),
        },
    )
    stats = match_utterances(utterances, people)
    topic_stats = classify_rules(utterances)

    store = SqliteStore(out / "data" / "db.sqlite")
    store.save_people(people)
    store.save_utterances(utterances)
    site_stats = build_site(people, utterances, out / "site")

    print(f"인물 {len(people)}명, 발언 {len(utterances)}건 (가상 샘플)")
    print(f"화자 매칭: 확정 {stats.matched} / 동명이인 보류 {stats.ambiguous} / 명부 외 {stats.unmatched}")
    print(f"주제 분류(rules): {topic_stats['with_topics']}/{topic_stats['total']}건에 주제 부여")
    print(f"사이트 생성: 인물 페이지 {site_stats.people_pages}개 -> {out / 'site' / 'index.html'}")
    print("브라우저로 열어 확인하세요. 실데이터 연결은 README의 fetch-members 절 참고.")
    return 0


def cmd_fetch_members(args: argparse.Namespace) -> int:
    from .sources.assembly_api import AssemblyOpenAPI, normalize_member

    api = AssemblyOpenAPI(api_key=args.key)
    filters = {}
    if args.era and args.era_param:
        # 현역 명부 기본 서비스는 필터가 없다. 역대 명부 등 커스텀 서비스에서만 지정한다.
        filters[args.era_param] = args.era

    rows = list(api.rows(args.service_id, **filters))
    people = [normalize_member(r) for r in rows]

    raw_out = Path(args.raw_out)
    raw_out.parent.mkdir(parents=True, exist_ok=True)
    raw_out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    store = SqliteStore(args.db)
    store.save_people(people)
    print(f"{len(people)}명 수집 → {store.db_path} (원본: {raw_out})")
    return 0


def cmd_parse_minutes(args: argparse.Namespace) -> int:
    text = Path(args.file).read_text(encoding="utf-8")
    speeches = parse_minutes_text(text)
    utterances = speeches_to_utterances(
        speeches,
        spoken_at=args.date,
        venue={"type": args.venue_type, "session": args.session},
        source={
            "kind": "assembly_minutes",
            "url": args.source_url,
            "title": args.session,
            "retrieved_at": _now_iso(),
        },
    )
    store = SqliteStore(args.db)
    stats = match_utterances(utterances, store.load_people())
    added = store.upsert_utterances(utterances)
    print(f"발언 {len(utterances)}건 파싱 (신규 {added}건) — "
          f"매칭 확정 {stats.matched} / 보류 {stats.ambiguous} / 명부 외 {stats.unmatched}")
    return 0


def cmd_fetch_minutes(args: argparse.Namespace) -> int:
    from .sources.assembly_api import AssemblyOpenAPI
    from .sources.minutes_catalog import (
        load_minutes_document,
        normalize_minutes_row,
    )

    api = AssemblyOpenAPI(api_key=args.key)
    service_id = args.service_id or (
        DEFAULT_COMMITTEE_MINUTES_SERVICE_ID
        if args.venue_type == "assembly_committee"
        else DEFAULT_PLENARY_MINUTES_SERVICE_ID
    )
    filters = dict(kv.split("=", 1) for kv in (args.filter or []))
    if service_id in {DEFAULT_PLENARY_MINUTES_SERVICE_ID, DEFAULT_COMMITTEE_MINUTES_SERVICE_ID}:
        filters.setdefault("DAE_NUM", args.era)
        filters.setdefault("CONF_DATE", args.year)

    records = []
    seen: set[str] = set()
    for row in api.rows(service_id, **filters):
        record = normalize_minutes_row(row)
        record_key = record.meeting_id or record.doc_url
        if record_key and record_key in seen:
            continue
        if record_key:
            seen.add(record_key)
        records.append(record)
        if args.limit and len(records) >= args.limit:
            break

    if args.list_only:
        for r in records:
            print(f"{r.date or '날짜미상':<12} {(r.title or '제목미상')[:50]:<52} {r.doc_url or 'URL 없음'}")
        print(f"\n총 {len(records)}건. 필드 확인: 첫 row 키 = {list(records[0].raw.keys()) if records else '없음'}")
        return 0

    store = SqliteStore(args.db)
    people = store.load_people()
    snapshot_dir = Path(args.snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    ok = skipped = 0
    for r in records:
        if not (r.date and r.doc_url):
            print(f"건너뜀 (날짜/URL 없음): {r.title or r.raw}", file=sys.stderr)
            skipped += 1
            continue
        try:
            speeches, snapshot, retrieved_at = load_minutes_document(r, snapshot_dir)
        except Exception as e:
            print(f"원문 검증 실패, 건너뜀: {r.title} ({e})", file=sys.stderr)
            skipped += 1
            continue

        venue = {"type": args.venue_type, "session": r.title or ""}
        if r.committee:
            venue["committee"] = r.committee
        source = {
            "kind": "assembly_minutes",
            "url": r.doc_url,
            "title": r.title,
            "retrieved_at": retrieved_at,
            "archived_snapshot": str(snapshot),
        }
        if r.pdf_url:
            source["pdf_url"] = r.pdf_url

        utterances = speeches_to_utterances(
            speeches,
            spoken_at=r.date,
            venue=venue,
            source=source,
        )
        match_utterances(utterances, people)
        added = store.upsert_utterances(utterances)
        print(f"{r.date} {r.title}: 발언 {len(utterances)}건 (신규 {added})")
        ok += 1

    print(f"\n완료: 회의록 {ok}건 수집, {skipped}건 건너뜀")
    return 0


def cmd_classify_topics(args: argparse.Namespace) -> int:
    store = SqliteStore(args.db)
    utterances = store.load_utterances()
    if not utterances:
        print("저장소에 발언이 없습니다.", file=sys.stderr)
        return 1

    if args.backend == "rules":
        stats = classify_rules(utterances)
    else:
        from .enrich.topics import classify_claude

        stats = classify_claude(
            utterances,
            model=args.model,
            batch_size=args.batch_size,
            confidence_threshold=args.confidence_threshold,
        )

    store.save_utterances(utterances)
    queued = 0
    if args.backend == "claude":
        for utterance in utterances:
            if not (utterance.topic_source or "").startswith("held:"):
                continue
            payload = {
                "topics": list(utterance.topics),
                "topic_source": utterance.topic_source,
            }
            review = ReviewItem(
                review_id=review_id_for(
                    "topic", utterance.utterance_id, utterance.topic_source or "held", payload
                ),
                kind="topic",
                target_id=utterance.utterance_id,
                payload=payload,
                reason=utterance.topic_source or "held",
                status="pending",
                created_at=_now_iso(),
            )
            queued += int(store.enqueue_review(review))
        stats["queued_for_review"] = queued
    print(f"주제 분류({args.backend}): {stats}")
    return 0


def cmd_verify_api(args: argparse.Namespace) -> int:
    """실제 API 키로 연결성·서비스 ID·필드 매핑을 점검한다. 로컬에서 실행."""
    from .sources.assembly_api import AssemblyAPIError, AssemblyOpenAPI, normalize_member

    print("[1/3] API 인증 및 접속 확인...")
    try:
        api = AssemblyOpenAPI(api_key=args.key)
        rows = api._fetch_page(args.service_id, page=1, page_size=5, filters={})
    except AssemblyAPIError as e:
        print(f"  실패: {e}")
        print("  → 키를 확인하거나, 포털에서 서비스 ID를 확인해 --service-id로 전달하세요.")
        return 1
    except Exception as e:
        print(f"  실패 (네트워크/기타): {e}")
        return 1
    if not rows:
        print(f"  접속은 되지만 '{args.service_id}' 데이터가 비어 있습니다. 서비스 ID를 확인하세요.")
        return 1
    print(f"  성공: 샘플 {len(rows)}건 수신")

    print("[2/3] 필드 매핑 검사...")
    person = normalize_member(rows[0])
    mapped = {
        "name": person.name, "person_id": person.person_id, "party": person.party,
        "district": person.district, "era": person.era, "committees": person.committees,
    }
    for key, value in mapped.items():
        marker = "OK " if value not in (None, [], "이름미상") else "MISS"
        print(f"  [{marker}] {key} = {value!r}")
    misses = [k for k, v in mapped.items() if v in (None, [], "이름미상")]
    if misses:
        print(f"  → 미매핑 필드 {misses}. 실제 row 키: {list(rows[0].keys())}")
        print("  → sources/assembly_api.py의 normalize_member 후보 키 목록에 추가하세요.")

    print("[3/3] 전체 수집 카운트...")
    filters = {args.era_param: args.era} if args.era and args.era_param else {}
    all_rows = list(api.rows(args.service_id, **filters))
    total = len(all_rows)
    print(f"  총 {total}명 (필터: {filters or '없음'})")
    count_ok = not args.era or 295 <= total <= 305
    if args.era and not args.era_param:
        print("  현역 명부 서비스는 대수 필터 없이 현재 재직 의원만 반환합니다.")
    if not count_ok:
        print("  → 현역 의원 수(295~305명)와 다릅니다. 서비스 ID 또는 필터를 확인하세요.")

    era_ok = True
    if args.era:
        normalized_members = [normalize_member(row) for row in all_rows]
        mismatched = [
            member.name
            for member in normalized_members
            if args.era not in (member.era or "")
        ]
        era_ok = not mismatched
        if mismatched:
            print(f"  → 대수 표기가 다른 레코드 {len(mismatched)}건 (예: {mismatched[:3]})")

    passed = not misses and count_ok and era_ok
    print("\n검증 완료." if passed else "\n검증 실패 (위 항목 보완 필요).")
    return 0 if passed else 1


def cmd_build_site(args: argparse.Namespace) -> int:
    store = SqliteStore(args.db)
    people = store.load_people()
    utterances = store.load_utterances()
    if not people:
        print("저장소에 인물이 없습니다. fetch-members 또는 quickstart를 먼저 실행하세요.", file=sys.stderr)
        return 1
    stats = build_site(people, utterances, args.out)
    print(f"인물 페이지 {stats.people_pages}개, 발언 {stats.utterances_rendered}건 렌더링 "
          f"(미귀속 {stats.unmatched_utterances}건) -> {Path(args.out) / 'index.html'}")
    return 0


def cmd_migrate_store(args: argparse.Namespace) -> int:
    source = Store(args.store)
    if not source.people_path.is_file() or not source.utterances_path.is_file():
        print(
            f"JSONL 저장소가 완전하지 않습니다: {source.people_path}, {source.utterances_path}",
            file=sys.stderr,
        )
        return 1
    people = source.load_people()
    utterances = source.load_utterances()
    reviews = source.load_reviews()
    stances = source.load_stances()
    target = SqliteStore(args.db)
    target.save_people(people)
    target.save_utterances(utterances)
    target.save_reviews(reviews)
    target.save_stances(stances)
    print(
        f"JSONL → SQLite 이관: 인물 {len(people)}명, 발언 {len(utterances)}건, "
        f"검수 {len(reviews)}건, 입장 {len(stances)}건 → {target.db_path}"
    )
    return 0


def cmd_export_jsonl(args: argparse.Namespace) -> int:
    source = SqliteStore(args.db)
    people = source.load_people()
    utterances = source.load_utterances()
    reviews = source.load_reviews()
    stances = source.load_stances()
    target = Store(args.out)
    target.save_people(people)
    target.save_utterances(utterances)
    target.save_reviews(reviews)
    target.save_stances(stances)
    print(
        f"SQLite → JSONL 내보내기: 인물 {len(people)}명, 발언 {len(utterances)}건, "
        f"검수 {len(reviews)}건, 입장 {len(stances)}건 → {target.root}"
    )
    return 0


def _review_edits(values: list[str] | None) -> dict:
    edits = {}
    for value in values or []:
        if "=" not in value:
            raise ValueError(f"--edit는 KEY=VALUE 형식이어야 합니다: {value}")
        key, raw = value.split("=", 1)
        if key == "topics":
            edits[key] = [item.strip() for item in raw.split(",") if item.strip()]
            continue
        try:
            edits[key] = json.loads(raw)
        except json.JSONDecodeError:
            edits[key] = raw
    return edits


def cmd_review_list(args: argparse.Namespace) -> int:
    store = SqliteStore(args.db)
    reviews = store.load_reviews(kind=args.kind, status=args.status)
    for review in reviews:
        print(
            f"{review.review_id}  {review.kind:<14} {review.status:<8} "
            f"{review.target_id}  {review.reason}"
        )
    print(f"총 {len(reviews)}건")
    return 0


def cmd_review_show(args: argparse.Namespace) -> int:
    review = SqliteStore(args.db).get_review(args.review_id)
    if review is None:
        print(f"검수 항목을 찾을 수 없습니다: {args.review_id}", file=sys.stderr)
        return 1
    print(json.dumps(review.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_review_approve(args: argparse.Namespace) -> int:
    store = SqliteStore(args.db)
    review = store.get_review(args.review_id)
    if review is None:
        print(f"검수 항목을 찾을 수 없습니다: {args.review_id}", file=sys.stderr)
        return 1
    if review.status != "pending":
        print(f"이미 결정된 검수 항목입니다: {review.status}", file=sys.stderr)
        return 1
    if review.kind != "topic":
        print(f"아직 승인 반영을 지원하지 않는 종류입니다: {review.kind}", file=sys.stderr)
        return 1

    try:
        edits = _review_edits(args.edit)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    payload = {**review.payload, **edits}
    topics = payload.get("topics")
    if not isinstance(topics, list) or not all(topic in TOPICS for topic in topics):
        print("topic 승인은 유효한 topics 목록이 필요합니다.", file=sys.stderr)
        return 1
    if len(topics) > 3:
        print("발언당 주제는 최대 3개입니다.", file=sys.stderr)
        return 1

    utterances = store.load_utterances()
    target = next(
        (utterance for utterance in utterances if utterance.utterance_id == review.target_id),
        None,
    )
    if target is None:
        print(f"대상 발언을 찾을 수 없습니다: {review.target_id}", file=sys.stderr)
        return 1
    target.topics = list(topics)
    target.topic_source = "human_reviewed"
    target.human_reviewed = True
    store.save_utterances(utterances)
    decided = store.decide_review(
        review.review_id,
        status="approved",
        decided_at=_now_iso(),
        note=args.note,
    )
    print(f"승인 완료: {decided.review_id} → {decided.target_id}")
    return 0


def cmd_review_reject(args: argparse.Namespace) -> int:
    store = SqliteStore(args.db)
    try:
        review = store.decide_review(
            args.review_id,
            status="rejected",
            decided_at=_now_iso(),
            note=args.note,
        )
    except (KeyError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"기각 완료: {review.review_id} → {review.target_id}")
    return 0


# -- parser -------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="politics-tracker", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    q = sub.add_parser("quickstart", help="가상 샘플로 수집→파싱→매칭→사이트 생성 전체 루프 실행")
    q.add_argument("--out", default="./quickstart_out")
    q.set_defaults(func=cmd_quickstart)

    f = sub.add_parser("fetch-members", help="열린국회정보 API에서 의원 명부 수집")
    f.add_argument("--key", default=None, help="API 키 (기본: ASSEMBLY_API_KEY 환경변수)")
    f.add_argument("--service-id", default=DEFAULT_MEMBER_SERVICE_ID,
                   help=f"열린국회정보 서비스 ID (기본 {DEFAULT_MEMBER_SERVICE_ID}=현역 의원 인적사항)")
    f.add_argument("--era", default=None, help="대수 필터 값 (예: 22)")
    f.add_argument("--era-param", default=None,
                   help="커스텀 명부 서비스의 대수 필터 파라미터명 (현역 기본 서비스는 불필요)")
    f.add_argument("--raw-out", default="data/raw/members.json")
    f.add_argument("--db", default=DEFAULT_DB_PATH)
    f.set_defaults(func=cmd_fetch_members)

    m = sub.add_parser("parse-minutes", help="회의록 텍스트 파일에서 발언 추출 후 저장소에 병합")
    m.add_argument("file", help="회의록 텍스트 파일 경로 (UTF-8)")
    m.add_argument("--date", required=True, help="회의 일자 YYYY-MM-DD")
    m.add_argument("--session", required=True, help='회의명 (예: "제418회 국회(정기회) 제3차 본회의")')
    m.add_argument("--source-url", required=True, help="회의록 원문 URL")
    m.add_argument("--venue-type", default="assembly_plenary",
                   help="assembly_plenary | assembly_committee | ...")
    m.add_argument("--db", default=DEFAULT_DB_PATH)
    m.set_defaults(func=cmd_parse_minutes)

    fm = sub.add_parser("fetch-minutes", help="회의록 목록 조회 → 원문 다운로드 → 발언 추출 → 저장소 병합")
    fm.add_argument("--service-id", default=None,
                    help="회의록 목록 서비스 ID (기본: venue-type에 따라 본회의/위원회 공식 서비스)")
    fm.add_argument("--key", default=None, help="API 키 (기본: ASSEMBLY_API_KEY 환경변수)")
    fm.add_argument("--era", default="22", help="국회 대수 (기본: 22)")
    fm.add_argument("--year", default=str(datetime.now(timezone.utc).year),
                    help="회의 연도 또는 날짜 검색어 (기본: 현재 연도)")
    fm.add_argument("--filter", action="append", metavar="KEY=VALUE",
                    help="API 필터 (반복 가능, 예: --filter DAE_NUM=22)")
    fm.add_argument("--limit", type=int, default=None, help="처리할 최대 회의록 수")
    fm.add_argument("--list-only", action="store_true",
                    help="다운로드 없이 목록과 필드만 출력 (서비스 ID·필드 확인용)")
    fm.add_argument("--venue-type", default="assembly_plenary")
    fm.add_argument("--snapshot-dir", default="data/raw/minutes", help="원문 스냅샷 보관 경로")
    fm.add_argument("--db", default=DEFAULT_DB_PATH)
    fm.set_defaults(func=cmd_fetch_minutes)

    c = sub.add_parser("classify-topics", help="저장소의 발언에 주제 태그 부여")
    c.add_argument("--backend", choices=["rules", "claude"], default="rules",
                   help="rules=키워드(기본, 오프라인) / claude=LLM 구조화 출력 (ANTHROPIC_API_KEY 필요)")
    c.add_argument("--model", default="claude-opus-5", help="claude 백엔드의 모델 ID")
    c.add_argument("--batch-size", type=int, default=20)
    c.add_argument("--confidence-threshold", type=float, default=0.6,
                   help="이 값 미만이면 주제를 붙이지 않고 보류")
    c.add_argument("--db", default=DEFAULT_DB_PATH)
    c.set_defaults(func=cmd_classify_topics)

    v = sub.add_parser("verify-api", help="실제 API 키로 접속·서비스ID·필드매핑 검증 (로컬 실행)")
    v.add_argument("--key", default=None, help="API 키 (기본: ASSEMBLY_API_KEY 환경변수)")
    v.add_argument("--service-id", default=DEFAULT_MEMBER_SERVICE_ID)
    v.add_argument("--era", default=None, help="대수 필터 값 (예: 22) — 지정 시 전체 카운트 검증")
    v.add_argument("--era-param", default=None,
                   help="커스텀 명부 서비스의 대수 필터 파라미터명")
    v.set_defaults(func=cmd_verify_api)

    b = sub.add_parser("build-site", help="저장소 데이터로 정적 사이트 생성")
    b.add_argument("--db", default=DEFAULT_DB_PATH)
    b.add_argument("--out", default="site_out")
    b.set_defaults(func=cmd_build_site)

    migrate = sub.add_parser("migrate-store", help="기존 JSONL 저장소를 SQLite로 이관")
    migrate.add_argument("--store", default="data/store", help="people.jsonl이 있는 기존 저장소")
    migrate.add_argument("--db", default=DEFAULT_DB_PATH)
    migrate.set_defaults(func=cmd_migrate_store)

    export = sub.add_parser("export-jsonl", help="SQLite를 JSONL 교환 포맷으로 내보내기")
    export.add_argument("--db", default=DEFAULT_DB_PATH)
    export.add_argument("--out", default="data/export")
    export.set_defaults(func=cmd_export_jsonl)

    review = sub.add_parser("review", help="저신뢰 산출물 검수 큐 관리")
    review_actions = review.add_subparsers(dest="review_command", required=True)

    review_list = review_actions.add_parser("list", help="검수 항목 목록")
    review_list.add_argument("--kind", default=None)
    review_list.add_argument(
        "--status", choices=["pending", "approved", "rejected"], default="pending"
    )
    review_list.add_argument("--db", default=DEFAULT_DB_PATH)
    review_list.set_defaults(func=cmd_review_list)

    review_show = review_actions.add_parser("show", help="검수 항목 상세")
    review_show.add_argument("review_id")
    review_show.add_argument("--db", default=DEFAULT_DB_PATH)
    review_show.set_defaults(func=cmd_review_show)

    review_approve = review_actions.add_parser("approve", help="검수 항목 승인·반영")
    review_approve.add_argument("review_id")
    review_approve.add_argument("--edit", action="append", metavar="KEY=VALUE")
    review_approve.add_argument("--note", default=None)
    review_approve.add_argument("--db", default=DEFAULT_DB_PATH)
    review_approve.set_defaults(func=cmd_review_approve)

    review_reject = review_actions.add_parser("reject", help="검수 항목 기각")
    review_reject.add_argument("review_id")
    review_reject.add_argument("--note", required=True)
    review_reject.add_argument("--db", default=DEFAULT_DB_PATH)
    review_reject.set_defaults(func=cmd_review_reject)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
