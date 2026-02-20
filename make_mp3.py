from __future__ import annotations

import argparse
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# ---- Defaults (override via CLI args) ----
PDF_TXT = Path("book.txt")
OUT_DIR = Path("out_mp3")
LANG = "hu-HU"
VOICE_NAME = "hu-HU-Chirp3-HD-Erinome"

# Keep chunks comfortably under typical API input limits.
# If you see API errors about size, reduce this to e.g. 3500.
MAX_CHARS = 3500
RETRY_ATTEMPTS = 6
MAX_SPLIT_DEPTH = 12

PRICING_SOURCE_URL = "https://cloud.google.com/text-to-speech/pricing"
PRICING_LAST_VERIFIED = "2026-02-20"

# Lazily loaded from google.api_core.exceptions
gexc: Any | None = None


@dataclass(frozen=True)
class VoicePricing:
    key: str
    label: str
    usd_per_char: float | None


@dataclass(frozen=True)
class BillingPreview:
    project_id: str
    month_key: str
    voice_name: str
    voice_pricing: VoicePricing
    total_chars: int
    pending_chars: int
    skipped_chars: int
    estimated_cost_usd: float | None


VOICE_PRICING_TABLE: dict[str, VoicePricing] = {
    # Prices from Google Cloud pricing page.
    # Verified on 2026-02-20.
    "chirp3_hd": VoicePricing(
        key="chirp3_hd",
        label="Chirp 3: HD",
        usd_per_char=30.0 / 1_000_000,
    ),
    "chirp3_instant_custom": VoicePricing(
        key="chirp3_instant_custom",
        label="Chirp 3: Instant Custom Voice",
        usd_per_char=60.0 / 1_000_000,
    ),
    "wavenet": VoicePricing(
        key="wavenet",
        label="WaveNet",
        usd_per_char=4.0 / 1_000_000,
    ),
    "standard": VoicePricing(
        key="standard",
        label="Standard",
        usd_per_char=4.0 / 1_000_000,
    ),
    "neural2": VoicePricing(
        key="neural2",
        label="Neural2",
        usd_per_char=16.0 / 1_000_000,
    ),
    "polyglot": VoicePricing(
        key="polyglot",
        label="Polyglot",
        usd_per_char=16.0 / 1_000_000,
    ),
    "studio": VoicePricing(
        key="studio",
        label="Studio",
        usd_per_char=160.0 / 1_000_000,
    ),
    "unknown": VoicePricing(
        key="unknown",
        label="Unknown voice family",
        usd_per_char=None,
    ),
}


def infer_voice_pricing(voice_name: str) -> VoicePricing:
    lower = voice_name.lower()

    if "instant" in lower and "chirp" in lower:
        return VOICE_PRICING_TABLE["chirp3_instant_custom"]
    if "chirp" in lower:
        return VOICE_PRICING_TABLE["chirp3_hd"]
    if "neural2" in lower:
        return VOICE_PRICING_TABLE["neural2"]
    if "polyglot" in lower:
        return VOICE_PRICING_TABLE["polyglot"]
    if "studio" in lower:
        return VOICE_PRICING_TABLE["studio"]
    if "wavenet" in lower or "wave-net" in lower:
        return VOICE_PRICING_TABLE["wavenet"]
    if "standard" in lower:
        return VOICE_PRICING_TABLE["standard"]

    return VOICE_PRICING_TABLE["unknown"]


def current_month_key() -> str:
    return datetime.now().strftime("%Y-%m")


def detect_project_id() -> str:
    for env_name in ("GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT", "GCP_PROJECT"):
        value = os.getenv(env_name)
        if value:
            return value
    return "unknown-project"


def format_usd(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:,.4f}"


def build_billing_preview(
    *,
    voice_name: str,
    chunks: list[str],
    out_dir: Path,
) -> BillingPreview:
    project_id = detect_project_id()
    month_key = current_month_key()
    pricing = infer_voice_pricing(voice_name)

    total_chars = sum(len(chunk) for chunk in chunks)
    pending_chars = 0
    skipped_chars = 0

    for idx, chunk in enumerate(chunks):
        out_file = out_dir / f"{idx:04d}.mp3"
        if out_file.exists() and out_file.stat().st_size > 0:
            skipped_chars += len(chunk)
        else:
            pending_chars += len(chunk)

    if pricing.usd_per_char is None:
        estimated_cost_usd = None
    else:
        estimated_cost_usd = pending_chars * pricing.usd_per_char

    return BillingPreview(
        project_id=project_id,
        month_key=month_key,
        voice_name=voice_name,
        voice_pricing=pricing,
        total_chars=total_chars,
        pending_chars=pending_chars,
        skipped_chars=skipped_chars,
        estimated_cost_usd=estimated_cost_usd,
    )


