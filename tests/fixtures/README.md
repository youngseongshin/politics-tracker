# Qualitative evidence recollection, 2026-10-16 KST

`ai_qualitative_evidence_20261016.json` contains the full parsed text of the 47
selected utterances, downloaded from the official meeting URLs in each record.
It is a regression fixture, not a replacement corpus or a collection cache.
The URLs were recovered from the last published report and fetched afresh.
Speaker, meeting heading, date, focus, and parsed order were checked against
the existing editorial notes. The report interpretation and review cutoff
remain unchanged.

## Why the build broke

`utterance_id_for` hashes the source URL and appends a positional speech index.
The current official HTML contains the same selected passages at different
positions in seven meetings. Twelve stored references are therefore stale.
This is source-content drift, not an ID-generation code change. The mismatch
is reproducible with the unchanged parser on current official documents.
We cannot establish exactly when or why the publisher changed the documents.

## Revalidated references

| Old ID | Current ID | Speaker | Official source |
| --- | --- | --- | --- |
| `utt_20260729_b4e5f588_0277` | `utt_20260729_b4e5f588_0273` | 구윤철 | https://record.assembly.go.kr/assembly/viewer/minutes/xml.do?id=57026&type=view |
| `utt_20260811_d6f1d2a9_0531` | `utt_20260811_d6f1d2a9_0530` | 허영 | https://record.assembly.go.kr/assembly/viewer/minutes/xml.do?id=57111&type=view |
| `utt_20260811_13285c00_0804` | `utt_20260811_13285c00_0807` | 이소영 | https://record.assembly.go.kr/assembly/viewer/minutes/xml.do?id=57076&type=view |
| `utt_20260812_4f9c01e8_1128` | `utt_20260812_4f9c01e8_1126` | 김종민 | https://record.assembly.go.kr/assembly/viewer/minutes/xml.do?id=57112&type=view |
| `utt_20260812_4f9c01e8_1170` | `utt_20260812_4f9c01e8_1168` | 장철민 | https://record.assembly.go.kr/assembly/viewer/minutes/xml.do?id=57112&type=view |
| `utt_20260407_b692a65c_0926` | `utt_20260407_b692a65c_0910` | 김재원 | https://record.assembly.go.kr/assembly/viewer/minutes/xml.do?id=56520&type=view |
| `utt_20260812_4f9c01e8_0827` | `utt_20260812_4f9c01e8_0826` | 박희승 | https://record.assembly.go.kr/assembly/viewer/minutes/xml.do?id=57112&type=view |
| `utt_20260408_f4e1b97f_1471` | `utt_20260408_f4e1b97f_1484` | 김용선 | https://record.assembly.go.kr/assembly/viewer/minutes/xml.do?id=56526&type=view |
| `utt_20260408_f4e1b97f_0854` | `utt_20260408_f4e1b97f_0869` | 정은경 | https://record.assembly.go.kr/assembly/viewer/minutes/xml.do?id=56526&type=view |
| `utt_20260812_41e4ea11_0383` | `utt_20260812_41e4ea11_0381` | 이주영 | https://record.assembly.go.kr/assembly/viewer/minutes/xml.do?id=57090&type=view |
| `utt_20260812_41e4ea11_0705` | `utt_20260812_41e4ea11_0707` | 차정인 | https://record.assembly.go.kr/assembly/viewer/minutes/xml.do?id=57090&type=view |
| `utt_20260407_b692a65c_0098` | `utt_20260407_b692a65c_0090` | 황정아 | https://record.assembly.go.kr/assembly/viewer/minutes/xml.do?id=56520&type=view |

## Checks and limitations

- All 47 selected passages still exist in the current official HTML.
- Twelve references changed; the other 35 IDs still resolve.
- The fixture is independent of the YAML, unlike the synthetic test helper.
- One focus occurs in speeches by two different people. Selection retained the
  originally cited speaker and ID; focus text alone is not an identity key.
- The existing missing-ID, quote-context, speaker-diversity and meeting-diversity
  checks are unchanged. Nothing was deleted or automatically reconnected.
- Later edits to the source can move positions again. Revalidation is still
  required; this patch does not change public IDs or silently heal stale links.
- This is not a complete current-year collection. The live Assembly API run
  and deployment still need to be checked after user review and merge.
