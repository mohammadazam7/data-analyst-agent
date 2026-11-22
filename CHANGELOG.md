# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/).

## [Unreleased]

### Added
- Execution-accuracy scoring: results compared as values, ignoring column names,
  column order and (unless the question asks for one) row order, with a numeric
  tolerance of 1e-6.
- Benchmark suites in TOML, checked on load (every gold query must pass the agent's
  own guardrails), and a first suite of 12 questions over the commerce marts.
- `analyst bench`: asks every question of a suite, scores each answer against its
  gold query and writes a Markdown report with accuracy per tag. `--gold` answers
  with the gold queries to check a suite and the harness.
- A support-bi suite of 12 questions over cloud-support-bi's marts, including type 2
  customer history and the daily backlog.
- `--dbt-manifest` on `ask` and `bench`: model and column descriptions from a dbt
  project's `manifest.json` are put in the prompt next to the tables they describe.

## [0.1.0] - 2026-11-17

### Added
- SQL guardrails: a model-written query must parse as one read-only statement, read only
  from allowed schemas and carry a row limit; file-reading functions are refused.
- A read-only DuckDB warehouse with schema introspection and a query time budget.
- A model provider interface with a scripted provider for tests and a client for any
  OpenAI-compatible chat endpoint, configured from the environment.
- The agent loop: write a query, check it, run it, answer from the result, with a
  bounded repair loop that feeds refusals and errors back, and a JSONL trace per run.
- `analyst ask`, which answers one question against a DuckDB file.
- Project foundation: package layout, CI, contribution and security guides, design doc
  and roadmap.
