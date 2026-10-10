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


def test_broken_example_trips_every_rule_family():
    _spec, report = run_lint([BROKEN])
    expected_errors = {"R01", "R02", "R06", "R08", "R10", "R11", "R13", "R14", "R15",
                       "R20", "R21", "R23", "R24", "R25", "R26"}
    assert expected_errors <= rules(report, "ERROR")
    assert {"R04", "R05", "R09", "R12", "R18", "R22"} <= rules(report, "WARN")


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
    assert len(names) == 13
    assert {"openapi.yaml", "traceability.md", "error-catalogue.md"} <= names


def test_generated_openapi_is_valid(tmp_path):
    validator = pytest.importorskip("openapi_spec_validator")
    spec, _ = run_lint([TODO])
    cli.build(spec, tmp_path)
    doc = yaml.safe_load((tmp_path / "openapi.yaml").read_text())
    validator.validate(doc)
    assert sum(len(v) for v in doc["paths"].values()) == 41


def test_cli_exit_codes(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["speclint", str(TODO)])
    with pytest.raises(SystemExit) as ok:
        cli.main()
    assert ok.value.code == 0
    monkeypatch.setattr("sys.argv", ["speclint", str(BROKEN)])
    with pytest.raises(SystemExit) as bad:
        cli.main()
    assert bad.value.code == 1
