from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import html
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from text2speech.shared.cli_args import (
    add_cost_control_args,
    add_dependency_args,
    add_pronunciation_args,
    add_retry_args,
    add_ui_args,
)
from text2speech.shared.dependencies import (
    MissingDependency,
    ensure_dependencies_ready as shared_ensure_dependencies_ready,
    find_missing_dependencies as shared_find_missing_dependencies,
    install_missing_dependencies as shared_install_missing_dependencies,
    load_dependencies as shared_load_dependencies,
    summarize_missing_dependencies as shared_summarize_missing_dependencies,
)
from text2speech.shared.parsing import parse_csv_items as shared_parse_csv_items
from text2speech.shared.parsing import parse_string_map as shared_parse_string_map
from text2speech.shared.tts_engine import (
    CacheLayer,
    ChunkPlanner,
    GoogleClientAdapter,
    RetryController,
    SsmlShaper,
    TtsEngine,
)
from text2speech.shared.validators import (
    validate_cost_limit,
    validate_pricing_table,
    validate_retry_settings,
)
from text2speech.version import get_version

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
DEFAULT_DEPENDENCIES_PATH = Path("dependencies.toml")

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



@dataclass(frozen=True)
class ChapterInfo:
    index: int
    title: str
    start_paragraph: int


@dataclass(frozen=True)
class ChunkPlan:
    text: str
    chapter_index: int


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int
    base_delay_s: float
    backoff_factor: float
    max_delay_s: float
    jitter_s: float


@dataclass(frozen=True)
class ChunkCachePlan:
    cache_dir: Path
    enabled: bool
    keys: dict[int, str]
    hit_candidates: set[int]
    hit_candidate_chars: int


class RateLimiter:
    def __init__(self, rate_limit_rps: float | None) -> None:
        if rate_limit_rps is None or rate_limit_rps <= 0:
            self._interval = 0.0
        else:
            self._interval = 1.0 / rate_limit_rps
        self._lock = threading.Lock()
        self._next_time = 0.0

    def acquire(self) -> None:
        if self._interval <= 0:
            return

        wait_s = 0.0
        with self._lock:
            now = time.monotonic()
            if self._next_time > now:
                wait_s = self._next_time - now
            self._next_time = max(self._next_time, now) + self._interval

        if wait_s > 0:
            time.sleep(wait_s)

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


def run_command(cmd: list[str], step: str) -> None:
    try:
        subprocess.run(cmd, check=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"{step} failed with exit code {exc.returncode}: {' '.join(cmd)}") from exc


def parse_string_list(value: Any, field_name: str, source: Path) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RuntimeError(f"{field_name} must be a list of strings in {source}")
    return [item.strip() for item in value if item.strip()]


def parse_string_map(value: Any, field_name: str, source: Path) -> dict[str, str]:
    return shared_parse_string_map(value, field_name, source)


def normalize_aliases_map(value: dict[str, Any] | None) -> dict[str, str]:
    if not value:
        return {}
    parsed: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, str):
            continue
        key = raw_key.strip()
        alias = raw_value.strip()
        if key and alias:
            parsed[key] = alias
    return parsed


def parse_csv_items(value: str | None) -> list[str]:
    return shared_parse_csv_items(value)


