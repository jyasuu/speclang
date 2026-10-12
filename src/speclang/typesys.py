"""Field types: turn entities and `types` into JSON Schema, and lint field-level rules.

The mini type language used in `type:` strings:
  string | uuid | integer | number | bigint | boolean | timestamp | json | bytes | markdown
  string[1..500]          bounded string (also markdown[0..10000])
  a | b | c               one of the listed values
  Name[]                  array of Name (a primitive or another type)
  TypeName                reference to another entry in `types`
"""
import operator
import re
from collections import deque
from datetime import datetime

from jsonschema import Draft202012Validator

PRIMS = {
    "string": {"type": "string"},
    "uuid": {"type": "string", "format": "uuid"},
    "integer": {"type": "integer"},
    "number": {"type": "number"},
    "bigint": {"type": "integer", "format": "int64"},
    "boolean": {"type": "boolean"},
    "timestamp": {"type": "string", "format": "date-time"},
    "json": {"type": "object"},
    "bytes": {"type": "string", "format": "byte"},
    "markdown": {"type": "string"},
}


def type_to_schema(t, type_names=()):
    """Return a JSON Schema fragment for a type string, or None if it cannot be resolved."""
    t = t.strip()
    if "|" in t:
        return {"type": "string", "enum": [v.strip() for v in t.split("|")]}
    m = re.fullmatch(r"([A-Za-z]\w*)\[\]", t)
    if m:
        inner = type_to_schema(m.group(1), type_names)
        return None if inner is None else {"type": "array", "items": inner}
    m = re.fullmatch(r"([A-Za-z]\w*)\[(\d+)\.\.(\d+)\]", t)
    if m:
        base = PRIMS.get(m.group(1))
        if base is None or base.get("type") != "string":
            return None
        return {**base, "minLength": int(m.group(2)), "maxLength": int(m.group(3))}
    if t in PRIMS:
        return dict(PRIMS[t])
    if t in type_names:
        return {"$ref": f"#/components/schemas/{t}"}
    return None


def base_name(t):
    """The referenced type name inside 'Name[]' or 'Name', else None."""
    m = re.fullmatch(r"([A-Za-z]\w*)(\[\])?", t.strip())
    return m.group(1) if m else None


def resolve_types(spec):
    """Flatten each type: start from its entity (only/omit), then apply its own fields."""
    entities = {e["name"]: e for e in spec.get("entities", [])}
    resolved = {}
    for t in spec.get("types", []):
        fields = []
        src = entities.get(t.get("from"))
        if src:
            for f in src["fields"]:
                if t.get("only") and f["name"] not in t["only"]:
                    continue
                if f["name"] in t.get("omit", []):
                    continue
                fields.append(dict(f))
        for f in t.get("fields", []):
            fields = [x for x in fields if x["name"] != f["name"]]
            fields.append(dict(f))
        if "required" in t:
            for f in fields:
                f["required"] = f["name"] in t["required"]
        resolved[t["name"]] = fields
    return resolved


COMPARE = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge,
           "==": operator.eq, "!=": operator.ne}
NUMERIC = {"integer", "bigint", "number"}
LITERAL_KEYWORD = {">": "exclusiveMinimum", ">=": "minimum", "<": "exclusiveMaximum", "<=": "maximum", "==": "const"}


def _coerce(value, ftype):
    """Make two values comparable: timestamps become datetimes, everything else is unchanged."""
    if ftype == "timestamp" and isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def check_violations(spec, name, example):
    """Messages for value checks (between fields, or against a literal) that `example` breaks.

    Numeric literal checks are enforced by the JSON Schema itself and are skipped here.
    """
    t = next((x for x in spec.get("types", []) if x["name"] == name), None)
    if not t:
        return []
    ftypes = {f["name"]: f["type"] for f in resolve_types(spec).get(name, [])}
    msgs = []
    for c in t.get("checks", []):
        left = c["left"]
        if left not in example or left not in ftypes:
            continue
        if "right" in c:
            if c["right"] not in example or c["right"] not in ftypes:
                continue
            a, b, desc = example[left], example[c["right"]], f"{left} {c['op']} {c['right']}"
            ca, cb = ftypes[left], ftypes[c["right"]]
        else:
            if ftypes[left] in NUMERIC:
                continue
            a, b, desc = example[left], c["value"], f"{left} {c['op']} {c['value']!r}"
            ca = cb = ftypes[left]
        try:
            ok = COMPARE[c["op"]](_coerce(a, ca), _coerce(b, cb))
        except (TypeError, ValueError):
            ok = False
        if not ok:
            msgs.append(c.get("message") or f"violates check {desc}")
    return msgs


