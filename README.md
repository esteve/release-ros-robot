# Release ROS Robot

A GitHub Action to automate ROS package releases using `bloom-release`. It follows a workflow similar to [release-please](https://github.com/googleapis/release-please) and [release-plz](https://release-plz.dev/), but for ROS packages.

## Features

- Automated Release PRs: Creates PRs with changelog and version bumps, like release-please and release-plz
- Conventional Commits: Auto-detects version bump type from commit messages
- Automatic changelog generation in catkin CHANGELOG.rst format
- Automatic version bumping in package.xml files
- Multi-distro support via explicit workflow inputs
- Automatic PR creation to [rosdistro](https://github.com/ros/rosdistro)
- Support for both first-time and subsequent releases

## Quick Start

Users familiar with [release-please](https://github.com/googleapis/release-please) or [release-plz](https://release-plz.dev/) will recognize the same PR-driven release workflow. Consumers only need a workflow file, no configuration files in the repository.

1. When you merge PRs to `main`, the action creates/updates a release PR with changelog and version bump
2. When you merge the release PR, the action automatically runs `bloom-release` for configured ROS distros

### 1. Create the GitHub workflow

Create `.github/workflows/bloom-release.yaml`:

```yaml
name: Release

on:
  push:
    branches:
      - main
      - jazzy
      - humble

jobs:
  # Runs on every push. Self-skips on non-release commits.
  release:
    name: Release
    runs-on: ubuntu-latest
    permissions:
      contents: write
    env:
      BLOOM_OAUTH_TOKEN: ${{ secrets.BLOOM_OAUTH_TOKEN }}
      BLOOM_GITHUB_USER: ${{ secrets.BLOOM_GITHUB_USER }}
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 0
          persist-credentials: false

      - name: Run bloom-release (main → rolling)
        if: github.ref_name == 'main'
        uses: esteve/release-ros-robot@v1
        with:
          mode: release
          repository: my_ros_package
          rosdistro: rolling
          track: rolling
          release-repository: https://github.com/ros2-gbp/my_ros_package-release.git

      - name: Run bloom-release (jazzy)
        if: github.ref_name == 'jazzy'
        uses: esteve/release-ros-robot@v1
        with:
          mode: release
          repository: my_ros_package
          rosdistro: jazzy
          track: jazzy
          release-repository: https://github.com/ros2-gbp/my_ros_package-release.git

      - name: Run bloom-release (humble)
        if: github.ref_name == 'humble'
        uses: esteve/release-ros-robot@v1
        with:
          mode: release
          repository: my_ros_package
          rosdistro: humble
          track: humble
          release-repository: https://github.com/ros2-gbp/my_ros_package-release.git

  # Runs on every push. Self-skips when the push came from merging a release PR.
  # The concurrency block ensures only one instance runs at a time per branch.
  release-pr:
    name: Release PR
    runs-on: ubuntu-latest
    permissions:
      contents: write
      pull-requests: write
    concurrency:
      group: release-pr-${{ github.ref }}
      cancel-in-progress: false
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 0
          persist-credentials: false

      - name: Update release PR
        uses: esteve/release-ros-robot@v1
        with:
          mode: prepare
          base-branch: ${{ github.ref_name }}
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

Keep release runs sequential when multiple tracks share the same release
repository. The example above is sequential because each release happens in a
separate step; if you switch to a matrix, set `strategy.max-parallel: 1`.

> Running in forks? Add `if: ${{ github.repository_owner == 'YOUR_ORG' }}` to
> each job to prevent the workflow from running (and failing) in forks. This is
> the same pattern recommended by the
> [release-plz quickstart](https://release-plz.dev/docs/github/quickstart).

### 2. Set up secrets

`BLOOM_OAUTH_TOKEN` and `BLOOM_GITHUB_USER` are only needed by the
`release` job (Settings → Secrets and variables → Actions):

`BLOOM_OAUTH_TOKEN`, A GitHub Personal Access Token (PAT) with:
- `public_repo`. For creating PRs on rosdistro
- `workflow`. For pushing tags and commits

`BLOOM_GITHUB_USER`, The GitHub username that owns the PAT above.
bloom-release uses a fork-based workflow: it pushes rosdistro changes to
`<BLOOM_GITHUB_USER>/rosdistro` before opening a PR to `ros/rosdistro`.
The action ensures this fork exists before running bloom. On the first release,
it may create `<BLOOM_GITHUB_USER>/rosdistro` automatically.

The `release-pr` job uses the built-in `GITHUB_TOKEN`, no extra secrets needed.

### 3. Use Conventional Commits

The action automatically detects version bump type from your commit messages
using the [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) specification:

```bash
feat: add new feature         # -> minor bump
fix: correct bug              # -> patch bump
feat!: breaking change        # -> major bump
fix: bug fix

BREAKING CHANGE: description  # -> major bump
```

## How It Works

### Prepare Mode

1. Analyzes commits since last tag using Conventional Commits
2. Determines version bump type (major/minor/patch)
3. Updates `package.xml` with new version
4. Generates/updates `CHANGELOG.rst` files following REP-0132:
   - Version header with date
   - Commit bullet list sorted by significance (breaking first, then features, fixes, other)
   - Contributor attribution
5. Creates or force-pushes to release PR branch: `bloom-release-<timestamp>`

### Release Mode

Triggered by the push that merging the release PR creates. The merge commit
message starts with `chore(release):`, which the `release` job detects to
decide whether to proceed. On any other push the job exits immediately.

1. Reads version from `package.xml` (already updated by prepare mode)
2. Creates git tag with version number (idempotent. Skips if tag already exists)
3. Ensures the PAT owner's `rosdistro` fork exists
4. Runs `bloom-release` with the explicitly provided repository, distribution,
   track, and release repository
5. Creates PR(s) to ros/rosdistro

Because the no-op guard is commit-driven rather than tag-driven, multiple
release steps targeting different tracks on the same branch can all operate on
the same release commit. The source tag is only created once, but bloom still
mutates shared state in the release repository, so releases for a shared
release repository should run sequentially. If you use a matrix, set
`strategy.max-parallel: 1`.

### Multi-Package Repositories

This action supports repositories with multiple ROS packages (e.g.,
[rclcpp](https://github.com/ros2/rclcpp), [ros2_control](https://github.com/ros-controls/ros2_control)). Per bloom's
requirements:

- Synchronized versions. All `package.xml` files must share the same
  version. The action enforces this and fails with a clear error if they
  drift. Version bumps are applied to every `package.xml` at once, and a
  single git tag is created per release.

- Per-package changelogs. Each package gets its own `CHANGELOG.rst`.
  All entries share the same version header and date, but bullet points
  are filtered so each package's changelog only lists commits that touched
  files inside that package's directory. This matches the ROS convention
  used by [rclcpp](https://github.com/ros2/rclcpp) and similar monorepos.

- Packages with no changes. Still get a version header (with an empty
  body) in their `CHANGELOG.rst`, since bloom releases all packages at the
  same version.

- Repo-wide commits. Commits that don't touch any package directory
  (e.g., root `README.md`, CI config, `.github/` changes) are not included
  in any package's changelog.

### Excluding Test Fixtures and Vendored Code

If your repository contains `package.xml` files that should not be
treated as real packages (e.g., test fixtures, vendored third-party code),
use the `exclude-paths` input:

```yaml
      - name: Update release PR
        uses: esteve/release-ros-robot@v1
        with:
          mode: prepare
          base-branch: ${{ github.ref_name }}
          exclude-paths: |
            test/**
            tests/**
            third_party/**
            vendor/**
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

Add the same `exclude-paths` input to the `release` steps as well.

Patterns are POSIX-style globs matched against the full relative path of
each `package.xml` (e.g., `test/fixtures/minimal/package.xml`). Without
this exclusion, fixture `package.xml` files would be:

1. Checked against the version consistency requirement. Usually causing
   release failure with a version mismatch error
2. Rewritten in place with the new release version
3. Given a spurious `CHANGELOG.rst` in the fixture directory
4. Staged for commit in the release PR

## Action Inputs

| Input | Description | Required | Default |
|-------|-------------|----------|---------|
| `mode` | Action mode: `prepare` or `release` | No | `release` |
| `oauth-token` | PAT for release mode. Falls back to `BLOOM_OAUTH_TOKEN` env var. Not used in prepare mode. | No | - |
| `github-user` | GitHub username for bloom fork workflow. Falls back to `BLOOM_GITHUB_USER` env var. Release mode only. | No | - |
| `repository` | Repository name as registered in rosdistro. Required in release mode. | No | - |
| `rosdistro` | ROS distribution to release (e.g., `rolling`, `jazzy`). Required in release mode. | No | - |
| `track` | Bloom track to use (e.g., `rolling`, `jazzy`). Required in release mode. | No | - |
| `release-repository` | Release repository URL (e.g., `https://github.com/ros2-gbp/my_package-release.git`). Required in release mode. | No | - |
| `dry-run` | Run without actually releasing | No | `false` |
| `exclude-paths` | Newline-separated glob patterns to exclude from `package.xml` discovery | No | - |
| `version-bump` | Version bump type: `auto`, `patch`, `minor`, `major` | No | `auto` |
| `base-branch` | Base branch for release PR | No | `main` |

## Action Outputs

| Output | Description |
|--------|-------------|
| `pr-created` | Whether a release PR was created (`prepare` mode) |
| `released` | Whether a release was performed (`release` mode) |
| `version` | The version that was released or will be released |
| `rosdistro` | The ROS distribution released to (`release` mode) |
| `pr-url` | URL of the release PR or rosdistro PR |


## Conventional Commits

The action supports automatic version bump detection using the
[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) specification:

```
<type>[optional scope][!]: <description>
```

Version bump rules:
- `BREAKING CHANGE` in message or `!` after type → `major`
- `feat` type → `minor`
- `fix`, `perf`, `refactor`, `docs`, `style`, `test`, `chore`, `build`, `ci` → `patch`

Examples:
```
feat: add new ROS distro support          → minor
feat!: redesign configuration format      → major
fix: correct version parsing              → patch
docs: update README                       → patch
refactor(core): simplify release logic    → patch
```

Set `version-bump: auto` (default) to enable automatic detection.

## Prerequisites

### Repository Workflow Permissions

Go to your repository Settings -> Actions -> General -> Workflow permissions:

1. Select "Read and write permissions"
2. Check "Allow GitHub Actions to create and approve pull requests"

This is required by the `release-pr` job so that `GITHUB_TOKEN` can push the
release branch and open a PR in your repository.

### Token Permissions

`release-pr` job uses `GITHUB_TOKEN` (automatic -- no extra setup needed) with:
- `contents: write` -- push the release branch
- `pull-requests: write` -- create/update the release PR

`release` job uses `BLOOM_OAUTH_TOKEN` (PAT you create) with:
- `public_repo` -- open a PR on `ros/rosdistro`
- `workflow` -- push tags and commits to your repository

## Troubleshooting

### Version bump is wrong or misses commits

Caused by a shallow git clone. Both modes need full history.

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0
```

### Release step does nothing for a branch

Each release step needs explicit `repository`, `rosdistro`, `track`, and
`release-repository` inputs. The action will fail fast if any are missing.

### "Failed to push to rosdistro fork" / "fork not found"

bloom-release pushes to `https://github.com/<BLOOM_GITHUB_USER>/rosdistro`
before opening a PR to `ros/rosdistro`. Ensure:

1. `BLOOM_GITHUB_USER` is the owner of the PAT in `BLOOM_OAUTH_TOKEN`
2. The PAT has `public_repo` and `workflow` scopes
3. That user is allowed to create a fork of https://github.com/ros/rosdistro

### `release-pr` job fails with a permission error

Check that the job has `permissions: contents: write` and
`pull-requests: write`, and that "Allow GitHub Actions to create and
approve pull requests" is enabled in repository settings.

## Development

### Setup

This project uses [mise](https://mise.jdx.dev) to manage tools and [pixi](https://pixi.sh) to manage the Python environment. Both are declared in `mise.toml` and `pyproject.toml` respectively.

```bash
# Clone the repository
git clone https://github.com/esteve/release-ros-robot.git
cd release-ros-robot

# Install mise (if not already installed)
curl https://mise.run | sh

# Install pixi and all project dependencies
mise install
pixi install

# Install pre-commit hooks (recommended)
pixi run pre-commit install
```

### Running Tests

```bash
# Run all tests
pixi run test

# Run with coverage
pixi run pytest --cov=scripts --cov-report=term-missing

# Run specific test
pixi run pytest tests/test_prepare_release.py::TestBumpVersion::test_bump_patch
```

### Code Quality

```bash
# Check formatting with Black
pixi run format-check

# Lint with Ruff
pixi run lint

# Type check with mypy
pixi run typecheck

# Run all pre-commit hooks
pixi run pre-commit run --all-files
```

The hooks automatically check for:
- Python formatting (Black)
- Linting (Ruff with auto-fixes)
- Type checking (mypy)
- YAML/JSON validation
- Python syntax errors

## License

Apache-2.0
