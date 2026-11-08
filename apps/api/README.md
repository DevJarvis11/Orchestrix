# apps/api

FastAPI service (SPEC 17): auth (dev JWT or Keycloak OIDC), RBAC, tasks, uploads, SSE events, escalations and reviewer chat, memories, costs, traces, replay and admin. Stateless: all state is in Postgres and Redis. Routers only wire packages together.
