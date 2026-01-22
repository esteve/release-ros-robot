# Test Suite for release-ros-robot

This directory contains the pytest-based test suite for the release-ros-robot GitHub Action.

## Structure

```
tests/
├── __init__.py                    # Package initialization
├── conftest.py                    # Pytest fixtures and shared configuration
├── test_common.py                 # Tests for common.py shared utilities
├── test_prepare_release.py        # Tests for prepare_release.py script
└── test_execute_release.py        # Tests for execute_release.py script
```

## Setup

This project uses [mise](https://mise.jdx.dev) and [pixi](https://pixi.sh) to manage the
development environment. All dependencies are declared in `pyproject.toml`.

```bash
# Install mise (if not already installed)
curl https://mise.run | sh

# Install pixi and all project dependencies
mise install
pixi install
```

## Running Tests

### Run All Tests

```bash
pixi run test
```

### Run with Coverage

```bash
pixi run pytest --cov=scripts --cov-report=term-missing --cov-report=html
```

The HTML coverage report will be generated in `htmlcov/index.html`.

### Run Specific Tests

```bash
# Run specific test file
pixi run pytest tests/test_prepare_release.py

# Run specific test class
pixi run pytest tests/test_prepare_release.py::TestBumpVersion

# Run specific test function
pixi run pytest tests/test_prepare_release.py::TestBumpVersion::test_bump_patch

# Run tests matching a pattern
pixi run pytest -k "test_bump"
```

## Test Coverage

The test suite covers:

### `common.py`
- ✅ Exclude-path glob matching (discover_package_xmls)
- ✅ Package version extraction and consistency validation
- ✅ Last git tag lookup

### `prepare_release.py`
- ✅ Package version extraction and consistency validation
- ✅ Conventional commit parsing
- ✅ Version bumping (patch, minor, major)
- ✅ Version bump detection from commits
- ✅ Batch commit file fetching
- ✅ Per-package commit routing
- ✅ Package version updating
- ✅ Changelog entry generation
- ✅ Multi-package changelog filtering
- ✅ Git operations (tags, commits)

### `execute_release.py`
- ✅ Package version extraction
- ✅ Current branch detection
- ✅ Git tag operations
- ✅ Track existence checking
- ✅ Bloom-release execution (mocked)
- ✅ Integration workflow

## Fixtures

Located in `conftest.py`:

- `temp_dir`: Temporary directory for test isolation
- `temp_git_repo`: Temporary git repository with initial commit
- `package_xml_content`: Sample package.xml content

## Writing New Tests

When adding new tests:

1. Follow the existing test structure and naming conventions
2. Use descriptive test names: `test_<what>_<scenario>`
3. Use fixtures from `conftest.py` for common setup
4. Mock external commands (git, bloom-release, gh) using `@patch`
5. Test both success and failure cases
6. Include docstrings explaining what each test does

Example:

```python
def test_bump_version_major(self):
    """Test bumping major version."""
    assert calculate_next_version("1.2.3", "major") == "2.0.0"
```

## Continuous Integration

These tests run automatically in CI via `.github/workflows/ci.yml` using pixi:

```yaml
- name: Run tests with coverage
  run: pixi run --locked pytest --cov=scripts --cov-report=xml
```

## Notes

- Tests use mocking extensively to avoid external dependencies
- Git operations are tested in real temporary repositories
- Bloom-release and GitHub API calls are mocked
- All tests should be isolated and not affect each other
