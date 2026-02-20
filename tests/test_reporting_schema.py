from __future__ import annotations

from text2speech.shared import reporting


def test_report_schema_version_constant() -> None:
    assert reporting.REPORT_SCHEMA_VERSION == 2


def test_build_run_report_uses_schema_version() -> None:
    report = reporting.build_run_report(
        mode="batch",
        args_payload={"input": "input"},
        jobs=[],
        failures=[],
        analytics={"totals": {}, "jobs": []},
    )
    assert report["schema_version"] == reporting.REPORT_SCHEMA_VERSION
