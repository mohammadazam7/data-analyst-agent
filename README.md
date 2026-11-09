# data-analyst-agent

An open-source AI agent that answers business questions on your data warehouse. Ask "Which
products drove the drop in on-time deliveries last quarter?" and it finds the right tables,
writes SQL, runs it read-only, checks the result, and explains the answer with the query
it used, so you can verify every number.

> **Status:** design stage. The design doc and roadmap are written; the agent loop, guardrails
> and the first warehouse connector are being built now. Nothing below is measured yet, and
> no accuracy figure will appear here until the benchmark has produced it.

## Why another text-to-SQL tool

Text-to-SQL demos look good on clean, single-table examples. Real warehouses are where
they fail: dozens of models with similar names, metrics with precise business definitions,
type 2 dimensions, timestamps in mixed time zones. A wrong answer that looks right is worse
than no answer. This project is built around that:

- **It knows your definitions.** It reads dbt docs and a metric layer (for example a
  `metrics.yml`) before writing SQL, so "SLA attainment" means what your team means.
- **It checks itself.** Every result goes through sanity checks (row counts, nulls,
  reconciliation against known totals) and the agent revises the query when a check fails.
- **It cannot change your data.** Read-only credentials, every statement parsed and
  refused unless it is a single `SELECT`, row and cost budgets, timeouts.
- **It is measured.** A public benchmark of realistic questions over real dbt warehouses,
  with gold queries, scored by execution accuracy, published per model and per release.
- **It runs with the model you choose.** Hosted APIs or a local open model through an
  OpenAI-compatible endpoint, with no code changes.

## How it works

```mermaid
flowchart LR
    Q[Question] --> P[Plan<br/>which tables, metrics,<br/>filters, grain]
    C[(Catalog<br/>schema, dbt docs,<br/>metric layer)] --> P
    P --> W[Write SQL]
    W --> G{Guardrails<br/>read-only, budget,<br/>allowed schemas}
    G -- refused --> W
    G -- ok --> X[(Warehouse<br/>DuckDB, BigQuery,<br/>Postgres)]
    X --> V{Checks<br/>shape, nulls,<br/>reconciliation}
    V -- fails --> W
    V -- passes --> A[Answer<br/>numbers, chart,<br/>SQL, caveats]
```

Every step is traced, so a run can be replayed and audited: the plan, each query, each
check and why it passed or failed.

## Planned interfaces

```bash
pip install data-analyst-agent            # not published yet
analyst ask "What was SLA attainment by tier last month?" --warehouse duckdb:///support.duckdb
analyst bench --suite support-bi --model <provider:model>
analyst mcp                                # serve the agent as an MCP server for other agents
```

## Benchmark

The benchmark uses warehouses built by two companion projects, so the questions sit on
realistic, tested dbt models rather than toy tables:

| Suite | Warehouse | What makes it hard |
|---|---|---|
| support-bi | [cloud-support-bi](https://github.com/mohammadazam7/cloud-support-bi) | type 2 customer tiers, SLA targets by priority and tier, additive metric components |
| commerce | [ecommerce-data-ai-platform](https://github.com/mohammadazam7/ecommerce-data-ai-platform) | nine source tables, per-order customer ids, out-of-order milestones, Portuguese category names |

Each question has a gold query; an answer counts as correct when its result matches the
gold result. Results will be reported by model, with cost and latency per question.

## Documentation

- [Design doc](docs/design.md): goals, non-goals, design, alternatives, safety, testing plan
- [Roadmap](docs/ROADMAP.md): milestones and dates

## Development

```bash
python -m venv .venv && source .venv/bin/activate
make install
make check
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## License

MIT
