"""Editor support: a schema enriched with the IDs of a specific spec, and a project scaffold."""
import copy
import json
from pathlib import Path

from .typesys import resolve_types


def _ids(spec, coll, key):
    return sorted({i[key] for i in spec.get(coll, [])})


def enriched_schema(base, spec):
    """The base schema with the IDs defined in `spec` listed as allowed values.

    YAML editors that read JSON Schema (VS Code with the YAML extension, JetBrains IDEs) then complete
    and check references such as `implements`, `errors`, `uses`, `delivers` and `shows` while you type.
    Collections that are empty are left unrestricted.
    """
    s = copy.deepcopy(base)
    d = s["definitions"]
    fr, nfr, ac = _ids(spec, "requirements", "id"), _ids(spec, "nfrs", "id"), _ids(spec, "acceptance", "id")
    errs, ops, ents = _ids(spec, "errors", "code"), _ids(spec, "operations", "id"), _ids(spec, "entities", "name")
    types, tests, personas = _ids(spec, "types", "name"), _ids(spec, "tests", "id"), _ids(spec, "personas", "id")
    roles = sorted(spec.get("roles", []))
    pairs = sorted(f"{t}.{f['name']}" for t, fields in resolve_types(spec).items() for f in fields)

    def items(defn, prop, values):
        if values:
            d[defn]["properties"][prop]["items"] = {"enum": values}

    def scalar(defn, prop, values):
        if values:
            d[defn]["properties"][prop] = {"enum": values}

    items("requirement", "accepts", ac)
    items("acceptance", "covers", fr + nfr)
    items("operation", "implements", fr)
    items("operation", "errors", errs)
    items("operation", "permission", roles)
    if ents:
        d["operation"]["properties"]["touches"]["propertyNames"] = {"enum": ents}
    scalar("operation", "request", types)
    scalar("operation", "response", types)
    items("component", "uses", ops)
    items("component", "implements", fr)
    items("component", "shows", pairs)
    items("slo", "covers", nfr)
    items("test", "covers", fr + nfr + ac)
    scalar("threat", "test", tests)
    items("phase", "delivers", fr)
    items("flow", "covers", fr)
    scalar("flow", "persona", personas)
    items("rule", "supports", fr + nfr)
    items("notification", "supports", fr)
    scalar("type", "from", ents)
    s["title"] += " (enriched for this spec)"
    return s


# ------------------------------------------------------------------ scaffold

def _starter(name):
    return {
        "spec/00-meta.yaml": f'''spec:
  name: {name}
  version: "0.1"
  status: draft

roles: [Anonymous, Authenticated]

goals:
  - {{ id: G-1, text: "State the one outcome this system must achieve" }}
''',
        "spec/01-requirements.yaml": '''requirements:
  - { id: FR-1, priority: M, area: status, text: "The system shall return its health status to authenticated callers.", accepts: [AC-1] }

acceptance:
  - { id: AC-1, covers: [FR-1], given: "an authenticated caller", when: "they request the status", then: "the API returns 200 with the service status within 200 ms" }

nfrs:
  - { id: NFR-1, category: latency, text: "API response time", target: "p95 < 200 ms", verify: "Load test" }
''',
        "spec/02-interfaces.yaml": '''errors:
  - { code: UNAUTHENTICATED, status: 401, meaning: "Missing or expired token", client: "Sign in again", global: true }
  - { code: RATE_LIMITED, status: 429, meaning: "Too many requests", client: "Back off for the Retry-After period", global: true }

operations:
  - { id: getStatus, method: GET, path: /status, implements: [FR-1], permission: [Authenticated], errors: [], touches: {} }
''',
        "spec/03-delivery.yaml": '''tests:
  - { id: T-1, level: integration, scope: "Status endpoint", target: "Returns 200 for an authenticated caller", covers: [FR-1, AC-1] }
  - { id: T-2, level: performance, scope: "Load test at 2x expected peak", target: "p95 under 200 ms", covers: [NFR-1] }

phases:
  - { id: PH-1, name: First release, scope: "Status endpoint", delivers: [FR-1], exit: ["Delivered requirements pass"] }
''',
        "docs/spec.md": f'''# {name}: specification

Hand-written prose goes here. The tables below are generated from the YAML in `spec/` by `speclint sync`.

## Acceptance criteria

<!-- speclang:acceptance -->
<!-- /speclang:acceptance -->

## Test coverage

<!-- speclang:coverage -->
<!-- /speclang:coverage -->

## Traceability

<!-- speclang:traceability -->
<!-- /speclang:traceability -->
''',
        ".vscode/settings.json": '''{
  "yaml.schemas": { "./spec.schema.json": ["spec/*.yaml"] }
}
''',
        ".github/workflows/spec.yml": '''name: spec
on:
  pull_request:
    paths: ["spec/**", "docs/**"]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install speclang
      - run: speclint spec/ --strict
      - run: speclint sync docs/spec.md --spec spec/ --check
      - name: Breaking-change check against the base branch
        run: |
          git worktree add /tmp/base "origin/${{ github.base_ref }}" || true
          [ -d /tmp/base/spec ] && speclint diff /tmp/base/spec spec/ --md --breaking >> "$GITHUB_STEP_SUMMARY" || true
''',
        "README.md": f'''# {name}

```
speclint spec/                              # validate
speclint spec/ --build build/               # also generate tables and an OpenAPI file
speclint sync docs/spec.md --spec spec/     # write generated tables into the document
speclint watch spec/ --build build/         # re-check whenever a file changes
speclint schema --for spec/ -o spec.schema.json   # refresh editor completion after adding IDs
```

`spec.schema.json` makes YAML editors complete requirement IDs, error codes, entity names and so on.
Refresh it with the last command above whenever you add new IDs.
''',
    }


def init_project(dest, name):
    """Create a starter project that lints clean. Refuses to overwrite an existing spec folder."""
    from .cli import load_specs  # local import: cli imports this module

    dest = Path(dest)
    if (dest / "spec").exists() and any((dest / "spec").iterdir()):
        raise FileExistsError(f"{dest / 'spec'} already exists and is not empty")
    files = _starter(name)
    for rel, text in files.items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    base = json.loads((Path(__file__).with_name("spec.schema.json")).read_text(encoding="utf-8"))
    schema = enriched_schema(base, load_specs([dest / "spec"]))
    (dest / "spec.schema.json").write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    return sorted(files) + ["spec.schema.json"]