def render_billing_preview(preview: BillingPreview, ui_mode: str) -> None:
    use_rich = should_use_rich_ui(ui_mode)

    unit_label = "characters"

    if use_rich:
        console, Table, Panel, box = rich_components()
        table = Table(show_header=False, box=box.ROUNDED)
        table.add_row("Project", preview.project_id)
        table.add_row("Month", preview.month_key)
        table.add_row("Voice", f"{preview.voice_name} ({preview.voice_pricing.label})")
        table.add_row("Billing Unit", unit_label)
        table.add_row("Pending This Run", f"{preview.pending_chars:,} chars")
        table.add_row("Already Done", f"{preview.skipped_chars:,} chars")
        table.add_row("Total In Input", f"{preview.total_chars:,} chars")
        table.add_row("Estimated Billed Characters", f"{preview.pending_chars:,} chars")
        table.add_row("Estimated Cost", format_usd(preview.estimated_cost_usd))

        table.add_row("Pricing Source", PRICING_SOURCE_URL)
        table.add_row("Pricing Verified", PRICING_LAST_VERIFIED)

        console.print(Panel(table, title="TTS Budget Preview", expand=False))
        return

    print("\n=== TTS Budget Preview ===")
    print(f"Project: {preview.project_id}")
    print(f"Month: {preview.month_key}")
    print(f"Voice: {preview.voice_name} ({preview.voice_pricing.label})")
    print(f"Pending this run: {preview.pending_chars:,} chars")
    print(f"Already done: {preview.skipped_chars:,} chars")
    print(f"Total in input: {preview.total_chars:,} chars")
    print(f"Estimated billed characters: {preview.pending_chars:,} chars")
    print(f"Estimated cost: {format_usd(preview.estimated_cost_usd)}")

    print(f"Pricing source: {PRICING_SOURCE_URL}")
    print(f"Pricing verified: {PRICING_LAST_VERIFIED}")


def render_billing_postrun(
    *,
    preview: BillingPreview,
    generated_chars: int,
    run_billable_chars: int,
    run_estimated_cost_usd: float | None,
    ui_mode: str,
) -> None:
    use_rich = should_use_rich_ui(ui_mode)

    if use_rich:
        console, Table, Panel, box = rich_components()
        table = Table(show_header=False, box=box.ROUNDED)
        table.add_row("Generated This Run", f"{generated_chars:,} chars")
        table.add_row("Billed This Run", f"{run_billable_chars:,} chars")
        table.add_row("Estimated Cost This Run", format_usd(run_estimated_cost_usd))
        console.print(Panel(table, title="TTS Usage Summary", expand=False))
        return

    print("\n=== TTS Usage Summary ===")
    print(f"Generated this run: {generated_chars:,} chars")
    print(f"Billed this run: {run_billable_chars:,} chars")
    print(f"Estimated cost this run: {format_usd(run_estimated_cost_usd)}")


def should_use_rich_ui(mode: str) -> bool:
    if mode == "plain":
        return False

    if mode == "auto" and not sys.stdout.isatty():
        return False

    try:
        import rich  # noqa: F401
    except Exception:  # noqa: BLE001
        if mode == "rich":
            raise RuntimeError(
                "UI mode 'rich' requested but package 'rich' is not installed. "
                "Install with: pip install rich"
            )
        return False

    return True


def rich_components() -> tuple[Any, Any, Any, Any]:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    return Console(), Table, Panel, box


def load_google_tts() -> Any:
    """Load Google TTS modules lazily so non-TTS steps can still run."""
    global gexc

    try:
        from google.cloud import texttospeech
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Missing dependency: google-cloud-texttospeech. "
            "Install with: pip install google-cloud-texttospeech"
        ) from exc

    try:
        from google.api_core import exceptions as api_exceptions

        gexc = api_exceptions
    except Exception:  # pragma: no cover - import shape can vary
        gexc = None

    return texttospeech


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
    client: Any,
    texttospeech: Any,
    text: str,
    voice: Any,
    audio_config: Any,
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

            sleep_s = min(30.0, (1.8**attempt) + random.uniform(0.0, 0.25))
            print(
                f"Retry {attempt}/{RETRY_ATTEMPTS} after error: {exc}. "
                f"Sleeping {sleep_s:.1f}s."
            )
            time.sleep(sleep_s)

    assert last_exc is not None
    raise last_exc


