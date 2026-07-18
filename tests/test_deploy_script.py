"""Integration tests for scripts/deploy-invoke-claude-native.sh."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "deploy-invoke-claude-native.sh"
MODULE_FILES = ("client.py", "bridge.py", "native_tools.py", "config.py", "retry.py")
DEFAULT_RUNTIME_DIR = "/usr/local/lib/hermes-agent/native/invoke_claude"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _make_repo_copy(tmp_path: Path, *, break_bridge: bool = False) -> Path:
    repo_dir = tmp_path / "repo"
    source_dir = repo_dir / "src" / "python"
    source_dir.mkdir(parents=True)

    for module_name in MODULE_FILES:
        content = (REPO_ROOT / "src" / "python" / module_name).read_text(encoding="utf-8")
        if break_bridge and module_name == "bridge.py":
            content = "def broken(:\n"
        (source_dir / module_name).write_text(content, encoding="utf-8")

    return repo_dir


def _worker_source(*, use_runtime_env_override: bool, hardcoded_runtime: str) -> str:
    if use_runtime_env_override:
        runtime_expr = (
            f'os.getenv("INVOKE_CLAUDE_NATIVE_RUNTIME_DIR", "{DEFAULT_RUNTIME_DIR}")'
        )
    else:
        runtime_expr = f'"{hardcoded_runtime}"'

    return textwrap.dedent(
        f"""
        import os
        from pathlib import Path

        class ClaudePluginResolver:
            pass

        resolved_api_key = "test-key"

        def _load_native_bridge():
            bridge_root = Path({runtime_expr})
            return bridge_root / "bridge.py"

        def invoke_claude(prompt, workdir, api_key=resolved_api_key, config_path=None):
            return {{
                "bridge": str(_load_native_bridge()),
                "prompt": prompt,
                "workdir": workdir,
                "config_path": config_path,
            }}
        """
    ).strip() + "\n"


def _create_fake_venv(
    tmp_path: Path,
    *,
    worker_source: str,
    preinstall_deps: bool,
) -> tuple[Path, Path]:
    venv_dir = tmp_path / "hermes" / "venv"
    bin_dir = venv_dir / "bin"
    site_packages = venv_dir / "lib" / "python3.11" / "site-packages"
    tools_dir = site_packages / "tools"
    deps_dir = tmp_path / "fake_deps"
    pip_log = tmp_path / "pip.log"

    bin_dir.mkdir(parents=True)
    tools_dir.mkdir(parents=True)
    deps_dir.mkdir(parents=True)

    (tools_dir / "__init__.py").write_text("", encoding="utf-8")
    (tools_dir / "claude_worker_lib.py").write_text(worker_source, encoding="utf-8")

    def write_dep(name: str, content: str) -> None:
        (deps_dir / f"{name}.py").write_text(content, encoding="utf-8")

    if preinstall_deps:
        write_dep(
            "httpx",
            textwrap.dedent(
                """
                class HTTPError(Exception):
                    pass


                class HTTPStatusError(HTTPError):
                    def __init__(self, response=None):
                        self.response = response


                class Client:
                    def __init__(self, *args, **kwargs):
                        pass
                """
            ).strip()
            + "\n",
        )
        write_dep(
            "structlog",
            textwrap.dedent(
                """
                class _Logger:
                    def info(self, *args, **kwargs):
                        pass

                    def error(self, *args, **kwargs):
                        pass

                    def warning(self, *args, **kwargs):
                        pass


                def get_logger(*args, **kwargs):
                    return _Logger()
                """
            ).strip()
            + "\n",
        )

    real_python = sys.executable
    python_wrapper = textwrap.dedent(
        f"""#!/bin/sh
        export PYTHONPATH="{deps_dir}:{site_packages}${{PYTHONPATH:+:$PYTHONPATH}}"
        exec "{real_python}" -S "$@"
        """
    )
    pip_wrapper = textwrap.dedent(
        f"""#!/usr/bin/env python3
from pathlib import Path
import sys

deps_dir = Path(r"{deps_dir}")
log_path = Path(r"{pip_log}")
deps_dir.mkdir(parents=True, exist_ok=True)
log_path.parent.mkdir(parents=True, exist_ok=True)
with log_path.open("a", encoding="utf-8") as fh:
    fh.write(" ".join(sys.argv[1:]) + "\\n")

stub_map = {{
    "httpx": '''class HTTPError(Exception):\\n    pass\\n\\nclass HTTPStatusError(HTTPError):\\n    def __init__(self, response=None):\\n        self.response = response\\n\\nclass Client:\\n    def __init__(self, *args, **kwargs):\\n        pass\\n''',
    "structlog": '''class _Logger:\\n    def info(self, *args, **kwargs):\\n        pass\\n\\n    def error(self, *args, **kwargs):\\n        pass\\n\\n    def warning(self, *args, **kwargs):\\n        pass\\n\\ndef get_logger(*args, **kwargs):\\n    return _Logger()\\n''',
    "tomli": "",
}}

