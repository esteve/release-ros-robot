"""Tests for common.py shared utilities."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from common import (
    DEFAULT_CONFIG_FILES,
    discover_package_xmls,
    get_config_string,
    get_config_string_list,
    get_config_value,
    get_last_tag,
    get_package_version,
    load_config_file,
    resolve_exclude_paths,
)


class TestGetPackageVersion:
    """Tests for get_package_version function (defined in common.py)."""

    def test_get_version_from_package_xml(
        self, temp_dir: Path, package_xml_content: str
    ):
        """Test extracting version from package.xml."""
        os.chdir(temp_dir)
        (temp_dir / "package.xml").write_text(package_xml_content)

        version = get_package_version([])
        assert version == "1.2.3"

    def test_get_version_no_package_xml(self, temp_dir: Path):
        """Test getting version when package.xml doesn't exist."""
        os.chdir(temp_dir)

        with pytest.raises(SystemExit):
            get_package_version([])

    def test_get_version_nested_package_xml(
        self, temp_dir: Path, package_xml_content: str
    ):
        """Test extracting version from a nested package.xml."""
        os.chdir(temp_dir)
        pkg_dir = temp_dir / "my_package"
        pkg_dir.mkdir()
        (pkg_dir / "package.xml").write_text(package_xml_content)

        version = get_package_version([])
        assert version == "1.2.3"


class TestGetPackageVersionConsistency:
    """Tests for version consistency validation in get_package_version."""

    def test_matched_versions_succeed(self, temp_dir: Path, package_xml_content: str):
        """Test that identical versions across packages return the version."""
        os.chdir(temp_dir)
        pkg1 = temp_dir / "pkg1"
        pkg2 = temp_dir / "pkg2"
        pkg1.mkdir()
        pkg2.mkdir()
        (pkg1 / "package.xml").write_text(package_xml_content)
        (pkg2 / "package.xml").write_text(package_xml_content)

        version = get_package_version([])
        assert version == "1.2.3"

    def test_mismatched_versions_fail(self, temp_dir: Path, package_xml_content: str):
        """Test that mismatched versions across packages raise SystemExit."""
        os.chdir(temp_dir)
        pkg1 = temp_dir / "pkg1"
        pkg2 = temp_dir / "pkg2"
        pkg1.mkdir()
        pkg2.mkdir()
        (pkg1 / "package.xml").write_text(package_xml_content)
        (pkg2 / "package.xml").write_text(
            package_xml_content.replace(
                "<version>1.2.3</version>", "<version>2.0.0</version>"
            )
        )

        with pytest.raises(SystemExit):
            get_package_version([])

    def test_mismatched_versions_error_mentions_exclude_paths(
        self, temp_dir: Path, package_xml_content: str, capsys
    ):
        """Test that the version-mismatch error message mentions exclude-paths."""
        os.chdir(temp_dir)
        pkg1 = temp_dir / "pkg1"
        fixture = temp_dir / "test" / "fixture"
        pkg1.mkdir()
        fixture.mkdir(parents=True)
        (pkg1 / "package.xml").write_text(package_xml_content)
        (fixture / "package.xml").write_text(
            package_xml_content.replace(
                "<version>1.2.3</version>", "<version>0.0.0</version>"
            )
        )

        with pytest.raises(SystemExit):
            get_package_version([])

        captured = capsys.readouterr()
        assert "exclude-paths" in captured.out

    def test_fixture_excluded_from_version_check(
        self, temp_dir: Path, package_xml_content: str
    ):
        """Test that fixtures excluded by patterns don't affect version validation."""
        os.chdir(temp_dir)
        pkg1 = temp_dir / "pkg1"
        fixture = temp_dir / "test" / "fixture"
        pkg1.mkdir()
        fixture.mkdir(parents=True)
        (pkg1 / "package.xml").write_text(package_xml_content)
        (fixture / "package.xml").write_text(
            package_xml_content.replace(
                "<version>1.2.3</version>", "<version>0.0.0</version>"
            )
        )

        version = get_package_version(["test/**"])
        assert version == "1.2.3"


