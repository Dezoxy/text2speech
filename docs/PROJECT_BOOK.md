# text2speech Little Book

This guide explains the project from zero to daily usage.
If `docs/COMMANDS.md` is the cheat sheet, this file is the story.

## Chapter 1: What This Project Does

`text2speech` converts long documents into audiobooks.

Supported input:
- PDF (`.pdf`)
- text (`.txt`)

Main output:
- merged audiobook MP3
- optional chaptered M4B (with metadata and cover)

Pipeline idea:

1. read source
2. extract text (OCR only when needed)
3. clean text for listening
4. split into chunks
5. synthesize with Google TTS
6. merge/export audio files
7. write run report with analytics

## Chapter 2: Project Folder Model

The repo uses a simple three-folder model:

- `input/`: put your source books here
- `work/`: all temporary and internal pipeline files
- `out/`: final audio files

Important subfolders under `work/`:

- `work/tmp/<job>/`: job-specific temp files and manifests
- `work/cache/`: chunk cache for reuse
- `work/reports/`: JSON reports and analytics

Keep this mental model:
- `input` = source
- `work` = process
- `out` = result

## Chapter 3: First-Time Setup

### Step 1: create virtual environment and install CLI

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install .
```

### Step 2: install required system tools (macOS)

```bash
brew install poppler ocrmypdf tesseract ffmpeg
```

Why these tools:
- `pdftotext` from Poppler: fast text extraction
- `ocrmypdf` + `tesseract`: OCR fallback for scanned pages
- `ffmpeg`: final merge and M4B export

### Step 3: configure Google credentials

Use your preferred Google Cloud authentication flow.
Common setup:
- set `GOOGLE_APPLICATION_CREDENTIALS` to your service account JSON path

## Chapter 4: Your First End-To-End Run

Put one file in `input/`, then run:

```bash
t2s
```

What happens:
- pipeline finds supported files in `input/`
- creates a job folder in `work/tmp/`
- runs extract -> clean -> tts -> merge
- writes final files to `out/`

If you want one specific input file:

```bash
t2s --input input/my-book.pdf
```

## Chapter 5: Pick Audio Style Quickly

Use voice profiles as presets:

- `academic`: clean and stable narration
- `novel`: smoother storytelling tone
- `scan-heavy`: safer pronunciation defaults for messy OCR text

Example:

```bash
t2s --voice-profile novel
```

If needed, override fine-grained parameters:

```bash
t2s --lang hu-HU --voice-name hu-HU-Chirp3-HD-Erinome --speaking-rate 1.0 --pitch 0.0
```

## Chapter 6: Pronunciation And Acronyms

For names and specialized terms, use a pronunciation dictionary.

Example dictionary file:

```toml
[aliases]
"ChatGPT" = "Chat G P T"
"NLP" = "N L P"
```

Run with:

```bash
t2s --pronunciation-file pronunciation.toml
```

Acronym behavior:

```bash
t2s --acronym-mode auto
t2s --acronym-include API,PDF,LLM
t2s --acronym-exclude CHAPTER
```

Rule of thumb:
- `auto`: good default
- `list`: strict include-list control
- `off`: disable acronym shaping

## Chapter 7: OCR Strategy That Works

Default OCR mode is `auto`, which is usually correct:

```bash
t2s --ocr auto
```

Use:
- `always` for scanned documents that fail direct extraction
- `never` for born-digital PDFs with clean text

If auto mode picks wrong pages, tune thresholds:

```bash
t2s --ocr-page-min-chars 30 --ocr-page-min-score 0.24 --ocr-auto-bad-page-ratio 0.10
```

## Chapter 8: Cost And Safety Controls

Google TTS cost is character-based.
Set hard safety gates before large runs.

```bash
t2s --max-chars-per-run 500000
t2s --max-cost-usd 10
t2s --confirm-before-spend
```

Behavior:
- run stops before paid synthesis if estimate is above your limits
- confirmation prompt appears when enabled

## Chapter 9: Better UX With Rich Dashboard

Use full-screen style progress:

```bash
t2s --ui rich --dashboard
```

Runtime keys:
- `p`: pause/resume
- `r`: queue retry pass for failed work
- `q`: cancel run

For plain terminal mode:

```bash
t2s --ui plain
```

## Chapter 10: Output Polish (MP3 + M4B)

Enable M4B export:

```bash
t2s --export-m4b
```

Add metadata and cover:

```bash
t2s --book-title "My Book" --book-author "Author Name" --cover-image /path/to/cover.jpg
```

Clean chapter titles in export:

```bash
t2s --chapter-title-cleanup
```

End result:
- merged `.mp3`
- chaptered `.m4b` with title/author/cover and better chapter names

## Chapter 11: Resume, Watch, And Test Mode

Resume interrupted jobs:

```bash
t2s --resume
```

Watch `input/` continuously:

```bash
t2s --watch
```

Run a fake pipeline (no Google spend):

```bash
t2s --test-run --ui rich
```

Use fake mode when:
- debugging workflow
- demonstrating UI/progress
- testing retry/queue behavior

## Chapter 12: Reports And Analytics

After each run, reports are written to:

- `work/reports/run-YYYYMMDD-HHMMSS-ffffff.json`
- `work/reports/latest_run.json`

Analytics includes:
- chars and billable chars
- estimated cost
- TTS duration and total duration
- failures
- cache hit rate

These reports are useful for:
- budget tracking
- throughput tuning
- spotting unstable documents/jobs

## Chapter 13: When To Use `--input-kind clean-txt`

Use `t2s --input-kind clean-txt` when your text is already ready.
It tells the pipeline to skip cleanup for clean TXT input.

Example:

```bash
t2s --input /path/to/book.txt --input-kind clean-txt --ui rich --dashboard
```

Best for:
- already cleaned `.txt` workflows
- isolated TTS experimentation
- fast reruns of text-only input

## Chapter 14: Troubleshooting Checklist

1. Command not found:
- reactivate `.venv`
- run `pip install .` again

2. Dependency errors:
- run `t2s --doctor-only`
- run with `--deps-check` enabled

3. OCR quality bad:
- try `--ocr always`
- adjust OCR threshold flags

4. Pronunciation wrong:
- add entry in pronunciation dictionary
- check acronym mode include/exclude

5. Cost estimate too high:
- lower `--max-chars-per-run`
- set `--max-cost-usd`
- inspect cleanup output and chapter splits

6. Failures on long jobs:
- reduce `--workers`
- lower `--rate-limit-rps`
- increase retry settings

## Chapter 15: Daily Practical Workflow

Recommended routine:

1. drop PDFs/TXTs into `input/`
2. run `t2s --ui rich --dashboard`
3. inspect results in `out/`
4. review `work/reports/latest_run.json`
5. if needed, tune profile/pronunciation and rerun

If you only remember one command, remember this:

```bash
t2s
```

Then refine with options from `docs/COMMANDS.md`.
