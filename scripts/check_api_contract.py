#!/usr/bin/env python3
"""Check that the OpenAPI document and the server route table cannot drift."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ntx import __version__, openapi  # noqa: E402
from ntx.serve import ROUTES  # noqa: E402


def check() -> list[str]:
    errors: list[str] = []
    spec = openapi.build_spec()
    try:
        json.dumps(spec)
    except (TypeError, ValueError) as exc:
        errors.append("OpenAPI document is not JSON serializable: %s" % exc)

    paths = set(spec.get("paths", {}))
    if paths != set(ROUTES):
        missing = sorted(set(ROUTES) - paths)
        extra = sorted(paths - set(ROUTES))
        if missing:
            errors.append("routes missing from OpenAPI: %s" % ", ".join(missing))
        if extra:
            errors.append("OpenAPI paths without a server route: %s" % ", ".join(extra))

    operation_ids: list[str] = []
    for path, item in spec.get("paths", {}).items():
        if not isinstance(item, dict) or not item:
            errors.append("%s: path has no operations" % path)
            continue
        for method, operation in item.items():
            if method != "get":
                errors.append("%s: unsupported method %s" % (path, method))
                continue
            operation_id = operation.get("operationId")
            if not operation_id:
                errors.append("%s: missing operationId" % path)
            else:
                operation_ids.append(operation_id)
            if "responses" not in operation:
                errors.append("%s: missing responses" % path)

    duplicates = sorted({item for item in operation_ids if operation_ids.count(item) > 1})
    if duplicates:
        errors.append("duplicate operationIds: %s" % ", ".join(duplicates))
    if spec.get("openapi") != openapi.OPENAPI_VERSION:
        errors.append("unexpected OpenAPI version: %s" % spec.get("openapi"))
    if spec.get("info", {}).get("version") != __version__:
        errors.append("OpenAPI info.version does not match package version")
    if not spec.get("components", {}).get("securitySchemes"):
        errors.append("OpenAPI document has no security schemes")
    return errors


def main() -> int:
    errors = check()
    if errors:
        for error in errors:
            print("api contract: " + error, file=sys.stderr)
        return 1
    print("api contract: ok (%d routes)" % len(ROUTES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
