# 예측 판정 배포 데이터

`prediction propose`가 만든 후보를 사람이 검수하고 `prediction register`로 주장,
마감일, 판정 기준을 확정한 뒤 이 디렉터리의 YAML에 기록합니다. GitHub Pages 배포는
여기 있는 사람 확정 레코드만 가져옵니다. LLM 후보를 직접 넣거나 마감 전 판정하지
않습니다.

```yaml
predictions:
  - utterance_id: utt_...
    person_id: "열린국회정보 인물 코드"
    claim: "사람이 확정한 검증 가능한 주장"
    deadline: "2027-12-31"
    criteria: "마감 전에 고정한 구체적인 판정 기준"
    status: open
    resolution: null
    registered_by: human
    resolved_at: null
```

판정 완료 레코드는 `status`를 `correct`, `incorrect`, `unresolvable` 중 하나로 바꾸고
`resolution.evidence`에 공식 근거 URL과 설명을 넣습니다. `prediction_id`를 생략하면
발언 ID, 주장, 마감일로 결정적 ID를 만듭니다. 한 번 판정된 레코드의 수정은 거부되며,
잘못된 판정은 정정 레코드로만 고칩니다.
