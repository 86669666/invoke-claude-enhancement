"""Configuration loader with multi-level hierarchy and network topology detection."""

import os
import socket
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # type: ignore


class ConfigLoader:
    """
    Load configuration from multiple sources with priority:
    1. Environment variables (CLAUDE_*)
    2. Project config (./.claude/config.toml)
    3. User config (~/.config/claude/config.toml)
    4. System config (/etc/claude/config.toml)
    5. Hard-coded defaults
    """

    DEFAULT_CONFIG = {
        "claude": {
            "bin_path": "/root/.hermes/node/bin/claude",
            "default_model": "claude-sonnet-5",
            "default_timeout": 900,
            "max_retries": 3,
        },
        "proxy": {
            "url": "http://10.10.10.111:4100",
            "topology": "auto",  # auto | fspve_internal | public | localhost
        },
        "retry": {
            "enabled": True,
            "max_attempts": 5,
            "initial_delay": 2.0,
            "max_delay": 60.0,
        },
    }

    TOPOLOGY_BASE_URLS = {
        "fspve_internal": "http://10.10.10.111:4100",
        "localhost": "http://127.0.0.1:4100",
        "public": "https://llm.bai.one",
    }

    def __init__(self, project_dir: Optional[str] = None):
        """
        Initialize config loader.

        Args:
            project_dir: Optional project directory for .claude/config.toml lookup
        """
        self.project_dir = Path(project_dir) if project_dir else Path.cwd()
        self._config: Optional[Dict[str, Any]] = None

    def load(self) -> Dict[str, Any]:
        """
        Load and merge configuration from all sources.

        Returns:
            Merged configuration dictionary
        """
        if self._config is not None:
            return self._config

        # Start with defaults
        config = self._deep_copy(self.DEFAULT_CONFIG)

        # Layer 4: System config
        system_config_path = Path("/etc/claude/config.toml")
        if system_config_path.exists():
            config = self._merge_config(config, self._load_toml(system_config_path))

        # Layer 3: User config
        user_config_path = Path.home() / ".config" / "claude" / "config.toml"
        if user_config_path.exists():
            config = self._merge_config(config, self._load_toml(user_config_path))

        # Layer 2: Project config
        project_config_path = self.project_dir / ".claude" / "config.toml"
        if project_config_path.exists():
            config = self._merge_config(config, self._load_toml(project_config_path))

        # Layer 1: Environment variables (highest priority)
        config = self._apply_env_overrides(config)

        # Auto-detect network topology if needed (but preserve explicit URL override)
        explicit_url = os.getenv("CLAUDE_PROXY_URL") is not None
        if config["proxy"]["topology"] == "auto" and not explicit_url:
            config["proxy"]["topology"] = self._detect_topology()

        # Resolve topology to actual URL (only if URL wasn't explicitly set)
        if not explicit_url:
            topology = config["proxy"]["topology"]
            if topology in self.TOPOLOGY_BASE_URLS:
                config["proxy"]["url"] = self.TOPOLOGY_BASE_URLS[topology]

        self._config = config
        return config

    def _load_toml(self, path: Path) -> Dict[str, Any]:
        """Load TOML file."""
        with open(path, "rb") as f:
            return tomllib.load(f)

    def _deep_copy(self, obj: Any) -> Any:
        """Deep copy a nested dict."""
        if isinstance(obj, dict):
            return {k: self._deep_copy(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._deep_copy(item) for item in obj]
        else:
            return obj

    def _merge_config(
        self, base: Dict[str, Any], override: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Deep merge two config dicts.

        Args:
            base: Base configuration
            override: Override configuration (higher priority)

        Returns:
            Merged configuration
        """
        result = self._deep_copy(base)

        for key, value in override.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = self._merge_config(result[key], value)
            else:
                result[key] = self._deep_copy(value)

        return result

    def _apply_env_overrides(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply environment variable overrides.

        Supported variables:
        - CLAUDE_BIN_PATH
        - CLAUDE_DEFAULT_MODEL
        - CLAUDE_DEFAULT_TIMEOUT
        - CLAUDE_MAX_RETRIES
        - CLAUDE_PROXY_URL
        - CLAUDE_PROXY_TOPOLOGY
        - CLAUDE_RETRY_ENABLED
        - CLAUDE_RETRY_MAX_ATTEMPTS
        """
        env_mapping = {
            "CLAUDE_BIN_PATH": ("claude", "bin_path"),
            "CLAUDE_DEFAULT_MODEL": ("claude", "default_model"),
            "CLAUDE_DEFAULT_TIMEOUT": ("claude", "default_timeout", int),
            "CLAUDE_MAX_RETRIES": ("claude", "max_retries", int),
            "CLAUDE_PROXY_URL": ("proxy", "url"),
            "CLAUDE_PROXY_TOPOLOGY": ("proxy", "topology"),
            "CLAUDE_RETRY_ENABLED": (
                "retry",
                "enabled",
                lambda v: v.lower() in ("true", "1", "yes"),
            ),
            "CLAUDE_RETRY_MAX_ATTEMPTS": ("retry", "max_attempts", int),
        }

        for env_var, mapping in env_mapping.items():
            value = os.getenv(env_var)
            if value is not None:
                section = mapping[0]
                key = mapping[1]
                converter = mapping[2] if len(mapping) > 2 else str

                try:
                    config[section][key] = converter(value)
                except (ValueError, KeyError):
                    pass  # Skip invalid values

        return config

    def _detect_topology(self) -> str:
        """
        Auto-detect network topology.

        Returns:
            One of: 'fspve_internal', 'localhost', 'public'
        """
        # Try FSPVE internal network
        if self._can_connect("10.10.10.111", 4100, timeout=1):
            return "fspve_internal"

        # Try localhost
        if self._can_connect("127.0.0.1", 4100, timeout=1):
            return "localhost"

        # Fallback to public
        return "public"

    def _can_connect(self, host: str, port: int, timeout: float = 1.0) -> bool:
        """
        Test if a host:port is reachable.

        Args:
            host: Hostname or IP
            port: Port number
            timeout: Connection timeout in seconds

        Returns:
            True if connection succeeds
        """
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except (socket.error, OSError):
            return False

    def get(self, section: str, key: str, default: Any = None) -> Any:
        """
        Get a configuration value.

        Args:
            section: Section name (e.g., 'claude', 'proxy')
            key: Key name
            default: Default value if not found

        Returns:
            Configuration value
        """
        config = self.load()
        return config.get(section, {}).get(key, default)
