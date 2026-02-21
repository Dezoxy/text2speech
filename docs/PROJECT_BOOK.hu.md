# text2speech Kis Konyv (HU)

Ez az utmutato lepesrol lepesre vegigvisz a projecten.
A `docs/COMMANDS.md` a parancslista, ez a fajl a teljes kep.

## 1. Fejezet: Mire jo a projekt?

A `text2speech` hosszu dokumentumokbol keszit hangoskonyvet.

Tamogatott bemenet:
- PDF (`.pdf`)
- TXT (`.txt`)

Fo kimenet:
- egyesitett MP3
- opcionisan fejezetes M4B (metadata + boritokep)

Pipeline roviden:

1. forras olvasasa
2. szovegkinyeres (OCR csak ha szukseges)
3. tisztitas felolvasashoz
4. darabolas chunkokra
5. Google TTS szintezis
6. audio merge/export
7. riport + analytics mentese

## 2. Fejezet: Mappa modell

Hasznalj 3 fo mappat:

- `input/`: ide teszed a forras fajlokat
- `work/`: ide kerul minden ideiglenes es technikai artefakt
- `out/`: ide kerulnek a kesz audio fajlok

Fontos `work/` almappak:

- `work/tmp/<job>/`: job allapot, ideiglenes fajlok, manifest
- `work/cache/`: chunk cache
- `work/reports/`: futasi riportok

Egyszeru szabaly:
- `input` = forras
- `work` = folyamat
- `out` = eredmeny

## 3. Fejezet: Elso telepites

### 1) Python virtualis kornyezet + CLI telepites

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install .
```

### 2) Rendszerfuggo eszkozok (macOS)

```bash
brew install poppler ocrmypdf tesseract ffmpeg
```

Mi kell mihez:
- `pdftotext`: gyors PDF szovegkinyeres
- `ocrmypdf` + `tesseract`: OCR fallback
- `ffmpeg`: merge + M4B export

### 3) Google hitelesites beallitasa

Pelda:

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/abszolut/utvonal/service-account.json"
```

## 4. Fejezet: Elso teljes futas

Tegyelj egy fajlt az `input/` mappaba, majd:

```bash
t2s
```

Mi tortenik:
- a pipeline beolvassa a tamogatott forrasokat
- letrehoz job mappat a `work/tmp/` alatt
- lefut: extract -> clean -> tts -> merge
- kiirja az eredmenyt az `out/` mappaba

Egy konkret fajl futtatasa:

```bash
t2s --input input/my-book.pdf
```

## 5. Fejezet: Hang stilus gyors valasztasa

Voice profile presetek:

- `academic`: semleges, stabil narracio
- `novel`: folyamatosabb, tortenetszeru hatas
- `scan-heavy`: zajosabb OCR szovegre konzervativabb

Pelda:

```bash
t2s --voice-profile novel
```

Finomhangolas:

```bash
t2s --lang hu-HU --voice-name hu-HU-Chirp3-HD-Erinome --speaking-rate 1.0 --pitch 0.0
```

## 6. Fejezet: Kiejtes es roviditesek

Kiejtes szotar nevekhez/szakmai kifejezesekhez:

```toml
[aliases]
"ChatGPT" = "Chat G P T"
"NLP" = "N L P"
```

Futtatas:

```bash
t2s --pronunciation-file pronunciation.toml
```

Rovidites (acronym) szabalyok:

```bash
t2s --acronym-mode auto
t2s --acronym-include API,PDF,LLM
t2s --acronym-exclude CHAPTER
```

Gyors szabaly:
- `auto`: altalanos jo alap
- `list`: csak include listaban levok
- `off`: acronym shaping kikapcsolva

## 7. Fejezet: OCR strategia

Alapertelmezett:

```bash
t2s --ocr auto
```

Mikor mit:
- `always`: gyenge/scannelt PDF
- `never`: digitalis PDF jo szoveggel

Ha rossz oldalakat valaszt:

```bash
t2s --ocr-page-min-chars 30 --ocr-page-min-score 0.24 --ocr-auto-bad-page-ratio 0.10
```

## 8. Fejezet: Koltseg es vedelmi limitek

A Google TTS karakter alapon szamol.
Hasznalj hard limiteket nagy futasok elott.

```bash
t2s --max-chars-per-run 500000
t2s --max-cost-usd 10
t2s --confirm-before-spend
```

Eredmeny:
- futas megall, ha becsles tullepi a limitet
- bekapcsolva ker megerositest koltes elott

## 9. Fejezet: Rich dashboard

Teljesebb CLI/TUI elmeny:

```bash
t2s --ui rich --dashboard
```

Billentyuk futas kozben:
- `p`: pause/resume
- `r`: retry pass a hibas elemekre
- `q`: megszakitas

Sima terminal mode:

```bash
t2s --ui plain
```

## 10. Fejezet: Export polish (MP3 + M4B)

M4B export:

```bash
t2s --export-m4b
```

Metadata + boritokep:

```bash
t2s --book-title "Konyv Cime" --book-author "Szerzo" --cover-image /path/to/cover.jpg
```

Fejezetcim tisztitas:

```bash
t2s --chapter-title-cleanup
```

Vegeredmeny:
- egyesitett `.mp3`
- fejezetes `.m4b` metadata-val es jobb fejezetcimekkel

## 11. Fejezet: Resume, watch, test

Megszakadt munka folytatasa:

```bash
t2s --resume
```

Folyamatos figyeles:

```bash
t2s --watch
```

Fake futas koltes nelkul:

```bash
t2s --test-run --ui rich
```

## 12. Fejezet: Riportok es analytics

Futas utan riportok:

- `work/reports/run-YYYYMMDD-HHMMSS-ffffff.json`
- `work/reports/latest_run.json`

Analytics mezok:
- chars + billable chars
- estimated cost
- TTS duration + total duration
- failures
- cache hit rate

## 13. Fejezet: Mikor hasznald az `--input-kind clean-txt` opciot?

Ha mar kesz a tiszta TXT, hasznald ezt:

```bash
t2s --input /path/to/book.txt --input-kind clean-txt --ui rich --dashboard
```

Jo erre:
- mar megtisztitott `.txt` forras
- TTS kiserletezes
- gyors ujrafutas PDF lepesek nelkul

## 14. Fejezet: Hibaelharitas

1. `command not found`
- aktivald ujra: `source .venv/bin/activate`
- telepitsd ujra: `pip install .`

2. Fuggoseg hiba
- `t2s --doctor-only`
- hagyd bekapcsolva a dep prechecket

3. Rossz OCR minoseg
- probald: `--ocr always`
- hangold az OCR threshold flag-eket

4. Rossz kiejtes
- add hozza a szotari aliasokat
- ellenorizd acronym include/exclude listat

5. Tulsagosan magas koltseg
- csokkentsd: `--max-chars-per-run`
- allits be: `--max-cost-usd`
- nezd meg a tisztitas eredmenyet

6. Instabil hosszu futas
- csokkentsd a `--workers` erteket
- csokkentsd a `--rate-limit-rps` erteket
- novelj retry beallitasokat

## 15. Fejezet: Napi workflow

Ajantlott rutin:

1. dobd a fajlokat az `input/` mappaba
2. inditsd: `t2s --ui rich --dashboard`
3. ellenorizd az `out/` eredmenyt
4. olvasd at a `work/reports/latest_run.json` riportot
5. ha kell, finomhangolj es futtasd ujra

Ha csak egy parancsot jegyzel meg:

```bash
t2s
```

Reszletes opciokhoz: `docs/COMMANDS.md`.
