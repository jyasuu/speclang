# speclang

Write a system spec as **structured YAML**, check it, and generate the tables that are tedious to keep in sync by hand.

- **Validate:** a JSON Schema plus 28 cross-reference and quality rules (every Must requirement has an acceptance criterion, every error an operation returns is catalogued, every screen declares its required states, and so on).
- **Generate:** traceability matrix, acceptance criteria, error catalogue, interaction and permission matrices, notifications, analytics events, plans, threat model, risk register, delivery plan, state machine diagrams (Mermaid) and an OpenAPI 3.1 skeleton.
- **Scale:** a spec can be one file or a folder of files; they are merged.

The YAML holds the *facts*. Rationale, diagrams and wireframes stay in your prose spec.

## Install

```
pip install .            # from this folder
pip install speclang     # once published
pip install ".[dev]"     # adds pytest and the OpenAPI validator
```

Requires Python 3.9+.

## Quick start

```
speclint examples/todo/                    # validate a folder of YAML files
speclint examples/todo/ --build build/     # also write the generated documents
speclint examples/todo/ --strict           # warnings fail the run too (use in CI)
speclint examples/broken.spec.yaml         # see every rule fire
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

`examples/todo/` is a complete system spec (32 requirements, 41 operations, 15 entities and more) split across six files.

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

## Limits (v0.1)

- OpenAPI request and response bodies are placeholders; field-level schemas are not modeled yet.
- Components do not yet declare which fields they display, so field-level coverage is not checked.
- Statements in `rules` are linked by ID and `supports` only, not checked against code or tests.
- There is no license file. Add one before publishing.

See `CHANGELOG.md` for the history.
