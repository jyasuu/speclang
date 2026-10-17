# speclang

Write a system spec as **structured YAML**, check it, and generate the tables that are tedious to keep in sync by hand.

- **Validate:** a JSON Schema plus 39 cross-reference and quality rules (every Must requirement has an acceptance criterion, every error an operation returns is catalogued, every screen declares its required states, and so on).
- **Generate:** traceability matrix, acceptance criteria, error catalogue, endpoint summary, data model tables, interaction and permission matrices, notifications, analytics events, plans, threat model, risk register, delivery plan, state machine diagrams (Mermaid) and an **OpenAPI 3.1 document with real request and response bodies**.
- **Link screens to fields:** components declare the fields they show, and the linter checks that an operation they use actually returns them.
- **Generate tests:** `speclint gherkin specs/ -o tests/ --steps` turns every acceptance criterion into a Gherkin scenario with pending steps for `pytest-bdd` or `behave`.
- **Share a spec:** `include:` pulls in other spec folders: a local path, a git repository pinned to a tag or commit (`git+https://host/org/repo.git@v1.2.0#path=spec`) or an https URL, with a local cache and an offline mode.
- **Adopt an existing API:** `speclint import-openapi api.yaml -o spec/` creates a starting spec from an OpenAPI file.
- **Start fast:** `speclint init DIR` creates a starter project that already passes the linter, with editor settings, a CI job and a sample document.
- **Edit with completion:** `speclint schema --for specs/` writes a JSON Schema listing your own requirement IDs, error codes, entities and so on, so YAML editors complete and check references as you type.
- **Watch:** `speclint watch specs/` re-checks (and can rebuild and sync) every time a file changes.
- **Catch API breaks:** `speclint diff old/ new/ --breaking` lists changes that would break existing clients.
- **Keep documents in step:** `speclint sync doc.md --spec specs/` rewrites the generated tables inside a Markdown document, and `--check` fails CI when they are out of date.
- **See test gaps:** a per-phase coverage report and rules that flag acceptance criteria and Must requirements that no test covers.
- **See what changed:** `speclint diff old/ new/` reports added, removed and changed items and what depends on a changed requirement.
- **Scale:** a spec can be one file or a folder of files; they are merged.

The YAML holds the *facts*. Rationale, diagrams and wireframes stay in your prose spec.

## Install

```
pip install .            # from this folder
pip install speclang     # once published
pip install ".[dev]"     # adds pytest, the OpenAPI validator, pytest-bdd, behave and the format checkers
pip install ".[formats]"  # checks date-time and uri formats in examples (see below)
```

Requires Python 3.9+.

## Quick start

```
speclint examples/todo/                    # validate a folder of YAML files
speclint examples/todo/ --build build/     # also write the generated documents
speclint examples/todo/ --strict           # warnings fail the run too (use in CI)
speclint examples/broken.spec.yaml         # see every rule fire
speclint diff old-specs/ specs/ --md       # change report between two versions
speclint diff old-specs/ specs/ --breaking # also list changes that break API clients (exit 1 if any)
speclint init my-system --name "My System"  # starter project that passes the linter
speclint gherkin specs/ -o tests/ --steps  # feature files and pending pytest-bdd steps (or: --steps behave)
speclint import-openapi api.yaml -o spec/  # starting spec from an OpenAPI file
speclint watch specs/ --build build/       # re-check on every change
speclint schema --for specs/ -o spec.schema.json   # editor completion with your own IDs
speclint sync docs/spec.md --spec specs/   # write generated tables into a Markdown document
speclint sync docs/spec.md --spec specs/ --check   # CI: exit 1 if the document is out of date
python -m speclang examples/todo/          # same as the speclint command
```

Exit code is `0` when there are no errors and `1` otherwise. Output lines look like:

```
ERROR R01 requirements[FR-10]: Must requirement has no acceptance criterion
WARN  R18 requirements[FR-2]: Must requirement is not implemented by any operation or component
```

## What a spec looks like

