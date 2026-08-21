# ESAM — Evolving Software Agent Manager

**Build software with agents without losing sight or control of the system.**

ESAM is the core platform for creating, running, observing, and evolving agentic
software. It combines a visual workflow designer with a YAML- and Git-native
backend so that people, Hermes, Codex, and other coding agents can work on the
same system through the interface that suits them best.

> Debt Management and the Intelligence Newsletter are **example products built
> on ESAM**. They are not ESAM itself. They exist to exercise the platform with
> realistic workflows and drive development of reusable platform capabilities.

## The product model

```text
                         ESAM CORE
  ┌──────────────────────────────────────────────────────────────┐
  │ Workflow schema · DAG runtime · authority · credentials      │
  │ visual designer · Git/YAML · tracing · replay · scheduling   │
  │ evaluation · connectors · human approval · observability     │
  └──────────────────────────────────────────────────────────────┘
                    ▲                         ▲
                    │ built on                │ built on
       ┌────────────┴────────────┐  ┌─────────┴────────────────┐
       │ Debt Management        │  │ Intelligence Newsletter │
       │ collections and payment│  │ research and publishing │
       └─────────────────────────┘  └──────────────────────────┘
```

### ESAM core

The platform owns capabilities that should work for every product:

- **Workflow definition:** YAML schemas, graph loading, validation, versioning,
  and Git-friendly changes.
- **Execution:** deterministic DAG scheduling around non-deterministic LLM and
  tool steps, conditions, loops, memory, and subworkflows.
- **Control:** delegation-of-authority levels, credential brokering, sandboxed
  tools, budgets, and human approval gates.
- **Understanding:** live visual state, structured traces, prompts, token/cost
  data, audit history, replay, and evaluation.
- **Evolution:** the visual editor and coding agents edit the same declarative
  source. A backend change made by Hermes or Codex must become visible in the
  designer; a visual edit must produce a reviewable YAML/Git change.

### Example products

| Product | What it tests in ESAM |
| --- | --- |
| [Debt Management](examples/debt-management/README.md) | Authority gates, human escalation, credentials, email, payments, accounting, audit, and long-running customer interactions. |
| [Intelligence Newsletter](examples/intelligence-newsletter/README.md) | Web tools, fan-out/fan-in data pipelines, extraction, citations, scoring, rendering, scheduling, archives, and quality regression. |

Product-specific code may prove a capability first. Once the capability is
general, it should move behind an ESAM core interface rather than becoming a
special case in the runtime.

## How agentic development works

ESAM has one system with two equally capable editing surfaces:

```text
Visual designer ──┐
                  ├── YAML workflow in Git ── validation ── runtime
Hermes / Codex ───┘                                  │
                                                     └── trace/state back to UI
```

1. A person sketches or changes a graph in the designer, or a coding agent
   edits its YAML and supporting code.
2. Both paths produce the same versioned representation and pass the same
   schema and policy checks.
3. ESAM executes the graph and streams node status, inputs, outputs, approvals,
   cost, and errors into the visual view.
4. Traces and evaluations provide evidence for the next human- or agent-made
   update.

See [the architecture guide](docs/architecture.md) for component boundaries and
[the development guide](docs/development.md) for the modularity rules that keep
the repository usable by coding agents.

## Repository map

```text
src/                 ESAM runtime and platform services
src/routes/          API boundaries (being extracted from the legacy server)
src/connectors/      reusable connector implementations
schema/              workflow contracts
tools/               tool definitions and authority metadata
workflows/           active, editable workflow instances
out-of-the-box/      reusable workflow templates
examples/            product explanations and product-level scenarios
tests/               platform and capability tests
```

Some domain modules are still mixed into `src/`. The target layout and staged
extraction plan are documented in [docs/architecture.md](docs/architecture.md).

## Current example: Debt Management

The debt-management product demonstrates an end-to-end receivables workflow:
assessment, reminders, customer response, manager review, skip tracing,
payments, accounting updates, and a final auditable resolution. Its active
workflow is [`workflows/debt-management.yaml`](workflows/debt-management.yaml).

Each step declares not only what runs, but who may decide:

- **Readonly / automatic:** observation and bounded inference.
- **Standard:** approved routine actions within policy and budget.
- **Elevated:** a hard human approval gate before a consequential action.
- **Escalated:** ownership is routed to the responsible person or team.

## Current example: Intelligence Newsletter

The newsletter product exercises a very different graph: source collection,
page extraction, entity and topic processing, cross-reference detection,
citation verification, narrative synthesis, rendering, delivery, and archive
comparison. This breadth is intentional—it ensures ESAM core is not designed
only around the debt-management use case.

## Development status

ESAM is an actively consolidated prototype. The runtime and example products
are substantial, but installation metadata, API decomposition, and product/core
boundaries are still being normalized. Do not treat the current security
defaults as production deployment guidance.

The immediate engineering priorities are:

1. make a clean checkout reproducibly installable and testable;
2. extract the monolithic API and executor into cohesive modules of roughly
   300 lines or fewer;
3. move product-specific behavior into `examples`/product packages and expose
   reusable behavior through core extension interfaces;
4. make every backend workflow update round-trip visibly through the designer;
5. add an end-to-end test for both example products using deterministic fakes.

## Quick start (prototype environment)

Prerequisites are Python 3.11+, Git, and an OpenAI-compatible LLM endpoint.
The repository does not yet contain complete dependency metadata; correcting
that is the first item in the development plan. In an environment with the
project dependencies already installed:

```bash
cp .env.example .env
PYTHONPATH=src python src/seed_demo_agents.py
PYTHONPATH=src uvicorn api_server:app --reload
```

The API is served at `http://localhost:8000`; use `/designer` for the workflow
designer and `/docs` for the API surface.

## License

MIT — see [LICENSE](LICENSE).

Built for the Hermes Agent Accelerated Business Hackathon (NVIDIA × Stripe ×
Nous Research). ESAM is the platform; the included products are its proving
grounds.