def constraint_schema(constraints):
    """Cross-field rules as JSON Schema (wrapped in allOf so several can combine)."""
    parts = []
    for c in constraints:
        if "exactly_one" in c:
            parts.append({"oneOf": [{"required": [f]} for f in c["exactly_one"]]})
        if "at_least_one" in c:
            parts.append({"anyOf": [{"required": [f]} for f in c["at_least_one"]]})
        if "at_most_one" in c:
            fs = c["at_most_one"]
            parts.append({"oneOf": [{"not": {"anyOf": [{"required": [f]} for f in fs]}}]
                          + [{"required": [f]} for f in fs]})
        if "requires" in c:
            parts.append({"dependentRequired": {k: list(v) for k, v in c["requires"].items()}})
    return parts


def type_schemas(spec):
    names = {t["name"] for t in spec.get("types", [])}
    defs = {t["name"]: t for t in spec.get("types", [])}
    schemas = {}
    for name, fields in resolve_types(spec).items():
        props, required = {}, []
        for f in fields:
            schema = type_to_schema(f["type"], names) or {}
            if f.get("note"):
                schema = {**schema, "description": f["note"]}
            props[f["name"]] = schema
            if f.get("required"):
                required.append(f["name"])
        entry = {"type": "object", "properties": props}
        if required:
            entry["required"] = required
        if defs[name].get("constraints"):
            entry["allOf"] = constraint_schema(defs[name]["constraints"])
        ftypes = {f["name"]: f["type"] for f in fields}
        notes = []
        for c in defs[name].get("checks", []):
            left, op = c["left"], c["op"]
            numeric_value = isinstance(c.get("value"), (int, float)) and not isinstance(c.get("value"), bool)
            if "value" in c and ftypes.get(left) in NUMERIC and numeric_value and op in LITERAL_KEYWORD:
                props.setdefault(left, {})[LITERAL_KEYWORD[op]] = c["value"]
                if c.get("message"):           # the keyword enforces the limit; x-checks keeps the message for people
                    notes.append({"rule": f"{left} {op} {c['value']!r}", "message": c["message"]})
            else:
                rule = f"{left} {op} {c['right'] if 'right' in c else repr(c['value'])}"
                notes.append({"rule": rule, **({"message": c["message"]} if c.get("message") else {})})
        if notes:
            entry["x-checks"] = notes
        if defs[name].get("example"):
            entry["examples"] = [defs[name]["example"]]
        schemas[name] = entry
    return schemas


def validate_example(spec, name, example):
    """Messages for every way `example` fails the schema of type `name` (empty list if valid)."""
    root = {"$ref": f"#/components/schemas/{name}", "components": {"schemas": type_schemas(spec)}}
    return sorted(e.message for e in Draft202012Validator(root).iter_errors(example))


def reachable(spec, start):
    """All (type, field) pairs reachable from a type, following references and arrays."""
    types = resolve_types(spec)
    seen, found, queue = set(), set(), deque([start])
    while queue:
        name = queue.popleft()
        if name in seen or name not in types:
            continue
        seen.add(name)
        for f in types[name]:
            found.add(f"{name}.{f['name']}")
            ref = base_name(f["type"])
            if ref in types:
                queue.append(ref)
    return found


def effective_success(op):
    return op.get("success") or (204 if op["method"] == "DELETE" else 200)


