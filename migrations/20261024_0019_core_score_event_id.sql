-- Expand: core.company keeps the id of the signal.scored event behind its stored score (T-198,
-- REQ-LNS-012, REQ-EVT-008, D-21).
--
-- core_app cannot read the ml schema (INV-01), so the worker records the id that ml puts in the
-- lead.score_updated stream entry next to the score, and the lead create sends it as the parent
-- of company.matched. Nullable, no default, no backfill: rows scored before this column stay
-- NULL until the next score event. The column inherits the row level security of core.company.
-- Additive and re-runnable.

ALTER TABLE core.company ADD COLUMN IF NOT EXISTS score_event_id uuid;
