# CCPO — Credit Card Portfolio Optimiser

Design docs in `docs/` (Parts A–E). Build phases and working rules in
`CLAUDE.md`. Current status: **Phase 1 complete** (scaffold, schema,
synthetic seed) — next: Phase 2, the deterministic engine.

Quick start (local):
```bash
createdb ccpo_dev
psql ccpo_dev -f supabase/local_auth_shim.sql
psql ccpo_dev -f supabase/migrations/0001_init.sql
cd compute && pip install -r requirements.txt
DATABASE_URL=postgresql://localhost/ccpo_dev python -m seeds.seed
```
