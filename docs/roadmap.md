# ESAM delivery roadmap

This is an execution plan, not a feature wish list. Its first objective is to
create safe seams in the existing prototype; its second is to make ESAM's
visual-and-code editing model real; its third is to use the two example products
as repeatable acceptance suites.

## Outcomes

The roadmap is complete when:

1. a clean checkout installs and runs with one documented command sequence;
2. handwritten modules are cohesive and normally no larger than roughly 300
   lines;
3. ESAM core has no imports from either example product;
4. a YAML or coding-agent change appears in the designer without manual cache
   repair, and a visual edit produces the expected YAML/Git diff;
5. a running graph visibly reports queued, running, waiting, completed, failed,
   and cancelled states with inputs, outputs, costs, and authority decisions;
6. step implementations are registered extensions rather than branches in a
   monolithic executor;
7. Debt Management and Intelligence Newsletter pass deterministic end-to-end
   acceptance suites; and
8. deployment defaults fail safely when secrets or policy are missing.

## Delivery principles

- **Preserve behavior before redesigning it.** Add characterization tests around
  an existing boundary, extract it, then improve it.
- **One authoritative path.** Never leave legacy and replacement routes or
  serializers active at the same time.
- **Compatibility is temporary and visible.** Small facades can preserve imports
  but must point one-way to the new implementation.
- **No mechanical line slicing.** The 300-line target is achieved by cohesive
  responsibilities and explicit interfaces.
- **Products prove the platform.** A product need may incubate a feature, but
  reusable behavior is promoted into core or an extension contract.
- **External services are optional in tests.** Every provider has a deterministic
  fake and contract test.

## Dependency order

```text
Phase 0 Foundations
       │
       ├──► Phase 1 API seam ───────────────┐
       ├──► Phase 2 workflow contracts ─────┤
       └──► Phase 3 persistence seam ───────┤
                                            ▼
                                  Phase 4 runtime kernel
                                            │
                       ┌────────────────────┼───────────────────┐
                       ▼                    ▼                   ▼
             Phase 5 control      Phase 6 observability   Phase 7 extensions
                       └────────────────────┼───────────────────┘
                                            ▼
                                  Phase 8 designer sync
                                            │
                              ┌─────────────┴─────────────┐
                              ▼                           ▼
                    Phase 9 debt product      Phase 10 newsletter product
                              └─────────────┬─────────────┘
                                            ▼
                                  Phase 11 hardening
```

Phases 1–3 can proceed independently after Phase 0. Phases 5–7 can proceed in
parallel after the runtime contracts stabilize. Product migration follows the
core seams, not the other way around.

## Phase 0 — reproducible foundations

**Goal:** make every later refactor measurable and runnable.

### Work

- Complete `pyproject.toml` with package metadata, supported Python versions,
  runtime dependencies, development dependencies, and build backend.
- Standardize the package entry points (`esam api`, `esam validate`, `esam
  seed`, and `esam worker`) while retaining compatibility commands temporarily.
- Replace configuration drift (`DATABASE_URL` versus `ESAM_DB_DIR`) with a typed
  settings object and explicit development/test/production modes.
- Configure pytest to discover both current suites, then consolidate them under
  one test root.
- Fix workflow validation to use the active Python environment rather than a
  developer-specific Hermes virtualenv.
- Add format, lint, type, dependency-direction, module-size, workflow-schema,
  import, and smoke-test jobs.
- Capture route paths and workflow round-trip output as characterization
  snapshots before moving code.

### Exit criteria

- A clean virtual environment can install the repository and run the complete
  test suite.
- CI runs the same commands documented for local development.
- The server can start with fake external providers and an isolated temporary
  database.
- An initial line-budget report is committed. The budget ratchets downward and
  permits only documented migration/generated-file exceptions.

## Phase 1 — application factory and API decomposition

**Goal:** replace `api_server.py` with a small composition root and authoritative
routers.

### Work

1. Extract typed settings, logging setup, lifespan/startup, auth middleware,
   audit middleware, CORS, and error mapping.