for package in sys.argv[1:]:
    if package in stub_map:
        (deps_dir / f"{{package}}.py").write_text(stub_map[package], encoding="utf-8")
        """
    )

    _write_executable(bin_dir / "python", python_wrapper)
    _write_executable(bin_dir / "pip", pip_wrapper)

    return venv_dir, pip_log


def _run_deploy(
    tmp_path: Path,
    *,
    worker_source: str,
    preinstall_deps: bool,
    runtime_dir: Path,
    repo_dir: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    repo_dir = repo_dir or _make_repo_copy(tmp_path)
    venv_dir, _ = _create_fake_venv(
        tmp_path,
        worker_source=worker_source,
        preinstall_deps=preinstall_deps,
    )

    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)

    return subprocess.run(
        [
            "bash",
            str(SCRIPT_PATH),
            "--hermes-venv",
            str(venv_dir),
            "--repo-path",
            str(repo_dir),
            "--runtime-dir",
            str(runtime_dir),
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )


def test_default_runtime_dir_is_fixed_constant() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert f'readonly DEFAULT_RUNTIME_DIR="{DEFAULT_RUNTIME_DIR}"' in source
    assert 'RUNTIME_DIR="$HERMES_HOME/native/invoke_claude"' not in source


def test_deploy_installs_only_missing_dependencies_and_reports_parent_env_contract(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "chosen-runtime"
    result = _run_deploy(
        tmp_path,
        worker_source=_worker_source(
            use_runtime_env_override=True,
            hardcoded_runtime=DEFAULT_RUNTIME_DIR,
        ),
        preinstall_deps=False,
        runtime_dir=runtime_dir,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert (runtime_dir / "bridge.py").is_file()
    pip_log = (tmp_path / "pip.log").read_text(encoding="utf-8")
    assert "install -q httpx structlog" in pip_log
    assert "INVOKE_CLAUDE_NATIVE=1 必须保留在父 profile 的 .env 中" in result.stdout
    assert "CLAUDE_API_KEY / CLAUDE_PROXY_URL / CLAUDE_MODEL" in result.stdout


def test_deploy_skips_pip_when_dependencies_already_exist(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    result = _run_deploy(
        tmp_path,
        worker_source=_worker_source(
            use_runtime_env_override=True,
            hardcoded_runtime=DEFAULT_RUNTIME_DIR,
        ),
        preinstall_deps=True,
        runtime_dir=runtime_dir,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "依赖已满足；跳过安装" in result.stdout
    assert not (tmp_path / "pip.log").exists()


def test_deploy_rejects_old_worker_marker_set_without_touching_worker(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    worker_source = textwrap.dedent(
        """
        def invoke_claude(prompt, workdir):
            return {"prompt": prompt, "workdir": workdir}
        """
    ).strip() + "\n"
    repo_dir = _make_repo_copy(tmp_path)
    venv_dir, _ = _create_fake_venv(
        tmp_path,
        worker_source=worker_source,
        preinstall_deps=True,
    )
    worker_path = (
        venv_dir / "lib" / "python3.11" / "site-packages" / "tools" / "claude_worker_lib.py"
    )
    original_worker = worker_path.read_text(encoding="utf-8")

    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [
            "bash",
            str(SCRIPT_PATH),
            "--hermes-venv",
            str(venv_dir),
            "--repo-path",
            str(repo_dir),
            "--runtime-dir",
            str(runtime_dir),
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "缺少安全的 child-only 原生集成标记" in result.stderr
    assert worker_path.read_text(encoding="utf-8") == original_worker
    assert not runtime_dir.exists()


def test_deploy_fails_closed_when_worker_points_into_git_worktree(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    result = _run_deploy(
        tmp_path,
        worker_source=_worker_source(
            use_runtime_env_override=False,
            hardcoded_runtime="/opt/workspace/git/invoke-claude-enhancement/src/python",
        ),
        preinstall_deps=True,
        runtime_dir=runtime_dir,
    )

    assert result.returncode != 0
    assert "仍指向 git worktree 路径" in result.stderr
    assert not runtime_dir.exists()


def test_deploy_rolls_back_runtime_when_smoke_verification_fails(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "sentinel.txt").write_text("original", encoding="utf-8")

    broken_repo = _make_repo_copy(tmp_path, break_bridge=True)
    result = _run_deploy(
        tmp_path,
        worker_source=_worker_source(
            use_runtime_env_override=True,
            hardcoded_runtime=DEFAULT_RUNTIME_DIR,
        ),
        preinstall_deps=True,
        runtime_dir=runtime_dir,
        repo_dir=broken_repo,
    )

    assert result.returncode != 0
    assert (runtime_dir / "sentinel.txt").read_text(encoding="utf-8") == "original"
    assert not (runtime_dir / "bridge.py").exists()
    assert "已回滚运行目录" in result.stderr
