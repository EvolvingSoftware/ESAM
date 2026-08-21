# Intelligence Newsletter — an ESAM example product

The Intelligence Newsletter is a second product built on ESAM. It deliberately
stresses different platform capabilities from Debt Management:

- scheduled and webhook-triggered execution;
- parallel source collection and rate-limited tools;
- parsing, extraction, scoring, and fan-in aggregation;
- citation provenance and hallucination checks;
- narrative synthesis and versioned prompt patterns;
- rendering, delivery, archives, and quality regression.

Active graphs are the `daily-intelligence-brief*.yaml` files in
[`../../workflows/`](../../workflows/). Newsletter templates in
`out-of-the-box/` provide additional scenarios.

Newsletter editorial logic belongs to this product. Generic fetch, parse,
evaluation, trace, scheduling, and tool-extension contracts belong in ESAM
core.
