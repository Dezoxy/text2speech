from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import make_mp3

DEFAULT_CONFIG_PATH = Path("config.toml")
DEFAULT_DEPENDENCIES_PATH = Path("dependencies.toml")
SUPPORTED_INPUT_SUFFIXES = (".pdf", ".txt")


@dataclass
class TextQuality:
    chars: int
    alpha_ratio: float
    non_ascii_ratio: float
    replacement_ratio: float
    whitespace_ratio: float
    score: float
    looks_good: bool


@dataclass
class OcrQualityThresholds:
    page_min_chars: int
    page_min_alpha_ratio: float
    page_max_replacement_ratio: float
    page_min_score: float
    auto_bad_page_ratio: float
    prefer_ocr_score_delta: float


@dataclass
class DoctorCheck:
    name: str
    ok: bool
    required: bool
    detail: str


@dataclass
class MissingDependency:
    kind: str
    name: str
    required: bool
    install_target: str | None
    install_tool: str | None


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip())
    slug = slug.strip("-._")
    return slug or "job"


def run_command(cmd: list[str], step: str) -> None:
    try:
        subprocess.run(cmd, check=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"{step} failed with exit code {exc.returncode}: {' '.join(cmd)}") from exc


def require_command(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"Missing required command: {name}")
    return path


def parse_string_list(value: Any, field_name: str, source: Path) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RuntimeError(f"{field_name} must be a list of strings in {source}")
    return [item.strip() for item in value if item.strip()]


def parse_string_map(value: Any, field_name: str, source: Path) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RuntimeError(f"{field_name} must be a table in {source}")

    parsed: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise RuntimeError(f"{field_name} keys and values must be strings in {source}")
        key_clean = key.strip()
        item_clean = item.strip()
        if key_clean and item_clean:
            parsed[key_clean] = item_clean
    return parsed


def load_dependencies(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"Missing dependency file: {path}")

    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise RuntimeError(f"Invalid TOML in dependency file {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"Dependency file must contain TOML tables: {path}")

    python_section = data.get("python", {})
    commands_section = data.get("commands", {})
    install_section = data.get("install", {})

    if not isinstance(python_section, dict):
        raise RuntimeError(f"[python] must be a TOML table in {path}")
    if not isinstance(commands_section, dict):
        raise RuntimeError(f"[commands] must be a TOML table in {path}")
    if not isinstance(install_section, dict):
        raise RuntimeError(f"[install] must be a TOML table in {path}")

    import_map = parse_string_map(python_section.get("imports"), "python.imports", path)
    if import_map:
        parse_string_list(list(import_map.keys()), "python.imports keys", path)

    return {
        "path": path,
        "python_required": parse_string_list(python_section.get("required"), "python.required", path),
        "python_optional": parse_string_list(python_section.get("optional"), "python.optional", path),
        "python_imports": import_map,
        "commands_required": parse_string_list(commands_section.get("required"), "commands.required", path),
        "commands_optional": parse_string_list(commands_section.get("optional"), "commands.optional", path),
        "install_brew": parse_string_map(install_section.get("brew"), "install.brew", path),
    }


def python_module_available(import_name: str) -> bool:
    try:
        return importlib.util.find_spec(import_name) is not None
    except (ValueError, ModuleNotFoundError):
        return False


def find_missing_dependencies(spec: dict[str, Any]) -> list[MissingDependency]:
    missing: list[MissingDependency] = []
    import_map = spec.get("python_imports", {})
    install_brew = spec.get("install_brew", {})

    for package in spec.get("python_required", []):
        import_name = import_map.get(package, package.replace("-", "_"))
        if not python_module_available(import_name):
            missing.append(
                MissingDependency(
                    kind="python",
                    name=package,
                    required=True,
                    install_target=package,
                    install_tool="pip",
                )
            )

    for package in spec.get("python_optional", []):
        import_name = import_map.get(package, package.replace("-", "_"))
        if not python_module_available(import_name):
            missing.append(
                MissingDependency(
                    kind="python",
                    name=package,
                    required=False,
                    install_target=package,
                    install_tool="pip",
                )
            )

    for command in spec.get("commands_required", []):
        if shutil.which(command) is None:
            missing.append(
                MissingDependency(
                    kind="command",
                    name=command,
                    required=True,
                    install_target=install_brew.get(command),
                    install_tool="brew" if command in install_brew else None,
                )
            )

    for command in spec.get("commands_optional", []):
        if shutil.which(command) is None:
            missing.append(
                MissingDependency(
                    kind="command",
                    name=command,
                    required=False,
                    install_target=install_brew.get(command),
                    install_tool="brew" if command in install_brew else None,
                )
            )

    return missing


def summarize_missing_dependencies(missing: list[MissingDependency]) -> None:
    if not missing:
        print("Dependency precheck: all dependencies are installed.")
        return

    print("\nDependency precheck found missing items:")
    for item in missing:
        level = "required" if item.required else "optional"
        if item.install_tool and item.install_target:
            install_hint = f"{item.install_tool} install {item.install_target}"
        else:
            install_hint = "no auto-install rule"
        print(f" - [{level}] {item.kind}:{item.name} ({install_hint})")


def prompt_install_missing() -> bool:
    if not sys.stdin.isatty():
        print("Non-interactive session detected; cannot prompt for dependency install approval.")
        return False
    answer = input("Install missing dependencies now? [y/N]: ").strip().lower()
    return answer in {"y", "yes"}


def install_python_packages_with_fallback(packages: list[str]) -> tuple[bool, Path | None]:
    cmd = [sys.executable, "-m", "pip", "install", *packages]
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.returncode == 0:
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        return False, None

    combined = f"{result.stdout}\n{result.stderr}".lower()
    if "externally-managed-environment" not in combined:
        details = (f"{result.stdout}\n{result.stderr}").strip()
        raise RuntimeError(
            "pip install missing dependencies failed with exit code "
            f"{result.returncode}: {details or 'no output'}"
        )

    print(
        "System Python is externally managed. "
        "Installing Python dependencies into local virtual environment: .venv"
    )
    run_command([sys.executable, "-m", "venv", ".venv"], "create .venv")
    venv_python = Path(".venv") / "bin" / "python"
    if not venv_python.exists():
        raise RuntimeError(f"Virtual environment python not found: {venv_python}")

    run_command(
        [str(venv_python), "-m", "pip", "install", *packages],
        "pip install missing dependencies in .venv",
    )
    return True, venv_python


def venv_has_required_python_dependencies(spec: dict[str, Any], venv_python: Path) -> bool:
    if not venv_python.exists():
        return False

    required_packages = spec.get("python_required", [])
    import_map = spec.get("python_imports", {})
    import_names = [import_map.get(pkg, pkg.replace("-", "_")) for pkg in required_packages]
    if not import_names:
        return True

    probe = (
        "import importlib.util,sys;"
        "mods=sys.argv[1:];"
        "missing=[m for m in mods if importlib.util.find_spec(m) is None];"
        "sys.exit(0 if not missing else 1)"
    )
    result = subprocess.run(
        [str(venv_python), "-c", probe, *import_names],
        text=True,
        capture_output=True,
    )
    return result.returncode == 0


def relaunch_with_venv_if_ready(spec: dict[str, Any], reason: str) -> None:
    venv_root = Path(".venv")
    venv_python = Path(".venv") / "bin" / "python"
    if not venv_python.exists():
        return

    try:
        if Path(sys.prefix).resolve() == venv_root.resolve():
            return
    except Exception:  # noqa: BLE001
        pass

    if not venv_has_required_python_dependencies(spec, venv_python):
        return

    print(f"{reason} Relaunching with {venv_python} ...")
    try:
        os.execv(str(venv_python), [str(venv_python), *sys.argv])
    except OSError as exc:
        raise RuntimeError(f"Failed to relaunch with {venv_python}: {exc}") from exc


def install_missing_dependencies(missing: list[MissingDependency]) -> tuple[bool, Path | None]:
    python_packages = sorted(
        {
            item.install_target
            for item in missing
            if item.kind == "python" and item.install_tool == "pip" and item.install_target
        }
    )
    brew_formulas = sorted(
        {
            item.install_target
            for item in missing
            if item.kind == "command" and item.install_tool == "brew" and item.install_target
        }
    )

    installed_in_venv = False
    venv_python: Path | None = None

    if python_packages:
        print(f"Installing Python packages via pip: {', '.join(python_packages)}")
        installed_in_venv, venv_python = install_python_packages_with_fallback(python_packages)

    if brew_formulas:
        brew_path = shutil.which("brew")
        if not brew_path:
            raise RuntimeError(
                "Homebrew is not installed, cannot auto-install missing command dependencies."
            )
        print(f"Installing command dependencies via brew: {', '.join(brew_formulas)}")
        run_command([brew_path, "install", *brew_formulas], "brew install missing dependencies")

    return installed_in_venv, venv_python


def ensure_dependencies_ready(
    dependency_file: Path,
    *,
    auto_install: bool,
) -> None:
    spec = load_dependencies(dependency_file)
    missing = find_missing_dependencies(spec)
    if not missing:
        summarize_missing_dependencies(missing)
        return

    required_missing = [item for item in missing if item.required]
    if required_missing and all(item.kind == "python" for item in required_missing):
        relaunch_with_venv_if_ready(
            spec,
            "Required Python dependencies are available in local .venv.",
        )

    summarize_missing_dependencies(missing)
    installable = [item for item in missing if item.install_tool and item.install_target]

    if auto_install and installable:
        if prompt_install_missing():
            installed_in_venv, venv_python = install_missing_dependencies(installable)
            print("Dependency installation completed.")
            if installed_in_venv and venv_python is not None:
                relaunch_with_venv_if_ready(
                    spec,
                    "Dependencies were installed into .venv because this Python is externally managed.",
                )
                rerun = " ".join(shlex.quote(arg) for arg in sys.argv[1:])
                cmd_preview = f"{venv_python} {Path(sys.argv[0]).name}"
                if rerun:
                    cmd_preview = f"{cmd_preview} {rerun}"
                raise RuntimeError(
                    "Dependencies were installed into .venv. "
                    f"Please rerun with: {cmd_preview}"
                )
            missing = find_missing_dependencies(spec)
            summarize_missing_dependencies(missing)
            required_missing = [item for item in missing if item.required]
        else:
            print("Dependency installation skipped by user.")

    if required_missing:
        needed = ", ".join(f"{item.kind}:{item.name}" for item in required_missing)
        raise RuntimeError(
            f"Required dependencies are still missing: {needed}. "
            "Install them and rerun."
        )

    if missing:
        optional = ", ".join(f"{item.kind}:{item.name}" for item in missing)
        print(f"Continuing with optional dependencies missing: {optional}")


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}

    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise RuntimeError(f"Invalid TOML in config file {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"Config file must contain TOML tables: {path}")

    return data


def cfg_get(config: dict[str, Any], section: str, key: str, default: Any) -> Any:
    section_obj = config.get(section, {})
    if isinstance(section_obj, dict) and key in section_obj:
        return section_obj[key]
    return default


def resolve_bool(cli_value: bool | None, config_value: Any, default: bool) -> bool:
    if cli_value is not None:
        return bool(cli_value)

    if isinstance(config_value, bool):
        return config_value

    if isinstance(config_value, str):
        lower = config_value.strip().lower()
        if lower in {"1", "true", "yes", "on"}:
            return True
        if lower in {"0", "false", "no", "off"}:
            return False

    return default


def resolve_choice(value: str, name: str, choices: tuple[str, ...]) -> str:
    if value not in choices:
        options = ", ".join(choices)
        raise RuntimeError(f"Invalid {name}: {value}. Expected one of: {options}")
    return value


def resolve_runtime_args(parsed: argparse.Namespace, config: dict[str, Any]) -> argparse.Namespace:
    runtime = argparse.Namespace(**vars(parsed))

    runtime.input = Path(
        parsed.input
        if parsed.input is not None
        else cfg_get(config, "paths", "input", "input")
    )
    runtime.work_dir = Path(
        parsed.work_dir
        if parsed.work_dir is not None
        else cfg_get(config, "paths", "work_dir", "work")
    )
    runtime.out_dir = Path(
        parsed.out_dir
        if parsed.out_dir is not None
        else cfg_get(config, "paths", "out_dir", "out")
    )

    runtime.ocr = resolve_choice(
        str(
            parsed.ocr
            if parsed.ocr is not None
            else cfg_get(config, "pipeline", "ocr", "auto")
        ),
        "ocr",
        ("auto", "always", "never"),
    )
    runtime.ocr_lang = str(
        parsed.ocr_lang
        if parsed.ocr_lang is not None
        else cfg_get(config, "pipeline", "ocr_lang", "hun+eng")
    )
    runtime.ocr_page_min_chars = int(
        parsed.ocr_page_min_chars
        if parsed.ocr_page_min_chars is not None
        else cfg_get(config, "pipeline", "ocr_page_min_chars", 30)
    )
    runtime.ocr_page_min_alpha_ratio = float(
        parsed.ocr_page_min_alpha_ratio
        if parsed.ocr_page_min_alpha_ratio is not None
        else cfg_get(config, "pipeline", "ocr_page_min_alpha_ratio", 0.22)
    )
    runtime.ocr_page_max_replacement_ratio = float(
        parsed.ocr_page_max_replacement_ratio
        if parsed.ocr_page_max_replacement_ratio is not None
        else cfg_get(config, "pipeline", "ocr_page_max_replacement_ratio", 0.02)
    )
    runtime.ocr_page_min_score = float(
        parsed.ocr_page_min_score
        if parsed.ocr_page_min_score is not None
        else cfg_get(config, "pipeline", "ocr_page_min_score", 0.24)
    )
    runtime.ocr_auto_bad_page_ratio = float(
        parsed.ocr_auto_bad_page_ratio
        if parsed.ocr_auto_bad_page_ratio is not None
        else cfg_get(config, "pipeline", "ocr_auto_bad_page_ratio", 0.1)
    )
    runtime.cleanup_diff_preview = resolve_bool(
        parsed.cleanup_diff_preview,
        cfg_get(config, "pipeline", "cleanup_diff_preview", True),
        True,
    )
    runtime.cleanup_diff_max_lines = int(
        parsed.cleanup_diff_max_lines
        if parsed.cleanup_diff_max_lines is not None
        else cfg_get(config, "pipeline", "cleanup_diff_max_lines", 200)
    )
    runtime.lang = str(
        parsed.lang
        if parsed.lang is not None
        else cfg_get(config, "pipeline", "lang", make_mp3.LANG)
    )
    runtime.voice_name = str(
        parsed.voice_name
        if parsed.voice_name is not None
        else cfg_get(config, "pipeline", "voice_name", make_mp3.VOICE_NAME)
    )
    runtime.max_chars = int(
        parsed.max_chars
        if parsed.max_chars is not None
        else cfg_get(config, "pipeline", "max_chars", make_mp3.MAX_CHARS)
    )
    runtime.speaking_rate = float(
        parsed.speaking_rate
        if parsed.speaking_rate is not None
        else cfg_get(config, "pipeline", "speaking_rate", 1.0)
    )
    runtime.pitch = float(
        parsed.pitch
        if parsed.pitch is not None
        else cfg_get(config, "pipeline", "pitch", 0.0)
    )
    runtime.ui = resolve_choice(
        str(
            parsed.ui
            if parsed.ui is not None
            else cfg_get(config, "pipeline", "ui", "auto")
        ),
        "ui",
        ("auto", "plain", "rich"),
    )

    runtime.skip_tts = resolve_bool(
        parsed.skip_tts,
        cfg_get(config, "flags", "skip_tts", False),
        False,
    )
    runtime.skip_merge = resolve_bool(
        parsed.skip_merge,
        cfg_get(config, "flags", "skip_merge", False),
        False,
    )
    runtime.estimate_only = resolve_bool(
        parsed.estimate_only,
        cfg_get(config, "flags", "estimate_only", False),
        False,
    )
    runtime.test_run = resolve_bool(
        parsed.test_run,
        cfg_get(config, "flags", "test_run", False),
        False,
    )
    runtime.test_write_placeholders = resolve_bool(
        parsed.test_write_placeholders,
        cfg_get(config, "flags", "test_write_placeholders", False),
        False,
    )
    runtime.use_ssml = resolve_bool(
        parsed.use_ssml,
        cfg_get(config, "pipeline", "use_ssml", True),
        True,
    )
    runtime.dashboard = resolve_bool(
        parsed.dashboard,
        cfg_get(config, "pipeline", "dashboard", True),
        True,
    )
    runtime.export_m4b = resolve_bool(
        parsed.export_m4b,
        cfg_get(config, "pipeline", "export_m4b", True),
        True,
    )
    runtime.confirm_before_spend = resolve_bool(
        parsed.confirm_before_spend,
        cfg_get(config, "limits", "confirm_before_spend", False),
        False,
    )
    max_chars_limit = (
        parsed.max_chars_per_run
        if parsed.max_chars_per_run is not None
        else cfg_get(config, "limits", "max_chars_per_run", None)
    )
    runtime.max_chars_per_run = int(max_chars_limit) if max_chars_limit is not None else None

    max_cost_limit = (
        parsed.max_cost_usd
        if parsed.max_cost_usd is not None
        else cfg_get(config, "limits", "max_cost_usd", None)
    )
    runtime.max_cost_usd = float(max_cost_limit) if max_cost_limit is not None else None
    runtime.test_delay = float(
        parsed.test_delay
        if parsed.test_delay is not None
        else cfg_get(config, "flags", "test_delay", 0.02)
    )

    return runtime


def build_job_config_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "ocr": args.ocr,
        "ocr_lang": args.ocr_lang,
        "ocr_page_min_chars": args.ocr_page_min_chars,
        "ocr_page_min_alpha_ratio": args.ocr_page_min_alpha_ratio,
        "ocr_page_max_replacement_ratio": args.ocr_page_max_replacement_ratio,
        "ocr_page_min_score": args.ocr_page_min_score,
        "ocr_auto_bad_page_ratio": args.ocr_auto_bad_page_ratio,
        "cleanup_diff_preview": args.cleanup_diff_preview,
        "cleanup_diff_max_lines": args.cleanup_diff_max_lines,
        "lang": args.lang,
        "voice_name": args.voice_name,
        "max_chars": args.max_chars,
        "speaking_rate": args.speaking_rate,
        "pitch": args.pitch,
        "skip_tts": args.skip_tts,
        "skip_merge": args.skip_merge,
        "ui": args.ui,
        "estimate_only": args.estimate_only,
        "test_run": args.test_run,
        "test_delay": args.test_delay,
        "test_write_placeholders": args.test_write_placeholders,
        "use_ssml": args.use_ssml,
        "dashboard": args.dashboard,
        "export_m4b": args.export_m4b,
        "confirm_before_spend": args.confirm_before_spend,
        "max_chars_per_run": args.max_chars_per_run,
        "max_cost_usd": args.max_cost_usd,
    }


