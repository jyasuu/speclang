#!/usr/bin/env python3
"""speclint: validate Spec Language files and generate documents from them.

Usage:
  speclint todo/                      # all *.yaml files in a folder, merged
  speclint a.yaml b.yaml --build build/
  speclint todo/ --strict             # warnings fail the run too

Multiple files are merged: list sections are concatenated, the first `spec` block wins.
Exit code: 0 if no errors, 1 otherwise.
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml
from jsonschema import Draft7Validator

VAGUE_WORDS = [
    "fast", "easy", "easily", "secure", "securely", "user-friendly", "intuitive",
    "robust", "quickly", "efficient", "efficiently", "seamless", "seamlessly",
    "as appropriate", "etc",
]
SCREEN_REQUIRED_STATES = {"loading", "empty", "error", "offline", "no-permission"}
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
ANALYTICS_FORBIDDEN = {"title", "description", "body", "comment", "query", "email", "name", "text"}

DUP_CHECKS = [
    ("requirements", "id"), ("acceptance", "id"), ("nfrs", "id"), ("entities", "name"),
    ("operations", "id"), ("errors", "code"), ("components", "name"), ("decisions", "id"),
    ("slos", "id"), ("goals", "id"), ("assumptions", "id"), ("personas", "id"), ("flows", "id"),
    ("tests", "id"), ("rules", "id"), ("threats", "id"), ("failures", "id"),
    ("notifications", "id"), ("risks", "id"), ("phases", "id"), ("rollout", "id"),
]


class Report:
    def __init__(self):
        self.items = []

    def add(self, level, rule, where, message):
        self.items.append((level, rule, where, message))

    @property
    def errors(self):
        return [i for i in self.items if i[0] == "ERROR"]

    @property
    def warnings(self):
        return [i for i in self.items if i[0] == "WARN"]

    def print(self):
        for level, rule, where, msg in sorted(self.items, key=lambda i: (i[0], i[1], i[2])):
            print(f"{level:5} {rule} {where}: {msg}")
        print(f"\n{len(self.errors)} error(s), {len(self.warnings)} warning(s)")


# ------------------------------------------------------------------ loading

def collect_files(paths):
    files = []
    for p in map(Path, paths):
        files.extend(sorted(p.glob("*.yaml")) if p.is_dir() else [p])
    return files


def load_specs(paths):
    merged = {}
    for f in collect_files(paths):
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for key, value in data.items():
            if key == "spec":
                merged.setdefault("spec", value)
            elif isinstance(value, list):
                merged.setdefault(key, []).extend(value)
            elif isinstance(value, dict):
                merged.setdefault(key, {}).update(value)
            else:
                merged[key] = value
    return merged


def check_schema(spec, schema, report):
    found = sorted(Draft7Validator(schema).iter_errors(spec), key=lambda e: list(e.absolute_path))
    for e in found:
        where = "/".join(str(p) for p in e.absolute_path) or "(root)"
        report.add("ERROR", "S00", where, e.message)
    return not found


# --------------------------------------------------------------------- lint

def lint(spec, report):
    g = lambda k: spec.get(k, [])
    reqs, acs, nfrs, ents, ops = g("requirements"), g("acceptance"), g("nfrs"), g("entities"), g("operations")
    errs, comps, tests, slos = g("errors"), g("components"), g("tests"), g("slos")
    roles = set(spec.get("roles", []))

    req_ids = {r["id"] for r in reqs}
    ac_ids = {a["id"] for a in acs}
    nfr_ids = {n["id"] for n in nfrs}
    ent_names = {e["name"] for e in ents}
    err_codes = {e["code"] for e in errs}
    op_ids = {o["id"] for o in ops}
    test_ids = {t["id"] for t in tests}
    persona_ids = {p["id"] for p in g("personas")}
    known_refs = req_ids | nfr_ids | ac_ids

    # R03 duplicate ids
    for coll, key in DUP_CHECKS:
        counts = Counter(i[key] for i in g(coll))
        for dup, n in counts.items():
            if n > 1:
                report.add("ERROR", "R03", f"{coll}[{dup}]", "duplicate id")

    # R02 / R01 requirements and acceptance
    for a in acs:
        for ref in a["covers"]:
            if ref not in req_ids | nfr_ids:
                report.add("ERROR", "R02", f"acceptance[{a['id']}]", f"covers unknown requirement {ref}")
    for r in reqs:
        for ref in r.get("accepts", []):
            if ref not in ac_ids:
                report.add("ERROR", "R02", f"requirements[{r['id']}]", f"accepts unknown criterion {ref}")
    covered = defaultdict(set)
    for a in acs:
        for ref in a["covers"]:
            covered[ref].add(a["id"])
    for r in reqs:
        covered[r["id"]].update(r.get("accepts", []))
        if r["priority"] == "M" and not covered[r["id"]]:
            report.add("ERROR", "R01", f"requirements[{r['id']}]", "Must requirement has no acceptance criterion")

    # R04 / R05 / R06 wording and measurability
    for r in reqs:
        if r["priority"] == "M" and not re.search(r"\bshall\b", r["text"], re.I):
            report.add("WARN", "R04", f"requirements[{r['id']}]", "Must requirement should use the word 'shall'")
        for w in VAGUE_WORDS:
            if re.search(rf"\b{re.escape(w)}\b", r["text"], re.I):
                report.add("WARN", "R05", f"requirements[{r['id']}]", f"vague word '{w}': replace with a measurable statement")
    for n in nfrs:
        if not re.search(r"\d", n["target"]):
            report.add("ERROR", "R06", f"nfrs[{n['id']}]", f"target '{n['target']}' contains no number or standard version")
        for w in VAGUE_WORDS:
            if re.search(rf"\b{re.escape(w)}\b", n["text"], re.I):
                report.add("WARN", "R05", f"nfrs[{n['id']}]", f"vague word '{w}'")

    # operations
    used_errors = set()
    touched_soft = set()
    for o in ops:
        where = f"operations[{o['id']}]"
        for ref in o["implements"]:
            if ref not in req_ids:
                report.add("ERROR", "R07", where, f"implements unknown requirement {ref}")
        for code in o["errors"]:
            used_errors.add(code)
            if code not in err_codes:
                report.add("ERROR", "R08", where, f"error {code} is not in the error catalogue")
        for ent, letters in o["touches"].items():
            if ent not in ent_names:
                report.add("ERROR", "R10", where, f"touches unknown entity {ent}")
            if "S" in letters:
                touched_soft.add(ent)
        if o["method"] == "POST" and "idempotent" not in o:
            report.add("ERROR", "R11", where, "POST must declare 'idempotent: true|false'")
        if o["method"] == "PATCH" and o.get("concurrency") != "if-match":
            report.add("ERROR", "R11", where, "PATCH must declare 'concurrency: if-match'")
        if o["method"] == "PUT" and "concurrency" not in o:
            report.add("ERROR", "R11", where, "PUT must declare 'concurrency'")
        writes = [e for e, letters in o["touches"].items() if set(letters) & set("CUDSA")]
        if o["method"] in MUTATING and len(writes) > 1 and not o.get("tx"):
            report.add("WARN", "R12", where, f"writes {len(writes)} entities but declares no 'tx' steps")
        if roles:
            for role in o["permission"]:
                if role not in roles:
                    report.add("ERROR", "R19", where, f"permission uses undeclared role '{role}'")

    # R09 unused errors
    for e in errs:
        if e["code"] not in used_errors and not e.get("global"):
            report.add("WARN", "R09", f"errors[{e['code']}]", "defined but not used by any operation (mark 'global: true' if it applies everywhere)")

    # R28 soft-delete entities need a retention rule
    for e in ents:
        if e["name"] in touched_soft and not e.get("retention"):
            report.add("WARN", "R28", f"entities[{e['name']}]", "is soft-deleted by an operation but declares no retention")

    # components, screens
    for c in comps:
        where = f"components[{c['name']}]"
        for u in c.get("uses", []):
            if u not in op_ids:
                report.add("ERROR", "R14", where, f"uses unknown operation {u}")
        for ref in c.get("implements", []):
            if ref not in req_ids:
                report.add("ERROR", "R14", where, f"implements unknown requirement {ref}")
        if c["kind"] == "screen":
            missing = SCREEN_REQUIRED_STATES - set(c["states"])
            if missing:
                report.add("ERROR", "R13", where, f"screen is missing required states: {', '.join(sorted(missing))}")
            if not c.get("route"):
                report.add("ERROR", "R27", where, "screen has no route")

    # R15 state machines
    for e in ents:
        st = e.get("states")
        if not st:
            continue
        where = f"entities[{e['name']}]"
        values = set(st["values"])
        if st["initial"] not in values:
            report.add("ERROR", "R15", where, f"initial state '{st['initial']}' is not declared")
        graph = defaultdict(set)
        for t in st["transitions"]:
            for end in (t["from"], t["to"]):
                if end not in values:
                    report.add("ERROR", "R15", where, f"transition uses undeclared state '{end}'")
            graph[t["from"]].add(t["to"])
        seen, stack = set(), [st["initial"]]
        while stack:
            s = stack.pop()
            if s not in seen:
                seen.add(s)
                stack.extend(graph[s])
        for s in sorted(values - seen):
            report.add("WARN", "R15", where, f"state '{s}' is unreachable from the initial state")

    # R18 Must requirements nothing implements
    implemented = set()
    for o in ops:
        implemented.update(o["implements"])
    for c in comps:
        implemented.update(c.get("implements", []))
    for r in reqs:
        if r["priority"] == "M" and r["id"] not in implemented:
            report.add("WARN", "R18", f"requirements[{r['id']}]", "Must requirement is not implemented by any operation or component")

    # R20 tests and NFR coverage
    nfr_covered = set()
    for t in tests:
        for ref in t["covers"]:
            if ref not in known_refs:
                report.add("ERROR", "R20", f"tests[{t['id']}]", f"covers unknown id {ref}")
            nfr_covered.add(ref)
    for s in slos:
        for ref in s.get("covers", []):
            if ref not in nfr_ids:
                report.add("ERROR", "R29", f"slos[{s['id']}]", f"covers unknown NFR {ref}")
            nfr_covered.add(ref)
    for n in nfrs:
        if n["id"] not in nfr_covered:
            report.add("ERROR", "R20", f"nfrs[{n['id']}]", "NFR is not covered by any test or SLO")

    # R21 threats
    for t in g("threats"):
        if t["test"] not in test_ids:
            report.add("ERROR", "R21", f"threats[{t['id']}]", f"test {t['test']} does not exist")

    # R22 notifications
    for n in g("notifications"):
        where = f"notifications[{n['id']}]"
        for ref in n.get("supports", []):
            if ref not in req_ids:
                report.add("ERROR", "R22", where, f"supports unknown requirement {ref}")
        if n["opt_out"] is False and "security" not in n["type"].lower():
            report.add("WARN", "R22", where, "cannot be opted out of; only security notifications should be mandatory (FR-43)")

    # R23 analytics must not carry content
    for ev in g("analytics"):
        for prop in ev["properties"]:
            if prop.split()[0].lower() in ANALYTICS_FORBIDDEN:
                report.add("ERROR", "R23", f"analytics[{ev['event']}]", f"property '{prop}' may carry user content")

    # R24 plans need PLAN_LIMIT
    if g("plans"):
        if "PLAN_LIMIT" not in err_codes:
            report.add("ERROR", "R24", "plans", "plans are defined but error PLAN_LIMIT is not in the catalogue")
        elif "PLAN_LIMIT" not in used_errors:
            report.add("WARN", "R24", "plans", "PLAN_LIMIT is not returned by any operation")

    # R25 phases deliver every requirement
    delivered = set()
    for ph in g("phases"):
        for ref in ph["delivers"]:
            if ref not in req_ids:
                report.add("ERROR", "R25", f"phases[{ph['id']}]", f"delivers unknown requirement {ref}")
            delivered.add(ref)
    if g("phases"):
        for r in reqs:
            if r["id"] not in delivered:
                level = "ERROR" if r["priority"] == "M" else "WARN"
                report.add(level, "R25", f"requirements[{r['id']}]", f"priority {r['priority']} requirement is not delivered by any phase")

    # R26 flows
    for f in g("flows"):
        for ref in f["covers"]:
            if ref not in req_ids:
                report.add("ERROR", "R26", f"flows[{f['id']}]", f"covers unknown requirement {ref}")
        if f.get("persona") and f["persona"] not in persona_ids:
            report.add("ERROR", "R26", f"flows[{f['id']}]", f"unknown persona {f['persona']}")

    # R30 rules
    for r in g("rules"):
        for ref in r.get("supports", []):
            if ref not in req_ids | nfr_ids:
                report.add("ERROR", "R30", f"rules[{r['id']}]", f"supports unknown requirement {ref}")


# --------------------------------------------------------------- generators

def md_table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for r in rows:
        out.append("| " + " | ".join(str(c) if str(c) != "" else " " for c in r) + " |")
    return "\n".join(out) + "\n"


def page(title, body):
    return f"# {title} (generated)\n\n{body}"


def gen_traceability(spec):
    covered, tests_for = defaultdict(set), defaultdict(set)
    for a in spec.get("acceptance", []):
        for ref in a["covers"]:
            covered[ref].add(a["id"])
    for r in spec["requirements"]:
        covered[r["id"]].update(r.get("accepts", []))
    for t in spec.get("tests", []):
        for ref in t["covers"]:
            tests_for[ref].add(t["id"])
    for s in spec.get("slos", []):
        for ref in s.get("covers", []):
            tests_for[ref].add(s["id"])
    ops_for, comps_for, phase_for = defaultdict(list), defaultdict(list), {}
    for o in spec.get("operations", []):
        for ref in o["implements"]:
            ops_for[ref].append(o["id"])
    for c in spec.get("components", []):
        for ref in c.get("implements", []):
            comps_for[ref].append(c["name"])
    for ph in spec.get("phases", []):
        for ref in ph["delivers"]:
            phase_for[ref] = ph["id"]
    rows = [[r["id"], r["priority"], r["text"], ", ".join(sorted(covered[r["id"]])) or "-",
             ", ".join(ops_for[r["id"]]) or "-", ", ".join(comps_for[r["id"]]) or "-",
             phase_for.get(r["id"], "-")] for r in spec["requirements"]]
    nrows = [[n["id"], n["category"], n["target"], n["verify"],
              ", ".join(sorted(tests_for[n["id"]])) or "-"] for n in spec.get("nfrs", [])]
    return page("Requirement traceability",
                md_table(["Requirement", "Pri", "Text", "Acceptance", "Operations", "Components", "Phase"], rows)
                + "\n## Non-functional requirements\n\n"
                + md_table(["NFR", "Category", "Target", "Verification", "Tests and SLOs"], nrows))


def gen_acceptance(spec):
    rows = [[a["id"], ", ".join(a["covers"]), a["given"], a["when"], a["then"]] for a in spec.get("acceptance", [])]
    return page("Acceptance criteria", md_table(["ID", "Covers", "Given", "When", "Then"], rows))


def gen_errors(spec):
    used_by = defaultdict(list)
    for o in spec.get("operations", []):
        for code in o["errors"]:
            used_by[code].append(o["id"])
    rows = [[f"`{e['code']}`", e["status"], e["meaning"], e["client"],
             "all" if e.get("global") else ", ".join(used_by[e["code"]]) or "-"]
            for e in sorted(spec.get("errors", []), key=lambda e: (e["status"], e["code"]))]
    return page("Error catalogue", md_table(["Code", "HTTP", "Meaning", "Client behavior", "Used by"], rows))


def gen_matrix(spec):
    ents = [e["name"] for e in spec.get("entities", [])]
    rows = [[f"`{o['method']} {o['path']}`"] + [o["touches"].get(e, "") for e in ents] for o in spec.get("operations", [])]
    return page("Interaction matrix",
                "C create, R read, U update, D delete, S soft delete, A anonymize.\n\n" + md_table(["Operation"] + ents, rows))


def gen_permissions(spec):
    roles = spec.get("roles", [])
    rows = [[f"`{o['method']} {o['path']}`"] + ["yes" if r in o["permission"] else "" for r in roles]
            for o in spec.get("operations", [])]
    return page("Permission matrix", md_table(["Operation"] + roles, rows))


def gen_notifications(spec):
    rows = [[n["id"], n["type"], n["trigger"], ", ".join(n["channels"]), n["rules"], "yes" if n["opt_out"] else "no (mandatory)"]
            for n in spec.get("notifications", [])]
    return page("Notifications", md_table(["ID", "Type", "Trigger", "Channels", "Rules", "Opt-out"], rows))


def gen_analytics(spec):
    rows = [[f"`{e['event']}`", ", ".join(e["properties"]), e["purpose"]] for e in spec.get("analytics", [])]
    return page("Analytics events", md_table(["Event", "Properties", "Purpose"], rows))


def gen_plans(spec):
    rows = [[p["limit"], p["free"], p["pro"]] for p in spec.get("plans", [])]
    return page("Plans and limits", md_table(["Limit", "Free", "Pro"], rows))


def gen_threats(spec):
    rows = [[t["id"], t["category"], t["example"], t["mitigation"], t["test"]] for t in spec.get("threats", [])]
    return page("Threat model (STRIDE)", md_table(["ID", "Threat", "Example", "Mitigation", "Test"], rows))


def gen_risks(spec):
    rows = [[r["id"], r["risk"], r["likelihood"], r["impact"], r["mitigation"], r["owner"]] for r in spec.get("risks", [])]
    return page("Risk register", md_table(["ID", "Risk", "Likelihood", "Impact", "Mitigation", "Owner"], rows))


def gen_phases(spec):
    rows = [[p["id"], p["name"], p["scope"], ", ".join(p["delivers"]) or "-", "; ".join(p["exit"])] for p in spec.get("phases", [])]
    body = md_table(["Phase", "Name", "Scope", "Delivers", "Exit criteria"], rows)
    stages = [[r["id"], r["stage"], r["audience"], r["exit"]] for r in spec.get("rollout", [])]
    if stages:
        body += "\n## Rollout\n\n" + md_table(["ID", "Stage", "Audience", "Exit"], stages)
    return page("Delivery plan", body)


def gen_states(spec):
    out = []
    for e in spec.get("entities", []):
        st = e.get("states")
        if not st:
            continue
        lines = ["```mermaid", "stateDiagram-v2", f"    [*] --> {st['initial']}"]
        lines += [f"    {t['from']} --> {t['to']}: {t['event']} ({', '.join(t['by'])})" for t in st["transitions"]]
        lines.append("```")
        out.append(f"## {e['name']}\n\n" + "\n".join(lines) + "\n")
    return page("State machines", "\n".join(out))


def gen_openapi(spec):
    errors = {e["code"]: e for e in spec.get("errors", [])}
    globals_ = [e for e in errors.values() if e.get("global")]
    paths = {}
    for o in spec.get("operations", []):
        params = [{"name": n, "in": "path", "required": True, "schema": {"type": "string"}}
                  for n in re.findall(r"{(\w+)}", o["path"])]
        if o["method"] == "POST" and o.get("idempotent"):
            params.append({"name": "Idempotency-Key", "in": "header", "required": False, "schema": {"type": "string"}})
        if o.get("concurrency") == "if-match":
            params.append({"name": "If-Match", "in": "header", "required": True, "schema": {"type": "integer"}})
        success = o.get("success") or (204 if o["method"] == "DELETE" else 200)
        responses = {str(success): {"description": "Success"}}
        by_status = defaultdict(list)
        for code in o["errors"] + [g["code"] for g in globals_]:
            by_status[errors[code]["status"]].append(code)
        for status, codes in sorted(by_status.items()):
            responses[str(status)] = {
                "description": ", ".join(sorted(set(codes))),
                "content": {"application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}}}
        op = {"operationId": o["id"], "summary": o.get("summary") or o["id"],
              "tags": [o["path"].strip("/").split("/")[0] or "root"], "parameters": params,
              "responses": responses,
              "x-implements": o["implements"], "x-permission": o["permission"], "x-touches": o["touches"]}
        if o["permission"] == ["Anonymous"]:
            op["security"] = []
        if o["method"] in {"POST", "PUT", "PATCH"}:
            op["requestBody"] = {"required": True, "content": {"application/json": {"schema": {"type": "object"}}}}
        paths.setdefault(o["path"], {})[o["method"].lower()] = op
    doc = {"openapi": "3.1.0",
           "info": {"title": spec["spec"]["name"] + " API", "version": spec["spec"]["version"],
                    "description": "Skeleton generated from the spec; request and response bodies are placeholders."},
           "security": [{"bearerAuth": []}],
           "paths": paths,
           "components": {
               "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}},
               "schemas": {"Problem": {"type": "object", "required": ["type", "title", "status", "code", "traceId"],
                                       "properties": {"type": {"type": "string"}, "title": {"type": "string"},
                                                      "status": {"type": "integer"}, "detail": {"type": "string"},
                                                      "code": {"type": "string"}, "traceId": {"type": "string"},
                                                      "errors": {"type": "array", "items": {"type": "object"}}}}}}}
    return yaml.safe_dump(doc, sort_keys=False, width=100)


def build(spec, outdir):
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "traceability.md": gen_traceability, "acceptance.md": gen_acceptance,
        "error-catalogue.md": gen_errors, "interaction-matrix.md": gen_matrix,
        "permission-matrix.md": gen_permissions, "notifications.md": gen_notifications,
        "analytics-events.md": gen_analytics, "plans.md": gen_plans, "threat-model.md": gen_threats,
        "risk-register.md": gen_risks, "delivery-plan.md": gen_phases, "state-machines.md": gen_states,
        "openapi.yaml": gen_openapi,
    }
    for name, fn in files.items():
        (out / name).write_text(fn(spec), encoding="utf-8")
    print(f"wrote {len(files)} files to {out}/")


def stats(spec):
    parts = [f"{k} {len(v)}" for k, v in spec.items() if isinstance(v, list)]
    print("constructs: " + ", ".join(parts))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("specs", nargs="+", help="YAML files or folders")
    p.add_argument("--schema", default=str(Path(__file__).with_name("spec.schema.json")))
    p.add_argument("--build", metavar="DIR", help="generate documents into DIR when there are no errors")
    p.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = p.parse_args()

    schema = json.loads(Path(args.schema).read_text(encoding="utf-8"))
    spec = load_specs(args.specs)
    report = Report()
    if check_schema(spec, schema, report):
        lint(spec, report)
    report.print()
    if not report.errors:
        stats(spec)
    failed = bool(report.errors) or (args.strict and bool(report.warnings))
    if not failed and args.build:
        build(spec, args.build)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
