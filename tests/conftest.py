"""Pytest fixtures for release-ros-robot tests."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Generator

import pytest


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for testing."""
    tmpdir = tempfile.mkdtemp()
    try:
        yield Path(tmpdir)
    finally:
        shutil.rmtree(tmpdir)


@pytest.fixture
def temp_git_repo(temp_dir: Path) -> Path:
    """Create a temporary git repository with initial commit."""
    os.chdir(temp_dir)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )

    # Create initial commit
    (temp_dir / "README.md").write_text("# Test Repository\n")
    subprocess.run(["git", "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"], check=True, capture_output=True
    )

    return temp_dir


@pytest.fixture
def package_xml_content() -> str:
    """Return sample package.xml content."""
    return """<?xml version="1.0"?>
<?xml-model href="http://download.ros.org/schema/package_format3.xsd" schematypens="http://www.w3.org/2001/XMLSchema"?>
<package format="3">
  <name>test_package</name>
  <version>1.2.3</version>
  <description>Test package for release-ros-robot</description>
  <maintainer email="test@example.com">Test Maintainer</maintainer>
  <license>Apache-2.0</license>

  <buildtool_depend>ament_cmake</buildtool_depend>

  <export>
    <build_type>ament_cmake</build_type>
  </export>
</package>
"""
