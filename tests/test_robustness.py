"""Robustness: malformed input must produce a message, never a traceback.

Deterministic mutation tests (fixed seeds). Each mutation deletes, corrupts, swaps or cross-wires one value of a good
spec or OpenAPI file, and the tools must either handle it or reject it with a clear error.
"""
import copy
import json
import random
import shutil
from pathlib import Path

import pytest
import yaml

from speclang import cli, diff as specdiff, editor, gherkin, importer
from speclang.importer import ImportFileError

ROOT = Path(__file__).resolve().parent.parent
TODO = ROOT / "examples" / "todo"
FOREIGN = ROOT / "examples" / "foreign-petstore.yaml"
SCHEMA = json.loads((Path(cli.__file__).with_name("spec.schema.json")).read_text(encoding="utf-8"))

JUNK = [None, "", 0, -1, 10 ** 12, True, False, [], {}, "x" * 300, "é✓😀", "FR-9999", "../../etc", "{id}", "a b",
        [None], {"a": 1}, 3.14, "#/components/schemas/Nope", "#/components/schemas/Task", "integer", "array", "object",
        {"type": "array"}, {"$ref": "#/components/schemas/Task"}, {"$ref": 5}, {"oneOf": []}, {"allOf": [None]},
        {"enum": [1, None]}, {"properties": []}, {"required": "id"}, ["a", 1], {"type": ["string", "null"]}]


def _walk(node, prefix=()):
    yield prefix, node
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, prefix + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, prefix + (i,))


def _get(root, path):
    for p in path:
        root = root[p]
    return root


def mutate(doc, rng, strings):
    m = copy.deepcopy(doc)
    path = rng.choice([p for p, _ in _walk(m) if p])
    parent, key = _get(m, path[:-1]), path[-1]
    kind = rng.choice(["delete", "junk", "junk", "wire", "swap", "dup"])
    if kind == "delete":
        del parent[key]
    elif kind == "junk":
        parent[key] = copy.deepcopy(rng.choice(JUNK))
    elif kind == "wire" and strings:
        parent[key] = rng.choice(strings)
    elif kind == "swap" and isinstance(parent, list) and len(parent) > 1:
        a, b = rng.sample(range(len(parent)), 2)
        parent[a], parent[b] = parent[b], parent[a]
    elif kind == "dup" and isinstance(parent, list):
        parent.append(copy.deepcopy(parent[key]))
    return m, f"{kind} at {'/'.join(map(str, path))}"


@pytest.fixture(scope="module")
def base_spec():
    return json.loads(json.dumps(cli.load_specs([TODO])))


def _all_stages(spec):
    s = cli.Spec()
    s.update(spec)
    for fn in (cli.gen_traceability, cli.gen_acceptance, cli.gen_errors, cli.gen_matrix, cli.gen_permissions,
               cli.gen_coverage, cli.gen_states, cli.gen_endpoints, cli.gen_entities, cli.gen_phases, cli.gen_openapi,
               gherkin.build_features, gherkin.build_steps, gherkin.build_behave_steps,
               lambda sp: editor.enriched_schema(SCHEMA, sp)):
        fn(s)


def test_lint_and_every_generator_survive_mutated_specs(base_spec):
    rng = random.Random(11)
    strings = [v for _, v in _walk(base_spec) if isinstance(v, str)]
    clean = 0
    for _ in range(50):
        m, desc = mutate(base_spec, rng, strings)
        try:
            report = cli.Report()
            if cli.check_schema(m, SCHEMA, report):
                cli.lint(m, report)
            if not report.errors:
                clean += 1
                _all_stages(m)
        except Exception as exc:                                             # noqa: BLE001
            pytest.fail(f"crashed on '{desc}': {type(exc).__name__}: {exc}")
    assert clean > 5                                                        # the mutations are not all rejected up front


def test_diff_and_schema_validate_first_so_they_never_crash(base_spec):
    rng = random.Random(12)
    strings = [v for _, v in _walk(base_spec) if isinstance(v, str)]
    ran = rejected = 0
    for _ in range(10):
        m, desc = mutate(base_spec, rng, strings)
        for label, fn, specs in [("diff", lambda: specdiff.format_diff(base_spec, m), (base_spec, m)),
                                 ("breaking", lambda: specdiff.breaking_changes(m, base_spec), (m, base_spec)),
                                 ("schema", lambda: editor.enriched_schema(SCHEMA, m), (m,))]:
            try:
                for s in specs:
                    cli.require_valid(s, "spec")
                fn()
                ran += 1
            except cli.SpecError:
                rejected += 1
            except Exception as exc:                                         # noqa: BLE001
                pytest.fail(f"{label} crashed on '{desc}': {type(exc).__name__}: {exc}")
    assert ran > 0 and rejected > 0


