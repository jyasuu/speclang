"""Tests for speclint: the todo example must be clean; the broken example must fail the right rules."""
from pathlib import Path

import pytest
import yaml

from speclang import cli

ROOT = Path(__file__).resolve().parent.parent
TODO = ROOT / "examples" / "todo"
BROKEN = ROOT / "examples" / "broken.spec.yaml"


def run_lint(paths):
    spec = cli.load_specs(paths)
    report = cli.Report()
    import json
    schema = json.loads((Path(cli.__file__).with_name("spec.schema.json")).read_text(encoding="utf-8"))
    if cli.check_schema(spec, schema, report):
        cli.lint(spec, report)
    return spec, report


def rules(report, level=None):
    return {r for (lv, r, _w, _m) in report.items if level in (None, lv)}


def test_todo_example_is_clean():
    _spec, report = run_lint([TODO])
    assert report.items == []


def test_todo_example_size():
    spec, _ = run_lint([TODO])
    assert len(spec["requirements"]) == 32
    assert len(spec["operations"]) == 41
    assert len(spec["acceptance"]) == 23
    assert len(spec["types"]) == 45


def test_broken_example_trips_every_rule_family():
    _spec, report = run_lint([BROKEN])
    expected_errors = {"R01", "R02", "R06", "R08", "R10", "R11", "R13", "R14", "R15",
                       "R20", "R21", "R23", "R24", "R25", "R26", "R31", "R32", "R34", "R36", "R37", "R40"}
    assert expected_errors <= rules(report, "ERROR")
    assert {"R04", "R05", "R09", "R12", "R18", "R22", "R33", "R35", "R38"} <= rules(report, "WARN")


def test_schema_rejects_bad_ids():
    bad = {"spec": {"name": "x", "version": "1"},
           "requirements": [{"id": "REQ-1", "priority": "M", "text": "The system shall do a thing."}]}
    import json
    schema = json.loads((Path(cli.__file__).with_name("spec.schema.json")).read_text(encoding="utf-8"))
    report = cli.Report()
    assert cli.check_schema(bad, schema, report) is False
    assert "S00" in rules(report)


def test_missing_acceptance_is_an_error():
    spec, _ = run_lint([TODO])
    drop = {"AC-4", "AC-5"}  # the two criteria that cover FR-10
    spec["acceptance"] = [a for a in spec["acceptance"] if a["id"] not in drop]
    for r in spec["requirements"]:
        r["accepts"] = [a for a in r.get("accepts", []) if a not in drop]
    report = cli.Report()
    cli.lint(spec, report)
    assert any(r == "R01" and "FR-10" in w for (_lv, r, w, _m) in report.items)


def test_build_writes_all_outputs(tmp_path):
    spec, _ = run_lint([TODO])
    cli.build(spec, tmp_path)
    names = {p.name for p in tmp_path.iterdir()}
    assert len(names) == 17
    assert {"openapi.yaml", "traceability.md", "error-catalogue.md", "spec-tables.md", "endpoints.md", "entities.md", "coverage.md"} <= names


