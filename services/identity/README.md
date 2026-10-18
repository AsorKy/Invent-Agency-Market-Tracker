# Identity service bootstrap

This directory reserves the `identity` service required by ARCH-0001 v2.2.
The current implementation exposes only liveness, readiness, and an empty
local JWKS document so the service topology can be validated without claiming
production authentication behavior.

The next identity phase must replace this bootstrap with Django and Django REST
Framework and implement users, tenants, scopes, RBAC, signed JWTs, key
rotation, sliding sessions, logout, and the private admin surface.