def test_diff_and_schema_commands_refuse_an_invalid_spec(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "bad"
    shutil.copytree(TODO, bad)
    f = bad / "01-requirements.yaml"
    f.write_text(f.read_text().replace("priority: M, area: accounts", "priority: Z, area: accounts", 1))
    for argv in (["diff", str(TODO), str(bad)], ["diff", str(bad), str(TODO), "--breaking"], ["schema", "--for", str(bad)]):
        monkeypatch.setattr("sys.argv", ["speclint", *argv])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        err = capsys.readouterr().err
        assert exc.value.code == 2 and "not a valid spec" in err and "requirements/0/priority" in err and "Traceback" not in err


# ---- the importer

@pytest.fixture(scope="module")
def openapi_docs(base_spec):
    s = cli.Spec()
    s.update(base_spec)
    docs = [yaml.safe_load(cli.gen_openapi(s)), yaml.safe_load(FOREIGN.read_text())]
    return [json.loads(json.dumps(d)) for d in docs]


@pytest.mark.parametrize("seed", [21, 22])
def test_importer_imports_or_rejects_with_a_message(openapi_docs, seed):
    rng = random.Random(seed)
    strings = [v for d in openapi_docs for _, v in _walk(d) if isinstance(v, str)]
    imported = rejected = 0
    for i in range(110):
        m, desc = mutate(openapi_docs[i % 2], rng, strings)
        try:
            files, _notes = importer.import_openapi(m)
        except ImportFileError as exc:
            assert str(exc)                                                  # always says what is wrong and where
            rejected += 1
            continue
        except Exception as exc:                                             # noqa: BLE001
            pytest.fail(f"importer crashed on '{desc}': {type(exc).__name__}: {exc}")
        imported += 1
        merged = cli.Spec()                                                  # the imported result must also be usable
        for text in files.values():
            for k, v in (yaml.safe_load(text) or {}).items():
                if k == "spec":
                    merged.setdefault("spec", v)
                elif isinstance(v, list):
                    merged.setdefault(k, []).extend(v)
                else:
                    merged[k] = v
        report = cli.Report()
        if cli.check_schema(merged, SCHEMA, report):
            cli.lint(merged, report)
            if not report.errors:
                cli.gen_openapi(merged)
    assert imported > 15 and rejected > 15


def test_circular_and_deep_references_do_not_hang_or_crash():
    def doc(schemas, body="A"):
        ref = {"$ref": f"#/components/schemas/{body}"}
        return {"openapi": "3.0.3", "info": {"title": "T", "version": "1"},
                "paths": {"/x": {"post": {"requestBody": {"content": {"application/json": {"schema": ref}}},
                                          "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": ref}}}}}}},
                "components": {"schemas": schemas}}
    r = lambda n: {"$ref": f"#/components/schemas/{n}"}
    cases = [
        ({"A": r("A")}, "A"),
        ({"A": r("B"), "B": r("A")}, "A"),
        ({"A": {"type": "object", "allOf": [r("A")]}}, "A"),
        ({"A": {"type": "object", "properties": {"child": r("A")}}}, "A"),
        ({"A": {"type": "object", "properties": {"b": r("B")}}, "B": {"type": "object", "properties": {"a": r("A")}}}, "A"),
        ({"A": {"type": "array", "items": r("A")}}, "A"),
        ({**{f"S{i}": {"type": "object", "allOf": [r(f"S{i + 1}")]} for i in range(60)},
          "S60": {"type": "object", "properties": {"x": {"type": "string"}}}}, "S0"),
    ]
    for schemas, body in cases:
        files, _ = importer.import_openapi(doc(schemas, body))
        yaml.safe_load(files["30-types.yaml"])


def test_malformed_openapi_names_the_file_and_the_place(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "api.yaml"
    bad.write_text("openapi: 3.0.3\ninfo: {title: T, version: '1'}\npaths:\n  /x:\n    get:\n      parameters:\n        - {name: q}\n"
                   "      responses: {'200': true}\n")
    monkeypatch.setattr("sys.argv", ["speclint", "import-openapi", str(bad), "-o", str(tmp_path / "out")])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    err = capsys.readouterr().err
    assert exc.value.code == 2 and "api.yaml cannot be imported" in err
    assert "paths//x/get/parameters/0" in err and "'in' is a required property" in err and "Traceback" not in err
    assert not (tmp_path / "out").exists()                                    # nothing half-written


def test_x_implements_must_name_requirements():
    doc = {"openapi": "3.0.3", "info": {"title": "T", "version": "1"},
           "paths": {"/x": {"get": {"x-implements": ["Nope"], "responses": {"200": {"description": "ok"}}}}}}
    with pytest.raises(ImportFileError, match="x-implements"):
        importer.import_openapi(doc)
