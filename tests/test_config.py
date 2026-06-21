"""Tests for ConfigLoader."""

import os
import tempfile
from pathlib import Path

import pytest

import sys
from pathlib import Path

# Add src/python to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "python"))

from config import ConfigLoader


class TestConfigLoader:
    """Test suite for ConfigLoader."""

    def test_default_config(self):
        """Test that default config loads correctly."""
        loader = ConfigLoader()
        config = loader.load()

        assert config["claude"]["default_model"] == "claude-opus-4"
        assert config["claude"]["default_timeout"] == 900
        assert config["proxy"]["url"] in ConfigLoader.TOPOLOGY_BASE_URLS.values()
        assert config["retry"]["enabled"] is True

    def test_env_override(self, monkeypatch):
        """Test that environment variables override defaults."""
        monkeypatch.setenv("CLAUDE_DEFAULT_MODEL", "claude-sonnet-4")
        monkeypatch.setenv("CLAUDE_DEFAULT_TIMEOUT", "600")
        monkeypatch.setenv("CLAUDE_PROXY_URL", "http://custom:8080")

        loader = ConfigLoader()
        config = loader.load()

        assert config["claude"]["default_model"] == "claude-sonnet-4"
        assert config["claude"]["default_timeout"] == 600
        assert config["proxy"]["url"] == "http://custom:8080"

    def test_toml_config_load(self, tmp_path):
        """Test loading from TOML file."""
        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"

        config_file.write_text("""
[claude]
default_model = "claude-haiku"
default_timeout = 300

[proxy]
url = "http://test:9000"
topology = "localhost"
""")

        loader = ConfigLoader(project_dir=str(tmp_path))
        config = loader.load()

        assert config["claude"]["default_model"] == "claude-haiku"
        assert config["claude"]["default_timeout"] == 300
        assert config["proxy"]["url"] == "http://127.0.0.1:4100"  # topology resolved

    def test_config_merge_priority(self, tmp_path, monkeypatch):
        """Test that config sources merge with correct priority."""
        # Project config
        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text("""
[claude]
default_model = "claude-opus-4"
default_timeout = 300
""")

        # Environment variable (highest priority)
        monkeypatch.setenv("CLAUDE_DEFAULT_TIMEOUT", "600")

        loader = ConfigLoader(project_dir=str(tmp_path))
        config = loader.load()

        # Model from TOML, timeout from env
        assert config["claude"]["default_model"] == "claude-opus-4"
        assert config["claude"]["default_timeout"] == 600

    def test_topology_detection(self, mocker):
        """Test network topology auto-detection."""
        # Mock _can_connect to simulate FSPVE internal network
        mocker.patch.object(
            ConfigLoader,
            "_can_connect",
            side_effect=lambda host, port, timeout: host == "10.10.10.111"
        )

        loader = ConfigLoader()
        config = loader.load()

        assert config["proxy"]["topology"] == "fspve_internal"
        assert config["proxy"]["url"] == "http://10.10.10.111:4100"

    def test_topology_fallback_to_public(self, mocker):
        """Test topology falls back to public when no local connection."""
        # Mock _can_connect to always fail
        mocker.patch.object(ConfigLoader, "_can_connect", return_value=False)

        loader = ConfigLoader()
        config = loader.load()

        assert config["proxy"]["topology"] == "public"
        assert config["proxy"]["url"] == "https://llm.bai.one"

    def test_get_method(self):
        """Test get() convenience method."""
        loader = ConfigLoader()

        assert loader.get("claude", "default_model") == "claude-opus-4"
        assert loader.get("proxy", "nonexistent", "fallback") == "fallback"
        assert loader.get("nonexistent_section", "key", 42) == 42