def synthesize_with_adaptive_split(
    client: Any,
    texttospeech: Any,
    text: str,
    voice: Any,
    audio_config: Any,
    depth: int = 0,
) -> bytes:
    """
    Try normal synthesis with retries; if input is still invalid, split recursively
    and concatenate child MP3 byte streams so caller still gets a single file.
    """
    try:
        return synthesize_with_retries(client, texttospeech, text, voice, audio_config)
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
            client, texttospeech, pieces[0], voice, audio_config, depth=depth + 1
        )
        right = synthesize_with_adaptive_split(
            client, texttospeech, pieces[1], voice, audio_config, depth=depth + 1
        )
        return left + right


def break_long_runs(t: str, max_run: int = 420) -> str:
    """
    Google Chirp3-HD rejects very long 'sentences' (runs without sentence-ending punctuation).
    This inserts sentence breaks into long runs while trying to keep natural Hungarian phrasing.
    """
    enders = ".!?…"

    out = []
    run = ""

    def flush_run(r: str) -> str:
        if len(r) <= max_run:
            return r

        # Prefer adding sentence boundaries at stronger punctuation first.
        r2 = re.sub(r"([;:,)\]\}])(\s+)", r"\1. \2", r)

        if len(r2) <= max_run:
            return r2

        parts = []
        s = r2
        while len(s) > max_run:
            cut = s.rfind(" ", 0, max_run)
            if cut < int(max_run * 0.6):
                cut = max_run
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
            # Treat paragraph breaks as safe boundaries too.
            out.append(flush_run(run))
            run = ""

    if run:
        out.append(flush_run(run))

    return "".join(out)


def clean_text(t: str) -> str:
    t = t.replace("\u00ad", "")  # soft hyphen
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)  # fix hyphenation across lines
    t = re.sub(r"(?<!\n)\n(?!\n)", " ", t)  # single newlines -> spaces
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = t.strip()

    # Critical: prevent "sentence too long" errors.
    t = break_long_runs(t, max_run=420)

    return t


def chunk_by_paragraph(t: str, max_chars: int) -> list[str]:
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
                chunks.append(p[i : i + max_chars])
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


