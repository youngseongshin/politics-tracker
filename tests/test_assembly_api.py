from argparse import Namespace

from politics_tracker import cli
from politics_tracker.sources import assembly_api
from politics_tracker.sources.assembly_api import (
    DEFAULT_MEMBER_SERVICE_ID,
    DEFAULT_PLENARY_MINUTES_SERVICE_ID,
    normalize_member,
)


def _member_row(index: int = 0) -> dict:
    return {
        "HG_NM": f"테스트{index}",
        "MONA_CD": f"CODE{index:04d}",
        "POLY_NM": "테스트당",
        "ORIG_NM": "비례대표",
        "CMITS": "교육위원회, 예산결산특별위원회",
        "UNITS": "제22대",
    }


def test_normalize_member_matches_verified_current_member_fields():
    person = normalize_member(_member_row())
    assert person.name == "테스트0"
    assert person.person_id == "CODE0000"
    assert person.party == "테스트당"
    assert person.district == "비례대표"
    assert person.era == "제22대"
    assert person.committees == ["교육위원회", "예산결산특별위원회"]


def test_cli_defaults_use_verified_service_ids():
    parser = cli.build_parser()
    members = parser.parse_args(["fetch-members", "--era", "22"])
    assert members.service_id == DEFAULT_MEMBER_SERVICE_ID
    assert members.era_param is None

    minutes = parser.parse_args(["fetch-minutes", "--limit", "5"])
    assert minutes.service_id is None
    assert minutes.era == "22"
    assert DEFAULT_PLENARY_MINUTES_SERVICE_ID == "nzbyfwhwaoanttzje"


def test_verify_api_passes_299_current_members_without_fake_era_filter(monkeypatch, capsys):
    rows = [_member_row(index) for index in range(299)]
    calls: list[dict] = []

    class FakeAPI:
        def __init__(self, api_key=None):
            pass

        def _fetch_page(self, service_id, page, page_size, filters):
            return rows[:5]

        def rows(self, service_id, **filters):
            calls.append(filters)
            yield from rows

    monkeypatch.setattr(assembly_api, "AssemblyOpenAPI", FakeAPI)
    args = Namespace(
        key=None,
        service_id=DEFAULT_MEMBER_SERVICE_ID,
        era="22",
        era_param=None,
    )
    assert cli.cmd_verify_api(args) == 0
    assert calls == [{}]
    assert "총 299명" in capsys.readouterr().out


def test_verify_api_rejects_out_of_range_member_count(monkeypatch):
    rows = [_member_row(index) for index in range(10)]

    class FakeAPI:
        def __init__(self, api_key=None):
            pass

        def _fetch_page(self, service_id, page, page_size, filters):
            return rows[:5]

        def rows(self, service_id, **filters):
            yield from rows

    monkeypatch.setattr(assembly_api, "AssemblyOpenAPI", FakeAPI)
    args = Namespace(
        key=None,
        service_id=DEFAULT_MEMBER_SERVICE_ID,
        era="22",
        era_param=None,
    )
    assert cli.cmd_verify_api(args) == 1
