#!/bin/bash
# deploy-invoke-claude-native.sh
# Install native invoke_claude bridge sources into Hermes without rewriting
# tools/claude_worker_lib.py.

set -eEuo pipefail

readonly MODULE_FILES=(
    "client.py"
    "bridge.py"
    "native_tools.py"
    "config.py"
    "retry.py"
)
readonly DEFAULT_RUNTIME_DIR="/usr/local/lib/hermes-agent/native/invoke_claude"

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
DEFAULT_REPO_PATH=$(cd "$SCRIPT_DIR/.." && pwd)
TRANSACTION_ACTIVE=0
STAGE_DIR=""
INSTALL_TARGET_DIR=""
INSTALL_BACKUP_DIR=""
HAD_PRIOR_TARGET=0

rollback_install_transaction() {
    if [[ "${TRANSACTION_ACTIVE:-0}" -ne 1 ]]; then
        return 0
    fi

    if [[ -n "${INSTALL_TARGET_DIR:-}" && -e "${INSTALL_TARGET_DIR:-}" ]]; then
        rm -rf "$INSTALL_TARGET_DIR"
    fi

    if [[ "${HAD_PRIOR_TARGET:-0}" -eq 1 && -n "${INSTALL_BACKUP_DIR:-}" && -e "${INSTALL_BACKUP_DIR:-}" ]]; then
        mv "$INSTALL_BACKUP_DIR" "$INSTALL_TARGET_DIR"
        echo "  已回滚运行目录 -> $INSTALL_TARGET_DIR" >&2
    fi

    TRANSACTION_ACTIVE=0
}

on_error() {
    local exit_code=$?
    trap - ERR
    rollback_install_transaction || true
    exit "$exit_code"
}

cleanup() {
    if [[ -n "${STAGE_DIR:-}" && -d "${STAGE_DIR:-}" ]]; then
        rm -rf "$STAGE_DIR"
    fi
}
trap on_error ERR
trap cleanup EXIT

detect_hermes_venv() {
    local candidates=(
        "/usr/local/lib/hermes-agent/venv"
        "$HOME/.hermes/venv"
        "/opt/hermes-agent/venv"
    )

    local path
    for path in "${candidates[@]}"; do
        if [[ -f "$path/bin/python" ]]; then
            echo "$path"
            return 0
        fi
    done

    echo "[ERROR] 无法自动探测 Hermes venv 路径，请用 --hermes-venv 手动指定" >&2
    return 1
}

detect_claude_worker_lib() {
    local venv_path="$1"
    local candidates=(
        "$venv_path/lib/python3.13/site-packages/tools/claude_worker_lib.py"
        "$venv_path/lib/python3.12/site-packages/tools/claude_worker_lib.py"
        "$venv_path/lib/python3.11/site-packages/tools/claude_worker_lib.py"
        "$venv_path/lib/python3.10/site-packages/tools/claude_worker_lib.py"
        "$venv_path/lib/python3.9/site-packages/tools/claude_worker_lib.py"
    )

    local path
    for path in "${candidates[@]}"; do
        if [[ -f "$path" ]]; then
            echo "$path"
            return 0
        fi
    done

    echo "[ERROR] 无法在 venv 中找到 tools/claude_worker_lib.py" >&2
    return 1
}

require_source_files() {
    local src_dir="$1"
    local file
    for file in "${MODULE_FILES[@]}"; do
        if [[ ! -f "$src_dir/$file" ]]; then
            echo "[ERROR] 缺少源文件: $src_dir/$file" >&2
            return 1
        fi
    done
}

