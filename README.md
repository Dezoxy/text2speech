# text2speech

Automate book conversion from PDF/TXT to audiobook MP3 using Google Cloud Text-to-Speech.

## Start Here

- Fast setup: read `docs/QUICKSTART.md`.
- New to the project: read `docs/PROJECT_BOOK.md` (step-by-step handbook).
- Hungarian handbook: read `docs/PROJECT_BOOK.hu.md`.
- Need commands quickly: use `docs/COMMANDS.md` (explained command reference).
- Working on code: read `docs/DEVELOPER_WORKFLOW.md`.
- Migration notes: read `docs/MIGRATION.md`.

## Folder Layout

- `input/`: source files (`.pdf` or `.txt`)
- `work/tmp/`: temporary processing artifacts (raw text, cleaned text, chunks, `job_manifest.json`)
- `out/`: finished audiobook outputs (`.mp3` and chaptered `.m4b`)

## Configuration

Defaults are loaded from `config.toml`.

Main sections:

- `[paths]`: `input`, `work_dir`, `out_dir`, `cache_dir`
- `[pipeline]`: `voice_profile`, `input_kind`, `ocr`, `ocr_lang`, `ocr_page_min_chars`, `ocr_page_min_alpha_ratio`, `ocr_page_max_replacement_ratio`, `ocr_page_min_score`, `ocr_auto_bad_page_ratio`, `lang`, `voice_name`, `max_chars`, `speaking_rate`, `pitch`, `ui`, `use_ssml`, `cleanup_diff_preview`, `cleanup_diff_max_lines`, `dashboard`, `export_m4b`, `chunk_cache`, `workers`, `rate_limit_rps`, `retry_failed_chunks`, `retry_failed_jobs`
- `[voice_profiles]`: presets like `academic`, `novel`, `scan-heavy`
- `[pronunciation]`: dictionary aliases for names/terms
- `[ssml_rules]`: acronym spell-out behavior (`off|auto|list`)
- `[export]`: `.m4b` metadata (`book_title`, `book_author`, `cover_image`, chapter title cleanup)
- `[retry]`: `attempts`, `base_delay`, `backoff_factor`, `max_delay`, `jitter`
- `[limits]`: `max_chars_per_run`, `max_cost_usd`, `confirm_before_spend`
- `[flags]`: default booleans for skip/test/estimate modes

Use another config file:

```bash
t2s --config /path/to/config.toml
```

CLI args always override config values.

## What This Repo Contains

- `launcher.py`: root launcher for end-to-end flow (`input -> OCR/extract -> cleanup -> TTS -> out/*.mp3`).
- `dependencies.toml`: dependency manifest used by startup precheck/installer.
- `docs/QUICKSTART.md`: one-page quick start.
- `docs/PROJECT_BOOK.md`: beginner-friendly walkthrough.
- `docs/PROJECT_BOOK.hu.md`: Hungarian handbook.
- `docs/COMMANDS.md`: detailed command guide with what/when/expected-result notes.
- `docs/DEVELOPER_WORKFLOW.md`: module map + developer workflow + smoke tests.
- `docs/MIGRATION.md`: compatibility and migration notes.

## System Requirements

- Python 3.10+
- Google Cloud Text-to-Speech API enabled
- Google credentials available to SDK (for example `GOOGLE_APPLICATION_CREDENTIALS`)

Install Python packages:

```bash
pip install google-cloud-texttospeech rich
```

## Installable CLI

