"""The ``analyst`` command line tool.

``analyst ask`` wires the pieces together: a warehouse opened read-only, a policy
restricting which schemas may be read, a provider built from the environment, and the
agent loop. Argument parsing and environment wiring live here so the loop itself
(:mod:`data_analyst_agent.agent`) stays testable without a real model or a real CLI.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from data_analyst_agent.agent import AgentError, AgentResult, ask, new_trace_path
from data_analyst_agent.bench import GoldProvider, SuiteError, format_results, load_suite, run_suite
from data_analyst_agent.grounding import DbtDocs, GroundingError
from data_analyst_agent.guardrails import Policy
from data_analyst_agent.providers import (
    OpenAICompatibleProvider,
    Provider,
    ProviderError,
    config_from_env,
)
from data_analyst_agent.warehouse import DuckDBWarehouse

DEFAULT_TRACE_DIR = Path(".analyst/traces")


class CLIError(ValueError):
    """Bad input from the command line; the message is shown to the user, not a trace."""


def parse_duckdb_uri(uri: str) -> Path:
    """``duckdb:///relative/path.db`` or ``duckdb:////abs/path.db`` to a filesystem path.

    Only DuckDB is wired up yet; the README lists it as the first of several warehouse
    kinds, so the scheme is checked now even though nothing else is accepted.
    """
    prefix = "duckdb://"
    if not uri.startswith(prefix):
        raise CLIError(f"--warehouse must start with 'duckdb://', got: {uri}")
    # One slash after the prefix starts a relative path, two start an absolute one;
    # either way exactly one of those slashes belongs to the scheme, not the path.
    rest = uri[len(prefix) :]
    path = rest[1:] if rest.startswith("/") else rest
    if not path:
        raise CLIError("--warehouse is missing a path after 'duckdb://'")
    return Path(path)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="analyst")
    sub = parser.add_subparsers(dest="command", required=True)

    ask_parser = sub.add_parser("ask", help="answer a question about a warehouse")
    ask_parser.add_argument("question")
    ask_parser.add_argument("--warehouse", required=True, help="e.g. duckdb:///support.duckdb")
    ask_parser.add_argument(
        "--schema",
        dest="schemas",
        action="append",
        required=True,
        help="an allowed schema; may be given more than once",
    )
    ask_parser.add_argument("--max-rows", type=int, default=1000)
    ask_parser.add_argument("--query-timeout", type=float, default=30.0)
    ask_parser.add_argument("--max-repairs", type=int, default=2)
    ask_parser.add_argument("--trace-dir", type=Path, default=DEFAULT_TRACE_DIR)
    ask_parser.add_argument("--dbt-manifest", type=Path, help=MANIFEST_HELP)

    bench_parser = sub.add_parser("bench", help="score the agent on a question suite")
    bench_parser.add_argument(
        "suite", type=Path, help="a suite file, e.g. benchmarks/commerce/questions.toml"
    )
    bench_parser.add_argument("--warehouse", required=True, help="e.g. duckdb:///commerce.duckdb")
    bench_parser.add_argument(
        "--gold",
        action="store_true",
        help="answer with the gold queries instead of a model, to check the suite and harness",
    )
    bench_parser.add_argument("--max-repairs", type=int, default=2)
    bench_parser.add_argument("--query-timeout", type=float, default=30.0)
    bench_parser.add_argument("--trace-dir", type=Path, default=DEFAULT_TRACE_DIR)
    bench_parser.add_argument("--dbt-manifest", type=Path, help=MANIFEST_HELP)
    bench_parser.add_argument("--out", type=Path, help="write the Markdown report here")
    bench_parser.add_argument(
        "--hardware", default="Hardware not stated", help="described in the report, e.g. '2 vCPUs'"
    )
    return parser


MANIFEST_HELP = "a dbt target/manifest.json whose model and column descriptions go in the prompt"


def _docs(args: argparse.Namespace) -> DbtDocs | None:
    path: Path | None = getattr(args, "dbt_manifest", None)
    return DbtDocs.from_manifest(path) if path is not None else None


def run_ask(args: argparse.Namespace, provider: Provider) -> AgentResult:
    """The ``ask`` subcommand with its provider already built, for testing without a model."""
    db_path = parse_duckdb_uri(args.warehouse)
    if not db_path.exists():
        raise CLIError(f"no warehouse file at {db_path}")
    policy = Policy.for_schemas(args.schemas, max_rows=args.max_rows)
    warehouse = DuckDBWarehouse(db_path, args.schemas, timeout_s=args.query_timeout)
    try:
        trace_path = new_trace_path(args.trace_dir)
        return ask(
            args.question,
            provider,
            warehouse,
            policy,
            trace_path,
            max_repairs=args.max_repairs,
            docs=_docs(args),
        )
    finally:
        warehouse.close()


def run_bench(args: argparse.Namespace, provider: Provider | None, model: str) -> str:
    """The ``bench`` subcommand: score a suite and return the Markdown report."""
    suite = load_suite(args.suite)
    db_path = parse_duckdb_uri(args.warehouse)
    if not db_path.exists():
        raise CLIError(f"no warehouse file at {db_path}")
    warehouse = DuckDBWarehouse(db_path, suite.schemas, timeout_s=args.query_timeout)
    try:
        args.trace_dir.mkdir(parents=True, exist_ok=True)
        result = run_suite(
            suite,
            provider if provider is not None else GoldProvider(suite),
            warehouse,
            args.trace_dir,
            max_repairs=args.max_repairs,
            docs=_docs(args),
        )
    finally:
        warehouse.close()
    return format_results(result, model=model, hardware=args.hardware)


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "ask":
            config = config_from_env(os.environ)
            provider = OpenAICompatibleProvider(config)
            result = run_ask(args, provider)
            print(result.answer)
            print()
            print(result.sql)
            return 0
        if args.command == "bench":
            if args.gold:
                report = run_bench(args, None, "gold queries")
            else:
                config = config_from_env(os.environ)
                report = run_bench(args, OpenAICompatibleProvider(config), config.model)
            print(report, end="")
            if args.out is not None:
                args.out.write_text(report, encoding="utf-8")
            return 0
    except (CLIError, ProviderError, AgentError, SuiteError, GroundingError) as err:
        print(f"analyst: {err}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
