# 정정 공개 기록

GitHub의 정정 요청 Issue를 접수하면 `correction add`로 기록하고 이 디렉터리의 YAML에
반영합니다. 처리 뒤에는 `correction resolve`로 결과를 한 번 확정하고 같은 YAML을
갱신합니다. 배포는 이 기록을 `corrections.html`과 해당 항목 주석에 표시합니다.

```yaml
corrections:
  - target_kind: utterance
    target_id: utt_...
    requested_at: "2026-08-16T09:00:00Z"
    request_summary: "요청 요지"
    channel: github_issue
    channel_ref: "issue #12"
    resolution: null
    resolved_at: null
    public_note: null
```

`correction_id`를 생략하면 대상, Issue 참조, 접수 시각으로 결정적 ID를 만듭니다.
접수 내용과 확정된 처리 결과는 수정하거나 삭제하지 않습니다. 잘못 기록한 경우에는
새 정정 기록을 추가합니다.
