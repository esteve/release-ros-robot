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
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Optional

import yaml

from common import (
    discover_package_xmls,
    get_config_string,
    get_config_value,
    get_package_version,
    is_release_commit,
    load_config_file,
    log_error,
    log_info,
    log_success,
    log_warning,
    resolve_exclude_paths,
    run_command,
    set_output,
)

Target = dict[str, str]


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


def get_package_names(exclude_patterns: list[str]) -> list[str]:
    """Return sorted package names from non-excluded package.xml files.

    Args:
        exclude_patterns: Glob patterns to exclude from package.xml discovery.

    Returns:
        Sorted unique package names declared in package.xml files.
    """
    package_xmls = discover_package_xmls(exclude_patterns)
    if not package_xmls:
        log_error("No package.xml found")
        sys.exit(1)

    package_names: set[str] = set()
    for pkg_xml in package_xmls:
        root = ET.parse(pkg_xml).getroot()
        package_name = root.findtext("name")
        if package_name is None or not package_name.strip():
            log_error(f"Could not determine package name from {pkg_xml}")
            sys.exit(1)

        package_names.add(package_name.strip())

    return sorted(package_names)


def is_release_repo_push_conflict(output: str) -> bool:
    """Return True if bloom failed on a release-repository push race.

    Args:
        output: Combined stdout and stderr from bloom-release.

    Returns:
        True when bloom hit its non-fast-forward retry/force prompt path for
        the release repository, False otherwise.
    """
    return (
        "failed to push some refs to" in output
        and "(fetch first)" in output
        and "would you like to add '--force'" in output
    )


def extract_rosdistro_pr_url(output: str) -> Optional[str]:
    """Extract the rosdistro pull request URL from bloom output.

    Args:
        output: Combined stdout and stderr from bloom-release.

    Returns:
        The rosdistro PR URL if present, otherwise None.
    """
    pr_match = re.search(r"(https://github\.com/ros/rosdistro/pull/\d+)", output)
    if pr_match:
        return pr_match.group(1)
    return None


def release_repo_has_expected_release_tags(
    release_repo: str,
    track: str,
    version: str,
    package_names: list[str],
) -> bool:
    """Return True if the remote release repo already has the track release tags.

    Args:
        release_repo: Release repository URL.
        track: Bloom track being released.
        version: Package version being released.
        package_names: Package names expected to have release tags.

    Returns:
        True if every package has a matching ``release/<track>/<package>/<version>-*``
        tag in the remote release repository, False otherwise.
    """
    try:
        result = run_command(
            ["git", "ls-remote", "--tags", release_repo],
            capture_output=True,
            check=False,
        )
    except OSError:
        return False

    if result.returncode != 0:
        return False

    refs = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue

        ref = parts[1]
        if ref.endswith("^{}"):
            ref = ref[:-3]
        refs.add(ref)

    missing_prefixes: list[str] = []
    for package_name in package_names:
        prefix = f"refs/tags/release/{track}/{package_name}/{version}-"
        if not any(ref.startswith(prefix) for ref in refs):
            missing_prefixes.append(prefix)

    if missing_prefixes:
        for prefix in missing_prefixes:
            log_warning(f"Missing remote release tag matching: {prefix}*")
        return False

    return True


def log_bloom_failure(error: subprocess.CalledProcessError) -> None:
    """Log a failed bloom-release subprocess with captured output.

    Args:
        error: The subprocess error raised by bloom-release.
    """
    log_error(f"bloom-release failed: {error}")
    if error.stdout:
        log_error(f"bloom-release stdout:\n{error.stdout}")
    if error.stderr:
        log_error(f"bloom-release stderr:\n{error.stderr}")


def can_continue_after_release_repo_push_conflict(
    output: str,
    release_repo: str,
    track: str,
    version: Optional[str],
    package_names: Optional[list[str]],
) -> bool:
    """Return True if a release-repo push conflict is safe to treat as success.

    Args:
        output: Combined stdout and stderr from bloom-release.
        release_repo: Release repository URL.
        track: Bloom track being released.
        version: Package version being released.
        package_names: Package names expected to have release tags.

    Returns:
        True if the conflict was recognized and the expected remote release tags
        already exist, False otherwise.
    """
    if not is_release_repo_push_conflict(output):
        return False

    if version is None or not package_names:
        log_error(
            "bloom-release release-repository push raced with another job, "
            "but the expected release tags could not be verified"
        )
        return False

    if not release_repo_has_expected_release_tags(
        release_repo=release_repo,
        track=track,
        version=version,
        package_names=package_names,
    ):
        log_error(
            "bloom-release release-repository push raced with another job, "
            "but the expected remote release tags were not found"
        )
        return False

    log_warning(
        "bloom-release release-repository push raced with another job, and the "
        "expected remote release tags already exist; continuing to pull-request "
        "creation"
    )
    return True


