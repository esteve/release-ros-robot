"""Tests for prepare_release.py script."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from prepare_release import (
    calculate_next_version,
    commit_touches_package,
    detect_version_bump_from_commits,
    filter_commits_for_package,
    generate_changelog_entry,
    generate_pr_summary,
    get_commit_files_batch,
    get_commits_since_ref,
    get_last_tag,
    get_package_version,
    is_release_commit,
    parse_conventional_commit,
    update_changelog_files,
    update_package_xml_version,
)


class TestParseConventionalCommit:
    """Tests for parse_conventional_commit function."""

    def test_parse_feat_commit(self):
        """Test parsing a feat commit."""
        commit_type, is_breaking = parse_conventional_commit("feat: add new feature")
        assert commit_type == "feat"
        assert is_breaking is False

    def test_parse_feat_commit_with_scope(self):
        """Test parsing a feat commit with scope."""
        commit_type, is_breaking = parse_conventional_commit(
            "feat(parser): add new parser"
        )
        assert commit_type == "feat"
        assert is_breaking is False

    def test_parse_breaking_change_exclamation(self):
        """Test parsing a breaking change with ! notation."""
        commit_type, is_breaking = parse_conventional_commit("feat!: redesign API")
        assert commit_type == "feat"
        assert is_breaking is True

    def test_parse_breaking_change_with_scope(self):
        """Test parsing a breaking change with scope and ! notation."""
        commit_type, is_breaking = parse_conventional_commit("feat(api)!: redesign API")
        assert commit_type == "feat"
        assert is_breaking is True

    def test_parse_fix_commit(self):
        """Test parsing a fix commit."""
        commit_type, is_breaking = parse_conventional_commit(
            "fix: correct bug in parser"
        )
        assert commit_type == "fix"
        assert is_breaking is False

    def test_parse_non_conventional_commit(self):
        """Test parsing a non-conventional commit."""
        commit_type, is_breaking = parse_conventional_commit(
            "Some random commit message"
        )
        assert commit_type is None
        assert is_breaking is False

    def test_parse_chore_commit(self):
        """Test parsing a chore commit."""
        commit_type, is_breaking = parse_conventional_commit(
            "chore: update dependencies"
        )
        assert commit_type == "chore"
        assert is_breaking is False


class TestBumpVersion:
    """Tests for calculate_next_version function."""

    def test_bump_patch(self):
        """Test bumping patch version."""
        assert calculate_next_version("1.2.3", "patch") == "1.2.4"

    def test_bump_minor(self):
        """Test bumping minor version."""
        assert calculate_next_version("1.2.3", "minor") == "1.3.0"

    def test_bump_major(self):
        """Test bumping major version."""
        assert calculate_next_version("1.2.3", "major") == "2.0.0"

    def test_bump_version_zero(self):
        """Test bumping version from 0.0.0."""
        assert calculate_next_version("0.0.0", "patch") == "0.0.1"
        assert calculate_next_version("0.0.0", "minor") == "0.1.0"
        assert calculate_next_version("0.0.0", "major") == "1.0.0"


class TestDetectVersionBumpFromCommits:
    """Tests for detect_version_bump_from_commits function."""

    def test_detect_breaking_change(self):
        """Test detecting a breaking change."""
        commits = [{"subject": "feat!: redesign API", "body": ""}]
        assert detect_version_bump_from_commits(commits) == "major"

    def test_detect_breaking_change_body_keyword(self):
        """Test detecting a breaking change via BREAKING CHANGE in body."""
        commits = [
            {
                "subject": "feat: some feature",
                "body": "BREAKING CHANGE: drops Python 3.9",
            }
        ]
        assert detect_version_bump_from_commits(commits) == "major"

    def test_detect_feature(self):
        """Test detecting a feature commit."""
        commits = [{"subject": "feat: add new feature", "body": ""}]
        assert detect_version_bump_from_commits(commits) == "minor"

    def test_detect_bugfix(self):
        """Test detecting a bugfix commit."""
        commits = [{"subject": "fix: correct bug", "body": ""}]
        assert detect_version_bump_from_commits(commits) == "patch"

    def test_detect_mixed_commits(self):
        """Test detecting bump type from mixed commits (breaking takes precedence)."""
        commits = [
            {"subject": "fix: correct bug", "body": ""},
            {"subject": "feat: add feature", "body": ""},
            {"subject": "feat!: breaking change", "body": ""},
        ]
        assert detect_version_bump_from_commits(commits) == "major"

    def test_detect_no_conventional_commits(self):
        """Test detecting bump type with no conventional commits defaults to patch."""
        commits = [
            {"subject": "Some random commit", "body": ""},
            {"subject": "Another commit", "body": ""},
        ]
        assert detect_version_bump_from_commits(commits) == "patch"

    def test_detect_empty_commits(self):
        """Test that an empty commit list defaults to patch."""
        assert detect_version_bump_from_commits([]) == "patch"

    @patch("prepare_release.run_command")
    def test_merge_commits_ignored(self, mock_run):
        """Test that merge commits are not included when getting commits."""
        from prepare_release import get_commits_since_ref

        # Both calls (metadata + batch file list) return a consistent mock.
        # The metadata call uses \x1f/\x1e delimiters; the batch call uses
        # the sentinel marker. Returning the same mock is fine, what matters
        # is that the metadata result is parseable and --no-merges is present.
        metadata_result = MagicMock()
        metadata_result.returncode = 0
        metadata_result.stdout = "abc123\x1fJohn Doe\x1fjohn@example.com\x1f2024-01-15T10:00:00Z\x1ffeat: add feature\x1f\x1e"

        batch_result = MagicMock()
        batch_result.returncode = 0
        batch_result.stdout = "__COMMIT_BOUNDARY__abc123\npkg_a/foo.cpp\n"

        mock_run.side_effect = [metadata_result, batch_result]

        commits = get_commits_since_ref("1.2.3")

        # Verify that git log was called twice (metadata + batch)
        assert mock_run.call_count == 2

        # Both calls must use --no-merges and the same ref range
        for call in mock_run.call_args_list:
            call_args = call[0][0]
            assert "--no-merges" in call_args
            assert "1.2.3..HEAD" in call_args

        # Verify commits were parsed correctly
        assert len(commits) == 1
        assert commits[0]["subject"] == "feat: add feature"
        assert commits[0]["hash"] == "abc123"
        assert commits[0]["author"] == "John Doe"
        assert commits[0]["body"] == ""
        assert commits[0]["files"] == ["pkg_a/foo.cpp"]


class TestUpdatePackageVersions:
    """Tests for update_package_xml_version function."""

    def test_update_single_package(self, temp_dir: Path, package_xml_content: str):
        """Test updating version in a single package.xml."""
        os.chdir(temp_dir)
        (temp_dir / "package.xml").write_text(package_xml_content)

        update_package_xml_version("2.0.0", [])

        updated_content = (temp_dir / "package.xml").read_text()
        assert "<version>2.0.0</version>" in updated_content
        assert "<version>1.2.3</version>" not in updated_content

    def test_update_multiple_packages(self, temp_dir: Path, package_xml_content: str):
        """Test updating versions in multiple package.xml files."""
        os.chdir(temp_dir)

        # Create multiple packages
        pkg1_dir = temp_dir / "package1"
        pkg1_dir.mkdir()
        (pkg1_dir / "package.xml").write_text(package_xml_content)

        pkg2_dir = temp_dir / "package2"
        pkg2_dir.mkdir()
        (pkg2_dir / "package.xml").write_text(package_xml_content)

        update_package_xml_version("3.0.0", [])

        assert "<version>3.0.0</version>" in (pkg1_dir / "package.xml").read_text()
        assert "<version>3.0.0</version>" in (pkg2_dir / "package.xml").read_text()


class TestGenerateChangelogEntry:
    """Tests for generate_changelog_entry function."""

    def test_generate_changelog_with_breaking_changes(self):
        """Test generating changelog with breaking changes."""
        commits = [
            {
                "subject": "feat!: redesign API",
                "body": "BREAKING CHANGE: Old API removed",
                "author": "John Doe",
            },
            {"subject": "feat: add feature", "body": "", "author": "Jane Smith"},
        ]

        entry = generate_changelog_entry("2.0.0", commits, "2024-01-15")

        # REP-0132 format: simple bullet list without subsections
        assert "2.0.0 (2024-01-15)" in entry
        assert "* feat!: redesign API" in entry
        assert "* feat: add feature" in entry
        assert "* Contributors:" in entry
        # Breaking changes should appear first in the list
        lines = entry.split("\n")
        breaking_idx = next(i for i, line in enumerate(lines) if "redesign API" in line)
        feature_idx = next(i for i, line in enumerate(lines) if "add feature" in line)
        assert breaking_idx < feature_idx

    def test_generate_changelog_with_features_and_fixes(self):
        """Test generating changelog with features and fixes."""
        commits = [
            {"subject": "feat: add feature X", "body": "", "author": "John Doe"},
            {"subject": "fix: correct bug Y", "body": "", "author": "Jane Smith"},
            {"subject": "chore: update deps", "body": "", "author": "Bob Jones"},
        ]

        entry = generate_changelog_entry("1.3.0", commits, "2024-01-15")

        # REP-0132 format: all changes in simple bullet list
        assert "1.3.0 (2024-01-15)" in entry
        assert "* feat: add feature X" in entry
        assert "* fix: correct bug Y" in entry
        assert "* chore: update deps" in entry
        # Verify order: feat, fix, then other
        lines = entry.split("\n")
        feat_idx = next(i for i, line in enumerate(lines) if "add feature X" in line)
        fix_idx = next(i for i, line in enumerate(lines) if "correct bug Y" in line)
        chore_idx = next(i for i, line in enumerate(lines) if "update deps" in line)
        assert feat_idx < fix_idx < chore_idx

    def test_generate_changelog_contributors(self):
        """Test that contributors are included in changelog."""
        commits = [
            {"subject": "feat: add feature", "body": "", "author": "John Doe"},
            {"subject": "fix: fix bug", "body": "", "author": "Jane Smith"},
            {"subject": "feat: another feature", "body": "", "author": "John Doe"},
        ]

        entry = generate_changelog_entry("1.3.0", commits, "2024-01-15")

        # REP-0132: Contributors listed at end as single bullet item
        assert "* Contributors:" in entry
        assert "John Doe" in entry
        assert "Jane Smith" in entry
        # Contributors should be last
        lines = entry.split("\n")
        contrib_line = next(line for line in lines if "Contributors:" in line)
        assert (
            "John Doe, Jane Smith" in contrib_line
            or "Jane Smith, John Doe" in contrib_line
        )


class TestGetCommitsSinceRef:
    """Tests for get_commits_since_ref function."""

    def test_get_commits_with_tag(self, temp_git_repo: Path):
        """Test getting commits since a tag."""
        os.chdir(temp_git_repo)

        # Create tag
        subprocess.run(["git", "tag", "1.0.0"], check=True, capture_output=True)

        # Create new commits
        (temp_git_repo / "file1.txt").write_text("content1")
        subprocess.run(["git", "add", "file1.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "feat: add feature"],
            check=True,
            capture_output=True,
        )

        (temp_git_repo / "file2.txt").write_text("content2")
        subprocess.run(["git", "add", "file2.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "fix: fix bug"], check=True, capture_output=True
        )

        commits = get_commits_since_ref("1.0.0")

        # Exactly the 2 commits created after the tag
        assert len(commits) == 2
        subjects = [c["subject"] for c in commits]
        assert "feat: add feature" in subjects
        assert "fix: fix bug" in subjects

    def test_get_commits_no_tag(self, temp_git_repo: Path):
        """Test getting all commits when no ref specified."""
        os.chdir(temp_git_repo)

        (temp_git_repo / "file1.txt").write_text("content1")
        subprocess.run(["git", "add", "file1.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "feat: add feature"],
            check=True,
            capture_output=True,
        )

        commits = get_commits_since_ref(None)

        # fixture has 1 initial commit + 1 new commit = 2 total
        assert len(commits) == 2
        subjects = [c["subject"] for c in commits]
        assert "feat: add feature" in subjects


class TestVersionCalculationFromTag:
    """Tests for version calculation using git tags instead of package.xml."""

    def test_version_calculated_from_tag_not_package_xml(
        self, temp_git_repo: Path, package_xml_content: str
    ):
        """Test that next version is calculated from git tag, not package.xml."""
        os.chdir(temp_git_repo)

        # Create package.xml with version 1.2.3
        (temp_git_repo / "package.xml").write_text(package_xml_content)
        subprocess.run(["git", "add", "package.xml"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"], check=True, capture_output=True
        )

        # Create tag 2.5.0 (different from package.xml version)
        subprocess.run(["git", "tag", "2.5.0"], check=True, capture_output=True)

        # Add a feature commit after the tag
        (temp_git_repo / "feature.txt").write_text("new feature")
        subprocess.run(["git", "add", "feature.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "feat: add new feature"],
            check=True,
            capture_output=True,
        )

        # Get versions
        package_version = get_package_version([])
        last_tag = get_last_tag()

        assert package_version == "1.2.3"
        assert last_tag == "2.5.0"

        # Detect version bump (should be minor due to feat commit)
        commits = get_commits_since_ref(last_tag)
        bump_type = detect_version_bump_from_commits(commits)
        assert bump_type == "minor"

        # Next version should be calculated from tag (2.5.0), not package.xml (1.2.3)
        # Base version should be the tag
        base_version = last_tag if last_tag else package_version
        next_version = calculate_next_version(base_version, bump_type)

        assert next_version == "2.6.0"  # From tag 2.5.0 + minor
        # NOT 1.3.0 (which would be from package.xml 1.2.3 + minor)

    def test_version_from_package_xml_when_no_tag(
        self, temp_git_repo: Path, package_xml_content: str
    ):
        """Test that version falls back to package.xml when no tag exists."""
        os.chdir(temp_git_repo)

        # Create package.xml with version 1.2.3
        (temp_git_repo / "package.xml").write_text(package_xml_content)
        subprocess.run(["git", "add", "package.xml"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"], check=True, capture_output=True
        )

        # Add a feature commit (no tag exists)
        (temp_git_repo / "feature.txt").write_text("new feature")
        subprocess.run(["git", "add", "feature.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "feat: add new feature"],
            check=True,
            capture_output=True,
        )

        # Get versions
        package_version = get_package_version([])
        last_tag = get_last_tag()

        assert package_version == "1.2.3"
        assert last_tag is None

        # Detect version bump
        commits = get_commits_since_ref(last_tag)
        bump_type = detect_version_bump_from_commits(commits)
        assert bump_type == "minor"

        # When no tag exists, use package.xml version
        base_version = last_tag if last_tag else package_version
        next_version = calculate_next_version(base_version, bump_type)

        assert next_version == "1.3.0"  # From package.xml 1.2.3 + minor

    def test_major_bump_from_tag_with_breaking_change(
        self, temp_git_repo: Path, package_xml_content: str
    ):
        """Test major version bump from tag with breaking change."""
        os.chdir(temp_git_repo)

        # Create package.xml with version 0.0.0
        content = package_xml_content.replace(
            "<version>1.2.3</version>", "<version>0.0.0</version>"
        )
        (temp_git_repo / "package.xml").write_text(content)
        subprocess.run(["git", "add", "package.xml"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"], check=True, capture_output=True
        )

        # Create tag 0.1.0
        subprocess.run(["git", "tag", "0.1.0"], check=True, capture_output=True)

        # Add a breaking change after the tag
        (temp_git_repo / "breaking.txt").write_text("breaking change")
        subprocess.run(["git", "add", "breaking.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "feat!: breaking change"],
            check=True,
            capture_output=True,
        )

        # Get versions
        last_tag = get_last_tag()
        assert last_tag == "0.1.0"

        # Detect version bump (should be major due to breaking change)
        commits = get_commits_since_ref(last_tag)
        bump_type = detect_version_bump_from_commits(commits)
        assert bump_type == "major"

        # Next version should be 1.0.0 (from 0.1.0 + major)
        base_version = last_tag
        next_version = calculate_next_version(base_version, bump_type)

        assert next_version == "1.0.0"

    def test_version_from_release_commit_when_no_tag(
        self, temp_git_repo: Path, package_xml_content: str
    ):
        """Test that version uses release commit as baseline when no tag exists."""
        os.chdir(temp_git_repo)

        # Create package.xml with version 0.0.0
        content = package_xml_content.replace(
            "<version>1.2.3</version>", "<version>0.0.0</version>"
        )
        (temp_git_repo / "package.xml").write_text(content)
        subprocess.run(["git", "add", "package.xml"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"], check=True, capture_output=True
        )

        # Add initial features (will be in release 1.0.0)
        (temp_git_repo / "feature1.txt").write_text("feature 1")
        subprocess.run(["git", "add", "feature1.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "feat!: breaking change"],
            check=True,
            capture_output=True,
        )

        (temp_git_repo / "feature2.txt").write_text("feature 2")
        subprocess.run(["git", "add", "feature2.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "feat: add feature"],
            check=True,
            capture_output=True,
        )

        # Update version to 1.0.0 and create release commit
        content = package_xml_content.replace(
            "<version>1.2.3</version>", "<version>1.0.0</version>"
        )
        (temp_git_repo / "package.xml").write_text(content)
        subprocess.run(["git", "add", "package.xml"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "chore(release): prepare release 1.0.0"],
            check=True,
            capture_output=True,
        )

        # Add a new feature after the release (should be in 1.1.0)
        (temp_git_repo / "feature3.txt").write_text("feature 3")
        subprocess.run(["git", "add", "feature3.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "feat: add another feature"],
            check=True,
            capture_output=True,
        )

        # Get versions
        from prepare_release import get_commits_since_ref, get_last_release_commit

        package_version = get_package_version([])
        last_tag = get_last_tag()
        last_release_commit = get_last_release_commit()

        assert package_version == "1.0.0"
        assert last_tag is None  # No tag exists
        assert last_release_commit is not None  # But release commit exists

        # Detect version bump using release commit as baseline
        commits = get_commits_since_ref(last_release_commit)
        bump_type = detect_version_bump_from_commits(commits)
        assert bump_type == "minor"  # Only the new feature should be detected

        # Next version should be 1.1.0 (from package.xml 1.0.0 + minor)
        next_version = calculate_next_version(package_version, bump_type)
        assert next_version == "1.1.0"

        # Verify that only the new commit is analyzed
        commits = get_commits_since_ref(last_release_commit)
        assert len(commits) == 1
        assert commits[0]["subject"] == "feat: add another feature"


class TestGetCommitFilesBatch:
    """Tests for get_commit_files_batch function."""

    @patch("prepare_release.run_command")
    def test_batch_parses_output_correctly(self, mock_run):
        """Test that sentinel-delimited output is correctly parsed into a hash->files dict."""
        sentinel = "__COMMIT_BOUNDARY__"
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            f"{sentinel}abc123\n"
            "pkg_a/foo.cpp\n"
            "pkg_a/foo.hpp\n"
            f"{sentinel}def456\n"
            "pkg_b/bar.cpp\n"
        )
        mock_run.return_value = mock_result

        result = get_commit_files_batch("1.0.0")

        assert result == {
            "abc123": ["pkg_a/foo.cpp", "pkg_a/foo.hpp"],
            "def456": ["pkg_b/bar.cpp"],
        }

    @patch("prepare_release.run_command")
    def test_batch_failure_returns_empty(self, mock_run):
        """Test that a failed git call returns an empty dict."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_run.return_value = mock_result

        result = get_commit_files_batch()
        assert result == {}

    @patch("prepare_release.run_command")
    def test_batch_handles_empty_commit(self, mock_run):
        """Test that an empty commit (no file changes) is absent from the result."""
        sentinel = "__COMMIT_BOUNDARY__"
        mock_result = MagicMock()
        mock_result.returncode = 0
        # abc123 has files; def456 is an empty commit with no files
        mock_result.stdout = f"{sentinel}abc123\npkg_a/foo.cpp\n{sentinel}def456\n"
        mock_run.return_value = mock_result

        result = get_commit_files_batch()
        assert result == {"abc123": ["pkg_a/foo.cpp"], "def456": []}


