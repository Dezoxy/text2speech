from __future__ import annotations

import argparse
from pathlib import Path


def add_dependency_args(parser: argparse.ArgumentParser, *, default_deps_file: Path) -> None:
    parser.add_argument(
        "--deps-file",
        type=Path,
        default=default_deps_file,
        help="Path to dependency TOML file (default: dependencies.toml)",
    )
    parser.add_argument(
        "--deps-check",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Precheck dependencies at startup",
    )
    parser.add_argument(
        "--deps-auto-install",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Offer to install missing dependencies after approval",
    )


def add_ui_args(parser: argparse.ArgumentParser, *, default_ui: str | None, default_dashboard: bool | None) -> None:
    parser.add_argument(
        "--ui",
        choices=("auto", "plain", "rich"),
        default=default_ui,
        help="CLI UI mode for dashboard/progress",
    )
    if default_dashboard is not None:
        parser.add_argument(
            "--dashboard",
            action=argparse.BooleanOptionalAction,
            default=default_dashboard,
            help="Enable full-screen rich dashboard with pause/resume controls",
        )


def add_pronunciation_args(
    parser: argparse.ArgumentParser,
    *,
    default_file: Path | None,
    default_case_sensitive: bool | None,
) -> None:
    parser.add_argument(
        "--pronunciation-file",
        type=Path,
        default=default_file,
        help="Path to pronunciation dictionary (.toml/.json or key=value text)",
    )
    parser.add_argument(
        "--pronunciation-case-sensitive",
        action=argparse.BooleanOptionalAction,
        default=default_case_sensitive,
        help="Treat pronunciation dictionary keys as case-sensitive",
    )


def add_retry_args(
    parser: argparse.ArgumentParser,
    *,
    attempts_default: int | None,
    base_delay_default: float | None,
    backoff_factor_default: float | None,
    max_delay_default: float | None,
    jitter_default: float | None,
    include_retry_failed_chunks: bool,
    include_retry_failed_jobs: bool,
) -> None:
    parser.add_argument("--retry-attempts", type=int, default=attempts_default, help="Retry attempts per API call")
    parser.add_argument(
        "--retry-base-delay",
        type=float,
        default=base_delay_default,
        help="Initial retry backoff delay in seconds",
    )
    parser.add_argument(
        "--retry-backoff-factor",
        type=float,
        default=backoff_factor_default,
        help="Exponential multiplier for retry delay",
    )
    parser.add_argument(
        "--retry-max-delay",
        type=float,
        default=max_delay_default,
        help="Cap for retry delay in seconds",
    )
    parser.add_argument(
        "--retry-jitter",
        type=float,
        default=jitter_default,
        help="Random jitter added to each retry delay in seconds",
    )
    if include_retry_failed_chunks:
        parser.add_argument(
            "--retry-failed-chunks",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Run an extra retry pass for failed chunks after first pass",
        )
    if include_retry_failed_jobs:
        parser.add_argument(
            "--retry-failed-jobs",
            action=argparse.BooleanOptionalAction,
            default=None,
            help="Retry failed jobs once at the queue level",
        )


def add_cost_control_args(
    parser: argparse.ArgumentParser,
    *,
    max_cost_default: float | None,
    confirm_default: bool | None,
    max_chars_default: int | None,
) -> None:
    parser.add_argument("--max-cost-usd", type=float, default=max_cost_default, help="Abort when estimated run cost exceeds this USD limit")
    parser.add_argument(
        "--confirm-before-spend",
        action=argparse.BooleanOptionalAction,
        default=confirm_default,
        help="Prompt before synthesis when estimated cost is non-zero",
    )
    parser.add_argument(
        "--max-chars-per-run",
        type=int,
        default=max_chars_default,
        help="Abort when pending run characters exceed this limit",
    )
