"""Tests for execute_release.py script."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from common import get_last_tag, get_package_version, is_release_commit
from execute_release import (
    check_track_exists,
    ensure_release_tag,
    extract_rosdistro_pr_url,
    get_local_tag_target,
    get_package_names,
    get_remote_tag_target,
    is_release_repo_push_conflict,
    parse_targets_yaml,
    parse_remote_tag_target,
    parse_track_list,
    release_repo_has_expected_release_tags,
    resolve_release_targets,
    run_single_target_release,
    run_bloom_release,
)


class TestGitBranchState:
    """Tests for git branch state used by execute_release.py."""

    def test_current_branch_in_temp_repo(self, temp_git_repo: Path) -> None:
        """Test the temporary repository reports the expected current branch."""
        os.chdir(temp_git_repo)
        subprocess.run(
            ["git", "checkout", "-b", "test-branch"], check=True, capture_output=True
        )

        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert branch == "test-branch"


class TestParseTrackList:
    """Tests for parse_track_list function."""

    def test_parse_track_list_from_available_tracks_output(self) -> None:
        """Test parsing bloom's Available tracks output."""
        output = "Available tracks: ['rolling', 'jazzy']\n"

        result = parse_track_list(output)

        assert result == {"rolling", "jazzy"}

    def test_parse_track_list_returns_none_for_unparseable_output(self) -> None:
        """Test parse_track_list returns None when output is unparseable."""
        result = parse_track_list("tracks:\n- rolling\n")

        assert result is None


class TestRemoteTagParsing:
    """Tests for remote tag parsing helpers."""

    def test_parse_remote_tag_target_prefers_peeled_ref(self) -> None:
        """Test peeled annotated-tag refs are preferred over tag object refs."""
        output = (
            "1111111111111111111111111111111111111111 refs/tags/1.2.3\n"
            "2222222222222222222222222222222222222222 refs/tags/1.2.3^{}\n"
        )

        result = parse_remote_tag_target(output, "1.2.3")

        assert result == "2222222222222222222222222222222222222222"

    def test_parse_remote_tag_target_uses_direct_ref_when_not_annotated(self) -> None:
        """Test lightweight tags resolve from the direct remote ref."""
        output = "3333333333333333333333333333333333333333 refs/tags/1.2.3\n"

        result = parse_remote_tag_target(output, "1.2.3")

        assert result == "3333333333333333333333333333333333333333"


class TestReleaseRepoPushConflict:
    """Tests for release-repository push conflict detection."""

    def test_detect_release_repo_push_conflict(self) -> None:
        """Test detecting bloom's fetch-first push-race failure output."""
        output = (
            "error: failed to push some refs to 'https://github.com/example/repo.git'\n"
            "! [rejected] master -> master (fetch first)\n"
            "Pushing changes failed, would you like to add '--force' to 'git push --all'?\n"
        )

        result = is_release_repo_push_conflict(output)

        assert result is True

    def test_ignore_non_conflict_failures(self) -> None:
        """Test unrelated bloom failures do not look like push races."""
        result = is_release_repo_push_conflict("permission denied")

        assert result is False


class TestExtractRosdistroPrUrl:
    """Tests for rosdistro PR URL extraction."""

    def test_extract_rosdistro_pr_url(self) -> None:
        """Test extracting a rosdistro PR URL from bloom output."""
        result = extract_rosdistro_pr_url(
            "Opened pull request https://github.com/ros/rosdistro/pull/123\n"
        )

        assert result == "https://github.com/ros/rosdistro/pull/123"

    def test_extract_rosdistro_pr_url_missing(self) -> None:
        """Test missing PR URLs return None."""
        result = extract_rosdistro_pr_url("no pr here")

        assert result is None