def run_bloom_release_phase(
    repo_name: str,
    rosdistro: str,
    track: str,
    release_repo: str,
    version: Optional[str],
    package_names: Optional[list[str]],
    dry_run: bool,
    new_track: bool,
) -> bool:
    """Run the release-repository mutation phase of bloom-release.

    Args:
        repo_name: Repository name as registered in rosdistro.
        rosdistro: ROS distribution to release.
        track: Bloom track to use.
        release_repo: Release repository URL.
        version: Package version being released.
        package_names: Package names expected to have release tags.
        dry_run: Run bloom in dry-run mode.
        new_track: Create the bloom track before releasing.

    Returns:
        True if the release phase succeeded or a verified push-race is safe to
        continue past, False otherwise.
    """
    try:
        run_bloom_command(
            repo_name=repo_name,
            rosdistro=rosdistro,
            track=track,
            release_repo=release_repo,
            no_pull_request=True,
            dry_run=dry_run,
            new_track=new_track,
        )
        return True
    except subprocess.CalledProcessError as error:
        output = (error.stdout or "") + (error.stderr or "")
        if can_continue_after_release_repo_push_conflict(
            output=output,
            release_repo=release_repo,
            track=track,
            version=version,
            package_names=package_names,
        ):
            return True

        log_bloom_failure(error)
        return False


def run_bloom_pr_phase(
    repo_name: str,
    rosdistro: str,
    track: str,
    release_repo: str,
    dry_run: bool,
) -> Optional[str]:
    """Run the rosdistro pull-request creation phase of bloom-release.

    Args:
        repo_name: Repository name as registered in rosdistro.
        rosdistro: ROS distribution to release.
        track: Bloom track to use.
        release_repo: Release repository URL.
        dry_run: Run bloom in dry-run mode.

    Returns:
        The rosdistro PR URL if successful, otherwise None.
    """
    try:
        pr_result = run_bloom_command(
            repo_name=repo_name,
            rosdistro=rosdistro,
            track=track,
            release_repo=release_repo,
            pull_request_only=True,
            dry_run=dry_run,
        )
    except subprocess.CalledProcessError as error:
        log_bloom_failure(error)
        return None

    output = pr_result.stdout + pr_result.stderr
    pr_url = extract_rosdistro_pr_url(output)
    if pr_url:
        return pr_url

    log_error("bloom-release completed without producing a rosdistro PR URL")
    if pr_result.stdout:
        log_error(f"bloom-release stdout:\n{pr_result.stdout}")
    if pr_result.stderr:
        log_error(f"bloom-release stderr:\n{pr_result.stderr}")
    return None


def run_bloom_command(
    repo_name: str,
    rosdistro: str,
    track: str,
    release_repo: str,
    no_pull_request: bool = False,
    pull_request_only: bool = False,
    dry_run: bool = False,
    new_track: bool = False,
) -> subprocess.CompletedProcess:
    """Run bloom-release with the requested execution mode.

    Args:
        repo_name: Repository name as registered in rosdistro.
        rosdistro: ROS distribution to release.
        track: Bloom track to use.
        release_repo: Release repository URL.
        no_pull_request: Skip opening a rosdistro PR after release actions.
        pull_request_only: Skip release actions and only open a rosdistro PR.
        dry_run: Run bloom in dry-run mode.
        new_track: Create the bloom track before releasing.

    Returns:
        Completed process for the bloom invocation.
    """
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

    if no_pull_request:
        cmd.append("--no-pull-request")

    if pull_request_only:
        cmd.append("--pull-request-only")

    cmd.extend(["--override-release-repository-url", release_repo])

    if dry_run:
        cmd.append("--dry-run")

    cmd.append(repo_name)

    # Run bloom-release (non-interactive mode)
    # Set BLOOM_SKIP_ROSDEP_UPDATE to speed up subsequent releases
    env = {"BLOOM_SKIP_ROSDEP_UPDATE": "1"}
    return run_command(cmd, capture_output=True, env=env)


def run_bloom_release(
    repo_name: str,
    rosdistro: str,
    track: str,
    release_repo: str,
    version: Optional[str] = None,
    package_names: Optional[list[str]] = None,
    dry_run: bool = False,
    new_track: bool = False,
) -> Optional[str]:
    """Run bloom release actions and then open the rosdistro pull request.

    Args:
        repo_name: Repository name as registered in rosdistro.
        rosdistro: ROS distribution to release.
        track: Bloom track to use.
        release_repo: Release repository URL.
        version: Package version being released.
        package_names: Package names expected to have release tags.
        dry_run: Run bloom in dry-run mode.
        new_track: Create the bloom track before releasing.

    Returns:
        The rosdistro PR URL if successful, otherwise None.
    """
    log_info(f"Running bloom-release for {rosdistro} (track: {track})...")

    if not run_bloom_release_phase(
        repo_name=repo_name,
        rosdistro=rosdistro,
        track=track,
        release_repo=release_repo,
        version=version,
        package_names=package_names,
        dry_run=dry_run,
        new_track=new_track,
    ):
        return None

    return run_bloom_pr_phase(
        repo_name=repo_name,
        rosdistro=rosdistro,
        track=track,
        release_repo=release_repo,
        dry_run=dry_run,
    )


