#!/usr/bin/env python3
"""
Execute Release Script for GitHub Actions

This script automates the ROS package release process using bloom-release.
It performs the following steps:
1. Read version from package.xml and create a git tag
2. Run bloom-release for the specified ROS distribution
"""

import argparse
import ast
import re
import subprocess
import sys
from typing import Optional

from common import (
    get_exclude_paths_from_env,
    get_package_version,
    is_release_commit,
    log_error,
    log_info,
    log_success,
    log_warning,
    run_command,
    set_output,
)


def get_current_branch() -> str:
    """Get the current git branch name."""
    result = run_command(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
    )
    return result.stdout.strip()


def get_local_tag_target(tag: str) -> Optional[str]:
    """Return the commit SHA referenced by a local tag.

    Args:
        tag: Tag name to resolve.

    Returns:
        The commit SHA referenced by the tag, or None if the tag could not be
        resolved.
    """
    try:
        result = run_command(
            ["git", "rev-list", "-n", "1", f"refs/tags/{tag}"],
            capture_output=True,
            check=False,
        )
    except OSError:
        return None

    target = result.stdout.strip()
    if result.returncode == 0 and target:
        return target
    return None


def parse_remote_tag_target(output: str, tag: str) -> Optional[str]:
    """Return the commit SHA referenced by a remote tag listing.

    Args:
        output: Output from ``git ls-remote --tags``.
        tag: Tag name to resolve.

    Returns:
        The peeled commit SHA for the tag, or None if the tag is absent.
    """
    ref = f"refs/tags/{tag}"
    peeled_ref = f"{ref}^{{}}"
    tag_target: Optional[str] = None

    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue

        sha, remote_ref = parts
        if remote_ref == peeled_ref:
            return sha
        if remote_ref == ref:
            tag_target = sha

    return tag_target


def get_remote_tag_target(tag: str) -> Optional[str]:
    """Return the commit SHA referenced by a remote tag.

    Args:
        tag: Tag name to resolve on ``origin``.

    Returns:
        The peeled commit SHA for the remote tag, or None if it could not be
        resolved.
    """
    try:
        result = run_command(
            [
                "git",
                "ls-remote",
                "--tags",
                "origin",
                f"refs/tags/{tag}",
                f"refs/tags/{tag}^{{}}",
            ],
            capture_output=True,
            check=False,
        )
    except OSError:
        return None

    if result.returncode != 0:
        return None
    return parse_remote_tag_target(result.stdout, tag)


def ensure_release_tag(tag: str) -> bool:
    """Create and push the release tag, tolerating same-HEAD races.

    Args:
        tag: Release tag name to create and push.

    Returns:
        True if the tag exists remotely and points at ``HEAD``, False otherwise.
    """
    head_result = run_command(
        ["git", "rev-parse", "HEAD"], capture_output=True, check=False
    )
    head_commit = head_result.stdout.strip()
    if head_result.returncode != 0 or not head_commit:
        log_error("Failed to determine HEAD commit for tag verification")
        if head_result.stderr:
            log_error(f"git rev-parse stderr:\n{head_result.stderr}")
        return False

    log_info(f"Creating git tag {tag}")
    create_result = run_command(
        ["git", "tag", "-a", tag, "-m", f"Release {tag}"],
        capture_output=True,
        check=False,
    )
    if create_result.returncode != 0:
        local_target = get_local_tag_target(tag)
        if local_target == head_commit:
            log_info(f"Local tag {tag} already points to {head_commit}, continuing")
        else:
            log_error(f"Failed to create tag {tag}")
            if create_result.stdout:
                log_error(f"git tag stdout:\n{create_result.stdout}")
            if create_result.stderr:
                log_error(f"git tag stderr:\n{create_result.stderr}")
            if local_target is None:
                log_error(f"Could not resolve local tag {tag} after creation failure")
            else:
                log_error(
                    f"Local tag {tag} points to {local_target}, expected {head_commit}"
                )
            return False

    push_result = run_command(
        ["git", "push", "origin", tag],
        capture_output=True,
        check=False,
    )
    if push_result.returncode != 0:
        remote_target = get_remote_tag_target(tag)
        if remote_target == head_commit:
            log_info(f"Remote tag {tag} already points to {head_commit}, continuing")
            return True

        log_error(f"Failed to push tag {tag}")
        if push_result.stdout:
            log_error(f"git push stdout:\n{push_result.stdout}")
        if push_result.stderr:
            log_error(f"git push stderr:\n{push_result.stderr}")
        if remote_target is None:
            log_error(f"Could not resolve remote tag {tag} after push failure")
        else:
            log_error(
                f"Remote tag {tag} points to {remote_target}, expected {head_commit}"
            )
        return False

    return True


def run_bloom_release(
    repo_name: str,
    rosdistro: str,
    track: str,
    release_repo: str,
    dry_run: bool = False,
    new_track: bool = False,
) -> Optional[str]:
    """
    Run bloom-release for the specified distribution.

    Returns:
        The PR URL if successful, None otherwise
    """
    log_info(f"Running bloom-release for {rosdistro} (track: {track})...")

    cmd = [
        "bloom-release",
        "--rosdistro",
        rosdistro,
        "--track",
        track,
        "--non-interactive",
    ]

    if new_track:
        cmd.append("--new-track")

    cmd.extend(["--override-release-repository-url", release_repo])

    if dry_run:
        cmd.append("--dry-run")

    cmd.append(repo_name)

    # Run bloom-release (non-interactive mode)
    # Set BLOOM_SKIP_ROSDEP_UPDATE to speed up subsequent releases
    env = {"BLOOM_SKIP_ROSDEP_UPDATE": "1"}

    try:
        result = run_command(cmd, capture_output=True, env=env)
        output = result.stdout + result.stderr

        # Extract PR URL from output
        pr_match = re.search(r"(https://github\.com/ros/rosdistro/pull/\d+)", output)
        if pr_match:
            return pr_match.group(1)

        log_error("bloom-release completed without producing a rosdistro PR URL")
        if result.stdout:
            log_error(f"bloom-release stdout:\n{result.stdout}")
        if result.stderr:
            log_error(f"bloom-release stderr:\n{result.stderr}")

    except subprocess.CalledProcessError as e:
        log_error(f"bloom-release failed: {e}")
        if e.stdout:
            log_error(f"bloom-release stdout:\n{e.stdout}")
        if e.stderr:
            log_error(f"bloom-release stderr:\n{e.stderr}")
        return None

    return None


def parse_track_list(output: str) -> Optional[set[str]]:
    """Parse track names from bloom-release --list-tracks output.

    Args:
        output: Combined stdout and stderr from bloom-release.

    Returns:
        The set of track names bloom reported, or None if parsing failed.
    """
    match = re.search(
        r"Available tracks:\s*(?:[A-Za-z_]+\()?([^\n]*\[[^\n]*\])\)?", output
    )
    if match is None:
        return None

    try:
        parsed_tracks = ast.literal_eval(match.group(1))
    except (SyntaxError, ValueError):
        return None

    if not isinstance(parsed_tracks, list):
        return None

    if any(not isinstance(track_name, str) for track_name in parsed_tracks):
        return None

    return set(parsed_tracks)


def check_track_exists(
    repo_name: str,
    rosdistro: str,
    track: str,
    release_repo: str,
) -> Optional[bool]:
    """Check if a bloom track exists in the explicit release repository.

    Args:
        repo_name: Repository name as registered in rosdistro.
        rosdistro: ROS distribution used for bloom context.
        track: Bloom track name to check.
        release_repo: Release repository URL bloom should inspect.

    Returns:
        True if the track exists, False if it definitely does not exist, or
        None if the probe was inconclusive.
    """
    try:
        result = run_command(
            [
                "bloom-release",
                "--rosdistro",
                rosdistro,
                "--override-release-repository-url",
                release_repo,
                "--list-tracks",
                repo_name,
            ],
            capture_output=True,
            check=False,
        )
    except OSError:
        return None

    output = result.stdout + result.stderr
    tracks = parse_track_list(output)
    if tracks is not None:
        return track in tracks

    if "Release repository has no tracks nor an old style bloom.conf file." in output:
        return False

    return None


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Execute bloom-release for ROS package distribution",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--repository",
        required=True,
        help="Repository name as registered in rosdistro (e.g., my_ros_package)",
    )
    parser.add_argument(
        "--rosdistro",
        required=True,
        help="ROS distribution to release (e.g., rolling, jazzy, humble)",
    )
    parser.add_argument(
        "--track",
        required=True,
        help="Bloom track to use (e.g., rolling, jazzy)",
    )
    parser.add_argument(
        "--release-repository",
        required=True,
        help="Release repository URL (e.g., https://github.com/ros2-gbp/my_package-release.git)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run in dry-run mode without actually releasing",
    )
    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()

    exclude_paths = get_exclude_paths_from_env()

    log_info(f"Repository: {args.repository}")
    log_info(f"ROS Distribution: {args.rosdistro}")
    log_info(f"Track: {args.track}")
    log_info(f"Release repository: {args.release_repository}")

    # Note: Changelog generation is done in prepare_release.py
    # This script only creates git tags and runs bloom-release

    # Self-no-op: skip on non-release pushes.
    # The release job is safe to run on every push without workflow-level
    # if: guards because it exits cleanly here on anything that is not a
    # release commit.  Crucially, this guard is commit-driven rather than
    # tag-driven, so multiple release steps for different tracks on the same
    # release commit all proceed correctly, the tag existing from a previous
    # step does not cause the next step to skip.
    if not is_release_commit():
        log_info("HEAD is not a release commit, nothing to release")
        set_output("released", "false")
        set_output("version", get_package_version(exclude_paths))
        set_output("rosdistro", args.rosdistro)
        return

    # Get the version from package.xml (already set by prepare_release.py)
    version = get_package_version(exclude_paths)
    log_info(f"Releasing version: {version}")

    # Optimistic tag creation: in a parallel matrix another job may create the
    # same annotated tag first. Treat that as success only when the tag resolves
    # to this job's HEAD commit.
    if not ensure_release_tag(version):
        set_output("released", "false")
        set_output("version", version)
        set_output("rosdistro", args.rosdistro)
        sys.exit(1)

    # Check if this is a new track
    track_exists = check_track_exists(
        repo_name=args.repository,
        rosdistro=args.rosdistro,
        track=args.track,
        release_repo=args.release_repository,
    )
    new_track = track_exists is False
    if new_track:
        log_info(f"Track '{args.track}' does not exist, will create new track")
    elif track_exists is None:
        log_warning(
            f"Could not determine whether track '{args.track}' exists; "
            "running bloom-release without --new-track"
        )

    # Run bloom-release
    if args.dry_run:
        log_warning("Dry-run mode enabled, skipping actual release")
        set_output("released", "false")
        set_output("version", version)
        set_output("rosdistro", args.rosdistro)
        return

    pr_url = run_bloom_release(
        repo_name=args.repository,
        rosdistro=args.rosdistro,
        track=args.track,
        release_repo=args.release_repository,
        dry_run=args.dry_run,
        new_track=new_track,
    )

    if pr_url:
        log_success(f"Release PR created: {pr_url}")
        set_output("released", "true")
        set_output("version", version)
        set_output("rosdistro", args.rosdistro)
        set_output("pr-url", pr_url)
    else:
        log_error("Release failed")
        set_output("released", "false")
        sys.exit(1)


if __name__ == "__main__":
    main()
