from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from text2speech import pipeline_app as pipeline


def test_page_meets_ocr_threshold() -> None:
    thresholds = pipeline.OcrQualityThresholds(
        page_min_chars=30,
        page_min_alpha_ratio=0.22,
        page_max_replacement_ratio=0.02,
        page_min_score=0.24,
        auto_bad_page_ratio=0.10,
        prefer_ocr_score_delta=0.04,
    )
    good_quality = pipeline.assess_text_quality("This is a quality page with readable words. " * 10)
    bad_quality = pipeline.assess_text_quality("\ufffd" * 200)

    assert pipeline.page_meets_ocr_threshold(good_quality, thresholds)
    assert not pipeline.page_meets_ocr_threshold(bad_quality, thresholds)


def test_write_cleanup_diff_preview_truncates(tmp_path: Path) -> None:
    raw = "\n".join(f"line {idx}" for idx in range(20))
    cleaned = "\n".join(f"line {idx}" for idx in range(10, 20))
    out_file = tmp_path / "preview.patch"

    stats = pipeline.write_cleanup_diff_preview(
        raw_text=raw,
        cleaned_text=cleaned,
        output_path=out_file,
        max_lines=8,
    )

    assert out_file.exists()
    text = out_file.read_text()
    assert stats["truncated"] is True
    assert stats["preview_lines"] == 8
    assert "... truncated " in text


def test_classify_txt_input_auto_detects_clean_text() -> None:
    raw = "Chapter 1\n\nThis is already clean text.\n"
    cleaned = raw

    decision = pipeline.classify_txt_input(raw, cleaned, requested_kind="auto")
    assert decision.kind == "clean-txt"
    assert decision.reason == "auto-identical-after-cleanup"
    assert decision.identical is True


def test_classify_txt_input_forced_modes() -> None:
    raw = "Raw text [12]"
    cleaned = "Raw text"

    forced_clean = pipeline.classify_txt_input(raw, cleaned, requested_kind="clean-txt")
    forced_raw = pipeline.classify_txt_input(raw, cleaned, requested_kind="raw-txt")

    assert forced_clean.kind == "clean-txt"
    assert forced_clean.reason == "forced-clean-txt"
    assert forced_raw.kind == "raw-txt"
    assert forced_raw.reason == "forced-raw-txt"


def test_classify_txt_input_auto_whitespace_only_diff_skips_clean() -> None:
    raw = "Line one.\n\nLine   two.\n"
    cleaned = "Line one.\n\nLine two.\n"

    decision = pipeline.classify_txt_input(raw, cleaned, requested_kind="auto")
    assert decision.kind == "clean-txt"
    assert decision.reason == "auto-whitespace-only-diff"
    assert decision.whitespace_only_change is True


def test_validate_input_kind_for_path_rejects_pdf_mode_for_txt() -> None:
    with pytest.raises(RuntimeError, match="--input-kind=pdf can only be used with PDF input"):
        pipeline.validate_input_kind_for_path("pdf", Path("book.txt"))


def test_validate_input_kind_for_path_rejects_txt_modes_for_pdf() -> None:
    with pytest.raises(RuntimeError, match="--input-kind=clean-txt can only be used with TXT input"):
        pipeline.validate_input_kind_for_path("clean-txt", Path("book.pdf"))
    with pytest.raises(RuntimeError, match="--input-kind=raw-txt can only be used with TXT input"):
        pipeline.validate_input_kind_for_path("raw-txt", Path("book.pdf"))


def test_validate_input_kind_for_path_allows_valid_combinations() -> None:
    pipeline.validate_input_kind_for_path("auto", Path("book.txt"))
    pipeline.validate_input_kind_for_path("clean-txt", Path("book.txt"))
    pipeline.validate_input_kind_for_path("raw-txt", Path("book.txt"))
    pipeline.validate_input_kind_for_path("auto", Path("book.pdf"))
    pipeline.validate_input_kind_for_path("pdf", Path("book.pdf"))


