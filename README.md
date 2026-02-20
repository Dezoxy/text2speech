# text2speech

Automate book conversion from PDF/TXT to audiobook MP3 using Google Cloud Text-to-Speech.

## Folder Layout

- `input/`: source files (`.pdf` or `.txt`)
- `work/tmp/`: temporary processing artifacts (raw text, cleaned text, chunks, `job_manifest.json`)
- `out/`: finished merged audiobook `.mp3` files

## Configuration

Defaults are loaded from `config.toml`.

Main sections:

- `[paths]`: `input`, `work_dir`, `out_dir`
- `[pipeline]`: `ocr`, `ocr_lang`, `lang`, `voice_name`, `max_chars`, `speaking_rate`, `pitch`, `ui`
- `[flags]`: default booleans for skip/test/estimate modes

Use another config file:

```bash
python3 pipeline.py --config /path/to/config.toml
```

CLI args always override config values.

## What This Repo Contains

- `pipeline.py`: End-to-end flow (`input -> OCR/extract -> cleanup -> TTS -> out/*.mp3`).
- `make_mp3.py`: Direct TTS chunk generator for already prepared `.txt` files.
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

This processes all supported files in `input/` (`.pdf`, `.txt`), stores tmp artifacts in `work/tmp/`, and writes final merged output to `out/`.

Useful options:

- `--input /path/to/file-or-folder`
- `--work-dir work`
- `--out-dir out`
- `--ocr auto|always|never`
- `--ocr-lang hun+eng`
- `--skip-tts` / `--no-skip-tts`
- `--skip-merge` / `--no-skip-merge`
- `--ui auto|plain|rich`
- `--estimate-only` / `--no-estimate-only`
- `--test-run` / `--no-test-run`
- `--test-delay 0.02`
- `--test-write-placeholders` / `--no-test-write-placeholders`
- `--doctor` / `--doctor-only`
- `--watch --watch-interval 5 --watch-stable-seconds 3`

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
