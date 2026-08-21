# Developing ESAM with people and coding agents

## Change contract

Before implementing a capability, identify whether it belongs to ESAM core, a
reusable extension, or an example product. Ask: “Would an unrelated third
product need this?” If yes, define a core/extension interface. If no, keep it in
the product and use only public platform interfaces.

For workflow-facing changes, update all relevant projections together:

- schema/model;
- YAML reader and writer;
- runtime handler;
- API/designer representation;
- trace/event representation;
- deterministic tests; and
- an example workflow where the behavior is visible.

## Agent-friendly modules

- Keep source modules near 300 lines or fewer.
- Give a module one reason to change.
- Prefer explicit typed inputs and return values over shared mutable globals.
- Put side effects behind interfaces with local fakes.
- Avoid route handlers that directly perform persistence or domain logic.
- Avoid adding another branch to a central executor; register a handler.
- Preserve compatibility through a small import facade during refactors.
- Document invariants next to the contract that enforces them.

## Definition of done

A platform change is done when a developer can inspect the YAML/Git diff, see
the corresponding graph and live state, run it with deterministic dependencies,
inspect a useful trace, and understand failures without reading the entire
application.

An example-product change is done when it demonstrates a platform capability
without adding product-specific branches to core execution.