def test_pipeline_test_run_cli_writes_diff_preview(tmp_path: Path) -> None:
    input_txt = tmp_path / "sample.txt"
    input_txt.write_text("CHAPTER 1\n\nhello world.[12]\n")
    work_dir = tmp_path / "work"
    out_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    repo_root = Path(__file__).resolve().parents[1]
    config_path = repo_root / "config.toml"

    # Run a fake end-to-end pipeline flow through the same code path users run.
    raw_args = pipeline.build_parser().parse_args(
        [
            "--config",
            str(config_path),
            "--input",
            str(input_txt),
            "--work-dir",
            str(work_dir),
            "--out-dir",
            str(out_dir),
            "--cache-dir",
            str(cache_dir),
            "--test-run",
            "--ui",
            "plain",
            "--no-deps-check",
        ]
    )
    args = pipeline.resolve_runtime_args(raw_args, pipeline.load_config(config_path))
    result = pipeline.process_single_input_new_job(
        args=args,
        input_path=input_txt,
        work_tmp_root=work_dir / "tmp",
        out_dir=out_dir,
        output_stem="sample",
    )

    manifest = json.loads((Path(result["tmp_dir"]) / "job_manifest.json").read_text())
    clean_step = manifest["steps"]["clean"]
    preview_meta = clean_step["cleanup_diff_preview"]
    assert clean_step["cleanup_diff_preview_enabled"] is True
    assert Path(preview_meta["path"]).exists()


def test_pipeline_forced_clean_txt_skips_cleanup(tmp_path: Path) -> None:
    input_txt = tmp_path / "clean_sample.txt"
    input_txt.write_text("Chapter 1\n\nAlready-clean sample text.\n")
    work_dir = tmp_path / "work"
    out_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    repo_root = Path(__file__).resolve().parents[1]
    config_path = repo_root / "config.toml"

    raw_args = pipeline.build_parser().parse_args(
        [
            "--config",
            str(config_path),
            "--input",
            str(input_txt),
            "--input-kind",
            "clean-txt",
            "--work-dir",
            str(work_dir),
            "--out-dir",
            str(out_dir),
            "--cache-dir",
            str(cache_dir),
            "--test-run",
            "--ui",
            "plain",
            "--no-deps-check",
        ]
    )
    args = pipeline.resolve_runtime_args(raw_args, pipeline.load_config(config_path))
    result = pipeline.process_single_input_new_job(
        args=args,
        input_path=input_txt,
        work_tmp_root=work_dir / "tmp",
        out_dir=out_dir,
        output_stem="clean-sample",
    )

    manifest = json.loads((Path(result["tmp_dir"]) / "job_manifest.json").read_text())
    clean_step = manifest["steps"]["clean"]
    assert clean_step["status"] == "skipped"
    assert clean_step["reason"] == "forced-clean-txt"
    assert clean_step["tts_input_txt"].endswith("/raw.txt")


def test_run_job_queue_retries_failed_job_once(tmp_path: Path) -> None:
    calls = {"count": 0}
    manifest_path = tmp_path / "job_manifest.json"
    manifest = {
        "status": "completed",
        "tmp_dir": str(tmp_path),
        "final_output_mp3": str(tmp_path / "out.mp3"),
        "steps": {},
    }

    def run_once_then_pass() -> tuple[Path, dict[str, object]]:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary failure")
        return manifest_path, manifest

    args = pipeline.argparse.Namespace(
        dashboard=False,
        ui="plain",
        retry_failed_jobs=True,
    )
    jobs = [
        pipeline.QueueJob(
            label="job-a",
            target="job-a",
            run=run_once_then_pass,
        )
    ]

    records, failures = pipeline.run_job_queue(
        args=args,
        mode_label="batch",
        queue_jobs=jobs,
    )

    assert calls["count"] == 2
    assert failures == []
    assert len(records) == 1
    assert records[0]["status"] == "completed"


