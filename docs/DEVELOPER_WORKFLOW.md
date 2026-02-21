# Developer Workflow

This document describes how to work on the refactored codebase.

## 1) Module Map

- `text2speech/pipeline_app.py`
  - End-to-end orchestration (`t2s`) for extract -> clean -> TTS -> merge/export -> report.
- `text2speech/make_mp3_app.py`
  - Internal TTS engine app used by the pipeline runtime.
- `text2speech/shared/`
  - `cli_args.py`: reusable argparse option groups.
  - `dependencies.py`: dependency precheck/install/relaunch flow.
  - `manifest.py`: manifest read/write/status/step lifecycle.
  - `models.py`: typed runtime/domain model and strict validation.
  - `parsing.py`: shared parsing helpers.
  - `reporting.py`: report schema + analytics + report writing.
  - `tts_engine.py`: composable TTS engine seams (client/planner/shaper/cache/retry).
  - `validators.py`: shared validation helpers.
- Root launcher:
  - `launcher.py` -> `text2speech.pipeline_app`

## 2) Keep Compatibility

Root script launcher command:

- `python3 launcher.py ...`

Package command:

- `t2s ...`

## 3) Local Dev Loop

```bash
python3 -m ruff check .
python3 -m pytest -q
python3 -m py_compile text2speech/pipeline_app.py text2speech/make_mp3_app.py text2speech/shared/*.py
```

## 4) Smoke Tests (No API Spend)

```bash
python3 launcher.py --input tests/fixtures/golden_input.txt --test-run --ui plain --no-deps-check --work-dir /tmp/t2s-work --out-dir /tmp/t2s-out --cache-dir /tmp/t2s-cache
python3 launcher.py --input tests/fixtures/golden_input.txt --input-kind clean-txt --test-run --ui plain --no-deps-check --work-dir /tmp/t2s-clean-work --out-dir /tmp/t2s-clean-out --cache-dir /tmp/t2s-clean-cache
```

## 5) Refactor Rule of Thumb

- Move reusable logic into `text2speech/shared/*` first.
- Keep single-launcher behavior stable.
- Update tests before/with refactors.
- Keep report schema stable unless intentionally version-bumped.
