# Commands

Quick command reference for this repository.

## 1) Install

### Python packages

```bash
pip install google-cloud-texttospeech rich
```

### macOS tools (Homebrew)

```bash
brew install poppler ocrmypdf tesseract ffmpeg
```

## 2) Pipeline Defaults

Uses `config.toml` by default:

```bash
python3 pipeline.py
```

Use custom config:

```bash
python3 pipeline.py --config /path/to/config.toml
```

## 3) Folder Layout

- Source files in `input/`
- Processing artifacts in `work/tmp/`
- Finished merged audiobooks in `out/`

## 4) Pipeline Runs

### Process everything in `input/` (default)

```bash
python3 pipeline.py
```

### Process one file

```bash
python3 pipeline.py --input input/book.pdf
python3 pipeline.py --input input/book.txt
```

### OCR control

```bash
python3 pipeline.py --ocr auto
python3 pipeline.py --ocr always
python3 pipeline.py --ocr never
python3 pipeline.py --ocr-lang hun+eng
```

### Output/workflow controls

```bash
python3 pipeline.py --work-dir work --out-dir out
python3 pipeline.py --skip-tts
python3 pipeline.py --no-skip-tts
python3 pipeline.py --skip-merge
python3 pipeline.py --no-skip-merge
python3 pipeline.py --job-name my-book
```

### Voice/tuning

```bash
python3 pipeline.py --lang hu-HU --voice-name hu-HU-Chirp3-HD-Erinome
python3 pipeline.py --max-chars 3500 --speaking-rate 1.0 --pitch 0.0
```

### UI and estimate

```bash
python3 pipeline.py --ui auto
python3 pipeline.py --ui rich
python3 pipeline.py --estimate-only
python3 pipeline.py --no-estimate-only
```

### Fake test workflow (no Google API calls)

```bash
python3 pipeline.py --test-run --ui rich
python3 pipeline.py --no-test-run
python3 pipeline.py --test-run --test-delay 0.005 --ui plain
python3 pipeline.py --test-run --test-write-placeholders
python3 pipeline.py --test-run --no-test-write-placeholders
```

## 5) Resume Jobs

Resume all unfinished jobs:

```bash
python3 pipeline.py --resume
```

Resume one job by folder:

```bash
python3 pipeline.py --resume-job work/tmp/<job-folder>
```

Resume one job by manifest file:

```bash
python3 pipeline.py --resume-job work/tmp/<job-folder>/job_manifest.json
```

## 6) Direct TTS Script (TXT ready)

### Standard run

```bash
python3 make_mp3.py --input /path/to/book.txt --output-dir out_mp3
```

### Estimate only

```bash
python3 make_mp3.py --input /path/to/book.txt --estimate-only --ui rich
```

### Fake test run

```bash
python3 make_mp3.py --input /path/to/book.txt --test-run --ui rich
python3 make_mp3.py --input /path/to/book.txt --test-run --test-delay 0.005
python3 make_mp3.py --input /path/to/book.txt --test-run --test-write-placeholders
```

## 7) Help

```bash
python3 pipeline.py --help
python3 make_mp3.py --help
```

## 8) Doctor (Environment Check)

Run checks and exit:

```bash
python3 pipeline.py --doctor-only
```

Run checks before pipeline work:

```bash
python3 pipeline.py --doctor
```

## 9) Watch Mode (Auto Process Input Folder)

Continuous mode:

```bash
python3 pipeline.py --watch
```

Tune watch behavior:

```bash
python3 pipeline.py --watch --watch-interval 5 --watch-stable-seconds 3
python3 pipeline.py --watch --watch-max-cycles 10 --test-run --test-delay 0 --ui plain
```

## 10) Run Reports

Every `pipeline.py` run writes JSON reports to:

- `work/reports/run-YYYYMMDD-HHMMSS-ffffff.json`
- `work/reports/latest_run.json`
