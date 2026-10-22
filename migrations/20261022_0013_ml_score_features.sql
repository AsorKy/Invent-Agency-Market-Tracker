-- Expand: keep the score inputs on ml.lead_score (T-151, REQ-LNS-007).
--
-- The explanation route rebuilds drivers and the counterfactual from the three
-- features and the three weights a score was computed with. All columns are
-- nullable (exact decimals): rows written before this migration have no stored features and the
-- route answers 404 for them until the next score regeneration. Same table,
-- same grants, same policies; additive only.

ALTER TABLE ml.lead_score ADD COLUMN IF NOT EXISTS feature_icp_fit numeric(12,9);
ALTER TABLE ml.lead_score ADD COLUMN IF NOT EXISTS feature_signal_strength numeric(12,9);
ALTER TABLE ml.lead_score ADD COLUMN IF NOT EXISTS feature_territory_opportunity numeric(12,9);
ALTER TABLE ml.lead_score ADD COLUMN IF NOT EXISTS weight_icp_fit numeric(14,6);
ALTER TABLE ml.lead_score ADD COLUMN IF NOT EXISTS weight_signal_strength numeric(14,6);
ALTER TABLE ml.lead_score ADD COLUMN IF NOT EXISTS weight_territory numeric(14,6);
