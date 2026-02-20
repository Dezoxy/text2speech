# text2speech

Automate book conversion from PDF/TXT to audiobook MP3 using Google Cloud Text-to-Speech.

## Folder Layout

- `input/`: source files (`.pdf` or `.txt`)
- `work/tmp/`: temporary processing artifacts (raw text, cleaned text, chunks, `job_manifest.json`)
- `out/`: finished audiobook outputs (`.mp3` and chaptered `.m4b`)

## Configuration

Defaults are loaded from `config.toml`.

Main sections:

- `[paths]`: `input`, `work_dir`, `out_dir`
- `[pipeline]`: `ocr`, `ocr_lang`, `ocr_page_min_chars`, `ocr_page_min_alpha_ratio`, `ocr_page_max_replacement_ratio`, `ocr_page_min_score`, `ocr_auto_bad_page_ratio`, `lang`, `voice_name`, `max_chars`, `speaking_rate`, `pitch`, `ui`, `use_ssml`, `cleanup_diff_preview`, `cleanup_diff_max_lines`, `dashboard`, `export_m4b`
- `[limits]`: `max_chars_per_run`, `max_cost_usd`, `confirm_before_spend`
- `[flags]`: default booleans for skip/test/estimate modes

Use another config file:

```bash
python3 pipeline.py --config /path/to/config.toml
```

CLI args always override config values.

## What This Repo Contains

- `pipeline.py`: End-to-end flow (`input -> OCR/extract -> cleanup -> TTS -> out/*.mp3`).
- `make_mp3.py`: Direct TTS chunk generator for already prepared `.txt` files.
- `dependencies.toml`: dependency manifest used by startup precheck/installer.
- `docs/COMMANDS.md`: command cheat sheet.

## System Requirements

- Python 3.10+
- Google Cloud Text-to-Speech API enabled
- Google credentials available to SDK (for example `GOOGLE_APPLICATION_CREDENTIALS`)

Install Python packages:

```bash
pip install google-cloud-texttospeech rich
```

Install local command-line tools used by the pipeline:

- `pdftotext` (from Poppler)
- `ocrmypdf` (for OCR fallback)
- `tesseract` (used by `ocrmypdf`)
- `ffmpeg` (for final merge)

macOS (Homebrew):

```bash
brew install poppler ocrmypdf tesseract ffmpeg
```

## Automated Pipeline (Recommended)

Put your books in `input/`, then run:

```bash
python3 pipeline.py
```

This processes all supported files in `input/` (`.pdf`, `.txt`), stores tmp artifacts in `work/tmp/`, and writes final merged `.mp3` plus chaptered `.m4b` to `out/`.

Useful options:

- `--input /path/to/file-or-folder`
- `--work-dir work`
- `--out-dir out`
- `--ocr auto|always|never`
- `--ocr-lang hun+eng`
- `--ocr-page-min-chars 30`
- `--ocr-page-min-alpha-ratio 0.22`
- `--ocr-page-max-replacement-ratio 0.02`
- `--ocr-page-min-score 0.24`
- `--ocr-auto-bad-page-ratio 0.10`
- `--skip-tts` / `--no-skip-tts`
- `--skip-merge` / `--no-skip-merge`
- `--ui auto|plain|rich`
- `--estimate-only` / `--no-estimate-only`
- `--test-run` / `--no-test-run`
- `--test-delay 0.02`
- `--test-write-placeholders` / `--no-test-write-placeholders`
- `--use-ssml` / `--no-use-ssml`
- `--cleanup-diff-preview` / `--no-cleanup-diff-preview`
- `--cleanup-diff-max-lines 200`
- `--dashboard` / `--no-dashboard`
- `--export-m4b` / `--no-export-m4b`
- `--max-chars-per-run 2000000`
- `--max-cost-usd 40`
- `--confirm-before-spend` / `--no-confirm-before-spend`
- `--doctor` / `--doctor-only`
- `--watch --watch-interval 5 --watch-stable-seconds 3`
- `--deps-file dependencies.toml`
- `--deps-check` / `--no-deps-check`
- `--deps-auto-install` / `--no-deps-auto-install`

## Dependency Precheck