def apply_manifest_defaults_for_resume(
    args: argparse.Namespace,
    raw_args: argparse.Namespace,
    manifest: dict[str, Any],
) -> argparse.Namespace:
    job_args = argparse.Namespace(**vars(args))
    cfg = manifest.get("config", {})
    if not isinstance(cfg, dict):
        return job_args

    if raw_args.ocr is None and "ocr" in cfg:
        job_args.ocr = str(cfg["ocr"])
    if raw_args.ocr_lang is None and "ocr_lang" in cfg:
        job_args.ocr_lang = str(cfg["ocr_lang"])
    if raw_args.ocr_page_min_chars is None and "ocr_page_min_chars" in cfg:
        job_args.ocr_page_min_chars = int(cfg["ocr_page_min_chars"])
    if raw_args.ocr_page_min_alpha_ratio is None and "ocr_page_min_alpha_ratio" in cfg:
        job_args.ocr_page_min_alpha_ratio = float(cfg["ocr_page_min_alpha_ratio"])
    if raw_args.ocr_page_max_replacement_ratio is None and "ocr_page_max_replacement_ratio" in cfg:
        job_args.ocr_page_max_replacement_ratio = float(cfg["ocr_page_max_replacement_ratio"])
    if raw_args.ocr_page_min_score is None and "ocr_page_min_score" in cfg:
        job_args.ocr_page_min_score = float(cfg["ocr_page_min_score"])
    if raw_args.ocr_auto_bad_page_ratio is None and "ocr_auto_bad_page_ratio" in cfg:
        job_args.ocr_auto_bad_page_ratio = float(cfg["ocr_auto_bad_page_ratio"])
    if raw_args.cleanup_diff_preview is None and "cleanup_diff_preview" in cfg:
        job_args.cleanup_diff_preview = bool(cfg["cleanup_diff_preview"])
    if raw_args.cleanup_diff_max_lines is None and "cleanup_diff_max_lines" in cfg:
        job_args.cleanup_diff_max_lines = int(cfg["cleanup_diff_max_lines"])
    if raw_args.lang is None and "lang" in cfg:
        job_args.lang = str(cfg["lang"])
    if raw_args.voice_name is None and "voice_name" in cfg:
        job_args.voice_name = str(cfg["voice_name"])
    if raw_args.max_chars is None and "max_chars" in cfg:
        job_args.max_chars = int(cfg["max_chars"])
    if raw_args.speaking_rate is None and "speaking_rate" in cfg:
        job_args.speaking_rate = float(cfg["speaking_rate"])
    if raw_args.pitch is None and "pitch" in cfg:
        job_args.pitch = float(cfg["pitch"])
    if raw_args.ui is None and "ui" in cfg:
        job_args.ui = str(cfg["ui"])
    if raw_args.use_ssml is None and "use_ssml" in cfg:
        job_args.use_ssml = bool(cfg["use_ssml"])
    if raw_args.dashboard is None and "dashboard" in cfg:
        job_args.dashboard = bool(cfg["dashboard"])
    if raw_args.export_m4b is None and "export_m4b" in cfg:
        job_args.export_m4b = bool(cfg["export_m4b"])
    if raw_args.confirm_before_spend is None and "confirm_before_spend" in cfg:
        job_args.confirm_before_spend = bool(cfg["confirm_before_spend"])
    if raw_args.max_chars_per_run is None and "max_chars_per_run" in cfg:
        raw_limit = cfg["max_chars_per_run"]
        job_args.max_chars_per_run = int(raw_limit) if raw_limit is not None else None
    if raw_args.max_cost_usd is None and "max_cost_usd" in cfg:
        raw_cost = cfg["max_cost_usd"]
        job_args.max_cost_usd = float(raw_cost) if raw_cost is not None else None

    return job_args


