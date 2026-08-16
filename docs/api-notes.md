# 열린국회정보 API 실측 기록

검증일: 2026-08-16 · 국회 제22대 · 로컬 네트워크에서 발급 키로 확인

이 문서는 `docs/implementation-plan.md`의 T1.1 결과다. 인증키 값은 기록하거나
커밋하지 않는다. 아래 서비스 ID와 필드는 열린국회정보의 공식 명세와 실제 JSON
응답을 함께 확인한 값이다.

## 1. 현역 국회의원 명부

| 항목 | 값 |
|---|---|
| 데이터셋 | 국회의원 인적사항 |
| 서비스 ID | `nwvrqwxyaytdsfvhu` |
| 요청 주소 | `https://open.assembly.go.kr/portal/openapi/nwvrqwxyaytdsfvhu` |
| 공식 명세 | `https://open.assembly.go.kr/portal/data/service/selectAPIServicePage.do/OWSSC6001134T516707` |
| 대수 필터 | 없음. 현재 재직 의원만 반환 |
| 2026-08-16 실측 | 299명 |

주요 출력 필드는 `HG_NM`(이름), `MONA_CD`(의원 코드), `POLY_NM`(정당),
`ORIG_NM`(선거구), `CMIT_NM`·`CMITS`(위원회), `UNITS`(당선 대수)다.
`verify-api --era 22`는 별도 대수 필터를 보내지 않고 총원 295~305명과 각 레코드의
`UNITS`에 `22`가 포함되는지를 검사한다.

`ALLNAMEMBER`는 역대 의원 3,295명을 반환하는 별도 데이터셋이다. 이 데이터셋에
`DAESU=22`를 보내도 필터가 적용되지 않았으므로 현역 명부 기본값으로 쓰지 않는다.

## 2. 본회의 회의록

| 항목 | 값 |
|---|---|
| 데이터셋 | 본회의 회의록 |
| 서비스 ID | `nzbyfwhwaoanttzje` |
| 요청 주소 | `https://open.assembly.go.kr/portal/openapi/nzbyfwhwaoanttzje` |
| 공식 명세 | `https://open.assembly.go.kr/portal/data/service/selectAPIServicePage.do/OO1X9P001017YF13038` |
| 필수 필터 | `DAE_NUM`(대수), `CONF_DATE`(연도 또는 날짜 검색어) |

주요 출력 필드는 `CONFER_NUM`, `TITLE`, `CLASS_NAME`, `DAE_NUM`, `CONF_DATE`,
`SUB_NAME`, `CONF_LINK_URL`, `PDF_LINK_URL`, `CONF_ID`다. `DAE_NUM=22`와
`CONF_DATE=2026` 조합으로 2026년 회의를 조회할 수 있음을 확인했다.

API는 회의가 아니라 안건 단위 row를 반환한다. 같은 `CONF_ID`와 URL이 안건 수만큼
반복되므로 수집기는 `CONF_ID` 기준으로 회의 단위 중복 제거를 한다. `TITLE`이 실제
회의명이고 `CONFER_NUM`은 숫자 회의번호이므로 제목 후보에서 `TITLE`을 우선한다.

## 3. 위원회 회의록

| 항목 | 값 |
|---|---|
| 데이터셋 | 위원회 회의록 |
| 서비스 ID | `ncwgseseafwbuheph` |
| 요청 주소 | `https://open.assembly.go.kr/portal/openapi/ncwgseseafwbuheph` |
| 공식 명세 | `https://open.assembly.go.kr/portal/data/service/selectAPIServicePage.do/OR137O001023MZ19321` |
| 필수 필터 | `DAE_NUM`, `CONF_DATE` |
| 선택 필터 | `TITLE`, `CLASS_NAME`, `COMM_NAME`, `SUB_NAME`, `DEPT_CD` |

본회의 필드에 `COMM_NAME`, `VODCOMM_CODE`, `PDF_FILE_ID`, `DEPT_CD`가 추가된다.
위원회명은 제목 추측보다 API의 `COMM_NAME`을 우선한다. 2026년 실응답에서
`외교통일위원회`, `상임위원회`, `CONF_ID=N054364`를 확인했다.

## 4. 원문 선택과 파싱

`CONF_LINK_URL`은 기본적으로 `type=summary` 회의정보 페이지를 가리킨다. 같은 공식
URL의 쿼리를 `type=view`로 바꾸면 회의록 본문을 제공한다. 본문 HTML은 각 발언을
다음처럼 서버 메타데이터로 구분한다.

```html
<div class="speaker spk_mem" data-name="조정식" data-pos="의장">
  <span class="spk_sub">발언 문장</span>
</div>
```

따라서 수집기는 `data-name`, `data-pos`, `span.spk_sub`를 직접 읽는다. 이 방식은
동일 회의의 PDF를 `pypdf`로 추출했을 때 발생한 공백·줄바꿈 소실을 피한다. 실측한
제22대 제437회 제3차 본회의에서 구조화 HTML은 발언 블록 84건을 추출했고, 같은
PDF에 기존 줄 시작 마커 규칙을 적용하면 2건만 추출됐다. 구조화 HTML을 사용할 수
없는 과거 문서에만 PDF·텍스트 규칙 파서를 폴백으로 사용한다.

사용자 출처 링크는 사람이 발언과 맥락을 확인할 수 있는 `type=view` URL로 저장하고,
`PDF_LINK_URL`도 source 메타데이터에 함께 보존한다. 원문 스냅샷 파일명은 URL의
SHA-256 앞 12자리로 결정해 재실행해도 동일하다.

## 5. 보조 상세 API

`VCONFDETAIL`은 `CONF_ID`를 받아 회기, 차수, 회의일자, 위원회명과 `DOWN_URL`을
반환한다. 목록 API에 필드가 없을 때 보완용으로 쓸 수 있으나 현재 본회의·위원회
기본 수집에는 추가 호출하지 않는다.
