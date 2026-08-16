# 발언록 (politics-tracker)

> 정치인의 말을 시간축 위에 기록하고, 출처와 함께 공개하고, 검증 가능한 지표로만 평가한다.

국내 주요 정치인·관료·국회의원의 발언을 추적해 보여주는 공개 사이트 프로젝트입니다.
전체 설계는 [docs/design.md](docs/design.md)를 보세요.

현재 상태: **Phase 2 완료 + Phase 3 진행 중**. 국회 회의록 파싱 → 발언 추출 →
화자 매칭 → 주제·정책 입장 분류 → 인간 검수 큐 → 본회의 표결 수집 → 말과 표결
일치 기록 → 정적 사이트까지의 전체 루프가 동작합니다. 기본 경로는 LLM 없이
돌아가고, 주제·입장·의안 후보 분류에 Claude 백엔드를 선택할 수 있습니다.

## 빠른 시작 (네트워크·API 키 불필요)

Python 3.10 이상이 필요합니다. macOS의 기본 `python3`가 3.9인 환경에서는
Homebrew 등으로 설치한 `python3.11`을 사용하세요.

```bash
python3.11 -m venv .venv && . .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
politics-tracker quickstart --out ./quickstart_out
# quickstart_out/site/index.html 을 브라우저로 열기
```

번들된 **가상 샘플**(허구의 인물 3명 + 허구의 본회의 회의록)로 수집→파싱→매칭→사이트
생성 루프 전체를 재현합니다. 샘플의 인물·발언은 모두 지어낸 것입니다.

## 실데이터 연결