def load_pronunciation_aliases_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise RuntimeError(f"Pronunciation dictionary file does not exist: {path}")

    suffix = path.suffix.lower()
    if suffix == ".toml":
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise RuntimeError(f"Invalid TOML in pronunciation file {path}: {exc}") from exc

        if not isinstance(data, dict):
            raise RuntimeError(f"Pronunciation file must be a TOML table: {path}")
        aliases_obj = data.get("aliases", data)
        if not isinstance(aliases_obj, dict):
            raise RuntimeError(f"Pronunciation aliases must be a TOML table in {path}")
        return normalize_aliases_map(aliases_obj)

    if suffix == ".json":
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON in pronunciation file {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"Pronunciation file must be a JSON object: {path}")
        aliases_obj = data.get("aliases", data)
        if not isinstance(aliases_obj, dict):
            raise RuntimeError(f"Pronunciation aliases must be an object in {path}")
        return normalize_aliases_map(aliases_obj)

    aliases: dict[str, str] = {}
    for line in path.read_text(errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, alias = stripped.split("=", 1)
        key = key.strip()
        alias = alias.strip()
        if key and alias:
            aliases[key] = alias
    if not aliases:
        raise RuntimeError(
            f"Unsupported or empty pronunciation file format for {path}. "
            "Use .toml/.json or key=value text lines."
        )
    return aliases


def load_dependencies(path: Path) -> dict[str, Any]:
    return shared_load_dependencies(path)


def find_missing_dependencies(spec: dict[str, Any]) -> list[MissingDependency]:
    return shared_find_missing_dependencies(spec)


def summarize_missing_dependencies(missing: list[MissingDependency]) -> None:
    return shared_summarize_missing_dependencies(missing)


def install_missing_dependencies(missing: list[MissingDependency]) -> tuple[bool, Path | None]:
    return shared_install_missing_dependencies(missing)


def ensure_dependencies_ready(
    dependency_file: Path,
    *,
    auto_install: bool,
) -> None:
    return shared_ensure_dependencies_ready(dependency_file, auto_install=auto_install)


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


def voice_supports_pitch(voice_name: str) -> bool:
    lower = voice_name.lower()
    # Chirp 3 voices reject the pitch field in AudioConfig.
    return "chirp3" not in lower


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


def build_chunk_cache_key(
    *,
    text: str,
    lang: str,
    voice_name: str,
    speaking_rate: float,
    pitch: float,
    use_ssml: bool,
    ssml_profile_signature: str,
) -> str:
    payload = {
        "schema": "tts-cache-v2",
        "text": text,
        "lang": lang,
        "voice_name": voice_name,
        "speaking_rate": speaking_rate,
        "pitch": pitch,
        "use_ssml": bool(use_ssml),
        "ssml_profile_signature": ssml_profile_signature,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def cache_file_for_key(cache_dir: Path, cache_key: str) -> Path:
    return cache_dir / f"{cache_key}.mp3"


def restore_chunk_from_cache(cache_file: Path, out_file: Path) -> bool:
    if not cache_file.exists() or cache_file.stat().st_size == 0:
        return False
    out_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = out_file.with_suffix(".mp3.tmp")
    shutil.copyfile(cache_file, tmp_file)
    tmp_file.replace(out_file)
    return True


def persist_chunk_to_cache(cache_file: Path, audio: bytes) -> bool:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    if cache_file.exists() and cache_file.stat().st_size > 0:
        return False
    tmp_file = cache_file.with_suffix(".mp3.tmp")
    tmp_file.write_bytes(audio)
    tmp_file.replace(cache_file)
    return True


def plan_chunk_cache(
    *,
    chunks: list[str],
    out_dir: Path,
    cache_dir: Path,
    enabled: bool,
    lang: str,
    voice_name: str,
    speaking_rate: float,
    pitch: float,
    use_ssml: bool,
    ssml_profile_signature: str,
) -> ChunkCachePlan:
    keys: dict[int, str] = {}
    hit_candidates: set[int] = set()
    hit_chars = 0
    if not enabled:
        return ChunkCachePlan(
            cache_dir=cache_dir,
            enabled=False,
            keys=keys,
            hit_candidates=hit_candidates,
            hit_candidate_chars=0,
        )

    for idx, chunk in enumerate(chunks):
        out_file = out_dir / f"{idx:04d}.mp3"
        if out_file.exists() and out_file.stat().st_size > 0:
            continue
        cache_key = build_chunk_cache_key(
            text=chunk,
            lang=lang,
            voice_name=voice_name,
            speaking_rate=speaking_rate,
            pitch=pitch,
            use_ssml=use_ssml,
            ssml_profile_signature=ssml_profile_signature,
        )
        keys[idx] = cache_key
        cache_file = cache_file_for_key(cache_dir, cache_key)
        if cache_file.exists() and cache_file.stat().st_size > 0:
            hit_candidates.add(idx)
            hit_chars += len(chunk)

    return ChunkCachePlan(
        cache_dir=cache_dir,
        enabled=True,
        keys=keys,
        hit_candidates=hit_candidates,
        hit_candidate_chars=hit_chars,
    )


def adjust_preview_for_cache_candidates(preview: BillingPreview, cache_plan: ChunkCachePlan) -> BillingPreview:
    if not cache_plan.enabled or cache_plan.hit_candidate_chars <= 0:
        return preview

    pending_chars = max(0, preview.pending_chars - cache_plan.hit_candidate_chars)
    skipped_chars = preview.total_chars - pending_chars
    if preview.voice_pricing.usd_per_char is None:
        estimated_cost_usd = None
    else:
        estimated_cost_usd = pending_chars * preview.voice_pricing.usd_per_char

    return BillingPreview(
        project_id=preview.project_id,
        month_key=preview.month_key,
        voice_name=preview.voice_name,
        voice_pricing=preview.voice_pricing,
        total_chars=preview.total_chars,
        pending_chars=pending_chars,
        skipped_chars=skipped_chars,
        estimated_cost_usd=estimated_cost_usd,
    )


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
    *,
    use_ssml: bool,
    retry_policy: RetryPolicy,
    rate_limiter: RateLimiter | None,
) -> bytes:
    last_exc: Exception | None = None
    for attempt in range(1, retry_policy.attempts + 1):
        try:
            if rate_limiter is not None:
                rate_limiter.acquire()
            if use_ssml:
                synthesis_input = texttospeech.SynthesisInput(ssml=text)
            else:
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
            if attempt >= retry_policy.attempts or not is_retryable_error(exc):
                raise

            exp_delay = retry_policy.base_delay_s * (retry_policy.backoff_factor ** max(0, attempt - 1))
            jitter = random.uniform(0.0, retry_policy.jitter_s) if retry_policy.jitter_s > 0 else 0.0
            sleep_s = min(retry_policy.max_delay_s, exp_delay + jitter)
            print(
                f"Retry {attempt}/{retry_policy.attempts} after error: {exc}. "
                f"Sleeping {sleep_s:.1f}s."
            )
            if sleep_s > 0:
                time.sleep(sleep_s)

    assert last_exc is not None
    raise last_exc


def synthesize_with_adaptive_split(
    client: Any,
    texttospeech: Any,
    text_chunk: str,
    voice: Any,
    audio_config: Any,
    *,
    use_ssml: bool,
    pronunciation_aliases: dict[str, str],
    pronunciation_case_sensitive: bool,
    acronym_mode: str,
    acronym_min_len: int,
    acronym_max_len: int,
    acronym_include: set[str],
    acronym_exclude: set[str],
    retry_policy: RetryPolicy,
    rate_limiter: RateLimiter | None,
    depth: int = 0,
) -> bytes:
    """
    Try normal synthesis with retries; if input is still invalid, split recursively
    and concatenate child MP3 byte streams so caller still gets a single file.
    """
    payload = (
        shape_ssml_text(
            text_chunk,
            pronunciation_aliases=pronunciation_aliases,
            pronunciation_case_sensitive=pronunciation_case_sensitive,
            acronym_mode=acronym_mode,
            acronym_min_len=acronym_min_len,
            acronym_max_len=acronym_max_len,
            acronym_include=acronym_include,
            acronym_exclude=acronym_exclude,
        )
        if use_ssml
        else text_chunk
    )

    try:
        return synthesize_with_retries(
            client,
            texttospeech,
            payload,
            voice,
            audio_config,
            use_ssml=use_ssml,
            retry_policy=retry_policy,
            rate_limiter=rate_limiter,
        )
    except Exception as exc:  # noqa: BLE001
        if not is_invalid_argument_error(exc):
            raise

        if depth >= MAX_SPLIT_DEPTH:
            raise RuntimeError(
                f"Max split depth reached ({MAX_SPLIT_DEPTH}) for chunk length {len(text_chunk)}"
            ) from exc

        pieces = split_for_retry(text_chunk)
        if len(pieces) <= 1:
            raise

        left = synthesize_with_adaptive_split(
            client,
            texttospeech,
            pieces[0],
            voice,
            audio_config,
            use_ssml=use_ssml,
            pronunciation_aliases=pronunciation_aliases,
            pronunciation_case_sensitive=pronunciation_case_sensitive,
            acronym_mode=acronym_mode,
            acronym_min_len=acronym_min_len,
            acronym_max_len=acronym_max_len,
            acronym_include=acronym_include,
            acronym_exclude=acronym_exclude,
            retry_policy=retry_policy,
            rate_limiter=rate_limiter,
            depth=depth + 1,
        )
        right = synthesize_with_adaptive_split(
            client,
            texttospeech,
            pieces[1],
            voice,
            audio_config,
            use_ssml=use_ssml,
            pronunciation_aliases=pronunciation_aliases,
            pronunciation_case_sensitive=pronunciation_case_sensitive,
            acronym_mode=acronym_mode,
            acronym_min_len=acronym_min_len,
            acronym_max_len=acronym_max_len,
            acronym_include=acronym_include,
            acronym_exclude=acronym_exclude,
            retry_policy=retry_policy,
            rate_limiter=rate_limiter,
            depth=depth + 1,
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


def strip_citations_and_footnotes(t: str) -> str:
    # Common inline citation forms: [12], [12-15], [12, 19]
    t = re.sub(r"\[(?:\s*\d+[a-z]?(?:\s*[-,;]\s*\d+[a-z]?)*\s*)\]", "", t)

    # Author-year citation forms: (Smith, 2020) or (Smith, 2020; Doe, 2021)
    t = re.sub(
        r"\((?:[A-ZÁÉÍÓÖŐÚÜŰ][^()]{1,60}?,\s*\d{4}[a-z]?"
        r"(?:;\s*[A-ZÁÉÍÓÖŐÚÜŰ][^()]{1,60}?,\s*\d{4}[a-z]?){0,4})\)",
        "",
        t,
    )

    t = re.sub(r"\b\d{1,4}\s*[\)\]](?=\s|$)", "", t)
    t = t.replace("¹", "").replace("²", "").replace("³", "").replace("⁴", "")
    t = t.replace("⁵", "").replace("⁶", "").replace("⁷", "").replace("⁸", "").replace("⁹", "")
    return t


def is_chapter_heading(paragraph: str) -> bool:
    p = paragraph.strip()
    if not p or len(p) > 140:
        return False

    patterns = (
        r"^(chapter|chap\.?|part)\s+[0-9ivxlcdm]+[\w .:-]*$",
        r"^(fejezet|r[eé]sz)\s+[0-9ivxlcdm]+[\w .:-]*$",
        r"^[0-9ivxlcdm]+\.\s*(chapter|fejezet|r[eé]sz)\b[\w .:-]*$",
    )
    if any(re.match(pattern, p, re.IGNORECASE) for pattern in patterns):
        return True

    compact = re.sub(r"[^A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű0-9 ]+", "", p).strip()
    if compact and compact.isupper() and 3 <= len(compact.split()) <= 8:
        return True

    return False


def clean_text(t: str) -> str:
    t = t.replace("\u00ad", "")  # soft hyphen
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)  # fix hyphenation across lines
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"\bwww\.\S+", " ", t)
    t = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", " ", t)
    t = strip_citations_and_footnotes(t)

    # Keep paragraph structure for chapter detection.
    t = re.sub(r"\r\n?", "\n", t)
    t = re.sub(r"(?<!\n)\n(?!\n)", " ", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = t.strip()

    # Trim bibliography/references tail when it appears near the end.
    ref_match = re.search(r"\n\n(?:references|bibliography|irodalomjegyz[eé]k)\s*\n", t, re.IGNORECASE)
    if ref_match and ref_match.start() > int(len(t) * 0.6):
        t = t[: ref_match.start()].strip()

    # Critical: prevent "sentence too long" errors.
    t = break_long_runs(t, max_run=420)
    return t


def split_paragraphs(t: str) -> list[str]:
    return [p.strip() for p in t.split("\n\n") if p.strip()]


def detect_chapters(paragraphs: list[str]) -> list[ChapterInfo]:
    chapters: list[ChapterInfo] = []
    for idx, paragraph in enumerate(paragraphs):
        if is_chapter_heading(paragraph):
            title = re.sub(r"\s+", " ", paragraph).strip()
            chapters.append(ChapterInfo(index=len(chapters), title=title, start_paragraph=idx))

    if not chapters:
        return [ChapterInfo(index=0, title="Chapter 1", start_paragraph=0)]

    if chapters[0].start_paragraph != 0:
        chapters.insert(0, ChapterInfo(index=0, title="Chapter 1", start_paragraph=0))

    normalized: list[ChapterInfo] = []
    for item in chapters:
        if normalized and normalized[-1].start_paragraph == item.start_paragraph:
            continue
        normalized.append(item)

    return [
        ChapterInfo(index=idx, title=item.title, start_paragraph=item.start_paragraph)
        for idx, item in enumerate(normalized)
    ]


def chapter_for_paragraph(paragraph_index: int, chapters: list[ChapterInfo]) -> int:
    current = 0
    for idx, chapter in enumerate(chapters):
        if chapter.start_paragraph <= paragraph_index:
            current = idx
        else:
            break
    return current


def build_chunk_plan(
    paragraphs: list[str],
    max_chars: int,
    chapters: list[ChapterInfo],
) -> list[ChunkPlan]:
    plans: list[ChunkPlan] = []
    buf: list[str] = []
    size = 0
    buf_chapter = 0

    def flush_buffer() -> None:
        nonlocal buf, size
        if not buf:
            return
        plans.append(ChunkPlan(text="\n\n".join(buf), chapter_index=buf_chapter))
        buf = []
        size = 0

    for idx, paragraph in enumerate(paragraphs):
        chapter_idx = chapter_for_paragraph(idx, chapters)

        if len(paragraph) > max_chars:
            flush_buffer()
            for start in range(0, len(paragraph), max_chars):
                plans.append(
                    ChunkPlan(text=paragraph[start : start + max_chars], chapter_index=chapter_idx)
                )
            continue

        add = len(paragraph) + (2 if buf else 0)
        if not buf:
            buf = [paragraph]
            size = len(paragraph)
            buf_chapter = chapter_idx
            continue

        if chapter_idx != buf_chapter or size + add > max_chars:
            flush_buffer()
            buf = [paragraph]
            size = len(paragraph)
            buf_chapter = chapter_idx
            continue

        buf.append(paragraph)
        size += add

    flush_buffer()
    return plans


def build_chapter_ranges(chapters: list[ChapterInfo], chunk_plan: list[ChunkPlan]) -> list[dict[str, Any]]:
    if not chunk_plan:
        return []

    ranges: list[dict[str, Any]] = []
    current_chapter = chunk_plan[0].chapter_index
    start_chunk = 0

    for idx, chunk in enumerate(chunk_plan[1:], start=1):
        if chunk.chapter_index != current_chapter:
            title = chapters[current_chapter].title if current_chapter < len(chapters) else f"Chapter {current_chapter + 1}"
            ranges.append(
                {
                    "chapter_index": current_chapter,
                    "title": title,
                    "start_chunk": start_chunk,
                    "end_chunk": idx - 1,
                }
            )
            current_chapter = chunk.chapter_index
            start_chunk = idx

    title = chapters[current_chapter].title if current_chapter < len(chapters) else f"Chapter {current_chapter + 1}"
    ranges.append(
        {
            "chapter_index": current_chapter,
            "title": title,
            "start_chunk": start_chunk,
            "end_chunk": len(chunk_plan) - 1,
        }
    )
    return ranges


def insert_ssml_breaks(escaped_text: str) -> str:
    escaped_text = re.sub(r"([.!?…])\s+", r'\1<break time="350ms"/> ', escaped_text)
    escaped_text = re.sub(r"([;:])\s+", r'\1<break time="220ms"/> ', escaped_text)
    return escaped_text


def token_is_spelled_acronym(
    token: str,
    *,
    mode: str,
    min_len: int,
    max_len: int,
    include: set[str],
    exclude: set[str],
) -> bool:
    upper = token.upper()
    if upper in exclude:
        return False

    if mode == "off":
        return False
    if mode == "list":
        return upper in include

    if upper in include:
        return True
    if not token.isalpha():
        return False
    if token != upper:
        return False
    if len(token) < min_len or len(token) > max_len:
        return False
    if re.fullmatch(r"[IVXLCDM]+", token):
        return False
    return True


def render_ssml_paragraph(
    paragraph: str,
    *,
    pronunciation_aliases: dict[str, str],
    pronunciation_case_sensitive: bool,
    acronym_mode: str,
    acronym_min_len: int,
    acronym_max_len: int,
    acronym_include: set[str],
    acronym_exclude: set[str],
) -> str:
    spans: list[tuple[int, int, str]] = []

    def overlaps(start: int, end: int) -> bool:
        for s_start, s_end, _ in spans:
            if not (end <= s_start or start >= s_end):
                return True
        return False

    if pronunciation_aliases:
        flags = 0 if pronunciation_case_sensitive else re.IGNORECASE
        for term, alias in sorted(pronunciation_aliases.items(), key=lambda item: len(item[0]), reverse=True):
            pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", flags)
            alias_escaped = html.escape(alias, quote=True)
            for match in pattern.finditer(paragraph):
                start, end = match.span()
                if overlaps(start, end):
                    continue
                matched_text = html.escape(match.group(0), quote=False)
                spans.append(
                    (
                        start,
                        end,
                        f'<sub alias="{alias_escaped}">{matched_text}</sub>',
                    )
                )

    acronym_pattern = re.compile(r"\b([A-Z]{2,12})\b")
    for match in acronym_pattern.finditer(paragraph):
        token = match.group(1)
        start, end = match.span(1)
        if overlaps(start, end):
            continue
        if not token_is_spelled_acronym(
            token,
            mode=acronym_mode,
            min_len=acronym_min_len,
            max_len=acronym_max_len,
            include=acronym_include,
            exclude=acronym_exclude,
        ):
            continue
        token_escaped = html.escape(token, quote=False)
        spans.append(
            (
                start,
                end,
                f'<say-as interpret-as="characters">{token_escaped}</say-as>',
            )
        )

    if not spans:
        return insert_ssml_breaks(html.escape(paragraph, quote=False))

    spans.sort(key=lambda item: item[0])
    out: list[str] = []
    cursor = 0
    for start, end, replacement in spans:
        if start > cursor:
            out.append(insert_ssml_breaks(html.escape(paragraph[cursor:start], quote=False)))
        out.append(replacement)
        cursor = end
    if cursor < len(paragraph):
        out.append(insert_ssml_breaks(html.escape(paragraph[cursor:], quote=False)))
    return "".join(out)


def shape_ssml_text(
    chunk_text: str,
    *,
    pronunciation_aliases: dict[str, str] | None = None,
    pronunciation_case_sensitive: bool = False,
    acronym_mode: str = "auto",
    acronym_min_len: int = 2,
    acronym_max_len: int = 5,
    acronym_include: set[str] | None = None,
    acronym_exclude: set[str] | None = None,
) -> str:
    paragraphs = split_paragraphs(chunk_text)
    aliases = pronunciation_aliases or {}
    include = {item.upper() for item in (acronym_include or set()) if item.strip()}
    exclude = {item.upper() for item in (acronym_exclude or set()) if item.strip()}
    blocks: list[str] = []
    for paragraph in paragraphs:
        rendered = render_ssml_paragraph(
            paragraph,
            pronunciation_aliases=aliases,
            pronunciation_case_sensitive=pronunciation_case_sensitive,
            acronym_mode=acronym_mode,
            acronym_min_len=acronym_min_len,
            acronym_max_len=acronym_max_len,
            acronym_include=include,
            acronym_exclude=exclude,
        )
        if is_chapter_heading(paragraph):
            blocks.append(f"<p><emphasis level=\"moderate\">{rendered}</emphasis><break time=\"900ms\"/></p>")
        else:
            blocks.append(f"<p>{rendered}</p>")
    return "<speak>" + "".join(blocks) + "</speak>"


def enforce_run_limits(
    preview: BillingPreview,
    *,
    max_chars_per_run: int | None,
    max_cost_usd: float | None,
) -> None:
    if max_chars_per_run is not None and preview.pending_chars > max_chars_per_run:
        raise RuntimeError(
            f"Pending characters {preview.pending_chars:,} exceed max_chars_per_run={max_chars_per_run:,}."
        )

    if (
        max_cost_usd is not None
        and preview.estimated_cost_usd is not None
        and preview.estimated_cost_usd > max_cost_usd
    ):
        raise RuntimeError(
            "Estimated cost exceeds max_cost_usd: "
            f"{format_usd(preview.estimated_cost_usd)} > {format_usd(max_cost_usd)}"
        )


def confirm_before_spend_gate(preview: BillingPreview, enabled: bool) -> None:
    if not enabled or preview.pending_chars == 0:
        return
    if not sys.stdin.isatty():
        raise RuntimeError("confirm-before-spend requires interactive terminal input.")

    answer = input(
        "Proceed with paid synthesis? "
        f"Pending chars={preview.pending_chars:,}, estimated cost={format_usd(preview.estimated_cost_usd)} [y/N]: "
    ).strip().lower()
    if answer not in {"y", "yes"}:
        raise RuntimeError("Synthesis cancelled by user at confirm-before-spend gate.")


@contextmanager
def dashboard_keyboard_mode(enabled: bool):
    if not enabled:
        yield
        return

    try:
        import termios
        import tty
    except Exception:  # noqa: BLE001
        yield
        return

    fd = sys.stdin.fileno()
    old_state = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    try:
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_state)