def test_run_job_queue_reports_failure_without_retry(tmp_path: Path) -> None:
    calls = {"count": 0}

    def always_fail() -> tuple[Path, dict[str, object]]:
        calls["count"] += 1
        raise RuntimeError("permanent failure")

    args = pipeline.argparse.Namespace(
        dashboard=False,
        ui="plain",
        retry_failed_jobs=False,
    )
    jobs = [
        pipeline.QueueJob(
            label="job-b",
            target="job-b",
            run=always_fail,
        )
    ]

    records, failures = pipeline.run_job_queue(
        args=args,
        mode_label="batch",
        queue_jobs=jobs,
    )

    assert calls["count"] == 1
    assert records == []
    assert len(failures) == 1
    assert failures[0][0] == "job-b"


def test_run_job_queue_skips_retry_for_non_retryable_error(tmp_path: Path) -> None:
    calls = {"count": 0}

    def always_fail_non_retryable() -> tuple[Path, dict[str, object]]:
        calls["count"] += 1
        raise RuntimeError("confirm-before-spend requires interactive terminal input.")

    args = pipeline.argparse.Namespace(
        dashboard=False,
        ui="plain",
        retry_failed_jobs=True,
    )
    jobs = [
        pipeline.QueueJob(
            label="job-c",
            target="job-c",
            run=always_fail_non_retryable,
        )
    ]

    records, failures = pipeline.run_job_queue(
        args=args,
        mode_label="batch",
        queue_jobs=jobs,
    )

    assert calls["count"] == 1
    assert records == []
    assert len(failures) == 1
    assert failures[0][0] == "job-c"


def test_is_queue_retryable_error_marks_input_kind_mismatch_non_retryable() -> None:
    assert (
        pipeline.is_queue_retryable_error("--input-kind=pdf can only be used with PDF input")
        is False
    )
    assert (
        pipeline.is_queue_retryable_error(
            "--input-kind=clean-txt can only be used with TXT input"
        )
        is False
    )


def test_classify_exception_retryable_prefers_exception_type() -> None:
    assert pipeline.classify_exception_retryable(pipeline.RetryableTransientError("x")) is True
    assert pipeline.classify_exception_retryable(pipeline.NonRetryableConfigError("x")) is False


def test_build_run_analytics_sums_tts_metrics(tmp_path: Path) -> None:
    manifest_path = tmp_path / "job_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "created_at": "2026-02-20T12:00:00",
                "updated_at": "2026-02-20T12:00:05",
                "input": "input/book.txt",
                "status": "completed",
                "steps": {
                    "tts": {
                        "summary": {
                            "generated": 8,
                            "generated_chars": 1000,
                            "run_billable_chars": 1000,
                            "run_estimated_cost_usd": 0.03,
                            "run_duration_s": 4.5,
                            "failures": 0,
                            "cache_hits": 2,
                        }
                    }
                },
            }
        )
    )
    jobs = [{"manifest_path": str(manifest_path)}]
    analytics = pipeline.build_run_analytics(jobs, failures=[])

    totals = analytics["totals"]
    assert totals["generated_chars"] == 1000
    assert totals["run_billable_chars"] == 1000
    assert totals["run_estimated_cost_usd"] == 0.03
    assert totals["tts_duration_s"] == 4.5
    assert totals["cache_hits"] == 2


def test_discover_resumable_manifests_deduplicates_by_output(tmp_path: Path) -> None:
    work_tmp_root = tmp_path / "tmp"
    work_tmp_root.mkdir(parents=True)
    final_out = tmp_path / "out" / "book.mp3"

    def write_manifest(job_name: str, status: str, mtime: int) -> Path:
        job_dir = work_tmp_root / job_name
        job_dir.mkdir(parents=True)
        manifest_path = job_dir / "job_manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "status": status,
                    "input": str(tmp_path / "input" / "book.txt"),
                    "tmp_dir": str(job_dir),
                    "final_output_mp3": str(final_out),
                }
            )
        )
        os.utime(manifest_path, (mtime, mtime))
        return manifest_path

    old_failed = write_manifest("job-old-failed", "failed", 1000)
    latest_failed = write_manifest("job-latest-failed", "failed", 2000)

    resumable = pipeline.discover_resumable_manifests(work_tmp_root)
    assert resumable == [latest_failed]
    assert old_failed not in resumable