def parse_targets_yaml(targets_text: str) -> dict[str, list[Target]]:
    """Parse the YAML block string for sequential release targets.

    Args:
        targets_text: YAML block string mapping branches to target lists.

    Returns:
        Mapping of branch name to ordered list of release targets.
    """
    try:
        parsed = yaml.safe_load(targets_text)
    except yaml.YAMLError as error:
        log_error(f"Failed to parse targets YAML: {error}")
        sys.exit(1)

    return validate_targets_mapping(parsed)


def validate_targets_mapping(parsed: object) -> dict[str, list[Target]]:
    """Validate branch-mapped release targets from YAML or TOML data.

    Args:
        parsed: Parsed branch-to-target structure.

    Returns:
        Mapping of branch name to ordered list of release targets.
    """
    branch_targets: dict[str, list[Target]] = {}
    if not isinstance(parsed, dict):
        log_error("targets must parse to a mapping of branches to target lists")
        sys.exit(1)

    for branch, targets in parsed.items():
        if not isinstance(branch, str) or not branch.strip():
            log_error("targets keys must be non-empty branch names")
            sys.exit(1)

        if not isinstance(targets, list):
            log_error(f"targets for branch '{branch}' must be a list")
            sys.exit(1)

        parsed_targets: list[Target] = []
        for index, target in enumerate(targets, start=1):
            if not isinstance(target, dict):
                log_error(
                    f"targets entry {index} for branch '{branch}' must be a mapping"
                )
                sys.exit(1)

            allowed_keys = {"rosdistro", "track"}
            unknown_keys = set(target) - allowed_keys
            if unknown_keys:
                keys = ", ".join(sorted(unknown_keys))
                log_error(
                    f"targets entry {index} for branch '{branch}' has unknown keys: {keys}"
                )
                sys.exit(1)

            rosdistro = target.get("rosdistro")
            track = target.get("track")
            if not isinstance(rosdistro, str) or not rosdistro.strip():
                log_error(
                    f"targets entry {index} for branch '{branch}' is missing rosdistro"
                )
                sys.exit(1)
            if not isinstance(track, str) or not track.strip():
                log_error(
                    f"targets entry {index} for branch '{branch}' is missing track"
                )
                sys.exit(1)

            parsed_targets.append(
                {
                    "rosdistro": rosdistro.strip(),
                    "track": track.strip(),
                }
            )

        branch_targets[branch.strip()] = parsed_targets

    return branch_targets


def resolve_release_settings(
    config: dict[str, object],
    repository_override: Optional[str],
    release_repository_override: Optional[str],
    targets_override: Optional[str],
) -> tuple[str, str, dict[str, list[Target]]]:
    """Resolve release settings from action inputs over config file values.

    Args:
        config: Parsed TOML config mapping.
        repository_override: Direct repository input.
        release_repository_override: Direct release repository input.
        targets_override: Direct YAML targets input.

    Returns:
        Tuple of resolved repository, release repository, and validated targets.
    """
    repository = repository_override or get_config_string(
        config, "repository", field_name="repository"
    )
    release_repository = release_repository_override or get_config_string(
        config, "release_repository", field_name="release_repository"
    )

    config_targets = get_config_value(config, "release", "targets")
    if targets_override:
        targets = parse_targets_yaml(targets_override)
    else:
        if config_targets is None:
            log_error(
                "release mode requires targets via the action input or the config file"
            )
            sys.exit(1)
        targets = validate_targets_mapping(config_targets)

    if not repository:
        log_error(
            "release mode requires repository via the action input or the config file"
        )
        sys.exit(1)
    if not release_repository:
        log_error(
            "release mode requires release_repository via the action input or the config file"
        )
        sys.exit(1)

    return repository, release_repository, targets


def resolve_release_targets(
    current_branch: str,
    targets: dict[str, list[Target]],
) -> list[Target]:
    """Resolve release targets for the current branch.

    Args:
        current_branch: Current Git branch name.
        targets: Validated branch-to-target mapping.

    Returns:
        Ordered list of targets to execute.
    """
    return targets.get(current_branch, [])