def test_generated_openapi_is_valid(tmp_path):
    validator = pytest.importorskip("openapi_spec_validator")
    spec, _ = run_lint([TODO])
    cli.build(spec, tmp_path)
    doc = yaml.safe_load((tmp_path / "openapi.yaml").read_text())
    validator.validate(doc)
    assert sum(len(v) for v in doc["paths"].values()) == 41
    task_create = doc["components"]["schemas"]["TaskCreate"]
    assert set(task_create["required"]) == {"id", "list_id", "title"}
    assert doc["paths"]["/tasks"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("TaskPage")


def test_cli_exit_codes(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["speclint", str(TODO)])
    with pytest.raises(SystemExit) as ok:
        cli.main()
    assert ok.value.code == 0
    monkeypatch.setattr("sys.argv", ["speclint", str(BROKEN)])
    with pytest.raises(SystemExit) as bad:
        cli.main()
    assert bad.value.code == 1


# ---------------------------------------------------------------- v0.2

from speclang import typesys, diff as specdiff


def test_type_mini_language():
    names = {"Task"}
    assert typesys.type_to_schema("uuid")["format"] == "uuid"
    assert typesys.type_to_schema("string[1..500]") == {"type": "string", "minLength": 1, "maxLength": 500}
    assert typesys.type_to_schema("owner | editor | viewer")["enum"] == ["owner", "editor", "viewer"]
    assert typesys.type_to_schema("Task[]", names)["items"] == {"$ref": "#/components/schemas/Task"}
    assert typesys.type_to_schema("Nope") is None
    assert typesys.type_to_schema("strng[]") is None


def test_types_derive_from_entities():
    spec, _ = run_lint([TODO])
    fields = {f["name"]: f for f in typesys.resolve_types(spec)["TaskCreate"]}
    assert "deleted_at" not in fields and "version" not in fields      # not part of a create request
    assert fields["title"]["required"] and not fields["description"]["required"]
    assert "tags" in fields and "reminder_at" in fields                # added fields


def test_field_linking_follows_nested_types():
    spec, _ = run_lint([TODO])
    assert "Task.title" in typesys.reachable(spec, "CompleteTaskResult")   # through the `task` field
    assert "Task.title" not in typesys.reachable(spec, "UserProfile")


def _mutate(spec_edit):
    spec, _ = run_lint([TODO])
    spec_edit(spec)
    report = cli.Report()
    cli.lint(spec, report)
    return report


def test_component_cannot_show_a_field_no_operation_returns():
    def edit(spec):
        for c in spec["components"]:
            if c["name"] == "TagPicker":
                c["shows"] = ["Tag.name", "Tag.color"]
    report = _mutate(edit)
    assert any(r == "R34" and "TagPicker" in w for (_lv, r, w, _m) in report.items)


def test_unknown_response_type_is_an_error():
    def edit(spec):
        next(o for o in spec["operations"] if o["id"] == "getTask")["response"] = "Taskk"
    assert "R32" in rules(_mutate(edit), "ERROR")


def test_diff_reports_changes_and_impact():
    old, _ = run_lint([TODO])
    new, _ = run_lint([TODO])
    next(r for r in new["requirements"] if r["id"] == "FR-12")["text"] = "A user shall complete or reopen a task and the actor is recorded."
    new["errors"] = [e for e in new["errors"] if e["code"] != "ALREADY_MEMBER"]
    text = specdiff.format_diff(old, new)
    assert "changed  FR-12" in text and "removed  ALREADY_MEMBER" in text
    assert "completeTask" in text and "AC-6" in text           # impact line
    assert specdiff.format_diff(old, old) == "No differences.\n"


def test_diff_command_exit_code(monkeypatch):
    monkeypatch.setattr("sys.argv", ["speclint", "diff", str(TODO), str(TODO), "--exit-code"])
    with pytest.raises(SystemExit) as same:
        cli.main()
    assert same.value.code == 0


# ---------------------------------------------------------------- v0.3

import shutil
from jsonschema import Draft202012Validator

DOC = ROOT / "examples" / "docs" / "todo-spec.md"


def _valid(schema, payload):
    return not list(Draft202012Validator(schema).iter_errors(payload))


def test_constraint_schemas():
    one = {"allOf": typesys.constraint_schema([{"exactly_one": ["a", "b"]}])}
    assert _valid(one, {"a": 1}) and _valid(one, {"b": 1})
    assert not _valid(one, {}) and not _valid(one, {"a": 1, "b": 2})
    least = {"allOf": typesys.constraint_schema([{"at_least_one": ["a", "b"]}])}
    assert _valid(least, {"a": 1, "b": 2}) and not _valid(least, {})
    most = {"allOf": typesys.constraint_schema([{"at_most_one": ["a", "b"]}])}
    assert _valid(most, {}) and _valid(most, {"a": 1}) and not _valid(most, {"a": 1, "b": 2})
    req = {"allOf": typesys.constraint_schema([{"requires": {"a": ["b"]}}])}
    assert _valid(req, {"b": 1}) and _valid(req, {"a": 1, "b": 2}) and not _valid(req, {"a": 1})


def test_examples_are_validated_against_their_type():
    def edit(spec):
        next(t for t in spec["types"] if t["name"] == "InviteRequest")["example"] = {"email": "x@y.z", "role": "admin"}
    assert "R36" in rules(_mutate(edit), "ERROR")

    def edit_both(spec):
        next(t for t in spec["types"] if t["name"] == "ReminderCreate")["example"] = {"offset_minutes": 5, "remind_at": "2026-11-01T09:00:00Z"}
    assert "R36" in rules(_mutate(edit_both), "ERROR")           # breaks exactly_one


def test_constraint_must_name_real_fields():
    def edit(spec):
        next(t for t in spec["types"] if t["name"] == "ListPatch")["constraints"] = [{"at_least_one": ["name", "nope"]}]
    assert "R37" in rules(_mutate(edit), "ERROR")


def test_openapi_carries_constraints_and_examples(tmp_path):
    spec, _ = run_lint([TODO])
    schemas = typesys.type_schemas(spec)
    assert schemas["ReminderCreate"]["allOf"][0]["oneOf"][0] == {"required": ["remind_at"]}
    assert schemas["TaskCreate"]["examples"][0]["title"] == "Renew passport"


def test_coverage_rules_and_report():
    def edit(spec):
        for t in spec["tests"]:
            t["covers"] = [c for c in t["covers"] if c != "AC-4"]
    report = _mutate(edit)
    assert any(r == "R38" and "AC-4" in w for (_lv, r, w, _m) in report.items)
    spec, _ = run_lint([TODO])
    cov = cli.coverage_data(spec)
    assert cov["status"]["FR-10"] == "covered" and cov["status"]["FR-23"] == "no acceptance criterion"
    text = cli.gen_coverage(spec)
    assert "## PH-1 MVP" in text and "Acceptance criteria with no test: none" in text


def _run(monkeypatch, *argv):
    """Run the CLI and return its exit code: 0 when it returns normally, otherwise the code it exits with."""
    monkeypatch.setattr("sys.argv", ["speclint", *map(str, argv)])
    try:
        cli.main()
    except SystemExit as exc:
        return exc.code
    return 0


def test_sync_is_idempotent_and_check_detects_drift(tmp_path, monkeypatch):
    doc = tmp_path / "doc.md"
    shutil.copy(DOC, doc)
    assert _run(monkeypatch, "sync", doc, "--spec", TODO) == 0
    first = doc.read_text()
    assert _run(monkeypatch, "sync", doc, "--spec", TODO) == 0
    assert doc.read_text() == first                              # idempotent
    assert _run(monkeypatch, "sync", doc, "--spec", TODO, "--check") == 0
    changed = tmp_path / "spec"
    shutil.copytree(TODO, changed)
    f = changed / "01-requirements.yaml"
    f.write_text(f.read_text().replace("the completion time is recorded.", "the completion time and actor are recorded."))
    assert _run(monkeypatch, "sync", doc, "--spec", changed, "--check") == 1
    assert doc.read_text() == first                              # --check never writes


def test_sync_only_touches_marked_blocks(tmp_path, monkeypatch):
    doc = tmp_path / "doc.md"
    doc.write_text("# Intro\n\nHand written.\n\n<!-- speclang:risks -->\nOLD\n<!-- /speclang:risks -->\n\nTail text.\n")
    _run(monkeypatch, "sync", doc, "--spec", TODO)
    out = doc.read_text()
    assert out.startswith("# Intro\n\nHand written.") and out.rstrip().endswith("Tail text.")
    assert "OLD" not in out and "RK-1" in out


def test_sync_refuses_a_spec_with_errors(tmp_path, monkeypatch):
    broken = tmp_path / "spec"
    shutil.copytree(TODO, broken)
    f = broken / "03-interfaces.yaml"
    f.write_text("\n".join(l for l in f.read_text().split("\n") if "code: ALREADY_DONE" not in l))
    doc = tmp_path / "doc.md"
    shutil.copy(DOC, doc)
    before = doc.read_text()
    assert _run(monkeypatch, "sync", doc, "--spec", broken) == 1
    assert doc.read_text() == before                             # regression: used to write anyway


def test_sync_unknown_block_fails(tmp_path, monkeypatch):
    doc = tmp_path / "doc.md"
    doc.write_text("<!-- speclang:nonsense -->\n<!-- /speclang:nonsense -->\n")
    assert _run(monkeypatch, "sync", doc, "--spec", TODO) == 1


def test_version_flag(monkeypatch, capsys):
    assert _run(monkeypatch, "--version") == 0
    assert "0.6.0" in capsys.readouterr().out


# ---------------------------------------------------------------- v0.4

import json
import subprocess
import sys

from jsonschema import Draft7Validator
from speclang import editor, watch as watchmod


def test_value_checks_become_schema_keywords_and_x_checks():
    spec, _ = run_lint([TODO])
    schemas = typesys.type_schemas(spec)
    size = schemas["UploadUrlRequest"]["properties"]["size_bytes"]
    assert size["exclusiveMinimum"] == 0 and size["maximum"] == 10485760
    assert schemas["ReminderCreate"]["properties"]["offset_minutes"]["minimum"] == 0
    assert schemas["TaskCreate"]["x-checks"] == [
        {"rule": "reminder_at <= due_at", "message": "The reminder must not be after the due date"}]


def test_field_to_field_check_compares_timestamps():
    spec, _ = run_lint([TODO])
    ok = {"id": "x", "list_id": "y", "title": "t", "due_at": "2026-11-02T09:00:00Z", "reminder_at": "2026-11-01T09:00:00Z"}
    late = {**ok, "reminder_at": "2026-11-03T09:00:00Z"}
    assert typesys.check_violations(spec, "TaskCreate", ok) == []
    assert typesys.check_violations(spec, "TaskCreate", late) == ["The reminder must not be after the due date"]
    assert typesys.check_violations(spec, "TaskCreate", {"title": "t"}) == []     # absent fields are not compared


def test_example_that_breaks_a_check_is_an_error():
    def edit(spec):
        t = next(x for x in spec["types"] if x["name"] == "TaskCreate")
        t["example"] = {**t["example"], "reminder_at": "2027-01-01T00:00:00Z"}
    assert "R36" in rules(_mutate(edit), "ERROR")

    def edit_numeric(spec):
        next(x for x in spec["types"] if x["name"] == "UploadUrlRequest")["example"]["size_bytes"] = 10 ** 11
    assert "R36" in rules(_mutate(edit_numeric), "ERROR")


def test_malformed_checks_are_reported_not_crashed():
    """Regression: a numeric field compared with a string used to raise TypeError inside the linter."""
    def edit(spec):
        t = next(x for x in spec["types"] if x["name"] == "ReminderCreate")
        t["checks"] = [{"left": "offset_minutes", "op": ">", "value": "zero"},
                       {"left": "nope", "op": "<", "value": 1},
                       {"left": "channel", "op": "<", "right": "offset_minutes"}]
    report = _mutate(edit)
    assert [r for (_lv, r, w, _m) in report.items if r == "R40" and "ReminderCreate" in w] == ["R40"] * 3


def test_enriched_schema_completes_ids_and_flags_unknown_ones():
    spec, _ = run_lint([TODO])
    base = json.loads((Path(cli.__file__).with_name("spec.schema.json")).read_text(encoding="utf-8"))
    schema = editor.enriched_schema(base, spec)
    assert list(Draft7Validator(schema).iter_errors(spec)) == []
    assert "FR-12" in schema["definitions"]["operation"]["properties"]["implements"]["items"]["enum"]
    bad = json.loads(json.dumps(spec, default=str))
    bad["operations"][0]["errors"] = ["NOT_A_REAL_ERROR"]
    bad["operations"][1]["implements"] = ["FR-999"]
    bad["operations"][2]["touches"] = {"NoSuchEntity": "C"}
    bad["components"][0]["uses"] = ["noSuchOperation"]
    bad["phases"][1]["delivers"] = ["FR-404"]
    assert len(list(Draft7Validator(schema).iter_errors(bad))) == 5


def test_schema_command_writes_enriched_schema(tmp_path, monkeypatch):
    out = tmp_path / "s.json"
    monkeypatch.setattr("sys.argv", ["speclint", "schema", "--for", str(TODO), "-o", str(out)])
    cli.main()                                                           # returns normally, no SystemExit
    schema = json.loads(out.read_text())
    assert "enriched" in schema["title"]
    assert "FR-12" in schema["definitions"]["operation"]["properties"]["implements"]["items"]["enum"]
    monkeypatch.setattr("sys.argv", ["speclint", "schema"])               # without --for: the plain schema
    cli.main()
    assert "enriched" not in json.loads((Path(cli.__file__).with_name("spec.schema.json")).read_text())["title"]


def test_init_creates_a_project_that_lints_clean(tmp_path, monkeypatch):
    dest = tmp_path / "proj"
    monkeypatch.setattr("sys.argv", ["speclint", "init", str(dest), "--name", "Billing"])
    cli.main()
    for rel in ["spec/00-meta.yaml", "docs/spec.md", ".vscode/settings.json", ".github/workflows/spec.yml", "spec.schema.json"]:
        assert (dest / rel).exists(), rel
    assert "Billing" in (dest / "spec" / "00-meta.yaml").read_text()
    assert _run(monkeypatch, dest / "spec", "--strict") == 0              # no errors and no warnings
    assert _run(monkeypatch, "sync", dest / "docs" / "spec.md", "--spec", dest / "spec") == 0
    assert "AC-1" in (dest / "docs" / "spec.md").read_text()
    monkeypatch.setattr("sys.argv", ["speclint", "init", str(dest)])      # second run must refuse
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1


def test_watch_reacts_to_edits_and_survives_bad_yaml(tmp_path):
    spec = tmp_path / "spec"
    shutil.copytree(TODO, spec)
    target = spec / "03-interfaces.yaml"
    original = target.read_text()
    steps = iter([original.replace("ALREADY_DONE, VERSION_CONFLICT, INVALID_COMPLETED_AT]", "ALREADY_DONE, VERSION_CONFLICT, MADE_UP]"),
                  "operations: [\n  - { id: oops",
                  original])
    import time as _t

    def sleeper(_):
        _t.sleep(0.01)                                                   # ensure a new modification time
        target.write_text(next(steps))

    lines = []
    results = watchmod.watch([spec], build_dir=tmp_path / "build", interval=0, max_runs=4, max_polls=30,
                             sleep=sleeper, out=lines.append)
    assert results[0] == (0, 0)
    assert results[1][0] == 1                                            # lint error found
    assert results[2] is None                                            # invalid YAML reported, not raised
    assert results[3] == (0, 0)                                          # recovered
    assert any("MADE_UP" in l for l in lines) and (tmp_path / "build" / "openapi.yaml").exists()


def test_breaking_changes_are_found():
    from speclang.diff import breaking_changes
    old, _ = run_lint([TODO])
    new, _ = run_lint([TODO])
    new["operations"] = [o for o in new["operations"] if o["id"] != "listActivity"]
    next(o for o in new["operations"] if o["id"] == "reopenTask")["permission"] = ["Owner"]
    next(o for o in new["operations"] if o["id"] == "getTask")["concurrency"] = "if-match"
    next(e for e in new["errors"] if e["code"] == "VERSION_CONFLICT")["status"] = 409
    next(t for t in new["types"] if t["name"] == "TaskCreate")["required"] = ["id", "list_id", "title", "priority"]
    next(t for t in new["types"] if t["name"] == "Task")["omit"] = ["deleted_at", "due_at"]
    text = "\n".join(breaking_changes(old, new))
    for expected in ["operation listActivity was removed", "reopenTask no longer allows Editor",
                     "getTask now requires an If-Match", "VERSION_CONFLICT changed HTTP status",
                     "TaskCreate.priority became required", "response field Task.due_at was removed"]:
        assert expected in text, expected


def test_additive_changes_are_not_breaking():
    from speclang.diff import breaking_changes
    old, _ = run_lint([TODO])
    new, _ = run_lint([TODO])
    new["errors"].append({"code": "BRAND_NEW", "status": 418, "meaning": "x", "client": "y"})
    new["operations"][0]["errors"].append("BRAND_NEW")
    next(t for t in new["types"] if t["name"] == "Task")["fields"].append({"name": "color", "type": "string"})
    assert breaking_changes(old, new) == []


def test_diff_breaking_exit_code(monkeypatch, tmp_path):
    changed = tmp_path / "spec"
    shutil.copytree(TODO, changed)
    f = changed / "03-interfaces.yaml"
    f.write_text("\n".join(l for l in f.read_text().split("\n") if "id: listActivity," not in l))
    assert _run(monkeypatch, "diff", TODO, changed, "--breaking") == 1
    assert _run(monkeypatch, "diff", TODO, TODO, "--breaking") == 0


def test_closed_output_pipe_exits_quietly():
    """Run in a child process: the handler redirects the real stdout descriptor, which must not happen in pytest."""
    code = ("from speclang import cli\n"
            "def boom():\n    raise BrokenPipeError\n"
            "cli._main = boom\n"
            "cli.main()\n")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 1
    assert "Traceback" not in result.stderr


# ---------------------------------------------------------------- v0.5

from speclang import gherkin, importer

FOREIGN = ROOT / "examples" / "foreign-petstore.yaml"


def test_features_have_one_scenario_per_acceptance_criterion():
    spec, _ = run_lint([TODO])
    files = gherkin.build_features(spec)
    text = "\n".join(files.values())
    assert text.count("Scenario:") == len(spec["acceptance"]) == 23
    assert {"accounts.feature", "tasks.feature", "sync.feature"} <= set(files)
    assert "@AC-4 @FR-10 @priority-M @PH-1 @T-4" in files["tasks.feature"]       # criterion, requirement, priority, phase, test
    assert "Given a signed-in Editor\n    When they add a task with a 1 to 500 character title" in files["tasks.feature"]


def test_step_stubs_cover_every_distinct_step():
    spec, _ = run_lint([TODO])
    code = gherkin.build_steps(spec)
    compile(code, "test_acceptance.py", "exec")                                  # valid Python
    steps = {("given", a["given"]) for a in spec["acceptance"]} | {("when", a["when"]) for a in spec["acceptance"]} \
        | {("then", a["then"]) for a in spec["acceptance"]}
    assert code.count("def step_") == len(steps)
    conftest = gherkin.build_conftest(gherkin.build_features(spec))
    compile(conftest, "conftest.py", "exec")
    assert "'AC-4'," in conftest and "'priority-M'," in conftest and "'PH-1'," in conftest


def test_generated_scenarios_run_under_pytest_bdd(tmp_path, monkeypatch):
    """A real Gherkin runner must accept every feature file and find a step definition for every step."""
    pytest.importorskip("pytest_bdd")
    assert _run(monkeypatch, "gherkin", TODO, "-o", tmp_path, "--steps") == 0
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--strict-markers"],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "23 skipped" in result.stdout and "error" not in result.stdout.lower()
    picked = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-m", "PH-2 and priority-M"],
                            cwd=tmp_path, capture_output=True, text=True)
    assert "4 skipped, 19 deselected" in picked.stdout