class TestCommitTouchesPackage:
    """Tests for commit_touches_package function."""

    def test_commit_inside_package(self, temp_dir: Path):
        """Test that a commit touching a file inside the package returns True."""
        commit = {"files": ["pkg_a/src/foo.cpp", "pkg_a/include/foo.hpp"]}
        assert commit_touches_package(commit, Path("pkg_a")) is True

    def test_commit_outside_package(self, temp_dir: Path):
        """Test that a commit touching only other packages returns False."""
        commit = {"files": ["pkg_b/src/bar.cpp"]}
        assert commit_touches_package(commit, Path("pkg_a")) is False

    def test_commit_partial_match(self, temp_dir: Path):
        """Test that a commit touching one file inside and one outside returns True."""
        commit = {"files": ["pkg_a/src/foo.cpp", "pkg_b/src/bar.cpp"]}
        assert commit_touches_package(commit, Path("pkg_a")) is True

    def test_package_at_repo_root(self, temp_dir: Path):
        """Test that a single-package repo at the root matches any file."""
        commit = {"files": ["src/foo.cpp", "CMakeLists.txt"]}
        assert commit_touches_package(commit, Path(".")) is True

    def test_commit_no_files(self):
        """Test that a commit with no file changes touches no package."""
        commit = {"files": []}
        assert commit_touches_package(commit, Path("pkg_a")) is False

    def test_missing_files_key_raises(self):
        """Test that a commit dict without 'files' raises KeyError."""
        commit = {"subject": "feat: add feature", "body": ""}
        with pytest.raises(KeyError):
            commit_touches_package(commit, Path("pkg_a"))