class TestGetLastTag:
    """Tests for get_last_tag function (defined in common.py)."""

    @patch("common.run_command")
    def test_get_last_tag_exists(self, mock_run):
        """Test getting the last tag when it exists."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "1.2.3\n"
        mock_run.return_value = mock_result

        tag = get_last_tag()
        assert tag == "1.2.3"

    @patch("common.run_command")
    def test_get_last_tag_none(self, mock_run):
        """Test getting the last tag when none exists."""
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_run.return_value = mock_result

        tag = get_last_tag()
        assert tag is None


class TestDiscoverPackageXmls:
    """Tests for discover_package_xmls function."""

    def test_no_exclude_returns_all(self, temp_dir: Path, package_xml_content: str):
        """Test that an empty exclude list returns all discovered package.xml files."""
        os.chdir(temp_dir)
        pkg_a = temp_dir / "pkg_a"
        pkg_b = temp_dir / "pkg_b"
        pkg_a.mkdir()
        pkg_b.mkdir()
        (pkg_a / "package.xml").write_text(package_xml_content)
        (pkg_b / "package.xml").write_text(package_xml_content)

        result = discover_package_xmls([])
        paths = [p.as_posix() for p in result]
        assert "pkg_a/package.xml" in paths
        assert "pkg_b/package.xml" in paths
        assert len(result) == 2

    def test_exclude_single_pattern(self, temp_dir: Path, package_xml_content: str):
        """Test that a single pattern correctly excludes matching files."""
        os.chdir(temp_dir)
        (temp_dir / "pkg_a").mkdir()
        (temp_dir / "test").mkdir()
        (temp_dir / "pkg_a" / "package.xml").write_text(package_xml_content)
        (temp_dir / "test" / "package.xml").write_text(package_xml_content)

        result = discover_package_xmls(["test/**"])
        paths = [p.as_posix() for p in result]
        assert "pkg_a/package.xml" in paths
        assert "test/package.xml" not in paths
        assert len(result) == 1

    def test_exclude_multiple_patterns(self, temp_dir: Path, package_xml_content: str):
        """Test that multiple patterns all get applied."""
        os.chdir(temp_dir)
        for d in ["pkg_a", "test", "third_party"]:
            (temp_dir / d).mkdir()
            (temp_dir / d / "package.xml").write_text(package_xml_content)

        result = discover_package_xmls(["test/**", "third_party/**"])
        paths = [p.as_posix() for p in result]
        assert "pkg_a/package.xml" in paths
        assert "test/package.xml" not in paths
        assert "third_party/package.xml" not in paths
        assert len(result) == 1

    def test_exclude_pattern_no_match(self, temp_dir: Path, package_xml_content: str):
        """Test that a pattern matching nothing leaves the result unchanged."""
        os.chdir(temp_dir)
        (temp_dir / "pkg_a").mkdir()
        (temp_dir / "pkg_a" / "package.xml").write_text(package_xml_content)

        result = discover_package_xmls(["vendor/**"])
        assert len(result) == 1
        assert result[0].as_posix() == "pkg_a/package.xml"

    def test_exclude_all_packages(self, temp_dir: Path, package_xml_content: str):
        """Test that excluding everything returns an empty list."""
        os.chdir(temp_dir)
        (temp_dir / "test").mkdir()
        (temp_dir / "test" / "package.xml").write_text(package_xml_content)

        result = discover_package_xmls(["test/**"])
        assert result == []

    def test_fnmatch_star_matches_nested_paths(
        self, temp_dir: Path, package_xml_content: str
    ):
        """Test fnmatch '*' matches path separators, so 'test/*' matches nested files."""
        os.chdir(temp_dir)
        nested = temp_dir / "test" / "fixtures" / "pkg"
        nested.mkdir(parents=True)
        (nested / "package.xml").write_text(package_xml_content)

        # Both "test/**" and "test/*" should match nested paths due to fnmatch semantics
        result_double = discover_package_xmls(["test/**"])
        result_single = discover_package_xmls(["test/*"])
        assert result_double == []
        assert result_single == []


class TestConfigLoading:
    """Tests for TOML config loading helpers."""

    def test_load_default_config_missing_returns_empty(self, temp_dir: Path) -> None:
        """Test the default missing config file is treated as optional."""
        os.chdir(temp_dir)

        config = load_config_file(None)

        assert config == {}

    def test_load_explicit_missing_config_fails(self, temp_dir: Path) -> None:
        """Test an explicitly requested missing config file fails."""
        os.chdir(temp_dir)

        with pytest.raises(SystemExit):
            load_config_file("custom.toml")

    def test_load_config_file(self, temp_dir: Path) -> None:
        """Test TOML config files are loaded successfully."""
        os.chdir(temp_dir)
        (temp_dir / DEFAULT_CONFIG_FILES[0]).write_text(
            'repository = "pkg"\n[prepare]\nbase_branch = "jazzy"\n'
        )

        config = load_config_file(None)

        assert config["repository"] == "pkg"
        assert config["prepare"]["base_branch"] == "jazzy"

    def test_load_hidden_default_config_file(self, temp_dir: Path) -> None:
        """Test the hidden root config file is also discovered by default."""
        os.chdir(temp_dir)
        (temp_dir / DEFAULT_CONFIG_FILES[1]).write_text('repository = "pkg"\n')

        config = load_config_file(None)

        assert config["repository"] == "pkg"

    def test_load_default_config_fails_when_both_default_files_exist(
        self, temp_dir: Path
    ) -> None:
        """Test ambiguous default config files are rejected."""
        os.chdir(temp_dir)
        for path in DEFAULT_CONFIG_FILES:
            (temp_dir / path).write_text('repository = "pkg"\n')

        with pytest.raises(SystemExit):
            load_config_file(None)


class TestGetConfigValue:
    """Tests for get_config_value nested key lookup."""

    def test_returns_value_for_existing_single_key(self) -> None:
        """Test a top-level key is returned correctly."""
        config = {"repository": "my_pkg"}

        result = get_config_value(config, "repository")

        assert result == "my_pkg"

    def test_returns_value_for_existing_nested_key(self) -> None:
        """Test a nested key is resolved through multiple levels."""
        config = {"release": {"targets": {"main": []}}}

        result = get_config_value(config, "release", "targets")

        assert result == {"main": []}

    def test_returns_none_for_missing_key(self) -> None:
        """Test a missing top-level key returns None."""
        result = get_config_value({}, "missing")

        assert result is None

    def test_returns_none_for_missing_nested_key(self) -> None:
        """Test a missing nested key returns None without raising."""
        config = {"release": {}}

        result = get_config_value(config, "release", "targets")

        assert result is None

    def test_returns_none_when_intermediate_key_is_not_dict(self) -> None:
        """Test that a non-dict intermediate value short-circuits to None."""
        config = {"release": "not-a-dict"}

        result = get_config_value(config, "release", "targets")

        assert result is None


class TestGetConfigString:
    """Tests for get_config_string single-value extraction."""

    def test_returns_string_value(self) -> None:
        """Test a valid string config value is returned stripped."""
        config = {"repository": "  my_pkg  "}

        result = get_config_string(config, "repository")

        assert result == "my_pkg"

    def test_returns_none_for_missing_key(self) -> None:
        """Test a missing key returns None without exiting."""
        result = get_config_string({}, "repository")

        assert result is None

    def test_exits_on_non_string_value(self) -> None:
        """Test a non-string value causes SystemExit."""
        config = {"repository": 42}

        with pytest.raises(SystemExit):
            get_config_string(config, "repository")

    def test_exits_on_empty_string_value(self) -> None:
        """Test a blank string value causes SystemExit."""
        config = {"repository": "   "}

        with pytest.raises(SystemExit):
            get_config_string(config, "repository")

    def test_uses_field_name_in_error_message(self, capsys) -> None:
        """Test the field_name kwarg appears in the error output."""
        config = {"repository": 123}

        with pytest.raises(SystemExit):
            get_config_string(config, "repository", field_name="repository")

        captured = capsys.readouterr()
        assert "repository" in captured.out


class TestGetConfigStringList:
    """Tests for get_config_string_list list extraction."""

    def test_returns_list_of_strings(self) -> None:
        """Test a valid list of strings is returned stripped."""
        config = {"exclude_paths": ["test/**", "  vendor/**  "]}

        result = get_config_string_list(config, "exclude_paths")

        assert result == ["test/**", "vendor/**"]

    def test_returns_empty_list_for_missing_key(self) -> None:
        """Test a missing key returns an empty list without exiting."""
        result = get_config_string_list({}, "exclude_paths")

        assert result == []

    def test_filters_blank_entries(self) -> None:
        """Test that blank strings inside the list are filtered out."""
        config = {"exclude_paths": ["test/**", "  ", "vendor/**"]}

        result = get_config_string_list(config, "exclude_paths")

        assert result == ["test/**", "vendor/**"]

    def test_exits_on_non_list_value(self) -> None:
        """Test a non-list value causes SystemExit."""
        config = {"exclude_paths": "test/**"}

        with pytest.raises(SystemExit):
            get_config_string_list(config, "exclude_paths")

    def test_exits_on_list_with_non_string_items(self) -> None:
        """Test a list containing non-string items causes SystemExit."""
        config = {"exclude_paths": ["test/**", 42]}

        with pytest.raises(SystemExit):
            get_config_string_list(config, "exclude_paths")


class TestResolveExcludePaths:
    """Tests for resolve_exclude_paths environment/config precedence."""

    def test_returns_env_paths_when_set(self, monkeypatch) -> None:
        """Test BLOOM_EXCLUDE_PATHS env var takes precedence over config."""
        monkeypatch.setenv("BLOOM_EXCLUDE_PATHS", "test/**\nvendor/**")
        config = {"exclude_paths": ["config_path/**"]}

        result = resolve_exclude_paths(config)

        assert result == ["test/**", "vendor/**"]

    def test_returns_config_paths_when_env_absent(self, monkeypatch) -> None:
        """Test config file exclude_paths is used when env var is absent."""
        monkeypatch.delenv("BLOOM_EXCLUDE_PATHS", raising=False)
        config = {"exclude_paths": ["config_path/**"]}

        result = resolve_exclude_paths(config)

        assert result == ["config_path/**"]

    def test_returns_empty_list_when_both_absent(self, monkeypatch) -> None:
        """Test an empty list is returned when neither env nor config has paths."""
        monkeypatch.delenv("BLOOM_EXCLUDE_PATHS", raising=False)

        result = resolve_exclude_paths({})

        assert result == []

    def test_env_blank_lines_are_ignored(self, monkeypatch) -> None:
        """Test blank lines in BLOOM_EXCLUDE_PATHS are filtered out."""
        monkeypatch.setenv("BLOOM_EXCLUDE_PATHS", "test/**\n\n  \nvendor/**")

        result = resolve_exclude_paths({})

        assert result == ["test/**", "vendor/**"]