class TestPackageNames:
    """Tests for package name discovery."""

    def test_get_package_names(self, temp_dir: Path) -> None:
        """Test package names are discovered from package.xml files."""
        os.chdir(temp_dir)
        (temp_dir / "pkg_a").mkdir()
        (temp_dir / "pkg_b").mkdir()
        (temp_dir / "pkg_a" / "package.xml").write_text(
            "<package><name>pkg_a</name><version>1.2.3</version></package>"
        )
        (temp_dir / "pkg_b" / "package.xml").write_text(
            "<package><name>pkg_b</name><version>1.2.3</version></package>"
        )

        result = get_package_names([])

        assert result == ["pkg_a", "pkg_b"]


class TestTargetsYaml:
    """Tests for YAML batch target parsing."""

    def test_parse_targets_yaml(self) -> None:
        """Test parsing branch-mapped sequential targets."""
        parsed = parse_targets_yaml(
            "main:\n"
            "  - rosdistro: rolling\n"
            "    track: rolling\n"
            "  - rosdistro: jazzy\n"
            "    track: jazzy\n"
        )

        assert parsed == {
            "main": [
                {"rosdistro": "rolling", "track": "rolling"},
                {"rosdistro": "jazzy", "track": "jazzy"},
            ]
        }

    def test_resolve_release_targets_for_current_branch(self) -> None:
        """Test target resolution preserves order for the current branch."""
        targets = resolve_release_targets(
            current_branch="main",
            rosdistro=None,
            track=None,
            targets_text=(
                "main:\n"
                "  - rosdistro: rolling\n"
                "    track: rolling\n"
                "  - rosdistro: jazzy\n"
                "    track: jazzy\n"
                "humble:\n"
                "  - rosdistro: humble\n"
                "    track: humble\n"
            ),
        )

        assert targets == [
            {"rosdistro": "rolling", "track": "rolling"},
            {"rosdistro": "jazzy", "track": "jazzy"},
        ]

    def test_resolve_release_targets_for_unmatched_branch(self) -> None:
        """Test unmatched branches resolve to an empty target list."""
        targets = resolve_release_targets(
            current_branch="other",
            rosdistro=None,
            track=None,
            targets_text="main:\n  - rosdistro: rolling\n    track: rolling\n",
        )

        assert targets == []

    def test_resolve_release_targets_single_target_mode(self) -> None:
        """Test explicit rosdistro/track inputs still resolve as one target."""
        targets = resolve_release_targets(
            current_branch="main",
            rosdistro="rolling",
            track="rolling",
            targets_text=None,
        )

        assert targets == [{"rosdistro": "rolling", "track": "rolling"}]


class TestReleaseRepoVerification:
    """Tests for release-repository state verification."""

    @patch("execute_release.run_command")
    def test_release_repo_has_expected_release_tags(self, mock_run) -> None:
        """Test remote release tags are matched per track, package, and version."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            "abc refs/tags/release/jazzy/pkg_a/1.2.3-1\n"
            "def refs/tags/release/jazzy/pkg_b/1.2.3-2\n"
        )
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        result = release_repo_has_expected_release_tags(
            release_repo="https://github.com/test/repo-release.git",
            track="jazzy",
            version="1.2.3",
            package_names=["pkg_a", "pkg_b"],
        )

        assert result is True

    @patch("execute_release.run_command")
    def test_release_repo_missing_expected_release_tags(self, mock_run) -> None:
        """Test missing per-package release tags fail verification."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "abc refs/tags/release/jazzy/pkg_a/1.2.3-1\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        result = release_repo_has_expected_release_tags(
            release_repo="https://github.com/test/repo-release.git",
            track="jazzy",
            version="1.2.3",
            package_names=["pkg_a", "pkg_b"],
        )

        assert result is False


