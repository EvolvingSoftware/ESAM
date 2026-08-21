# ESAM codebase map

This map describes the repository as it exists today and assigns each area to
ESAM core, a reusable extension, or an example product. It is the baseline for
the extraction work in the [roadmap](roadmap.md).

## Current shape

| Area | Current location | Intended owner | Notes |
| --- | --- | --- | --- |
| Workflow contract | `schema/`, `workflow_loader.py`, `yaml_pipeline.py` | Core / contracts | Parsing, writing, validation, discovery, and database synchronization are currently combined. |
| Workflow persistence | `agent_workflow.py`, `database.py`, `migration.py`, `migrations/` | Core / persistence | One shared SQLite schema and large repository class. |
| Runtime | `workflow_executor.py`, `state.py`, `executor_context.py`, `job_queue.py` | Core / runtime | The executor contains orchestration plus dozens of product-oriented step implementations. |
| Authority and secrets | `policy_engine.py`, `credential_broker.py`, `credential_store.py`, `sandbox_router.py`, `escalation_engine.py` | Core / control | These should be platform services used by every consequential action. |
| Observability | `tracing.py`, `audit_log.py`, `audit_trail.py`, `replay.py`, `evaluator.py`, `observability/` | Core / observability | Overlapping audit abstractions need one explicit contract. |
| API | `api_server.py`, `routes/`, `scheduler/routes.py`, `portal_api.py` | Core interface plus product routers | The monolith and extracted routers overlap; router registration is currently additive rather than replacing old routes. |
| Designer | `index.html` and API workflow routes | Core interface | Must become a faithful view/editor of the same workflow and runtime event contracts used by agents. |
| Tools and connectors | `tool_executor.py`, `tools/registry.yaml`, `connectors/`, `src/connectors/`, `integrations/` | Extension layer | Manifests, implementations, credentials, and execution need one extension protocol. |
| Debt Management | debt, payment, accounting, legal, portal, and collections modules in `src/` | Example product | Product behavior currently sits beside core modules and contributes many routes to `api_server.py`. |
| Intelligence Newsletter | fetcher, parser, extractor, citation, crossref, narrative, renderer, quality, archive, registry, and delivery packages | Example product plus reusable extensions | Generic primitives should graduate to extensions; editorial pipelines remain product-owned. |

## Core dependency direction

The desired dependency graph is one-way:

```text
interfaces (API / designer / CLI / coding agents)
                         │
                         ▼
                 application services
                  │      │       │
                  ▼      ▼       ▼
             contracts runtime control ◄── observability
                  ▲      ▲       ▲
                  └──── extensions ────┘
                           ▲
                           │
                    example products
```

Products may depend on public core and extension interfaces. Core must never
import a product. Interfaces must call application services rather than embed
SQL or domain engines. Runtime step handlers may depend on declared extensions,
but the planner and runner must not know about newsletter or debt concepts.

## Oversized-module map

The initial inventory found 50,000+ lines of Python source. The following files
are the major context bottlenecks and their proposed decomposition.

| Current module | Approx. lines | Decomposition |
| --- | ---: | --- |
| `api_server.py` | 5,847 | App factory; configuration; startup; middleware; platform routers; debt-product routers; newsletter-product routers. Delete duplicate routes as extracted modules become authoritative. |
| `workflow_executor.py` | 4,498 | Runner; planner; dispatcher; LLM handler; tool handler; control-flow handlers; approval handler; memory handlers; registered extension handlers. |
| `source_registry.py` | 1,191 | Models; repository; validation; sync service; query service. |
| `agent_workflow.py` | 1,099 | Agent, step, connection, credential, run, and trace repositories. |
| `legal_pathway.py` | 1,047 | Debt-product rules, repository, transition service, and document/tool adapter. |
| `database.py` | 962 | Connection lifecycle; transaction helper; schema ownership by bounded repository; compatibility facade. |
| `workflow_loader.py` | 961 | YAML reader; YAML writer; validator; discovery; import/sync service; CLI. |
| `abn_verification.py` | 789 | Client; validation; caching/repository; orchestration service. |
| `legal_docs.py` | 759 | Document models/templates; generation; repository; product service. |
| `ptrs_check.py` | 730 | Client; scoring; repository; product service. |
| `credit_limit.py` | 716 | Policy/rules; repository; decision service; product adapter. |
| `portal_api.py` | 697 | Move HTTP routes out; split token/session service, portal queries, commands, and product DTOs. |
| `late_fees.py` | 672 | Rules; assessment service; repository; notice adapter. |
| `patterns/engine.py` | 656 | Pattern models; store; renderer orchestration; version service. |
| `letter_of_demand.py` | 651 | Templates; rules; generator; repository; delivery orchestration. |
| `migration.py` | 650 | Migration discovery, metadata repository, runner, CLI; keep immutable migration snapshots exempt. |
| `seed_demo_agents.py` | 644 | Scenario builders per example product plus a small CLI. |
| `evaluator.py` | 594 | Dataset, runner, scorers, result repository, report service. |
| `quality/regression.py` | 548 | Baselines, tests, comparison, persistence. |
| `stories_engine.py` | 521 | Story repository, normalization, tracking, query service. |
| `reply_handler.py` | 515 | Debt-product classifier, transition policy, repository, application service. |
| `tether_engine.py` | 511 | Product ingestion, processing, and repository; rename around its actual responsibility. |
| `integrations/sync.py` | 491 | Provider protocol, sync planner, sync runner, result repository. |
| `skip_tracing.py` | 489 | Provider adapter, policy, repository, debt-product service. |
| `credential_broker.py` | 477 | Lease model, policy, resolver, audit integration. |
| `routes/agents.py` | 470 | Agent, step/connection, credential, and execution routers. |
| `routes/tether.py` | 469 | Separate debt-product routers by capability. |

Other modules above the target are tracked by the line-budget check introduced
in the roadmap. Generated migrations may exceed the target; handwritten core,
extension, product, and test modules should converge on roughly 300 lines.

## Capability ownership

### ESAM core

- workflow schema, serialization, validation, and migrations;
- graph planning, execution, suspension, resumption, and cancellation;
- authority, policy, budgets, credentials, sandboxing, and approvals;
- run state, events, traces, audit, replay, evaluation, and scheduling;
- designer/API/CLI contracts and Git-aware workflow change operations;
- extension discovery and registration.

### Reusable extensions

- LLM providers and OpenAI-compatible endpoints;
- HTTP fetch, structured parsing, SMTP, payments, search, and scraping;
- accounting and content-source connectors where the contract is generic;
- deterministic fake implementations for every external extension.

### Debt Management product

- collections policy and customer lifecycle;
- late fees, payment plans, letters of demand, and legal escalation;
- debtor portal behavior, credit decisions, and debt-specific prompts;
- product workflows, fixtures, acceptance tests, and UI views.

### Intelligence Newsletter product

- editorial selection and newsletter workflow composition;
- story trajectory and edition-specific narrative decisions;
- newsletter section composition, delivery policy, and archive views;
- product workflows, fixtures, acceptance tests, and UI views.

Extraction should be based on this ownership, not on where a file happens to
live today.
