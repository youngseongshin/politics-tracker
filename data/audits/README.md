# 균형 감사

`politics-tracker audit-balance`는 현재 SQLite 자료에서 정당별 수집 건수, 화자 매칭
보류율, 주제·입장 보류율, 검수 승인율을 계산합니다. 사람이 대조한 표본이 있으면
`--sample-size`와 `--sample-errors`를 함께 넣고 대조일과 범위를
`--sample-checked-at`, `--sample-note`로 기록합니다. 배포 워크플로는 매번 현재 자료로
`balance-latest.json`을 만들고 방법론 페이지에 게시합니다.

표본 오류율은 사람이 실제로 대조한 결과만 입력합니다. 값이 없으면 `null`로 공개하며
0%로 간주하지 않습니다.