def test_gherkin_command_refuses_a_spec_with_errors(tmp_path, monkeypatch):
    bad = tmp_path / "spec"
    shutil.copytree(TODO, bad)
    f = bad / "03-interfaces.yaml"
    f.write_text("\n".join(l for l in f.read_text().split("\n") if "code: ALREADY_DONE" not in l))
    out = tmp_path / "out"
    assert _run(monkeypatch, "gherkin", bad, "-o", out) == 1
    assert not out.exists()


# ---- importer

def _roundtrip(tmp_path):
    spec, _ = run_lint([TODO])
    original = yaml.safe_load(cli.gen_openapi(spec))
    files, notes = importer.import_openapi(original)
    out = tmp_path / "imported"
    out.mkdir()
    for name, text in files.items():
        (out / name).write_text(text)
    imported = cli.load_specs([out])
    return original, yaml.safe_load(cli.gen_openapi(imported)), imported, out


def test_import_round_trip_is_exact(tmp_path):
    """The whole OpenAPI document, every key: constraints, checks, examples and descriptions included."""
    original, rebuilt, imported, _out = _roundtrip(tmp_path)
    assert sum(len(v) for v in original["paths"].values()) == 41 and len(original["components"]["schemas"]) == 46
    assert original == rebuilt
    report = cli.Report()
    cli.lint(imported, report)
    assert report.errors == []                                           # warnings only: no tx steps, no retention
    assert rules(report) <= {"R12", "R28"}


