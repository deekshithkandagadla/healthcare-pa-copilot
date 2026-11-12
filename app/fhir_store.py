"""Read-only access to the synthetic FHIR-style data.

`LocalFhirStore` reads the JSON bundles directly; `HttpFhirStore` calls the mock FHIR API.
Both expose the same methods, so the MCP server works with either.

What is true FHIR here: resource types, ids, references, `code.coding`, `period`,
`onsetDateTime`, `effectiveDateTime`, and search by `patient`. What is simplified: no
server-side search modifiers, no paging, Observation values are strings, and
DocumentReference content points at local Markdown files instead of base64 attachments.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from app import config

RESOURCE_TYPES = ("Patient", "Condition", "Observation", "Encounter", "DocumentReference", "Coverage")


class LocalFhirStore:
    def __init__(self, data_dir: Path = config.DATA_DIR):
        self.data_dir = data_dir
        self._by_patient: dict[str, list[dict]] = {}
        for f in sorted((data_dir / "fhir").glob("*.json")):
            bundle = json.loads(f.read_text())
            pid = f.stem
            self._by_patient[pid] = [e["resource"] for e in bundle["entry"]]

    async def search(self, resource_type: str, patient_id: str) -> list[dict]:
        if resource_type not in RESOURCE_TYPES:
            raise ValueError(f"Unsupported resource type {resource_type}")
        return [r for r in self._by_patient.get(patient_id, []) if r["resourceType"] == resource_type]

    async def read_note(self, note_id: str) -> str:
        path = self.data_dir / "notes" / f"{note_id}.md"
        if not path.resolve().is_relative_to((self.data_dir / "notes").resolve()) or not path.exists():
            raise FileNotFoundError(note_id)
        return path.read_text()

    def auth_history(self) -> list[dict]:
        return json.loads((self.data_dir / "auth_history.json").read_text())


class HttpFhirStore:
    def __init__(self, base_url: str, timeout: float = config.TOOL_TIMEOUT_SECONDS):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def search(self, resource_type: str, patient_id: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(f"{self.base_url}/{resource_type}", params={"patient": patient_id})
            r.raise_for_status()
            return [e["resource"] for e in r.json().get("entry", [])]

    async def read_note(self, note_id: str) -> str:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(f"{self.base_url}/Binary/{note_id}")
            r.raise_for_status()
            return r.text

    def auth_history(self) -> list[dict]:
        r = httpx.get(f"{self.base_url}/_synthetic/auth-history", timeout=self.timeout)
        r.raise_for_status()
        return r.json()


def get_store():
    return HttpFhirStore(config.FHIR_BASE_URL) if config.FHIR_BASE_URL else LocalFhirStore()
