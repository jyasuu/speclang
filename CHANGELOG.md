# Changelog

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
