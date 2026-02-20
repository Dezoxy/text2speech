# Command Guide

This file is a practical command manual for `text2speech`.
Each section explains:

- what the command does
- when to use it
- what result to expect

If you want a full walkthrough, read `docs/PROJECT_BOOK.md` first.

## 1) Install And First Check

### Create local environment and install the app

Use this once when setting up the project on a machine.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install .
```

Expected result:
- `t2s` and `t2s-make` commands are available in the active shell.

### Install optional developer tools (tests + lint)

Use this if you will run tests or maintain the code.

```bash
pip install '.[dev]'
```

Expected result:
- `pytest` and `ruff` are installed in `.venv`.

### Install required external binaries on macOS

Use this once on macOS for PDF/OCR/audio merge support.

```bash
brew install poppler ocrmypdf tesseract ffmpeg
```

Expected result:
- `pdftotext`, `ocrmypdf`, `tesseract`, `ffmpeg` are available.

## 2) Core Workflow Commands

### Process all books in `input/`

Use this as the default daily command.

```bash
t2s
```

Expected result:
- source files are read from `input/`
- temp files are created in `work/tmp/`
- final audio is written to `out/`

### Process one file directly

Use this for one-off conversion or debugging a single document.

```bash
t2s --input input/book.pdf
t2s --input input/book.txt
```

Expected result:
- only the selected file is processed.

### Override working and output folders

Use this when you want custom folder layout.

```bash
t2s --work-dir work --out-dir out --cache-dir work/cache
```

Expected result:
- pipeline artifacts and outputs use the exact paths you pass.

## 3) OCR Commands (PDF Quality Control)

### Pick OCR strategy

Use `auto` for normal use, `always` for scanned PDFs, `never` for digital PDFs.

```bash
t2s --ocr auto
t2s --ocr always
t2s --ocr never
```

### Set OCR language pack

Use languages installed in Tesseract.

```bash
t2s --ocr-lang hun+eng
```

### Tune bad-page detection in `auto` mode

Use these only if OCR decisions are too aggressive or too weak.

```bash
t2s --ocr-page-min-chars 30
t2s --ocr-page-min-alpha-ratio 0.22
t2s --ocr-page-max-replacement-ratio 0.02
t2s --ocr-page-min-score 0.24
t2s --ocr-auto-bad-page-ratio 0.10
```

Expected result:
- `auto` mode switches to OCR only when page-quality thresholds indicate it should.

## 4) Voice, Pronunciation, And SSML

### Pick a voice profile preset

Use profiles as starting points:
- `academic`: neutral, steady
- `novel`: more narrative feel
- `scan-heavy`: safer defaults for noisy OCR text

```bash
t2s --voice-profile academic
t2s --voice-profile novel
t2s --voice-profile scan-heavy
```

### Override exact voice parameters

Use this after selecting a profile if you want fine tuning.

```bash
t2s --lang hu-HU --voice-name hu-HU-Chirp3-HD-Erinome
t2s --max-chars 3500 --speaking-rate 1.0 --pitch 0.0
```

### Enable or disable SSML shaping

Use SSML for better pauses and headings.

```bash
t2s --use-ssml
t2s --no-use-ssml
```

### Add pronunciation dictionary and acronym behavior

Use this for names, acronyms, and technical words.

```bash
t2s --pronunciation-file pronunciation.toml
t2s --acronym-mode auto
t2s --acronym-min-len 2 --acronym-max-len 5
t2s --acronym-include API,PDF,AI
t2s --acronym-exclude CHAPTER,FEJEZET
```

Expected result:
- problematic words are spoken the way you define.

## 5) Cleanup And Diff Preview

### Show cleanup diff artifact

Use this to inspect what text cleanup changed before synthesis.

```bash
t2s --cleanup-diff-preview
t2s --cleanup-diff-max-lines 300
```

Disable when you do not need preview artifacts:

```bash
t2s --no-cleanup-diff-preview
```

Expected result:
- patch-style diff file appears under `work/tmp/<job>/clean_diff_preview.patch`.

## 6) Cost And Safety Commands

### Hard-limit by characters and budget

Use these to prevent accidental expensive runs.

```bash
t2s --max-chars-per-run 500000
t2s --max-cost-usd 10
```

### Require manual confirmation before paid synthesis

```bash
t2s --confirm-before-spend
t2s --no-confirm-before-spend
```

Expected result:
- pipeline aborts early if estimates exceed your configured limits.

## 7) Dashboard, Concurrency, Retry

### Use rich TUI and dashboard

```bash
t2s --ui rich --dashboard
```

Keyboard controls:
- `p` pause/resume
- `r` trigger retry pass after first pass
- `q` cancel run

### Tune speed and reliability

```bash
t2s --workers 4
t2s --rate-limit-rps 15
t2s --retry-attempts 6 --retry-base-delay 1.0
t2s --retry-backoff-factor 1.8 --retry-max-delay 30 --retry-jitter 0.25
t2s --retry-failed-chunks
t2s --retry-failed-jobs
```

Expected result:
- faster throughput with controlled API pressure and better recovery from transient failures.

## 8) Output Format Commands

### MP3 + chaptered M4B export

```bash
t2s --export-m4b
t2s --no-export-m4b
```

### Add metadata and cover to M4B

```bash
t2s --book-title "My Book"
t2s --book-author "Author Name"
t2s --cover-image /path/to/cover.jpg
t2s --chapter-title-cleanup
t2s --no-chapter-title-cleanup
```

Expected result:
- merged `.mp3` and optional chaptered `.m4b` with clean chapter titles and tags.

## 9) Test Mode (No Google Calls)

### Fake run for workflow testing

Use this to demo or debug pipeline behavior without spending money.

```bash
t2s --test-run --ui rich
t2s --test-run --test-delay 0.005 --ui plain
t2s --test-run --test-write-placeholders
t2s --test-run --no-test-write-placeholders
```

Expected result:
- pipeline stages run with simulated synthesis and progress output.

## 10) Resume And Watch

### Resume unfinished work

```bash
t2s --resume
t2s --resume-job work/tmp/<job-folder>
t2s --resume-job work/tmp/<job-folder>/job_manifest.json
```

Expected result:
- unfinished jobs continue from saved manifest state.

### Watch input folder continuously

```bash
t2s --watch
t2s --watch --watch-interval 5 --watch-stable-seconds 3
t2s --watch --watch-max-cycles 10 --test-run --test-delay 0 --ui plain
```

Expected result:
- new or changed files in `input/` are processed automatically.

## 11) Environment Doctor And Dependency Precheck

### Run doctor only

```bash
t2s --doctor-only
```

### Run doctor, then continue pipeline

```bash
t2s --doctor
```

### Dependency precheck controls

```bash
t2s --deps-file /path/to/dependencies.toml
t2s --no-deps-auto-install
t2s --no-deps-check
```

Expected result:
- startup checks missing tools/packages and can offer guided install.

## 12) Direct Text-To-Speech Command (`t2s-make`)

Use `t2s-make` if your `.txt` is already extracted and cleaned.

### Standard run

```bash
t2s-make --input /path/to/book.txt --output-dir out_mp3
t2s-make --input /path/to/book.txt --use-ssml
t2s-make --input /path/to/book.txt --ui rich --dashboard
t2s-make --input /path/to/book.txt --max-chars-per-run 500000 --max-cost-usd 10
t2s-make --input /path/to/book.txt --confirm-before-spend
t2s-make --input /path/to/book.txt --pronunciation-file pronunciation.toml
t2s-make --input /path/to/book.txt --acronym-mode list --acronym-include API,PDF
t2s-make --input /path/to/book.txt --chunk-cache --cache-dir work/cache
t2s-make --input /path/to/book.txt --workers 4 --rate-limit-rps 15
t2s-make --input /path/to/book.txt --retry-attempts 6 --retry-base-delay 1.0
```

### Estimate only

```bash
t2s-make --input /path/to/book.txt --estimate-only --ui rich
```

### Fake test run

```bash
t2s-make --input /path/to/book.txt --test-run --ui rich
t2s-make --input /path/to/book.txt --test-run --test-delay 0.005
t2s-make --input /path/to/book.txt --test-run --test-write-placeholders
```

## 13) Reports, Help, And Maintenance

### Help and version

```bash
t2s --help
t2s --version
t2s-make --help
```

### Report files

After each run, check:
- `work/reports/run-YYYYMMDD-HHMMSS-ffffff.json`
- `work/reports/latest_run.json`

Report analytics include:
- chars and billable chars
- estimated cost
- TTS/job duration
- failures
- cache hit rate

### Lint and tests

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest -q
```
