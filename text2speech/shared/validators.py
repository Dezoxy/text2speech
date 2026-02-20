from __future__ import annotations


def validate_cost_limit(max_cost_usd: float | None) -> None:
    if max_cost_usd is not None and max_cost_usd < 0:
        raise ValueError("--max-cost-usd must be >= 0")


def validate_retry_settings(
    *,
    attempts: int,
    base_delay: float,
    backoff_factor: float,
    max_delay: float,
    jitter: float,
) -> None:
    if attempts < 1:
        raise ValueError("--retry-attempts must be >= 1")
    if base_delay < 0:
        raise ValueError("--retry-base-delay must be >= 0")
    if backoff_factor < 1:
        raise ValueError("--retry-backoff-factor must be >= 1")
    if max_delay < 0:
        raise ValueError("--retry-max-delay must be >= 0")
    if jitter < 0:
        raise ValueError("--retry-jitter must be >= 0")


def validate_pricing_table(pricing_table: dict[str, object]) -> None:
    if not pricing_table:
        raise ValueError("pricing table must not be empty")
    for key, value in pricing_table.items():
        usd = getattr(value, "usd_per_char", None)
        if usd is None:
            continue
        if usd < 0:
            raise ValueError(f"pricing for {key} must be >= 0")
