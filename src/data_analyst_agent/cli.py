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
    return parser


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
        )
    finally:
        warehouse.close()


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
    except (CLIError, ProviderError, AgentError) as err:
        print(f"analyst: {err}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
