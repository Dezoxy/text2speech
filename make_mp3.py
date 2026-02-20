import re
import random
import time
from pathlib import Path
from google.cloud import texttospeech

try:
    from google.api_core import exceptions as gexc
except Exception:  # pragma: no cover - fallback if api_core import shape changes
    gexc = None

# ---- Config (edit if needed) ----
PDF_TXT = Path("book.txt")
OUT_DIR = Path("out_mp3")
LANG = "hu-HU"
VOICE_NAME = "hu-HU-Chirp3-HD-Erinome"

# Keep chunks comfortably under typical API input limits.
# If you see API errors about size, reduce this to e.g. 3500.
MAX_CHARS = 3500
RETRY_ATTEMPTS = 6
MAX_SPLIT_DEPTH = 12


def is_invalid_argument_error(exc: Exception) -> bool:
    if gexc and isinstance(exc, gexc.InvalidArgument):
        return True
    return "invalid argument" in str(exc).lower()


def is_retryable_error(exc: Exception) -> bool:
    if gexc and isinstance(
        exc,
        (
            gexc.DeadlineExceeded,
            gexc.ServiceUnavailable,
            gexc.ResourceExhausted,
            gexc.InternalServerError,
            gexc.Aborted,
            gexc.Unknown,
            gexc.TooManyRequests,
        ),
    ):
        return True

    msg = str(exc).lower()
    retry_markers = (
        "deadline exceeded",
        "service unavailable",
        "resource exhausted",
        "internal",
        "temporar",
        "timeout",
        "too many requests",
        "429",
        "503",
    )
    return any(marker in msg for marker in retry_markers)


def split_for_retry(chunk: str) -> list[str]:
    """
    Split a problematic chunk into two smaller pieces near its midpoint.
    Prefer paragraph or sentence boundaries; fallback to whitespace.
    """
    n = len(chunk)
    if n < 400:
        return [chunk]

    mid = n // 2

    # 1) Try paragraph boundary near midpoint
    pcut = chunk.rfind("\n\n", max(0, mid - 800), min(n, mid + 800))
    if pcut != -1:
        left = chunk[:pcut].strip()
        right = chunk[pcut + 2 :].strip()
        if left and right:
            return [left, right]

    # 2) Try sentence boundary near midpoint
    best = -1
    for m in re.finditer(r"[.!?…]\s+", chunk):
        if abs(m.end() - mid) < 800:
            best = m.end()
    if best != -1:
        left = chunk[:best].strip()
        right = chunk[best:].strip()
        if left and right:
            return [left, right]

    # 3) Fallback: split at whitespace near midpoint
    cut = chunk.rfind(" ", 0, mid)
    if cut < int(n * 0.3):
        cut = chunk.find(" ", mid)
    if cut == -1:
        return [chunk]

    left = chunk[:cut].strip()
    right = chunk[cut:].strip()
    if left and right:
        return [left, right]
    return [chunk]


def synthesize_with_retries(
    client: texttospeech.TextToSpeechClient,
    text: str,
    voice: texttospeech.VoiceSelectionParams,
    audio_config: texttospeech.AudioConfig,
) -> bytes:
    last_exc: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            synthesis_input = texttospeech.SynthesisInput(text=text)
            resp = client.synthesize_speech(
                input=synthesis_input,
                voice=voice,
                audio_config=audio_config,
            )
            if not resp.audio_content:
                raise RuntimeError("Empty audio response from TTS API.")
            return resp.audio_content
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if is_invalid_argument_error(exc):
                raise
            if attempt >= RETRY_ATTEMPTS or not is_retryable_error(exc):
                raise

            sleep_s = min(30.0, (1.8 ** attempt) + random.uniform(0.0, 0.25))
            print(
                f"Retry {attempt}/{RETRY_ATTEMPTS} after error: {exc}. "
                f"Sleeping {sleep_s:.1f}s."
            )
            time.sleep(sleep_s)

    assert last_exc is not None
    raise last_exc


def synthesize_with_adaptive_split(
    client: texttospeech.TextToSpeechClient,
    text: str,
    voice: texttospeech.VoiceSelectionParams,
    audio_config: texttospeech.AudioConfig,
    depth: int = 0,
) -> bytes:
    """
    Try normal synthesis with retries; if input is still invalid, split recursively
    and concatenate child MP3 byte streams so caller still gets a single file.
    """
    try:
        return synthesize_with_retries(client, text, voice, audio_config)
    except Exception as exc:  # noqa: BLE001
        if not is_invalid_argument_error(exc):
            raise

        if depth >= MAX_SPLIT_DEPTH:
            raise RuntimeError(
                f"Max split depth reached ({MAX_SPLIT_DEPTH}) for chunk length {len(text)}"
            ) from exc

        pieces = split_for_retry(text)
        if len(pieces) <= 1:
            raise

        left = synthesize_with_adaptive_split(
            client, pieces[0], voice, audio_config, depth=depth + 1
        )
        right = synthesize_with_adaptive_split(
            client, pieces[1], voice, audio_config, depth=depth + 1
        )
        return left + right