1. [열린국회정보](https://open.assembly.go.kr)에서 무료 API 키 발급 → `ASSEMBLY_API_KEY` 환경변수로 설정
2. 검증된 기본 서비스 ID와 필드는 [docs/api-notes.md](docs/api-notes.md)에서 확인

```bash
# 0. 연결·서비스ID·필드매핑 검증 (가장 먼저 실행 — 아래 "검증" 절 참고)
politics-tracker verify-api --era 22

# 1. 의원 명부 수집 (22대)
politics-tracker fetch-members --era 22

# 2-a. 본회의 회의록 자동 수집: 목록 조회 → 구조화 원문 다운로드 → 발언 추출 → 병합
politics-tracker fetch-minutes --era 22 --year 2026 --list-only --limit 5
politics-tracker fetch-minutes --era 22 --year 2026 --limit 10

# 위원회 회의록은 venue-type만 바꾸면 검증된 위원회 서비스 ID를 사용
politics-tracker fetch-minutes --venue-type assembly_committee \
  --era 22 --year 2026 --list-only --limit 5

# 2-b. 또는 수동: 내려받은 텍스트 파일에서 발언 추출
politics-tracker parse-minutes ./minutes/2026-07-15-plenary.txt \
  --date 2026-07-15 \
  --session "제418회 국회(정기회) 제3차 본회의" \
  --source-url "https://likms.assembly.go.kr/record/..."

# 2-c. 최근 본회의 표결 20개 의안 수집. 특정 의안은 --bill-id로 재현
politics-tracker fetch-votes --era 22 --limit 20

# 3. 주제 분류 — 기본은 키워드 규칙(오프라인·결정적)
politics-tracker classify-topics
# LLM 백엔드 (pip install -e ".[llm]" + ANTHROPIC_API_KEY 필요, 저신뢰는 보류 처리)
politics-tracker classify-topics --backend claude

# 4. 정책 축 입장 추출. 기본은 보수적 문구 규칙, Claude는 구조화 출력 사용
politics-tracker extract-stances --backend rules
# politics-tracker extract-stances --backend claude
politics-tracker detect-stance-changes

# 4-b. 발언과 의안 연결. Claude 후보는 전건 검수 큐로 이동
politics-tracker link-bills --backend rules
# politics-tracker link-bills --backend claude
politics-tracker compute-consistency

# 5. 정적 사이트 생성
politics-tracker build-site --out ./site_out
```

운영 저장소 기본값은 `data/db.sqlite`입니다. 기존 JSONL 저장소를 옮기거나 교환용
백업을 만들 때는 다음 명령을 사용합니다.

```bash
politics-tracker migrate-store --store data/store --db data/db.sqlite
politics-tracker export-jsonl --db data/db.sqlite --out data/export
```

Claude 분류에서 신뢰도 임계값을 넘지 못한 결과는 자동 공개하지 않고 검수 큐에
적재합니다. 현재 큐는 다음처럼 처리합니다.

```bash
politics-tracker review list --kind topic
politics-tracker review show rev_...
politics-tracker review approve rev_... --edit topics=housing,economy --note "원문 대조 완료"
politics-tracker review reject rev_... --note "근거 부족"
```

입장 변화 후보는 맥락 주석을 넣어 승인합니다.

```bash
politics-tracker review approve rev_... \
  --edit context_note="당적 변경 전후 발언" --note "두 원문 확인"
```

한 번 승인·기각한 검수 결정은 수정할 수 없습니다.

`fetch-minutes`는 국회회의록시스템의 구조화 HTML에서 서버가 표시한 화자·직함·발언을
읽습니다. 구조화 HTML이 없는 과거 문서는 PDF/텍스트 규칙 파서로 폴백합니다.
다운로드한 원문은 `data/raw/minutes/`에 스냅샷으로 보관하고 발언의
`source.archived_snapshot`에 경로를 기록합니다 (링크 부패 대비).

### 검증 (verify-api)

`verify-api`는 ① API 접속/인증 ② `normalize_member` 필드 매핑 커버리지
③ 현역 총원(295~305명)과 대수 표기를 순서대로 점검합니다. 현역 의원 기본 서비스는
대수 필터 없이 현재 재직 의원만 반환합니다.

GitHub Actions에서 수집할 때는 저장소 Actions Secret에 `ASSEMBLY_API_KEY`를
등록하고 키 값을 코드·로그·커밋에 넣지 마세요.

## 구조

```text
politics_tracker/
├── models.py              Person / Utterance / ReviewItem — 출처·검수 불변성 강제
├── storage.py             SQLite 운영 저장소 + JSONL 마이그레이션·교환 포맷
├── matching.py            화자→인물 매칭 (동명이인은 확정하지 않고 보류)
├── sources/
│   ├── minutes_parser.py  회의록 발언자 마커(◯) 규칙 파싱 — Phase 0의 핵심
│   ├── minutes_catalog.py 회의록 목록 조회·구조화 HTML 파싱 (PDF/텍스트 폴백)
│   └── assembly_api.py    열린국회정보 Open API 클라이언트
├── enrich/
│   └── topics.py          주제 분류 — rules(키워드) / claude(구조화 출력, 저신뢰 보류)
├── site/                  Jinja2 정적 사이트 빌더 (렌더 레이어는 추후 Next.js로 교체 가능)
└── samples/               가상 샘플 데이터 (quickstart용)
schemas/                   person / utterance JSON Schema
docs/design.md             전체 설계안 (데이터 소스, 평가 방법론, 법적 검토, 로드맵)
```

설계 원칙 요약 — 자세한 근거는 design.md:

1. 모든 발언에 원문 링크. 출처 없는 발언은 코드 레벨에서 거부된다.
2. 평가는 기계적으로 재현 가능한 지표로만. 종합 점수·랭킹은 만들지 않는다.
3. 화자 매칭은 확실할 때만. 오귀속보다 미귀속이 낫다.
4. 방법론·프롬프트·코드 전부 공개.

## 테스트

```bash
pytest
```

## 로드맵

- **Phase 0 (완료)**: 회의록 파싱 → 타임라인 정적 사이트. LLM 없음
- **Phase 1 (완료)**: 실 API 수집, 상임위 회의록, 주제 분류, 연도 샤드 검색,
  주제별 페이지, 인물·주제 필터, 발언 퍼머링크
- **Phase 2 (완료)**: 인간 검수 큐, 입장 추출·변화 감지, 표결 수집,
  근거를 펼쳐 보는 말–표 일치 기록
- **Phase 3 (진행 중)**: 공약 추적, 예측 채점, 팩트체크 연계, 정정 채널
- Phase 4: 공개 API, 비교 페이지 선거 모드

단계별 성공 기준은 [docs/design.md 12장](docs/design.md#12-로드맵),
Phase 3까지의 마일스톤별 상세 구현 계획(스키마, 수용 기준, 작업 순서)은
[docs/implementation-plan.md](docs/implementation-plan.md) 참고.
구현 계획서는 구현 담당 에이전트(GPT 5.6 Sol)가 단독으로 실행할 수 있도록 작성되어 있다.
