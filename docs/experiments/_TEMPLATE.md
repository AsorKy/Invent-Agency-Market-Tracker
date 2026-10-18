# EXP-NNN Component name

Layer: L3. Pre-register this sheet before building the component (ADR-IMT-0003-PRED section 2.7). Never edit the hypothesis, baseline, or thresholds after seeing data.

| Element | Value |
| --- | --- |
| Falsifiable hypothesis | Component X improves metric Y by at least Z% over baseline B. |
| Baseline | Trivial reference (rule, seasonal naive, stratified random, flat k-NN). Never zero. |
| Contrast design | Holdout (5-10%) when there is an intervention; rolling temporal backtest otherwise. |
| Statistical power | Base rate p, minimum detectable effect delta, required n (`statsmodels.stats.power`). |
| Thresholds | MDE (business-relevant), alpha = 0.05, Benjamini-Hochberg when several components are evaluated together. |
| Five LENS questions | D, Dx, P, Prescriptive gate, V metric plus kill trigger, each with a one-statistic validator. |
| Decision | ADOPT, ITERATE, or KILL. Emit `component.evaluated` with the verdict and `source_version`. |
| Re-evaluation | Quarterly, with the same thresholds (scheduled by V6). |