def break_long_runs(t: str, max_run: int = 420) -> str:
    """
    Google Chirp3-HD rejects very long 'sentences' (runs without sentence-ending punctuation).
    This inserts sentence breaks into long runs while trying to keep natural Hungarian phrasing.
    """
    # Define sentence-ending punctuation (keep it simple + robust)
    enders = ".!?…"

    out = []
    run = ""

    def flush_run(r: str):
        # If the run is short enough, keep it as-is.
        if len(r) <= max_run:
            return r

        # First pass: add sentence ends after strong separators if the run is huge.
        # We prefer breaking after: ; : , ) ] } — followed by whitespace
        r2 = re.sub(r"([;:,)\]\}])(\s+)", r"\1. \2", r)

        # Second pass: if still too long, break every ~max_run at whitespace.
        if len(r2) <= max_run:
            return r2

        parts = []
        s = r2
        while len(s) > max_run:
            cut = s.rfind(" ", 0, max_run)
            if cut < int(max_run * 0.6):
                cut = max_run  # fallback: hard cut
            parts.append(s[:cut].rstrip() + ".")
            s = s[cut:].lstrip()
        if s:
            parts.append(s)
        return " ".join(parts)

    for ch in t:
        run += ch
        if ch in enders:
            out.append(flush_run(run))
            run = ""
        elif ch == "\n":
            # Treat paragraph breaks as safe boundaries too
            out.append(flush_run(run))
            run = ""

    if run:
        out.append(flush_run(run))

    return "".join(out)

def clean_text(t: str) -> str:
    t = t.replace("\u00ad", "")                  # soft hyphen
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)       # fix hyphenation across lines
    t = re.sub(r"(?<!\n)\n(?!\n)", " ", t)       # single newlines -> spaces
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = t.strip()

    # Critical: prevent "sentence too long" errors
    t = break_long_runs(t, max_run=420)

    return t

def chunk_by_paragraph(t: str, max_chars: int):
    paras = [p.strip() for p in t.split("\n\n") if p.strip()]
    chunks = []
    buf = []
    size = 0

    for p in paras:
        # If a single paragraph is huge, hard-split it.
        if len(p) > max_chars:
            if buf:
                chunks.append("\n\n".join(buf))
                buf, size = [], 0
            for i in range(0, len(p), max_chars):
                chunks.append(p[i:i+max_chars])
            continue

        add = len(p) + (2 if buf else 0)
        if size + add <= max_chars:
            buf.append(p)
            size += add
        else:
            chunks.append("\n\n".join(buf))
            buf = [p]
            size = len(p)

    if buf:
        chunks.append("\n\n".join(buf))
    return chunks

def main():
    if not PDF_TXT.exists():
        raise SystemExit(f"Missing {PDF_TXT}. Run pdftotext first.")

    OUT_DIR.mkdir(exist_ok=True)

    raw = PDF_TXT.read_text(errors="ignore")
    text = clean_text(raw)
    chunks = chunk_by_paragraph(text, MAX_CHARS)

    print(f"Total cleaned chars: {len(text):,}")
    print(f"Chunks to synthesize: {len(chunks):,}")
    print(f"Output folder: {OUT_DIR.resolve()}")

    client = texttospeech.TextToSpeechClient()

    voice = texttospeech.VoiceSelectionParams(
        language_code=LANG,
        name=VOICE_NAME,
    )

    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        # You can tweak these if you want:
        speaking_rate=1.0,
        pitch=0.0,
    )

    failures = []
    generated = 0
    skipped = 0

    for idx, chunk in enumerate(chunks):
        stem = f"{idx:04d}"
        out_file = OUT_DIR / f"{stem}.mp3"
        if out_file.exists() and out_file.stat().st_size > 0:
            skipped += 1
            continue

        try:
            audio = synthesize_with_adaptive_split(
                client, chunk, voice, audio_config, depth=0
            )
        except Exception as exc:  # noqa: BLE001
            failures.append((stem, str(exc)))
            print(f"Failed {stem}: {exc}")
            continue

        tmp_file = out_file.with_suffix(".mp3.tmp")
        tmp_file.write_bytes(audio)
        tmp_file.replace(out_file)
        generated += 1

        if generated % 25 == 0:
            print(f"Generated {generated}/{len(chunks)} files. Last: {out_file.name}")

    print(f"Done. Generated: {generated}, skipped: {skipped}, failures: {len(failures)}.")
    if failures:
        for stem, err in failures[:10]:
            print(f" - {stem}: {err}")
        raise SystemExit(1)

if __name__ == "__main__":
    main()
