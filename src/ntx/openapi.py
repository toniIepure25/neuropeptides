"""OpenAPI 3.1 contract for the local read-only API.

The specification is built in Python so the route set, package version and
documented parameters cannot drift from the server without a failing test.
"""

from __future__ import annotations

from typing import Any

from . import __version__
from .store import SCHEMA_VERSION

OPENAPI_VERSION = "3.1.0"


def _response(description: str, schema: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/" + schema}}},
    }


def _parameter(name: str, description: str, schema: dict[str, Any], required: bool = False) -> dict[str, Any]:
    return {
        "name": name,
        "in": "query",
        "required": required,
        "description": description,
        "schema": schema,
    }


def build_spec() -> dict[str, Any]:
    """Return a fresh, JSON-serializable OpenAPI document."""

    limit = _parameter(
        "limit",
        "Maximum number of claims to return.",
        {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
    )
    offset = _parameter(
        "offset",
        "Zero-based claim offset.",
        {"type": "integer", "minimum": 0, "maximum": 10_000_000, "default": 0},
    )
    entity = _parameter("entity", "Ontology entity id, for example NP:OXT.", {"type": "string"})
    outcome = _parameter("outcome", "Outcome identifier, for example social_cognition.", {"type": "string"})
    top = _parameter(
        "top",
        "Maximum number of ranked hypotheses.",
        {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
    )
    bootstrap = _parameter(
        "bootstrap",
        "Bootstrap resamples; 0 disables resampling.",
        {"type": "integer", "minimum": 0, "maximum": 5000, "default": 200},
    )

    errors = {
        "400": {"$ref": "#/components/responses/BadRequest"},
        "401": {"$ref": "#/components/responses/Unauthorized"},
        "403": {"$ref": "#/components/responses/Forbidden"},
        "500": {"$ref": "#/components/responses/InternalError"},
        "503": {"$ref": "#/components/responses/ServiceUnavailable"},
    }

    paths: dict[str, Any] = {
        "/api/v1/health": {
            "get": {
                "tags": ["system"],
                "operationId": "getHealth",
                "summary": "Service and database schema health.",
                "responses": {
                    "200": _response("Service is healthy.", "Health"),
                    **{key: value for key, value in errors.items() if key != "503"},
                    "503": errors["503"],
                },
            }
        },
        "/api/v1/counts": {
            "get": {
                "tags": ["system"],
                "operationId": "getCounts",
                "summary": "Row counts per table.",
                "responses": {
                    "200": _response("Table counts.", "Counts"),
                    **errors,
                },
            }
        },
        "/api/v1/entities": {
            "get": {
                "tags": ["evidence"],
                "operationId": "listEntities",
                "summary": "Ontology entities with claim counts.",
                "responses": {
                    "200": _response("Entity list.", "EntityList"),
                    **errors,
                },
            }
        },
        "/api/v1/claims": {
            "get": {
                "tags": ["evidence"],
                "operationId": "listClaims",
                "summary": "Paged claims with optional entity and outcome filters.",
                "parameters": [entity, outcome, limit, offset],
                "responses": {
                    "200": _response("Claim page.", "ClaimPage"),
                    **errors,
                },
            }
        },
        "/api/v1/gaps": {
            "get": {
                "tags": ["synthesis"],
                "operationId": "listGaps",
                "summary": "Ranked gap hypotheses.",
                "parameters": [top],
                "responses": {
                    "200": _response("Ranked hypotheses.", "GapList"),
                    **errors,
                },
            }
        },
        "/api/v1/synthesis": {
            "get": {
                "tags": ["synthesis"],
                "operationId": "getSynthesis",
                "summary": "Design-weighted synthesis summaries.",
                "parameters": [bootstrap],
                "responses": {
                    "200": _response("Synthesis summaries.", "SynthesisList"),
                    **errors,
                },
            }
        },
        "/api/v1/quality": {
            "get": {
                "tags": ["quality"],
                "operationId": "getQuality",
                "summary": "Schema, provenance, effect and gold quality gates.",
                "responses": {
                    "200": _response("Quality report.", "Quality"),
                    **errors,
                },
            }
        },
        "/api/v1/reconciliation": {
            "get": {
                "tags": ["quality"],
                "operationId": "getReconciliation",
                "summary": "Claim-level abstract/full-text reconciliation.",
                "parameters": [entity],
                "responses": {
                    "200": _response("Reconciliation report.", "Reconciliation"),
                    **errors,
                },
            }
        },
        "/api/v1/manifest/latest": {
            "get": {
                "tags": ["system"],
                "operationId": "getLatestManifest",
                "summary": "Newest run manifest.",
                "responses": {
                    "200": _response("Manifest or null.", "Manifest"),
                    **errors,
                },
            }
        },
        "/api/v1/openapi.json": {
            "get": {
                "tags": ["system"],
                "operationId": "getOpenApi",
                "summary": "This OpenAPI 3.1 document.",
                "responses": {
                    "200": _response("OpenAPI document.", "OpenApiDocument"),
                    **{key: value for key, value in errors.items() if key != "503"},
                },
            }
        },
    }

    schemas: dict[str, Any] = {
        "Error": {
            "type": "object",
            "required": ["error", "status"],
            "properties": {
                "error": {"type": "string"},
                "status": {"type": "integer"},
            },
        },
        "Health": {
            "type": "object",
            "required": ["status", "version", "schema_version", "database"],
            "properties": {
                "status": {"const": "ok"},
                "version": {"type": "string"},
                "schema_version": {"type": "integer", "const": SCHEMA_VERSION},
                "database": {"type": "string"},
            },
        },
        "Counts": {
            "type": "object",
            "additionalProperties": {"type": "integer", "minimum": 0},
        },
        "Entity": {
            "type": "object",
            "required": ["entity_id", "pref_name", "class", "claims"],
            "properties": {
                "entity_id": {"type": "string"},
                "pref_name": {"type": "string"},
                "class": {"type": "string"},
                "claims": {"type": "integer", "minimum": 0},
            },
        },
        "EntityList": {
            "type": "object",
            "required": ["items"],
            "properties": {"items": {"type": "array", "items": {"$ref": "#/components/schemas/Entity"}}},
        },
        "Claim": {
            "type": "object",
            "required": ["claim_id", "doc_id", "entity_id", "direction", "outcome"],
            "properties": {
                "claim_id": {"type": "integer"},
                "doc_id": {"type": "string"},
                "entity_id": {"type": "string"},
                "direction": {"type": "string"},
                "outcome": {"type": "string"},
                "role": {"type": "string"},
                "evidence_start": {"type": "integer", "minimum": 0},
                "evidence_end": {"type": "integer", "minimum": 0},
                "reasons": {"type": "array", "items": {"type": "string"}},
                "quant": {"type": "object", "additionalProperties": True},
            },
            "additionalProperties": True,
        },
        "ClaimPage": {
            "type": "object",
            "required": ["total", "limit", "offset", "items"],
            "properties": {
                "total": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                "offset": {"type": "integer", "minimum": 0},
                "items": {"type": "array", "items": {"$ref": "#/components/schemas/Claim"}},
            },
        },
        "Gap": {
            "type": "object",
            "additionalProperties": True,
            "required": ["entity_id", "outcome"],
            "properties": {
                "entity_id": {"type": "string"},
                "outcome": {"type": "string"},
            },
        },
        "GapList": {
            "type": "object",
            "required": ["items"],
            "properties": {"items": {"type": "array", "items": {"$ref": "#/components/schemas/Gap"}}},
        },
        "Synthesis": {
            "type": "object",
            "additionalProperties": True,
            "required": ["entity_id", "outcome"],
            "properties": {
                "entity_id": {"type": "string"},
                "outcome": {"type": "string"},
            },
        },
        "SynthesisList": {
            "type": "object",
            "required": ["items"],
            "properties": {"items": {"type": "array", "items": {"$ref": "#/components/schemas/Synthesis"}}},
        },
        "Quality": {
            "type": "object",
            "additionalProperties": True,
            "required": ["ok"],
            "properties": {"ok": {"type": "boolean"}},
        },
        "Reconciliation": {
            "type": "object",
            "additionalProperties": True,
        },
        "Manifest": {
            "type": "object",
            "properties": {
                "run_id": {"type": ["string", "null"]},
                "created": {"type": ["string", "null"]},
                "manifest": {"type": ["object", "null"], "additionalProperties": True},
            },
        },
        "OpenApiDocument": {
            "type": "object",
            "additionalProperties": True,
            "required": ["openapi", "info", "paths"],
        },
    }

    return {
        "openapi": OPENAPI_VERSION,
        "info": {
            "title": "ntx read-only API",
            "version": __version__,
            "description": (
                "Local, read-only inspection API for a built ntx SQLite database. "
                "It is not a public service and has no write endpoints. The bearer "
                "and X-API-Token schemes apply only when NTX_API_TOKEN is configured."
            ),
            "license": {"name": "MIT", "identifier": "MIT"},
        },
        "servers": [{"url": "http://127.0.0.1:8765", "description": "Default local bind"}],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "Required when NTX_API_TOKEN is set.",
                },
                "apiToken": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-API-Token",
                    "description": "Alternative to the Authorization bearer header.",
                },
            },
            "schemas": schemas,
            "responses": {
                "BadRequest": _response("Invalid query parameter.", "Error"),
                "Unauthorized": _response("Token required or invalid.", "Error"),
                "Forbidden": _response("Host not allowed.", "Error"),
                "InternalError": _response("Internal error without a traceback.", "Error"),
                "ServiceUnavailable": _response("Database file missing.", "Error"),
            },
        },
        "tags": [
            {"name": "system", "description": "Health, manifests and contract metadata."},
            {"name": "evidence", "description": "Entities and provenance-checked claims."},
            {"name": "synthesis", "description": "Gap hypotheses and design-weighted summaries."},
            {"name": "quality", "description": "Quality gates and abstract/full-text reconciliation."},
        ],
    }
