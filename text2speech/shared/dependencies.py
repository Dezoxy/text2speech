from __future__ import annotations

import importlib.util
import os
import shlex
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from text2speech.shared.parsing import parse_string_map


@dataclass(frozen=True)
class MissingDependency:
    kind: str
    name: str
    required: bool
    install_target: str | None
    install_tool: str | None


def parse_string_list(value: Any, field_name: str, source: Path) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RuntimeError(f"{field_name} must be a list of strings in {source}")
    return [item.strip() for item in value if item.strip()]


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
            missing.append(MissingDependency("python", package, True, package, "pip"))

    for package in spec.get("python_optional", []):
        import_name = import_map.get(package, package.replace("-", "_"))
        if not python_module_available(import_name):
            missing.append(MissingDependency("python", package, False, package, "pip"))

    for command in spec.get("commands_required", []):
        if shutil.which(command) is None:
            missing.append(
                MissingDependency(
                    "command",
                    command,
                    True,
                    install_brew.get(command),
                    "brew" if command in install_brew else None,
                )
            )

    for command in spec.get("commands_optional", []):
        if shutil.which(command) is None:
            missing.append(
                MissingDependency(
                    "command",
                    command,
                    False,
                    install_brew.get(command),
                    "brew" if command in install_brew else None,
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


def _run_command(cmd: list[str], step: str) -> None:
    try:
        subprocess.run(cmd, check=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"{step} failed with exit code {exc.returncode}: {' '.join(cmd)}") from exc


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

    print("System Python is externally managed. Installing Python dependencies into local virtual environment: .venv")
    _run_command([sys.executable, "-m", "venv", ".venv"], "create .venv")
    venv_python = Path(".venv") / "bin" / "python"
    if not venv_python.exists():
        raise RuntimeError(f"Virtual environment python not found: {venv_python}")

    _run_command([str(venv_python), "-m", "pip", "install", *packages], "pip install missing dependencies in .venv")
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
    result = subprocess.run([str(venv_python), "-c", probe, *import_names], text=True, capture_output=True)
    return result.returncode == 0


def relaunch_with_venv_if_ready(spec: dict[str, Any], reason: str) -> None:
    venv_root = Path(".venv")
    venv_python = venv_root / "bin" / "python"
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
            raise RuntimeError("Homebrew is not installed, cannot auto-install missing command dependencies.")
        print(f"Installing command dependencies via brew: {', '.join(brew_formulas)}")
        _run_command([brew_path, "install", *brew_formulas], "brew install missing dependencies")

    return installed_in_venv, venv_python


def ensure_dependencies_ready(dependency_file: Path, *, auto_install: bool) -> None:
    spec = load_dependencies(dependency_file)
    missing = find_missing_dependencies(spec)
    if not missing:
        summarize_missing_dependencies(missing)
        return

    required_missing = [item for item in missing if item.required]
    if required_missing and all(item.kind == "python" for item in required_missing):
        relaunch_with_venv_if_ready(spec, "Required Python dependencies are available in local .venv.")

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
        raise RuntimeError(f"Missing required dependencies: {needed}")