class TestRunSingleTargetRelease:
    """Tests for single-target release execution."""

    @patch("execute_release.run_bloom_release")
    @patch("execute_release.check_track_exists")
    def test_run_single_target_release_existing_track(
        self, mock_check_track_exists, mock_run_bloom_release
    ) -> None:
        """Test single-target execution passes through the resolved bloom settings."""
        mock_check_track_exists.return_value = True
        mock_run_bloom_release.return_value = (
            "https://github.com/ros/rosdistro/pull/123"
        )

        result = run_single_target_release(
            repo_name="test_package",
            rosdistro="rolling",
            track="rolling",
            release_repo="https://github.com/ros2-gbp/test_package-release.git",
            version="1.2.3",
            package_names=["test_package"],
            dry_run=False,
        )

        assert result == "https://github.com/ros/rosdistro/pull/123"
        mock_run_bloom_release.assert_called_once_with(
            repo_name="test_package",
            rosdistro="rolling",
            track="rolling",
            release_repo="https://github.com/ros2-gbp/test_package-release.git",
            version="1.2.3",
            package_names=["test_package"],
            dry_run=False,
            new_track=False,
        )


class TestTagHelpers:
    """Tests for release tag helper functions."""

    @patch("execute_release.run_command")
    def test_get_local_tag_target(self, mock_run) -> None:
        """Test resolving the commit targeted by a local tag."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "abc123\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        result = get_local_tag_target("1.2.3")

        assert result == "abc123"

    @patch("execute_release.run_command")
    def test_get_remote_tag_target(self, mock_run) -> None:
        """Test resolving the commit targeted by a remote annotated tag."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            "1111111111111111111111111111111111111111 refs/tags/1.2.3\n"
            "abc123 refs/tags/1.2.3^{}\n"
        )
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        result = get_remote_tag_target("1.2.3")

        assert result == "abc123"


class TestEnsureReleaseTag:
    """Tests for ensure_release_tag function."""

    @patch("execute_release.run_command")
    def test_ensure_release_tag_success(self, mock_run) -> None:
        """Test normal tag creation and push success."""
        head_result = MagicMock(returncode=0, stdout="head123\n", stderr="")
        create_result = MagicMock(returncode=0, stdout="", stderr="")
        push_result = MagicMock(returncode=0, stdout="", stderr="")
        mock_run.side_effect = [head_result, create_result, push_result]

        result = ensure_release_tag("1.2.3")

        assert result is True

    @patch("execute_release.get_local_tag_target")
    @patch("execute_release.run_command")
    def test_ensure_release_tag_local_race_same_head(
        self, mock_run, mock_get_local_tag_target
    ) -> None:
        """Test local tag creation failure is tolerated when tag points to HEAD."""
        head_result = MagicMock(returncode=0, stdout="head123\n", stderr="")
        create_result = MagicMock(returncode=128, stdout="", stderr="tag exists")
        push_result = MagicMock(returncode=0, stdout="", stderr="")
        mock_run.side_effect = [head_result, create_result, push_result]
        mock_get_local_tag_target.return_value = "head123"

        result = ensure_release_tag("1.2.3")

        assert result is True

    @patch("execute_release.log_error")
    @patch("execute_release.get_local_tag_target")
    @patch("execute_release.run_command")
    def test_ensure_release_tag_local_race_wrong_head(
        self, mock_run, mock_get_local_tag_target, mock_log_error
    ) -> None:
        """Test local tag creation failure aborts when tag points elsewhere."""
        head_result = MagicMock(returncode=0, stdout="head123\n", stderr="")
        create_result = MagicMock(returncode=128, stdout="", stderr="tag exists")
        mock_run.side_effect = [head_result, create_result]
        mock_get_local_tag_target.return_value = "other456"

        result = ensure_release_tag("1.2.3")

        assert result is False
        logged_messages = [call.args[0] for call in mock_log_error.call_args_list]
        assert any(
            "Local tag 1.2.3 points to other456, expected head123" in msg
            for msg in logged_messages
        )

    @patch("execute_release.get_remote_tag_target")
    @patch("execute_release.run_command")
    def test_ensure_release_tag_remote_race_same_head(
        self, mock_run, mock_get_remote_tag_target
    ) -> None:
        """Test push failure is tolerated when remote tag already points to HEAD."""
        head_result = MagicMock(returncode=0, stdout="head123\n", stderr="")
        create_result = MagicMock(returncode=0, stdout="", stderr="")
        push_result = MagicMock(returncode=1, stdout="", stderr="remote exists")
        mock_run.side_effect = [head_result, create_result, push_result]
        mock_get_remote_tag_target.return_value = "head123"

        result = ensure_release_tag("1.2.3")

        assert result is True

    @patch("execute_release.log_error")
    @patch("execute_release.get_remote_tag_target")
    @patch("execute_release.run_command")
    def test_ensure_release_tag_remote_race_wrong_head(
        self, mock_run, mock_get_remote_tag_target, mock_log_error
    ) -> None:
        """Test push failure aborts when remote tag points to another commit."""
        head_result = MagicMock(returncode=0, stdout="head123\n", stderr="")
        create_result = MagicMock(returncode=0, stdout="", stderr="")
        push_result = MagicMock(returncode=1, stdout="", stderr="remote exists")
        mock_run.side_effect = [head_result, create_result, push_result]
        mock_get_remote_tag_target.return_value = "other456"

        result = ensure_release_tag("1.2.3")

        assert result is False
        logged_messages = [call.args[0] for call in mock_log_error.call_args_list]
        assert any(
            "Remote tag 1.2.3 points to other456, expected head123" in msg
            for msg in logged_messages
        )