def test_discover_resumable_manifests_ignores_stale_failed_if_latest_completed(tmp_path: Path) -> None:
    work_tmp_root = tmp_path / "tmp"
    work_tmp_root.mkdir(parents=True)
    final_out = tmp_path / "out" / "book.mp3"

    def write_manifest(job_name: str, status: str, mtime: int) -> Path:
        job_dir = work_tmp_root / job_name
        job_dir.mkdir(parents=True)
        manifest_path = job_dir / "job_manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "status": status,
                    "input": str(tmp_path / "input" / "book.txt"),
                    "tmp_dir": str(job_dir),
                    "final_output_mp3": str(final_out),
                }
            )
        )
        os.utime(manifest_path, (mtime, mtime))
        return manifest_path

    write_manifest("job-failed", "failed", 1000)
    write_manifest("job-completed", "completed", 2000)

    resumable = pipeline.discover_resumable_manifests(work_tmp_root)
    assert resumable == []


def test_run_watch_loop_ignores_disappearing_file(monkeypatch, tmp_path: Path) -> None:
    missing = tmp_path / "gone.txt"
    args = pipeline.argparse.Namespace(
        watch_interval=0.0,
        watch_stable_seconds=0.0,
        watch_max_cycles=1,
        dashboard=False,
        ui="plain",
        retry_failed_jobs=False,
    )

    monkeypatch.setattr(
        pipeline,
        "discover_inputs_allow_empty",
        lambda _input_dir: [missing],
    )

    jobs, failures = pipeline.run_watch_loop(
        args=args,
        input_dir=tmp_path,
        work_tmp_root=tmp_path / "work" / "tmp",
        out_dir=tmp_path / "out",
    )

    assert jobs == []
    assert failures == []


def test_discover_resumable_manifests_skips_non_retryable_failed_latest(tmp_path: Path) -> None:
    work_tmp_root = tmp_path / "work" / "tmp"
    work_tmp_root.mkdir(parents=True)
    final_out = tmp_path / "out" / "book.mp3"

    def write_manifest(job_name: str, status: str, mtime: int, retryable: bool | None) -> None:
        job_dir = work_tmp_root / job_name
        job_dir.mkdir(parents=True)
        payload = {
            "status": status,
            "input": str(tmp_path / "input" / "book.txt"),
            "tmp_dir": str(job_dir),
            "final_output_mp3": str(final_out),
        }
        if retryable is not None:
            payload["retryable"] = retryable
        manifest_path = job_dir / "job_manifest.json"
        manifest_path.write_text(json.dumps(payload))
        os.utime(manifest_path, (mtime, mtime))

    write_manifest("job-old-failed", "failed", 1000, True)
    write_manifest("job-latest-failed", "failed", 2000, False)

    resumable = pipeline.discover_resumable_manifests(work_tmp_root)
    assert resumable == []


def test_run_watch_loop_circuit_breaker_suppresses_repeated_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "input.txt"
    file_path.write_text("x")

    args = pipeline.argparse.Namespace(
        watch_interval=0.0,
        watch_stable_seconds=0.0,
        watch_max_cycles=2,
        watch_failure_limit=1,
        watch_failure_window_seconds=60.0,
        dashboard=False,
        ui="plain",
        retry_failed_jobs=False,
    )

    monkeypatch.setattr(
        pipeline,
        "discover_inputs_allow_empty",
        lambda _input_dir: [file_path],
    )

    calls = {"count": 0}

    def always_fail(**_kwargs: object) -> dict[str, object]:
        calls["count"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "process_single_input_new_job", always_fail)

    jobs, failures = pipeline.run_watch_loop(
        args=args,
        input_dir=tmp_path,
        work_tmp_root=tmp_path / "work" / "tmp",
        out_dir=tmp_path / "out",
    )

    assert jobs == []
    assert len(failures) == 1
    assert calls["count"] == 1