class TestFilterCommitsForPackage:
    """Tests for filter_commits_for_package function."""

    def test_filter_subset(self):
        """Test that only commits touching the package are returned."""
        commits = [
            {"subject": "fix: pkg_a fix", "files": ["pkg_a/foo.cpp"]},
            {"subject": "feat: pkg_b feat", "files": ["pkg_b/bar.cpp"]},
            {"subject": "fix: both fix", "files": ["pkg_a/x.cpp", "pkg_b/y.cpp"]},
        ]
        result = filter_commits_for_package(commits, Path("pkg_a"))
        assert len(result) == 2
        subjects = [c["subject"] for c in result]
        assert "fix: pkg_a fix" in subjects
        assert "fix: both fix" in subjects
        assert "feat: pkg_b feat" not in subjects


class TestUpdateChangelogFilesMultiPackage:
    """Integration tests for per-package changelog filtering."""

    def test_changelogs_contain_only_package_commits(
        self, temp_git_repo: Path, package_xml_content: str
    ):
        """Test that each package's changelog only contains commits touching its files."""
        os.chdir(temp_git_repo)

        # Set up two packages
        pkg_a = temp_git_repo / "pkg_a"
        pkg_b = temp_git_repo / "pkg_b"
        pkg_a.mkdir()
        pkg_b.mkdir()
        (pkg_a / "package.xml").write_text(package_xml_content)
        (pkg_b / "package.xml").write_text(package_xml_content)
        subprocess.run(["git", "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "chore: add packages"],
            check=True,
            capture_output=True,
        )

        # Commit 1: only touches pkg_a
        (pkg_a / "foo.cpp").write_text("// pkg_a only")
        subprocess.run(["git", "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "fix: fix pkg_a bug"],
            check=True,
            capture_output=True,
        )

        # Commit 2: only touches pkg_b
        (pkg_b / "bar.cpp").write_text("// pkg_b only")
        subprocess.run(["git", "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "feat: add pkg_b feature"],
            check=True,
            capture_output=True,
        )

        # Commit 3: touches both
        (pkg_a / "shared.cpp").write_text("// both")
        (pkg_b / "shared.cpp").write_text("// both")
        subprocess.run(["git", "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "refactor: shared refactor"],
            check=True,
            capture_output=True,
        )

        commits = get_commits_since_ref(None)
        # Filter to only our test commits (exclude the setup commit)
        commits = [c for c in commits if "chore: add packages" not in c["subject"]]
        update_changelog_files("1.3.0", commits, [])

        pkg_a_log = (pkg_a / "CHANGELOG.rst").read_text()
        pkg_b_log = (pkg_b / "CHANGELOG.rst").read_text()

        # Version header present in both
        assert "1.3.0 (" in pkg_a_log
        assert "1.3.0 (" in pkg_b_log

        # pkg_a changelog: has pkg_a-only and shared, NOT pkg_b-only
        assert "fix: fix pkg_a bug" in pkg_a_log
        assert "refactor: shared refactor" in pkg_a_log
        assert "feat: add pkg_b feature" not in pkg_a_log

        # pkg_b changelog: has pkg_b-only and shared, NOT pkg_a-only
        assert "feat: add pkg_b feature" in pkg_b_log
        assert "refactor: shared refactor" in pkg_b_log
        assert "fix: fix pkg_a bug" not in pkg_b_log

    def test_package_with_no_changes_gets_empty_entry(
        self, temp_git_repo: Path, package_xml_content: str
    ):
        """Test that a package with no touching commits gets a header-only entry."""
        os.chdir(temp_git_repo)

        pkg_a = temp_git_repo / "pkg_a"
        pkg_b = temp_git_repo / "pkg_b"
        pkg_a.mkdir()
        pkg_b.mkdir()
        (pkg_a / "package.xml").write_text(package_xml_content)
        (pkg_b / "package.xml").write_text(package_xml_content)
        subprocess.run(["git", "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "chore: add packages"],
            check=True,
            capture_output=True,
        )

        # Only pkg_a is touched
        (pkg_a / "foo.cpp").write_text("change")
        subprocess.run(["git", "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "feat: pkg_a only"], check=True, capture_output=True
        )

        commits = get_commits_since_ref(None)
        commits = [c for c in commits if "chore: add packages" not in c["subject"]]
        update_changelog_files("1.1.0", commits, [])

        pkg_b_log = (pkg_b / "CHANGELOG.rst").read_text()

        # pkg_b gets a version header but no bullet points
        assert "1.1.0 (" in pkg_b_log
        assert "* " not in pkg_b_log

    def test_repo_wide_commits_dropped(
        self, temp_git_repo: Path, package_xml_content: str
    ):
        """Test that commits not touching any package directory are excluded."""
        os.chdir(temp_git_repo)

        pkg_a = temp_git_repo / "pkg_a"
        pkg_a.mkdir()
        (pkg_a / "package.xml").write_text(package_xml_content)
        subprocess.run(["git", "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "chore: add package"],
            check=True,
            capture_output=True,
        )

        # Repo-wide commit: only touches root README
        (temp_git_repo / "README.md").write_text("updated")
        subprocess.run(["git", "add", "README.md"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "docs: update root README"],
            check=True,
            capture_output=True,
        )

        commits = get_commits_since_ref(None)
        commits = [c for c in commits if "chore: add package" not in c["subject"]]
        update_changelog_files("1.0.1", commits, [])

        pkg_a_log = (pkg_a / "CHANGELOG.rst").read_text()

        # The root README commit must NOT appear in any package changelog
        assert "docs: update root README" not in pkg_a_log

    def test_fixture_excluded_from_changelog_discovery(
        self, temp_git_repo: Path, package_xml_content: str
    ):
        """Test end-to-end that a fixture package.xml is not processed when excluded."""
        os.chdir(temp_git_repo)

        # Real package
        pkg_a = temp_git_repo / "pkg_a"
        pkg_a.mkdir()
        (pkg_a / "package.xml").write_text(package_xml_content)

        # Test fixture, same version so it passes validation, but should
        # not receive a CHANGELOG.rst when its directory is excluded
        fixture_dir = temp_git_repo / "test" / "fixture_pkg"
        fixture_dir.mkdir(parents=True)
        (fixture_dir / "package.xml").write_text(package_xml_content)

        subprocess.run(["git", "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "chore: add packages"],
            check=True,
            capture_output=True,
        )

        (pkg_a / "foo.cpp").write_text("change")
        subprocess.run(["git", "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "feat: add feature"],
            check=True,
            capture_output=True,
        )

        commits = get_commits_since_ref(None)
        commits = [c for c in commits if "chore: add packages" not in c["subject"]]

        # Exclude the test fixture directory via exclude_patterns
        update_changelog_files("1.1.0", commits, ["test/**"])

        # Real package gets a CHANGELOG.rst
        assert (pkg_a / "CHANGELOG.rst").exists()

        # Fixture dir does NOT get a CHANGELOG.rst
        assert not (fixture_dir / "CHANGELOG.rst").exists()


class TestGeneratePrSummary:
    """Tests for generate_pr_summary function."""

    def test_empty_commits(self):
        """Test that an empty commit list produces 'No changes'."""
        assert generate_pr_summary([]) == "No changes"

    def test_only_breaking_changes(self):
        """Test that only breaking commits produce only the BREAKING CHANGES section."""
        commits = [{"subject": "feat!: redesign API", "body": ""}]
        summary = generate_pr_summary(commits)
        assert "### BREAKING CHANGES" in summary
        assert "* feat!: redesign API" in summary
        assert "### Features" not in summary
        assert "### Bug Fixes" not in summary
        assert "### Other Changes" not in summary

    def test_only_feature(self):
        """Test that a single feature produces only the Features section."""
        commits = [{"subject": "feat: add feature", "body": ""}]
        summary = generate_pr_summary(commits)
        assert "### Features" in summary
        assert "* feat: add feature" in summary
        assert "### BREAKING CHANGES" not in summary
        assert "### Bug Fixes" not in summary
        assert "### Other Changes" not in summary

    def test_non_conventional_commits_go_to_other(self):
        """Test that non-conventional commits are placed in Other Changes."""
        commits = [{"subject": "Some random commit", "body": ""}]
        summary = generate_pr_summary(commits)
        assert "### Other Changes" in summary
        assert "* Some random commit" in summary
        assert "### Features" not in summary

    def test_mixed_commits_sections_in_order(self):
        """Test that mixed commits produce all sections in priority order."""
        commits = [
            {"subject": "fix: bug fix", "body": ""},
            {"subject": "feat: add feature", "body": ""},
            {"subject": "chore: misc", "body": ""},
            {"subject": "feat!: breaking change", "body": ""},
        ]
        summary = generate_pr_summary(commits)

        assert "### BREAKING CHANGES" in summary
        assert "### Features" in summary
        assert "### Bug Fixes" in summary
        assert "### Other Changes" in summary

        # Order: breaking → features → fixes → other
        breaking_pos = summary.index("### BREAKING CHANGES")
        features_pos = summary.index("### Features")
        fixes_pos = summary.index("### Bug Fixes")
        other_pos = summary.index("### Other Changes")
        assert breaking_pos < features_pos < fixes_pos < other_pos


class TestIsReleaseCommit:
    """Tests for is_release_commit() self-no-op guard."""

    def _make_repo_with_commit(self, repo: "Path", message: str) -> None:
        """Helper: create a git repo with a single commit using the given message."""
        import subprocess as _sp

        (repo / "file.txt").write_text("x")
        _sp.run(["git", "add", "file.txt"], check=True, capture_output=True, cwd=repo)
        _sp.run(
            ["git", "commit", "-m", message],
            check=True,
            capture_output=True,
            cwd=repo,
        )

    def test_rebase_merge_release_commit(self, temp_git_repo: "Path"):
        """Rebase-merge shape: chore(release): prepare release X.Y.Z"""
        os.chdir(temp_git_repo)
        self._make_repo_with_commit(
            temp_git_repo, "chore(release): prepare release 1.2.0"
        )
        assert is_release_commit() is True

    def test_squash_merge_release_commit(self, temp_git_repo: "Path"):
        """Squash-merge shape: chore(release): X.Y.Z (PR title)"""
        os.chdir(temp_git_repo)
        self._make_repo_with_commit(temp_git_repo, "chore(release): 1.2.0")
        assert is_release_commit() is True

    def test_regular_feat_commit(self, temp_git_repo: "Path"):
        """A normal feature commit must not trigger the no-op."""
        os.chdir(temp_git_repo)
        self._make_repo_with_commit(temp_git_repo, "feat: add cool feature")
        assert is_release_commit() is False

    def test_regular_fix_commit(self, temp_git_repo: "Path"):
        """A normal fix commit must not trigger the no-op."""
        os.chdir(temp_git_repo)
        self._make_repo_with_commit(temp_git_repo, "fix: correct version parsing")
        assert is_release_commit() is False

    def test_chore_non_release(self, temp_git_repo: "Path"):
        """A chore commit without (release) scope must not trigger the no-op."""
        os.chdir(temp_git_repo)
        self._make_repo_with_commit(temp_git_repo, "chore: update dependencies")
        assert is_release_commit() is False


class TestPrepareConfig:
    """Tests for prepare-mode config fallback and overrides."""

    @patch("prepare_release.set_output")
    @patch("prepare_release.create_or_update_release_pr")
    @patch("prepare_release.calculate_next_version")
    @patch("prepare_release.detect_version_bump_from_commits")
    @patch("prepare_release.get_commits_since_ref")
    @patch("prepare_release.get_last_release_commit")
    @patch("prepare_release.get_last_tag")
    @patch("prepare_release.get_package_version")
    @patch("prepare_release.is_release_commit")
    @patch("prepare_release.parse_args")
    def test_prepare_uses_config_defaults(
        self,
        mock_parse_args,
        mock_is_release_commit,
        mock_get_package_version,
        mock_get_last_tag,
        mock_get_last_release_commit,
        mock_get_commits_since_ref,
        mock_detect_version_bump,
        mock_calculate_next_version,
        mock_create_or_update_release_pr,
        mock_set_output,
        temp_dir: Path,
    ) -> None:
        """Test prepare mode falls back to config file values."""
        os.chdir(temp_dir)
        config_dir = temp_dir / ".github"
        config_dir.mkdir()
        (config_dir / "bloom-release.toml").write_text(
            '[prepare]\nbase_branch = "jazzy"\nversion_bump = "minor"\n'
        )

        mock_parse_args.return_value = MagicMock(
            config_file=".github/bloom-release.toml",
            base_branch="",
            version_bump="",
        )
        mock_is_release_commit.return_value = False
        mock_get_package_version.return_value = "1.2.3"
        mock_get_last_tag.return_value = "1.2.3"
        mock_get_last_release_commit.return_value = None
        mock_get_commits_since_ref.return_value = [
            {"subject": "feat: add feature", "body": ""}
        ]
        mock_detect_version_bump.return_value = "minor"
        mock_calculate_next_version.return_value = "1.3.0"
        mock_create_or_update_release_pr.return_value = (
            "https://github.com/example/pull/1",
            True,
        )

        from prepare_release import main

        main()

        assert mock_calculate_next_version.call_args[0][1] == "minor"
        assert mock_create_or_update_release_pr.call_args[0][0] == "jazzy"
        mock_set_output.assert_any_call("version", "1.3.0")

    @patch("prepare_release.set_output")
    @patch("prepare_release.create_or_update_release_pr")
    @patch("prepare_release.calculate_next_version")
    @patch("prepare_release.get_commits_since_ref")
    @patch("prepare_release.get_last_release_commit")
    @patch("prepare_release.get_last_tag")
    @patch("prepare_release.get_package_version")
    @patch("prepare_release.is_release_commit")
    @patch("prepare_release.parse_args")
    def test_prepare_inputs_override_config(
        self,
        mock_parse_args,
        mock_is_release_commit,
        mock_get_package_version,
        mock_get_last_tag,
        mock_get_last_release_commit,
        mock_get_commits_since_ref,
        mock_calculate_next_version,
        mock_create_or_update_release_pr,
        mock_set_output,
        temp_dir: Path,
    ) -> None:
        """Test direct prepare inputs override config values."""
        os.chdir(temp_dir)
        config_dir = temp_dir / ".github"
        config_dir.mkdir()
        (config_dir / "bloom-release.toml").write_text(
            '[prepare]\nbase_branch = "jazzy"\nversion_bump = "minor"\n'
        )

        mock_parse_args.return_value = MagicMock(
            config_file=".github/bloom-release.toml",
            base_branch="rolling",
            version_bump="patch",
        )
        mock_is_release_commit.return_value = False
        mock_get_package_version.return_value = "1.2.3"
        mock_get_last_tag.return_value = "1.2.3"
        mock_get_last_release_commit.return_value = None
        mock_get_commits_since_ref.return_value = [
            {"subject": "feat: add feature", "body": ""}
        ]
        mock_calculate_next_version.return_value = "1.2.4"
        mock_create_or_update_release_pr.return_value = (
            "https://github.com/example/pull/1",
            True,
        )

        from prepare_release import main

        main()

        assert mock_calculate_next_version.call_args[0][1] == "patch"
        assert mock_create_or_update_release_pr.call_args[0][0] == "rolling"
        mock_set_output.assert_any_call("version", "1.2.4")
