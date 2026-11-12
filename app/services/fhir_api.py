"""Mock FHIR REST API (read-only, synthetic data).

    uvicorn app.services.fhir_api:app --port 8101

GET /fhir/{ResourceType}?patient=PT-001  -> searchset Bundle
GET /fhir/Binary/{note_id}               -> note text (simplified Binary read)
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from app.fhir_store import RESOURCE_TYPES, LocalFhirStore

app = FastAPI(title="Synthetic FHIR API (mock)")
store = LocalFhirStore()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/fhir/metadata")
def capability():
    return {"resourceType": "CapabilityStatement", "status": "active", "kind": "instance",
            "fhirVersion": "4.0.1", "format": ["json"],
            "rest": [{"mode": "server", "resource": [
                {"type": t, "interaction": [{"code": "search-type"}],
                 "searchParam": [{"name": "patient", "type": "reference"}]} for t in RESOURCE_TYPES]}]}


@app.get("/fhir/Binary/{note_id}", response_class=PlainTextResponse)
async def read_note(note_id: str):
    try:
        return await store.read_note(note_id)
    except FileNotFoundError:
        raise HTTPException(404, "Not found")


@app.get("/fhir/_synthetic/auth-history")
def auth_history():
    return store.auth_history()


@app.get("/fhir/{resource_type}")
async def search(resource_type: str, patient: str):
    if resource_type not in RESOURCE_TYPES:
        raise HTTPException(404, f"Unsupported resource type {resource_type}")
    hits = await store.search(resource_type, patient)
    return {"resourceType": "Bundle", "type": "searchset", "total": len(hits),
            "entry": [{"resource": r} for r in hits]}
