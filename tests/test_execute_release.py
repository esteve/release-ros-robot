"""Tests for execute_release.py script."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from common import get_last_tag, get_package_version, is_release_commit
from execute_release import (
    check_track_exists,
    get_current_branch,
    run_bloom_release,
)


class TestGetCurrentBranch:
    """Tests for get_current_branch function."""

    def test_get_current_branch(self, temp_git_repo: Path):
        """Test getting the current branch name."""
        os.chdir(temp_git_repo)
        subprocess.run(
            ["git", "checkout", "-b", "test-branch"], check=True, capture_output=True
        )

        branch = get_current_branch()
        assert branch == "test-branch"


class TestCheckTrackExists:
    """Tests for check_track_exists function."""

    @patch("execute_release.run_command")
    def test_track_exists(self, mock_run):
        """Test checking if a track exists."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "tracks:\n- rolling\n- jazzy\n"
        mock_run.return_value = mock_result

        result = check_track_exists("https://github.com/test/repo.git", "rolling")
        assert result is True

    @patch("execute_release.run_command")
    def test_track_does_not_exist(self, mock_run):
        """Test checking if a track doesn't exist."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "tracks:\n- jazzy\n"
        mock_run.return_value = mock_result

        result = check_track_exists("https://github.com/test/repo.git", "rolling")
        assert result is False

    @patch("execute_release.run_command")
    def test_track_check_error(self, mock_run):
        """Test checking track when command fails."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_run.return_value = mock_result

        result = check_track_exists("https://github.com/test/repo.git", "rolling")
        assert result is False


class TestRunBloomRelease:
    """Tests for run_bloom_release function."""

    @patch("execute_release.run_command")
    def test_run_bloom_release_calls_bloom(self, mock_run):
        """Test that bloom-release is invoked with the expected arguments."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Success"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        run_bloom_release(
            repo_name="test_package",
            rosdistro="rolling",
            track="rolling",
            release_repo="https://github.com/test/repo.git",
        )

        # Verify bloom-release was called
        assert any("bloom-release" in str(call) for call in mock_run.call_args_list)

    @patch("execute_release.run_command")
    def test_run_bloom_release_passes_release_repo(self, mock_run):
        """Test that --override-release-repository-url is always passed."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Success"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        run_bloom_release(
            repo_name="test_package",
            rosdistro="rolling",
            track="rolling",
            release_repo="https://github.com/ros2-gbp/test_package-release.git",
        )

        bloom_calls = [
            call for call in mock_run.call_args_list if "bloom-release" in str(call)
        ]
        assert len(bloom_calls) > 0
        for call_obj in bloom_calls:
            args = call_obj[0][0] if call_obj[0] else []
            assert "--override-release-repository-url" in args
            assert "https://github.com/ros2-gbp/test_package-release.git" in args

    @patch("execute_release.run_command")
    def test_run_bloom_release_no_new_track_by_default(self, mock_run):
        """Test that --new-track is not passed when new_track=False (default)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Success"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        run_bloom_release(
            repo_name="test_package",
            rosdistro="rolling",
            track="rolling",
            release_repo="https://github.com/ros2-gbp/test_package-release.git",
        )

        bloom_calls = [
            call for call in mock_run.call_args_list if "bloom-release" in str(call)
        ]
        assert len(bloom_calls) > 0
        for call_obj in bloom_calls:
            args = call_obj[0][0] if call_obj[0] else []
            assert "--new-track" not in args

    @patch("execute_release.run_command")
    def test_run_bloom_release_new_track(self, mock_run):
        """Test that --new-track is passed when new_track=True."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Success"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        run_bloom_release(
            repo_name="test_package",
            rosdistro="rolling",
            track="rolling",
            release_repo="https://github.com/ros2-gbp/test_package-release.git",
            new_track=True,
        )

        bloom_calls = [
            call for call in mock_run.call_args_list if "bloom-release" in str(call)
        ]
        assert len(bloom_calls) > 0
        found_new_track = any(
            "--new-track" in (call_obj[0][0] if call_obj[0] else [])
            for call_obj in bloom_calls
        )
        assert found_new_track


