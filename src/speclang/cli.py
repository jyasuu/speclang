#!/usr/bin/env python3
"""speclint: validate Spec Language files and generate documents from them.

Usage:
  speclint todo/                      # all *.yaml files in a folder, merged
  speclint a.yaml b.yaml --build build/
  speclint todo/ --strict             # warnings fail the run too

  speclint diff old/ new/ [--md]                # change report between two versions
  speclint sync doc.md --spec todo/ [--check]   # write generated tables into a Markdown file
  speclint watch todo/ [--build DIR] [--sync DOC]  # re-check on every change
  speclint init DIR [--name NAME]               # starter project that passes the linter
  speclint schema [--for todo/] [-o FILE]       # JSON Schema for editors, enriched with your IDs
  speclint gherkin todo/ -o tests/ [--steps]    # feature files and pytest-bdd stubs from acceptance criteria
  speclint import-openapi api.yaml -o spec/     # starting spec from an existing OpenAPI file
  speclint diff old/ new/ --breaking            # also list changes that break API clients

Multiple files are merged: list sections are concatenated, the first `spec` block wins.
A file can pull in others with `include: [../shared-spec]` (paths relative to the file; folders and globs work),
or from git: `include: [git+https://host/org/repo.git@v1.2#path=spec]`, or an https URL of a YAML file.
SPECLANG_CACHE sets the cache folder; SPECLANG_OFFLINE=1 forbids network access.
Exit code: 0 if no errors, 1 otherwise.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml
from jsonschema import Draft7Validator

from . import __version__, remote
from .diff import breaking_changes, format_diff
from .editor import enriched_schema, init_project
from .gherkin import build_behave_steps, build_conftest, build_features, build_steps
from .importer import ImportFileError, import_openapi
from .openapi import gen_openapi
from .typesys import lint_types

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
    ("slos", "id"), ("types", "name"), ("goals", "id"), ("assumptions", "id"), ("personas", "id"), ("flows", "id"),
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

class SpecError(Exception):
    """A problem with the spec files themselves (unreadable YAML, a missing include), not with the spec's content."""


def collect_files(paths):
    files = []
    for p in map(Path, paths):
        if not p.exists():
            raise SpecError(f"{p} does not exist")
        files.extend(sorted(p.glob("*.yaml")) if p.is_dir() else [p])
    return files