def assess_text_quality(text: str) -> TextQuality:
    chars = len(text)
    if chars == 0:
        return TextQuality(
            chars=0,
            alpha_ratio=0.0,
            non_ascii_ratio=0.0,
            replacement_ratio=1.0,
            whitespace_ratio=1.0,
            score=0.0,
            looks_good=False,
        )

    alpha = sum(1 for c in text if c.isalpha())
    non_ascii = sum(1 for c in text if ord(c) > 127)
    replacement = text.count("\ufffd")
    whitespace = sum(1 for c in text if c.isspace())

    alpha_ratio = alpha / chars
    non_ascii_ratio = non_ascii / chars
    replacement_ratio = replacement / chars
    whitespace_ratio = whitespace / chars

    # Score is intentionally simple and explainable.
    score = 0.0
    score += min(chars / 80000.0, 1.0) * 0.45
    score += min(alpha_ratio / 0.65, 1.0) * 0.45
    score += max(0.0, 1.0 - (replacement_ratio * 50.0)) * 0.1

    looks_good = chars >= 1800 and alpha_ratio >= 0.40 and replacement_ratio < 0.01

    return TextQuality(
        chars=chars,
        alpha_ratio=alpha_ratio,
        non_ascii_ratio=non_ascii_ratio,
        replacement_ratio=replacement_ratio,
        whitespace_ratio=whitespace_ratio,
        score=round(score, 4),
        looks_good=looks_good,
    )


def build_ocr_quality_thresholds(args: argparse.Namespace) -> OcrQualityThresholds:
    return OcrQualityThresholds(
        page_min_chars=int(args.ocr_page_min_chars),
        page_min_alpha_ratio=float(args.ocr_page_min_alpha_ratio),
        page_max_replacement_ratio=float(args.ocr_page_max_replacement_ratio),
        page_min_score=float(args.ocr_page_min_score),
        auto_bad_page_ratio=float(args.ocr_auto_bad_page_ratio),
        prefer_ocr_score_delta=0.04,
    )


