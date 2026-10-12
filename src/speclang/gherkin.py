"""Gherkin export: one feature file per requirement area, one scenario per acceptance criterion.

Each scenario is tagged with its criterion, the requirements it covers, the priority, the delivery phase and the
tests that cover it, so a test runner can select scenarios by tag (for example `-m "PH-1 and priority-M"`).
Optionally writes a pytest-bdd step file whose steps are all pending, ready to be filled in.
"""
import re
from collections import defaultdict


def _natural(ident):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", ident)]


def _clean(text):
    return re.sub(r"\s+", " ", str(text)).strip().rstrip(".")


def _title(area):
    return re.sub(r"[-_]+", " ", area).strip().title()


def _slug(area):
    return re.sub(r"[^a-z0-9]+", "-", area.lower()).strip("-") or "general"


def _grouped(spec):
    """Acceptance criteria grouped by the area of the first requirement each one covers."""
    reqs = {r["id"]: r for r in spec["requirements"]}
    groups = defaultdict(list)
    for a in sorted(spec.get("acceptance", []), key=lambda a: _natural(a["id"])):
        frs = [c for c in a["covers"] if c in reqs]
        area = reqs[frs[0]].get("area", "general") if frs else "non-functional"
        groups[area].append((a, frs))
    return reqs, groups


def build_features(spec):
    """Return {filename: feature text}."""
    reqs, groups = _grouped(spec)
    ac_tests = defaultdict(list)
    for t in spec.get("tests", []):
        for ref in t["covers"]:
            if ref.startswith("AC-"):
                ac_tests[ref].append(t["id"])
    phase_of = {fr: ph["id"] for ph in spec.get("phases", []) for fr in ph["delivers"]}

    files = {}
    for area, items in sorted(groups.items()):
        covered = sorted({f for _a, frs in items for f in frs}, key=_natural)
        lines = [f"Feature: {_title(area)}",
                 f"  Acceptance criteria for {', '.join(covered) if covered else 'non-functional requirements'}.", ""]
        for a, frs in items:
            tags = [a["id"]] + frs
            if frs:
                tags.append(f"priority-{reqs[frs[0]]['priority']}")
                if frs[0] in phase_of:
                    tags.append(phase_of[frs[0]])
            tags += sorted(ac_tests[a["id"]], key=_natural)
            lines += [f"  {' '.join('@' + t for t in tags)}",
                      f"  Scenario: {a['id']} covers {', '.join(a['covers'])}",
                      f"    Given {_clean(a['given'])}",
                      f"    When {_clean(a['when'])}",
                      f"    Then {_clean(a['then'])}", ""]
        files[f"{_slug(area)}.feature"] = "\n".join(lines).rstrip() + "\n"
    return files


def _distinct_steps(spec):
    """[(keyword, text, first criterion that uses it)] in feature order, each distinct step once."""
    _reqs, groups = _grouped(spec)
    seen, order = {}, []
    for _area, items in sorted(groups.items()):
        for a, _frs in items:
            for keyword in ("given", "when", "then"):
                k = (keyword, _clean(a[keyword]))
                if k not in seen:
                    seen[k] = a["id"]
                    order.append((keyword, k[1], a["id"]))
    return order


def build_behave_steps(spec):
    """A behave steps module (features/steps/steps.py) with one pending step per distinct step text.

    behave reads `{name}` in a step as a parameter, so literal braces in the criteria are doubled.
    """
    out = ['"""Step stubs generated from acceptance criteria (behave).',
           "",
           "Every step is pending: it skips the scenario until you replace the `pending(...)` call with real code.",
           "Run:  pip install behave  &&  behave",
           '"""',
           "from behave import given, then, when",
           "",
           "",
           "def pending(context, ref):",
           '    context.scenario.skip(f"pending: {ref}")',
           ""]
    for n, (keyword, text, ref) in enumerate(_distinct_steps(spec), 1):
        escaped = text.replace("{", "{{").replace("}", "}}")
        out += ["", f"@{keyword}({escaped!r})", f"def step_{keyword}_{n}(context):", f"    pending(context, {ref!r})", ""]
    return "\n".join(out).rstrip() + "\n"


def build_steps(spec, features_dir="features"):
    """A pytest-bdd module with one pending step definition per distinct step text."""
    steps = {(k, text): ref for k, text, ref in _distinct_steps(spec)}
    order = [(k, text) for k, text, _ref in _distinct_steps(spec)]
    out = ['"""Step stubs generated from acceptance criteria (pytest-bdd).',
           "",
           "Every step is pending: it skips the scenario until you replace the `pending(...)` call with real code.",
           'Run:  pip install pytest-bdd  &&  pytest -rs',
           '"""',
           "import pytest",
           "from pytest_bdd import given, scenarios, then, when",
           "",
           f'scenarios("{features_dir}")',
           "",
           "",
           "def pending(ref):",
           '    pytest.skip(f"pending: {ref}")',
           ""]
    for n, (keyword, text) in enumerate(order, 1):
        out += ["", f"@{keyword}({text!r})", f"def step_{keyword}_{n}():", f"    pending({steps[(keyword, text)]!r})", ""]
    return "\n".join(out).rstrip() + "\n"


def build_conftest(features):
    """A conftest.py that registers every tag used in the feature files as a pytest marker (no unknown-mark warnings)."""
    tags = sorted({tag for text in features.values() for tag in re.findall(r"(?m)^\s*((?:@\S+\s*)+)$", text)
                   for tag in tag.split()}, key=_natural)
    names = [tag.lstrip("@") for tag in tags]
    lines = ['"""Registers the scenario tags from the feature files as pytest markers."""', "", "TAGS = ["]
    lines += [f"    {n!r}," for n in names]
    lines += ["]", "", "", "def pytest_configure(config):", "    for tag in TAGS:",
              '        config.addinivalue_line("markers", f"{tag}: scenario tag from the spec")', ""]
    return "\n".join(lines)
