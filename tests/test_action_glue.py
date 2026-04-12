"""Lightweight glue tests for action.yml structure and shell wiring.

These tests validate composite-action structural expectations without
running actual GitHub Actions or requiring network access.  They are
intentionally narrow: they check shell patterns and YAML structure, not
business logic (which lives in the Python scripts and their own test
suites).
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ACTION_YML = Path(__file__).parent.parent / "action.yml"


@pytest.fixture(scope="module")
def action() -> dict[str, Any]:
    """Load and parse action.yml once for all tests in this module."""
    return yaml.safe_load(ACTION_YML.read_text())


@pytest.fixture(scope="module")
def steps(action: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the composite action steps list."""
    return action["runs"]["steps"]


@pytest.fixture(scope="module")
def action_text() -> str:
    """Return the raw text of action.yml for shell-pattern assertions."""
    return ACTION_YML.read_text()


# ---------------------------------------------------------------------------
# Basic YAML validity
# ---------------------------------------------------------------------------


class TestActionYmlParseable:
    """action.yml must be parseable and well-formed."""

    def test_yaml_parses_without_error(self) -> None:
        """action.yml is valid YAML."""
        content = ACTION_YML.read_text()
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, dict)

    def test_top_level_keys_present(self, action: dict[str, Any]) -> None:
        """action.yml contains required top-level keys."""
        for key in ("name", "description", "inputs", "outputs", "runs"):
            assert key in action, f"Missing top-level key: {key}"

    def test_composite_runner(self, action: dict[str, Any]) -> None:
        """action.yml uses the composite runner."""
        assert action["runs"]["using"] == "composite"

    def test_steps_is_nonempty_list(self, steps: list[dict[str, Any]]) -> None:
        """Steps list exists and is non-empty."""
        assert isinstance(steps, list)
        assert len(steps) > 0


# ---------------------------------------------------------------------------
# Expected step names
# ---------------------------------------------------------------------------