def test_round_trip_comparison_can_actually_fail(tmp_path):
    """Negative controls: each kind of loss must make the whole-document comparison fail."""
    import copy
    original, rebuilt, _imported, _out = _roundtrip(tmp_path)
    assert original == rebuilt
    schemas = lambda d: d["components"]["schemas"]

    def mutated(edit):
        d = copy.deepcopy(rebuilt)
        edit(d)
        return d
    assert original != mutated(lambda d: d["paths"]["/tasks"]["get"]["parameters"][0].update(required=True))
    assert original != mutated(lambda d: schemas(d)["Task"]["properties"]["title"].update(type="integer"))
    assert original != mutated(lambda d: schemas(d)["TaskCreate"].pop("examples"))
    assert original != mutated(lambda d: schemas(d)["TaskCreate"].pop("x-checks"))
    assert original != mutated(lambda d: schemas(d)["ReminderCreate"].pop("allOf"))
    assert original != mutated(lambda d: schemas(d)["UploadUrlRequest"]["properties"]["size_bytes"].pop("maximum"))
    assert original != mutated(lambda d: d["info"].update(title="Todo App API API"))


def test_import_of_a_foreign_api(tmp_path):
    files, notes = importer.import_openapi(yaml.safe_load(FOREIGN.read_text()))
    out = tmp_path / "pets"
    out.mkdir()
    for name, text in files.items():
        (out / name).write_text(text)
    spec = cli.load_specs([out])
    ops = {o["id"]: o for o in spec["operations"]}
    assert set(ops) == {"getPets", "postPets", "getPetById", "patchPetsByPetid", "deletePetsByPetid", "getHealth"}
    assert ops["getHealth"]["permission"] == ["Anonymous"] and ops["getPets"]["permission"] == ["Authenticated"]
    assert ops["postPets"]["success"] == 201 and ops["postPets"]["idempotent"] is True
    assert ops["patchPetsByPetid"]["request"] == "PetUpdate"                     # allOf was merged, not dropped
    assert {f["name"] for t in spec["types"] if t["name"] == "PetUpdate" for f in t["fields"]} == {"id", "email"}
    assert {e["code"] for e in spec["errors"]} == {"HTTP_400", "HTTP_404", "HTTP_409"}
    assert any("oneOf" in n for n in notes) and any("Status" in n for n in notes)  # simplifications are reported