class TestIntegration:
    """Integration tests for execute_release.py."""

    @patch("execute_release.run_command")
    @patch("execute_release.check_track_exists")
    def test_version_and_branch_in_temp_repo(
        self,
        mock_check,
        mock_run,
        temp_git_repo: Path,
        package_xml_content: str,
    ):
        """Test that get_package_version and get_current_branch work in a temp git repo."""
        os.chdir(temp_git_repo)

        # Setup package.xml
        (temp_git_repo / "package.xml").write_text(package_xml_content)

        mock_check.return_value = True
        mock_run.side_effect = lambda cmd, **kwargs: subprocess.run(
            cmd, capture_output=True, text=True, check=False
        )

        version = get_package_version([])
        branch = get_current_branch()

        assert version == "1.2.3"
        assert branch in ["main", "master"]  # Git default branch


class TestReleaseNoOp:
    """Tests for the release self-no-op guard.

    The guard is commit-driven: non-release commits are no-ops regardless of
    tag state.  This allows multiple release steps for different tracks on the
    same release commit to all proceed, an existing tag from the first step
    does not cause subsequent steps to skip.
    """

    def _commit(self, repo: Path, message: str, filename: str = "file.txt") -> None:
        """Helper: add a file and commit with the given message."""
        (repo / filename).write_text(message)
        subprocess.run(["git", "add", filename], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", message], check=True, capture_output=True
        )

    def test_non_release_commit_is_no_op(
        self, temp_git_repo: Path, package_xml_content: str
    ):
        """A regular push (non-release commit) → is_release_commit() is False."""
        os.chdir(temp_git_repo)
        (temp_git_repo / "package.xml").write_text(package_xml_content)
        self._commit(temp_git_repo, "feat: add feature", "feature.txt")

        assert is_release_commit() is False

    def test_release_commit_with_no_tag_proceeds(
        self, temp_git_repo: Path, package_xml_content: str
    ):
        """Release commit + no existing tag → is_release_commit() True, no tag yet."""
        os.chdir(temp_git_repo)
        (temp_git_repo / "package.xml").write_text(package_xml_content)
        subprocess.run(["git", "add", "package.xml"], check=True, capture_output=True)
        self._commit(temp_git_repo, "chore(release): prepare release 1.2.3", "rel.txt")

        assert is_release_commit() is True
        assert get_last_tag() is None
        assert get_package_version([]) == "1.2.3"

    def test_release_commit_with_existing_tag_still_proceeds(
        self, temp_git_repo: Path, package_xml_content: str
    ):
        """Release commit + tag already exists → is_release_commit() True.

        This is the multi-track case: a second release step for a different
        track should still run bloom even though the first step already created
        the tag.
        """
        os.chdir(temp_git_repo)
        (temp_git_repo / "package.xml").write_text(package_xml_content)
        subprocess.run(["git", "add", "package.xml"], check=True, capture_output=True)
        self._commit(temp_git_repo, "chore(release): prepare release 1.2.3", "rel.txt")
        subprocess.run(
            ["git", "tag", "-a", "1.2.3", "-m", "Release 1.2.3"],
            check=True,
            capture_output=True,
        )

        # Tag already exists but commit is still a release commit → proceed
        assert is_release_commit() is True
        assert get_last_tag() == "1.2.3"
        assert get_package_version([]) == "1.2.3"

    def test_non_release_commit_with_existing_tag_is_no_op(
        self, temp_git_repo: Path, package_xml_content: str
    ):
        """Non-release push after a release → no-op even if tag matches version.

        Verifies that a later unrelated commit on the same branch does not
        retrigger bloom just because the tag still exists.
        """
        os.chdir(temp_git_repo)
        (temp_git_repo / "package.xml").write_text(package_xml_content)
        subprocess.run(["git", "add", "package.xml"], check=True, capture_output=True)
        self._commit(temp_git_repo, "chore(release): prepare release 1.2.3", "rel.txt")
        subprocess.run(
            ["git", "tag", "-a", "1.2.3", "-m", "Release 1.2.3"],
            check=True,
            capture_output=True,
        )
        # Subsequent non-release commit
        self._commit(temp_git_repo, "fix: post-release fix", "fix.txt")

        assert is_release_commit() is False
        assert get_last_tag() == "1.2.3"