class TestExpectedSteps:
    """Key steps must be present and in the correct relative order."""

    def _step_names(self, steps: list[dict[str, Any]]) -> list[str]:
        return [s.get("name", "") for s in steps]

    def test_setup_step_present(self, steps: list[dict[str, Any]]) -> None:
        """'Set up mise and pixi' step is present."""
        names = self._step_names(steps)
        assert "Set up mise and pixi" in names

    def test_install_dependencies_step_present(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """'Install dependencies' step is present."""
        names = self._step_names(steps)
        assert "Install dependencies" in names

    def test_configure_git_step_present(self, steps: list[dict[str, Any]]) -> None:
        """'Configure Git' step is present."""
        names = self._step_names(steps)
        assert "Configure Git" in names

    def test_configure_bloom_step_present(self, steps: list[dict[str, Any]]) -> None:
        """'Configure Bloom' step is present."""
        names = self._step_names(steps)
        assert "Configure Bloom" in names

    def test_ensure_rosdistro_fork_step_present(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """'Ensure rosdistro fork exists' step is present."""
        names = self._step_names(steps)
        assert "Ensure rosdistro fork exists" in names

    def test_prepare_release_step_present(self, steps: list[dict[str, Any]]) -> None:
        """'Prepare Release PR' step is present."""
        names = self._step_names(steps)
        assert "Prepare Release PR" in names

    def test_bloom_release_step_present(self, steps: list[dict[str, Any]]) -> None:
        """'Run Bloom Release' step is present."""
        names = self._step_names(steps)
        assert "Run Bloom Release" in names

    def test_configure_git_before_configure_bloom(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """'Configure Git' must precede 'Configure Bloom'."""
        names = self._step_names(steps)
        assert names.index("Configure Git") < names.index("Configure Bloom")

    def test_configure_bloom_before_ensure_fork(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """'Configure Bloom' must precede 'Ensure rosdistro fork exists'."""
        names = self._step_names(steps)
        assert names.index("Configure Bloom") < names.index(
            "Ensure rosdistro fork exists"
        )


# ---------------------------------------------------------------------------
# Removed dead code
# ---------------------------------------------------------------------------


class TestRemovedDeadCode:
    """Dead/no-op steps must not appear in action.yml."""

    def _step_names(self, steps: list[dict[str, Any]]) -> list[str]:
        return [s.get("name", "") for s in steps]

    def test_no_validate_release_mode_inputs_step(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """The no-op 'Validate release-mode inputs' step is absent."""
        names = self._step_names(steps)
        assert "Validate release-mode inputs" not in names


# ---------------------------------------------------------------------------
# Credential normalization: GITHUB_ENV export
# ---------------------------------------------------------------------------


class TestCredentialNormalization:
    """Resolved credentials must be propagated via GITHUB_ENV in Configure Git."""

    def _configure_git_run(self, steps: list[dict[str, Any]]) -> str:
        for step in steps:
            if step.get("name") == "Configure Git":
                return step.get("run", "")
        raise AssertionError("Configure Git step not found")

    def test_bloom_oauth_token_written_to_github_env(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """Configure Git exports BLOOM_OAUTH_TOKEN to GITHUB_ENV."""
        run = self._configure_git_run(steps)
        assert "BLOOM_OAUTH_TOKEN=" in run
        assert "GITHUB_ENV" in run

    def test_bloom_github_user_written_to_github_env(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """Configure Git exports BLOOM_GITHUB_USER to GITHUB_ENV."""
        run = self._configure_git_run(steps)
        assert "BLOOM_GITHUB_USER=" in run
        assert "GITHUB_ENV" in run

    def test_github_env_export_uses_append_redirect(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """GITHUB_ENV is written with >> (append), not overwrite."""
        run = self._configure_git_run(steps)
        assert '>> "${GITHUB_ENV}"' in run or '>> "${GITHUB_ENV}"' in run


# ---------------------------------------------------------------------------
# No credential duplication in downstream release steps
# ---------------------------------------------------------------------------


class TestNoCredentialDuplication:
    """Configure Bloom and Ensure rosdistro fork must not re-declare input env vars."""

    def _get_step(self, steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
        for step in steps:
            if step.get("name") == name:
                return step
        raise AssertionError(f"Step '{name}' not found")

    def test_configure_bloom_has_no_input_env_block(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """'Configure Bloom' step has no 'env' block re-declaring input variables."""
        step = self._get_step(steps, "Configure Bloom")
        env = step.get("env", {})
        assert "INPUT_OAUTH_TOKEN" not in env
        assert "INPUT_GITHUB_USER" not in env

    def test_ensure_fork_has_no_input_env_block(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """'Ensure rosdistro fork exists' step has no 'env' block re-declaring inputs."""
        step = self._get_step(steps, "Ensure rosdistro fork exists")
        env = step.get("env", {})
        assert "INPUT_OAUTH_TOKEN" not in env
        assert "INPUT_GITHUB_USER" not in env

    def test_configure_bloom_reads_env_vars_directly(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """'Configure Bloom' shell reads BLOOM_OAUTH_TOKEN and BLOOM_GITHUB_USER."""
        step = self._get_step(steps, "Configure Bloom")
        run = step.get("run", "")
        assert "BLOOM_OAUTH_TOKEN" in run
        assert "BLOOM_GITHUB_USER" in run

    def test_ensure_fork_reads_env_vars_directly(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """'Ensure rosdistro fork exists' shell reads BLOOM_OAUTH_TOKEN and BLOOM_GITHUB_USER."""
        step = self._get_step(steps, "Ensure rosdistro fork exists")
        run = step.get("run", "")
        assert "BLOOM_OAUTH_TOKEN" in run
        assert "BLOOM_GITHUB_USER" in run


# ---------------------------------------------------------------------------
# Pixi environment wiring
# ---------------------------------------------------------------------------


class TestPixiEnvironmentWiring:
    """Runtime pixi invocations should target the 'action' environment."""

    def test_rosdep_init_uses_action_environment(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """'Initialize rosdep' step passes --environment action to pixi."""
        for step in steps:
            if step.get("name") == "Initialize rosdep":
                run = step.get("run", "")
                assert "--environment action" in run
                return
        raise AssertionError("'Initialize rosdep' step not found")

    def test_rosdep_update_uses_action_environment(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """'Update rosdep' step passes --environment action to pixi."""
        for step in steps:
            if step.get("name") == "Update rosdep":
                run = step.get("run", "")
                assert "--environment action" in run
                return
        raise AssertionError("'Update rosdep' step not found")

    def test_bloom_release_uses_action_environment(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """'Run Bloom Release' step passes --environment action to pixi."""
        for step in steps:
            if step.get("name") == "Run Bloom Release":
                run = step.get("run", "")
                assert "--environment action" in run
                return
        raise AssertionError("'Run Bloom Release' step not found")


# ---------------------------------------------------------------------------
# Conditional gating
# ---------------------------------------------------------------------------


class TestConditionalGating:
    """Release-only and prepare-only steps must be correctly gated."""

    def _get_step(self, steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
        for step in steps:
            if step.get("name") == name:
                return step
        raise AssertionError(f"Step '{name}' not found")

    def test_rosdep_init_gated_on_release_mode(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """'Initialize rosdep' only runs in release mode."""
        step = self._get_step(steps, "Initialize rosdep")
        assert step.get("if") == "inputs.mode == 'release'"

    def test_rosdep_update_gated_on_release_mode(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """'Update rosdep' only runs in release mode."""
        step = self._get_step(steps, "Update rosdep")
        assert step.get("if") == "inputs.mode == 'release'"

    def test_configure_bloom_gated_on_release_mode(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """'Configure Bloom' only runs in release mode."""
        step = self._get_step(steps, "Configure Bloom")
        assert step.get("if") == "inputs.mode == 'release'"

    def test_ensure_fork_gated_on_release_mode(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """'Ensure rosdistro fork exists' only runs in release mode."""
        step = self._get_step(steps, "Ensure rosdistro fork exists")
        assert step.get("if") == "inputs.mode == 'release'"

    def test_prepare_release_gated_on_prepare_mode(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """'Prepare Release PR' only runs in prepare mode."""
        step = self._get_step(steps, "Prepare Release PR")
        assert step.get("if") == "inputs.mode == 'prepare'"

    def test_bloom_release_gated_on_release_mode(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """'Run Bloom Release' only runs in release mode."""
        step = self._get_step(steps, "Run Bloom Release")
        assert step.get("if") == "inputs.mode == 'release'"


# ---------------------------------------------------------------------------
# Output declarations
# ---------------------------------------------------------------------------


class TestOutputDeclarations:
    """All declared outputs must reference the expected step IDs."""

    def test_pr_created_references_prepare_release_step(
        self, action: dict[str, Any]
    ) -> None:
        """pr-created output references prepare-release step."""
        value = action["outputs"]["pr-created"]["value"]
        assert "prepare-release" in value

    def test_released_references_bloom_release_step(
        self, action: dict[str, Any]
    ) -> None:
        """released output references bloom-release step."""
        value = action["outputs"]["released"]["value"]
        assert "bloom-release" in value

    def test_version_references_both_step_ids(self, action: dict[str, Any]) -> None:
        """version output references both prepare-release and bloom-release step IDs."""
        value = action["outputs"]["version"]["value"]
        assert "prepare-release" in value
        assert "bloom-release" in value

    def test_pr_url_references_both_step_ids(self, action: dict[str, Any]) -> None:
        """pr-url output references both prepare-release and bloom-release step IDs."""
        value = action["outputs"]["pr-url"]["value"]
        assert "prepare-release" in value
        assert "bloom-release" in value


# ---------------------------------------------------------------------------
# Token handling
# ---------------------------------------------------------------------------


class TestTokenHandling:
    """Token resolution must follow env-first, input-fallback precedence."""

    def _configure_git_run(self, steps: list[dict[str, Any]]) -> str:
        for step in steps:
            if step.get("name") == "Configure Git":
                return step.get("run", "")
        raise AssertionError("Configure Git step not found")

    def test_configure_git_env_declares_input_oauth_token(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """Configure Git env block exposes INPUT_OAUTH_TOKEN from the action input."""
        for step in steps:
            if step.get("name") == "Configure Git":
                env = step.get("env", {})
                assert "INPUT_OAUTH_TOKEN" in env
                return
        raise AssertionError("Configure Git step not found")

    def test_configure_git_env_declares_input_github_user(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """Configure Git env block exposes INPUT_GITHUB_USER from the action input."""
        for step in steps:
            if step.get("name") == "Configure Git":
                env = step.get("env", {})
                assert "INPUT_GITHUB_USER" in env
                return
        raise AssertionError("Configure Git step not found")

    def test_token_env_first_input_fallback_pattern_present(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """Configure Git uses env-first input-fallback pattern for token resolution."""
        run = self._configure_git_run(steps)
        # Pattern: "${BLOOM_OAUTH_TOKEN:-${INPUT_OAUTH_TOKEN:-}}"
        assert re.search(
            r"BLOOM_OAUTH_TOKEN:-\$\{INPUT_OAUTH_TOKEN",
            run,
        ), "env-first fallback pattern for BLOOM_OAUTH_TOKEN not found"

    def test_bloom_release_exports_gh_token_from_env(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """'Run Bloom Release' exports GH_TOKEN from BLOOM_OAUTH_TOKEN (no input lookup)."""
        for step in steps:
            if step.get("name") == "Run Bloom Release":
                run = step.get("run", "")
                # Should use the normalized env var, not re-resolve from input
                assert 'GH_TOKEN="${BLOOM_OAUTH_TOKEN}"' in run
                # Must NOT re-introduce the input fallback in this step
                assert "INPUT_OAUTH_TOKEN" not in run
                return
        raise AssertionError("'Run Bloom Release' step not found")