verify_worker_markers() {
    local python_bin="$1"
    local worker_path="$2"
    local chosen_runtime_dir="$3"
    local default_runtime_dir="$4"
    local repo_path="$5"

    "$python_bin" - "$worker_path" "$chosen_runtime_dir" "$default_runtime_dir" "$repo_path" <<'PY'
from pathlib import Path
import re
import sys

worker_path = Path(sys.argv[1])
chosen_runtime_dir = str(Path(sys.argv[2]))
default_runtime_dir = str(Path(sys.argv[3]))
repo_path = str(Path(sys.argv[4]))
source = worker_path.read_text(encoding="utf-8")

checks = [
    ("ClaudePluginResolver", "ClaudePluginResolver" in source),
    ("_load_native_bridge", "_load_native_bridge" in source),
    ("api_key=resolved_api_key", "api_key=resolved_api_key" in source),
    (
        "invoke_claude(... config_path=...)",
        re.search(r"def\s+invoke_claude\([^)]*config_path", source, re.DOTALL) is not None,
    ),
]

bad_worktree_patterns = [
    repo_path,
    "/invoke-claude-enhancement/src/python",
    "/invoke-claude-enhancement",
]
worktree_hit = next((pattern for pattern in bad_worktree_patterns if pattern and pattern in source), None)
if worktree_hit:
    print(
        "[ERROR] 现有 Hermes worker 仍指向 git worktree 路径，拒绝部署: "
        + worktree_hit,
        file=sys.stderr,
    )
    sys.exit(1)

runtime_override_patterns = [
    re.compile(
        r'getenv\(\s*["\'][^"\']*(?:RUNTIME|BRIDGE)[^"\']*(?:DIR|PATH)[^"\']*["\']\s*,\s*["\']'
        + re.escape(default_runtime_dir)
        + r'["\']'
    ),
    re.compile(
        r'environ\.get\(\s*["\'][^"\']*(?:RUNTIME|BRIDGE)[^"\']*(?:DIR|PATH)[^"\']*["\']\s*,\s*["\']'
        + re.escape(default_runtime_dir)
        + r'["\']'
    ),
]
runtime_path_ok = any(pattern.search(source) for pattern in runtime_override_patterns)
runtime_path_ok = runtime_path_ok or chosen_runtime_dir in source
runtime_path_ok = runtime_path_ok or (
    chosen_runtime_dir == default_runtime_dir and default_runtime_dir in source
)

checks.append(
    (
        "native bridge runtime path",
        runtime_path_ok,
    )
)

missing = [label for label, ok in checks if not ok]
if missing:
    print(
        "[ERROR] 现有 Hermes worker 缺少安全的 child-only 原生集成标记: "
        + ", ".join(missing),
        file=sys.stderr,
    )
    print(
        "[ERROR] 不会安装任何 bridge 文件。请先升级 Hermes worker 或安装包含 "
        "ClaudePluginResolver/_load_native_bridge 且指向稳定 runtime bridge 的 invoke_claude 插件版本。",
        file=sys.stderr,
    )
    sys.exit(1)
PY
}

install_runtime_dir() {
    local src_dir="$1"
    local target_dir="$2"

    local parent_dir
    parent_dir=$(dirname "$target_dir")
    install -d -m 0755 "$parent_dir"

    STAGE_DIR=$(mktemp -d "$parent_dir/.invoke_claude.stage.XXXXXX")

    local file
    for file in "${MODULE_FILES[@]}"; do
        install -m 0644 "$src_dir/$file" "$STAGE_DIR/$file"
    done

    local backup_dir=""
    if [[ -e "$target_dir" ]]; then
        backup_dir="${target_dir}.bak.$(date +%Y%m%d%H%M%S)"
        mv "$target_dir" "$backup_dir"
        echo "  已备份现有运行目录 -> $backup_dir"
        HAD_PRIOR_TARGET=1
    else
        HAD_PRIOR_TARGET=0
    fi

    if ! mv "$STAGE_DIR" "$target_dir"; then
        rm -rf "$STAGE_DIR"
        STAGE_DIR=""
        if [[ -n "$backup_dir" && -e "$backup_dir" && ! -e "$target_dir" ]]; then
            mv "$backup_dir" "$target_dir"
        fi
        echo "[ERROR] 安装运行目录失败，已回滚" >&2
        return 1
    fi

    INSTALL_TARGET_DIR="$target_dir"
    INSTALL_BACKUP_DIR="$backup_dir"
    TRANSACTION_ACTIVE=1
    STAGE_DIR=""
}

commit_install_transaction() {
    TRANSACTION_ACTIVE=0
    INSTALL_TARGET_DIR=""
    INSTALL_BACKUP_DIR=""
    HAD_PRIOR_TARGET=0
}

ensure_python_dependencies() {
    local python_bin="$1"
    local pip_bin="$2"

    local missing=()
    local package
    for package in httpx structlog; do
        if "$python_bin" -c "import $package" >/dev/null 2>&1; then
            echo "  已存在依赖: $package"
        else
            missing+=("$package")
        fi
    done

    if "$python_bin" -c "import sys; raise SystemExit(0 if sys.version_info < (3, 11) else 1)"; then
        if "$python_bin" -c "import tomli" >/dev/null 2>&1; then
            echo "  已存在依赖: tomli"
        else
            missing+=("tomli")
        fi
    fi

    if [[ ${#missing[@]} -eq 0 ]]; then
        echo "  依赖已满足；跳过安装"
        return 0
    fi

    echo "  缺失依赖，执行安装: ${missing[*]}"
    "$pip_bin" install -q "${missing[@]}"
}

smoke_verify() {
    local python_bin="$1"
    local runtime_dir="$2"
    local worker_path="$3"

    local runtime_files=()
    local file
    for file in "${MODULE_FILES[@]}"; do
        runtime_files+=("$runtime_dir/$file")
    done

    "$python_bin" - "$runtime_dir" "$worker_path" "${runtime_files[@]}" <<'PY'
import importlib
import py_compile
import sys
from pathlib import Path

runtime_dir = Path(sys.argv[1])
worker_path = Path(sys.argv[2])
module_paths = [Path(path) for path in sys.argv[3:]]

for path in [worker_path, *module_paths]:
    py_compile.compile(str(path), doraise=True)

sys.path.insert(0, str(runtime_dir))
for module_name in ("config", "retry", "client", "native_tools", "bridge"):
    importlib.import_module(module_name)

worker_module = importlib.import_module("tools.claude_worker_lib")
if not hasattr(worker_module, "invoke_claude"):
    raise RuntimeError("tools.claude_worker_lib.invoke_claude 不存在")
PY
}

HERMES_VENV=""
REPO_PATH="$DEFAULT_REPO_PATH"
RUNTIME_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --hermes-venv)
            HERMES_VENV="$2"
            shift 2
            ;;
        --repo-path)
            REPO_PATH="$2"
            shift 2
            ;;
        --runtime-dir)
            RUNTIME_DIR="$2"
            shift 2
            ;;
        *)
            echo "未知参数: $1" >&2
            echo "用法: $0 [--hermes-venv /path/to/venv] [--repo-path /path/to/repo] [--runtime-dir /path/to/native/invoke_claude]" >&2
            exit 1
            ;;
    esac