def test_foreign_import_reports_the_missing_concurrency_control(tmp_path):
    files, _ = importer.import_openapi(yaml.safe_load(FOREIGN.read_text()))
    out = tmp_path / "pets"
    out.mkdir()
    for name, text in files.items():
        (out / name).write_text(text)
    _spec, report = run_lint([out])
    assert [(r, w) for (_lv, r, w, _m) in report.errors] == [("R11", "operations[patchPetsByPetid]")]
    fixed = (out / "20-api.yaml").read_text().replace("concurrency: none", "concurrency: if-match")
    (out / "20-api.yaml").write_text(fixed)
    assert run_lint([out])[1].items == []


def test_import_command_guards(tmp_path, monkeypatch):
    out = tmp_path / "spec"
    assert _run(monkeypatch, "import-openapi", FOREIGN, "-o", out) == 0
    assert (out / "20-api.yaml").exists()
    assert _run(monkeypatch, "import-openapi", FOREIGN, "-o", out) == 1            # not empty
    notapi = tmp_path / "x.yaml"
    notapi.write_text("hello: world\n")
    assert _run(monkeypatch, "import-openapi", notapi, "-o", tmp_path / "other") == 1


# ---------------------------------------------------------------- v0.6

import textwrap

MULTI = ROOT / "examples" / "multi" / "billing-app" / "spec"


