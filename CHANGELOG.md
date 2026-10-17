# Changelog

## 0.8.0
- **Robustness pass, found by fuzzing.** Mutating a good spec and good OpenAPI files in thousands of ways showed: `diff` and `schema --for` crashed on 566 of 7,500 runs when the spec was invalid; the importer crashed on 363 of 1,500 mutated files in 74 different places. All fixed:
  - `diff` and `schema --for` validate the spec first and exit 2 with the first problems and their paths.
  - The importer checks the OpenAPI shape before reading it and reports the file and the place; circular `$ref`s (an alias to itself, mutual aliases, `allOf` of itself, recursive objects) and 60-level `allOf` chains are handled; `x-implements` must name `FR-n` requirements; requirement IDs sort safely.
  - After the fix: 4,500 mutated OpenAPI files either import or are rejected with a message, 0 crashes; 679 imported results were linted and rebuilt, 0 crashes; 2,250 diff/schema runs on mutated specs: 1,030 clean rejections, 0 crashes.
- **`tests/test_robustness.py`:** eight deterministic mutation and edge-case tests (fixed seeds) keep these fixed. 85 tests in total.
- **`pip install speclang[formats]`:** adds `rfc3339-validator` and `rfc3987` so `date-time` and `uri` formats are checked in examples.
- Not in this release (planned): a lock file with commit ids and checksums for remote includes; ID namespacing.

## 0.7.0
- **Remote includes:** `include:` accepts `git+URL[@ref][#path=sub]` (a folder or file in a git repository at a tag, branch or commit) and `https://...yaml` URLs. Fetched content is cached (`SPECLANG_CACHE`); `SPECLANG_OFFLINE=1` forbids network access. Fetches are announced on stderr. Plain http only for localhost; URL includes must be `.yaml`/`.yml` and at most 2 MB.
- **Containment:** a file fetched from a remote source may only include files from inside that source, and `#path=` may not leave the repository; either attempt stops with an error and reads nothing local.
- **Duplicate IDs name their files** (R03): `duplicate id (defined in platform/00-platform.yaml, spec/01-extra.yaml)`. Namespacing was considered and not built: it needs a new ID syntax in every ID pattern and rewriting every reference.
- **`pattern` and `format` on text fields** (entities and types): carried into the OpenAPI schema, checked against examples (including `email`, `date`, `ipv4`, `uuid`), read by the importer. New rule R41; 39 rules in total. The todo example now uses email formats and a one-time-code pattern.
- Fixed during development: an invalid regular expression on a field crashed example validation with an uncaught `re.error` (same class of bug as the malformed check in 0.4); the example check now skips cleanly while R41 reports the pattern. Found by a test written for the feature.
- Example data fix: the todo spec's entities now carry every field the prose data model lists (timestamps, `created_by`, `color`, `due_all_day`, `ip`, `user_agent`, `search_vector`), so the prose documents can be generated from it without losing information; the derived API types keep `search_vector` private.

## 0.6.0
- **`include`:** a spec file can pull in other files, folders or globs, relative to itself (a shared platform spec, another repository checked out beside this one). Nested includes, each file read once, cycles harmless. Followed by lint, build, sync, gherkin, diff and watch. New example: `examples/multi/`.
- **Importer reads constraints, checks and examples:** `oneOf` / `anyOf` / `dependentRequired` patterns become `constraints`; numeric limits (including OpenAPI 3.0 `exclusiveMinimum: true`) and `x-checks` become `checks`; `example` / `examples` become `example`. The lint then tests imported examples.
- **Exact round trip:** importing this tool's OpenAPI file and rebuilding gives an identical document (41 operations, 46 schemas), compared in full. The importer now strips the " API" suffix it would otherwise double.
- A numeric check with a message now also appears in `x-checks`, so the message is not lost (the keyword still enforces the limit).
- **`speclint gherkin --steps behave`:** writes `features/steps/steps.py` for behave (pending steps, braces escaped). `--steps` alone still means pytest-bdd; unknown runners are rejected.
- **Friendly errors, exit code 2:** invalid YAML (with the line), a path that does not exist, a file that is not a mapping and a missing include now print one `error:` line instead of a traceback.
- Fixed during development: invalid YAML used to crash with a traceback (present since the first version). While checking behave tag selection, my first way of counting selected scenarios could not tell filtered from unfiltered runs; selection is now measured with a `before_scenario` hook and tested (23 / 4 / 1 scenarios, same as pytest-bdd).

