# Deployment

`docker-compose.yml` is the F0 local environment. It runs APIs and outbox/
stream workers separately so the complete flow is observable without another
supervisor. Staging and production keep a private network and expose only the
gateway. Vercel frontend settings, Caddy, Bright Data credentials, and Supabase
must be configured outside the repository through environment secrets.

The Python image shares `libs` and service code to keep the first cut small.
Once service boundaries stabilize, build contexts can be separated without
changing contracts.

