"""Generate an OpenAPI 3.1 document from operations, errors and types."""
import re
from collections import defaultdict

import yaml

from .typesys import effective_success, type_schemas, type_to_schema


def gen_openapi(spec):
    errors = {e["code"]: e for e in spec.get("errors", [])}
    globals_ = [e["code"] for e in errors.values() if e.get("global")]
    type_names = {t["name"] for t in spec.get("types", [])}
    schemas = type_schemas(spec)
    paths = {}
    for o in spec.get("operations", []):
        params = [{"name": n, "in": "path", "required": True, "schema": {"type": "string"}}
                  for n in re.findall(r"{(\w+)}", o["path"])]
        for q in o.get("query", []):
            params.append({"name": q["name"], "in": "query", "required": bool(q.get("required")),
                           "schema": type_to_schema(q["type"], type_names) or {"type": "string"},
                           **({"description": q["note"]} if q.get("note") else {})})
        if o["method"] == "POST" and o.get("idempotent"):
            params.append({"name": "Idempotency-Key", "in": "header", "required": False, "schema": {"type": "string"}})
        if o.get("concurrency") == "if-match":
            params.append({"name": "If-Match", "in": "header", "required": True, "schema": {"type": "integer"}})

        success = effective_success(o)
        ok = {"description": "Success"}
        if o.get("response") and success != 204:
            ok["content"] = {"application/json": {"schema": {"$ref": f"#/components/schemas/{o['response']}"}}}
        responses = {str(success): ok}
        by_status = defaultdict(list)
        for code in o["errors"] + globals_:
            by_status[errors[code]["status"]].append(code)
        for status, codes in sorted(by_status.items()):
            responses[str(status)] = {
                "description": ", ".join(sorted(set(codes))),
                "content": {"application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}}}

        item = {"operationId": o["id"], "summary": o.get("summary") or o["id"],
                "tags": [o["path"].strip("/").split("/")[0] or "root"],
                "parameters": params, "responses": responses,
                "x-implements": o["implements"], "x-permission": o["permission"], "x-touches": o["touches"]}
        if o["permission"] == ["Anonymous"]:
            item["security"] = []
        if o.get("request"):
            item["requestBody"] = {"required": True, "content": {
                "application/json": {"schema": {"$ref": f"#/components/schemas/{o['request']}"}}}}
        paths.setdefault(o["path"], {})[o["method"].lower()] = item

    schemas["Problem"] = {
        "type": "object", "required": ["type", "title", "status", "code", "traceId"],
        "properties": {"type": {"type": "string"}, "title": {"type": "string"}, "status": {"type": "integer"},
                       "detail": {"type": "string"}, "code": {"type": "string"}, "traceId": {"type": "string"},
                       "errors": {"type": "array", "items": {"type": "object"}}}}
    doc = {
        "openapi": "3.1.0",
        "info": {"title": spec["spec"]["name"] + " API", "version": spec["spec"]["version"],
                 "description": "Generated from the spec. Bodies come from `types`; operations without a request or response type have none."},
        "security": [{"bearerAuth": []}],
        "paths": paths,
        "components": {"securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}},
                       "schemas": schemas},
    }
    return yaml.safe_dump(doc, sort_keys=False, width=100)
