# Scoring feature definition (note, not an experiment sheet)

Status: pointer only. Decision D-23, task T-178.

- `icp_fit`, `signal_strength` and `territory_opportunity` keep their deterministic derivation in `libs/domain/score_features.py` as the permanent audited baseline, `source_version` `demo-1` (INV-18). It is never deleted or switched off.
- A formula change bumps `source_version` as `features-<major>.<minor>` in the same pull request. `tests/unit/test_score_features.py` pins the seed rows and a formula fingerprint per version.
- The production feature definition is L3 work (owner: second-backend-engineer). It needs a pre-registered experiment sheet (INV-19, `_TEMPLATE.md`) and runs in shadow next to this baseline.
- That work is not started. No `EXP-NNN` sheet exists yet.
