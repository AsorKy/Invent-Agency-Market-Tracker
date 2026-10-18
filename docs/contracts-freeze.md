# Shared contracts freeze

Task T-014 freezes the shared contracts (REQ-EVT-005, REQ-EXT-001, REQ-EXT-004). Snapshots live
in `tests/unit/snapshots/` and are checked by `tests/unit/test_contract_snapshot.py`.

## What is frozen

| Snapshot | Content | Needs pydantic |
| --- | --- | --- |
| `settings_fields.json` | Field names of `Settings` in `libs/common/settings.py` (AST) | no |
| `event_types.json` | `EventType` and `ReservedEventType` names and values (AST) | no |
| `table_catalog.json` | Net schemas, tables and columns parsed from `migrations/*.sql` | no |
| `model_schemas.json` | `model_json_schema()` of every model in `libs/contracts`, keyed `module.Class`, plus event type values | yes |

`model_schemas.json` could not be generated offline. The test skips while pydantic is missing and
the file is absent. With pydantic installed and the file absent it fails, telling the coordinator
to generate and commit it.

## Regenerate

```
UPDATE_SNAPSHOTS=1 pytest tests/unit/test_contract_snapshot.py
```

Run it in CI or any environment with pydantic so that `model_schemas.json` is written too. Review
the diff of every snapshot before committing; a changed snapshot is a contract change.

## Freeze rule

After T-014:

- Edits to existing files in `libs/contracts/`, `libs/common/settings.py` and `migrations/` need
  the coordinator. A service task must not change them, nor regenerate their snapshots.
- Service tasks may add new modules under `libs/contracts/` and extend the snapshot for their own
  module only (new `module.Class` keys in `model_schemas.json`).
- Multiagent lanes start only after this task is done.
