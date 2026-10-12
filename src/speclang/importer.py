"""Import an OpenAPI 3 document as a starting spec.

What is imported: schemas become `types`; operations (id, method, path, summary, query parameters, request and response
types, success status, idempotency and If-Match headers); error responses become the error catalogue; roles come from
`x-permission` (documents produced by this tool) or from the security setting.

What is NOT in an OpenAPI file, so is created as a placeholder for you to rewrite: requirements (one placeholder per
operation, priority S), entities (stubs, only when `x-touches` names them), acceptance criteria, tests, phases.
Imported from schemas: `constraints` (the oneOf/anyOf/dependentRequired patterns this tool writes), `checks` (numeric limits
and `x-checks`) and `example`.
"""
import ast
import re

import yaml

METHODS = ["get", "post", "put", "patch", "delete"]
CODE = re.compile(r"^[A-Z][A-Z0-9_]+$")
KEYWORD_OPS = [("minimum", ">="), ("exclusiveMinimum", ">"), ("maximum", "<="), ("exclusiveMaximum", "<"), ("const", "==")]


def _numeric(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _simple_required(options):
    """['a', 'b'] if every option is exactly {required: [one field]}, else None."""
    names = []
    for o in options:
        if set(o) == {"required"} and len(o["required"]) == 1:
            names.append(o["required"][0])
        else:
            return None
    return names or None


def parse_constraint(part):
    """Recognise the JSON Schema patterns this tool writes for `constraints`; None for anything else."""
    if "oneOf" in part:
        opts = part["oneOf"]
        names = _simple_required(opts)
        if names:
            return {"exactly_one": names}
        first = opts[0] if opts else {}
        if set(first) == {"not"} and "anyOf" in first["not"]:
            base = _simple_required(first["not"]["anyOf"])
            if base and _simple_required(opts[1:]) == base:
                return {"at_most_one": base}
    if "anyOf" in part:
        names = _simple_required(part["anyOf"])
        if names:
            return {"at_least_one": names}
    if "dependentRequired" in part:
        return {"requires": {k: list(v) for k, v in part["dependentRequired"].items()}}
    return None


def _type_name(raw, taken):
    name = re.sub(r"[^A-Za-z0-9]", "", raw) or "Type"
    name = name[0].upper() + name[1:]
    base, n = name, 2
    while name in taken:
        name, n = f"{base}{n}", n + 1
    taken.add(name)
    return name


def _op_id(op, method, path, taken):
    raw = op.get("operationId")
    if not raw:
        segs = []
        for seg in path.split("/"):
            if seg.startswith("{"):
                segs.append("By" + re.sub(r"[^A-Za-z0-9]", "", seg).capitalize())
            elif seg:
                segs.append(re.sub(r"[^A-Za-z0-9]", "", seg).capitalize())
        raw = method + "".join(segs)
    name = re.sub(r"[^A-Za-z0-9]", "", raw) or method
    name = name[0].lower() + name[1:]
    base, n = name, 2
    while name in taken:
        name, n = f"{base}{n}", n + 1
    taken.add(name)
    return name


class Importer:
    def __init__(self, doc):
        self.doc = doc
        self.schemas = doc.get("components", {}).get("schemas", {}) or {}
        self.taken_types = set()
        self.type_names = {}          # schema name -> spec type name (object schemas only)
        self.types = []
        self.notes = []               # things that were simplified

    # ------------------------------------------------------------ field types
    def resolve(self, schema):
        while "$ref" in schema:
            schema = self.schemas.get(schema["$ref"].rsplit("/", 1)[-1], {})
        return schema

    def flatten(self, schema):
        """Follow $ref and merge `allOf` of object schemas into one object schema (the usual 'extends' pattern)."""
        schema = self.resolve(schema)
        if "allOf" not in schema:
            return schema
        merged = {"type": "object", "properties": {}, "required": []}
        for part in schema["allOf"]:
            part = self.flatten(part)
            if not self.is_object(part):
                return schema
            merged["properties"].update(part.get("properties") or {})
            merged["required"] += part.get("required", [])
        merged["properties"].update(schema.get("properties") or {})
        merged["required"] += schema.get("required", [])
        if schema.get("description"):
            merged["description"] = schema["description"]
        return merged

    def field_type(self, schema, where):
        """(type string, note or None) in the spec's type language."""
        if "$ref" in schema:
            name = schema["$ref"].rsplit("/", 1)[-1]
            if name in self.type_names:
                return self.type_names[name], None
            return self.field_type(self.schemas.get(name, {}), where)
        if "allOf" in schema and len(schema["allOf"]) == 1 and len(schema) <= 2:
            return self.field_type(schema["allOf"][0], where)
        for key in ("oneOf", "anyOf", "allOf"):
            if key in schema:
                self.notes.append(f"{where}: {key} simplified to json")
                return "json", f"was {key} in the OpenAPI file"
        t = schema.get("type")
        if "enum" in schema and all(isinstance(v, str) for v in schema["enum"]):
            return " | ".join(schema["enum"]), None
        if t == "array":
            inner, _ = self.field_type(schema.get("items", {}), where)
            if "|" in inner:                      # arrays of enums are not expressible; use string[]
                inner = "string"
            return f"{inner}[]", None
        if t == "string":
            fmt = schema.get("format")
            if fmt == "uuid":
                return "uuid", None
            if fmt == "date-time":
                return "timestamp", None
            if fmt == "byte":
                return "bytes", None
            if "maxLength" in schema:
                return f"string[{schema.get('minLength', 0)}..{schema['maxLength']}]", None
            return "string", None
        if t == "integer":
            return ("bigint" if schema.get("format") == "int64" else "integer"), None
        if t == "number":
            return "number", None
        if t == "boolean":
            return "boolean", None
        return "json", None

    def object_type(self, name, schema):
        required = set(schema.get("required", []))
        fields = []
        for fname, fschema in (schema.get("properties") or {}).items():
            ftype, note = self.field_type(fschema, f"{name}.{fname}")
            f = {"name": fname, "type": ftype}
            if fname in required:
                f["required"] = True
            desc = note or fschema.get("description")
            if desc:
                f["note"] = desc
            fields.append(f)
        entry = {"name": name, "fields": fields}
        if schema.get("description"):
            entry["note"] = schema["description"]
        constraints = [c for c in (parse_constraint(part) for part in schema.get("allOf", [])) if c]
        if constraints:
            entry["constraints"] = constraints
        checks = self.checks_for(name, schema, {f["name"] for f in fields})
        if checks:
            entry["checks"] = checks
        example = schema.get("example")
        if example is None and isinstance(schema.get("examples"), list) and schema["examples"]:
            example = schema["examples"][0]
        if isinstance(example, dict):
            entry["example"] = example
        return entry

    def checks_for(self, name, schema, field_names):
        """Value checks: from `x-checks` (rule and message) and from numeric limits on properties."""
        checks = []
        for xc in schema.get("x-checks") or []:
            m = re.fullmatch(r"(\w+) (<=|>=|==|!=|<|>) (.+)", str(xc.get("rule", "")))
            if not m or m.group(1) not in field_names:
                self.notes.append(f"{name}: x-checks rule {xc.get('rule')!r} could not be imported")
                continue
            left, op, rest = m.groups()
            if rest in field_names:
                c = {"left": left, "op": op, "right": rest}
            else:
                try:
                    value = ast.literal_eval(rest)
                except (ValueError, SyntaxError):
                    value = rest
                c = {"left": left, "op": op, "value": value}
            if xc.get("message"):
                c["message"] = xc["message"]
            checks.append(c)
        have = {(c["left"], c["op"], repr(c["value"])) for c in checks if "value" in c}
        for fname, fschema in (schema.get("properties") or {}).items():
            if fschema.get("type") not in ("integer", "number"):
                continue
            for keyword, op in KEYWORD_OPS:
                value = fschema.get(keyword)
                if not _numeric(value):
                    continue
                if keyword == "minimum" and fschema.get("exclusiveMinimum") is True:   # OpenAPI 3.0 spelling
                    op = ">"
                if keyword == "maximum" and fschema.get("exclusiveMaximum") is True:
                    op = "<"
                if (fname, op, repr(value)) not in have:
                    checks.append({"left": fname, "op": op, "value": value})
        return checks

    def is_object(self, schema):
        return schema.get("type") == "object" or "properties" in schema

    def body_type(self, schema, op_id, suffix):
        """Type name for a request or response body schema; creates a type for inline schemas."""
        if not schema:
            return None
        if "$ref" in schema:
            name = schema["$ref"].rsplit("/", 1)[-1]
            if name not in self.type_names:
                self.notes.append(f"{op_id}: the {suffix.lower()} body {name} is not an object schema; it was not imported")
            return self.type_names.get(name)
        schema = self.flatten(schema)
        taken = self.taken_types
        if schema.get("type") == "array":
            name = _type_name(op_id + suffix, taken)
            inner, _ = self.field_type(schema.get("items", {}), name)
            self.types.append({"name": name, "fields": [{"name": "items", "type": f"{inner}[]", "required": True}],
                               "note": "Imported: the body is a bare array"})
            return name
        if self.is_object(schema):
            name = _type_name(op_id + suffix, taken)
            self.types.append(self.object_type(name, schema))
            return name
        return None

    # ------------------------------------------------------------------ main
    def run(self, name=None):
        doc = self.doc
        # types from named object schemas (the generic Problem body is the error format, not a type)
        for raw, schema in self.schemas.items():
            if raw == "Problem":
                continue
            if self.is_object(self.flatten(schema)):
                self.type_names[raw] = _type_name(raw, self.taken_types)
            else:
                self.notes.append(f"schema {raw} is not an object; it was not imported as a type (fields using it are inlined)")
        for raw, tname in self.type_names.items():
            self.types.append(self.object_type(tname, self.flatten(self.schemas[raw])))

        ops, errors, entities, reqs = [], {}, {}, []
        op_ids, fr_seen, next_fr = set(), set(), 1
        doc_security = doc.get("security")
        for path, item in (doc.get("paths") or {}).items():
            for method in METHODS:
                op = item.get(method)
                if not op:
                    continue
                oid = _op_id(op, method, path, op_ids)
                entry = {"id": oid, "method": method.upper(), "path": path}
                if op.get("summary") and op["summary"] != op.get("operationId"):
                    entry["summary"] = op["summary"]

                # requirements: restore from x-implements, else one placeholder per operation
                implements = op.get("x-implements")
                if not implements:
                    while f"FR-{next_fr}" in fr_seen:
                        next_fr += 1
                    implements = [f"FR-{next_fr}"]
                    fr_seen.add(implements[0])
                    what = op.get("summary") or f"{method.upper()} {path}"
                    reqs.append({"id": implements[0], "priority": "S", "area": (op.get("tags") or ["api"])[0],
                                 "text": f"The system shall provide: {what}.",
                                 "notes": "Imported from OpenAPI; rewrite as a real requirement and set the priority."})
                else:
                    for fr in implements:
                        if fr not in fr_seen:
                            fr_seen.add(fr)
                            reqs.append({"id": fr, "priority": "S", "area": (op.get("tags") or ["api"])[0],
                                         "text": f"Imported placeholder for {fr}; rewrite as a shall-statement.",
                                         "notes": "Imported from OpenAPI; the original wording is not in the file."})
                entry["implements"] = list(implements)

                if "x-permission" in op:
                    entry["permission"] = list(op["x-permission"])
                else:
                    sec = op.get("security", doc_security)
                    entry["permission"] = ["Anonymous"] if sec == [] else ["Authenticated"]

                # parameters
                query, idempotent, if_match = [], False, False
                for prm in list(item.get("parameters", [])) + list(op.get("parameters", [])):
                    where = prm.get("in")
                    if where == "query":
                        ptype, _ = self.field_type(prm.get("schema", {}), f"{oid}.{prm['name']}")
                        q = {"name": prm["name"], "type": ptype}
                        if prm.get("required"):
                            q["required"] = True
                        if prm.get("description"):
                            q["note"] = prm["description"]
                        query.append(q)
                    elif where == "header" and prm["name"].lower() == "idempotency-key":
                        idempotent = True
                    elif where == "header" and prm["name"].lower() == "if-match":
                        if_match = True
                if query:
                    entry["query"] = query
                if method == "post":
                    entry["idempotent"] = idempotent
                if method in ("put", "patch"):
                    entry["concurrency"] = "if-match" if if_match else "none"
                elif if_match:
                    entry["concurrency"] = "if-match"

                # request body
                body = (op.get("requestBody") or {}).get("content", {}).get("application/json", {}).get("schema")
                req_type = self.body_type(body, oid, "Request")
                if req_type:
                    entry["request"] = req_type

                # responses
                success, codes = None, []
                for status, resp in (op.get("responses") or {}).items():
                    if not str(status).isdigit():
                        continue
                    status = int(status)
                    if 200 <= status < 300:
                        if success is None:
                            success = status
                            schema = (resp.get("content") or {}).get("application/json", {}).get("schema")
                            res_type = self.body_type(schema, oid, "Response") if status != 204 else None
                            if res_type:
                                entry["response"] = res_type
                    elif status >= 400:
                        desc = (resp.get("description") or "").strip()
                        parts = [p.strip() for p in desc.split(",")] if desc else []
                        found = parts if parts and all(CODE.match(p) for p in parts) else [f"HTTP_{status}"]
                        for code in found:
                            codes.append(code)
                            errors.setdefault(code, {"code": code, "status": status,
                                                     "meaning": f"Imported from the OpenAPI file (HTTP {status})",
                                                     "client": "Handle the error"})
                default = 204 if method == "delete" else 200
                if success is not None and success != default:
                    entry["success"] = success
                entry["errors"] = codes
                touches = op.get("x-touches") or {}
                entry["touches"] = dict(touches)
                for ent in touches:
                    entities.setdefault(ent, {
                        "name": ent, "owner": "imported",
                        "fields": [{"name": "id", "type": "uuid", "required": True}],
                        "notes": "Stub created by the import from x-touches; add the real fields."})
                ops.append(entry)

        # errors present on every operation are global
        if len(ops) >= 2:
            common = set.intersection(*(set(o["errors"]) for o in ops)) if ops else set()
            for code in common:
                errors[code]["global"] = True
            for o in ops:
                o["errors"] = [c for c in o["errors"] if c not in common]
        for o in ops:
            o["errors"] = list(dict.fromkeys(o["errors"]))

        roles = sorted({r for o in ops for r in o["permission"]})
        title = str(doc.get("info", {}).get("title", "Imported API"))
        if title.endswith(" API") and len(title) > 4:        # the generated file adds " API" again
            title = title[:-4]
        meta = {"spec": {"name": name or title,
                         "version": str(doc.get("info", {}).get("version", "0.1")), "status": "draft"},
                "roles": roles}
        return {
            "00-meta.yaml": meta,
            "10-requirements.yaml": {"requirements": sorted(reqs, key=lambda r: int(r["id"].split("-")[1]))},
            "20-api.yaml": {**({"entities": list(entities.values())} if entities else {}),
                            "errors": sorted(errors.values(), key=lambda e: (e["status"], e["code"])),
                            "operations": ops},
            "30-types.yaml": {"types": self.types},
        }


def import_openapi(doc, name=None):
    """Return ({filename: yaml text}, notes) for the spec files that describe `doc`."""
    importer = Importer(doc)
    parts = importer.run(name)
    header = ("# Imported from an OpenAPI document by speclint import-openapi.\n"
              "# Requirements, entities, acceptance criteria, tests and phases are placeholders or missing: fill them in.\n")
    files = {fname: header + yaml.safe_dump(data, sort_keys=False, width=110, allow_unicode=True)
             for fname, data in parts.items()}
    return files, importer.notes