done

[[ -z "$HERMES_VENV" ]] && HERMES_VENV=$(detect_hermes_venv)
if [[ ! -f "$HERMES_VENV/bin/python" ]]; then
    echo "[ERROR] 无效的 Hermes venv: $HERMES_VENV" >&2
    exit 1
fi

REPO_PATH=$(cd "$REPO_PATH" && pwd)
SOURCE_DIR="$REPO_PATH/src/python"
require_source_files "$SOURCE_DIR"

HERMES_HOME=$(cd "$HERMES_VENV/.." && pwd)
if [[ -z "$RUNTIME_DIR" ]]; then
    RUNTIME_DIR="$DEFAULT_RUNTIME_DIR"
fi
PLUGIN_ENV_PATH="$HERMES_HOME/claude-plugin.env"

CLAUDE_WORKER_LIB=$(detect_claude_worker_lib "$HERMES_VENV")
PYTHON_BIN="$HERMES_VENV/bin/python"
PIP_BIN="$HERMES_VENV/bin/pip"

echo "=========================================="
echo "invoke_claude 原生 bridge 安装"
echo "=========================================="
echo "Hermes home: $HERMES_HOME"
echo "Hermes venv: $HERMES_VENV"
echo "Repo 路径:   $REPO_PATH"
echo "Worker 路径: $CLAUDE_WORKER_LIB"
echo "安装目录:   $RUNTIME_DIR"
echo

echo "[1/6] 安装 Python 依赖..."
ensure_python_dependencies "$PYTHON_BIN" "$PIP_BIN"

echo "[2/6] 校验 Hermes worker 已具备安全集成标记..."
verify_worker_markers "$PYTHON_BIN" "$CLAUDE_WORKER_LIB" "$RUNTIME_DIR" "$DEFAULT_RUNTIME_DIR" "$REPO_PATH"
echo "  标记校验通过；不会修改 tools/claude_worker_lib.py"

echo "[3/6] 原子安装原生 bridge 源文件..."
install_runtime_dir "$SOURCE_DIR" "$RUNTIME_DIR"
echo "  已安装: ${MODULE_FILES[*]}"

echo "[4/6] 检查 child-only 配置文件路径..."
if [[ -f "$PLUGIN_ENV_PATH" ]]; then
    chmod 600 "$PLUGIN_ENV_PATH"
    echo "  已确保 $PLUGIN_ENV_PATH 权限为 0600"
    echo "  该文件只应包含: CLAUDE_API_KEY / CLAUDE_PROXY_URL / CLAUDE_MODEL"
else
    echo "  未创建 child-only 配置文件。请仅将 Claude 子进程凭据写入:"
    echo "    $PLUGIN_ENV_PATH"
    echo "  允许的键: CLAUDE_API_KEY / CLAUDE_PROXY_URL / CLAUDE_MODEL"
    echo "  INVOKE_CLAUDE_NATIVE=1 必须保留在父 profile 的 .env 中；不要写入 $PLUGIN_ENV_PATH"
fi

echo "[5/6] 运行语法/编译/导入 smoke 验证..."
smoke_verify "$PYTHON_BIN" "$RUNTIME_DIR" "$CLAUDE_WORKER_LIB"
commit_install_transaction
echo "  smoke 验证通过"

echo "[6/6] 安装完成"
echo
echo "后续操作："
echo "  1. 如未配置，创建 $PLUGIN_ENV_PATH（权限 0600）"
echo "  2. 在父 profile 的 .env 中保留 INVOKE_CLAUDE_NATIVE=1"
echo "  3. 仅在 $PLUGIN_ENV_PATH 中设置 CLAUDE_API_KEY / CLAUDE_PROXY_URL / CLAUDE_MODEL"
echo "  4. 无需重写 worker，也无需由本脚本重启服务"
echo
echo "快速验证："
echo "  $PYTHON_BIN -c 'from tools.claude_worker_lib import invoke_claude; print(callable(invoke_claude))'"