## 0.5.0
- **`speclint gherkin SPEC... -o DIR [--steps]`:** Gherkin feature files (one per requirement area, one scenario per acceptance criterion, tagged with criterion, requirements, priority, phase and tests) and, with `--steps`, a `pytest-bdd` step file with pending steps plus a `conftest.py` that registers the tags. Verified by running the generated files under `pytest-bdd`: every step matches, all scenarios skip, `--strict-markers` is clean.
- **`speclint import-openapi FILE -o DIR [--name NAME]`:** a starting spec from an OpenAPI 3.x file. Types, operations, query parameters, bodies, success statuses, idempotency and `If-Match`, error catalogue (with global errors), roles. `allOf` of objects is merged; simplifications are reported as notes. Requirements are placeholders. Refuses a non-empty folder or a file without `paths`.
- Documents produced by this tool round-trip: import then rebuild gives identical operations (41) and schemas (46); a negative-control test proves the comparison can fail.
- New `number` field type.
- Example: `examples/foreign-petstore.yaml`, a hand-written API with no extensions.
- Fixed during development: the first importer silently dropped a request body defined with `allOf`; it now merges `allOf` and reports anything it skips. Generated test files initially produced 61 unknown-marker warnings; the new `conftest.py` removes them. Two of my own tests contained assertions that could never fail (`or True`); they were rewritten and the test helper now handles commands that return normally.

## 0.4.0
- **Value checks on types:** `checks` compare two fields or a field with a fixed value (`<  <=  >  >=  ==  !=`). Numeric limits become JSON Schema keywords; field-to-field comparisons are published as `x-checks` (rule and message). Examples are tested against checks (R36); rule R40 validates the checks themselves. 38 rules in total.
- **Editor support:** `speclint schema [--for SPEC] [-o FILE]` prints the JSON Schema, enriched with the IDs of a spec so YAML editors complete and flag references.
- **`speclint init DIR [--name NAME]`:** a starter project that passes `--strict`, with editor settings, CI workflow and a sample document. Refuses to overwrite an existing `spec/` folder.
- **`speclint watch SPEC... [--build DIR] [--sync DOC...]`:** re-checks on every change, rebuilds and syncs when clean, survives invalid YAML.
- **`speclint diff --breaking`:** lists changes that can break API clients; exit code 1 if any.
- The CLI now exits quietly when its output pipe is closed (for example `speclint ... | head`) instead of printing a traceback.
- Fixed during development: a malformed check (a numeric field compared with a string) crashed the linter; it is now reported as R40 and example validation is skipped for that type (regression test added). `x-checks` keeps the machine-readable rule alongside the message.
- Example spec: value checks on `TaskCreate`, `ReminderCreate` and `UploadUrlRequest`.

## 0.3.0
- **Examples and constraints on types:** `example` payloads and cross-field `constraints` (`exactly_one`, `at_least_one`, `at_most_one`, `requires`). Constraints become JSON Schema in the OpenAPI output; examples are published and validated (rules R36, R37).
- **Test coverage:** `coverage.md` per delivery phase; rules R38 (acceptance criterion without a test) and R39 (Must requirement without a test path). 37 rules in total.
- **`speclint sync`:** writes generated tables into Markdown files between `<!-- speclang:NAME -->` markers, with `--check` for CI. Idempotent, never touches text outside markers, refuses to run on a spec with errors.
- **`--version`** flag; 17 generated files.
- Example: `examples/docs/todo-spec.md` (prose with markers); example types now carry examples and constraints; the test plan gained coverage for AC-5 and AC-19, which the new rules flagged.
- Fixed during development: `sync` initially validated only the schema, so it could write tables from a spec with lint errors. It now runs the full lint first (regression test added).

## 0.2.0
- **Types:** new `types` section for request and response bodies, derived from entities (`from`, `only`, `omit`, `required`) with a small field type language. Operations gain `request`, `response` and `query`.
- **Real OpenAPI bodies:** the generated OpenAPI 3.1 file now contains request bodies, response bodies, query parameters and component schemas instead of placeholders.
- **Field linking:** components declare `shows: [Type.field]`; rule R34 checks an operation they use returns each field.
- **New rules:** R31 (type definitions), R32 (operation types), R33 (response declared), R34 (field linking), R35 (unused types). 33 rules in total.
- **Change report:** `speclint diff old new [--md] [--exit-code]` with an impact summary for changed requirements.
- **New outputs:** `endpoints.md`, `entities.md` and `spec-tables.md` (main tables under prose-spec section numbers); 16 generated files in total.
- CI example now posts the change report to the pull-request summary.
- Internal: the field-type module is named `typesys` (a module named `types` shadows the standard library).
- Example spec: new `06-schemas.yaml` with 45 types; operations and components in `03-interfaces.yaml` bound to them.

## 0.1.0
- First packaged release.
- Schema covering requirements, acceptance criteria, NFRs, entities and state machines, operations,
  errors, components, decisions, SLOs, plus goals, personas, flows, tests, rules, threats, failures,
  notifications, analytics, plans, risks, phases, rollout, capacity, glossary and revisions.
- `speclint` command: schema validation, 28 cross-reference and quality rules, multi-file merge.
- 13 generators: traceability, acceptance, error catalogue, interaction and permission matrices,
  notifications, analytics, plans, threat model, risk register, delivery plan, state machines,
  and an OpenAPI 3.1 skeleton.
- Transition key is `event` (not `on`, which YAML reads as a boolean).
