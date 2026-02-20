from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def read_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def set_manifest_status(
    manifest_path: Path,
    manifest: dict[str, Any],
    status: str,
    error: str | None = None,
) -> None:
    manifest["status"] = status
    manifest["updated_at"] = now_iso()
    if error is None:
        manifest.pop("error", None)
    else:
        manifest["error"] = error
    write_manifest(manifest_path, manifest)


def set_step(
    manifest_path: Path,
    manifest: dict[str, Any],
    step: str,
    status: str,
    **fields: Any,
) -> None:
    steps = manifest.setdefault("steps", {})
    entry = dict(steps.get(step, {}))
    entry.update(fields)
    entry["status"] = status
    entry["updated_at"] = now_iso()
    if status != "failed":
        entry.pop("error", None)
    steps[step] = entry
    manifest["updated_at"] = now_iso()
    write_manifest(manifest_path, manifest)


def step_status(manifest: dict[str, Any], step: str) -> str | None:
    entry = manifest.get("steps", {}).get(step, {})
    if isinstance(entry, dict):
        return entry.get("status")
    return None