```yaml
requirements:
  - { id: FR-12, priority: M, text: "A user shall mark a task complete or reopen it.", accepts: [AC-6] }

acceptance:
  - { id: AC-6, covers: [FR-12], given: "a weekly recurring task", when: "the user completes it", then: "the next occurrence is created 7 days later" }

operations:
  - id: completeTask
    method: POST
    path: /tasks/{id}/complete
    implements: [FR-12]
    permission: [Editor, Owner]
    errors: [FORBIDDEN_ROLE, ALREADY_DONE, VERSION_CONFLICT]
    touches: { Task: UC, Reminder: UC, ChangeLog: C }     # C create, R read, U update, D delete, S soft delete, A anonymize
    idempotent: true
    concurrency: if-match
    tx: ["lock task", "check version", "apply", "append change_log", "commit"]
```

`examples/todo/` is a complete system spec (32 requirements, 41 operations, 15 entities, 45 types and more) split across seven files, with a sample prose document in `examples/docs/`.

### Top-level sections

| Section | Holds |
|---|---|
| `spec`, `roles`, `goals`, `nongoals`, `assumptions`, `personas`, `flows` | Context and users |
| `requirements`, `acceptance`, `nfrs` | What the system must do, and how we check |
| `entities`, `decisions`, `rules` | Data model with state machines, ADRs, design statements (security, privacy, backend, sync, mobile, ...) |
| `operations`, `errors`, `components` | API operations with data effects, error catalogue, screens and components |
| `notifications`, `analytics`, `plans`, `threats` | Cross-cutting design |
| `slos`, `tests`, `failures`, `capacity`, `rollout`, `phases`, `risks` | Delivery and operations |
| `glossary`, `revisions` | Reference |

The machine-readable definition is `src/speclang/spec.schema.json`.

## Types, request and response bodies (new in 0.2)

A **type** is a request or response body. It starts from an entity, so each field is defined once, then adds or overrides fields:

```yaml
types:
  - name: TaskCreate
    from: Task                       # start from the Task entity
    only: [id, list_id, title, description, priority, due_at]
    required: [id, list_id, title]   # exactly these are required
    fields:
      - { name: tags, type: "string[]" }
  - name: TaskPage
    fields:
      - { name: items, type: "Task[]", required: true }
      - { name: next_cursor, type: string }

operations:
  - { id: createTask, method: POST, path: /tasks, request: TaskCreate, response: Task, ... }
  - { id: listTasks, method: GET, path: /tasks, response: TaskPage,
      query: [{ name: status, type: "open | done" }, { name: limit, type: integer }], ... }

components:
  - { name: TaskRow, kind: component, uses: [completeTask], shows: [Task.title, Task.due_at], ... }
```

Field types: `string`, `uuid`, `integer`, `number`, `bigint`, `boolean`, `timestamp`, `json`, `bytes`, `markdown`; bounded strings `string[1..500]`; enums `a | b | c`; arrays `Name[]`; and references to other types by name. The generated OpenAPI file uses these for request bodies, response bodies and query parameters.

**Field linking:** `shows: [Task.title]` must be returned, directly or through a nested type, by an operation the component `uses`. If someone removes a field from a response, every screen that shows it fails the check.

### Examples and cross-field rules on types (new in 0.3)

```yaml
types:
  - name: ReminderCreate
    from: Reminder
    only: [remind_at, channel]
    required: []
    fields: [{ name: offset_minutes, type: integer }]
    constraints:
      - exactly_one: [remind_at, offset_minutes]     # also: at_least_one, at_most_one, requires: { a: [b] }
    example: { offset_minutes: 30, channel: push }
```

`constraints` become JSON Schema (`oneOf`, `anyOf`, `dependentRequired`) in the OpenAPI file. An `example` is checked against its own type, including the constraints, and is published as an example payload. Rule R36 fails when an example is invalid; R37 fails when a constraint names a field the type does not have.

### Value rules between fields (new in 0.4)

`constraints` say which fields must be present. `checks` say how values relate:

```yaml
types:
  - name: TaskCreate
    from: Task
    only: [id, list_id, title, due_at]
    fields: [{ name: reminder_at, type: timestamp }]
    checks:
      - { left: reminder_at, op: "<=", right: due_at, message: "The reminder must not be after the due date" }
  - name: UploadUrlRequest
    fields: [{ name: size_bytes, type: bigint, required: true }]
    checks:
      - { left: size_bytes, op: ">", value: 0 }
      - { left: size_bytes, op: "<=", value: 10485760 }
```