def test_include_merges_a_shared_spec():
    spec, report = run_lint([MULTI])
    assert report.items == []
    assert {e["code"] for e in spec["errors"]} == {"UNAUTHENTICATED", "RATE_LIMITED", "FORBIDDEN_ROLE", "VALIDATION_FAILED"}
    assert spec["roles"] == ["Anonymous", "Authenticated", "Admin"]
    assert spec["spec"]["name"] == "Billing App"                         # the including file's own spec block wins
    assert "include" not in spec


def test_include_is_needed_for_shared_definitions(tmp_path):
    f = tmp_path / "s.yaml"
    f.write_text("\n".join(l for l in (MULTI / "00-billing.yaml").read_text().split("\n") if not l.startswith(("include:", "  - ../../platform"))))
    _spec, report = run_lint([f])
    assert any(r == "R08" and "voidInvoice" in w for (_lv, r, w, _m) in report.errors)


def test_include_forms_cycles_and_duplicates(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "a.yaml").write_text("roles: [A]\ninclude: [../app]\n")            # includes its includer: a cycle
    (tmp_path / "shared" / "b.yaml").write_text("roles: [B]\n")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "x.yaml").write_text("spec: {name: X, version: '1'}\nroles: [X]\ninclude: ['../shared/*.yaml', '../shared']\n")
    spec = cli.load_specs([tmp_path / "app"])
    assert sorted(spec["roles"]) == ["A", "B", "X"]                       # glob and folder both used; each file read once


