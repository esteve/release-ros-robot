#!/usr/bin/env python3
"""
Prepare Release Script for GitHub Actions

This script prepares a release PR using a workflow similar to release-please
and release-plz for ROS packages:
1. Detect version bump from conventional commits
2. Generate/update changelog
3. Bump version in package.xml
4. Create or update a release PR
"""

import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from common import (
    DEFAULT_CONFIG_FILE,
    discover_package_xmls,
    get_config_string,
    get_last_tag,
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

# Type alias for commit dicts returned by get_commits_since_ref.
# Fields: hash (str), author (str), email (str), date (str),
#         subject (str), body (str), files (list[str]).
Commit = dict[str, Any]


def parse_conventional_commit(message: str) -> tuple[Optional[str], bool]:
    """
    Parse a conventional commit message.

    Conventional commit format: <type>[optional scope][!]: <description>

    Args:
        message: The commit message subject line

    Returns:
        Tuple of (commit_type, is_breaking)
        commit_type is None if the message doesn't follow conventional commits
    """
    pattern = r"^(?P<type>[a-z]+)(?:\([^)]+\))?(?P<breaking>!)?\s*:\s*.+"
    match = re.match(pattern, message.lower())

    if not match:
        return None, False

    commit_type = match.group("type")
    is_breaking = match.group("breaking") == "!"

    return commit_type, is_breaking


def is_breaking_change(subject: str, body: str) -> bool:
    """
    Check whether a commit contains any breaking-change marker.

    Detects both BREAKING CHANGE / BREAKING-CHANGE keywords in subject/body
    and the '!' notation parsed by parse_conventional_commit.

    Args:
        subject: Commit subject line
        body: Commit body text

    Returns:
        True if the commit is a breaking change
    """
    normalized_subject = subject.upper()
    normalized_body = body.upper()
    if (
        "BREAKING CHANGE" in normalized_subject
        or "BREAKING-CHANGE" in normalized_subject
    ):
        return True
    if "BREAKING CHANGE" in normalized_body or "BREAKING-CHANGE" in normalized_body:
        return True
    _, has_breaking_marker = parse_conventional_commit(subject)
    return has_breaking_marker


def categorize_commit(subject: str, body: str) -> str:
    """
    Categorize a commit for changelog ordering and PR summary grouping.

    Args:
        subject: Commit subject line
        body: Commit body text

    Returns:
        One of: "breaking", "feat", "fix", "other"
    """
    if is_breaking_change(subject, body):
        return "breaking"
    commit_type, _ = parse_conventional_commit(subject)
    if commit_type == "feat":
        return "feat"
    if commit_type == "fix":
        return "fix"
    return "other"


def get_last_release_commit() -> Optional[str]:
    """
    Get the most recent release commit SHA.

    Looks for commits with message pattern "chore(release): prepare release X.Y.Z"
    which are created when release PRs are merged.

    Returns:
        Commit SHA of the last release, or None if no release commits found
    """
    result = run_command(
        [
            "git",
            "log",
            "--no-merges",
            "--grep=^chore(release): prepare release",
            "--format=%H",
            "-1",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def get_commit_files_batch(ref: Optional[str] = None) -> dict[str, list[str]]:
    """Fetch files-changed per commit in a single git log call.

    Uses a sentinel marker to reliably separate commit records in the
    interleaved --name-only output, independent of git's blank-line
    behavior between the format string and the file list.

    Args:
        ref: Git ref to fetch commit files since. If None, fetches all commits.

    Returns:
        Dict mapping commit hash to list of file paths. Commits not in the
        result (e.g., empty commits with no file changes) implicitly have no
        files, callers should default to an empty list via .get().
    """
    sentinel = "__COMMIT_BOUNDARY__"
    format_arg = f"--format={sentinel}%H"
    if ref:
        cmd = [
            "git",
            "log",
            "--no-merges",
            f"{ref}..HEAD",
            "--name-only",
            format_arg,
        ]
    else:
        cmd = ["git", "log", "--no-merges", "--name-only", format_arg]

    result = run_command(cmd, capture_output=True, check=False)
    if result.returncode != 0:
        return {}

    files_by_hash: dict[str, list[str]] = {}
    for chunk in result.stdout.split(sentinel):
        lines = [line for line in chunk.split("\n") if line]
        if not lines:
            continue
        commit_hash = lines[0]
        files_by_hash[commit_hash] = lines[1:]
    return files_by_hash


def get_commits_since_ref(ref: Optional[str] = None) -> list[Commit]:
    """
    Get commit information since the given ref.

    Args:
        ref: A git ref (tag, commit SHA, etc.) to get commits since.
             If None, gets all commits.

    Returns:
        List of dicts with commit info: {subject, body, author, date, hash}
    """
    if ref:
        cmd = [
            "git",
            "log",
            "--no-merges",
            f"{ref}..HEAD",
            "--pretty=format:%H%x1f%an%x1f%ae%x1f%aI%x1f%s%x1f%b%x1e",
        ]
    else:
        cmd = [
            "git",
            "log",
            "--no-merges",
            "--pretty=format:%H%x1f%an%x1f%ae%x1f%aI%x1f%s%x1f%b%x1e",
        ]

    result = run_command(cmd, capture_output=True, check=False)
    if result.returncode == 0 and result.stdout.strip():
        commits = []
        for commit_str in result.stdout.strip().split("\x1e"):
            if not commit_str.strip():
                continue
            parts = commit_str.split("\x1f")
            if len(parts) >= 5:
                commits.append(
                    {
                        "hash": parts[0].strip(),
                        "author": parts[1],
                        "email": parts[2],
                        "date": parts[3],
                        "subject": parts[4],
                        "body": parts[5] if len(parts) > 5 else "",
                    }
                )

        # Pre-populate per-commit file lists in a single batched git call.
        # Commits not present in the batch result (e.g., empty commits with no
        # file changes) get an empty list and correctly route to no package.
        if commits:
            files_by_hash = get_commit_files_batch(ref)
            for commit in commits:
                commit["files"] = files_by_hash.get(commit["hash"], [])

        return commits
    return []


def detect_version_bump_from_commits(commits: list[Commit]) -> str:
    """
    Determine the appropriate version bump type from a list of commits.

    Follows Conventional Commits specification:
    - BREAKING CHANGE or ! after type -> major
    - feat -> minor
    - fix, perf, refactor, etc. -> patch

    Args:
        commits: Pre-fetched list of commit dicts from get_commits_since_ref.

    Returns:
        Version bump type: "major", "minor", or "patch"
    """
    if not commits:
        log_info("No commits found, defaulting to patch bump")
        return "patch"

    log_info(f"Analyzing {len(commits)} commit(s) for version bump type")

    has_breaking = False
    has_feature = False

    for commit in commits:
        category = categorize_commit(commit["subject"], commit["body"])
        if category == "breaking":
            has_breaking = True
        elif category == "feat":
            has_feature = True

    if has_breaking:
        log_info("Detected breaking change(s) -> major bump")
        return "major"
    elif has_feature:
        log_info("Detected new feature(s) -> minor bump")
        return "minor"
    else:
        log_info("Detected fixes/other changes -> patch bump")
        return "patch"


def calculate_next_version(current_version: str, bump_type: str) -> str:
    """
    Calculate the next version based on the bump type.

    Args:
        current_version: Current version string (e.g., "1.2.3")
        bump_type: Type of bump ("major", "minor", or "patch")

    Returns:
        Next version string
    """
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", current_version)
    if not match:
        log_error(f"Invalid version format: {current_version}")
        sys.exit(1)

    major, minor, patch = map(int, match.groups())

    if bump_type == "major":
        return f"{major + 1}.0.0"
    elif bump_type == "minor":
        return f"{major}.{minor + 1}.0"
    else:  # patch
        return f"{major}.{minor}.{patch + 1}"


def update_package_xml_version(version: str, exclude_patterns: list[str]) -> None:
    """Update version in all package.xml files."""
    package_xmls = discover_package_xmls(exclude_patterns)

    for package_xml in package_xmls:
        log_info(f"Updating version in {package_xml}")
        with open(package_xml) as f:
            content = f.read()

        updated_content = re.sub(
            r"<version>[^<]+</version>",
            f"<version>{version}</version>",
            content,
        )

        with open(package_xml, "w") as f:
            f.write(updated_content)


_CATEGORY_PRIORITY: dict[str, int] = {"breaking": 0, "feat": 1, "fix": 2, "other": 3}


def generate_changelog_entry(
    version: str, commits: list[Commit], entry_date: str
) -> str:
    """
    Generate a single changelog entry following REP-0132 format.

    REP-0132 specifies that changelog entries should be simple bullet lists
    without subsections. See: https://ros.org/reps/rep-0132.html

    Args:
        version: Version number for this entry
        commits: List of commit dicts
        entry_date: Date string in YYYY-MM-DD format

    Returns:
        Formatted changelog entry in RST format compliant with REP-0132
    """
    lines = []
    lines.append(f"{version} ({entry_date})")
    lines.append("-" * len(lines[0]))

    # Collect unique contributors and categorize commits for ordering
    contributors: set[str] = set()
    all_items = []

    for commit in commits:
        contributors.add(commit["author"])
        category = categorize_commit(commit["subject"], commit["body"])
        all_items.append((_CATEGORY_PRIORITY[category], commit["subject"]))

    # Sort by priority (breaking first, then feat, fix, other)
    all_items.sort(key=lambda x: x[0])

    # Add all changes as simple bullet list (REP-0132 compliant)
    for _, subject in all_items:
        lines.append(f"* {subject}")

    # Add contributors at the end (common ROS practice)
    if contributors:
        lines.append(f"* Contributors: {', '.join(sorted(contributors))}")

    lines.append("")
    return "\n".join(lines)


def find_package_dirs(exclude_patterns: list[str]) -> list[Path]:
    """Find all package directories (directories containing a package.xml)."""
    package_xmls = discover_package_xmls(exclude_patterns)
    return sorted({p.parent for p in package_xmls})


def commit_touches_package(commit: Commit, package_dir: Path) -> bool:
    """Check whether a commit touched any file inside package_dir.

    Uses string prefix matching on POSIX-style paths, which handles the
    repo-root case (package at ".") gracefully without try/except noise
    around Path.relative_to.

    Requires commit['files'] to be pre-populated by get_commits_since_ref
    via get_commit_files_batch. Raises KeyError if missing, fail loud
    rather than silently dropping commits.

    Args:
        commit: Commit dict with a pre-populated 'files' list
        package_dir: Directory of the package to check against

    Returns:
        True if any changed file is inside package_dir, False otherwise
    """
    files = commit["files"]

    pkg_prefix = package_dir.as_posix().lstrip("./")
    if pkg_prefix and not pkg_prefix.endswith("/"):
        pkg_prefix += "/"

    for file_path in files:
        if not pkg_prefix:  # package is at repo root; all files belong to it
            return True
        if file_path.startswith(pkg_prefix):
            return True
    return False


def filter_commits_for_package(
    commits: list[Commit], package_dir: Path
) -> list[Commit]:
    """Return commits that touched at least one file in package_dir.

    Args:
        commits: List of commit dicts with pre-populated 'files' lists
        package_dir: Directory of the package to filter for

    Returns:
        Subset of commits that touched the package
    """
    return [c for c in commits if commit_touches_package(c, package_dir)]


def generate_empty_changelog_entry(version: str, entry_date: str) -> str:
    """Generate a version-header-only changelog entry (no bullets).

    Used when a package has no commits touching it in this release, but
    still needs a CHANGELOG.rst entry because bloom releases all packages
    at the same synchronized version.

    Args:
        version: Version number for this entry
        entry_date: Date string in YYYY-MM-DD format

    Returns:
        RST-formatted version header with divider and no bullet points
    """
    header = f"{version} ({entry_date})"
    return f"{header}\n{'-' * len(header)}\n"


def update_changelog_files(
    version: str, commits: list[Commit], exclude_patterns: list[str]
) -> None:
    """
    Update CHANGELOG.rst files for all packages with per-package entries.

    Each package gets its own entry filtered to commits that touched files
    inside that package's directory. All packages share the same version
    header and date (bloom enforces synchronized versions), but bullet
    points differ per package following ROS multi-package convention
    (see https://github.com/ros2/rclcpp).

    Packages with no commits touching them still get a version header
    (empty entry) since bloom releases them at the same version.
    Repo-wide commits not inside any package directory are dropped.

    Args:
        version: New version number
        commits: List of commits to include in changelog
        exclude_patterns: Glob patterns to exclude from package.xml discovery
    """
    log_info("Updating CHANGELOG.rst files...")

    if not commits:
        log_warning("No commits to add to changelog")
        return

    today = date.today().strftime("%Y-%m-%d")
    package_dirs = find_package_dirs(exclude_patterns)

    # Update or create CHANGELOG.rst in each package directory
    for pkg_dir in package_dirs:
        pkg_commits = filter_commits_for_package(commits, pkg_dir)

        if pkg_commits:
            log_info(f"  {pkg_dir.name}: {len(pkg_commits)} commit(s)")
            new_entry = generate_changelog_entry(version, pkg_commits, today)
        else:
            log_info(f"  {pkg_dir.name}: no changes (empty entry)")
            new_entry = generate_empty_changelog_entry(version, today)

        changelog_path = pkg_dir / "CHANGELOG.rst"

        if changelog_path.exists():
            # Read existing changelog
            with open(changelog_path) as f:
                existing_content = f.read()

            # Check if this version already exists
            if f"{version} (" in existing_content:
                log_info(f"Version {version} already in {changelog_path}, updating...")
                # Remove existing entry for this version
                lines = existing_content.split("\n")
                new_lines = []
                skip = False
                for i, line in enumerate(lines):
                    if line.startswith(f"{version} ("):
                        skip = True
                        continue
                    # Stop skipping when we hit the next version or end
                    if (
                        skip
                        and i > 0
                        and line
                        and not line.startswith(" ")
                        and not line.startswith("-")
                        and not line.startswith("^")
                    ):
                        if re.match(r"^\d+\.\d+\.\d+", line):
                            skip = False
                    if not skip:
                        new_lines.append(line)
                existing_content = "\n".join(new_lines).strip()

            # Find the position after the header
            header_pattern = r"^[^\n]+\n[=]+\n"
            match = re.search(header_pattern, existing_content, re.MULTILINE)

            if match:
                # Insert after header
                insert_pos = match.end()
                new_content = (
                    existing_content[:insert_pos]
                    + "\n"
                    + new_entry
                    + "\n"
                    + existing_content[insert_pos:].lstrip()
                )
            else:
                # No proper header, prepend
                new_content = new_entry + "\n\n" + existing_content
        else:
            # Create new changelog
            log_info(f"Creating new {changelog_path}")
            package_name = pkg_dir.name
            header = f"^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\nChangelog for package {package_name}\n^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n\n"
            new_content = header + new_entry

        # Write updated changelog
        with open(changelog_path, "w") as f:
            f.write(new_content)

        log_success(f"Updated {changelog_path}")

        # Stage the file
        run_command(["git", "add", str(changelog_path)])


def get_existing_release_branch(base_branch: str) -> Optional[str]:
    """
    Get the name of an existing bloom-release PR branch.

    Args:
        base_branch: Base branch to check PRs for

    Returns:
        Branch name if exists, None otherwise
    """
    result = run_command(
        [
            "gh",
            "pr",
            "list",
            "--base",
            base_branch,
            "--state",
            "open",
            "--json",
            "headRefName,url",
            "--jq",
            '.[] | select(.headRefName | startswith("bloom-release-")) | .headRefName',
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().split("\n")[0]
    return None


def get_release_branch_name(base_branch: str) -> tuple[str, bool]:
    """
    Get release PR branch name and whether it already exists.

    Args:
        base_branch: Base branch to check for existing release branches

    Returns:
        Tuple of (branch_name, is_existing): is_existing is True if the
        branch was reused from an open release PR, False if a new name
        was generated.
    """
    existing = get_existing_release_branch(base_branch)
    if existing:
        log_info(f"Reusing existing release branch: {existing}")
        return existing, True

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"bloom-release-{timestamp}", False


def generate_pr_summary(commits: list[Commit]) -> str:
    """
    Generate a summary of changes for the PR body.

    Args:
        commits: List of commit dicts

    Returns:
        Formatted summary in Markdown
    """
    if not commits:
        return "No changes"

    lines = []
    features = []
    fixes = []
    breaking = []
    other = []

    for commit in commits:
        category = categorize_commit(commit["subject"], commit["body"])
        if category == "breaking":
            breaking.append(commit["subject"])
        elif category == "feat":
            features.append(commit["subject"])
        elif category == "fix":
            fixes.append(commit["subject"])
        else:
            other.append(commit["subject"])

    if breaking:
        lines.append("### BREAKING CHANGES")
        for msg in breaking:
            lines.append(f"* {msg}")
        lines.append("")

    if features:
        lines.append("### Features")
        for msg in features:
            lines.append(f"* {msg}")
        lines.append("")

    if fixes:
        lines.append("### Bug Fixes")
        for msg in fixes:
            lines.append(f"* {msg}")
        lines.append("")

    if other:
        lines.append("### Other Changes")
        for msg in other:
            lines.append(f"* {msg}")
        lines.append("")

    return "\n".join(lines)


def create_or_update_release_pr(
    base_branch: str,
    version: str,
    commits: list[Commit],
    exclude_patterns: list[str],
) -> tuple[str, bool]:
    """
    Create or update a release PR.

    Args:
        base_branch: Base branch to merge into
        version: New version number
        commits: List of commits for this release
        exclude_patterns: Glob patterns to exclude from package.xml discovery

    Returns:
        Tuple of (PR URL, is_new_pr)
    """
    release_branch, is_existing_branch = get_release_branch_name(base_branch)

    # Checkout base branch and ensure we're up to date
    run_command(["git", "checkout", base_branch])
    run_command(["git", "pull", "origin", base_branch])

    # Create or checkout release branch
    if is_existing_branch:
        log_info(f"Updating existing release branch: {release_branch}")
        run_command(["git", "fetch", "origin", release_branch])
        run_command(["git", "checkout", "-B", release_branch, f"origin/{base_branch}"])
    else:
        log_info(f"Creating new release branch: {release_branch}")
        run_command(["git", "checkout", "-b", release_branch])

    # Update package.xml
    update_package_xml_version(version, exclude_patterns)

    # Update CHANGELOG.rst files
    update_changelog_files(version, commits, exclude_patterns)

    # Stage changes
    for package_xml in discover_package_xmls(exclude_patterns):
        run_command(["git", "add", str(package_xml)])

    # Commit changes
    commit_message = f"chore(release): prepare release {version}"
    run_command(["git", "commit", "-m", commit_message])

    # Push branch (force push if updating existing)
    if is_existing_branch:
        log_info("Force-pushing to existing release branch")
        run_command(["git", "push", "--force", "origin", release_branch])
    else:
        run_command(["git", "push", "-u", "origin", release_branch])

    # Generate PR body
    changelog_summary = generate_pr_summary(commits)

    pr_body = f"""## Release {version}

This automated PR prepares the release for version {version}.

When this PR is merged, the bloom-release workflow will automatically:
- Create git tag {version}
- Run bloom-release for configured ROS distributions
- Create PR(s) to ros/rosdistro

### Changes

{changelog_summary}

---
*This PR was automatically generated by the [release-ros-robot](https://github.com/esteve/release-ros-robot)*
"""

    if is_existing_branch:
        # Update existing PR
        log_info("Updating existing PR")
        run_command(
            [
                "gh",
                "pr",
                "edit",
                release_branch,
                "--title",
                f"chore(release): {version}",
                "--body",
                pr_body,
            ],
            capture_output=True,
        )
        # Get PR URL
        result = run_command(
            [
                "gh",
                "pr",
                "view",
                release_branch,
                "--json",
                "url",
                "--jq",
                ".url",
            ],
            capture_output=True,
        )
        pr_url = result.stdout.strip()
        return pr_url, False
    else:
        # Create new PR
        log_info("Creating new PR")
        result = run_command(
            [
                "gh",
                "pr",
                "create",
                "--base",
                base_branch,
                "--head",
                release_branch,
                "--title",
                f"chore(release): {version}",
                "--body",
                pr_body,
            ],
            capture_output=True,
        )
        pr_url = result.stdout.strip()
        return pr_url, True


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Prepare a release PR with version bump and changelog",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config-file",
        default=DEFAULT_CONFIG_FILE,
        help="Path to the TOML configuration file",
    )
    parser.add_argument(
        "--version-bump",
        default="auto",
        choices=["auto", "major", "minor", "patch"],
        help="Version bump type: auto (detect from conventional commits), major, minor, or patch",
    )
    parser.add_argument(
        "--base-branch",
        default="main",
        help="Base branch for release PR",
    )
    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()
    config = load_config_file(args.config_file)

    # Self-no-op: skip when the push was caused by merging a release PR.
    # Works for squash and rebase merges; merge-commit merges are handled
    # by the existing "no new commits" guard below.
    if is_release_commit():
        log_info("HEAD is a release commit, nothing to prepare")
        set_output("pr-created", "false")
        return

    exclude_paths = resolve_exclude_paths(config)

    config_base_branch = get_config_string(
        config, "prepare", "base_branch", field_name="prepare.base_branch"
    )
    config_version_bump = get_config_string(
        config, "prepare", "version_bump", field_name="prepare.version_bump"
    )

    base_branch = args.base_branch or config_base_branch or "main"
    version_bump = args.version_bump or config_version_bump or "auto"

    # Get current version from package.xml
    package_version = get_package_version(exclude_paths)
    log_info(f"Version in package.xml: {package_version}")

    # Get base version for calculation (prefer tag over package.xml)
    last_tag = get_last_tag()
    if last_tag:
        base_version = last_tag
        log_info(f"Using last tag as base version: {base_version}")
        commit_ref = last_tag
    else:
        # No tag found, try to find last release commit
        last_release_commit = get_last_release_commit()
        if last_release_commit:
            # Use package.xml version as base (since it was updated in the release commit)
            base_version = package_version
            commit_ref = last_release_commit
            log_info(
                f"No tag found, using last release commit {last_release_commit[:8]} "
                f"with version {base_version}"
            )
        else:
            # No tag and no release commit, use package.xml and analyze all commits
            base_version = package_version
            commit_ref = None
            log_info(
                f"No tag or release commit found, using package.xml version: {base_version}"
            )

    # Fetch commits once, used for both bump detection and changelog generation
    commits = get_commits_since_ref(commit_ref)
    if not commits:
        log_info("No new commits since last release, skipping")
        set_output("pr-created", "false")
        return

    # Detect version bump
    if version_bump == "auto":
        bump_type = detect_version_bump_from_commits(commits)
    else:
        bump_type = version_bump
        log_info(f"Using explicit version bump: {bump_type}")

    # Calculate next version
    next_version = calculate_next_version(base_version, bump_type)
    log_info(f"Next version: {next_version}")

    # Create or update release PR
    log_info(f"Creating/updating release PR for version {next_version}...")
    pr_url, is_new = create_or_update_release_pr(
        base_branch, next_version, commits, exclude_paths
    )

    if is_new:
        log_success(f"Release PR created: {pr_url}")
    else:
        log_success(f"Release PR updated: {pr_url}")

    set_output("pr-created", "true")
    set_output("pr-url", pr_url)
    set_output("version", next_version)


if __name__ == "__main__":
    main()