class TestCheckTrackExists:
    """Tests for check_track_exists function."""

    @patch("execute_release.run_command")
    def test_track_exists(self, mock_run) -> None:
        """Test checking if a track exists."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Available tracks: ['rolling', 'jazzy']\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        result = check_track_exists(
            "test_package",
            "rolling",
            "rolling",
            "https://github.com/test/repo-release.git",
        )
        assert result is True

    @patch("execute_release.run_command")
    def test_track_does_not_exist(self, mock_run) -> None:
        """Test checking if a track doesn't exist."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Available tracks: ['jazzy']\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        result = check_track_exists(
            "test_package",
            "rolling",
            "rolling",
            "https://github.com/test/repo-release.git",
        )
        assert result is False

    @patch("execute_release.run_command")
    def test_track_does_not_exist_in_empty_release_repo(self, mock_run) -> None:
        """Test missing tracks are detected for an empty release repository."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = (
            "Release repository has no tracks nor an old style bloom.conf file."
        )
        mock_run.return_value = mock_result

        result = check_track_exists(
            "test_package",
            "rolling",
            "rolling",
            "https://github.com/test/repo-release.git",
        )
        assert result is False

    @patch("execute_release.run_command")
    def test_track_check_error(self, mock_run) -> None:
        """Test checking track returns None when command output is inconclusive."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "unexpected output"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        result = check_track_exists(
            "test_package",
            "rolling",
            "rolling",
            "https://github.com/test/repo-release.git",
        )
        assert result is None


