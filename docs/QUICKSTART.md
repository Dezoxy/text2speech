# Quickstart

Fastest path from zero to first audiobook.

## 1) Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install .
brew install poppler ocrmypdf tesseract ffmpeg
```

## 2) Configure Google Auth

Set your Google Cloud credentials (example):

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/absolute/path/to/service-account.json"
```

## 3) Add Input Files

Put your files into:

- `input/` (`.pdf` or `.txt`)

## 4) Run The Pipeline

Default run:

```bash
t2s
```

With rich dashboard:

```bash
t2s --ui rich --dashboard
```

## 5) Find Outputs

- final audio files: `out/`
- temp/process files: `work/tmp/`
- latest report: `work/reports/latest_run.json`

## 6) Common Useful Options

Voice profile:

```bash
t2s --voice-profile academic
t2s --voice-profile novel
t2s --voice-profile scan-heavy
```

OCR mode:

```bash
t2s --ocr auto
t2s --ocr always
t2s --ocr never
```

Budget safety:

```bash
t2s --max-chars-per-run 500000 --max-cost-usd 10 --confirm-before-spend
```

Pronunciation dictionary:

```bash
t2s --pronunciation-file pronunciation.toml
```

M4B export metadata:

```bash
t2s --export-m4b --book-title "Book" --book-author "Author" --cover-image /path/to/cover.jpg
```

## 7) No-Cost Test Run

Simulate full flow without Google spend:

```bash
t2s --test-run --ui rich
```

## 8) Resume Interrupted Work

```bash
t2s --resume
```

## 9) Troubleshooting

Environment check:

```bash
t2s --doctor-only
```

Help:

```bash
t2s --help
python3 launcher.py --help
```

## 10) Next Docs

- Full handbook: `docs/PROJECT_BOOK.md`
- Hungarian handbook: `docs/PROJECT_BOOK.hu.md`
- Detailed command guide: `docs/COMMANDS.md`