def split_pages_with_empty(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    pages = normalized.split("\f")
    while len(pages) > 1 and not pages[-1].strip():
        pages.pop()
    return pages or [normalized]


def page_meets_ocr_threshold(quality: TextQuality, thresholds: OcrQualityThresholds) -> bool:
    if quality.chars == 0:
        return False
    return (
        quality.chars >= thresholds.page_min_chars
        and quality.alpha_ratio >= thresholds.page_min_alpha_ratio
        and quality.replacement_ratio <= thresholds.page_max_replacement_ratio
        and quality.score >= thresholds.page_min_score
    )


def summarize_page_qualities(
    pages: list[str],
    thresholds: OcrQualityThresholds,
) -> tuple[list[dict[str, Any]], list[int]]:
    qualities: list[dict[str, Any]] = []
    bad_page_indexes: list[int] = []
    for index, page in enumerate(pages, start=1):
        quality = assess_text_quality(page)
        passes = page_meets_ocr_threshold(quality, thresholds)
        if not passes:
            bad_page_indexes.append(index)
        qualities.append(
            {
                "page": index,
                "chars": quality.chars,
                "score": quality.score,
                "alpha_ratio": round(quality.alpha_ratio, 4),
                "replacement_ratio": round(quality.replacement_ratio, 6),
                "passes_threshold": passes,
            }
        )
    return qualities, bad_page_indexes


def pick_page_source(
    *,
    direct_page: str,
    ocr_page: str,
    thresholds: OcrQualityThresholds,
    force_ocr: bool,
) -> tuple[str, str, dict[str, Any]]:
    direct_quality = assess_text_quality(direct_page)
    ocr_quality = assess_text_quality(ocr_page)
    direct_passes = page_meets_ocr_threshold(direct_quality, thresholds)
    ocr_passes = page_meets_ocr_threshold(ocr_quality, thresholds)

    decision: dict[str, Any] = {
        "direct_quality": asdict(direct_quality),
        "ocr_quality": asdict(ocr_quality),
        "direct_passes": direct_passes,
        "ocr_passes": ocr_passes,
    }

    if force_ocr:
        decision["reason"] = "forced_ocr_mode"
        return "ocr", ocr_page, decision

    if direct_page.strip() and not ocr_page.strip():
        decision["reason"] = "ocr_empty_keep_direct"
        return "direct", direct_page, decision
    if ocr_page.strip() and not direct_page.strip():
        decision["reason"] = "direct_empty_use_ocr"
        return "ocr", ocr_page, decision

    if direct_passes and not ocr_passes:
        decision["reason"] = "direct_passes_only"
        return "direct", direct_page, decision
    if ocr_passes and not direct_passes:
        decision["reason"] = "ocr_passes_only"
        return "ocr", ocr_page, decision

    score_delta = ocr_quality.score - direct_quality.score
    if score_delta >= thresholds.prefer_ocr_score_delta:
        decision["reason"] = "ocr_better_score"
        return "ocr", ocr_page, decision
    if score_delta <= -thresholds.prefer_ocr_score_delta:
        decision["reason"] = "direct_better_score"
        return "direct", direct_page, decision

    if ocr_quality.replacement_ratio + 0.001 < direct_quality.replacement_ratio:
        decision["reason"] = "ocr_cleaner_replacement"
        return "ocr", ocr_page, decision
    if direct_quality.replacement_ratio + 0.001 < ocr_quality.replacement_ratio:
        decision["reason"] = "direct_cleaner_replacement"
        return "direct", direct_page, decision

    if ocr_quality.chars > direct_quality.chars + 20:
        decision["reason"] = "ocr_more_text"
        return "ocr", ocr_page, decision

    decision["reason"] = "default_keep_direct"
    return "direct", direct_page, decision


def split_pages(text: str) -> list[str]:
    pages = [p for p in re.split(r"\f+", text) if p.strip()]
    return pages or [text]


def normalized_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip().lower()


def detect_repeated_margins(pages: list[str]) -> tuple[set[str], set[str]]:
    top_counts: Counter[str] = Counter()
    bottom_counts: Counter[str] = Counter()

    for page in pages:
        lines = [ln.strip() for ln in page.splitlines() if ln.strip()]
        if not lines:
            continue
        top_counts[normalized_line(lines[0])] += 1
        bottom_counts[normalized_line(lines[-1])] += 1

    threshold = max(3, int(len(pages) * 0.20))

    top_repeated = {
        line
        for line, count in top_counts.items()
        if count >= threshold and len(line) <= 120
    }
    bottom_repeated = {
        line
        for line, count in bottom_counts.items()
        if count >= threshold and len(line) <= 120
    }
    return top_repeated, bottom_repeated


def looks_like_noise_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False

    patterns = (
        r"^[-–—]?\s*\d+\s*[-–—]?$",  # page numbers
        r"^(page|oldal)\s+\d+$",
        r"^https?://\S+$",
        r"^www\.\S+$",
        r"^\.{3,}\s*\d+$",  # TOC fragments
        r"^.{1,80}\.{3,}\s*\d+$",  # TOC dotted leader line
    )

    lower = s.lower()
    if any(re.match(p, lower) for p in patterns):
        return True

    if len(s) <= 3 and all(ch in "*-_~" for ch in s):
        return True

    return False


def clean_for_audiobook(raw_text: str) -> str:
    pages = split_pages(raw_text.replace("\r\n", "\n").replace("\r", "\n"))
    repeated_top, repeated_bottom = detect_repeated_margins(pages)

    cleaned_pages: list[str] = []

    for page in pages:
        lines = page.splitlines()
        kept: list[str] = []

        for line in lines:
            norm = normalized_line(line)
            if norm and (norm in repeated_top or norm in repeated_bottom):
                continue
            if looks_like_noise_line(line):
                continue
            kept.append(line.rstrip())

        non_empty = [ln for ln in kept if ln.strip()]
        toc_like = sum(1 for ln in non_empty if re.search(r"\.{3,}\s*\d+\s*$", ln))
        if non_empty and toc_like >= 4 and toc_like >= int(len(non_empty) * 0.5):
            # Drop index/table-of-contents-like pages.
            continue

        cleaned_pages.append("\n".join(kept).strip())

    text = "\n\n".join(page for page in cleaned_pages if page)

    text = text.replace("\u00ad", "")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    text = re.sub(r"\s*\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def write_cleanup_diff_preview(
    *,
    raw_text: str,
    cleaned_text: str,
    output_path: Path,
    max_lines: int,
) -> dict[str, Any]:
    raw_lines = raw_text.splitlines()
    cleaned_lines = cleaned_text.splitlines()
    diff_lines = list(
        difflib.unified_diff(
            raw_lines,
            cleaned_lines,
            fromfile="raw.txt",
            tofile="clean_for_tts.txt",
            n=1,
            lineterm="",
        )
    )
    preview_lines = diff_lines[:max_lines]
    truncated_lines = max(0, len(diff_lines) - len(preview_lines))
    added_lines = sum(
        1 for line in diff_lines if line.startswith("+") and not line.startswith("+++")
    )
    removed_lines = sum(
        1 for line in diff_lines if line.startswith("-") and not line.startswith("---")
    )

    header = [
        "# Cleanup Diff Preview",
        (
            f"raw_chars={len(raw_text)} clean_chars={len(cleaned_text)} "
            f"removed_chars={max(0, len(raw_text) - len(cleaned_text))}"
        ),
        f"raw_lines={len(raw_lines)} clean_lines={len(cleaned_lines)}",
        (
            f"diff_lines_total={len(diff_lines)} "
            f"preview_lines={len(preview_lines)} added={added_lines} removed={removed_lines}"
        ),
        "",
    ]

    body = preview_lines
    if not body:
        body = ["(No line-level differences detected)"]
    if truncated_lines > 0:
        body.extend(["", f"... truncated {truncated_lines} additional diff lines ..."])

    output_path.write_text("\n".join(header + body) + "\n")
    return {
        "path": str(output_path),
        "diff_lines_total": len(diff_lines),
        "preview_lines": len(preview_lines),
        "added_lines": added_lines,
        "removed_lines": removed_lines,
        "truncated": truncated_lines > 0,
        "truncated_lines": truncated_lines,
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def read_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def set_manifest_status(
    manifest_path: Path,
    manifest: dict[str, Any],
    status: str,
    error: str | None = None,
) -> None:
    manifest["status"] = status
    manifest["updated_at"] = now_iso()
    if error is None:
        manifest.pop("error", None)
    else:
        manifest["error"] = error
    write_manifest(manifest_path, manifest)


def set_step(
    manifest_path: Path,
    manifest: dict[str, Any],
    step: str,
    status: str,
    **fields: Any,
) -> None:
    steps = manifest.setdefault("steps", {})
    entry = dict(steps.get(step, {}))
    entry.update(fields)
    entry["status"] = status
    entry["updated_at"] = now_iso()
    if status != "failed":
        entry.pop("error", None)
    steps[step] = entry
    manifest["updated_at"] = now_iso()
    write_manifest(manifest_path, manifest)


def step_status(manifest: dict[str, Any], step: str) -> str | None:
    entry = manifest.get("steps", {}).get(step, {})
    if isinstance(entry, dict):
        return entry.get("status")
    return None


def extract_text_from_pdf(
    pdf_path: Path,
    job_dir: Path,
    ocr_mode: str,
    ocr_lang: str,
    ocr_thresholds: OcrQualityThresholds,
) -> tuple[str, dict[str, Any]]:
    pdftotext = require_command("pdftotext")

    direct_txt = job_dir / "raw_direct.txt"
    run_command([pdftotext, "-layout", str(pdf_path), str(direct_txt)], "pdftotext extraction")
    direct_text = direct_txt.read_text(errors="ignore")
    direct_quality = assess_text_quality(direct_text)
    direct_pages = split_pages_with_empty(direct_text)
    direct_page_qualities, direct_bad_pages = summarize_page_qualities(direct_pages, ocr_thresholds)
    direct_bad_ratio = len(direct_bad_pages) / max(1, len(direct_pages))
    bad_page_trigger_count = max(1, int(len(direct_pages) * ocr_thresholds.auto_bad_page_ratio))

    extraction_info: dict[str, Any] = {
        "direct_txt": str(direct_txt),
        "direct_quality": asdict(direct_quality),
        "direct_looks_good": bool(direct_quality.looks_good),
        "ocr_mode": ocr_mode,
        "ocr_thresholds": asdict(ocr_thresholds),
        "direct_page_count": len(direct_pages),
        "direct_bad_pages": len(direct_bad_pages),
        "direct_bad_page_ratio": round(direct_bad_ratio, 4),
        "direct_bad_page_examples": direct_bad_pages[:25],
        "direct_page_qualities_json": str(job_dir / "direct_page_qualities.json"),
        "ocr_auto_bad_page_trigger_count": bad_page_trigger_count,
    }
    (job_dir / "direct_page_qualities.json").write_text(
        json.dumps({"pages": direct_page_qualities}, indent=2, ensure_ascii=False) + "\n"
    )

    selected_txt = direct_txt

    should_ocr = False
    trigger_reasons: list[str] = []
    direct_overall_passes = page_meets_ocr_threshold(direct_quality, ocr_thresholds)
    extraction_info["direct_overall_passes_threshold"] = bool(direct_overall_passes)
    if ocr_mode == "always":
        should_ocr = True
        trigger_reasons.append("forced_by_mode")
    elif ocr_mode == "auto":
        if not direct_overall_passes:
            should_ocr = True
            trigger_reasons.append("direct_overall_below_threshold")
        if len(direct_bad_pages) >= bad_page_trigger_count:
            should_ocr = True
            trigger_reasons.append("bad_pages_exceed_threshold")

    if should_ocr:
        ocrmypdf = require_command("ocrmypdf")
        ocr_pdf = job_dir / "ocr.pdf"
        ocr_txt = job_dir / "raw_ocr.txt"

        ocr_flags = ["--language", ocr_lang]
        ocr_flags.insert(0, "--force-ocr")

        run_command([ocrmypdf, *ocr_flags, str(pdf_path), str(ocr_pdf)], "ocrmypdf")
        run_command([pdftotext, "-layout", str(ocr_pdf), str(ocr_txt)], "pdftotext on OCR PDF")

        ocr_text = ocr_txt.read_text(errors="ignore")
        ocr_quality = assess_text_quality(ocr_text)
        ocr_pages = split_pages_with_empty(ocr_text)
        ocr_page_qualities, ocr_bad_pages = summarize_page_qualities(ocr_pages, ocr_thresholds)
        ocr_bad_ratio = len(ocr_bad_pages) / max(1, len(ocr_pages))

        extraction_info["ocr_pdf"] = str(ocr_pdf)
        extraction_info["ocr_txt"] = str(ocr_txt)
        extraction_info["ocr_quality"] = asdict(ocr_quality)
        extraction_info["ocr_page_count"] = len(ocr_pages)
        extraction_info["ocr_bad_pages"] = len(ocr_bad_pages)
        extraction_info["ocr_bad_page_ratio"] = round(ocr_bad_ratio, 4)
        extraction_info["ocr_bad_page_examples"] = ocr_bad_pages[:25]
        extraction_info["ocr_trigger_reasons"] = trigger_reasons
        ocr_page_qualities_json = job_dir / "ocr_page_qualities.json"
        ocr_page_qualities_json.write_text(
            json.dumps({"pages": ocr_page_qualities}, indent=2, ensure_ascii=False) + "\n"
        )
        extraction_info["ocr_page_quality_json"] = str(ocr_page_qualities_json)

        if ocr_mode == "always":
            selected_txt = ocr_txt
            extraction_info["ocr_selection_mode"] = "ocr_full"
            extraction_info["selected_source_summary"] = {
                "ocr_pages": len(ocr_pages),
                "direct_pages": 0,
            }
        else:
            page_count = max(len(direct_pages), len(ocr_pages))
            merged_pages: list[str] = []
            selected_ocr_pages = 0
            selected_direct_pages = 0
            decision_rows: list[dict[str, Any]] = []

            for index in range(page_count):
                direct_page = direct_pages[index] if index < len(direct_pages) else ""
                ocr_page = ocr_pages[index] if index < len(ocr_pages) else ""
                source, selected_page, decision = pick_page_source(
                    direct_page=direct_page,
                    ocr_page=ocr_page,
                    thresholds=ocr_thresholds,
                    force_ocr=False,
                )
                merged_pages.append(selected_page)
                if source == "ocr":
                    selected_ocr_pages += 1
                else:
                    selected_direct_pages += 1
                decision_rows.append(
                    {
                        "page": index + 1,
                        "selected": source,
                        "reason": decision.get("reason"),
                        "direct_score": decision["direct_quality"]["score"],
                        "ocr_score": decision["ocr_quality"]["score"],
                        "direct_chars": decision["direct_quality"]["chars"],
                        "ocr_chars": decision["ocr_quality"]["chars"],
                        "direct_passes": decision["direct_passes"],
                        "ocr_passes": decision["ocr_passes"],
                    }
                )

            selected_text = "\f".join(merged_pages)
            selected_txt = job_dir / "raw_selected.txt"
            selected_txt.write_text(selected_text)

            ocr_decisions_path = job_dir / "ocr_page_decisions.json"
            ocr_decisions_path.write_text(
                json.dumps(
                    {
                        "thresholds": asdict(ocr_thresholds),
                        "direct_pages": len(direct_pages),
                        "ocr_pages": len(ocr_pages),
                        "selected_ocr_pages": selected_ocr_pages,
                        "selected_direct_pages": selected_direct_pages,
                        "decisions": decision_rows,
                        "direct_page_qualities": direct_page_qualities,
                        "ocr_page_qualities": ocr_page_qualities,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n"
            )
            extraction_info["ocr_page_decisions_json"] = str(ocr_decisions_path)
            extraction_info["selected_source_summary"] = {
                "ocr_pages": selected_ocr_pages,
                "direct_pages": selected_direct_pages,
            }
            if selected_ocr_pages == 0:
                extraction_info["ocr_selection_mode"] = "direct_full"
            elif selected_direct_pages == 0:
                extraction_info["ocr_selection_mode"] = "ocr_full"
            else:
                extraction_info["ocr_selection_mode"] = "hybrid_page"
    else:
        extraction_info["ocr_trigger_reasons"] = trigger_reasons
        extraction_info["ocr_selection_mode"] = "direct_full"
        extraction_info["selected_source_summary"] = {
            "ocr_pages": 0,
            "direct_pages": len(direct_pages),
        }

    extraction_info["ocr_triggered"] = should_ocr

    extraction_info["selected_txt"] = str(selected_txt)
    selected_text = selected_txt.read_text(errors="ignore")
    return selected_text, extraction_info


def merge_mp3_chunks(chunks_dir: Path, output_mp3: Path) -> dict[str, Any]:
    mp3_files = sorted(chunks_dir.glob("*.mp3"))
    if not mp3_files:
        return {"merged": False, "reason": "no chunk mp3 files found"}

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"merged": False, "reason": "ffmpeg not installed"}

    concat_list = chunks_dir / "concat_list.txt"
    with concat_list.open("w", encoding="utf-8") as fh:
        for path in mp3_files:
            escaped = str(path.resolve()).replace("'", "'\\''")
            fh.write(f"file '{escaped}'\n")

    run_command(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            str(output_mp3),
        ],
        "ffmpeg concat",
    )

    return {
        "merged": True,
        "chunks": len(mp3_files),
        "concat_list": str(concat_list),
        "output_mp3": str(output_mp3),
    }


def ffprobe_duration_ms(audio_file: Path) -> int:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe not installed (required for chaptered m4b export)")

    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            str(audio_file),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    raw = result.stdout.strip()
    duration_s = float(raw) if raw else 0.0
    return max(1, int(duration_s * 1000))


def load_chapter_ranges(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return []
    chapters = data.get("chapters", [])
    if not isinstance(chapters, list):
        return []
    ranges: list[dict[str, Any]] = []
    for item in chapters:
        if not isinstance(item, dict):
            continue
        try:
            start_chunk = int(item.get("start_chunk"))
            end_chunk = int(item.get("end_chunk"))
        except Exception:  # noqa: BLE001
            continue
        if start_chunk < 0 or end_chunk < start_chunk:
            continue
        title = str(item.get("title", f"Chapter {len(ranges) + 1}")).strip() or f"Chapter {len(ranges) + 1}"
        ranges.append(
            {
                "title": title,
                "start_chunk": start_chunk,
                "end_chunk": end_chunk,
            }
        )
    return ranges


def export_chaptered_m4b(
    *,
    chunks_dir: Path,
    merged_mp3: Path,
    output_m4b: Path,
    chapter_map_path: Path | None,
) -> dict[str, Any]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"m4b_exported": False, "reason": "ffmpeg not installed"}
    if not merged_mp3.exists():
        return {"m4b_exported": False, "reason": "merged mp3 missing"}

    mp3_files = sorted(chunks_dir.glob("*.mp3"))
    if not mp3_files:
        return {"m4b_exported": False, "reason": "no chunk mp3 files found"}

    chapters = load_chapter_ranges(chapter_map_path) if chapter_map_path is not None else []
    if not chapters:
        chapters = [{"title": "Chapter 1", "start_chunk": 0, "end_chunk": len(mp3_files) - 1}]

    durations_ms: list[int] = []
    for chunk_path in mp3_files:
        durations_ms.append(ffprobe_duration_ms(chunk_path))

    cumulative: list[int] = [0]
    for duration in durations_ms:
        cumulative.append(cumulative[-1] + duration)

    metadata_file = chunks_dir / "chapters.ffmeta"
    lines = [";FFMETADATA1", f"title={output_m4b.stem}"]
    for item in chapters:
        start_chunk = min(max(int(item["start_chunk"]), 0), len(mp3_files) - 1)
        end_chunk = min(max(int(item["end_chunk"]), start_chunk), len(mp3_files) - 1)
        start_ms = cumulative[start_chunk]
        end_ms = cumulative[end_chunk + 1]
        if end_ms <= start_ms:
            end_ms = start_ms + 1
        title = str(item["title"]).replace("\n", " ").strip() or "Chapter"

        lines.extend(
            [
                "[CHAPTER]",
                "TIMEBASE=1/1000",
                f"START={start_ms}",
                f"END={end_ms}",
                f"title={title}",
            ]
        )

    metadata_file.write_text("\n".join(lines) + "\n")
    run_command(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(merged_mp3),
            "-i",
            str(metadata_file),
            "-map_metadata",
            "1",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(output_m4b),
        ],
        "ffmpeg m4b export",
    )
    return {
        "m4b_exported": True,
        "output_m4b": str(output_m4b),
        "chapter_count": len(chapters),
        "chapter_metadata": str(metadata_file),
    }


def discover_inputs_allow_empty(input_dir: Path) -> list[Path]:
    if not input_dir.exists():
        raise RuntimeError(f"Input path does not exist: {input_dir}")
    if not input_dir.is_dir():
        raise RuntimeError(f"Watch mode requires an input directory, got: {input_dir}")

    return [
        p
        for p in sorted(input_dir.iterdir(), key=lambda item: item.name.lower())
        if p.is_file() and p.suffix.lower() in SUPPORTED_INPUT_SUFFIXES
    ]


def build_output_stem_map(paths: list[Path]) -> dict[str, str]:
    base_counts = Counter(slugify(path.stem) for path in paths)
    mapping: dict[str, str] = {}

    for path in paths:
        resolved = str(path.resolve())
        base = slugify(path.stem)
        if base_counts[base] == 1:
            mapping[resolved] = base
            continue

        suffix = path.suffix.lower().lstrip(".") or "file"
        mapping[resolved] = slugify(f"{path.stem}-{suffix}")

    return mapping


def build_job_record(manifest_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    steps_obj = manifest.get("steps", {})
    steps: dict[str, str] = {}
    if isinstance(steps_obj, dict):
        for step, value in steps_obj.items():
            if isinstance(value, dict):
                steps[str(step)] = str(value.get("status", "unknown"))
            else:
                steps[str(step)] = "unknown"

    return {
        "manifest_path": str(manifest_path),
        "input": manifest.get("input"),
        "status": manifest.get("status"),
        "tmp_dir": manifest.get("tmp_dir"),
        "final_output_mp3": manifest.get("final_output_mp3"),
        "final_output_m4b": manifest.get("final_output_m4b"),
        "error": manifest.get("error"),
        "steps": steps,
        "updated_at": manifest.get("updated_at"),
    }


def write_run_report(
    work_dir: Path,
    *,
    mode: str,
    args: argparse.Namespace,
    jobs: list[dict[str, Any]],
    failures: list[tuple[str, str]],
) -> Path:
    reports_dir = work_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    generated_at = now_iso()
    report = {
        "schema_version": 1,
        "generated_at": generated_at,
        "mode": mode,
        "args": {
            "input": str(args.input),
            "work_dir": str(args.work_dir),
            "out_dir": str(args.out_dir),
            "resume": bool(getattr(args, "resume", False)),
            "resume_job": str(args.resume_job) if getattr(args, "resume_job", None) else None,
            "test_run": bool(args.test_run),
            "estimate_only": bool(args.estimate_only),
            "watch": bool(getattr(args, "watch", False)),
            "ocr": str(args.ocr),
            "ocr_lang": str(args.ocr_lang),
            "ocr_page_min_chars": int(args.ocr_page_min_chars),
            "ocr_page_min_alpha_ratio": float(args.ocr_page_min_alpha_ratio),
            "ocr_page_max_replacement_ratio": float(args.ocr_page_max_replacement_ratio),
            "ocr_page_min_score": float(args.ocr_page_min_score),
            "ocr_auto_bad_page_ratio": float(args.ocr_auto_bad_page_ratio),
            "use_ssml": bool(args.use_ssml),
            "cleanup_diff_preview": bool(args.cleanup_diff_preview),
            "cleanup_diff_max_lines": int(args.cleanup_diff_max_lines),
            "dashboard": bool(args.dashboard),
            "export_m4b": bool(args.export_m4b),
            "max_chars_per_run": args.max_chars_per_run,
            "max_cost_usd": args.max_cost_usd,
            "confirm_before_spend": bool(args.confirm_before_spend),
        },
        "summary": {
            "attempted_jobs": len(jobs) + len(failures),
            "recorded_jobs": len(jobs),
            "completed_jobs": sum(1 for job in jobs if str(job.get("status")) == "completed"),
            "failed_jobs": len(failures),
        },
        "jobs": jobs,
        "failures": [{"target": target, "error": error} for target, error in failures],
    }

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    report_path = reports_dir / f"run-{timestamp}.json"
    report_text = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    report_path.write_text(report_text)
    (reports_dir / "latest_run.json").write_text(report_text)
    print(f"Run report: {report_path}")
    return report_path


def run_doctor(args: argparse.Namespace) -> bool:
    checks: list[DoctorCheck] = []

    def add(name: str, ok: bool, detail: str, required: bool = True) -> None:
        checks.append(
            DoctorCheck(
                name=name,
                ok=ok,
                required=required,
                detail=detail,
            )
        )

    python_ok = sys.version_info >= (3, 10)
    add(
        "Python",
        python_ok,
        f"{sys.version.split()[0]} (requires 3.10+)",
        required=True,
    )

    required_commands = [
        ("pdftotext", "required for PDF extraction"),
        ("ffmpeg", "required for final MP3 merge"),
    ]
    if args.export_m4b:
        required_commands.append(("ffprobe", "required for chaptered m4b export"))
    for cmd, note in required_commands:
        cmd_path = shutil.which(cmd)
        add(cmd, cmd_path is not None, note if cmd_path is None else cmd_path, required=True)

    optional_commands = [("ocrmypdf", "needed when OCR runs"), ("tesseract", "used by ocrmypdf")]
    for cmd, note in optional_commands:
        cmd_path = shutil.which(cmd)
        add(cmd, cmd_path is not None, note if cmd_path is None else cmd_path, required=False)

    tts_needed = not args.skip_tts and not args.estimate_only and not args.test_run
    try:
        import google.cloud.texttospeech as _  # noqa: F401
        tts_module_ok = True
    except Exception:  # noqa: BLE001
        tts_module_ok = False
    add(
        "google-cloud-texttospeech",
        tts_module_ok,
        "pip install google-cloud-texttospeech",
        required=tts_needed,
    )

    creds_env = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    creds_ok = False
    creds_detail = "Set GOOGLE_APPLICATION_CREDENTIALS or gcloud ADC credentials"

    if creds_env:
        creds_path = Path(creds_env).expanduser()
        if creds_path.exists():
            creds_ok = True
            creds_detail = f"{creds_path.resolve()} (GOOGLE_APPLICATION_CREDENTIALS)"
        else:
            creds_detail = f"{creds_env} (GOOGLE_APPLICATION_CREDENTIALS file not found)"
    else:
        adc_path = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
        if adc_path.exists():
            try:
                adc_obj = json.loads(adc_path.read_text())
            except Exception as exc:  # noqa: BLE001
                creds_ok = False
                creds_detail = f"{adc_path} (invalid JSON: {exc})"
            else:
                adc_type = str(adc_obj.get("type", "unknown"))
                quota_project = str(adc_obj.get("quota_project_id", "")).strip()
                if adc_type == "authorized_user" and not quota_project:
                    creds_ok = False
                    creds_detail = (
                        f"{adc_path} (authorized_user without quota_project_id; "
                        "run: gcloud auth application-default set-quota-project <PROJECT_ID>)"
                    )
                else:
                    creds_ok = True
                    qp = quota_project or "none"
                    creds_detail = f"{adc_path} ({adc_type}, quota_project_id={qp})"
    add("Google credentials", creds_ok, creds_detail, required=tts_needed)

    print("\n=== Doctor ===")
    failures = 0
    for check in checks:
        mark = "OK" if check.ok else ("WARN" if not check.required else "FAIL")
        print(f"[{mark}] {check.name}: {check.detail}")
        if check.required and not check.ok:
            failures += 1

    if failures:
        print(f"Doctor failed: {failures} required check(s) failed.")
        return False

    print("Doctor passed.")
    return True


def discover_inputs(input_path: Path) -> list[Path]:
    if not input_path.exists():
        raise RuntimeError(f"Input path does not exist: {input_path}")

    if input_path.is_file():
        if input_path.suffix.lower() not in SUPPORTED_INPUT_SUFFIXES:
            raise RuntimeError(f"Unsupported input file type: {input_path.suffix}")
        return [input_path]

    if input_path.is_dir():
        files = [
            p
            for p in sorted(input_path.iterdir(), key=lambda item: item.name.lower())
            if p.is_file() and p.suffix.lower() in SUPPORTED_INPUT_SUFFIXES
        ]
        if not files:
            supported = ", ".join(SUPPORTED_INPUT_SUFFIXES)
            raise RuntimeError(f"No supported files ({supported}) found in: {input_path}")
        return files

    raise RuntimeError(f"Input path must be a file or directory: {input_path}")


def unique_stem(base_stem: str, used_stems: dict[str, int]) -> str:
    count = used_stems.get(base_stem, 0) + 1
    used_stems[base_stem] = count
    if count == 1:
        return base_stem
    return f"{base_stem}-{count}"


def create_new_manifest(
    args: argparse.Namespace,
    input_path: Path,
    job_tmp_dir: Path,
    out_dir: Path,
    output_stem: str,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "status": "created",
        "input": str(input_path),
        "tmp_dir": str(job_tmp_dir),
        "out_dir": str(out_dir),
        "final_output_mp3": str(out_dir / f"{output_stem}.mp3"),
        "final_output_m4b": str(out_dir / f"{output_stem}.m4b"),
        "config": build_job_config_snapshot(args),
        "steps": {},
    }


def ensure_manifest_basics(
    manifest: dict[str, Any],
    args: argparse.Namespace,
    input_path: Path,
    job_tmp_dir: Path,
    out_dir: Path,
    output_stem: str,
) -> None:
    manifest.setdefault("schema_version", 2)
    manifest.setdefault("created_at", now_iso())
    manifest.setdefault("updated_at", now_iso())
    manifest.setdefault("status", "created")
    manifest.setdefault("input", str(input_path))
    manifest.setdefault("tmp_dir", str(job_tmp_dir))
    manifest.setdefault("out_dir", str(out_dir))
    manifest.setdefault("final_output_mp3", str(out_dir / f"{output_stem}.mp3"))
    manifest.setdefault("final_output_m4b", str(out_dir / f"{output_stem}.m4b"))
    manifest.setdefault("config", build_job_config_snapshot(args))
    manifest.setdefault("steps", {})


def run_job(
    args: argparse.Namespace,
    input_path: Path,
    job_tmp_dir: Path,
    out_dir: Path,
    output_stem: str,
    manifest_path: Path,
    manifest: dict[str, Any],
    resume: bool,
) -> dict[str, Any]:
    ensure_manifest_basics(manifest, args, input_path, job_tmp_dir, out_dir, output_stem)

    raw_txt = job_tmp_dir / "raw.txt"
    clean_txt = job_tmp_dir / "clean_for_tts.txt"
    clean_diff_preview_txt = job_tmp_dir / "clean_diff_preview.patch"
    chapter_map_json = job_tmp_dir / "chapter_map.json"
    chunks_dir = job_tmp_dir / "chunks"
    final_output_mp3 = out_dir / f"{output_stem}.mp3"
    final_output_m4b = out_dir / f"{output_stem}.m4b"
    ocr_thresholds = build_ocr_quality_thresholds(args)

    print(f"Input: {input_path}")
    print(f"Tmp dir: {job_tmp_dir}")
    print(f"Final out: {final_output_mp3}")

    current_step: str | None = None

    try:
        set_manifest_status(manifest_path, manifest, "in_progress")

        # Step 1: extract
        current_step = "extract"
        if resume and step_status(manifest, "extract") == "completed" and raw_txt.exists():
            print("Resume: extraction already completed.")
        else:
            set_step(manifest_path, manifest, "extract", "in_progress")

            if input_path.suffix.lower() == ".pdf":
                raw_text, extraction_info = extract_text_from_pdf(
                    pdf_path=input_path,
                    job_dir=job_tmp_dir,
                    ocr_mode=args.ocr,
                    ocr_lang=args.ocr_lang,
                    ocr_thresholds=ocr_thresholds,
                )
                raw_txt.write_text(raw_text)
                set_step(
                    manifest_path,
                    manifest,
                    "extract",
                    "completed",
                    raw_txt=str(raw_txt),
                    **extraction_info,
                )
            elif input_path.suffix.lower() == ".txt":
                raw_text = input_path.read_text(errors="ignore")
                selected_txt = job_tmp_dir / "raw_input.txt"
                selected_txt.write_text(raw_text)
                raw_txt.write_text(raw_text)
                set_step(
                    manifest_path,
                    manifest,
                    "extract",
                    "completed",
                    mode="txt_input",
                    selected_txt=str(selected_txt),
                    raw_txt=str(raw_txt),
                    quality=asdict(assess_text_quality(raw_text)),
                )
            else:
                raise RuntimeError("Unsupported input type. Use .pdf or .txt")

        # Step 2: clean
        current_step = "clean"
        if resume and step_status(manifest, "clean") == "completed" and clean_txt.exists():
            print("Resume: clean step already completed.")
        else:
            if not raw_txt.exists():
                raise RuntimeError(f"Missing raw text for clean step: {raw_txt}")

            set_step(manifest_path, manifest, "clean", "in_progress")
            raw_text = raw_txt.read_text(errors="ignore")
            cleaned = clean_for_audiobook(raw_text)
            clean_txt.write_text(cleaned)
            clean_diff_preview: dict[str, Any] | None = None
            if args.cleanup_diff_preview:
                clean_diff_preview = write_cleanup_diff_preview(
                    raw_text=raw_text,
                    cleaned_text=cleaned,
                    output_path=clean_diff_preview_txt,
                    max_lines=args.cleanup_diff_max_lines,
                )
            set_step(
                manifest_path,
                manifest,
                "clean",
                "completed",
                raw_txt=str(raw_txt),
                clean_txt=str(clean_txt),
                raw_chars=len(raw_text),
                clean_chars=len(cleaned),
                removed_chars=len(raw_text) - len(cleaned),
                clean_quality=asdict(assess_text_quality(cleaned)),
                cleanup_diff_preview=clean_diff_preview,
                cleanup_diff_preview_enabled=bool(args.cleanup_diff_preview),
            )

        print(f"Raw text: {raw_txt}")
        print(f"Cleaned text: {clean_txt}")
        if args.cleanup_diff_preview and clean_diff_preview_txt.exists():
            print(f"Cleanup diff preview: {clean_diff_preview_txt}")

        # Step 3: tts
        current_step = "tts"
        if args.skip_tts:
            set_step(
                manifest_path,
                manifest,
                "tts",
                "skipped",
                reason="skip-tts flag",
            )
            set_step(
                manifest_path,
                manifest,
                "merge",
                "skipped",
                reason="skip-tts implies no merge",
            )
            set_manifest_status(manifest_path, manifest, "completed")
            print("TTS skipped (--skip-tts).")
            return manifest

        if (
            resume
            and step_status(manifest, "tts") == "completed"
            and chunks_dir.exists()
            and not args.estimate_only
            and not args.test_run
        ):
            print("Resume: TTS already completed.")
        else:
            set_step(manifest_path, manifest, "tts", "in_progress")
            tts_summary = make_mp3.run_tts(
                input_txt=clean_txt,
                out_dir=chunks_dir,
                lang=args.lang,
                voice_name=args.voice_name,
                max_chars=args.max_chars,
                speaking_rate=args.speaking_rate,
                pitch=args.pitch,
                write_cleaned=job_tmp_dir / "tts_cleaned_effective.txt",
                write_chapter_map=chapter_map_json,
                ui_mode=args.ui,
                estimate_only=args.estimate_only,
                test_run=args.test_run,
                test_delay=args.test_delay,
                test_write_placeholders=args.test_write_placeholders,
                use_ssml=args.use_ssml,
                max_chars_per_run=args.max_chars_per_run,
                max_cost_usd=args.max_cost_usd,
                confirm_before_spend=args.confirm_before_spend,
                dashboard=args.dashboard,
            )

            if tts_summary.get("estimate_only"):
                set_step(
                    manifest_path,
                    manifest,
                    "tts",
                    "skipped",
                    reason="estimate-only mode",
                    summary=tts_summary,
                    chunks_dir=str(chunks_dir),
                )
            elif tts_summary.get("test_run"):
                set_step(
                    manifest_path,
                    manifest,
                    "tts",
                    "skipped",
                    reason="test-run mode",
                    summary=tts_summary,
                    chunks_dir=str(chunks_dir),
                )
            else:
                set_step(
                    manifest_path,
                    manifest,
                    "tts",
                    "completed",
                    summary=tts_summary,
                    chunks_dir=str(chunks_dir),
                )

        # Step 4: merge
        current_step = "merge"
        if args.estimate_only:
            set_step(
                manifest_path,
                manifest,
                "merge",
                "skipped",
                reason="estimate-only mode",
            )
            set_manifest_status(manifest_path, manifest, "completed")
            print("Estimate-only mode enabled; no TTS requests were sent.")
            return manifest

        if args.test_run:
            set_step(
                manifest_path,
                manifest,
                "merge",
                "skipped",
                reason="test-run mode (simulated outputs)",
            )
            set_manifest_status(manifest_path, manifest, "completed")
            print("Test run mode enabled; merge skipped because outputs are simulated.")
            return manifest

        if args.skip_merge:
            set_step(
                manifest_path,
                manifest,
                "merge",
                "skipped",
                reason="skip-merge flag",
            )
            set_manifest_status(manifest_path, manifest, "completed")
            print("MP3 merge skipped (--skip-merge).")
            return manifest

        if resume and step_status(manifest, "merge") == "completed" and final_output_mp3.exists():
            print("Resume: merge already completed.")
        else:
            set_step(manifest_path, manifest, "merge", "in_progress")
            merge_summary = merge_mp3_chunks(chunks_dir, final_output_mp3)
            if merge_summary.get("merged"):
                m4b_summary: dict[str, Any] = {"m4b_exported": False, "reason": "export disabled"}
                if args.export_m4b:
                    m4b_summary = export_chaptered_m4b(
                        chunks_dir=chunks_dir,
                        merged_mp3=final_output_mp3,
                        output_m4b=final_output_m4b,
                        chapter_map_path=chapter_map_json,
                    )
                set_step(
                    manifest_path,
                    manifest,
                    "merge",
                    "completed",
                    **merge_summary,
                    m4b_summary=m4b_summary,
                )
                print(f"Merged audiobook: {final_output_mp3}")
                if m4b_summary.get("m4b_exported"):
                    print(f"Chaptered audiobook: {final_output_m4b}")
                elif args.export_m4b:
                    print(f"M4B export skipped: {m4b_summary.get('reason', 'unknown reason')}")
            else:
                set_step(
                    manifest_path,
                    manifest,
                    "merge",
                    "skipped",
                    reason=merge_summary.get("reason", "merge not completed"),
                    **merge_summary,
                )
                print(f"Merge skipped: {merge_summary.get('reason', 'unknown reason')}")

        set_manifest_status(manifest_path, manifest, "completed")
        return manifest

    except Exception as exc:  # noqa: BLE001
        if current_step is not None and step_status(manifest, current_step) not in {
            "completed",
            "skipped",
        }:
            set_step(
                manifest_path,
                manifest,
                current_step,
                "failed",
                error=str(exc),
            )
        set_manifest_status(manifest_path, manifest, "failed", error=str(exc))
        raise


def process_single_input_new_job(
    args: argparse.Namespace,
    input_path: Path,
    work_tmp_root: Path,
    out_dir: Path,
    output_stem: str,
) -> dict[str, Any]:
    job_tmp_dir: Path | None = None
    for _ in range(1000):
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        candidate = work_tmp_root / f"{output_stem}-{timestamp}"
        try:
            candidate.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        job_tmp_dir = candidate
        break
    if job_tmp_dir is None:
        raise RuntimeError("Could not allocate unique temporary job directory.")

    manifest_path = job_tmp_dir / "job_manifest.json"
    manifest = create_new_manifest(
        args=args,
        input_path=input_path,
        job_tmp_dir=job_tmp_dir,
        out_dir=out_dir,
        output_stem=output_stem,
    )
    write_manifest(manifest_path, manifest)

    return run_job(
        args=args,
        input_path=input_path,
        job_tmp_dir=job_tmp_dir,
        out_dir=out_dir,
        output_stem=output_stem,
        manifest_path=manifest_path,
        manifest=manifest,
        resume=False,
    )


def resolve_manifest_path_from_resume_job(path: Path) -> Path:
    target = path.resolve()
    if target.is_file():
        if target.name != "job_manifest.json":
            raise RuntimeError(f"Expected job_manifest.json file, got: {target}")
        return target
    if target.is_dir():
        manifest = target / "job_manifest.json"
        if not manifest.exists():
            raise RuntimeError(f"No job_manifest.json found in: {target}")
        return manifest
    raise RuntimeError(f"Resume target does not exist: {target}")


def discover_resumable_manifests(work_tmp_root: Path) -> list[Path]:
    if not work_tmp_root.exists():
        return []

    candidates = sorted(work_tmp_root.glob("*/job_manifest.json"), key=lambda p: p.stat().st_mtime)
    resumable: list[Path] = []

    for manifest_path in candidates:
        try:
            manifest = read_manifest(manifest_path)
        except Exception:  # noqa: BLE001
            continue

        status = str(manifest.get("status", "")).lower()
        if status != "completed":
            resumable.append(manifest_path)

    return resumable


def filter_manifests_by_input(
    manifests: list[Path],
    input_target: Path,
) -> list[Path]:
    if input_target.is_file():
        allowed = {str(input_target.resolve())}
    elif input_target.is_dir():
        files = discover_inputs(input_target)
        allowed = {str(p.resolve()) for p in files}
    else:
        return manifests

    filtered: list[Path] = []
    for manifest_path in manifests:
        try:
            manifest = read_manifest(manifest_path)
        except Exception:  # noqa: BLE001
            continue
        manifest_input = str(Path(manifest.get("input", "")).resolve())
        if manifest_input in allowed:
            filtered.append(manifest_path)

    return filtered


def resume_job_from_manifest(
    args: argparse.Namespace,
    raw_args: argparse.Namespace,
    manifest_path: Path,
) -> dict[str, Any]:
    manifest = read_manifest(manifest_path)

    input_path = Path(manifest.get("input", "")).resolve()
    if not input_path.exists():
        raise RuntimeError(f"Cannot resume job, input no longer exists: {input_path}")

    job_tmp_dir = Path(manifest.get("tmp_dir", manifest_path.parent)).resolve()
    if not job_tmp_dir.exists():
        raise RuntimeError(f"Cannot resume job, tmp dir is missing: {job_tmp_dir}")

    final_output_mp3 = Path(
        manifest.get(
            "final_output_mp3",
            str(args.out_dir.resolve() / f"{slugify(input_path.stem)}.mp3"),
        )
    ).resolve()
    out_dir = final_output_mp3.parent
    output_stem = final_output_mp3.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    job_args = apply_manifest_defaults_for_resume(args, raw_args, manifest)

    manifest["resumed_at"] = now_iso()
    write_manifest(manifest_path, manifest)

    return run_job(
        args=job_args,
        input_path=input_path,
        job_tmp_dir=job_tmp_dir,
        out_dir=out_dir,
        output_stem=output_stem,
        manifest_path=manifest_path,
        manifest=manifest,
        resume=True,
    )


def run_watch_loop(
    *,
    args: argparse.Namespace,
    input_dir: Path,
    work_tmp_root: Path,
    out_dir: Path,
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    jobs: list[dict[str, Any]] = []
    failures: list[tuple[str, str]] = []

    stable_state: dict[str, dict[str, Any]] = {}
    processed_signatures: dict[str, tuple[int, int]] = {}
    failed_signatures: dict[str, tuple[int, int]] = {}

    print(
        "Watch mode enabled. "
        f"Polling {input_dir} every {args.watch_interval:.2f}s "
        f"(stable after {args.watch_stable_seconds:.2f}s)."
    )
    if args.watch_max_cycles > 0:
        print(f"Watch will stop after {args.watch_max_cycles} cycle(s).")

    cycle = 0
    try:
        while True:
            cycle += 1

            files = discover_inputs_allow_empty(input_dir)
            stem_map = build_output_stem_map(files)
            active_keys = {str(path.resolve()) for path in files}

            for key in list(stable_state):
                if key not in active_keys:
                    stable_state.pop(key, None)
                    processed_signatures.pop(key, None)
                    failed_signatures.pop(key, None)

            now = time.time()
            ready: list[tuple[Path, tuple[int, int]]] = []

            for path in files:
                key = str(path.resolve())
                stat = path.stat()
                signature = (int(stat.st_size), int(stat.st_mtime_ns))
                entry = stable_state.get(key)

                if entry is None or entry.get("signature") != signature:
                    stable_state[key] = {"signature": signature, "since": now}
                    if (
                        args.watch_stable_seconds <= 0
                        and processed_signatures.get(key) != signature
                        and failed_signatures.get(key) != signature
                    ):
                        ready.append((path, signature))
                    continue

                stable_for = now - float(entry.get("since", now))
                if stable_for < args.watch_stable_seconds:
                    continue
                if processed_signatures.get(key) == signature:
                    continue
                if failed_signatures.get(key) == signature:
                    continue

                ready.append((path, signature))

            if ready:
                print(f"\nWatch cycle {cycle}: processing {len(ready)} ready file(s).")

            for path, signature in ready:
                key = str(path.resolve())
                output_stem = stem_map.get(key, slugify(path.stem))
                try:
                    manifest = process_single_input_new_job(
                        args=args,
                        input_path=path,
                        work_tmp_root=work_tmp_root,
                        out_dir=out_dir,
                        output_stem=output_stem,
                    )
                    manifest_path = Path(manifest.get("tmp_dir", "")).resolve() / "job_manifest.json"
                    jobs.append(build_job_record(manifest_path, manifest))
                    processed_signatures[key] = signature
                    failed_signatures.pop(key, None)
                except Exception as exc:  # noqa: BLE001
                    failures.append((str(path), str(exc)))
                    failed_signatures[key] = signature
                    print(f"Watch failed {path}: {exc}")

            if args.watch_max_cycles > 0 and cycle >= args.watch_max_cycles:
                break

            if args.watch_interval > 0:
                time.sleep(args.watch_interval)
    except KeyboardInterrupt:
        print("\nWatch interrupted by user.")

    return jobs, failures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Automate PDF/TXT -> cleanup/OCR -> TTS with input/, work/tmp/, out/ layout"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to config TOML file (default: config.toml)",
    )
    parser.add_argument(
        "--deps-file",
        type=Path,
        default=DEFAULT_DEPENDENCIES_PATH,
        help="Path to dependency TOML file (default: dependencies.toml)",
    )
    parser.add_argument(
        "--deps-check",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Precheck dependencies at startup",
    )
    parser.add_argument(
        "--deps-auto-install",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Offer to install missing dependencies after approval",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Input file or directory (default from config or input)",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Base working directory for temporary artifacts (default from config or work)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Directory for finished merged .mp3 outputs (default from config or out)",
    )
    parser.add_argument(
        "--job-name",
        default=None,
        help="Optional custom output name (single-file mode only)",
    )
    parser.add_argument(
        "--ocr",
        choices=("auto", "always", "never"),
        default=None,
        help="OCR strategy for PDF input",
    )
    parser.add_argument(
        "--ocr-lang",
        default=None,
        help="OCR language(s) passed to ocrmypdf --language",
    )
    parser.add_argument(
        "--ocr-page-min-chars",
        type=int,
        default=None,
        help="Auto OCR threshold: minimum characters expected per extracted page",
    )
    parser.add_argument(
        "--ocr-page-min-alpha-ratio",
        type=float,
        default=None,
        help="Auto OCR threshold: minimum alphabetic ratio per extracted page",
    )
    parser.add_argument(
        "--ocr-page-max-replacement-ratio",
        type=float,
        default=None,
        help="Auto OCR threshold: maximum replacement-char ratio per extracted page",
    )
    parser.add_argument(
        "--ocr-page-min-score",
        type=float,
        default=None,
        help="Auto OCR threshold: minimum quality score per extracted page",
    )
    parser.add_argument(
        "--ocr-auto-bad-page-ratio",
        type=float,
        default=None,
        help="In auto mode, trigger OCR when at least this page ratio looks low-quality",
    )
    parser.add_argument(
        "--lang",
        default=None,
        help="Google TTS language code",
    )
    parser.add_argument(
        "--voice-name",
        default=None,
        help="Google TTS voice name",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=None,
        help="Max chunk size before TTS",
    )
    parser.add_argument(
        "--speaking-rate",
        type=float,
        default=None,
        help="Google TTS speaking rate",
    )
    parser.add_argument(
        "--pitch",
        type=float,
        default=None,
        help="Google TTS pitch",
    )
    parser.add_argument(
        "--skip-tts",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Skip TTS step",
    )
    parser.add_argument(
        "--skip-merge",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Skip merge step",
    )
    parser.add_argument(
        "--ui",
        choices=("auto", "plain", "rich"),
        default=None,
        help="CLI UI mode for TTS dashboard/progress",
    )
    parser.add_argument(
        "--estimate-only",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Show character/cost estimate and exit before calling Google TTS",
    )
    parser.add_argument(
        "--test-run",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Fake TTS run with progress; no Google requests are sent",
    )
    parser.add_argument(
        "--test-delay",
        type=float,
        default=None,
        help="Seconds per chunk in --test-run mode",
    )
    parser.add_argument(
        "--test-write-placeholders",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="In --test-run mode, create placeholder .mp3 files",
    )
    parser.add_argument(
        "--use-ssml",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Shape text chunks as SSML before TTS",
    )
    parser.add_argument(
        "--cleanup-diff-preview",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Write raw-vs-clean preview diff before TTS",
    )
    parser.add_argument(
        "--cleanup-diff-max-lines",
        type=int,
        default=None,
        help="Maximum lines to write in cleanup diff preview artifact",
    )
    parser.add_argument(
        "--dashboard",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable rich full-screen dashboard with pause/resume controls",
    )
    parser.add_argument(
        "--export-m4b",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Export chaptered .m4b in addition to merged .mp3",
    )
    parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=None,
        help="Hard limit: stop when estimated cost exceeds this amount",
    )
    parser.add_argument(
        "--max-chars-per-run",
        type=int,
        default=None,
        help="Hard limit: stop when pending chars exceed this count",
    )
    parser.add_argument(
        "--confirm-before-spend",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Ask for confirmation before real paid synthesis",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume unfinished jobs from work/tmp",
    )
    parser.add_argument(
        "--resume-job",
        type=Path,
        default=None,
        help="Resume a specific job dir or job_manifest.json",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Run environment checks before processing",
    )
    parser.add_argument(
        "--doctor-only",
        action="store_true",
        help="Run environment checks and exit",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch input directory and auto-process new/changed files",
    )
    parser.add_argument(
        "--watch-interval",
        type=float,
        default=5.0,
        help="Seconds between scans in --watch mode",
    )
    parser.add_argument(
        "--watch-stable-seconds",
        type=float,
        default=3.0,
        help="File must stay unchanged this many seconds before processing in --watch mode",
    )
    parser.add_argument(
        "--watch-max-cycles",
        type=int,
        default=0,
        help="Stop watch mode after N scan cycles (0 = run until interrupted)",
    )
    return parser


def main() -> None:
    raw_args = build_parser().parse_args()

    try:
        config = load_config(raw_args.config.resolve())
        args = resolve_runtime_args(raw_args, config)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    if args.watch_interval < 0:
        raise SystemExit("--watch-interval must be >= 0")
    if args.watch_stable_seconds < 0:
        raise SystemExit("--watch-stable-seconds must be >= 0")
    if args.watch_max_cycles < 0:
        raise SystemExit("--watch-max-cycles must be >= 0")
    if args.max_chars_per_run is not None and args.max_chars_per_run <= 0:
        raise SystemExit("--max-chars-per-run must be > 0")
    if args.max_cost_usd is not None and args.max_cost_usd < 0:
        raise SystemExit("--max-cost-usd must be >= 0")
    if args.ocr_page_min_chars < 0:
        raise SystemExit("--ocr-page-min-chars must be >= 0")
    if not 0.0 <= args.ocr_page_min_alpha_ratio <= 1.0:
        raise SystemExit("--ocr-page-min-alpha-ratio must be in [0, 1]")
    if not 0.0 <= args.ocr_page_max_replacement_ratio <= 1.0:
        raise SystemExit("--ocr-page-max-replacement-ratio must be in [0, 1]")
    if not 0.0 <= args.ocr_page_min_score <= 1.0:
        raise SystemExit("--ocr-page-min-score must be in [0, 1]")
    if not 0.0 <= args.ocr_auto_bad_page_ratio <= 1.0:
        raise SystemExit("--ocr-auto-bad-page-ratio must be in [0, 1]")
    if args.cleanup_diff_max_lines <= 0:
        raise SystemExit("--cleanup-diff-max-lines must be > 0")

    if raw_args.watch and (raw_args.resume or raw_args.resume_job is not None):
        raise SystemExit("Cannot combine --watch with --resume or --resume-job")

    if raw_args.deps_check:
        try:
            ensure_dependencies_ready(
                raw_args.deps_file.resolve(),
                auto_install=bool(raw_args.deps_auto_install),
            )
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc

    if raw_args.doctor or raw_args.doctor_only:
        doctor_ok = run_doctor(args)
        if raw_args.doctor_only:
            raise SystemExit(0 if doctor_ok else 1)
        if not doctor_ok:
            raise SystemExit(1)

    input_target = args.input.resolve()
    out_dir = args.out_dir.resolve()
    work_dir = args.work_dir.resolve()
    work_tmp_root = work_dir / "tmp"

    out_dir.mkdir(parents=True, exist_ok=True)
    work_tmp_root.mkdir(parents=True, exist_ok=True)

    job_records: list[dict[str, Any]] = []
    failures: list[tuple[str, str]] = []

    if raw_args.resume_job is not None or raw_args.resume:
        try:
            if raw_args.resume_job is not None:
                resume_manifests = [resolve_manifest_path_from_resume_job(raw_args.resume_job)]
            else:
                resume_manifests = discover_resumable_manifests(work_tmp_root)
                if raw_args.input is not None:
                    resume_manifests = filter_manifests_by_input(
                        resume_manifests,
                        input_target,
                    )
                if not resume_manifests:
                    raise RuntimeError("No resumable jobs found.")
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc

        print(f"Resuming jobs: {len(resume_manifests)}")
        for index, manifest_path in enumerate(resume_manifests, start=1):
            print(f"\n[{index}/{len(resume_manifests)}] Resume {manifest_path.parent.name}")
            try:
                manifest = resume_job_from_manifest(args=args, raw_args=raw_args, manifest_path=manifest_path)
                job_records.append(build_job_record(manifest_path, manifest))
            except Exception as exc:  # noqa: BLE001
                failures.append((str(manifest_path), str(exc)))
                print(f"Failed resume {manifest_path}: {exc}")

        write_run_report(
            work_dir=work_dir,
            mode="resume",
            args=args,
            jobs=job_records,
            failures=failures,
        )

        if failures:
            print(f"\nCompleted with failures: {len(failures)}/{len(resume_manifests)}")
            for target, error in failures:
                print(f" - {target}: {error}")
            raise SystemExit(1)

        print(f"\nResume completed successfully: {len(resume_manifests)} job(s).")
        return

    if raw_args.watch:
        if not input_target.exists():
            raise SystemExit(f"Input path does not exist: {input_target}")
        if not input_target.is_dir():
            raise SystemExit("--watch requires --input to be a directory")
        if args.job_name:
            print("Ignoring --job-name in --watch mode.")

        job_records, failures = run_watch_loop(
            args=args,
            input_dir=input_target,
            work_tmp_root=work_tmp_root,
            out_dir=out_dir,
        )

        write_run_report(
            work_dir=work_dir,
            mode="watch",
            args=args,
            jobs=job_records,
            failures=failures,
        )

        if failures:
            print(f"\nWatch stopped with failures: {len(failures)}")
            for target, error in failures[:20]:
                print(f" - {target}: {error}")
            raise SystemExit(1)

        print(f"\nWatch completed. Processed jobs: {len(job_records)}")
        return

    try:
        input_files = discover_inputs(input_target)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    print(f"Input target: {input_target}")
    print(f"Discovered files: {len(input_files)}")
    print(f"Work tmp root: {work_tmp_root}")
    print(f"Final out dir: {out_dir}")

    if args.job_name and len(input_files) > 1:
        print("Ignoring --job-name because multiple input files were discovered.")

    used_stems: dict[str, int] = {}

    for index, input_path in enumerate(input_files, start=1):
        print(f"\n[{index}/{len(input_files)}] Processing {input_path.name}")

        if args.job_name and len(input_files) == 1:
            base_stem = slugify(args.job_name)
        else:
            base_stem = slugify(input_path.stem)
        output_stem = unique_stem(base_stem, used_stems)

        try:
            manifest = process_single_input_new_job(
                args=args,
                input_path=input_path,
                work_tmp_root=work_tmp_root,
                out_dir=out_dir,
                output_stem=output_stem,
            )
            manifest_path = Path(manifest.get("tmp_dir", "")).resolve() / "job_manifest.json"
            job_records.append(build_job_record(manifest_path, manifest))
        except Exception as exc:  # noqa: BLE001
            failures.append((input_path.name, str(exc)))
            print(f"Failed {input_path.name}: {exc}")

    write_run_report(
        work_dir=work_dir,
        mode="batch",
        args=args,
        jobs=job_records,
        failures=failures,
    )

    if failures:
        print(f"\nCompleted with failures: {len(failures)}/{len(input_files)}")
        for target, error in failures:
            print(f" - {target}: {error}")
        raise SystemExit(1)

    print(f"\nCompleted successfully: {len(input_files)} file(s) processed.")


if __name__ == "__main__":
    main()