def run_single_target_release(
    repo_name: str,
    rosdistro: str,
    track: str,
    release_repo: str,
    version: str,
    package_names: list[str],
    dry_run: bool,
) -> Optional[str]:
    """Run the release flow for a single rosdistro/track target.

    Args:
        repo_name: Repository name as registered in rosdistro.
        rosdistro: ROS distribution to release.
        track: Bloom track to use.
        release_repo: Release repository URL.
        version: Package version being released.
        package_names: Package names expected to have release tags.
        dry_run: Run bloom in dry-run mode.

    Returns:
        The rosdistro PR URL if successful, otherwise None.
    """
    track_exists = check_track_exists(
        repo_name=repo_name,
        rosdistro=rosdistro,
        track=track,
        release_repo=release_repo,
    )
    new_track = track_exists is False
    if new_track:
        log_info(f"Track '{track}' does not exist, will create new track")
    elif track_exists is None:
        log_warning(
            f"Could not determine whether track '{track}' exists; "
            "running bloom-release without --new-track"
        )

    return run_bloom_release(
        repo_name=repo_name,
        rosdistro=rosdistro,
        track=track,
        release_repo=release_repo,
        version=version,
        package_names=package_names,
        dry_run=dry_run,
        new_track=new_track,
    )


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
    except SyntaxError, ValueError:
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
        "--config-file",
        help="Path to the TOML configuration file",
    )
    parser.add_argument(
        "--repository",
        help="Repository name as registered in rosdistro (e.g., my_ros_package)",
    )
    parser.add_argument(
        "--release-repository",
        help="Release repository URL (e.g., https://github.com/ros2-gbp/my_package-release.git)",
    )
    parser.add_argument(
        "--targets",
        help="YAML block string mapping branches to sequential release targets",
    )
    parser.add_argument(
        "--current-branch",
        help="Current branch name used to select entries from --targets",
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

    config = load_config_file(args.config_file)
    exclude_paths = resolve_exclude_paths(config)
    current_branch = (
        args.current_branch
        or run_command(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True
        ).stdout.strip()
    )
    repository, release_repository, all_targets = resolve_release_settings(
        config=config,
        repository_override=args.repository,
        release_repository_override=args.release_repository,
        targets_override=args.targets,
    )
    targets = resolve_release_targets(
        current_branch=current_branch,
        targets=all_targets,
    )

    log_info(f"Repository: {repository}")
    log_info(f"Current branch: {current_branch}")
    log_info(f"Release repository: {release_repository}")
    if targets:
        log_info(
            "Matched release targets: "
            + ", ".join(
                f"{target['rosdistro']}/{target['track']}" for target in targets
            )
        )
    else:
        log_info(f"No release targets configured for branch '{current_branch}'")

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
        return

    if not targets:
        log_info("No release targets matched the current branch, nothing to release")
        set_output("released", "false")
        set_output("version", get_package_version(exclude_paths))
        return

    # Get the version from package.xml (already set by prepare_release.py)
    version = get_package_version(exclude_paths)
    package_names = get_package_names(exclude_paths)
    log_info(f"Releasing version: {version}")

    if args.dry_run:
        log_warning("Dry-run mode enabled, skipping actual release")
        set_output("released", "false")
        set_output("version", version)
        if len(targets) == 1:
            set_output("rosdistro", targets[0]["rosdistro"])
        return

    # Optimistic tag creation: in a parallel matrix another job may create the
    # same annotated tag first. Treat that as success only when the tag resolves
    # to this job's HEAD commit.
    if not ensure_release_tag(version):
        set_output("released", "false")
        set_output("version", version)
        if len(targets) == 1:
            set_output("rosdistro", targets[0]["rosdistro"])
        sys.exit(1)

    results: list[dict[str, Optional[str] | bool]] = []
    for target in targets:
        rosdistro = target["rosdistro"]
        track = target["track"]
        pr_url = run_single_target_release(
            repo_name=repository,
            rosdistro=rosdistro,
            track=track,
            release_repo=release_repository,
            version=version,
            package_names=package_names,
            dry_run=args.dry_run,
        )
        if pr_url is None:
            log_error(f"Release failed for {rosdistro}/{track}")
            set_output("released", "false")
            set_output("version", version)
            if len(targets) == 1:
                set_output("rosdistro", rosdistro)
            sys.exit(1)

        log_success(f"Release PR created for {rosdistro}/{track}: {pr_url}")
        results.append(
            {
                "branch": current_branch,
                "rosdistro": rosdistro,
                "track": track,
                "released": True,
                "pr_url": pr_url,
            }
        )

    set_output("released", "true")
    set_output("version", version)
    set_output("results-json", json.dumps(results))
    if len(targets) == 1:
        set_output("rosdistro", targets[0]["rosdistro"])
        set_output("pr-url", str(results[0]["pr_url"]))


if __name__ == "__main__":
    main()
