from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

REPORT_SCHEMA_VERSION = 2


def iso_duration_seconds(start_iso: str | None, end_iso: str | None) -> float | None:
    if not start_iso or not end_iso:
        return None
    try:
        start = datetime.fromisoformat(start_iso)
        end = datetime.fromisoformat(end_iso)
    except ValueError:
        return None
    duration = (end - start).total_seconds()
    if duration < 0:
        return None
    return duration


def build_run_analytics(
    jobs: list[dict[str, Any]],
    failures: list[tuple[str, str]],
    *,
    read_manifest: Callable[[Path], dict[str, Any]],
) -> dict[str, Any]:
    per_job: list[dict[str, Any]] = []
    total_chars = 0
    total_billable_chars = 0
    total_cost_usd = 0.0
    total_tts_duration_s = 0.0
    total_job_duration_s = 0.0
    total_tts_failures = 0
    total_cache_hits = 0
    total_cache_misses = 0

    for record in jobs:
        manifest_path_raw = record.get("manifest_path")
        if not isinstance(manifest_path_raw, str) or not manifest_path_raw:
            continue

        manifest_path = Path(manifest_path_raw)
        if not manifest_path.exists():
            continue

        try:
            manifest = read_manifest(manifest_path)
        except Exception:  # noqa: BLE001
            continue

        steps = manifest.get("steps", {})
        tts_step = steps.get("tts", {}) if isinstance(steps, dict) else {}
        tts_summary = tts_step.get("summary", {}) if isinstance(tts_step, dict) else {}
        if not isinstance(tts_summary, dict):
            tts_summary = {}

        chars = int(tts_summary.get("generated_chars", 0) or 0)
        billable = int(tts_summary.get("run_billable_chars", 0) or 0)
        cost = float(tts_summary.get("run_estimated_cost_usd", 0.0) or 0.0)
        tts_duration = float(tts_summary.get("run_duration_s", 0.0) or 0.0)
        tts_failures = int(tts_summary.get("failures", 0) or 0)
        cache_hits = int(tts_summary.get("cache_hits", 0) or 0)
        generated_chunks = int(tts_summary.get("generated", 0) or 0)
        chunk_attempts = max(0, generated_chunks + cache_hits)
        cache_misses = max(0, chunk_attempts - cache_hits)
        cache_hit_rate = (cache_hits / chunk_attempts) if chunk_attempts > 0 else 0.0

        job_duration = iso_duration_seconds(
            str(manifest.get("created_at", "")) or None,
            str(manifest.get("updated_at", "")) or None,
        )

        total_chars += chars
        total_billable_chars += billable
        total_cost_usd += cost
        total_tts_duration_s += tts_duration
        total_tts_failures += tts_failures
        total_cache_hits += cache_hits
        total_cache_misses += cache_misses
        if job_duration is not None:
            total_job_duration_s += job_duration

        per_job.append(
            {
                "manifest_path": str(manifest_path),
                "input": manifest.get("input"),
                "status": manifest.get("status"),
                "generated_chars": chars,
                "run_billable_chars": billable,
                "run_estimated_cost_usd": round(cost, 6),
                "tts_duration_s": round(tts_duration, 3),
                "job_duration_s": round(job_duration, 3) if job_duration is not None else None,
                "tts_failures": tts_failures,
                "cache_hits": cache_hits,
                "cache_misses": cache_misses,
                "cache_hit_rate": round(cache_hit_rate, 4),
            }
        )

    cache_total = total_cache_hits + total_cache_misses
    overall_cache_hit_rate = (total_cache_hits / cache_total) if cache_total > 0 else 0.0

    return {
        "totals": {
            "generated_chars": total_chars,
            "run_billable_chars": total_billable_chars,
            "run_estimated_cost_usd": round(total_cost_usd, 6),
            "tts_duration_s": round(total_tts_duration_s, 3),
            "job_duration_s": round(total_job_duration_s, 3),
            "tts_failures": total_tts_failures,
            "failed_jobs": len(failures),
            "cache_hits": total_cache_hits,
            "cache_misses": total_cache_misses,
            "cache_hit_rate": round(overall_cache_hit_rate, 4),
        },
        "jobs": per_job,
    }


def build_run_report(
    *,
    mode: str,
    args_payload: dict[str, Any],
    jobs: list[dict[str, Any]],
    failures: list[tuple[str, str]],
    analytics: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "args": args_payload,
        "summary": {
            "attempted_jobs": len(jobs) + len(failures),
            "recorded_jobs": len(jobs),
            "completed_jobs": sum(1 for job in jobs if str(job.get("status")) == "completed"),
            "failed_jobs": len(failures),
        },
        "analytics": analytics,
        "jobs": jobs,
        "failures": [{"target": target, "error": error} for target, error in failures],
    }


def write_report_files(work_dir: Path, report: dict[str, Any]) -> Path:
    reports_dir = work_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    report_path = reports_dir / f"run-{timestamp}.json"
    report_text = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    report_path.write_text(report_text)
    (reports_dir / "latest_run.json").write_text(report_text)
    return report_path
