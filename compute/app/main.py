"""CCPO compute service — FastAPI shell.

Endpoints land per Part E §E.0: /evaluate and /next-best-spend in Phase 3,
/optimise and /whatif in Phase 4. Until then this exposes /health only.
"""
from fastapi import FastAPI

app = FastAPI(title="ccpo-compute", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "engine": "not yet built (phase 2)"}
