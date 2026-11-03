# Frontend

Claude Design components (`*.dc.html`) on the generated dc runtime (`support.js`, do not edit),
wired to the gateway's `/api/v1/*` through `api.js`. The app is `Invent Market Tracker.dc.html`;
the other components are its views. `Design System.dc.html` is the style-guide board.

Run it with `make frontend` and open http://localhost:3000. Checks: `make test-frontend`.
Details, session modes and known limits: `docs/runbooks/frontend.md`. The Next.js 15 port
(REQ-FE-001) remains a later decision.
