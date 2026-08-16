"""회의록 목록 조회와 원문 자동 다운로드 (Phase 1 수집 자동화).

흐름: 열린국회정보 API에서 회의록 목록 조회 → 국회회의록시스템의 구조화 HTML
원문 다운로드 → 발언 분리 → 저장소 병합. 구조화 HTML을 제공하지 않는 과거 문서는
PDF/텍스트 추출과 minutes_parser 규칙으로 폴백한다.

회의록 목록 데이터셋의 서비스 ID와 필드명은 포털에서 확인해야 한다.
필드명을 모르는 상태에서도 동작하도록 row 값 전체를 스캔해 URL과 날짜를
찾는 방어적 정규화를 쓰고, 원본 row는 항상 raw에 보존한다.
`fetch-minutes --list-only`로 실제 필드를 먼저 확인하는 것을 권장.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from io import BytesIO
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from .minutes_parser import Speech, parse_minutes_text

# 다운로드한 원문은 스냅샷으로 보관한다 (링크 부패 대비, utterance.source.archived_snapshot)
_UA = {
    "User-Agent": "politics-tracker/0.1 (+https://github.com/youngseongshin/politics-tracker)"
}

_DATE_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DATE_COMPACT_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_DATE_KEYS = ["CONF_DATE", "CONF_DT", "CONF_DT_YMD", "MEETING_DATE"]
_TITLE_KEYS = ["TITLE", "CONF_TTL", "CONF_NM", "MEETINGSESSION", "CLASS_NAME", "CONFER_NUM"]
_PDF_KEYS = ["PDF_LINK_URL", "DOWN_URL", "PDF_URL"]


@dataclass
class MinutesRecord:
    date: str | None  # "YYYY-MM-DD"
    title: str | None
    doc_url: str | None  # 사람이 읽고 파서가 처리하는 구조화 HTML 원문 우선
    pdf_url: str | None = None
    meeting_id: str | None = None
    committee: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _normalize_date(value: str) -> str | None:
    m = _DATE_ISO_RE.search(value)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = _DATE_COMPACT_RE.match(value.strip())
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def _as_view_url(url: str) -> str:
    """회의정보(summary) URL을 발언 구조가 있는 본문(view) URL로 바꾼다."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if parts.path.endswith("/xml.do") and query.get("id"):
        query["type"] = "view"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _first_string(row: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return None


def normalize_minutes_row(row: dict[str, Any]) -> MinutesRecord:
    """열린국회정보 row를 구조화 HTML 원문 중심으로 정규화한다.

    본회의·위원회 API는 안건별로 같은 회의를 여러 번 반환한다. ``meeting_id``는
    호출자가 회의 단위로 중복 제거할 때 사용한다. 원본 row는 항상 보존한다.
    """
    date = None
    explicit_date = _first_string(row, _DATE_KEYS)
    if explicit_date:
        date = _normalize_date(explicit_date)
    for value in row.values() if date is None else []:
        if isinstance(value, str):
            date = _normalize_date(value)
            if date:
                break

    urls = [v for v in row.values() if isinstance(v, str) and v.startswith(("http://", "https://"))]
    pdf_url = _first_string(row, _PDF_KEYS)
    if pdf_url is None:
        pdf_url = next((u for u in urls if "pdf" in u.lower() or "down" in u.lower()), None)

    summary_url = _first_string(row, ["CONF_LINK_URL", "VIEW_URL", "DETAIL_URL"])
    doc_url = _as_view_url(summary_url) if summary_url else None
    if doc_url is None and pdf_url:
        pdf_id = re.search(r"[?&]id=(\d+)", pdf_url)
        if pdf_id:
            doc_url = (
                "https://record.assembly.go.kr/assembly/viewer/minutes/xml.do"
                f"?id={pdf_id.group(1)}&type=view"
            )
    if doc_url is None:
        doc_url = pdf_url or (urls[0] if urls else None)

    title = _first_string(row, _TITLE_KEYS)

    return MinutesRecord(
        date=date,
        title=title,
        doc_url=doc_url,
        pdf_url=pdf_url,
        meeting_id=_first_string(row, ["CONF_ID", "CONFER_NUM"]),
        committee=_first_string(row, ["COMM_NAME", "CMIT_NM"]),
        raw=dict(row),
    )


def unique_minutes_records(rows: list[dict[str, Any]]) -> list[MinutesRecord]:
    """안건 단위 API row를 회의 단위로 결정적으로 중복 제거한다."""
    records: list[MinutesRecord] = []
    seen: set[str] = set()
    for row in rows:
        record = normalize_minutes_row(row)
        key = record.meeting_id or record.doc_url
        if not key:
            # 식별할 수 없는 레코드는 잘못 합치지 않고 개별 보존한다.
            key = f"unidentified:{len(records)}"
        if key in seen:
            continue
        seen.add(key)
        records.append(record)
    return records


def download_document(url: str, timeout: float = 60.0) -> bytes:
    resp = requests.get(url, headers=_UA, timeout=timeout)
    resp.raise_for_status()
    return resp.content


class _ViewerHTMLSpeechParser(HTMLParser):
    """국회회의록시스템의 ``div.speaker`` 블록만 읽는 최소 HTML 파서."""

    _VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.speaker_depth: int | None = None
        self.sub_depth: int | None = None
        self.current_name: str | None = None
        self.current_role: str | None = None
        self.current_lines: list[str] = []
        self.current_sub_parts: list[str] = []
        self.speeches: list[Speech] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in self._VOID_TAGS:
            self.depth += 1
        attr = {key: value or "" for key, value in attrs}
        classes = set(attr.get("class", "").split())

        if self.speaker_depth is None and tag == "div" and "speaker" in classes:
            name = attr.get("data-name", "").strip()
            if name:
                self.speaker_depth = self.depth
                self.current_name = name
                self.current_role = attr.get("data-pos", "").strip() or None
                self.current_lines = []
            return

        if self.speaker_depth is not None and "spk_sub" in classes:
            self.sub_depth = self.depth
            self.current_sub_parts = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # br/img 같은 빈 요소는 발언 텍스트에 별도 문자를 더하지 않는다.
        return

    def handle_data(self, data: str) -> None:
        if self.sub_depth is not None:
            self.current_sub_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.sub_depth == self.depth:
            line = " ".join("".join(self.current_sub_parts).split())
            if line:
                self.current_lines.append(line)
            self.sub_depth = None
            self.current_sub_parts = []

        if self.speaker_depth == self.depth:
            body = "\n".join(self.current_lines).strip()
            if self.current_name and body:
                self.speeches.append(
                    Speech(
                        speaker_name=self.current_name,
                        speaker_role=self.current_role,
                        text=body,
                        order=len(self.speeches),
                    )
                )
            self.speaker_depth = None
            self.current_name = None
            self.current_role = None
            self.current_lines = []

        self.depth = max(0, self.depth - 1)


def parse_viewer_html(text: str) -> list[Speech]:
    """국회회의록 뷰어 HTML에서 서버가 명시한 화자·직함·발언을 추출한다."""
    parser = _ViewerHTMLSpeechParser()
    parser.feed(text)
    parser.close()
    return parser.speeches


def extract_text(content: bytes) -> str:
    """PDF면 pypdf로, 아니면 텍스트로 디코딩한다 (국회 사이트는 EUC-KR도 씀)."""
    if content[:5] == b"%PDF-":
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    for encoding in ("utf-8", "cp949", "euc-kr"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def extract_speeches(content: bytes) -> list[Speech]:
    """구조화 HTML을 우선 사용하고, 과거 PDF/텍스트는 규칙 파서로 폴백한다."""
    text = extract_text(content)
    if "data-name=" in text and "speaker" in text and "spk_sub" in text:
        speeches = parse_viewer_html(text)
        if speeches:
            return speeches
    return parse_minutes_text(text)


def document_suffix(content: bytes) -> str:
    """스냅샷 확장자를 콘텐츠로 결정한다."""
    if content[:5] == b"%PDF-":
        return ".pdf"
    head = content[:4096].lower()
    if b"<!doctype html" in head or b"<html" in head:
        return ".html"
    return ".txt"
