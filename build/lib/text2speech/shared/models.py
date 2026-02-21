from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from text2speech.shared.validators import validate_cost_limit, validate_retry_settings


@dataclass(frozen=True)
class RetrySettings:
    attempts: int
    base_delay: float
    backoff_factor: float
    max_delay: float
    jitter: float
    retry_failed_chunks: bool


@dataclass(frozen=True)
class CostControls:
    max_chars_per_run: int | None
    max_cost_usd: float | None
    confirm_before_spend: bool


@dataclass(frozen=True)
class UiSettings:
    ui: str
    dashboard: bool


@dataclass(frozen=True)
class PronunciationSettings:
    pronunciation_file: Path | None
    pronunciation_aliases: dict[str, str]
    pronunciation_case_sensitive: bool
    acronym_mode: str
    acronym_min_len: int
    acronym_max_len: int
    acronym_include: list[str]
    acronym_exclude: list[str]


@dataclass(frozen=True)
class PipelineRuntimeConfig:
    input_kind: str
    lang: str
    voice_name: str
    max_chars: int
    speaking_rate: float
    pitch: float
    ocr: str
    ocr_lang: str
    ocr_page_min_chars: int
    ocr_page_min_alpha_ratio: float
    ocr_page_max_replacement_ratio: float
    ocr_page_min_score: float
    ocr_auto_bad_page_ratio: float
    ocr_prefer_score_delta: float
    skip_tts: bool
    skip_merge: bool
    estimate_only: bool
    test_run: bool
    test_delay: float
    test_write_placeholders: bool
    use_ssml: bool
    cleanup_diff_preview: bool
    cleanup_diff_max_lines: int
    chunk_cache: bool
    cache_dir: Path
    workers: int
    rate_limit_rps: float | None
    max_chars_per_run: int | None
    max_cost_usd: float | None
    confirm_before_spend: bool
    export_m4b: bool
    book_title: str | None
    book_author: str | None
    cover_image: Path | None
    chapter_title_cleanup: bool
    ui_settings: UiSettings
    retry: RetrySettings
    cost: CostControls
    pronunciation: PronunciationSettings

    @classmethod
    def from_namespace(cls, args: Any) -> "PipelineRuntimeConfig":
        if args.input_kind not in {"auto", "clean-txt", "raw-txt", "pdf"}:
            raise ValueError("--input-kind must be one of: auto, clean-txt, raw-txt, pdf")
        if args.acronym_min_len < 1:
            raise ValueError("--acronym-min-len must be >= 1")
        if args.acronym_max_len < args.acronym_min_len:
            raise ValueError("--acronym-max-len must be >= --acronym-min-len")
        if args.cache_dir is None:
            raise ValueError("cache_dir is required")
        if args.workers < 1:
            raise ValueError("--workers must be >= 1")
        if args.rate_limit_rps is not None and args.rate_limit_rps <= 0:
            raise ValueError("--rate-limit-rps must be > 0")
        if args.pronunciation_file is not None and not Path(args.pronunciation_file).exists():
            raise ValueError(f"Pronunciation file does not exist: {args.pronunciation_file}")
        if args.cover_image is not None and not Path(args.cover_image).exists():
            raise ValueError(f"Cover image does not exist: {args.cover_image}")
        cache_dir = Path(args.cache_dir)
        if cache_dir.exists() and not cache_dir.is_dir():
            raise ValueError(f"cache_dir must be a directory path: {cache_dir}")
        validate_retry_settings(
            attempts=args.retry_attempts,
            base_delay=args.retry_base_delay,
            backoff_factor=args.retry_backoff_factor,
            max_delay=args.retry_max_delay,
            jitter=args.retry_jitter,
        )
        validate_cost_limit(args.max_cost_usd)

        return cls(
            input_kind=args.input_kind,
            lang=args.lang,
            voice_name=args.voice_name,
            max_chars=args.max_chars,
            speaking_rate=args.speaking_rate,
            pitch=args.pitch,
            ocr=args.ocr,
            ocr_lang=args.ocr_lang,
            ocr_page_min_chars=args.ocr_page_min_chars,
            ocr_page_min_alpha_ratio=args.ocr_page_min_alpha_ratio,
            ocr_page_max_replacement_ratio=args.ocr_page_max_replacement_ratio,
            ocr_page_min_score=args.ocr_page_min_score,
            ocr_auto_bad_page_ratio=args.ocr_auto_bad_page_ratio,
            ocr_prefer_score_delta=0.04,
            skip_tts=bool(args.skip_tts),
            skip_merge=bool(args.skip_merge),
            estimate_only=bool(args.estimate_only),
            test_run=bool(args.test_run),
            test_delay=args.test_delay,
            test_write_placeholders=bool(args.test_write_placeholders),
            use_ssml=bool(args.use_ssml),
            cleanup_diff_preview=bool(args.cleanup_diff_preview),
            cleanup_diff_max_lines=args.cleanup_diff_max_lines,
            chunk_cache=bool(args.chunk_cache),
            cache_dir=cache_dir,
            workers=args.workers,
            rate_limit_rps=args.rate_limit_rps,
            max_chars_per_run=args.max_chars_per_run,
            max_cost_usd=args.max_cost_usd,
            confirm_before_spend=bool(args.confirm_before_spend),
            export_m4b=bool(args.export_m4b),
            book_title=args.book_title,
            book_author=args.book_author,
            cover_image=args.cover_image,
            chapter_title_cleanup=bool(args.chapter_title_cleanup),
            ui_settings=UiSettings(ui=args.ui, dashboard=bool(args.dashboard)),
            retry=RetrySettings(
                attempts=args.retry_attempts,
                base_delay=args.retry_base_delay,
                backoff_factor=args.retry_backoff_factor,
                max_delay=args.retry_max_delay,
                jitter=args.retry_jitter,
                retry_failed_chunks=bool(args.retry_failed_chunks),
            ),
            cost=CostControls(
                max_chars_per_run=args.max_chars_per_run,
                max_cost_usd=args.max_cost_usd,
                confirm_before_spend=bool(args.confirm_before_spend),
            ),
            pronunciation=PronunciationSettings(
                pronunciation_file=args.pronunciation_file,
                pronunciation_aliases=args.pronunciation_aliases,
                pronunciation_case_sensitive=bool(args.pronunciation_case_sensitive),
                acronym_mode=args.acronym_mode,
                acronym_min_len=args.acronym_min_len,
                acronym_max_len=args.acronym_max_len,
                acronym_include=args.acronym_include,
                acronym_exclude=args.acronym_exclude,
            ),
        )
