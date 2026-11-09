# Design: data-analyst-agent

**Author:** Mohammad Azam · **Status:** Draft · **Last updated:** Nov 9, 2026

## Context

Analysts spend much of their time answering routine questions from the warehouse:
counts, trends, breakdowns, comparisons against targets. Large language models can write
SQL, but on real warehouses they fail in ways that are hard to spot: joining a type 2
dimension on the natural key, averaging daily ratios, using the wrong definition of a
metric, or reading local time as UTC. Those answers look right and are wrong.

## Goals

1. Answer analytical questions on a warehouse correctly, with the SQL shown so a person can
   verify the answer.
2. Use the organization's own definitions: dbt model docs and a metric layer take priority
   over the model's guesses.
3. Never modify data, never read outside the schemas it is allowed, and stay within a cost
   budget.
4. Measure accuracy on a public benchmark built on realistic warehouses, per model and per
   release.
5. Work with hosted model APIs and with local open models, chosen by configuration.

## Non-goals

- Writing to the warehouse, building pipelines or changing dbt models.
- Replacing a BI tool's dashboards; the agent answers questions, dashboards monitor.
- Training or fine-tuning models.

## Design

### Components

| Component | Responsibility |
|---|---|
| Catalog | Tables, columns and types from the warehouse; model and column descriptions from dbt's `manifest.json`; metric definitions from a metric layer. Ranked retrieval picks what goes into the prompt. |
| Planner | Turns the question into a plan: the metric, grain, filters, time window and the tables to use. The plan is stored and shown. |
| SQL writer | Writes one query for the plan in the warehouse's dialect. |
| Guardrails | Parses the query (sqlglot) and refuses anything but a single read on allowed schemas; adds a row limit; enforces a timeout and, where the warehouse supports it, a byte or cost budget (BigQuery dry run). |
| Executor | Runs the query on a read-only connection and returns a typed result. |
| Checks | Sanity checks on the result: empty or suspiciously large results, nulls in key columns, ratios outside [0, 1], and reconciliation against a metric's known total when one exists. A failed check goes back to the writer with the reason. |
| Answerer | Writes the answer: the numbers, a chart when a series is involved, the SQL, and any caveats from the checks. |
| Tracer | Records every step (prompts, plans, queries, checks, timings, token counts and cost) as structured events. |

### Model providers

One small interface: given messages and tool definitions, return a message. Implementations
for hosted APIs and for any OpenAI-compatible endpoint (which covers local servers such as
llama.cpp and Ollama). A scripted provider replays fixed responses so the agent loop is
tested deterministically in CI without network access or keys.

### Loop budget

At most a fixed number of write and check rounds per question (default 3). When the
budget runs out, the agent says it could not produce a checked answer rather than
returning an unchecked one.

## Alternatives considered

- **A single prompt that writes SQL directly.** Simplest, but gives no place to apply
  definitions, check results or explain failures. Benchmarks will include it as the
  baseline the agent has to beat.
- **Fine-tuning a model on the warehouse.** Ties the system to one model and one schema;
  retrieval of definitions adapts to schema changes without retraining.
- **Generating queries only through the metric layer.** Safe and consistent, but cannot
  answer questions the metric layer does not cover. The agent prefers metric-layer
  definitions and falls back to SQL when needed.

## Security and privacy

See [SECURITY.md](../SECURITY.md). The agent treats table contents, column names and user
questions as data, never as instructions. Results can contain personal data; traces store
query text and summaries by default, and full results only when configured.

## Testing plan

- Unit tests for the guardrails, with a corpus of statements that must be refused
  (writes, DDL, multiple statements, comments hiding statements, disallowed schemas).
- The agent loop tested end to end with the scripted provider on a small DuckDB fixture.
- The benchmark run in CI on a small sample with a local model, and in full per release.

## Rollout

1. M1: DuckDB only, CLI, scripted and hosted providers.
2. M2: benchmark and first results.
3. M3 to M4: grounding, MCP server, BigQuery and Postgres.
4. v1.0: published package, results for several models, demo.

## Open questions

- Which local model gives acceptable accuracy on a laptop CPU for the demo.
- Whether to publish the benchmark questions in full or keep a held-out set to limit
  overfitting.
