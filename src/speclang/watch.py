"""Watch mode: re-check (and optionally rebuild and sync) whenever a spec file changes. Polling, no dependencies."""
import json
import time
from datetime import datetime
from pathlib import Path

import yaml


def snapshot(paths, extra=()):
    from .cli import SpecError, collect_files, expand_files
    try:
        files = list(expand_files(paths))
    except SpecError:                      # unreadable right now (mid-edit): watch the plain files so a fix is noticed
        try:
            files = list(collect_files(paths))
        except SpecError:
            files = []
    files += [Path(p) for p in extra]
    return {str(f): f.stat().st_mtime_ns for f in files if f.exists()}


def run_once(paths, build_dir=None, sync_docs=(), out=print, limit=12):
    """Check the spec once. Returns (errors, warnings), or None if the YAML could not be read."""
    from . import cli
    stamp = datetime.now().strftime("%H:%M:%S")
    try:
        spec = cli.load_specs(paths)
    except (cli.SpecError, yaml.YAMLError, OSError) as exc:
        out(f"[{stamp}] cannot read the spec: {str(exc).splitlines()[0] if str(exc) else exc}")
        return None
    schema = json.loads(Path(cli.__file__).with_name("spec.schema.json").read_text(encoding="utf-8"))
    report = cli.Report()
    if cli.check_schema(spec, schema, report):
        cli.lint(spec, report)
    items = sorted(report.items, key=lambda i: (i[0], i[1], i[2]))
    out(f"[{stamp}] {len(report.errors)} error(s), {len(report.warnings)} warning(s)")
    for level, rule, where, msg in items[:limit]:
        out(f"  {level:5} {rule} {where}: {msg}")
    if len(items) > limit:
        out(f"  ... and {len(items) - limit} more")
    if not report.errors:
        if build_dir:
            cli.build(spec, build_dir)
        for doc in sync_docs:
            f = Path(doc)
            new, updated, unknown = cli.sync_text(f.read_text(encoding="utf-8"), spec)
            for name in unknown:
                out(f"  {f}: unknown block '{name}'")
            if updated:
                f.write_text(new, encoding="utf-8")
                out(f"  synced {f}: {', '.join(updated)}")
    return len(report.errors), len(report.warnings)


def watch(paths, build_dir=None, sync_docs=(), interval=1.0, max_runs=None, max_polls=None,
          sleep=time.sleep, out=print):
    """Run `run_once` now and again after every change. Stops after `max_runs` runs or `max_polls` polls."""
    runs = polls = 0
    last = None
    results = []
    while True:
        snap = snapshot(paths)
        if snap != last:
            last = snap
            results.append(run_once(paths, build_dir, sync_docs, out))
            runs += 1
            if max_runs and runs >= max_runs:
                return results
        polls += 1
        if max_polls and polls >= max_polls:
            return results
        sleep(interval)
