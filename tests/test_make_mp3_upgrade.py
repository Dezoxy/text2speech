from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from text2speech import make_mp3_app as make_mp3


FIXTURES = Path(__file__).parent / "fixtures"


def test_synthesize_with_retries_honors_backoff_policy(monkeypatch) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def synthesize_speech(self, **_kwargs):
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError("deadline exceeded")
            return SimpleNamespace(audio_content=b"ok")

    class FakeTTS:
        class SynthesisInput:
            def __init__(self, *, text=None, ssml=None):
                self.text = text
                self.ssml = ssml

    sleeps: list[float] = []
    monkeypatch.setattr(make_mp3.time, "sleep", lambda sec: sleeps.append(sec))
    monkeypatch.setattr(make_mp3.random, "uniform", lambda a, b: 0.0)

    policy = make_mp3.RetryPolicy(
        attempts=4,
        base_delay_s=1.0,
        backoff_factor=2.0,
        max_delay_s=10.0,
        jitter_s=0.0,
    )
    audio = make_mp3.synthesize_with_retries(
        client=FakeClient(),
        texttospeech=FakeTTS,
        text="hello",
        voice=object(),
        audio_config=object(),
        use_ssml=False,
        retry_policy=policy,
        rate_limiter=None,
    )

    assert audio == b"ok"
    assert sleeps == [1.0, 2.0]


def test_run_tts_fake_run_matches_regression_fixtures(tmp_path: Path) -> None:
    input_txt = FIXTURES / "golden_input.txt"
    expected_cleaned = (FIXTURES / "golden_cleaned.txt").read_text()
    expected_chapters = json.loads((FIXTURES / "golden_chapters.json").read_text())
    cleaned_out = tmp_path / "cleaned.txt"
    chapter_map = tmp_path / "chapter_map.json"

    summary = make_mp3.run_tts(
        input_txt=input_txt,
        out_dir=tmp_path / "chunks",
        lang="hu-HU",
        voice_name="hu-HU-Chirp3-HD-Erinome",
        max_chars=500,
        speaking_rate=1.0,
        pitch=0.0,
        write_cleaned=cleaned_out,
        ui_mode="plain",
        test_run=True,
        test_write_placeholders=True,
        use_ssml=True,
        write_chapter_map=chapter_map,
        chunk_cache=True,
        cache_dir=tmp_path / "cache",
    )

    assert summary["test_run"] is True
    assert summary["generated"] == 2
    assert cleaned_out.read_text().strip() == expected_cleaned.strip()

    map_obj = json.loads(chapter_map.read_text())
    assert map_obj["chunk_count"] == expected_chapters["chunk_count"]
    assert map_obj["chapters"] == expected_chapters["chapters"]


def test_chunk_cache_and_parallel_workers(tmp_path: Path, monkeypatch) -> None:
    input_txt = FIXTURES / "golden_input.txt"
    cache_dir = tmp_path / "cache"
    out_a = tmp_path / "out-a"
    out_b = tmp_path / "out-b"
    call_threads: set[int] = set()
    call_count = {"value": 0}

    class FakeTTS:
        class AudioEncoding:
            MP3 = "MP3"

        class SynthesisInput:
            def __init__(self, *, text=None, ssml=None):
                self.text = text
                self.ssml = ssml

        class VoiceSelectionParams:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class AudioConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class TextToSpeechClient:
            def synthesize_speech(self, **_kwargs):
                call_count["value"] += 1
                call_threads.add(threading.get_ident())
                time.sleep(0.03)
                return SimpleNamespace(audio_content=b"fake-audio")

    monkeypatch.setattr(make_mp3, "load_google_tts", lambda: FakeTTS)

    first = make_mp3.run_tts(
        input_txt=input_txt,
        out_dir=out_a,
        lang="hu-HU",
        voice_name="hu-HU-Chirp3-HD-Erinome",
        max_chars=20,
        speaking_rate=1.0,
        pitch=0.0,
        ui_mode="plain",
        test_run=False,
        use_ssml=True,
        chunk_cache=True,
        cache_dir=cache_dir,
        workers=3,
        rate_limit_rps=100.0,
        retry_attempts=2,
    )
    assert first["generated"] > 0
    assert first["cache_writes"] > 0
    assert len(call_threads) > 1
    calls_after_first = call_count["value"]

    second = make_mp3.run_tts(
        input_txt=input_txt,
        out_dir=out_b,
        lang="hu-HU",
        voice_name="hu-HU-Chirp3-HD-Erinome",
        max_chars=20,
        speaking_rate=1.0,
        pitch=0.0,
        ui_mode="plain",
        test_run=False,
        use_ssml=True,
        chunk_cache=True,
        cache_dir=cache_dir,
        workers=3,
        rate_limit_rps=100.0,
        retry_attempts=2,
    )
    assert second["generated"] == 0
    assert second["cache_hits"] > 0
    assert call_count["value"] == calls_after_first


