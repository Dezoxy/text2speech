from __future__ import annotations

import subprocess
import sys


def test_legacy_pipeline_wrapper_help() -> None:
    result = subprocess.run(
        [sys.executable, "pipeline.py", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--input" in result.stdout


def test_legacy_make_wrapper_help() -> None:
    result = subprocess.run(
        [sys.executable, "make_mp3.py", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--input" in result.stdout


def test_module_entrypoint_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "text2speech", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--input" in result.stdout
