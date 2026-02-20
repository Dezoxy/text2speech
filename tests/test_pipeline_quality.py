from __future__ import annotations

import json
from pathlib import Path

import pipeline


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
