from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class GoogleClientAdapter:
    load_google_tts: Callable[[], Any]

    def create(self) -> tuple[Any, Any]:
        texttospeech = self.load_google_tts()
        client = texttospeech.TextToSpeechClient()
        return texttospeech, client


@dataclass(frozen=True)
class ChunkPlanner:
    clean_text: Callable[[str], str]
    split_paragraphs: Callable[[str], list[str]]
    detect_chapters: Callable[[list[str]], list[Any]]
    build_chunk_plan: Callable[[list[str], int, list[Any]], list[Any]]
    build_chapter_ranges: Callable[[list[Any], list[Any]], list[dict[str, Any]]]

    def plan(self, raw_text: str, max_chars: int) -> dict[str, Any]:
        cleaned = self.clean_text(raw_text)
        paragraphs = self.split_paragraphs(cleaned)
        chapters = self.detect_chapters(paragraphs)
        chunk_plan = self.build_chunk_plan(paragraphs, max_chars, chapters)
        chunks = [chunk.text for chunk in chunk_plan]
        chapter_ranges = self.build_chapter_ranges(chapters, chunk_plan)
        return {
            "cleaned": cleaned,
            "paragraphs": paragraphs,
            "chapters": chapters,
            "chunk_plan": chunk_plan,
            "chunks": chunks,
            "chapter_ranges": chapter_ranges,
        }


@dataclass(frozen=True)
class SsmlShaper:
    shape_ssml_text: Callable[..., str]

    def shape(self, chunk: str, **kwargs: Any) -> str:
        return self.shape_ssml_text(chunk, **kwargs)


@dataclass(frozen=True)
class CacheLayer:
    plan_chunk_cache: Callable[..., Any]
    adjust_preview_for_cache_candidates: Callable[..., Any]
    cache_file_for_key: Callable[[Path, str], Path]
    restore_chunk_from_cache: Callable[[Path, Path], bool]
    persist_chunk_to_cache: Callable[[Path, bytes], bool]


@dataclass(frozen=True)
class RetryController:
    synthesize_with_retries: Callable[..., bytes]


@dataclass(frozen=True)
class TtsEngine:
    client_adapter: GoogleClientAdapter
    planner: ChunkPlanner
    shaper: SsmlShaper
    cache: CacheLayer
    retry: RetryController