Operators: `<  <=  >  >=  ==  !=`. A check compares two fields (`right`) or a field with a fixed value (`value`). A numeric limit becomes standard JSON Schema (`exclusiveMinimum`, `maximum`, ...) in the OpenAPI file; a comparison between two fields cannot be expressed that way, so it is published as `x-checks` with the rule and your message. Examples are tested against both. Rule R40 fails when a check names a field the type does not have, compares things that cannot be compared (a timestamp with an integer), or uses a value of the wrong kind.

### Patterns and formats on text fields (new in 0.7)

```yaml
fields:
  - { name: email, type: string, format: email, required: true }
  - { name: mfa_code, type: string, pattern: "^[0-9]{6,8}$" }
```

`format` (`email`, `uri`, `hostname`, `ipv4`, `ipv6`, `date`, `time`, `duration`, `regex`, ...) and `pattern` (a regular expression) go in the OpenAPI schema, and examples are checked against both. They work on `string` and `markdown` fields, on entities and on types, and a type that starts from an entity inherits them. Rule R41 fails for a pattern that is not a valid regular expression or a `format` or `pattern` on a number, and warns about a format name it does not know. The importer reads `format` and `pattern` from schemas (`uuid`, `date-time` and `byte` are already types).

## Editor support

```
speclint schema --for specs/ -o spec.schema.json
```

writes a schema in which fields such as `implements`, `errors`, `uses`, `delivers`, `shows` and `touches` list the IDs that exist in your spec. In VS Code with the YAML extension, point `yaml.schemas` at it (`speclint init` does this for you) and the editor offers those IDs and underlines unknown ones as you type. Run the command again after adding new IDs.

## Starter project

```
speclint init my-system --name "My System"
```

creates `spec/` (four small YAML files), `docs/spec.md` with sync markers, `.vscode/settings.json`, a CI workflow and `spec.schema.json`. It refuses to run if `spec/` already has files. The starter passes `speclint spec/ --strict`.

## Watch mode

```
speclint watch specs/ --build build/ --sync docs/spec.md
```

Checks the spec now and again whenever a file changes. When the spec is clean it regenerates the documents and syncs the Markdown files. If you save invalid YAML mid-edit it says so and carries on. Press Ctrl-C to stop. It polls file times, so it needs no extra packages.

## Breaking-change check

```
speclint diff old-specs/ specs/ --breaking
```

lists changes that can break existing API clients and exits 1 if there are any: an operation removed or moved, a role that lost permission, a changed success status, an operation that now requires `If-Match`, a response field removed or retyped or no longer guaranteed, a request field or query parameter that is newly required or retyped, and an error code whose HTTP status changed. Adding things is not flagged. A field removed from a shared type is reported on every operation that returns it.

## Test skeletons from acceptance criteria (new in 0.5)

```
speclint gherkin specs/ -o tests/ --steps
pip install pytest-bdd
cd tests && pytest -rs
```

writes `features/*.feature` (one file per requirement area, one scenario per acceptance criterion), `test_acceptance.py` (a `pytest-bdd` step for every distinct Given, When and Then, all pending) and `conftest.py` (registers the tags). The first run reports every scenario as skipped with `pending: AC-n`: nothing is silently green. Replace a `pending(...)` call with real code and that scenario starts running.

Each scenario carries tags for its criterion, the requirements it covers, the priority, the delivery phase and the tests that cover it:

```
@AC-4 @FR-10 @priority-M @PH-1 @T-4
Scenario: AC-4 covers FR-10
  Given a signed-in Editor
  When they add a task with a 1 to 500 character title
  Then it appears at the top of the list within 100 ms and the server confirms it within 500 ms at p95
```

so `pytest -m "PH-2 and priority-M"` runs only the Must scenarios of one phase. The command refuses to run on a spec with errors.

