# Debt Management — an ESAM example product

Debt Management is a product built on ESAM, not a subsystem that defines ESAM.
It provides demanding real-world scenarios for developing the platform:

- long-running workflows that suspend for customer or manager input;
- readonly, standard, elevated, and escalated authority decisions;
- email, payments, accounting, identity checks, and skip-tracing tools;
- protected credentials and sandboxed side effects;
- complete audit and case-resolution traces; and
- policy, cost, and regulatory constraints.

The active demonstration graph is
[`../../workflows/debt-management.yaml`](../../workflows/debt-management.yaml).
Reusable starting points also live in `out-of-the-box/`.

Product-specific prompts, portal behavior, and collections rules belong here.
Generic approval, credential, audit, scheduling, and execution behavior belongs
in ESAM core.
