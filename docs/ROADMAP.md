# Roadmap

Target: v1.0 on **Dec 31, 2026**. Each milestone ends with a pull request, a tagged
release and a changelog entry.

- [x] **M0 (Nov 9): foundation.** Repository, CI, design doc, roadmap.
- [ ] **M1 (Nov 13 to 20): the core loop.** Warehouse interface with a DuckDB connector,
  schema introspection, SQL guardrails (parse, refuse anything but a single read, budgets,
  timeouts), a model-provider interface (hosted APIs and any OpenAI-compatible local
  endpoint) with a scripted provider for deterministic tests, the plan, write, check and
  answer loop, run traces, and `analyst ask`.
- [ ] **M2 (Nov 21 to Dec 2): the benchmark.** Question suites with gold queries over the
  support-bi and commerce warehouses, execution-accuracy scoring, cost and latency per
  question, `analyst bench`, and the first published results.
- [ ] **M3 (Dec 3 to 12): grounded answers.** Reading dbt docs and metric layers, result
  checks and query repair, charts, answers that cite their SQL, and an MCP server.
- [ ] **M4 (Dec 13 to 22): more warehouses and operations.** BigQuery and Postgres
  connectors, cost budgets per warehouse, structured logs and traces, an interactive web
  view of runs.
- [ ] **M5 (Dec 23 to 31): v1.0.** Package published, benchmark results for several
  models, demo video, final design doc and ADRs.