For `behave` use `--steps behave`: it writes `features/steps/steps.py` next to the feature files (behave's required layout). Run `behave`, or select with `behave --tags=PH-2 --tags=priority-M` (both tags). Literal braces in a criterion are escaped, because behave reads `{name}` in a step as a parameter.

## Importing an existing API (new in 0.5)

```
speclint import-openapi api.yaml -o spec/        # YAML or JSON, OpenAPI 3.x
speclint spec/                                   # lists what the API does not say
```

Imported: schemas become `types`; operations keep their id (or get one), method, path, summary, query parameters, request and response types and success status; `Idempotency-Key` and `If-Match` headers become `idempotent` and `concurrency`; error responses become the error catalogue (an error that appears on every operation becomes `global`); `security` decides Anonymous or Authenticated. `allOf` of objects is merged. Anything that cannot be expressed (`oneOf`, schemas that are not objects) is simplified and listed as a `note:` when you run the import.

Not in an OpenAPI file, so created as placeholders: one requirement per operation (priority S, marked as imported), and nothing for entities, acceptance criteria, tests or phases. The lint of a fresh import is therefore a to-do list. On a hand-written pet-store API the only error was real: its PATCH endpoint had no concurrency control (R11).

From schemas it also reads `constraints` (the `oneOf`, `anyOf` and `dependentRequired` patterns this tool writes), `checks` (numeric `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`, `const`, including the OpenAPI 3.0 `exclusiveMinimum: true` spelling, plus `x-checks`) and `example` or `examples`. The lint then tests those examples, so an example that contradicts its own schema is reported.

An OpenAPI file produced by this tool carries `x-implements`, `x-permission` and `x-touches`, and importing it restores requirements, roles and entity stubs. Importing the todo spec's OpenAPI file and rebuilding one from the result gives an identical document: 41 operations and 46 schemas, equal in every key, nothing excluded from the comparison. 
## Sharing a spec across folders or repositories (new in 0.6)

```yaml
# billing-app/spec/00-billing.yaml
include:
  - ../../platform            # a folder: every *.yaml inside
  - ../../shared/errors.yaml  # a single file
  - ../../libs/*/spec         # a glob

spec: { name: Billing App, version: "0.1" }
requirements: [...]
```

Paths are relative to the file that contains them. An included file is merged exactly as if it were in the folder: lists are concatenated and the including file's own `spec` block wins. Includes may nest, each file is read once, and a cycle is harmless. `speclint`, `build`, `sync`, `gherkin`, `diff` and `watch` all follow includes (watch re-checks when an included file changes). A missing include is an error with the file and the path named.

`examples/multi/` has a shared `platform/` folder (roles and error codes) and an application that includes it. For another repository, check it out next to this one and point at it (`../other-repo/spec`).

Rules to know: IDs must be unique across everything merged (R03 reports a clash), and there is no namespacing. A shared catalogue holds entries a given application may not use, so R09 warns about them; mark errors that apply everywhere `global: true`, as the example does.

### Remote includes (new in 0.7)

```yaml
include:
  - ../../platform                                              # local folder, file or glob
  - git+https://github.com/acme/platform-spec.git@v1.2.0#path=spec   # a folder inside a git repository
  - git+file:///work/platform-spec@3f9c2ab                      # anything git can clone, at a tag, branch or commit
  - https://specs.acme.example/errors.yaml                      # a single YAML file
```

- **Pin what you fetch.** Use a tag or a commit id. Fetched content is cached (default `~/.cache/speclang`, set `SPECLANG_CACHE` to change it) and reused on later runs, so a branch name stays at the commit it had when first fetched. A different tag gives different content, which is how a shared spec is versioned.
- **Offline.** `SPECLANG_OFFLINE=1` forbids network access: cached includes work, anything else fails with a clear error. Use it in CI after a first fetch, or on a machine without network.
- **What is fetched** is printed once to stderr (`speclint: fetching ...`). A git include needs the `git` command. Plain `http://` is accepted only for localhost; URL includes must end in `.yaml` or `.yml` and are limited to 2 MB.
- **Containment.** A file fetched from a remote source may only include files from inside that same repository (or other remote sources). If it tries to include one of your local files, by a relative path, an absolute path or `#path=../..`, the run stops with an error and nothing local is read.
- **Trust.** Remote content is read as data (YAML only, nothing is run), but it adds requirements, errors and rules to your spec and can include further remote sources. Review what you include and pin it.
- **Duplicate IDs name their files.** If a shared spec and yours both define `FORBIDDEN_ROLE`, rule R03 reports `duplicate id (defined in platform/00-platform.yaml, spec/01-extra.yaml)`. There is no namespacing: IDs must be unique across everything merged. Namespacing would need a new ID syntax in every ID pattern and rewriting every reference, so it is not offered yet.

## Robustness (new in 0.8)

Malformed input produces a message, not a traceback. This is tested by mutation: the test suite breaks a good spec and a good OpenAPI file in many small ways (deleting keys, corrupting values, swapping and cross-wiring references, circular `$ref`s) and checks that nothing crashes.

- `speclint diff` and `speclint schema --for` check the spec against the schema first and stop with `error: the new spec (...) is not a valid spec:` followed by the first problems and their paths.
- `speclint import-openapi` checks the shape of the file before reading it and names the file and the place: `error: api.yaml cannot be imported: paths//x/get/parameters/0: 'in' is a required property`. Nothing is written when an import fails. Circular and very deeply nested schemas are handled.
- `x-implements` on an operation must name requirements (`FR-n`).

## Exit codes and errors

`0` clean, `1` the spec has errors (or warnings with `--strict`), `2` the input could not be used: invalid YAML (the line is named), a path that does not exist, a file that is not a mapping, a missing include, a spec that does not match the schema (for `diff` and `schema`), an OpenAPI file that cannot be imported. These print one `error:` line, never a traceback.

## Test coverage

`coverage.md` shows, for every delivery phase, which requirements are covered. A requirement is covered when a test covers it directly or through one of its acceptance criteria. R38 warns about acceptance criteria no test covers, and R39 about Must requirements that have criteria but no test path.

## Keeping a prose document in step (sync)

Put markers in any Markdown file where a generated table belongs:

```
<!-- speclang:acceptance -->
<!-- /speclang:acceptance -->
```

`speclint sync doc.md --spec specs/` fills the space between the markers. Text outside the markers is never touched, running it twice changes nothing, and it refuses to write if the spec has errors. Block names: `acceptance`, `entities`, `interaction`, `endpoints`, `errors`, `permissions`, `threats`, `notifications`, `analytics`, `plans`, `delivery`, `risks`, `traceability`, `coverage`, `states`. `examples/docs/todo-spec.md` is a working example.

## Change report

```
speclint diff old-specs/ specs/            # text
speclint diff old-specs/ specs/ --md       # Markdown, for pull-request summaries
speclint diff old-specs/ specs/ --exit-code    # exit 1 when anything changed
```

Output lists added, removed and changed items per section, then an impact line for each changed or removed requirement showing the operations, components, acceptance criteria and phases that refer to it.

## Generated outputs

`--build DIR` writes 17 files: `traceability`, `acceptance`, `error-catalogue`, `endpoints`, `entities`, `interaction-matrix`, `permission-matrix`, `notifications`, `analytics-events`, `plans`, `threat-model`, `risk-register`, `delivery-plan`, `coverage`, `state-machines` (Mermaid), `openapi.yaml`, and `spec-tables.md`, which puts the main tables under the section numbers of a prose spec so they can be pasted in.

## Rules at a glance

| Rule | Level | Check |
|---|---|---|
| S00 | E | File matches the schema |
| R01, R02, R03 | E | Must requirements have acceptance criteria; references exist; no duplicate IDs |
| R04, R05 | W | Must requirements use "shall"; no vague words (fast, easy, secure, ...) |
| R06 | E | Every NFR target contains a number or standard version |
| R07, R08, R10 | E | Operations implement real requirements, use catalogued errors, touch real entities |
| R09 | W | Catalogued errors are used (or marked `global: true`) |
| R11, R12 | E / W | POST declares idempotency; PATCH declares `if-match`; PUT declares concurrency; multi-entity writes describe a transaction |
| R13, R14, R27 | E | Screens have the five required states and a route; components use real operations |
| R15 | E / W | State machines use declared states; every state is reachable |
| R18 | W | Every Must requirement is implemented by an operation or component |
| R19 | E | Operation permissions use declared roles |
| R20, R21, R29 | E | Tests cover real IDs; every NFR has a test or SLO; threats point to real tests |
| R22, R23 | E / W | Notifications reference real requirements and only security ones are mandatory; analytics never carry user content |
| R24, R25, R26, R28, R30 | E / W | Plans have `PLAN_LIMIT`; every requirement is delivered by a phase; flows, retention and rules reference real things |
| R31, R32 | E | Types derive from real entities and use resolvable field types; operations use defined request, response and query types |
| R33 | W | An operation that returns a body declares its `response` type |
| R34 | E | A component only shows fields that an operation it uses returns |
| R35 | W | Every type is used by an operation or another type |
| R36, R37 | E | Type examples are valid against their type and its constraints; constraints name real fields |
| R38, R39 | W | Every acceptance criterion has a test; every Must requirement has a test path |
| R40 | E | Value checks name real fields and compare things that can be compared |
| R41 | E / W | Patterns are valid regular expressions; patterns and formats are only on text fields (E); unknown format names (W) |

## Authoring tips

- **Avoid YAML 1.1 words as keys.** `on`, `off`, `yes` and `no` are read as booleans. That is why a transition uses `event`, not `on`.
- **Quote free text in inline maps.** A comma or bracket inside an unquoted value ends the map early. Use quotes or block style.
- **A valid link is not a correct link.** The linter checks that references exist, not that they make sense. A reviewer still has to judge that.
- **One fact, one place.** Link by ID. Never retype a fact in a second place.

## Use in CI

`.github/workflows/spec.yml` is a ready example: it installs the package, runs `speclint specs/ --strict --build build/` and uploads the generated tables as an artifact.

## Develop

```
pip install -e ".[dev]"
pytest
python -m build        # builds dist/*.whl and dist/*.tar.gz
```

## Limits (v0.7)

- Remote includes are fetched with `git` or plain HTTP(S) and are not signed or checksummed. Pinning a tag or commit is the only protection against a changed source, and a tag can be moved by whoever owns the repository: use a commit id when that matters.
- There is no authentication for URL includes; private git repositories work only where `git` already has credentials, and prompts are disabled.
- Format names are checked in examples as far as the installed packages allow: `email`, `date`, `ipv4`, `uuid` and similar always, and `date-time` and `uri` only after `pip install speclang[formats]` (otherwise they are accepted unchecked).
- Remote includes have no lock file or checksum yet. Pin a commit id; a tag can be moved by the repository owner.
- The mutation tests use fixed seeds and modest sizes to keep the suite quick, so they are a strong regression net, not proof that no malformed input can crash the tools.
- `include` reads local paths only: there is no URL or git fetching, and no namespacing of IDs between included specs.
- Imported constraints are recognised only in the JSON Schema patterns this tool writes; other `oneOf` and `anyOf` forms are simplified to `json` with a note. Numeric limits import as checks; `pattern`, `format: email` and string limits other than length are not imported.
- Generated step files exist for `pytest-bdd` and `behave`. The feature files are plain Gherkin, so other runners (Cucumber, Godog) can use them, but you write the steps.
- The importer reads OpenAPI 3.x JSON or YAML, not Swagger 2, and does not follow references to other files. It guesses nothing about requirements, so the placeholders need rewriting.
- Round-trip fidelity is tested for this tool's own output. For other APIs it depends on how much of the file the importer understands; read its `note:` lines.
- Watch mode polls; it does not use operating-system file events, so there can be up to one interval of delay.
- The enriched schema only knows IDs that exist when it is generated, so new IDs are not offered until you regenerate it.
- Breaking-change detection looks at the spec, not at a running API: it cannot see behavior changes that the spec does not describe.
- Checks compare two fields or a field and a fixed value; they cannot express rules over several fields (sums) or lists.
- `sync` only fills tables between markers; it does not write into non-Markdown formats (Word, Google Docs). Paste `spec-tables.md` there.
- Coverage counts links between tests and requirements. It does not run the tests or measure code coverage.
- Constraints cover presence of fields (exactly one, at least one, at most one, requires), not value rules between fields.
- Field linking checks that a field is *returned*; it does not check that the field is editable or validated the same way on input.
- Query parameters and bodies are not linked to requirements field by field.
- Statements in `rules` are linked by ID and `supports` only, not checked against code or tests.
- There is no license file. Add one before publishing.

See `CHANGELOG.md` for the history.