2. Introduce `create_app(settings, dependencies)` so tests do not rely on import
   side effects.
3. Inventory all paths and remove duplicate `/health`, workflow, audit, and
   execution registrations.
4. Convert `routes/` modules to `APIRouter` modules with explicit service
   dependencies; stop passing the whole application into `register()`.
5. Split core routers by resource: workflows, runs, approvals, traces,
   evaluation, schedules, tools/extensions, credentials, health, and auth.
6. Move debt and newsletter HTTP endpoints to product router packages.
7. Reduce `api_server.py` to a compatibility import and CLI entry point, then
   remove it after callers migrate.

### Exit criteria

- The composition root is under 300 lines and has no business logic.
- Each route exists exactly once and OpenAPI has stable operation identifiers.
- Router tests run with injected services and a temporary database.
- Core API modules do not import product engines.

## Phase 2 — canonical workflow contract

**Goal:** make YAML, API, designer, and runtime use one lossless workflow model.

### Work

- Define typed workflow, step, connection, authority, tool-instance, credential
  reference, and input/output schema models.
- Split YAML parsing, serialization, validation, discovery, and synchronization
  out of `workflow_loader.py`.
- Replace the handwritten subset parser with a standards-compliant YAML library
  while preserving comments/order where designer round trips require it.
- Version the contract and implement explicit migrations.
- Validate unique IDs, connection endpoints, acyclicity rules, template
  references, declared step handlers, tool references, authority configuration,
  and input/output schemas.
- Establish optimistic concurrency using workflow revision/content hash.
- Emit a structured workflow-changed event after file or visual edits.

### Exit criteria

- Every checked-in workflow round-trips without semantic data loss.
- Schema and runtime step registries cannot silently drift.
- Conflicting UI/agent edits return a revision conflict rather than overwrite.
- Validation errors identify the YAML location and graph element.

## Phase 3 — persistence boundaries

**Goal:** stop sharing database details across the application.

### Work

- Split connection lifecycle, transaction handling, migration execution, and
  repositories out of `database.py`, `agent_workflow.py`, and `migration.py`.
- Define repositories for workflows/cache, runs, step attempts, approvals,
  credentials, traces/events, schedules, and product state.
- Make transactions explicit at application-service boundaries.
- Assign every table and migration to an owning layer/product.
- Reconcile overlapping audit stores and define append-only integrity rules.
- Keep SQLite as the supported local implementation; avoid premature database
  abstraction beyond tested repository contracts.

### Exit criteria

- Runtime and API services depend on repository protocols, not SQL connections.
- Tests create isolated stores without writing to a developer home directory.
- Migrations are idempotent, versioned, and tested from empty and prior schemas.

## Phase 4 — runtime kernel and registered step handlers

**Goal:** turn `workflow_executor.py` into a small, product-neutral runtime.

### Work

- Separate graph planning/topological validation from execution.
- Define `StepHandler`, `StepContext`, `StepResult`, `RunEvent`, and suspension
  contracts.
- Replace the step-type conditional with a registry populated by core handlers
  and extensions.
- Extract core handlers: input, LLM, tool, condition, loop, subworkflow, score,
  memory read/write, and human approval.
- Move fetch/parse/citation/story/narrative/quality/archive handlers into
  reusable extensions or the newsletter product.
- Implement explicit run and step state machines with durable checkpoints.
- Specify retries, idempotency keys, timeouts, cancellation, compensation, and
  resume behavior.
- Remove legacy execution paths after characterization tests pass.

### Exit criteria

- The runner, planner, dispatcher, and each handler are cohesive modules near
  the line target.
- Adding a step type requires registration, not editing the runner.
- Runs resume after process restart from a waiting or retryable state.
- Unknown handlers fail validation before execution.

## Phase 5 — authority, credentials, and human control

**Goal:** make consequential agent behavior enforceable rather than descriptive.

### Work

- Define one authority vocabulary and migration path for existing levels.
- Evaluate authority before issuing a credential lease or external side effect.
- Separate policy decisions from credential resolution and secret storage.
- Make approvals durable, attributable, expiring, scoped to exact proposed
  inputs, and invalidated when those inputs change.