def test_run_watch_loop_circuit_breaker_applies_when_signature_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "input.txt"
    file_path.write_text("x")

    args = pipeline.argparse.Namespace(
        watch_interval=0.0,
        watch_stable_seconds=0.0,
        watch_max_cycles=2,
        watch_failure_limit=1,
        watch_failure_window_seconds=60.0,
        dashboard=False,
        ui="plain",
        retry_failed_jobs=False,
    )

    monkeypatch.setattr(
        pipeline,
        "discover_inputs_allow_empty",
        lambda _input_dir: [file_path],
    )

    calls = {"count": 0}

    def always_fail(**_kwargs: object) -> dict[str, object]:
        calls["count"] += 1
        file_path.write_text(f"x{calls['count']}")
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "process_single_input_new_job", always_fail)

    jobs, failures = pipeline.run_watch_loop(
        args=args,
        input_dir=tmp_path,
        work_tmp_root=tmp_path / "work" / "tmp",
        out_dir=tmp_path / "out",
    )

    assert jobs == []
    assert len(failures) == 1
    assert calls["count"] == 1


def test_run_watch_loop_uses_failure_timestamp_not_cycle_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "input.txt"
    file_path.write_text("x")

    args = pipeline.argparse.Namespace(
        watch_interval=0.0,
        watch_stable_seconds=0.0,
        watch_max_cycles=2,
        watch_failure_limit=1,
        watch_failure_window_seconds=1.0,
        dashboard=False,
        ui="plain",
        retry_failed_jobs=False,
    )

    monkeypatch.setattr(
        pipeline,
        "discover_inputs_allow_empty",
        lambda _input_dir: [file_path],
    )

    time_points = [0.0, 10.0, 10.5]

    def fake_time() -> float:
        if len(time_points) > 1:
            return time_points.pop(0)
        return time_points[0]

    monkeypatch.setattr(pipeline.time, "time", fake_time)

    calls = {"count": 0}

    def always_fail(**_kwargs: object) -> dict[str, object]:
        calls["count"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "process_single_input_new_job", always_fail)

    jobs, failures = pipeline.run_watch_loop(
        args=args,
        input_dir=tmp_path,
        work_tmp_root=tmp_path / "work" / "tmp",
        out_dir=tmp_path / "out",
    )

    assert jobs == []
    assert len(failures) == 1
    assert calls["count"] == 1

def test_run_watch_loop_retries_same_signature_after_failure_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "input.txt"
    file_path.write_text("x")

    args = pipeline.argparse.Namespace(
        watch_interval=0.0,
        watch_stable_seconds=0.0,
        watch_max_cycles=2,
        watch_failure_limit=1,
        watch_failure_window_seconds=1.0,
        dashboard=False,
        ui="plain",
        retry_failed_jobs=False,
    )

    monkeypatch.setattr(
        pipeline,
        "discover_inputs_allow_empty",
        lambda _input_dir: [file_path],
    )

    time_points = [0.0, 0.0, 2.0, 2.0]

    def fake_time() -> float:
        if len(time_points) > 1:
            return time_points.pop(0)
        return time_points[0]

    monkeypatch.setattr(pipeline.time, "time", fake_time)

    calls = {"count": 0}

    def always_fail(**_kwargs: object) -> dict[str, object]:
        calls["count"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "process_single_input_new_job", always_fail)

    jobs, failures = pipeline.run_watch_loop(
        args=args,
        input_dir=tmp_path,
        work_tmp_root=tmp_path / "work" / "tmp",
        out_dir=tmp_path / "out",
    )

    assert jobs == []
    assert len(failures) == 2
    assert calls["count"] == 2
