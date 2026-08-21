# ESAM architecture and boundaries

## North star

ESAM enables people and coding agents to evolve the same running agentic
software safely. The visual graph is not a simplified copy of the backend, and
the YAML is not an export format. They are two projections of one versioned
system.

An update is complete only when it is:

1. representable in the workflow contract;
2. reviewable as a Git change;
3. visible in the designer;
4. executable by the runtime;
5. observable through state and traces; and
6. testable without a live external service.

## Layer model

### 1. Contract layer

Owns workflow schema, identifiers, step and connection models, validation,
serialization, and migrations. It must not import FastAPI, product code, or
concrete external integrations.

### 2. Runtime layer

Owns graph planning, execution state, step dispatch, retries, suspension,
resumption, budgets, and result propagation. A step implementation is selected
through a registry rather than a growing conditional in the executor.

### 3. Control layer

Owns delegation of authority, policy evaluation, credential leases, sandbox
routing, human approval, and audit records. Product workflows configure these
services; they do not bypass them.

### 4. Observability layer

Owns traces, event streaming, metrics, replay, and evaluation. The designer
consumes this layer to show what is running and why—not a separate UI-only
simulation of execution.

### 5. Interface layer

Owns API routers, the designer, CLI commands, and coding-agent integrations.
Interfaces call application services and should not contain domain engines or
SQL.

### 6. Extension layer

Owns reusable tools and connectors. Extensions declare their input/output
contract, authority requirements, credential requirements, deterministic test
double, and observability attributes.

### 7. Example-product layer

Debt Management and Intelligence Newsletter assemble core workflows,
extensions, prompts, fixtures, and product-level acceptance tests. A product can
incubate a capability, but generic capability must graduate into a core or
extension interface.

## Target source layout

```text
src/esam/
  contracts/       workflow models, schema, serialization, validation
  runtime/         planner, runner, state, step handlers
  control/         authority, policy, credentials, approvals, sandbox
  observability/   traces, events, metrics, replay, evaluation
  application/     use cases joining the layers above
  api/             app factory, middleware, small routers
  extensions/      tool and connector interfaces/registry
  designer/        designer backend and static application boundary
examples/
  debt-management/
  intelligence-newsletter/
```

Existing import paths should remain as compatibility facades while code is
moved. This permits incremental extraction without forcing one unsafe rewrite.

## Module size and cohesion rule

The target is **roughly 300 physical lines per source file**, not an absolute
gameable limit. A module should normally contain one cohesive concept. When a
module approaches 300 lines, split by responsibility before adding behavior.

Exceptions require an adjacent explanation and should generally be limited to:

- generated files and immutable migration snapshots;
- declarative fixtures where splitting reduces clarity; or
- a temporary compatibility facade with an active extraction issue.

Moving arbitrary line ranges into dynamically executed fragments does not meet
this rule. The purpose is to give a person or coding agent enough local context
to understand and safely change a module.

Tests follow the same cohesion goal: prefer several scenario-focused test
modules over one large test file.

## Extraction sequence

1. Establish the application factory and middleware modules.
2. Extract API routers by platform capability; product endpoints move to their
   product package.
3. Replace the executor's step-type conditional with registered handlers.
4. Separate workflow parsing, serialization, validation, discovery, and cache
   synchronization.
5. Split persistence by repository interface and schema area.
6. Move debt and newsletter engines behind extension/product boundaries.
7. Retain small facades for old imports until callers and tests migrate.

Every extraction must preserve behavior and include import, unit, and API
contract checks. File size is a design constraint, not permission to skip those
checks.
