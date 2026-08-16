from politics_tracker import cli
from politics_tracker.models import Person, Utterance
from politics_tracker.sources.votes import (
    DEFAULT_BILL_SERVICE_ID,
    DEFAULT_VOTE_SERVICE_ID,
    collect_votes,
    match_vote_person,
    normalize_bill,
    normalize_vote,
)
from politics_tracker.storage import SqliteStore, Store


def _bill_row(**updates):
    row = {
        "BILL_ID": "PRC_TEST_1",
        "BILL_NO": "2218438",
        "BILL_NAME": "세종특별자치시 설치 등에 관한 특별법 일부개정법률안",
        "PROPOSE_DT": "2026-04-01",
        "PROC_DT": "2026-04-23",
        "PROC_RESULT": "수정가결",
        "DETAIL_LINK": "http://likms.assembly.go.kr/bill/billDetail.do?billId=PRC_TEST_1",
    }
    row.update(updates)
    return row


def _vote_row(**updates):
    row = {
        "MONA_CD": "p1",
        "HG_NM": "이가상",
        "RESULT_VOTE_MOD": "찬성",
        "VOTE_DATE": "20260423 172604",
        "BILL_URL": "http://likms.assembly.go.kr/bill/billDetail.do?billId=PRC_TEST_1",
    }
    row.update(updates)
    return row


class FakeAPI:
    def __init__(self):
        self.calls = []

    def rows(self, service_id, **filters):
        self.calls.append((service_id, filters))
        if service_id == DEFAULT_BILL_SERVICE_ID:
            return iter(
                [
                    _bill_row(
                        BILL_ID="PRC_NOT_VOTED",
                        BILL_NO="2200001",
                        PROC_RESULT="대안반영폐기",
                    ),
                    _bill_row(),
                ]
            )
        if service_id == DEFAULT_VOTE_SERVICE_ID:
            return iter([_vote_row(), _vote_row(MONA_CD="missing", HG_NM="이가상")])
        return iter([])


def test_bill_vote_normalization_preserves_raw_and_source():
    bill = normalize_bill(_bill_row())
    assert bill.bill_id.startswith("bill_")
    assert bill.assembly_bill_no == "2218438"
    assert bill.link_url.startswith("https://")
    assert bill.raw["BILL_ID"] == "PRC_TEST_1"

    vote = normalize_vote(_vote_row(), bill, "p1")
    assert vote.vote_id.startswith("vote_")
    assert vote.decision == "찬성"
    assert vote.voted_at == "2026-04-23"
    assert vote.source == {
        "kind": "assembly_vote_api",
        "url": "https://likms.assembly.go.kr/bill/billDetail.do?billId=PRC_TEST_1",
    }


def test_vote_person_matching_prefers_code_and_holds_unknown_code():
    people = [Person(person_id="p1", name="이가상")]
    assert match_vote_person(_vote_row(), people) == ("p1", "api:MONA_CD")
    assert match_vote_person(_vote_row(MONA_CD="missing"), people) == (
        None,
        "held:unknown_MONA_CD",
    )
    assert match_vote_person(_vote_row(MONA_CD=""), people) == ("p1", "name:unique")


def test_collect_and_store_votes_is_deterministic(tmp_path):
    api = FakeAPI()
    people = [Person(person_id="p1", name="이가상")]
    result = collect_votes(api, people, era="22", limit=1)
    assert result.bills_scanned == 2
    assert result.bills_with_votes == 1
    assert len(result.votes) == 1
    assert result.unmatched_people == 1

    store = SqliteStore(tmp_path / "db.sqlite")
    assert store.upsert_bills(result.bills) == 1
    assert store.upsert_votes(result.votes) == 1
    assert store.upsert_bills(result.bills) == 0
    assert store.upsert_votes(result.votes) == 0
    assert store.load_bills() == result.bills
    assert store.load_votes(person_id="p1") == result.votes


def test_fetch_votes_cli_defaults_are_bounded():
    args = cli.build_parser().parse_args(["fetch-votes"])
    assert args.limit == 20
    assert args.db == "data/db.sqlite"
    assert args.bill_service_id == DEFAULT_BILL_SERVICE_ID
    assert args.vote_service_id == DEFAULT_VOTE_SERVICE_ID


def test_vote_jsonl_sqlite_roundtrip(tmp_path):
    bill = normalize_bill(_bill_row())
    vote = normalize_vote(_vote_row(), bill, "p1")
    jsonl = Store(tmp_path / "source")
    jsonl.save_people([Person(person_id="p1", name="이가상")])
    jsonl.save_utterances(
        [
            Utterance(
                utterance_id="utt_1",
                speaker_name="이가상",
                speaker_role="의원",
                spoken_at="2026-04-23",
                venue={"type": "assembly_plenary"},
                text="의안에 찬성합니다.",
                source={"kind": "assembly_minutes", "url": "https://example.invalid/1"},
                person_id="p1",
            )
        ]
    )
    jsonl.save_bills([bill])
    jsonl.save_votes([vote])

    db_path = tmp_path / "db.sqlite"
    assert cli.cmd_migrate_store(
        type("Args", (), {"store": str(jsonl.root), "db": str(db_path)})()
    ) == 0
    output = tmp_path / "output"
    assert cli.cmd_export_jsonl(
        type("Args", (), {"db": str(db_path), "out": str(output)})()
    ) == 0
    exported = Store(output)
    assert exported.load_bills() == [bill]
    assert exported.load_votes() == [vote]