def run_tts(
    input_txt: Path,
    out_dir: Path,
    lang: str,
    voice_name: str,
    max_chars: int,
    speaking_rate: float,
    pitch: float,
    write_cleaned: Path | None = None,
    ui_mode: str = "auto",
    estimate_only: bool = False,
    test_run: bool = False,
    test_delay: float = 0.02,
    test_write_placeholders: bool = False,
) -> dict[str, int | float | str | bool]:
    if not input_txt.exists():
        raise RuntimeError(f"Missing {input_txt}. Provide a valid text file.")

    out_dir.mkdir(exist_ok=True, parents=True)

    raw = input_txt.read_text(errors="ignore")
    text = clean_text(raw)
    chunks = chunk_by_paragraph(text, max_chars)

    if write_cleaned is not None:
        write_cleaned.parent.mkdir(parents=True, exist_ok=True)
        write_cleaned.write_text(text)

    print(f"Input file: {input_txt}")
    print(f"Total cleaned chars: {len(text):,}")
    print(f"Chunks to synthesize: {len(chunks):,}")
    print(f"Output folder: {out_dir.resolve()}")

    preview = build_billing_preview(
        voice_name=voice_name,
        chunks=chunks,
        out_dir=out_dir,
    )
    render_billing_preview(preview, ui_mode=ui_mode)

    if estimate_only:
        return {
            "estimate_only": True,
            "test_run": test_run,
            "generated": 0,
            "skipped": 0,
            "failures": 0,
            "chunks": len(chunks),
            "generated_chars": 0,
            "estimated_pending_chars": preview.pending_chars,
            "estimated_billable_chars": preview.pending_chars,
            "estimated_cost_usd": float(preview.estimated_cost_usd or 0.0),
        }

    if preview.pending_chars == 0:
        print("All chunk files already exist. Nothing new to synthesize.")
        render_billing_postrun(
            preview=preview,
            generated_chars=0,
            run_billable_chars=0,
            run_estimated_cost_usd=0.0,
            ui_mode=ui_mode,
        )
        return {
            "estimate_only": False,
            "generated": 0,
            "skipped": len(chunks),
            "failures": 0,
            "chunks": len(chunks),
            "generated_chars": 0,
            "run_billable_chars": 0,
            "run_estimated_cost_usd": 0.0,
        }

    if test_run:
        if test_delay < 0:
            raise RuntimeError("--test-delay must be >= 0")

        generated = 0
        skipped = 0
        generated_chars = 0

        use_rich = should_use_rich_ui(ui_mode)
        if use_rich:
            from rich.progress import (
                BarColumn,
                MofNCompleteColumn,
                Progress,
                TextColumn,
                TimeRemainingColumn,
            )

            with Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeRemainingColumn(),
            ) as progress:
                task = progress.add_task("Simulating", total=len(chunks))
                for idx, chunk in enumerate(chunks):
                    out_file = out_dir / f"{idx:04d}.mp3"
                    if out_file.exists() and out_file.stat().st_size > 0:
                        skipped += 1
                        progress.advance(task)
                        continue

                    if test_delay > 0:
                        time.sleep(test_delay)
                    if test_write_placeholders:
                        out_file.write_bytes(b"FAKE_MP3_PLACEHOLDER")
                    generated += 1
                    generated_chars += len(chunk)
                    progress.advance(task)
        else:
            for idx, chunk in enumerate(chunks):
                out_file = out_dir / f"{idx:04d}.mp3"
                if out_file.exists() and out_file.stat().st_size > 0:
                    skipped += 1
                    continue

                if test_delay > 0:
                    time.sleep(test_delay)
                if test_write_placeholders:
                    out_file.write_bytes(b"FAKE_MP3_PLACEHOLDER")
                generated += 1
                generated_chars += len(chunk)

                if generated % 25 == 0:
                    print(f"Simulated {generated}/{len(chunks)} files. Last: {out_file.name}")

        print(f"Done (test run). Simulated: {generated}, skipped: {skipped}, failures: 0.")
        if preview.voice_pricing.usd_per_char is None:
            run_estimated_cost_usd = None
        else:
            run_estimated_cost_usd = generated_chars * preview.voice_pricing.usd_per_char
        render_billing_postrun(
            preview=preview,
            generated_chars=generated_chars,
            run_billable_chars=generated_chars,
            run_estimated_cost_usd=run_estimated_cost_usd,
            ui_mode=ui_mode,
        )
        print("Test run mode enabled: no Google TTS requests were sent.")
        return {
            "estimate_only": False,
            "test_run": True,
            "generated": generated,
            "skipped": skipped,
            "failures": 0,
            "chunks": len(chunks),
            "generated_chars": generated_chars,
            "run_billable_chars": generated_chars,
            "run_estimated_cost_usd": float(run_estimated_cost_usd or 0.0),
        }

    texttospeech = load_google_tts()
    client = texttospeech.TextToSpeechClient()

    voice = texttospeech.VoiceSelectionParams(
        language_code=lang,
        name=voice_name,
    )

    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        speaking_rate=speaking_rate,
        pitch=pitch,
    )

    failures: list[tuple[str, str]] = []
    generated = 0
    skipped = 0
    generated_chars = 0

    use_rich = should_use_rich_ui(ui_mode)

    if use_rich:
        from rich.progress import (
            BarColumn,
            MofNCompleteColumn,
            Progress,
            TextColumn,
            TimeRemainingColumn,
        )

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeRemainingColumn(),
        ) as progress:
            task = progress.add_task("Synthesizing", total=len(chunks))

            for idx, chunk in enumerate(chunks):
                stem = f"{idx:04d}"
                out_file = out_dir / f"{stem}.mp3"
                if out_file.exists() and out_file.stat().st_size > 0:
                    skipped += 1
                    progress.advance(task)
                    continue

                try:
                    audio = synthesize_with_adaptive_split(
                        client, texttospeech, chunk, voice, audio_config, depth=0
                    )
                except Exception as exc:  # noqa: BLE001
                    failures.append((stem, str(exc)))
                    progress.console.log(f"Failed {stem}: {exc}")
                    progress.advance(task)
                    continue

                tmp_file = out_file.with_suffix(".mp3.tmp")
                tmp_file.write_bytes(audio)
                tmp_file.replace(out_file)
                generated += 1
                generated_chars += len(chunk)
                progress.advance(task)
    else:
        for idx, chunk in enumerate(chunks):
            stem = f"{idx:04d}"
            out_file = out_dir / f"{stem}.mp3"
            if out_file.exists() and out_file.stat().st_size > 0:
                skipped += 1
                continue

            try:
                audio = synthesize_with_adaptive_split(
                    client, texttospeech, chunk, voice, audio_config, depth=0
                )
            except Exception as exc:  # noqa: BLE001
                failures.append((stem, str(exc)))
                print(f"Failed {stem}: {exc}")
                continue

            tmp_file = out_file.with_suffix(".mp3.tmp")
            tmp_file.write_bytes(audio)
            tmp_file.replace(out_file)
            generated += 1
            generated_chars += len(chunk)

            if generated % 25 == 0:
                print(f"Generated {generated}/{len(chunks)} files. Last: {out_file.name}")

    print(f"Done. Generated: {generated}, skipped: {skipped}, failures: {len(failures)}.")

    run_billable_chars = generated_chars

    if preview.voice_pricing.usd_per_char is None:
        run_estimated_cost_usd = None
    else:
        run_estimated_cost_usd = run_billable_chars * preview.voice_pricing.usd_per_char

    render_billing_postrun(
        preview=preview,
        generated_chars=generated_chars,
        run_billable_chars=run_billable_chars,
        run_estimated_cost_usd=run_estimated_cost_usd,
        ui_mode=ui_mode,
    )

    if failures:
        for stem, err in failures[:10]:
            print(f" - {stem}: {err}")
        raise RuntimeError(f"TTS failed for {len(failures)} chunk(s).")

    return {
        "estimate_only": False,
        "test_run": False,
        "generated": generated,
        "skipped": skipped,
        "failures": len(failures),
        "chunks": len(chunks),
        "generated_chars": generated_chars,
        "run_billable_chars": int(run_billable_chars or 0),
        "run_estimated_cost_usd": float(run_estimated_cost_usd or 0.0),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate MP3 chunks from a text file.")
    parser.add_argument(
        "--input",
        type=Path,
        default=PDF_TXT,
        help="Path to source text file (default: book.txt)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUT_DIR,
        help="Directory for chunked mp3 output (default: out_mp3)",
    )
    parser.add_argument(
        "--lang",
        default=LANG,
        help="Voice language code (default: hu-HU)",
    )
    parser.add_argument(
        "--voice-name",
        default=VOICE_NAME,
        help="Google TTS voice name",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=MAX_CHARS,
        help="Max chars per chunk before synthesis",
    )
    parser.add_argument(
        "--speaking-rate",
        type=float,
        default=1.0,
        help="TTS speaking rate",
    )
    parser.add_argument(
        "--pitch",
        type=float,
        default=0.0,
        help="TTS pitch",
    )
    parser.add_argument(
        "--write-cleaned",
        type=Path,
        default=None,
        help="Optional path to persist cleaned text before chunking",
    )
    parser.add_argument(
        "--ui",
        choices=("auto", "plain", "rich"),
        default="auto",
        help="CLI UI mode. 'rich' requires: pip install rich",
    )
    parser.add_argument(
        "--estimate-only",
        action="store_true",
        help="Show character/cost estimate and exit without calling Google TTS",
    )
    parser.add_argument(
        "--test-run",
        action="store_true",
        help="Fake synthesis run with progress; no Google requests are sent",
    )
    parser.add_argument(
        "--test-delay",
        type=float,
        default=0.02,
        help="Seconds to sleep per chunk in --test-run mode",
    )
    parser.add_argument(
        "--test-write-placeholders",
        action="store_true",
        help="In --test-run mode, create placeholder .mp3 files for resume demo",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        run_tts(
            input_txt=args.input,
            out_dir=args.output_dir,
            lang=args.lang,
            voice_name=args.voice_name,
            max_chars=args.max_chars,
            speaking_rate=args.speaking_rate,
            pitch=args.pitch,
            write_cleaned=args.write_cleaned,
            ui_mode=args.ui,
            estimate_only=args.estimate_only,
            test_run=args.test_run,
            test_delay=args.test_delay,
            test_write_placeholders=args.test_write_placeholders,
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