Install the project as a CLI app (recommended):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install .
```

Then use:

```bash
t2s --help
t2s --version
```

Direct script mode is still supported:

```bash
python3 launcher.py --help
```


## Module Map + Developer Workflow

For contributors, the module map and refactor workflow are documented in:

- `docs/DEVELOPER_WORKFLOW.md`
- `docs/MIGRATION.md`

This includes:

- package module ownership (`pipeline_app`, `make_mp3_app`, `shared/*`)
- single launcher entrypoint expectations (`launcher.py`)
- required lint/test/smoke validation commands

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
t2s
```

This processes all supported files in `input/` (`.pdf`, `.txt`), stores tmp artifacts in `work/tmp/`, and writes final merged `.mp3` plus chaptered `.m4b` to `out/`.

Useful options:

- `--input /path/to/file-or-folder`
- `--input-kind auto|clean-txt|raw-txt|pdf`
- `--work-dir work`
- `--out-dir out`
- `--ocr auto|always|never`
- `--voice-profile academic|novel|scan-heavy`
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
- `--pronunciation-file pronunciation.toml`
- `--acronym-mode off|auto|list`
- `--acronym-min-len 2 --acronym-max-len 5`
- `--acronym-include API,PDF,AI`
- `--acronym-exclude CHAPTER,FEJEZET`
- `--cleanup-diff-preview` / `--no-cleanup-diff-preview`
- `--cleanup-diff-max-lines 200`
- `--dashboard` / `--no-dashboard`
- `--chunk-cache` / `--no-chunk-cache`
- `--cache-dir work/cache`
- `--workers 4`
- `--rate-limit-rps 15`
- `--retry-attempts 6`
- `--retry-base-delay 1.0`
- `--retry-backoff-factor 1.8`
- `--retry-max-delay 30`
- `--retry-jitter 0.25`
- `--retry-failed-chunks` / `--no-retry-failed-chunks`
- `--retry-failed-jobs` / `--no-retry-failed-jobs`
- `--export-m4b` / `--no-export-m4b`
- `--book-title "Book Title"`
- `--book-author "Author Name"`
- `--cover-image /path/to/cover.jpg`
- `--chapter-title-cleanup` / `--no-chapter-title-cleanup`
- `--max-chars-per-run 2000000`
- `--max-cost-usd 40`
- `--confirm-before-spend` / `--no-confirm-before-spend`
- `--doctor` / `--doctor-only`
- `--watch --watch-interval 5 --watch-stable-seconds 3`
- `--watch-failure-limit 3 --watch-failure-window-seconds 120`
- `--reliable-mode`
- `--deps-file dependencies.toml`
- `--deps-check` / `--no-deps-check`
- `--deps-auto-install` / `--no-deps-auto-install`

## Dependency Precheck

At startup, `t2s` reads `dependencies.toml`, checks installed dependencies, and shows exactly what is missing.

If there are missing items and auto-install is enabled, it asks for your confirmation before installing anything.
If required Python dependencies already exist in local `.venv`, the app auto-relaunches with `.venv/bin/python`.
On Homebrew-managed Python (PEP 668), Python packages are installed into local `.venv`, then the app auto-relaunches.

Default behavior:

- `--deps-check` is enabled
- `--deps-auto-install` is enabled (still asks for approval)

Example:

```bash
t2s
t2s --input /path/to/book.txt --input-kind clean-txt
```

Check-only mode (no installer prompt):

```bash
t2s --no-deps-auto-install
```

Skip startup dependency precheck:

```bash
t2s --no-deps-check
```

## Audio Quality + Chapters

- Smarter cleanup removes common citations/footnote markers and reference-tail noise.
- OCR auto mode now checks page-level extraction quality and uses OCR when bad pages exceed your thresholds.
- When OCR runs in auto mode, pages are selected with a hybrid chooser (direct vs OCR per page).
- Default thresholds are tuned for mixed prose+table books to avoid unnecessary OCR on numeric pages.
- Chapter headings are detected automatically (`Chapter`, `Fejezet`, `Part`, roman/numeric variants).
- SSML shaping is enabled by default for better pauses and heading emphasis (`--use-ssml`).
- Pronunciation dictionary aliases are supported (names/terms via `pronunciation.aliases` or `--pronunciation-file`).
- Acronyms can be spoken as characters using SSML rules (`--acronym-mode`, include/exclude lists).
- Chapter metadata is used to export chaptered `.m4b` alongside merged `.mp3`.
- `.m4b` export supports metadata tags (title/author) and optional embedded cover art.

Example external dictionary file: `pronunciation.toml.example`.

Cleanup preview artifact:

- `work/tmp/<job>/clean_diff_preview.patch` shows a bounded raw-vs-clean diff before TTS.

Disable m4b export if needed:

```bash
t2s --no-export-m4b
```

Disable SSML shaping if needed:

```bash
t2s --no-use-ssml
```

## Cost + Safety Controls

Hard stop by char budget:

```bash
t2s --max-chars-per-run 500000
```

Hard stop by USD estimate:

```bash
t2s --max-cost-usd 10
```

Explicit confirm gate before paid synthesis:

```bash
t2s --confirm-before-spend
```

## Performance + Reliability

- Chunk cache stores synthesized chunk audio by text+voice parameters and reuses it in later runs.
- Parallel workers (`--workers N`) can synthesize multiple chunks at once.
- Rate limiting (`--rate-limit-rps`) and retry policy flags tune backoff behavior under API pressure.
- Multi-job queue dashboard tracks per-job state and can retry failed jobs/chunks.

## Rich Dashboard (TUI)

Use rich mode with dashboard:

```bash
t2s --ui rich --dashboard
```

Dashboard keys during synthesis and multi-job queue mode:

- `p`: pause/resume
- `r`: arm retry pass for failed chunks/jobs
- `q`: cancel run

## Resume Workflow

If a job fails or gets interrupted, resume unfinished jobs:

```bash
t2s --resume
```

Resume one specific job:

```bash
t2s --resume-job work/tmp/<job-folder>
```

or:

```bash
t2s --resume-job work/tmp/<job-folder>/job_manifest.json
```

Each job manifest tracks per-step status (`extract`, `clean`, `tts`, `merge`) and run status (`in_progress`, `completed`, `failed`).

## Doctor Check

Validate environment requirements before running:

```bash
t2s --doctor-only
```

Run doctor first, then continue into pipeline only if checks pass:

```bash
t2s --doctor
```

## Watch Mode

Watch `input/` continuously and auto-process new/changed files:

```bash
t2s --watch
```

Useful watch flags:

- `--watch-interval 5` (scan every 5 seconds)
- `--watch-stable-seconds 3` (file must be unchanged for 3 seconds before processing)
- `--watch-max-cycles 20` (for testing; `0` means run until interrupted)
- `--watch-failure-limit 3` (per-file circuit breaker threshold)
- `--watch-failure-window-seconds 120` (circuit breaker window)

Strict reliability preset:

- `--reliable-mode` enables `--doctor`, caps retry aggressiveness, applies safer watch defaults, disables `--confirm-before-spend` in non-interactive terminals, and validates written run reports.

## Run Reports

After each run (`batch`, `resume`, `watch`) a JSON report is written to:

- `work/reports/run-YYYYMMDD-HHMMSS-ffffff.json`
- `work/reports/latest_run.json`

Reports now include analytics totals and per-job metrics:

- generated characters
- estimated cost
- TTS duration / job duration
- failures
- cache hit rate

## Tests

Run automated tests:

```bash
pytest -q
```

Lint:

```bash
ruff check .
```

## Clean TXT Shortcut

If your `.txt` is already clean, run:

```bash
t2s --input /path/to/book.txt --input-kind clean-txt
```

## Notes

- Google TTS billing is character-based.
- Pipeline shows character/cost preview before synthesis.
- `--test-run` never calls Google TTS.
