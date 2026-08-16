from io import BytesIO

from politics_tracker.sources.minutes_catalog import (
    document_suffix,
    extract_speeches,
    extract_text,
    normalize_minutes_row,
    parse_viewer_html,
    unique_minutes_records,
)


def test_normalize_official_row_prefers_structured_viewer_and_keeps_pdf():
    row = {
        "CONF_ID": "N054334",
        "CONF_DATE": "2026-07-30",
        "TITLE": "제22대 제437회 제3차 국회본회의",
        "CONF_LINK_URL": (
            "https://record.assembly.go.kr/assembly/viewer/minutes/xml.do"
            "?id=57050&type=summary"
        ),
        "PDF_LINK_URL": (
            "https://record.assembly.go.kr/assembly/viewer/minutes/download/pdf.do?id=57050"
        ),
    }
    r = normalize_minutes_row(row)
    assert r.date == "2026-07-30"
    assert r.title == "제22대 제437회 제3차 국회본회의"
    assert r.doc_url.endswith("xml.do?id=57050&type=view")
    assert r.pdf_url.endswith("pdf.do?id=57050")
    assert r.meeting_id == "N054334"
    assert r.raw == row  # 원본 보존


def test_normalize_row_unknown_field_names():
    row = {"X1": "20260715", "X2": "https://example.invalid/a", "X3": "회의명"}
    r = normalize_minutes_row(row)
    assert r.date == "2026-07-15"  # 압축 날짜 형식 정규화
    assert r.doc_url == "https://example.invalid/a"
    assert r.title is None  # 모르는 필드는 추측하지 않는다


def test_normalize_row_empty():
    r = normalize_minutes_row({"A": "값"})
    assert r.date is None and r.doc_url is None


def test_unique_minutes_records_deduplicates_agenda_rows_by_conf_id():
    base = {
        "CONF_ID": "N054334",
        "CONF_DATE": "2026-07-30",
        "TITLE": "제22대 제437회 제3차 국회본회의",
        "CONF_LINK_URL": "https://example.invalid/xml.do?id=57050&type=summary",
    }
    rows = [{**base, "SUB_NAME": "1. 첫 안건"}, {**base, "SUB_NAME": "2. 둘째 안건"}]
    records = unique_minutes_records(rows)
    assert len(records) == 1
    assert records[0].raw["SUB_NAME"] == "1. 첫 안건"


VIEWER_HTML = """
<!doctype html><html><body>
<div class="speaker spk_mem" data-name="홍길동" data-pos="의원">
  <div><input type="checkbox"><img src="portrait.png"></div>
  <div class="talk"><span class="spk_sub">&nbsp;첫 문장입니다.</span><br>
  <span class="spk_sub"><div>(박수)</div></span></div>
</div>
<div class="speaker spk_mem" data-name="김의장" data-pos="의장">
  <span class="spk_sub">회의를 시작하겠습니다.</span>
</div>
</body></html>
"""


def test_parse_viewer_html_uses_server_speaker_metadata():
    speeches = parse_viewer_html(VIEWER_HTML)
    assert [(s.speaker_name, s.speaker_role) for s in speeches] == [
        ("홍길동", "의원"),
        ("김의장", "의장"),
    ]
    assert speeches[0].text == "첫 문장입니다.\n(박수)"
    assert speeches[1].order == 1


def test_extract_speeches_prefers_html_and_falls_back_to_marker_text():
    assert len(extract_speeches(VIEWER_HTML.encode())) == 2
    plain = "◯홍길동 의원  발언 내용입니다.\n다음 줄입니다."
    speeches = extract_speeches(plain.encode())
    assert speeches[0].speaker_name == "홍길동"
    assert speeches[0].text == "발언 내용입니다.\n다음 줄입니다."


def test_document_suffix_detects_supported_snapshots():
    assert document_suffix(b"%PDF-1.7") == ".pdf"
    assert document_suffix(b"\n<!doctype html><html>") == ".html"
    assert document_suffix("회의록".encode()) == ".txt"


def test_extract_text_utf8():
    assert extract_text("◯이가상 의원  발언".encode("utf-8")) == "◯이가상 의원  발언"


def test_extract_text_cp949():
    # 국회 사이트 일부는 EUC-KR/CP949로 내려준다. cp949에는 ◯(U+25EF)가 없어
    # ○(U+25CB)로 표기되는데, 파서의 SPEAKER_MARKERS가 두 문자 모두 처리한다.
    assert extract_text("○이가상 의원  발언".encode("cp949")) == "○이가상 의원  발언"


def test_extract_text_pdf_dispatch():
    # pypdf 연동 스모크 테스트: 빈 페이지 PDF에서 예외 없이 빈 텍스트 추출
    from pypdf import PdfWriter

    buf = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(buf)

    assert extract_text(buf.getvalue()).strip() == ""