def test_chirp_voice_omits_pitch_from_audio_config(tmp_path: Path, monkeypatch) -> None:
    input_txt = FIXTURES / "golden_input.txt"
    captured_kwargs: list[dict[str, object]] = []

    class FakeTTS:
        class AudioEncoding:
            MP3 = "MP3"

        class SynthesisInput:
            def __init__(self, *, text=None, ssml=None):
                self.text = text
                self.ssml = ssml

        class VoiceSelectionParams:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class AudioConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class TextToSpeechClient:
            def synthesize_speech(self, **kwargs):
                captured_kwargs.append(kwargs["audio_config"].kwargs)
                return SimpleNamespace(audio_content=b"fake-audio")

    monkeypatch.setattr(make_mp3, "load_google_tts", lambda: FakeTTS)

    summary = make_mp3.run_tts(
        input_txt=input_txt,
        out_dir=tmp_path / "out-chirp",
        lang="hu-HU",
        voice_name="hu-HU-Chirp3-HD-Erinome",
        max_chars=500,
        speaking_rate=1.0,
        pitch=-1.0,
        ui_mode="plain",
        use_ssml=True,
        retry_attempts=1,
    )

    assert summary["generated"] > 0
    assert captured_kwargs
    assert "pitch" not in captured_kwargs[0]


def test_non_chirp_voice_keeps_pitch_in_audio_config(tmp_path: Path, monkeypatch) -> None:
    input_txt = FIXTURES / "golden_input.txt"
    captured_kwargs: list[dict[str, object]] = []

    class FakeTTS:
        class AudioEncoding:
            MP3 = "MP3"

        class SynthesisInput:
            def __init__(self, *, text=None, ssml=None):
                self.text = text
                self.ssml = ssml

        class VoiceSelectionParams:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class AudioConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class TextToSpeechClient:
            def synthesize_speech(self, **kwargs):
                captured_kwargs.append(kwargs["audio_config"].kwargs)
                return SimpleNamespace(audio_content=b"fake-audio")

    monkeypatch.setattr(make_mp3, "load_google_tts", lambda: FakeTTS)

    summary = make_mp3.run_tts(
        input_txt=input_txt,
        out_dir=tmp_path / "out-wavenet",
        lang="hu-HU",
        voice_name="hu-HU-Wavenet-B",
        max_chars=500,
        speaking_rate=1.0,
        pitch=-1.0,
        ui_mode="plain",
        use_ssml=True,
        retry_attempts=1,
    )

    assert summary["generated"] > 0
    assert captured_kwargs
    assert captured_kwargs[0]["pitch"] == -1.0


def test_clean_text_filters_pdf_reference_artifacts() -> None:
    raw = (
        "Tartalomjegyzék\n\n"
        "AZ AMAZON S3 TABLES EGY UJ SZOLGALTATAS, AMELY NAGYBETUS TEASERKENT KERULT IDE.\n\n"
        "Bevezeto szoveg maradjon.\n\n"
        "ábra 3- Pelda abra felirat.\n\n"
        "What is Amazon EC2 Auto Scaling?\n\n"
        "Predictive scaling for Amazon EC2 Auto Scaling - Amazon EC2 Auto Scaling\n\n"
        "New for AWS Lambda – Container Image Support | AWS News Blog\n\n"
        "Masik hasznos bekezdes."
    )

    cleaned = make_mp3.clean_text(raw)

    assert "AZ AMAZON S3 TABLES" not in cleaned
    assert "ábra 3" not in cleaned
    assert "What is Amazon EC2 Auto Scaling?" not in cleaned
    assert "Predictive scaling for Amazon EC2 Auto Scaling" not in cleaned
    assert "AWS News Blog" not in cleaned
    assert "Bevezeto szoveg maradjon." in cleaned
    assert "Masik hasznos bekezdes." in cleaned


def test_clean_text_normalizes_common_ocr_punctuation_noise() -> None:
    raw = "Andy Jassy,. ezt mondta,. pelda. : es I. On-Demand: 3 Rugalmas."

    cleaned = make_mp3.clean_text(raw)

    assert ",." not in cleaned
    assert ". :" not in cleaned
    assert ": 3 Rugalmas" not in cleaned
    assert "On-Demand: Rugalmas" in cleaned


def test_shape_ssml_text_applies_aliases_and_acronym_rules() -> None:
    ssml = make_mp3.shape_ssml_text(
        "ChatGPT with API and NASA.",
        pronunciation_aliases={"ChatGPT": "Chat G P T"},
        pronunciation_case_sensitive=False,
        acronym_mode="list",
        acronym_include={"API"},
        acronym_exclude={"NASA"},
    )

    assert '<sub alias="Chat G P T">ChatGPT</sub>' in ssml
    assert '<say-as interpret-as="characters">API</say-as>' in ssml
    assert "NASA" in ssml
    assert '<say-as interpret-as="characters">NASA</say-as>' not in ssml


def test_load_pronunciation_aliases_file_toml(tmp_path: Path) -> None:
    aliases_toml = tmp_path / "aliases.toml"
    aliases_toml.write_text(
        '[aliases]\nChatGPT = "Chat G P T"\nOCR = "O C R"\n'
    )

    loaded = make_mp3.load_pronunciation_aliases_file(aliases_toml)
    assert loaded["ChatGPT"] == "Chat G P T"
    assert loaded["OCR"] == "O C R"