def test_include_errors_are_clear(tmp_path):
    (tmp_path / "x.yaml").write_text("include: [nowhere]\n")
    with pytest.raises(cli.SpecError, match="matches nothing"):
        cli.load_specs([tmp_path])
    (tmp_path / "x.yaml").write_text("include: nowhere\n")
    with pytest.raises(cli.SpecError, match="must be a list"):
        cli.load_specs([tmp_path])


def test_duplicate_id_across_included_folders_is_reported(tmp_path):
    shutil.copytree(ROOT / "examples" / "multi", tmp_path / "multi")
    (tmp_path / "multi" / "billing-app" / "spec" / "01-extra.yaml").write_text(
        "errors:\n  - { code: FORBIDDEN_ROLE, status: 403, meaning: again, client: x }\n")
    _spec, report = run_lint([tmp_path / "multi" / "billing-app" / "spec"])
    assert any(r == "R03" and "FORBIDDEN_ROLE" in w for (_lv, r, w, _m) in report.errors)


def test_watch_notices_a_change_in_an_included_folder(tmp_path):
    shutil.copytree(ROOT / "examples" / "multi", tmp_path / "multi")
    spec_dir = tmp_path / "multi" / "billing-app" / "spec"
    shared = tmp_path / "multi" / "platform" / "00-platform.yaml"
    assert str(shared.resolve()) in watchmod.snapshot([spec_dir])          # the included file is being watched
    original = shared.read_text()
    import time as _t

    def sleeper(_):
        _t.sleep(0.01)
        shared.write_text(original.replace("FORBIDDEN_ROLE", "FORBIDDEN_ROLE_RENAMED"))

    lines = []
    results = watchmod.watch([spec_dir], interval=0, max_runs=2, max_polls=10, sleep=sleeper, out=lines.append)
    assert results[0] == (0, 0) and results[1][0] >= 1                     # the rename broke voidInvoice's error reference


def test_unreadable_spec_files_give_a_message_not_a_traceback(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("requirements: [\n  - { id: oops")
    assert _run(monkeypatch, bad) == 2
    err = capsys.readouterr().err
    assert "cannot read YAML" in err and "line 2" in err and "Traceback" not in err
    assert _run(monkeypatch, tmp_path / "missing") == 2
    listing = tmp_path / "list.yaml"
    listing.write_text("- a\n- b\n")
    assert _run(monkeypatch, listing) == 2
    assert "mapping" in capsys.readouterr().err


# ---- importer: constraints, checks, examples

def _import_one(schema_doc):
    doc = {"openapi": "3.0.3", "info": {"title": "T", "version": "1"},
           "paths": {"/x": {"post": {"requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Thing"}}}},
                                     "responses": {"201": {"description": "ok"}}}}},
           "components": {"schemas": {"Thing": schema_doc}}}
    files, notes = importer.import_openapi(doc)
    return yaml.safe_load(files["30-types.yaml"])["types"][0], notes


def test_import_reads_all_four_constraint_forms():
    base = {"type": "object", "properties": {k: {"type": "string"} for k in "abc"}}
    one = {"oneOf": [{"required": ["a"]}, {"required": ["b"]}]}
    least = {"anyOf": [{"required": ["a"]}, {"required": ["b"]}]}
    most = {"oneOf": [{"not": {"anyOf": [{"required": ["a"]}, {"required": ["b"]}]}}, {"required": ["a"]}, {"required": ["b"]}]}
    req = {"dependentRequired": {"a": ["b", "c"]}}
    t, notes = _import_one({**base, "allOf": [one, least, most, req]})
    assert t["constraints"] == [{"exactly_one": ["a", "b"]}, {"at_least_one": ["a", "b"]},
                                {"at_most_one": ["a", "b"]}, {"requires": {"a": ["b", "c"]}}]


