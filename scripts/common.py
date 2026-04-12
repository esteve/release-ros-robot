#!/usr/bin/env python3
"""
Shared utilities for release-ros-robot scripts.

Used by both prepare_release.py and execute_release.py.
"""

import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from fnmatch import fnmatch
from pathlib import Path
import importlib
from typing import Any, Optional

tomllib = importlib.import_module("tomllib")
if sys.version_info < (3, 11):  # pragma: no cover - Python 3.10 fallback
    tomllib = importlib.import_module("tomli")


DEFAULT_CONFIG_FILE = ".github/bloom-release.toml"


def log_info(msg: str) -> None:
    """Print info message."""
    print(f"\033[34m[INFO]\033[0m {msg}")


def log_success(msg: str) -> None:
    """Print success message."""
    print(f"\033[32m[SUCCESS]\033[0m {msg}")


def log_warning(msg: str) -> None:
    """Print warning message."""
    print(f"\033[33m[WARNING]\033[0m {msg}")


def log_error(msg: str) -> None:
    """Print error message."""
    print(f"\033[31m[ERROR]\033[0m {msg}")


def run_command(
    cmd: list[str],
    check: bool = True,
    capture_output: bool = False,
    env: Optional[dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    log_info(f"Running: {' '.join(cmd)}")
    merged_env = {**os.environ, **(env or {})}
    result = subprocess.run(
        cmd,
        check=check,
        capture_output=capture_output,
        text=True,
        env=merged_env,
    )
    return result


def set_output(name: str, value: str) -> None:
    """Set a GitHub Actions output variable."""
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"{name}={value}\n")
    else:
        # Fallback for local testing outside GitHub Actions
        log_info(f"Output: {name}={value}")


def load_config_file(config_file: str) -> dict[str, Any]:
    """Load the action TOML configuration file if it exists.

    Args:
        config_file: Path to the TOML configuration file.

    Returns:
        Parsed top-level TOML table, or an empty dict when the file does not
        exist.
    """
    config_path = Path(config_file)
    if not config_path.exists():
        if config_file != DEFAULT_CONFIG_FILE:
            log_error(f"Config file not found: {config_file}")
            sys.exit(1)
        return {}

    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as error:
        log_error(f"Failed to load config file {config_file}: {error}")
        sys.exit(1)

    if not isinstance(config, dict):
        log_error(f"Config file {config_file} must contain a top-level TOML table")
        sys.exit(1)

    log_info(f"Loaded config file: {config_file}")
    return config


def get_config_value(config: dict[str, Any], *keys: str) -> Any:
    """Return a nested config value or None when the key path is absent."""
    current: Any = config
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def get_config_string(
    config: dict[str, Any], *keys: str, field_name: Optional[str] = None
) -> Optional[str]:
    """Return a non-empty string from the config file.

    Args:
        config: Parsed config mapping.
        keys: Nested keys to resolve.
        field_name: Optional human-readable field name for errors.

    Returns:
        Trimmed string value if present, otherwise None.
    """
    value = get_config_value(config, *keys)
    if value is None:
        return None

    name = field_name or ".".join(keys)
    if not isinstance(value, str) or not value.strip():
        log_error(f"Config field '{name}' must be a non-empty string")
        sys.exit(1)
    return value.strip()


def get_config_string_list(
    config: dict[str, Any], *keys: str, field_name: Optional[str] = None
) -> list[str]:
    """Return a list of trimmed strings from the config file.

    Args:
        config: Parsed config mapping.
        keys: Nested keys to resolve.
        field_name: Optional human-readable field name for errors.

    Returns:
        List of non-empty string values, or an empty list if the key is absent.
    """
    value = get_config_value(config, *keys)
    if value is None:
        return []

    name = field_name or ".".join(keys)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        log_error(f"Config field '{name}' must be a list of strings")
        sys.exit(1)

    return [item.strip() for item in value if item.strip()]


def discover_package_xmls(exclude_patterns: list[str]) -> list[Path]:
    """Discover all package.xml files in the repository, respecting exclusions.

    This is the single entry point for package.xml discovery. All callers
    should use this function instead of calling Path.rglob("package.xml")
    directly so that exclude_patterns are consistently applied everywhere.

    Patterns are matched against the full POSIX-style relative path of each
    package.xml (e.g., "test/fixtures/pkg/package.xml"). Python's fnmatch is
    used for matching, where '*' matches any character including '/', so both
    "test/**" and "test/*" match files at any depth under test/.

    Examples:
        "test/**"            matches test/fixture/package.xml
        "test/*"             also matches test/fixture/package.xml (fnmatch '*' matches '/')
        "third_party/**"     matches third_party/vendor/pkg/package.xml
        "test/**/package.xml" matches test/deep/nested/package.xml

    Args:
        exclude_patterns: List of POSIX-style glob patterns. Any package.xml
            whose relative path matches at least one pattern is excluded.
            Pass [] for no filtering.

    Returns:
        Sorted list of Path objects for non-excluded package.xml files.
    """
    all_xmls = sorted(Path(".").rglob("package.xml"))

    if not exclude_patterns:
        return all_xmls

    filtered: list[Path] = []
    excluded: list[Path] = []
    for pkg_xml in all_xmls:
        path_str = pkg_xml.as_posix()
        if any(fnmatch(path_str, pattern) for pattern in exclude_patterns):
            excluded.append(pkg_xml)
        else:
            filtered.append(pkg_xml)

    if excluded:
        log_info(
            f"Discovered {len(all_xmls)} package.xml file(s), "
            f"{len(excluded)} excluded by exclude-paths:"
        )
        for p in excluded:
            log_info(f"  Excluded: {p.as_posix()}")

    return filtered


def get_package_version(exclude_patterns: list[str]) -> str:
    """Get the common package version across all package.xml files.

    Bloom requires all packages in a repository to share the same version.
    This function validates that invariant and fails loudly on mismatch.

    Args:
        exclude_patterns: Glob patterns to exclude from package.xml discovery.
            Pass [] for no filtering.

    Returns:
        The shared version string found in all (non-excluded) package.xml files
    """
    package_xmls = discover_package_xmls(exclude_patterns)
    if not package_xmls:
        log_error("No package.xml found")
        sys.exit(1)

    versions: dict[Path, str] = {}
    for pkg_xml in package_xmls:
        root = ET.parse(pkg_xml).getroot()
        version = root.findtext("version")
        if version is not None and version.strip():
            versions[pkg_xml] = version.strip()

    if not versions:
        return "0.0.0"

    unique_versions = set(versions.values())
    if len(unique_versions) > 1:
        log_error("package.xml files have mismatched versions:")
        for pkg_xml, ver in sorted(versions.items()):
            log_error(f"  {pkg_xml}: {ver}")
        log_error(
            "Bloom does not support multiple packages with different versions. "
            "Please align all package.xml versions before releasing."
        )
        log_error("")
        log_error(
            "If some of the above are test fixtures or vendored code, exclude "
            "them using the exclude-paths input in your workflow."
        )
        sys.exit(1)

    return next(iter(unique_versions))


def is_release_commit() -> bool:
    """Return True if HEAD is a release commit created by this action.

    Detects the two commit message shapes produced when a release PR is merged:
    - squash merge  : ``chore(release): X.Y.Z``  (PR title used as subject)
    - rebase merge  : ``chore(release): prepare release X.Y.Z``  (branch commit)

    For a merge-commit merge the HEAD is a synthetic merge commit whose
    subject does not match, but the ``prepare`` mode "no new commits" guard
    and the ``release`` mode commit-driven guard both handle that case
    naturally.
    """
    result = run_command(
        ["git", "log", "-1", "--format=%s"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    return bool(re.match(r"chore\(release\):", result.stdout.strip()))


def get_exclude_paths_from_env() -> list[str]:
    """Return the exclude-paths list from the BLOOM_EXCLUDE_PATHS environment variable.

    The action passes the ``exclude-paths`` workflow input as a newline-separated
    string via this env var.  Empty lines are ignored.
    """
    raw = os.environ.get("BLOOM_EXCLUDE_PATHS", "")
    return [p.strip() for p in raw.splitlines() if p.strip()]


def resolve_exclude_paths(config: dict[str, Any]) -> list[str]:
    """Resolve exclude-paths from direct input first, then config file.

    Args:
        config: Parsed config mapping.

    Returns:
        The exclude-paths list from the environment override, or the config file
        fallback when the override is absent.
    """
    env_paths = get_exclude_paths_from_env()
    if env_paths:
        return env_paths
    return get_config_string_list(config, "exclude_paths", field_name="exclude_paths")


def get_last_tag() -> Optional[str]:
    """Get the most recent git tag."""
    result = run_command(
        ["git", "describe", "--tags", "--abbrev=0"],
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return None