At startup, `pipeline.py` and `make_mp3.py` read `dependencies.toml`, check installed dependencies, and show exactly what is missing.

If there are missing items and auto-install is enabled, it asks for your confirmation before installing anything.
If required Python dependencies already exist in local `.venv`, the app auto-relaunches with `.venv/bin/python`.
On Homebrew-managed Python (PEP 668), Python packages are installed into local `.venv`, then the app auto-relaunches.

Default behavior:

- `--deps-check` is enabled
- `--deps-auto-install` is enabled (still asks for approval)

Example:

```bash
python3 pipeline.py
python3 make_mp3.py --input /path/to/book.txt
```

Check-only mode (no installer prompt):

```bash
python3 pipeline.py --no-deps-auto-install
python3 make_mp3.py --input /path/to/book.txt --no-deps-auto-install
```

Skip startup dependency precheck:

```bash
python3 pipeline.py --no-deps-check
python3 make_mp3.py --input /path/to/book.txt --no-deps-check
```

## Audio Quality + Chapters

- Smarter cleanup removes common citations/footnote markers and reference-tail noise.
- OCR auto mode now checks page-level extraction quality and uses OCR when bad pages exceed your thresholds.
- When OCR runs in auto mode, pages are selected with a hybrid chooser (direct vs OCR per page).
- Default thresholds are tuned for mixed prose+table books to avoid unnecessary OCR on numeric pages.
- Chapter headings are detected automatically (`Chapter`, `Fejezet`, `Part`, roman/numeric variants).
- SSML shaping is enabled by default for better pauses and heading emphasis (`--use-ssml`).
- Chapter metadata is used to export chaptered `.m4b` alongside merged `.mp3`.

Cleanup preview artifact:

- `work/tmp/<job>/clean_diff_preview.patch` shows a bounded raw-vs-clean diff before TTS.

Disable m4b export if needed:

```bash
python3 pipeline.py --no-export-m4b
```

Disable SSML shaping if needed:

```bash
python3 pipeline.py --no-use-ssml
```

## Cost + Safety Controls

Hard stop by char budget:

```bash
python3 pipeline.py --max-chars-per-run 500000
```

Hard stop by USD estimate:

```bash
python3 pipeline.py --max-cost-usd 10
```

Explicit confirm gate before paid synthesis:

```bash
python3 pipeline.py --confirm-before-spend
```

The same controls are available in `make_mp3.py`.

## Rich Dashboard (TUI)

Use rich mode with dashboard:

```bash
python3 pipeline.py --ui rich --dashboard
```

Dashboard keys during synthesis:

- `p`: pause/resume
- `r`: arm retry pass for failed chunks
- `q`: cancel run

## Resume Workflow

If a job fails or gets interrupted, resume unfinished jobs:

```bash
python3 pipeline.py --resume
```

Resume one specific job:

```bash
python3 pipeline.py --resume-job work/tmp/<job-folder>
```

or:

```bash
python3 pipeline.py --resume-job work/tmp/<job-folder>/job_manifest.json
```

Each job manifest tracks per-step status (`extract`, `clean`, `tts`, `merge`) and run status (`in_progress`, `completed`, `failed`).

## Doctor Check

Validate environment requirements before running:

```bash
python3 pipeline.py --doctor-only
```

Run doctor first, then continue into pipeline only if checks pass:

```bash
python3 pipeline.py --doctor
```

## Watch Mode

Watch `input/` continuously and auto-process new/changed files:

```bash
python3 pipeline.py --watch
```

Useful watch flags:

- `--watch-interval 5` (scan every 5 seconds)
- `--watch-stable-seconds 3` (file must be unchanged for 3 seconds before processing)
- `--watch-max-cycles 20` (for testing; `0` means run until interrupted)

## Run Reports

After each run (`batch`, `resume`, `watch`) a JSON report is written to:

- `work/reports/run-YYYYMMDD-HHMMSS-ffffff.json`
- `work/reports/latest_run.json`

## Direct TTS Script (Prepared TXT Input)

If you already have a clean text file:

```bash
python3 make_mp3.py --input /path/to/book.txt --output-dir out_mp3
```

## Notes

- Google TTS billing is character-based.
- Pipeline shows character/cost preview before synthesis.
- `--test-run` never calls Google TTS.