def _read_yaml(f):
    try:
        data = yaml.safe_load(Path(f).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        first = str(exc).strip().splitlines()[0] if str(exc).strip() else "invalid YAML"
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark else ""
        raise SpecError(f"{f}: cannot read YAML{where}: {first}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise SpecError(f"{f}: a spec file must be a mapping of sections, not a {type(data).__name__}")
    return data


def _inside(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    return path == root or root in path.parents


def expand_files(paths):
    """Every file that makes up a spec: the given files plus everything they `include`, each once.

    An include is a local path or glob (relative to the including file), `git+URL[@ref][#path=sub]`, or an https URL
    of a YAML file. A file that was fetched from a remote source may only include files from inside that same
    repository (or other remote sources), never your local files.
    """
    seen, out = set(), []

    def visit(f, root=None):
        f = Path(f).resolve()
        if f in seen:
            return
        seen.add(f)
        out.append(f)
        includes = _read_yaml(f).get("include") or []
        if not isinstance(includes, list):
            raise SpecError(f"{f}: 'include' must be a list")
        for inc in includes:
            if remote.is_remote(inc):
                try:
                    target, child_root = remote.fetch(inc)
                except remote.RemoteError as exc:
                    raise SpecError(f"{f}: {exc}") from exc
                matches = [target]
            else:
                child_root = root
                target = f.parent / inc
                matches = sorted(f.parent.glob(inc)) if any(ch in inc for ch in "*?[") else [target]
                matches = [m for m in matches if m.exists()]
                if root is not None and any(not _inside(m, root) for m in matches):
                    raise SpecError(f"{f}: include '{inc}' leaves the fetched repository; "
                                    "a remote spec may only include files from inside itself")
            if not matches:
                raise SpecError(f"{f}: include '{inc}' matches nothing")
            for m in matches:
                for sub in (sorted(m.glob("*.yaml")) if m.is_dir() else [m]):
                    visit(sub, child_root)

    for f in collect_files(paths):
        visit(f)
    return out


class Spec(dict):
    """The merged spec. `sources` maps (section, id) to the files that define it, to explain duplicate IDs."""

    def __init__(self):
        super().__init__()
        self.sources = defaultdict(list)


def load_specs(paths):
    """Merge every spec file (and its includes): lists are concatenated, the first `spec` block wins."""
    id_keys = dict(DUP_CHECKS)
    merged = Spec()
    for f in expand_files(paths):
        label = f"{f.parent.name}/{f.name}"
        for key, value in _read_yaml(f).items():
            if key == "include":
                continue
            if key == "spec":
                merged.setdefault("spec", value)
            elif isinstance(value, list):
                merged.setdefault(key, []).extend(value)
                if key in id_keys:
                    for item in value:
                        if isinstance(item, dict) and id_keys[key] in item:
                            merged.sources[(key, item[id_keys[key]])].append(label)
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
                where = getattr(spec, "sources", {}).get((coll, dup), [])
                report.add("ERROR", "R03", f"{coll}[{dup}]",
                           f"duplicate id (defined in {', '.join(where)})" if len(where) > 1 else "duplicate id")

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

    # R38 / R39 test coverage
    cov = coverage_data(spec)
    for ac in acs:
        if not cov["ac_tests"][ac["id"]]:
            report.add("WARN", "R38", f"acceptance[{ac['id']}]", "no test covers this acceptance criterion")
    for r in reqs:
        if r["priority"] == "M" and cov["status"][r["id"]] == "not tested":
            report.add("WARN", "R39", f"requirements[{r['id']}]", "Must requirement has acceptance criteria but no test covers it, directly or through them")

    # R31-R35 field-level rules (types, request/response, component fields)
    lint_types(spec, report)


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


def coverage_data(spec):
    """Per requirement: acceptance criteria, tests (direct or through its criteria), and a status."""
    ac_tests, direct, ac_for = defaultdict(set), defaultdict(set), defaultdict(set)
    for t in spec.get("tests", []):
        for ref in t["covers"]:
            (ac_tests if ref.startswith("AC-") else direct)[ref].add(t["id"])
    for a in spec.get("acceptance", []):
        for ref in a["covers"]:
            ac_for[ref].add(a["id"])
    for r in spec["requirements"]:
        ac_for[r["id"]].update(r.get("accepts", []))
    acs, tests, status = {}, {}, {}
    for r in spec["requirements"]:
        rid = r["id"]
        acs[rid] = sorted(ac_for[rid])
        tests[rid] = set(direct[rid])
        for ac in acs[rid]:
            tests[rid] |= ac_tests[ac]
        status[rid] = ("no acceptance criterion" if not acs[rid]
                       else "not tested" if not tests[rid] else "covered")
    return {"ac_tests": ac_tests, "acs": acs, "tests": tests, "status": status}


def gen_coverage(spec):
    cov = coverage_data(spec)
    reqs = {r["id"]: r for r in spec["requirements"]}
    phases = spec.get("phases", [])
    summary, sections, delivered = [], [], set()
    for ph in phases:
        ids = [i for i in ph["delivers"] if i in reqs]
        delivered.update(ids)
        counts = Counter(cov["status"][i] for i in ids)
        summary.append([ph["id"], ph["name"], len(ids), counts["covered"], counts["not tested"], counts["no acceptance criterion"]])
        if ids:
            rows = [[i, reqs[i]["priority"], ", ".join(cov["acs"][i]) or "-", ", ".join(sorted(cov["tests"][i])) or "-", cov["status"][i]]
                    for i in ids]
            sections.append(f"## {ph['id']} {ph['name']}\n\n" + md_table(["Requirement", "Pri", "Acceptance", "Tests", "Status"], rows))
    loose = [i for i in reqs if i not in delivered]
    if loose:
        rows = [[i, reqs[i]["priority"], cov["status"][i]] for i in loose]
        sections.append("## Not delivered by any phase\n\n" + md_table(["Requirement", "Pri", "Status"], rows))
    ac_gaps = [a["id"] for a in spec.get("acceptance", []) if not cov["ac_tests"][a["id"]]]
    head = md_table(["Phase", "Name", "Requirements", "Covered", "Not tested", "No acceptance"], summary)
    tail = ("\nAcceptance criteria with no test: " + (", ".join(ac_gaps) if ac_gaps else "none") + "\n")
    return page("Test coverage by phase", head + "\n" + "\n".join(sections) + tail)


def gen_endpoints(spec):
    rows = []
    for o in spec.get("operations", []):
        succ = o.get("success") or (204 if o["method"] == "DELETE" else 200)
        notable = ", ".join(c for c in o["errors"]) or "-"
        rows.append([f"`{o['method']} {o['path']}`", o.get("summary") or o["id"], succ, notable])
    return page("Endpoint summary", md_table(["Method and path", "Operation", "Success", "Notable errors"], rows))


def gen_entities(spec):
    out = []
    for e in spec.get("entities", []):
        rows = [[f["name"], f["type"], "yes" if f.get("required") else "", f.get("note", "")] for f in e["fields"]]
        extra = f"Owner: {e['owner']}. " + (f"Retention: {e['retention']}" if e.get("retention") else "")
        out.append(f"## {e['name']}\n\n{extra}\n\n" + md_table(["Field", "Type", "Required", "Note"], rows))
    return page("Data model", "\n".join(out))


def gen_spec_tables(spec, pieces):
    """One file with every generated table under the section numbers of the prose spec."""
    order = [
        ("4. Acceptance Criteria", "acceptance.md"), ("9.1 Entities", "entities.md"),
        ("11.2 Interaction matrix", "interaction-matrix.md"), ("13.2 Endpoint summary", "endpoints.md"),
        ("14. Error Catalogue", "error-catalogue.md"), ("17.2 Threat model", "threat-model.md"),
        ("19. Notifications", "notifications.md"), ("20. Analytics events", "analytics-events.md"),
        ("22. Plans and limits", "plans.md"), ("29. Delivery plan", "delivery-plan.md"),
        ("29.1 Test coverage by phase", "coverage.md"),
        ("30. Risk register", "risk-register.md"), ("31. Requirement traceability", "traceability.md"),
    ]
    out = [f"# {spec['spec']['name']}: generated tables (paste under the matching sections)\n"]
    for heading, fname in order:
        body = pieces[fname].split("\n", 2)[2]  # drop the generated title line and blank line
        out.append(f"\n## {heading}\n\n{body}")
    return "".join(out)


def build(spec, outdir):
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    generators = {
        "traceability.md": gen_traceability, "acceptance.md": gen_acceptance,
        "error-catalogue.md": gen_errors, "interaction-matrix.md": gen_matrix,
        "permission-matrix.md": gen_permissions, "notifications.md": gen_notifications,
        "analytics-events.md": gen_analytics, "plans.md": gen_plans, "threat-model.md": gen_threats,
        "risk-register.md": gen_risks, "delivery-plan.md": gen_phases, "state-machines.md": gen_states,
        "endpoints.md": gen_endpoints, "entities.md": gen_entities, "coverage.md": gen_coverage,
        "openapi.yaml": gen_openapi,
    }
    pieces = {name: fn(spec) for name, fn in generators.items()}
    pieces["spec-tables.md"] = gen_spec_tables(spec, pieces)
    for name, text in pieces.items():
        (out / name).write_text(text, encoding="utf-8")
    print(f"wrote {len(pieces)} files to {out}/")


def stats(spec):
    parts = [f"{k} {len(v)}" for k, v in spec.items() if isinstance(v, list)]
    print("constructs: " + ", ".join(parts))


BLOCKS = {
    "acceptance": gen_acceptance, "entities": gen_entities, "interaction": gen_matrix,
    "endpoints": gen_endpoints, "errors": gen_errors, "permissions": gen_permissions,
    "threats": gen_threats, "notifications": gen_notifications, "analytics": gen_analytics,
    "plans": gen_plans, "delivery": gen_phases, "risks": gen_risks, "traceability": gen_traceability,
    "coverage": gen_coverage, "states": gen_states,
}
MARKER = re.compile(r"(?P<open><!--\s*speclang:(?P<name>[\w-]+)\s*-->)(?P<body>.*?)(?P<close><!--\s*/speclang:(?P=name)\s*-->)", re.S)


def sync_text(text, spec):
    """Replace the content between speclang markers with freshly generated tables.

    Returns (new_text, updated_names, unknown_names). Text outside the markers is never touched.
    """
    updated, unknown = [], []

    def repl(m):
        name = m.group("name")
        gen = BLOCKS.get(name)
        if gen is None:
            unknown.append(name)
            return m.group(0)
        body = gen(spec).split("\n", 2)[2].strip()
        new = f"{m.group('open')}\n\n{body}\n\n{m.group('close')}"
        if new != m.group(0):
            updated.append(name)
        return new

    return MARKER.sub(repl, text), updated, unknown


def sync_main(argv):
    p = argparse.ArgumentParser(prog="speclint sync", description="Write generated tables into Markdown files between speclang markers.")
    p.add_argument("files", nargs="+", help="Markdown files containing <!-- speclang:NAME --> ... <!-- /speclang:NAME --> markers")
    p.add_argument("--spec", nargs="+", required=True, help="spec YAML files or folders")
    p.add_argument("--check", action="store_true", help="change nothing; exit 1 if any file is out of date")
    args = p.parse_args(argv)
    spec = load_specs(args.spec)
    schema = json.loads(Path(__file__).with_name("spec.schema.json").read_text(encoding="utf-8"))
    report = Report()
    if check_schema(spec, schema, report):
        lint(spec, report)
    if report.errors:
        report.print()
        print("spec has errors; nothing synced")
        sys.exit(1)
    stale = failed = False
    for f in map(Path, args.files):
        text = f.read_text(encoding="utf-8")
        new, updated, unknown = sync_text(text, spec)
        for name in unknown:
            print(f"{f}: unknown block '{name}' (known: {', '.join(sorted(BLOCKS))})")
            failed = True
        if updated:
            stale = True
            print(f"{f}: {'out of date' if args.check else 'updated'}: {', '.join(updated)}")
            if not args.check:
                f.write_text(new, encoding="utf-8")
        else:
            print(f"{f}: up to date")
    sys.exit(1 if failed or (args.check and stale) else 0)


def gherkin_main(argv):
    p = argparse.ArgumentParser(prog="speclint gherkin", description="Generate Gherkin feature files (one per requirement area, one scenario per acceptance criterion).")
    p.add_argument("specs", nargs="+", help="spec YAML files or folders")
    p.add_argument("-o", "--output", required=True, metavar="DIR", help="folder to write into (a features/ folder is created inside)")
    p.add_argument("--steps", nargs="?", const="pytest-bdd", choices=["pytest-bdd", "behave"], metavar="RUNNER",
                   help="also write pending step stubs for a runner: pytest-bdd (default; test_acceptance.py and conftest.py) or behave (features/steps/steps.py)")
    args = p.parse_args(argv)
    spec = load_specs(args.specs)
    schema = json.loads(Path(__file__).with_name("spec.schema.json").read_text(encoding="utf-8"))
    report = Report()
    if check_schema(spec, schema, report):
        lint(spec, report)
    if report.errors:
        report.print()
        print("spec has errors; nothing generated")
        sys.exit(1)
    out = Path(args.output)
    feats = out / "features"
    feats.mkdir(parents=True, exist_ok=True)
    files = build_features(spec)
    for name, text in files.items():
        (feats / name).write_text(text, encoding="utf-8")
    scenarios = sum(text.count("Scenario:") for text in files.values())
    print(f"wrote {len(files)} feature file(s) with {scenarios} scenario(s) to {feats}/")
    if args.steps == "pytest-bdd":
        (out / "test_acceptance.py").write_text(build_steps(spec), encoding="utf-8")
        (out / "conftest.py").write_text(build_conftest(files), encoding="utf-8")
        print(f"wrote {out / 'test_acceptance.py'} and {out / 'conftest.py'}")
    elif args.steps == "behave":
        (feats / "steps").mkdir(exist_ok=True)
        (feats / "steps" / "steps.py").write_text(build_behave_steps(spec), encoding="utf-8")
        print(f"wrote {feats / 'steps' / 'steps.py'}")


def import_main(argv):
    p = argparse.ArgumentParser(prog="speclint import-openapi", description="Create a starting spec from an OpenAPI 3 file (YAML or JSON).")
    p.add_argument("file", help="OpenAPI file")
    p.add_argument("-o", "--output", required=True, metavar="DIR", help="folder for the spec files (must be empty or missing)")
    p.add_argument("--name", help="system name (default: the title in the OpenAPI file)")
    args = p.parse_args(argv)
    doc = yaml.safe_load(Path(args.file).read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or "paths" not in doc:
        print("error: that file does not look like an OpenAPI document (no 'paths')")
        sys.exit(1)
    out = Path(args.output)
    if out.exists() and any(out.iterdir()):
        print(f"error: {out} already exists and is not empty")
        sys.exit(1)
    try:
        files, notes = import_openapi(doc, args.name)
    except ImportFileError as exc:
        raise SpecError(f"{args.file} cannot be imported: {exc}") from exc
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")
        print(f"wrote {out / name}")
    for note in sorted(set(notes)):
        print(f"note: {note}")
    print(f"\nnext: speclint {out}   (expect findings: the file has no requirements, tests or acceptance criteria)")


def require_valid(spec, label):
    """Stop with a readable message if `spec` does not match the schema. For commands that read a spec without linting it."""
    schema = json.loads(Path(__file__).with_name("spec.schema.json").read_text(encoding="utf-8"))
    report = Report()
    if not check_schema(spec, schema, report):
        lines = [f"  {where}: {msg}" for (_lv, _r, where, msg) in report.items[:5]]
        more = f"\n  ... and {len(report.items) - 5} more" if len(report.items) > 5 else ""
        raise SpecError(f"{label} is not a valid spec:\n" + "\n".join(lines) + more + "\n(run speclint on it to see everything)")


def diff_main(argv):
    p = argparse.ArgumentParser(prog="speclint diff", description="Show what changed between two versions of a spec.")
    p.add_argument("old", help="old YAML file or folder")
    p.add_argument("new", help="new YAML file or folder")
    p.add_argument("--md", action="store_true", help="Markdown output")
    p.add_argument("--exit-code", action="store_true", help="exit 1 if there are differences")
    p.add_argument("--breaking", action="store_true", help="list changes that can break API clients; exit 1 if there are any")
    args = p.parse_args(argv)
    old, new = load_specs([args.old]), load_specs([args.new])
    require_valid(old, f"the old spec ({args.old})")
    require_valid(new, f"the new spec ({args.new})")
    text = format_diff(old, new, md=args.md)
    print(text, end="")
    code = 1 if args.exit_code and text != "No differences.\n" else 0
    if args.breaking:
        items = breaking_changes(old, new)
        print(("## Breaking changes" if args.md else "BREAKING CHANGES") + "\n")
        for item in items:
            print(f"{'- ' if args.md else '  '}{item}")
        print(f"\n{len(items)} breaking change(s)" if items else "None.")
        code = 1 if items else code
    sys.exit(code)


def schema_main(argv):
    p = argparse.ArgumentParser(prog="speclint schema", description="Print the JSON Schema, optionally enriched with the IDs of a spec so YAML editors can complete them.")
    p.add_argument("--for", dest="specs", nargs="+", metavar="SPEC", help="spec YAML files or folders to take IDs from")
    p.add_argument("-o", "--output", help="write to a file instead of standard output")
    args = p.parse_args(argv)
    schema = json.loads(Path(__file__).with_name("spec.schema.json").read_text(encoding="utf-8"))
    if args.specs:
        spec = load_specs(args.specs)
        require_valid(spec, "the spec")
        schema = enriched_schema(schema, spec)
    text = json.dumps(schema, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(text, end="")


def init_main(argv):
    p = argparse.ArgumentParser(prog="speclint init", description="Create a starter spec project that already passes the linter.")
    p.add_argument("dir", help="folder to create the project in")
    p.add_argument("--name", default="My System", help="system name (default: My System)")
    args = p.parse_args(argv)
    try:
        created = init_project(args.dir, args.name)
    except FileExistsError as exc:
        print(f"error: {exc}")
        sys.exit(1)
    for rel in created:
        print(f"created {Path(args.dir) / rel}")
    print(f"\nnext: speclint {Path(args.dir) / 'spec'}")


def watch_main(argv):
    from .watch import watch
    p = argparse.ArgumentParser(prog="speclint watch", description="Re-check the spec whenever a file changes (Ctrl-C to stop).")
    p.add_argument("specs", nargs="+", help="YAML files or folders")
    p.add_argument("--build", metavar="DIR", help="regenerate documents into DIR after each clean check")
    p.add_argument("--sync", nargs="+", default=[], metavar="DOC", help="Markdown files to sync after each clean check")
    p.add_argument("--interval", type=float, default=1.0, help="seconds between checks for changes (default 1)")
    args = p.parse_args(argv)
    print(f"watching {', '.join(args.specs)} (Ctrl-C to stop)")
    try:
        watch(args.specs, build_dir=args.build, sync_docs=args.sync, interval=args.interval)
    except KeyboardInterrupt:
        print("\nstopped")


def _main():
    if sys.argv[1:2] == ["diff"]:
        return diff_main(sys.argv[2:])
    if sys.argv[1:2] == ["sync"]:
        return sync_main(sys.argv[2:])
    commands = {"schema": schema_main, "init": init_main, "watch": watch_main,
                "gherkin": gherkin_main, "import-openapi": import_main}
    if sys.argv[1:2] and sys.argv[1] in commands:
        return commands[sys.argv[1]](sys.argv[2:])
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("specs", nargs="+", help="YAML files or folders")
    p.add_argument("--version", action="version", version=f"speclint {__version__}")
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


def main():
    """Entry point. Exits quietly if the output pipe is closed early (for example `speclint ... | head`)."""
    try:
        _main()
    except SpecError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    except BrokenPipeError:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        sys.exit(1)


if __name__ == "__main__":
    main()
