"""Change report: compare two versions of a spec and show what changed and what it touches."""
from collections import defaultdict

from .typesys import base_name, resolve_types

KEYS = {
    "requirements": "id", "acceptance": "id", "nfrs": "id", "entities": "name", "types": "name",
    "operations": "id", "errors": "code", "components": "name", "decisions": "id", "slos": "id",
    "goals": "id", "assumptions": "id", "personas": "id", "flows": "id", "tests": "id", "rules": "id",
    "threats": "id", "failures": "id", "notifications": "id", "risks": "id", "phases": "id",
    "rollout": "id", "analytics": "event", "plans": "limit", "glossary": "term", "revisions": "version",
}


def _index(spec, coll):
    key = KEYS[coll]
    return {i[key]: i for i in spec.get(coll, [])}


def diff_specs(old, new):
    result = {}
    for coll in KEYS:
        a, b = _index(old, coll), _index(new, coll)
        added = sorted(set(b) - set(a))
        removed = sorted(set(a) - set(b))
        changed = []
        for k in sorted(set(a) & set(b)):
            if a[k] != b[k]:
                fields = sorted(f for f in set(a[k]) | set(b[k]) if a[k].get(f) != b[k].get(f))
                changed.append((k, fields))
        if added or removed or changed:
            result[coll] = {"added": added, "removed": removed, "changed": changed}
    return result


def impact(old, new, diff):
    """For changed or removed requirements, list what refers to them."""
    lines = []
    req = diff.get("requirements", {})
    for rid in [k for k, _ in req.get("changed", [])] + req.get("removed", []):
        spec = new if rid not in req.get("removed", []) else old
        ops = [o["id"] for o in spec.get("operations", []) if rid in o["implements"]]
        comps = [c["name"] for c in spec.get("components", []) if rid in c.get("implements", [])]
        acs = [a["id"] for a in spec.get("acceptance", []) if rid in a["covers"]]
        acs += [a for r in spec.get("requirements", []) if r["id"] == rid for a in r.get("accepts", []) if a not in acs]
        phases = [p["id"] for p in spec.get("phases", []) if rid in p["delivers"]]
        state = "removed" if rid in req.get("removed", []) else "changed"
        lines.append((rid, state, ops, comps, acs, phases))
    return lines


def format_diff(old, new, md=False):
    d = diff_specs(old, new)
    if not d:
        return "No differences.\n"
    h = (lambda t: f"## {t}\n") if md else (lambda t: f"{t.upper()}\n")
    bullet = "- " if md else "  "
    out = []
    for coll, c in d.items():
        out.append(h(coll))
        for k in c["added"]:
            out.append(f"{bullet}added    {k}")
        for k in c["removed"]:
            out.append(f"{bullet}removed  {k}")
        for k, fields in c["changed"]:
            out.append(f"{bullet}changed  {k} ({', '.join(fields)})")
        out.append("")
    imp = impact(old, new, d)
    if imp:
        out.append(h("impact of requirement changes"))
        for rid, state, ops, comps, acs, phases in imp:
            out.append(f"{bullet}{rid} {state}: operations [{', '.join(ops) or '-'}], components [{', '.join(comps) or '-'}], "
                       f"acceptance [{', '.join(acs) or '-'}], phases [{', '.join(phases) or '-'}]")
        out.append("")
    total = sum(len(c["added"]) + len(c["removed"]) + len(c["changed"]) for c in d.values())
    out.append(f"{total} difference(s) in {len(d)} section(s)")
    return "\n".join(out) + "\n"


def _closure(types, start):
    """Every (type.field) reachable from a type, with its field type and whether it is required."""
    out, seen, stack = {}, set(), [start]
    while stack:
        name = stack.pop()
        if name in seen or name not in types:
            continue
        seen.add(name)
        for f in types[name]:
            out[f"{name}.{f['name']}"] = (f["type"], bool(f.get("required")))
            ref = base_name(f["type"])
            if ref in types:
                stack.append(ref)
    return out


def breaking_changes(old, new):
    """Changes that can break existing API clients, as readable strings.

    Breaking: an operation removed or moved; a role that lost permission; a changed success status; a response field
    removed, retyped or no longer guaranteed; a request field or query parameter that is newly required or retyped;
    an operation that now requires If-Match; an error code whose HTTP status changed.
    """
    out = []
    old_ops = {o["id"]: o for o in old.get("operations", [])}
    new_ops = {o["id"]: o for o in new.get("operations", [])}
    old_t, new_t = resolve_types(old), resolve_types(new)
    for oid, a in sorted(old_ops.items()):
        b = new_ops.get(oid)
        if b is None:
            out.append(f"operation {oid} was removed")
            continue
        if (a["method"], a["path"]) != (b["method"], b["path"]):
            out.append(f"operation {oid} moved from {a['method']} {a['path']} to {b['method']} {b['path']}")
        lost = set(a["permission"]) - set(b["permission"])
        if lost:
            out.append(f"operation {oid} no longer allows {', '.join(sorted(lost))}")
        sa = a.get("success") or (204 if a["method"] == "DELETE" else 200)
        sb = b.get("success") or (204 if b["method"] == "DELETE" else 200)
        if sa != sb:
            out.append(f"operation {oid} success status changed from {sa} to {sb}")
        if b.get("concurrency") == "if-match" and a.get("concurrency") != "if-match":
            out.append(f"operation {oid} now requires an If-Match header")
        if a.get("response"):
            if not b.get("response"):
                out.append(f"operation {oid} no longer returns a body")
            else:
                before, after = _closure(old_t, a["response"]), _closure(new_t, b["response"])
                for key, (typ, req) in sorted(before.items()):
                    if key not in after:
                        out.append(f"operation {oid}: response field {key} was removed")
                    elif after[key][0] != typ:
                        out.append(f"operation {oid}: response field {key} changed type from {typ} to {after[key][0]}")
                    elif req and not after[key][1]:
                        out.append(f"operation {oid}: response field {key} is no longer guaranteed")
        if b.get("request"):
            before = _closure(old_t, a["request"]) if a.get("request") else {}
            after = _closure(new_t, b["request"])
            for key, (typ, req) in sorted(after.items()):
                if key not in before and req:
                    out.append(f"operation {oid}: request field {key} is newly required")
                elif key in before and before[key][0] != typ:
                    out.append(f"operation {oid}: request field {key} changed type from {before[key][0]} to {typ}")
                elif key in before and req and not before[key][1]:
                    out.append(f"operation {oid}: request field {key} became required")
        old_q = {q["name"]: q for q in a.get("query", [])}
        for q in b.get("query", []):
            was = old_q.get(q["name"])
            if q.get("required") and not (was and was.get("required")):
                out.append(f"operation {oid}: query parameter {q['name']} is newly required")
            elif was and was["type"] != q["type"]:
                out.append(f"operation {oid}: query parameter {q['name']} changed type from {was['type']} to {q['type']}")
    old_err = {e["code"]: e for e in old.get("errors", [])}
    for e in new.get("errors", []):
        if e["code"] in old_err and old_err[e["code"]]["status"] != e["status"]:
            out.append(f"error {e['code']} changed HTTP status from {old_err[e['code']]['status']} to {e['status']}")
    return out
