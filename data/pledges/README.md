# 공약 수동 입력

이 디렉터리에는 선거관리위원회 정책공약집 등 공식 원문과 사람이 대조한 공약만
`*.yaml`로 추가합니다. 대상은 사이트가 추적하는 모든 인물이며, 확인이 끝난 인물부터
증분 등록합니다. PDF에서 자동 추출한 문장은 원문 대조 전에는 넣지 않습니다.

```yaml
pledges:
  - person_id: "열린국회정보 인물 코드"
    text: "공약 원문"
    source:
      kind: nec_pledge_book
      url: "https://공식-원문.example/pledge.pdf"
      title: "정책공약집 제목"
    criteria: "등록 뒤 바꾸지 않을 구체적인 이행 판정 기준"
    status_history:
      - status: 검증 불가
        decided_at: "2026-08-16"
        evidence:
          - url: "https://판정-근거.example/source"
            note: "현재 공개 자료만으로 이행 여부를 판정할 수 없음"
```

`pledge_id`를 생략하면 인물 ID, 공약 원문, 원문 URL로 결정적 ID를 만듭니다.
상태는 `이행`, `부분 이행`, `미이행`, `검증 불가` 중 하나이며 모든 판정에 근거 URL과
설명이 필요합니다.