def lint_types(spec, report):
    types = spec.get("types", [])
    ops = spec.get("operations", [])
    comps = spec.get("components", [])
    entities = {e["name"]: {f["name"] for f in e["fields"]} for e in spec.get("entities", [])}
    type_names = {t["name"] for t in types}
    resolved = resolve_types(spec)

    # R31 type definitions
    for t in types:
        where = f"types[{t['name']}]"
        if t.get("from") and t["from"] not in entities:
            report.add("ERROR", "R31", where, f"'from' entity {t['from']} does not exist")
        elif t.get("from"):
            for key in ("only", "omit", "required"):
                for fname in t.get(key, []):
                    if key == "required" and fname in {f["name"] for f in t.get("fields", [])}:
                        continue
                    if fname not in entities[t["from"]] and fname not in {f["name"] for f in t.get("fields", [])}:
                        report.add("ERROR", "R31", where, f"'{key}' names unknown field {fname}")
        names = [f["name"] for f in t.get("fields", [])]
        for dup in {n for n in names if names.count(n) > 1}:
            report.add("ERROR", "R31", where, f"duplicate field {dup}")
        for f in resolved.get(t["name"], []):
            if type_to_schema(f["type"], type_names) is None:
                report.add("ERROR", "R31", where, f"field '{f['name']}' has unresolvable type '{f['type']}'")

    # R36 examples validate, R37 constraints name real fields
    for t in types:
        where = f"types[{t['name']}]"
        field_names = {f["name"] for f in resolved.get(t["name"], [])}
        for c in t.get("constraints", []):
            named = []
            for key, v in c.items():
                if key == "requires":
                    named += list(v) + [f for fs in v.values() for f in fs]
                else:
                    named += list(v)
            for fname in named:
                if fname not in field_names:
                    report.add("ERROR", "R37", where, f"constraint names unknown field {fname}")
        # R40 value checks name real fields and compare comparable things
        ftypes = {f["name"]: f["type"] for f in resolved.get(t["name"], [])}
        first_new = len(report.items)
        for c in t.get("checks", []):
            ref = f"check '{c['left']} {c['op']} {c.get('right', c.get('value'))}'"
            if c["left"] not in ftypes:
                report.add("ERROR", "R40", where, f"{ref}: unknown field {c['left']}")
                continue
            lt = ftypes[c["left"]]
            if "right" in c:
                if c["right"] not in ftypes:
                    report.add("ERROR", "R40", where, f"{ref}: unknown field {c['right']}")
                else:
                    rt = ftypes[c["right"]]
                    if not ((lt in NUMERIC and rt in NUMERIC) or lt == rt):
                        report.add("ERROR", "R40", where, f"{ref}: cannot compare {lt} with {rt}")
            else:
                v = c["value"]
                numeric_ok = lt in NUMERIC and isinstance(v, (int, float)) and not isinstance(v, bool)
                stamp_ok = lt == "timestamp" and isinstance(v, str)
                if stamp_ok:
                    try:
                        _coerce(v, "timestamp")
                    except ValueError:
                        stamp_ok = False
                text_ok = lt.split("[")[0] in ("string", "markdown") and isinstance(v, str)
                if not (numeric_ok or stamp_ok or text_ok):
                    report.add("ERROR", "R40", where, f"{ref}: value {v!r} does not fit field type {lt}")
        bad_checks = any(i[1] == "R40" and i[2] == where for i in report.items[first_new:])

        if t.get("example") and not bad_checks and all(f["type"] and type_to_schema(f["type"], type_names) is not None
                                    for f in resolved.get(t["name"], [])):
            for msg in validate_example(spec, t["name"], t["example"]) + check_violations(spec, t["name"], t["example"]):
                report.add("ERROR", "R36", where, f"example is invalid: {msg}")

    # R32 / R33 operations
    used_types = set()
    for o in ops:
        where = f"operations[{o['id']}]"
        for key in ("request", "response"):
            ref = o.get(key)
            if ref:
                used_types.add(ref)
                if ref not in type_names:
                    report.add("ERROR", "R32", where, f"{key} type {ref} is not defined")
        for q in o.get("query", []):
            if type_to_schema(q["type"], type_names) is None:
                report.add("ERROR", "R32", where, f"query parameter '{q['name']}' has unresolvable type '{q['type']}'")
        if types and effective_success(o) in (200, 201) and not o.get("response"):
            report.add("WARN", "R33", where, "returns a body but declares no 'response' type")

    # R34 component field linking
    ops_by_id = {o["id"]: o for o in ops}
    for c in comps:
        shows = c.get("shows", [])
        if not shows:
            continue
        available = set()
        for u in c.get("uses", []):
            resp = ops_by_id.get(u, {}).get("response")
            if resp:
                available |= reachable(spec, resp)
        for item in shows:
            if item not in available:
                report.add("ERROR", "R34", f"components[{c['name']}]",
                           f"shows {item}, which no operation it uses returns")

    # R35 unused types
    referenced = set(used_types)
    for fields in resolved.values():
        for f in fields:
            ref = base_name(f["type"])
            if ref:
                referenced.add(ref)
    for name in sorted(type_names - referenced):
        report.add("WARN", "R35", f"types[{name}]", "is not used by any operation or other type")