- Record decision reason, applicable policy, approver, credential lease, cost
  budget, and sandbox result in the trace/audit event stream.
- Introduce deny-by-default production settings and narrow API authorization.

### Exit criteria

- Tests prove gated tools cannot execute before valid approval.
- Secrets never appear in workflow YAML, API responses, logs, or traces.
- Replaying or modifying an approved action cannot reuse stale approval.

## Phase 6 — event model, observability, replay, and evaluation

**Goal:** give the designer and coding agents a complete, stable account of what
is happening.

### Work

- Define a versioned event envelope for workflow, run, step, approval, tool,
  token/cost, error, and cancellation events.
- Use the event stream as the source for live UI updates and trace inspection.
- Consolidate trace, audit, metrics, replay, and evaluation identifiers.
- Add redaction and retention policies.
- Make replay distinguish reused recorded outputs from newly executed side
  effects.
- Define evaluation datasets and regression gates for workflow revisions.

### Exit criteria

- A run timeline can be reconstructed from persisted events.
- Live updates reconnect without losing or duplicating state.
- Every failure includes a stable code, responsible graph element, and useful
  diagnostic context.

## Phase 7 — extension and connector platform

**Goal:** make tools portable, declared, testable, and policy-controlled.

### Work

- Unify `tools/registry.yaml`, connector manifests, connector implementations,
  integrations, and executor dispatch under one extension descriptor.
- Define configuration, credentials, input/output schema, authority, timeout,
  retry, idempotency, health-check, and fake-provider contracts.
- Separate generic providers from product-specific orchestration.
- Add extension discovery and compatibility/version checks.
- Create contract suites reused by HTTP, SMTP, Stripe, accounting, search,
  scraper, and content-source adapters.

### Exit criteria

- Every installed extension is visible in the designer with its schemas and
  authority requirements.
- Missing or incompatible extensions fail workflow validation.
- Every built-in external provider passes the same fake-backed contract suite.

## Phase 8 — visual/code synchronization

**Goal:** deliver ESAM's defining bidirectional development experience.

### Work

- Build the designer from canonical workflow models and schema-derived editors.
- Add filesystem/Git change detection and workflow-changed events.
- Show validation, uncommitted changes, revision, and conflict state in the UI.
- Stream execution events onto graph nodes and expose inputs, outputs, prompts,
  tools, approvals, errors, timing, tokens, and cost.
- Provide diff, undo, revision comparison, and explicit commit/branch operations
  rather than hidden Git side effects.
- Expose coding-agent-safe commands/APIs to inspect, validate, patch, execute,
  and evaluate a workflow.

### Exit criteria

- Editing YAML externally updates an open designer session.
- A visual edit creates a minimal, deterministic YAML diff.
- Concurrent edits produce a resolvable conflict.
- A developer can diagnose a running or failed node without opening server code.

## Phase 9 — Debt Management product migration

**Goal:** prove ESAM with consequential, long-running business workflows.

### Work

- Move debt routes, services, prompts, workflow templates, fixtures, and UI views
  into a product package.
- Split debt modules by rules, repositories, provider adapters, and application
  services according to the codebase map.
- Replace direct engine/runtime coupling with registered extensions and core
  workflow handlers.
- Build deterministic scenarios: reminder only; customer dispute; payment plan;
  manager approval; skip trace; payment; legal escalation; provider failure;
  cancellation and resume.

### Exit criteria

- Core has no debt imports or debt-specific step branches.
- The product installs/registers as a composition of ESAM and extensions.
- Its acceptance suite proves authority, audit, restart, and failure behavior.

## Phase 10 — Intelligence Newsletter product migration

**Goal:** prove ESAM with data-intensive, fan-out/fan-in agent workflows.

### Work

- Move editorial workflows, prompts, fixtures, product routes, and UI views into
  a product package.
- Promote generic fetch, parse, extract, cite, render, deliver, archive, and
  evaluation behavior into extensions with explicit contracts.
