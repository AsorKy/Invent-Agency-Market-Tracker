# Streamlit pages

Multipage location for the reporting service. Every page loads its cards
through the gateway with `ui.card`, checks the session role, and shows the
`as_of` caption with a stale warning. Shared helpers live in `_common.py`.

| File | Page | Endpoint (via gateway) |
| --- | --- | --- |
| `1_Market.py` | Market radar KPIs and top sources; companies by city and category, Maps rating and reviews, hiring demand by city and keyword, mid-size share, companies with their RUES label | `pulse/pipeline-backlog`, `pulse/top-sources`, `companies/insights`, `companies` |
| `2_Leads.py` | Scored lead inbox with RUES label, decision makers, and role-gated export | `leads` |
| `3_Segmentos.py` | Segments | `segments` |
| `4_Territorios.py` | Territory opportunity ranking and IOZ placeholder | `territory/opportunity` |
| `5_Pipeline.py` | Kanban columns per pipeline stage | `pipeline/kanban` |
| `6_Proyectos.py` | Projects list with role-gated export | `projects` |
| `7_Ops.py` | Freshness per source and outbox backlog (admin, strategist, analyst) | `pulse/ops/health` |