def test_import_reads_checks_from_limits_and_x_checks():
    t, notes = _import_one({
        "type": "object",
        "properties": {"qty": {"type": "integer", "minimum": 1, "maximum": 100},
                       "price": {"type": "number", "minimum": 0, "exclusiveMinimum": True},   # OpenAPI 3.0 spelling
                       "start": {"type": "string", "format": "date-time"}, "end": {"type": "string", "format": "date-time"}},
        "x-checks": [{"rule": "start <= end", "message": "Start must not be after end"},
                     {"rule": "qty <= 100", "message": "At most 100"},
                     {"rule": "not a rule at all"}]})
    checks = t["checks"]
    assert {"left": "start", "op": "<=", "right": "end", "message": "Start must not be after end"} in checks
    assert {"left": "qty", "op": "<=", "value": 100, "message": "At most 100"} in checks      # message kept, not duplicated
    assert len([c for c in checks if c["left"] == "qty" and c["op"] == "<="]) == 1
    assert {"left": "qty", "op": ">=", "value": 1} in checks
    assert {"left": "price", "op": ">", "value": 0} in checks                                   # exclusiveMinimum: true
    assert any("could not be imported" in n for n in notes)


def test_import_reads_examples_and_the_lint_checks_them(tmp_path):
    good, _ = _import_one({"type": "object", "properties": {"a": {"type": "string"}}, "example": {"a": "x"}})
    assert good["example"] == {"a": "x"}
    listed, _ = _import_one({"type": "object", "properties": {"a": {"type": "string"}}, "examples": [{"a": "y"}]})
    assert listed["example"] == {"a": "y"}                                                      # OpenAPI 3.1 spelling
    # an example that breaks the schema's own constraint (neither a nor b is present) must be reported by the lint
    bad = {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
           "example": {"qty": 1}, "allOf": [{"anyOf": [{"required": ["a"]}, {"required": ["b"]}]}]}
    doc = {"openapi": "3.0.3", "info": {"title": "T", "version": "1"},
           "paths": {"/x": {"post": {"requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Thing"}}}},
                                     "responses": {"201": {"description": "ok"}}}}},
           "components": {"schemas": {"Thing": bad}}}
    files, _ = importer.import_openapi(doc)
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    _spec, report = run_lint([tmp_path])
    assert any(r == "R36" and "Thing" in w for (_lv, r, w, _m) in report.errors)


def test_import_strips_the_generated_api_suffix():
    files, _ = importer.import_openapi({"openapi": "3.0.3", "info": {"title": "Pet Store API", "version": "1"}, "paths": {}})
    assert yaml.safe_load(files["00-meta.yaml"])["spec"]["name"] == "Pet Store"


def test_numeric_check_messages_survive_in_x_checks():
    spec, _ = run_lint([TODO])
    schema = typesys.type_schemas(spec)["UploadUrlRequest"]
    assert schema["properties"]["size_bytes"]["maximum"] == 10485760
    assert {"rule": "size_bytes <= 10485760", "message": "Attachments are limited to 10 MB (FR-16)"} in schema["x-checks"]


# ---- behave

def test_behave_steps_escape_braces_and_compile():
    spec = {"spec": {"name": "B", "version": "1"},
            "requirements": [{"id": "FR-1", "priority": "S", "area": "things", "text": "The system shall render templates."}],
            "acceptance": [{"id": "AC-1", "covers": ["FR-1"], "given": 'a template "Hello {name}"', "when": "rendered for {user_id}", "then": "no {placeholders} remain"}]}
    code = gherkin.build_behave_steps(spec)
    compile(code, "steps.py", "exec")
    assert "{{name}}" in code and "{{user_id}}" in code and "{{placeholders}}" in code
    assert "context.scenario.skip" in code


def test_generated_scenarios_run_and_select_by_tag_under_behave(tmp_path, monkeypatch):
    """Selection is measured with a before_scenario hook: behave's own summary counts filtered scenarios as skipped."""
    pytest.importorskip("behave")
    assert _run(monkeypatch, "gherkin", TODO, "-o", tmp_path, "--steps", "behave") == 0
    (tmp_path / "features" / "environment.py").write_text(
        'def before_scenario(context, scenario):\n    open("selected.log", "a").write(scenario.name + "\\n")\n')

    def selected(*tags):
        (tmp_path / "selected.log").unlink(missing_ok=True)
        r = subprocess.run([sys.executable, "-m", "behave", "-f", "progress", *tags], cwd=tmp_path, capture_output=True, text=True)
        log = tmp_path / "selected.log"
        return r, (len(log.read_text().splitlines()) if log.exists() else 0)

    result, n = selected()
    assert n == 23 and "0 failed" in result.stdout and "undefined" not in result.stdout.lower()
    assert "69 skipped" in result.stdout                                    # every step has a stub
    assert selected("--tags=PH-2", "--tags=priority-M")[1] == 4
    assert selected("--tags=AC-10")[1] == 1


def test_steps_flag_defaults_to_pytest_bdd_and_rejects_unknown_runners(tmp_path, monkeypatch):
    assert _run(monkeypatch, "gherkin", TODO, "-o", tmp_path / "a", "--steps") == 0
    assert (tmp_path / "a" / "test_acceptance.py").exists() and not (tmp_path / "a" / "features" / "steps").exists()
    assert _run(monkeypatch, "gherkin", TODO, "-o", tmp_path / "b", "--steps", "cucumber") == 2