- Keep edition selection, narrative policy, and newsletter composition in the
  product.
- Build deterministic scenarios: partial source failure; rate limiting;
  duplicate stories; invalid citations; quality regression; delivery failure;
  scheduled resume and archive comparison.

### Exit criteria

- Core has no newsletter/editorial imports or step branches.
- The product demonstrates parallelism, aggregation, provenance, and regression
  gates entirely through public ESAM interfaces.

## Phase 11 — hardening and release

**Goal:** turn the consolidated prototype into a supportable ESAM release.

### Work

- Threat-model API, designer, Git operations, sandbox, credentials, webhooks,
  and extension supply chain.
- Replace permissive CORS, public route prefixes, swallowed startup failures,
  and development secrets with explicit environment policies.
- Add backup/restore, retention, upgrade, recovery, load, soak, and chaos tests.
- Publish versioned API, workflow-contract, extension-authoring, operations, and
  product-authoring documentation.
- Produce signed releases and a migration guide from legacy import paths.

### Exit criteria

- Security, recovery, upgrade, and performance targets are documented and met.
- A new example product can be built without changing core runtime or API code.

## Proposed pull-request sequence

Keep PRs small enough to review and bisect. The first sequence should be:

1. **Packaging and test discovery** — complete `pyproject.toml`; run both suites.
2. **Portable workflow validation** — remove the Hermes-venv path; add schema CI.
3. **Settings and isolated database tests** — one configuration contract.
4. **Route/OpenAPI characterization** — snapshot current public surface.
5. **Application factory** — injected settings/dependencies; no route movement.
6. **Middleware and lifecycle extraction** — preserve behavior.
7. **Health/auth/core router extraction** — remove originals once registered.
8. **Workflow/run router extraction** — service-backed, remove duplicates.
9. **Product router extraction** — debt and newsletter namespaces/packages.
10. **Workflow model and round-trip tests** — canonical typed contract.
11. **Loader decomposition** — reader, writer, validator, discovery, sync.
12. **Repository protocols and isolated stores** — then split persistence.
13. **Runtime characterization suite** — state transitions and handler outputs.
14. **Step registry and core handlers** — shrink the executor incrementally.
15. **Newsletter handler extraction** — extensions/product registrations.
16. **Authority and approval enforcement** — credential lease integration.
17. **Versioned run events** — live/reconnectable event API.
18. **Designer round-trip synchronization** — external edit and visual diff.
19. **Debt acceptance suite and migration**.
20. **Newsletter acceptance suite and migration**.

Each PR updates the line-budget report, removes at least as much legacy surface
as it introduces, and leaves tests green. A replacement is not complete while
both old and new implementations remain active.

## Progress scorecard

Update this table in each architectural PR.

| Phase | Status | Evidence |
| --- | --- | --- |
| 0. Foundations | Not started | Packaging and full-suite CI absent. |
| 1. API decomposition | Not started | Preliminary `routes/` exist, but the 5,847-line server remains authoritative and registrations overlap. |
| 2. Workflow contract | Prototype | JSON Schema and YAML pipeline exist; lossless canonical round trip is unproven. |
| 3. Persistence | Prototype | SQLite works locally; boundaries and isolated configuration are absent. |
| 4. Runtime kernel | Prototype | Broad executor exists; dispatch and product handlers are monolithic. |
| 5. Control | Prototype | Authority, credential, sandbox, and escalation modules exist; end-to-end enforcement needs proof. |
| 6. Observability | Prototype | Trace/audit/metrics/eval implementations exist but lack one event contract. |
| 7. Extensions | Prototype | Tool and connector registries exist but are not one contract. |
| 8. Designer sync | Prototype | Designer and YAML APIs exist; robust external-edit/conflict/live-state behavior is unproven. |
| 9. Debt product | Mixed into core | Workflows and substantial domain implementation exist. |
| 10. Newsletter product | Mixed into core | Workflows and substantial domain implementation exist. |
| 11. Hardening | Not started | Current defaults remain development-oriented. |