def poll_dashboard_key() -> str | None:
    if not sys.stdin.isatty():
        return None
    try:
        import select
    except Exception:  # noqa: BLE001
        return None

    try:
        ready, _, _ = select.select([sys.stdin], [], [], 0)
    except Exception:  # noqa: BLE001
        return None
    if not ready:
        return None
    try:
        return sys.stdin.read(1)
    except Exception:  # noqa: BLE001
        return None


def build_dashboard_view(
    *,
    preview: BillingPreview,
    progress: Any,
    generated: int,
    skipped: int,
    failures: int,
    generated_chars: int,
    paused: bool,
    retry_requested: bool,
    cancelled: bool,
    current_chunk: str,
    events: list[str],
) -> Any:
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table

    summary = Table(show_header=False, box=None, padding=(0, 1))
    summary.add_row("Current Chunk", current_chunk)
    summary.add_row("Generated", str(generated))
    summary.add_row("Skipped", str(skipped))
    summary.add_row("Failures", str(failures))
    summary.add_row("Generated Chars", f"{generated_chars:,}")
    summary.add_row("Pending Chars", f"{preview.pending_chars:,}")
    summary.add_row("Estimated Cost", format_usd(preview.estimated_cost_usd))
    summary.add_row("Paused", "yes" if paused else "no")
    summary.add_row("Retry Failed", "armed" if retry_requested else "off")
    summary.add_row("Cancelled", "yes" if cancelled else "no")

    controls = "Controls: p pause/resume | r retry failed chunks after pass | q cancel"
    events_text = "\n".join(events[-6:]) if events else "No events yet."

    return Group(
        Panel(summary, title="TTS Dashboard", expand=False),
        progress,
        Panel(controls, title="Controls", expand=False),
        Panel(events_text, title="Events", expand=False),
    )


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
    use_ssml: bool = True,
    pronunciation_file: Path | None = None,
    pronunciation_aliases: dict[str, str] | None = None,
    pronunciation_case_sensitive: bool = False,
    acronym_mode: str = "auto",
    acronym_min_len: int = 2,
    acronym_max_len: int = 5,
    acronym_include: list[str] | None = None,
    acronym_exclude: list[str] | None = None,
    write_chapter_map: Path | None = None,
    max_chars_per_run: int | None = None,
    max_cost_usd: float | None = None,
    confirm_before_spend: bool = False,
    dashboard: bool = True,
    workers: int = 1,
    rate_limit_rps: float | None = None,
    retry_attempts: int = RETRY_ATTEMPTS,
    retry_base_delay_s: float = 1.0,
    retry_backoff_factor: float = 1.8,
    retry_max_delay_s: float = 30.0,
    retry_jitter_s: float = 0.25,
    retry_failed_chunks: bool = True,
    chunk_cache: bool = True,
    cache_dir: Path | None = None,
) -> dict[str, int | float | str | bool]:
    run_started_at = time.perf_counter()
    if not input_txt.exists():
        raise RuntimeError(f"Missing {input_txt}. Provide a valid text file.")
    try:
        validate_pricing_table(VOICE_PRICING_TABLE)
    except ValueError as exc:
        raise RuntimeError(f"Invalid pricing config: {exc}") from exc
    if workers < 1:
        raise RuntimeError("--workers must be >= 1")
    try:
        validate_retry_settings(
            attempts=retry_attempts,
            base_delay=retry_base_delay_s,
            backoff_factor=retry_backoff_factor,
            max_delay=retry_max_delay_s,
            jitter=retry_jitter_s,
        )
        validate_cost_limit(max_cost_usd)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    if rate_limit_rps is not None and rate_limit_rps <= 0:
        raise RuntimeError("--rate-limit-rps must be > 0")
    if retry_failed_chunks not in {True, False}:
        raise RuntimeError("--retry-failed-chunks must be a boolean value")
    if acronym_mode not in {"off", "auto", "list"}:
        raise RuntimeError("--acronym-mode must be one of: off, auto, list")
    if acronym_min_len < 1:
        raise RuntimeError("--acronym-min-len must be >= 1")
    if acronym_max_len < acronym_min_len:
        raise RuntimeError("--acronym-max-len must be >= --acronym-min-len")

    out_dir.mkdir(exist_ok=True, parents=True)
    if cache_dir is None:
        cache_dir = out_dir / ".chunk_cache"
    if chunk_cache:
        cache_dir.mkdir(exist_ok=True, parents=True)

    retry_policy = RetryPolicy(
        attempts=retry_attempts,
        base_delay_s=retry_base_delay_s,
        backoff_factor=retry_backoff_factor,
        max_delay_s=retry_max_delay_s,
        jitter_s=retry_jitter_s,
    )
    engine = TtsEngine(
        client_adapter=GoogleClientAdapter(load_google_tts=load_google_tts),
        planner=ChunkPlanner(
            clean_text=clean_text,
            split_paragraphs=split_paragraphs,
            detect_chapters=detect_chapters,
            build_chunk_plan=build_chunk_plan,
            build_chapter_ranges=build_chapter_ranges,
        ),
        shaper=SsmlShaper(shape_ssml_text=shape_ssml_text),
        cache=CacheLayer(
            plan_chunk_cache=plan_chunk_cache,
            adjust_preview_for_cache_candidates=adjust_preview_for_cache_candidates,
            cache_file_for_key=cache_file_for_key,
            restore_chunk_from_cache=restore_chunk_from_cache,
            persist_chunk_to_cache=persist_chunk_to_cache,
        ),
        retry=RetryController(synthesize_with_retries=synthesize_with_retries),
    )

    effective_aliases: dict[str, str] = {}
    if pronunciation_file is not None:
        effective_aliases.update(load_pronunciation_aliases_file(pronunciation_file))
    if pronunciation_aliases:
        effective_aliases.update(normalize_aliases_map(pronunciation_aliases))
    acronym_include_set = {item.strip().upper() for item in (acronym_include or []) if item.strip()}
    acronym_exclude_set = {item.strip().upper() for item in (acronym_exclude or []) if item.strip()}
    ssml_profile_signature = json.dumps(
        {
            "use_ssml": bool(use_ssml),
            "pronunciation_case_sensitive": bool(pronunciation_case_sensitive),
            "aliases": effective_aliases,
            "acronym_mode": acronym_mode,
            "acronym_min_len": int(acronym_min_len),
            "acronym_max_len": int(acronym_max_len),
            "acronym_include": sorted(acronym_include_set),
            "acronym_exclude": sorted(acronym_exclude_set),
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    raw = input_txt.read_text(errors="ignore")
    planning = engine.planner.plan(raw, max_chars)
    text = planning["cleaned"]
    chunk_plan = planning["chunk_plan"]
    chunks = planning["chunks"]
    chapter_ranges = planning["chapter_ranges"]

    if write_cleaned is not None:
        write_cleaned.parent.mkdir(parents=True, exist_ok=True)
        write_cleaned.write_text(text)

    if write_chapter_map is not None:
        write_chapter_map.parent.mkdir(parents=True, exist_ok=True)
        write_chapter_map.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                    "chunk_count": len(chunk_plan),
                    "chapters": chapter_ranges,
                },
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )

    print(f"Input file: {input_txt}")
    print(f"Total cleaned chars: {len(text):,}")
    print(f"Chunks to synthesize: {len(chunks):,}")
    print(f"Detected chapters: {len(chapter_ranges)}")
    print(f"Output folder: {out_dir.resolve()}")
    if use_ssml:
        print(
            f"SSML profile: aliases={len(effective_aliases)}, "
            f"acronym_mode={acronym_mode}, acronym_len={acronym_min_len}-{acronym_max_len}"
        )

    preview = build_billing_preview(
        voice_name=voice_name,
        chunks=chunks,
        out_dir=out_dir,
    )
    cache_plan = engine.cache.plan_chunk_cache(
        chunks=chunks,
        out_dir=out_dir,
        cache_dir=cache_dir,
        enabled=chunk_cache,
        lang=lang,
        voice_name=voice_name,
        speaking_rate=speaking_rate,
        pitch=pitch,
        use_ssml=use_ssml,
        ssml_profile_signature=ssml_profile_signature,
    )
    preview = engine.cache.adjust_preview_for_cache_candidates(preview, cache_plan)
    render_billing_preview(preview, ui_mode=ui_mode)
    if cache_plan.enabled:
        print(
            f"Chunk cache: {cache_plan.cache_dir.resolve()} "
            f"(candidates: {len(cache_plan.hit_candidates)} chunks, "
            f"{cache_plan.hit_candidate_chars:,} chars)"
        )
    enforce_run_limits(
        preview,
        max_chars_per_run=max_chars_per_run,
        max_cost_usd=max_cost_usd,
    )

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
            "cache_enabled": bool(chunk_cache),
            "cache_dir": str(cache_dir),
            "cache_hit_candidates": len(cache_plan.hit_candidates),
            "cache_hit_candidate_chars": cache_plan.hit_candidate_chars,
            "total_chars": len(text),
            "voice_name": voice_name,
            "run_duration_s": round(time.perf_counter() - run_started_at, 3),
            "chapters": len(chapter_ranges),
            "chapter_map_path": str(write_chapter_map) if write_chapter_map is not None else "",
        }

    if preview.pending_chars == 0:
        cache_hits = 0
        if cache_plan.enabled and cache_plan.hit_candidates:
            for idx in sorted(cache_plan.hit_candidates):
                out_file = out_dir / f"{idx:04d}.mp3"
                if out_file.exists() and out_file.stat().st_size > 0:
                    continue
                cache_key = cache_plan.keys.get(idx)
                if cache_key is None:
                    continue
                cache_file = engine.cache.cache_file_for_key(cache_plan.cache_dir, cache_key)
                if engine.cache.restore_chunk_from_cache(cache_file, out_file):
                    cache_hits += 1

        print("All chunk files already exist or were restored from cache. Nothing new to synthesize.")
        render_billing_postrun(
            preview=preview,
            generated_chars=0,
            run_billable_chars=0,
            run_estimated_cost_usd=0.0,
            ui_mode=ui_mode,
        )
        return {
            "estimate_only": False,
            "test_run": False,
            "generated": 0,
            "skipped": len(chunks),
            "failures": 0,
            "chunks": len(chunks),
            "generated_chars": 0,
            "run_billable_chars": 0,
            "run_estimated_cost_usd": 0.0,
            "cache_enabled": bool(chunk_cache),
            "cache_dir": str(cache_dir),
            "cache_hits": cache_hits,
            "cache_writes": 0,
            "workers": workers,
            "total_chars": len(text),
            "voice_name": voice_name,
            "run_duration_s": round(time.perf_counter() - run_started_at, 3),
            "chapters": len(chapter_ranges),
            "chapter_map_path": str(write_chapter_map) if write_chapter_map is not None else "",
        }

    if not test_run:
        confirm_before_spend_gate(preview, enabled=confirm_before_spend)

    if test_run:
        if test_delay < 0:
            raise RuntimeError("--test-delay must be >= 0")

        generated = 0
        skipped = 0
        generated_chars = 0
        failures = 0
        cache_hits = 0

        use_rich = should_use_rich_ui(ui_mode)
        dashboard_enabled = use_rich and dashboard and sys.stdin.isatty() and sys.stdout.isatty()
        if dashboard_enabled:
            from rich.live import Live
            from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn

            progress = Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeRemainingColumn(),
            )
            task = progress.add_task("Simulating", total=len(chunks))

            paused = False
            retry_requested = False
            cancelled = False
            current_chunk = "-"
            events: list[str] = []

            def handle_keys() -> None:
                nonlocal paused, retry_requested, cancelled
                while True:
                    key = poll_dashboard_key()
                    if key is None:
                        return
                    key = key.lower()
                    if key == "p":
                        paused = not paused
                        events.append("Paused." if paused else "Resumed.")
                    elif key == "r":
                        retry_requested = True
                        events.append("Retry flag enabled for failed chunks.")
                    elif key == "q":
                        cancelled = True
                        events.append("Cancel requested.")

            with dashboard_keyboard_mode(True):
                with Live(
                    build_dashboard_view(
                        preview=preview,
                        progress=progress,
                        generated=generated,
                        skipped=skipped,
                        failures=failures,
                        generated_chars=generated_chars,
                        paused=paused,
                        retry_requested=retry_requested,
                        cancelled=cancelled,
                        current_chunk=current_chunk,
                        events=events,
                    ),
                    refresh_per_second=8,
                ) as live:
                    for idx, chunk in enumerate(chunks):
                        handle_keys()
                        while paused and not cancelled:
                            handle_keys()
                            live.update(
                                build_dashboard_view(
                                    preview=preview,
                                    progress=progress,
                                    generated=generated,
                                    skipped=skipped,
                                    failures=failures,
                                    generated_chars=generated_chars,
                                    paused=paused,
                                    retry_requested=retry_requested,
                                    cancelled=cancelled,
                                    current_chunk=current_chunk,
                                    events=events,
                                )
                            )
                            time.sleep(0.1)

                        if cancelled:
                            raise RuntimeError("Cancelled from dashboard.")

                        current_chunk = f"{idx + 1}/{len(chunks)}"
                        out_file = out_dir / f"{idx:04d}.mp3"
                        if out_file.exists() and out_file.stat().st_size > 0:
                            skipped += 1
                            progress.advance(task)
                            events.append(f"Skipped {out_file.name} (already exists).")
                            continue
                        if cache_plan.enabled and idx in cache_plan.hit_candidates:
                            skipped += 1
                            cache_hits += 1
                            progress.advance(task)
                            events.append(f"Skipped {out_file.name} (cache candidate).")
                            continue

                        if test_delay > 0:
                            time.sleep(test_delay)
                        if test_write_placeholders:
                            out_file.write_bytes(b"FAKE_MP3_PLACEHOLDER")
                        generated += 1
                        generated_chars += len(chunk)
                        progress.advance(task)
                        if generated % 25 == 0:
                            events.append(f"Simulated {generated}/{len(chunks)} chunks.")

                        live.update(
                            build_dashboard_view(
                                preview=preview,
                                progress=progress,
                                generated=generated,
                                skipped=skipped,
                                failures=failures,
                                generated_chars=generated_chars,
                                paused=paused,
                                retry_requested=retry_requested,
                                cancelled=cancelled,
                                current_chunk=current_chunk,
                                events=events,
                            )
                        )
        elif use_rich:
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
                    if cache_plan.enabled and idx in cache_plan.hit_candidates:
                        skipped += 1
                        cache_hits += 1
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
                if cache_plan.enabled and idx in cache_plan.hit_candidates:
                    skipped += 1
                    cache_hits += 1
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
            "cache_enabled": bool(chunk_cache),
            "cache_dir": str(cache_dir),
            "cache_hits": cache_hits,
            "cache_writes": 0,
            "workers": workers,
            "total_chars": len(text),
            "voice_name": voice_name,
            "run_duration_s": round(time.perf_counter() - run_started_at, 3),
            "chapters": len(chapter_ranges),
            "chapter_map_path": str(write_chapter_map) if write_chapter_map is not None else "",
        }

    texttospeech, base_client = engine.client_adapter.create()
    

    voice = texttospeech.VoiceSelectionParams(
        language_code=lang,
        name=voice_name,
    )

    audio_config_kwargs: dict[str, Any] = {
        "audio_encoding": texttospeech.AudioEncoding.MP3,
        "speaking_rate": speaking_rate,
    }
    if voice_supports_pitch(voice_name):
        audio_config_kwargs["pitch"] = pitch
    elif abs(pitch) > 1e-9:
        print(f"Voice {voice_name} ignores --pitch; omitting pitch setting.")

    audio_config = texttospeech.AudioConfig(**audio_config_kwargs)

    rate_limiter = RateLimiter(rate_limit_rps)
    failures: list[tuple[str, str]] = []
    failed_indices: list[int] = []
    generated = 0
    skipped = 0
    cache_hits = 0
    cache_writes = 0
    generated_chars = 0

    def synthesize_index(idx: int, *, client_obj: Any) -> bytes:
        return synthesize_with_adaptive_split(
            client_obj,
            texttospeech,
            chunks[idx],
            voice,
            audio_config,
            use_ssml=use_ssml,
            pronunciation_aliases=effective_aliases,
            pronunciation_case_sensitive=pronunciation_case_sensitive,
            acronym_mode=acronym_mode,
            acronym_min_len=acronym_min_len,
            acronym_max_len=acronym_max_len,
            acronym_include=acronym_include_set,
            acronym_exclude=acronym_exclude_set,
            retry_policy=retry_policy,
            rate_limiter=rate_limiter,
            depth=0,
        )

    def write_chunk_audio(idx: int, audio: bytes) -> None:
        nonlocal cache_writes
        out_file = out_dir / f"{idx:04d}.mp3"
        tmp_file = out_file.with_suffix(".mp3.tmp")
        tmp_file.write_bytes(audio)
        tmp_file.replace(out_file)
        if cache_plan.enabled:
            cache_key = cache_plan.keys.get(idx)
            if cache_key is not None:
                cache_file = engine.cache.cache_file_for_key(cache_plan.cache_dir, cache_key)
                if engine.cache.persist_chunk_to_cache(cache_file, audio):
                    cache_writes += 1

    pending_indices: list[int] = []
    for idx in range(len(chunks)):
        out_file = out_dir / f"{idx:04d}.mp3"
        if out_file.exists() and out_file.stat().st_size > 0:
            skipped += 1
            continue

        restored = False
        if cache_plan.enabled and idx in cache_plan.hit_candidates:
            cache_key = cache_plan.keys.get(idx)
            if cache_key is not None:
                cache_file = engine.cache.cache_file_for_key(cache_plan.cache_dir, cache_key)
                restored = restore_chunk_from_cache(cache_file, out_file)

        if restored:
            skipped += 1
            cache_hits += 1
            continue

        pending_indices.append(idx)

    use_rich = should_use_rich_ui(ui_mode)
    dashboard_enabled = use_rich and dashboard and sys.stdin.isatty() and sys.stdout.isatty()
    effective_workers = workers
    if dashboard_enabled and workers > 1:
        print("Dashboard mode currently runs sequentially. Use --no-dashboard for parallel workers.")
        effective_workers = 1

    print(
        f"Synthesis settings: workers={effective_workers}, "
        f"rate_limit_rps={rate_limit_rps if rate_limit_rps is not None else 'none'}, "
        f"retry_attempts={retry_policy.attempts}"
    )

    if pending_indices:
        if dashboard_enabled:
            from rich.live import Live
            from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn

            progress = Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeRemainingColumn(),
            )
            task = progress.add_task("Synthesizing", total=len(pending_indices))

            paused = False
            retry_requested = False
            cancelled = False
            current_chunk = "-"
            events: list[str] = []

            def handle_keys() -> None:
                nonlocal paused, retry_requested, cancelled
                while True:
                    key = poll_dashboard_key()
                    if key is None:
                        return
                    key = key.lower()
                    if key == "p":
                        paused = not paused
                        events.append("Paused." if paused else "Resumed.")
                    elif key == "r":
                        retry_requested = True
                        events.append("Retry flag enabled for failed chunks.")
                    elif key == "q":
                        cancelled = True
                        events.append("Cancel requested.")

            with dashboard_keyboard_mode(True):
                with Live(
                    build_dashboard_view(
                        preview=preview,
                        progress=progress,
                        generated=generated,
                        skipped=skipped,
                        failures=len(failures),
                        generated_chars=generated_chars,
                        paused=paused,
                        retry_requested=retry_requested,
                        cancelled=cancelled,
                        current_chunk=current_chunk,
                        events=events,
                    ),
                    refresh_per_second=8,
                ) as live:
                    for pos, idx in enumerate(pending_indices, start=1):
                        handle_keys()
                        while paused and not cancelled:
                            handle_keys()
                            live.update(
                                build_dashboard_view(
                                    preview=preview,
                                    progress=progress,
                                    generated=generated,
                                    skipped=skipped,
                                    failures=len(failures),
                                    generated_chars=generated_chars,
                                    paused=paused,
                                    retry_requested=retry_requested,
                                    cancelled=cancelled,
                                    current_chunk=current_chunk,
                                    events=events,
                                )
                            )
                            time.sleep(0.1)

                        if cancelled:
                            raise RuntimeError("Cancelled from dashboard.")

                        stem = f"{idx:04d}"
                        current_chunk = f"{pos}/{len(pending_indices)}"
                        try:
                            audio = synthesize_index(idx, client_obj=base_client)
                        except Exception as exc:  # noqa: BLE001
                            failures.append((stem, str(exc)))
                            failed_indices.append(idx)
                            events.append(f"Failed {stem}: {exc}")
                            progress.advance(task)
                            continue

                        write_chunk_audio(idx, audio)
                        generated += 1
                        generated_chars += len(chunks[idx])
                        progress.advance(task)
                        if generated % 25 == 0:
                            events.append(f"Generated {generated}/{len(pending_indices)} chunks.")

                        live.update(
                            build_dashboard_view(
                                preview=preview,
                                progress=progress,
                                generated=generated,
                                skipped=skipped,
                                failures=len(failures),
                                generated_chars=generated_chars,
                                paused=paused,
                                retry_requested=retry_requested,
                                cancelled=cancelled,
                                current_chunk=current_chunk,
                                events=events,
                            )
                        )

                    if retry_requested and failed_indices:
                        retry_task = progress.add_task("Retry failed", total=len(failed_indices))
                        for idx in list(failed_indices):
                            handle_keys()
                            while paused and not cancelled:
                                handle_keys()
                                time.sleep(0.1)
                            if cancelled:
                                raise RuntimeError("Cancelled from dashboard.")

                            stem = f"{idx:04d}"
                            out_file = out_dir / f"{stem}.mp3"
                            if out_file.exists() and out_file.stat().st_size > 0:
                                progress.advance(retry_task)
                                continue

                            try:
                                audio = synthesize_index(idx, client_obj=base_client)
                            except Exception as exc:  # noqa: BLE001
                                for pos, (failed_stem, _) in enumerate(failures):
                                    if failed_stem == stem:
                                        failures[pos] = (failed_stem, str(exc))
                                        break
                                progress.advance(retry_task)
                                events.append(f"Retry failed {stem}: {exc}")
                                continue

                            write_chunk_audio(idx, audio)
                            generated += 1
                            generated_chars += len(chunks[idx])
                            failures = [item for item in failures if item[0] != stem]
                            progress.advance(retry_task)
                            events.append(f"Retry succeeded {stem}.")

        elif effective_workers > 1:
            thread_state = threading.local()

            def worker(task_idx: int) -> tuple[int, bytes]:
                client_obj = getattr(thread_state, "client", None)
                if client_obj is None:
                    client_obj = texttospeech.TextToSpeechClient()
                    thread_state.client = client_obj
                return task_idx, synthesize_index(task_idx, client_obj=client_obj)

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
                    task = progress.add_task("Synthesizing", total=len(pending_indices))
                    with concurrent.futures.ThreadPoolExecutor(max_workers=effective_workers) as pool:
                        future_map = {pool.submit(worker, idx): idx for idx in pending_indices}
                        for future in concurrent.futures.as_completed(future_map):
                            idx = future_map[future]
                            stem = f"{idx:04d}"
                            try:
                                _, audio = future.result()
                            except Exception as exc:  # noqa: BLE001
                                failures.append((stem, str(exc)))
                                failed_indices.append(idx)
                                progress.console.log(f"Failed {stem}: {exc}")
                                progress.advance(task)
                                continue
                            write_chunk_audio(idx, audio)
                            generated += 1
                            generated_chars += len(chunks[idx])
                            progress.advance(task)
            else:
                completed = 0
                with concurrent.futures.ThreadPoolExecutor(max_workers=effective_workers) as pool:
                    future_map = {pool.submit(worker, idx): idx for idx in pending_indices}
                    for future in concurrent.futures.as_completed(future_map):
                        idx = future_map[future]
                        stem = f"{idx:04d}"
                        completed += 1
                        try:
                            _, audio = future.result()
                        except Exception as exc:  # noqa: BLE001
                            failures.append((stem, str(exc)))
                            failed_indices.append(idx)
                            print(f"Failed {stem}: {exc}")
                            continue
                        write_chunk_audio(idx, audio)
                        generated += 1
                        generated_chars += len(chunks[idx])
                        if completed % 25 == 0:
                            print(f"Processed {completed}/{len(pending_indices)} chunks.")
        elif use_rich:
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
                task = progress.add_task("Synthesizing", total=len(pending_indices))
                for idx in pending_indices:
                    stem = f"{idx:04d}"
                    try:
                        audio = synthesize_index(idx, client_obj=base_client)
                    except Exception as exc:  # noqa: BLE001
                        failures.append((stem, str(exc)))
                        failed_indices.append(idx)
                        progress.console.log(f"Failed {stem}: {exc}")
                        progress.advance(task)
                        continue
                    write_chunk_audio(idx, audio)
                    generated += 1
                    generated_chars += len(chunks[idx])
                    progress.advance(task)
        else:
            for pos, idx in enumerate(pending_indices, start=1):
                stem = f"{idx:04d}"
                try:
                    audio = synthesize_index(idx, client_obj=base_client)
                except Exception as exc:  # noqa: BLE001
                    failures.append((stem, str(exc)))
                    failed_indices.append(idx)
                    print(f"Failed {stem}: {exc}")
                    continue

                write_chunk_audio(idx, audio)
                generated += 1
                generated_chars += len(chunks[idx])
                if pos % 25 == 0:
                    print(f"Generated {pos}/{len(pending_indices)} files. Last: {stem}.mp3")

    if retry_failed_chunks and failures:
        retry_candidates = sorted(
            {
                int(stem)
                for stem, _ in failures
                if stem.isdigit()
            }
        )
        if retry_candidates:
            print(f"Retrying failed chunks once: {len(retry_candidates)}")
            remaining_failures: list[tuple[str, str]] = []
            for idx in retry_candidates:
                stem = f"{idx:04d}"
                out_file = out_dir / f"{stem}.mp3"
                if out_file.exists() and out_file.stat().st_size > 0:
                    continue
                try:
                    audio = synthesize_index(idx, client_obj=base_client)
                except Exception as exc:  # noqa: BLE001
                    remaining_failures.append((stem, str(exc)))
                    continue
                write_chunk_audio(idx, audio)
                generated += 1
                generated_chars += len(chunks[idx])
            failures = remaining_failures

    print(
        f"Done. Generated: {generated}, skipped: {skipped}, failures: {len(failures)}, "
        f"cache hits: {cache_hits}, cache writes: {cache_writes}."
    )

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
        "cache_enabled": bool(chunk_cache),
        "cache_dir": str(cache_dir),
        "cache_hits": cache_hits,
        "cache_writes": cache_writes,
        "workers": effective_workers,
        "total_chars": len(text),
        "voice_name": voice_name,
        "run_duration_s": round(time.perf_counter() - run_started_at, 3),
        "chapters": len(chapter_ranges),
        "chapter_map_path": str(write_chapter_map) if write_chapter_map is not None else "",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate MP3 chunks from a text file.")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {get_version()}",
    )
    add_dependency_args(parser, default_deps_file=DEFAULT_DEPENDENCIES_PATH)
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
    add_ui_args(parser, default_ui="auto", default_dashboard=True)
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
    parser.add_argument(
        "--use-ssml",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Shape chunk text into SSML before synthesis",
    )
    add_pronunciation_args(
        parser,
        default_file=None,
        default_case_sensitive=False,
    )
    parser.add_argument(
        "--acronym-mode",
        choices=("off", "auto", "list"),
        default="auto",
        help="Acronym SSML behavior: off, auto, or list-only",
    )
    parser.add_argument(
        "--acronym-min-len",
        type=int,
        default=2,
        help="Minimum acronym length for auto mode",
    )
    parser.add_argument(
        "--acronym-max-len",
        type=int,
        default=5,
        help="Maximum acronym length for auto mode",
    )
    parser.add_argument(
        "--acronym-include",
        default="",
        help="Comma-separated acronyms to force spell-out (works with auto/list)",
    )
    parser.add_argument(
        "--acronym-exclude",
        default="",
        help="Comma-separated acronyms to never spell-out",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel synthesis workers (dashboard mode stays sequential)",
    )
    parser.add_argument(
        "--rate-limit-rps",
        type=float,
        default=None,
        help="Max API request starts per second across workers",
    )
    add_retry_args(
        parser,
        attempts_default=RETRY_ATTEMPTS,
        base_delay_default=1.0,
        backoff_factor_default=1.8,
        max_delay_default=30.0,
        jitter_default=0.25,
        include_retry_failed_chunks=True,
        include_retry_failed_jobs=False,
    )
    parser.add_argument(
        "--chunk-cache",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable reusable chunk audio cache",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Directory for reusable chunk cache (default: <output-dir>/.chunk_cache)",
    )
    add_cost_control_args(
        parser,
        max_cost_default=None,
        confirm_default=False,
        max_chars_default=None,
    )
    parser.add_argument(
        "--write-chapter-map",
        type=Path,
        default=None,
        help="Optional path to write chapter-to-chunk mapping JSON",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.deps_check:
            ensure_dependencies_ready(
                args.deps_file.resolve(),
                auto_install=bool(args.deps_auto_install),
            )

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
            use_ssml=bool(args.use_ssml),
            pronunciation_file=args.pronunciation_file,
            pronunciation_case_sensitive=bool(args.pronunciation_case_sensitive),
            acronym_mode=args.acronym_mode,
            acronym_min_len=args.acronym_min_len,
            acronym_max_len=args.acronym_max_len,
            acronym_include=parse_csv_items(args.acronym_include),
            acronym_exclude=parse_csv_items(args.acronym_exclude),
            write_chapter_map=args.write_chapter_map,
            max_chars_per_run=args.max_chars_per_run,
            max_cost_usd=args.max_cost_usd,
            confirm_before_spend=bool(args.confirm_before_spend),
            dashboard=bool(args.dashboard),
            workers=args.workers,
            rate_limit_rps=args.rate_limit_rps,
            retry_attempts=args.retry_attempts,
            retry_base_delay_s=args.retry_base_delay,
            retry_backoff_factor=args.retry_backoff_factor,
            retry_max_delay_s=args.retry_max_delay,
            retry_jitter_s=args.retry_jitter,
            retry_failed_chunks=bool(args.retry_failed_chunks),
            chunk_cache=bool(args.chunk_cache),
            cache_dir=args.cache_dir,
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
