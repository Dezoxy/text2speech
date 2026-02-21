from __future__ import annotations

from pathlib import Path
from typing import Any


def parse_string_map(value: Any, field_name: str, source: Path) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RuntimeError(f"{field_name} must be a table in {source}")

    parsed: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise RuntimeError(f"{field_name} keys and values must be strings in {source}")
        key_clean = key.strip()
        item_clean = item.strip()
        if key_clean and item_clean:
            parsed[key_clean] = item_clean
    return parsed


def parse_csv_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []
