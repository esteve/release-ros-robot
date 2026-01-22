#!/usr/bin/env python3
"""
Execute Release Script for GitHub Actions

This script automates the ROS package release process using bloom-release.
It performs the following steps:
1. Read version from package.xml and create a git tag
2. Run bloom-release for the specified ROS distribution
"""

import argparse
import re
import subprocess
import sys

from common import (
    get_exclude_paths_from_env,
    get_last_tag,
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


def run_bloom_release(
    repo_name: str,
    rosdistro: str,
    track: str,
    release_repo: str,
    dry_run: bool = False,
    new_track: bool = False,
) -> str | None:
    """
    Run bloom-release for the specified distribution.

    Returns:
        The PR URL if successful, None otherwise
    """
    log_info(f"Running bloom-release for {rosdistro} (track: {track})...")

    cmd = ["bloom-release", "--rosdistro", rosdistro, "--track", track]

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

    except subprocess.CalledProcessError as e:
        log_error(f"bloom-release failed: {e}")
        return None

    return None


def check_track_exists(repo_name: str, track: str) -> bool:
    """Check if a bloom track already exists for this repository."""
    try:
        result = run_command(
            ["bloom-release", "--list-tracks", repo_name],
            capture_output=True,
            check=False,
        )
        return track in result.stdout
    except OSError:
        return False


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

    # Idempotent tag creation: the tag may already exist when multiple release
    # steps run for different tracks on the same release commit.  Only create
    # and push the tag if it does not yet exist.
    existing_tag = get_last_tag()
    if existing_tag == version:
        log_info(f"Tag {version} already exists, skipping tag creation")
    else:
        log_info(f"Creating git tag {version}")
        run_command(["git", "tag", "-a", version, "-m", f"Release {version}"])
        run_command(["git", "push", "origin", version])

    # Check if this is a new track
    new_track = not check_track_exists(args.repository, args.track)
    if new_track:
        log_info(f"Track '{args.track}' does not exist, will create new track")

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