class TestRunBloomRelease:
    """Tests for run_bloom_release function."""

    @patch("execute_release.run_command")
    def test_run_bloom_release_calls_bloom(self, mock_run) -> None:
        """Test that bloom-release runs release and PR phases."""
        release_result = MagicMock(returncode=0, stdout="release ok", stderr="")
        pr_result = MagicMock(
            returncode=0,
            stdout="https://github.com/ros/rosdistro/pull/123\n",
            stderr="",
        )
        mock_run.side_effect = [release_result, pr_result]

        result = run_bloom_release(
            repo_name="test_package",
            rosdistro="rolling",
            track="rolling",
            release_repo="https://github.com/test/repo.git",
            version="1.2.3",
            package_names=["test_package"],
        )

        assert result == "https://github.com/ros/rosdistro/pull/123"
        assert len(mock_run.call_args_list) == 2
        assert any("bloom-release" in str(call) for call in mock_run.call_args_list)

    @patch("execute_release.run_command")
    def test_run_bloom_release_passes_release_repo(self, mock_run) -> None:
        """Test that --override-release-repository-url is always passed."""
        release_result = MagicMock(returncode=0, stdout="release ok", stderr="")
        pr_result = MagicMock(
            returncode=0,
            stdout="https://github.com/ros/rosdistro/pull/123\n",
            stderr="",
        )
        mock_run.side_effect = [release_result, pr_result]

        run_bloom_release(
            repo_name="test_package",
            rosdistro="rolling",
            track="rolling",
            release_repo="https://github.com/ros2-gbp/test_package-release.git",
            version="1.2.3",
            package_names=["test_package"],
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
    def test_run_bloom_release_is_non_interactive(self, mock_run) -> None:
        """Test that bloom-release is always run in non-interactive mode."""
        release_result = MagicMock(returncode=0, stdout="release ok", stderr="")
        pr_result = MagicMock(
            returncode=0,
            stdout="https://github.com/ros/rosdistro/pull/123\n",
            stderr="",
        )
        mock_run.side_effect = [release_result, pr_result]

        run_bloom_release(
            repo_name="test_package",
            rosdistro="rolling",
            track="rolling",
            release_repo="https://github.com/ros2-gbp/test_package-release.git",
            version="1.2.3",
            package_names=["test_package"],
        )

        bloom_calls = [
            call for call in mock_run.call_args_list if "bloom-release" in str(call)
        ]
        assert len(bloom_calls) > 0
        for call_obj in bloom_calls:
            args = call_obj[0][0] if call_obj[0] else []
            assert "--non-interactive" in args

    @patch("execute_release.run_command")
    def test_run_bloom_release_no_new_track_by_default(self, mock_run) -> None:
        """Test that --new-track is not passed when new_track=False (default)."""
        release_result = MagicMock(returncode=0, stdout="release ok", stderr="")
        pr_result = MagicMock(
            returncode=0,
            stdout="https://github.com/ros/rosdistro/pull/123\n",
            stderr="",
        )
        mock_run.side_effect = [release_result, pr_result]

        run_bloom_release(
            repo_name="test_package",
            rosdistro="rolling",
            track="rolling",
            release_repo="https://github.com/ros2-gbp/test_package-release.git",
            version="1.2.3",
            package_names=["test_package"],
        )

        bloom_calls = [
            call for call in mock_run.call_args_list if "bloom-release" in str(call)
        ]
        assert len(bloom_calls) > 0
        for call_obj in bloom_calls:
            args = call_obj[0][0] if call_obj[0] else []
            assert "--new-track" not in args

    @patch("execute_release.run_command")
    def test_run_bloom_release_new_track(self, mock_run) -> None:
        """Test that --new-track is passed when new_track=True."""
        release_result = MagicMock(returncode=0, stdout="release ok", stderr="")
        pr_result = MagicMock(
            returncode=0,
            stdout="https://github.com/ros/rosdistro/pull/123\n",
            stderr="",
        )
        mock_run.side_effect = [release_result, pr_result]

        run_bloom_release(
            repo_name="test_package",
            rosdistro="rolling",
            track="rolling",
            release_repo="https://github.com/ros2-gbp/test_package-release.git",
            version="1.2.3",
            package_names=["test_package"],
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

    @patch("execute_release.run_command")
    def test_run_bloom_release_splits_release_and_pr_flags(self, mock_run) -> None:
        """Test the release and PR phases use the expected bloom flags."""
        release_result = MagicMock(returncode=0, stdout="release ok", stderr="")
        pr_result = MagicMock(
            returncode=0,
            stdout="https://github.com/ros/rosdistro/pull/123\n",
            stderr="",
        )
        mock_run.side_effect = [release_result, pr_result]

        run_bloom_release(
            repo_name="test_package",
            rosdistro="rolling",
            track="rolling",
            release_repo="https://github.com/ros2-gbp/test_package-release.git",
            version="1.2.3",
            package_names=["test_package"],
        )

        release_args = mock_run.call_args_list[0][0][0]
        pr_args = mock_run.call_args_list[1][0][0]

        assert "--no-pull-request" in release_args
        assert "--pull-request-only" not in release_args
        assert "--pull-request-only" in pr_args
        assert "--no-pull-request" not in pr_args

    @patch("execute_release.log_error")
    @patch("execute_release.run_command")
    def test_run_bloom_release_logs_stdout_stderr_on_failure(
        self, mock_run, mock_log_error
    ) -> None:
        """Test that bloom stdout and stderr are logged when bloom-release fails."""
        mock_run.side_effect = subprocess.CalledProcessError(
            1,
            ["bloom-release", "--rosdistro", "rolling", "test_package"],
            output="bloom stdout details",
            stderr="bloom stderr details",
        )

        result = run_bloom_release(
            repo_name="test_package",
            rosdistro="rolling",
            track="rolling",
            release_repo="https://github.com/ros2-gbp/test_package-release.git",
            version="1.2.3",
            package_names=["test_package"],
        )

        assert result is None
        logged_messages = [call.args[0] for call in mock_log_error.call_args_list]
        assert any("bloom-release failed:" in msg for msg in logged_messages)
        assert any("bloom stdout details" in msg for msg in logged_messages)
        assert any("bloom stderr details" in msg for msg in logged_messages)

    @patch("execute_release.release_repo_has_expected_release_tags")
    @patch("execute_release.run_command")
    def test_run_bloom_release_continues_after_release_repo_push_conflict(
        self, mock_run, mock_verify_release_repo
    ) -> None:
        """Test bloom-release continues to PR creation after a push race."""
        conflict_error = subprocess.CalledProcessError(
            1,
            ["bloom-release", "--rosdistro", "rolling", "test_package"],
            output=(
                "error: failed to push some refs to 'https://github.com/test/repo.git'\n"
                "! [rejected] master -> master (fetch first)\n"
                "Pushing changes failed, would you like to add '--force' to 'git push --all'?\n"
            ),
            stderr="",
        )
        success_result = MagicMock()
        success_result.returncode = 0
        success_result.stdout = "https://github.com/ros/rosdistro/pull/123\n"
        success_result.stderr = ""
        mock_run.side_effect = [conflict_error, success_result]
        mock_verify_release_repo.return_value = True

        result = run_bloom_release(
            repo_name="test_package",
            rosdistro="rolling",
            track="rolling",
            release_repo="https://github.com/ros2-gbp/test_package-release.git",
            version="1.2.3",
            package_names=["test_package"],
        )

        assert result == "https://github.com/ros/rosdistro/pull/123"
        assert mock_run.call_count == 2

    @patch("execute_release.release_repo_has_expected_release_tags")
    @patch("execute_release.run_command")
    def test_run_bloom_release_stops_after_unverified_release_repo_push_conflict(
        self, mock_run, mock_verify_release_repo
    ) -> None:
        """Test push-race conflicts fail when remote release tags are missing."""
        conflict_error = subprocess.CalledProcessError(
            1,
            ["bloom-release", "--rosdistro", "rolling", "test_package"],
            output=(
                "error: failed to push some refs to 'https://github.com/test/repo.git'\n"
                "! [rejected] master -> master (fetch first)\n"
                "Pushing changes failed, would you like to add '--force' to 'git push --all'?\n"
            ),
            stderr="",
        )
        mock_run.side_effect = [conflict_error]
        mock_verify_release_repo.return_value = False

        result = run_bloom_release(
            repo_name="test_package",
            rosdistro="rolling",
            track="rolling",
            release_repo="https://github.com/ros2-gbp/test_package-release.git",
            version="1.2.3",
            package_names=["test_package"],
        )

        assert result is None
        assert mock_run.call_count == 1

    @patch("execute_release.run_command")
    def test_run_bloom_release_does_not_retry_unrelated_failure(self, mock_run) -> None:
        """Test bloom-release stops on unrelated release failures."""
        mock_run.side_effect = subprocess.CalledProcessError(
            1,
            ["bloom-release", "--rosdistro", "rolling", "test_package"],
            output="bloom stdout details",
            stderr="bloom stderr details",
        )

        result = run_bloom_release(
            repo_name="test_package",
            rosdistro="rolling",
            track="rolling",
            release_repo="https://github.com/ros2-gbp/test_package-release.git",
            version="1.2.3",
            package_names=["test_package"],
        )

        assert result is None
        assert mock_run.call_count == 1

    @patch("execute_release.log_error")
    @patch("execute_release.run_command")
    def test_run_bloom_release_logs_pr_phase_without_url(
        self, mock_run, mock_log_error
    ) -> None:
        """Test missing PR URLs in the PR-only phase are logged as errors."""
        release_result = MagicMock(returncode=0, stdout="release ok", stderr="")
        pr_result = MagicMock(returncode=0, stdout="no pr here", stderr="")
        mock_run.side_effect = [release_result, pr_result]

        result = run_bloom_release(
            repo_name="test_package",
            rosdistro="rolling",
            track="rolling",
            release_repo="https://github.com/ros2-gbp/test_package-release.git",
            version="1.2.3",
            package_names=["test_package"],
        )

        assert result is None
        logged_messages = [call.args[0] for call in mock_log_error.call_args_list]
        assert any(
            "completed without producing a rosdistro PR URL" in msg
            for msg in logged_messages
        )


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
    ) -> None:
        """Test version parsing and branch detection in a temp git repo."""
        os.chdir(temp_git_repo)

        # Setup package.xml
        (temp_git_repo / "package.xml").write_text(package_xml_content)

        mock_check.return_value = True
        mock_run.side_effect = lambda cmd, **kwargs: subprocess.run(
            cmd, capture_output=True, text=True, check=False
        )

        version = get_package_version([])
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        assert version == "1.2.3"
        assert branch in ["main", "master"]  # Git default branch


class TestMainTargetsMode:
    """Tests for the batch targets mode in main()."""

    @patch("execute_release.set_output")
    @patch("execute_release.run_single_target_release")
    @patch("execute_release.ensure_release_tag")
    @patch("execute_release.get_package_names")
    @patch("execute_release.get_package_version")
    @patch("execute_release.is_release_commit")
    @patch("execute_release.get_exclude_paths_from_env")
    @patch("execute_release.parse_args")
    def test_main_runs_targets_sequentially(
        self,
        mock_parse_args,
        mock_get_exclude_paths,
        mock_is_release_commit,
        mock_get_package_version,
        mock_get_package_names,
        mock_ensure_release_tag,
        mock_run_single_target_release,
        mock_set_output,
    ) -> None:
        """Test batch mode runs matching targets in declared order."""
        mock_parse_args.return_value = MagicMock(
            repository="test_package",
            release_repository="https://github.com/ros2-gbp/test_package-release.git",
            rosdistro=None,
            track=None,
            targets=(
                "main:\n"
                "  - rosdistro: rolling\n"
                "    track: rolling\n"
                "  - rosdistro: jazzy\n"
                "    track: jazzy\n"
            ),
            current_branch="main",
            dry_run=False,
        )
        mock_get_exclude_paths.return_value = []
        mock_is_release_commit.return_value = True
        mock_get_package_version.return_value = "1.2.3"
        mock_get_package_names.return_value = ["test_package"]
        mock_ensure_release_tag.return_value = True
        mock_run_single_target_release.side_effect = [
            "https://github.com/ros/rosdistro/pull/123",
            "https://github.com/ros/rosdistro/pull/124",
        ]

        from execute_release import main

        main()

        assert mock_run_single_target_release.call_count == 2
        first_call = mock_run_single_target_release.call_args_list[0].kwargs
        second_call = mock_run_single_target_release.call_args_list[1].kwargs
        assert first_call["rosdistro"] == "rolling"
        assert first_call["track"] == "rolling"
        assert second_call["rosdistro"] == "jazzy"
        assert second_call["track"] == "jazzy"
        mock_set_output.assert_any_call("released", "true")
        mock_set_output.assert_any_call("version", "1.2.3")

    @patch("execute_release.set_output")
    @patch("execute_release.get_package_names")
    @patch("execute_release.get_package_version")
    @patch("execute_release.is_release_commit")
    @patch("execute_release.get_exclude_paths_from_env")
    @patch("execute_release.parse_args")
    def test_main_no_matching_targets_is_no_op(
        self,
        mock_parse_args,
        mock_get_exclude_paths,
        mock_is_release_commit,
        mock_get_package_version,
        mock_get_package_names,
        mock_set_output,
    ) -> None:
        """Test batch mode no-ops cleanly when no targets match the branch."""
        mock_parse_args.return_value = MagicMock(
            repository="test_package",
            release_repository="https://github.com/ros2-gbp/test_package-release.git",
            rosdistro=None,
            track=None,
            targets="main:\n  - rosdistro: rolling\n    track: rolling\n",
            current_branch="jazzy",
            dry_run=False,
        )
        mock_get_exclude_paths.return_value = []
        mock_is_release_commit.return_value = True
        mock_get_package_version.return_value = "1.2.3"
        mock_get_package_names.return_value = ["test_package"]

        from execute_release import main

        main()

        mock_set_output.assert_any_call("released", "false")
        mock_set_output.assert_any_call("version", "1.2.3")

    @patch("execute_release.set_output")
    @patch("execute_release.run_single_target_release")
    @patch("execute_release.ensure_release_tag")
    @patch("execute_release.get_package_names")
    @patch("execute_release.get_package_version")
    @patch("execute_release.is_release_commit")
    @patch("execute_release.get_exclude_paths_from_env")
    @patch("execute_release.parse_args")
    def test_main_batch_mode_fails_fast(
        self,
        mock_parse_args,
        mock_get_exclude_paths,
        mock_is_release_commit,
        mock_get_package_version,
        mock_get_package_names,
        mock_ensure_release_tag,
        mock_run_single_target_release,
        mock_set_output,
    ) -> None:
        """Test batch mode stops on the first failed target."""
        mock_parse_args.return_value = MagicMock(
            repository="test_package",
            release_repository="https://github.com/ros2-gbp/test_package-release.git",
            rosdistro=None,
            track=None,
            targets=(
                "main:\n"
                "  - rosdistro: rolling\n"
                "    track: rolling\n"
                "  - rosdistro: jazzy\n"
                "    track: jazzy\n"
            ),
            current_branch="main",
            dry_run=False,
        )
        mock_get_exclude_paths.return_value = []
        mock_is_release_commit.return_value = True
        mock_get_package_version.return_value = "1.2.3"
        mock_get_package_names.return_value = ["test_package"]
        mock_ensure_release_tag.return_value = True
        mock_run_single_target_release.side_effect = [None]

        from execute_release import main

        with pytest.raises(SystemExit):
            main()

        assert mock_run_single_target_release.call_count == 1
        mock_set_output.assert_any_call("released", "false")


class TestReleaseNoOp:
    """Tests for the release self-no-op guard.

    The guard is commit-driven: non-release commits are no-ops regardless of
    tag state. Existing source tags do not cause later non-release pushes to
    retrigger bloom, and an existing source tag does not prevent a release
    commit from proceeding.
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
    ) -> None:
        """A regular push (non-release commit) → is_release_commit() is False."""
        os.chdir(temp_git_repo)
        (temp_git_repo / "package.xml").write_text(package_xml_content)
        self._commit(temp_git_repo, "feat: add feature", "feature.txt")

        assert is_release_commit() is False

    def test_release_commit_with_no_tag_proceeds(
        self, temp_git_repo: Path, package_xml_content: str
    ) -> None:
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
    ) -> None:
        """Release commit + tag already exists → is_release_commit() True.

        This verifies the source-tag guard is not tag-driven: an existing tag
        does not suppress a real release commit.
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
    ) -> None:
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
