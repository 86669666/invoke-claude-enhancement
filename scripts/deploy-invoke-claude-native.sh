#!/bin/bash
# deploy-invoke-claude-native.sh
# invoke_claude 原生 Python 实现一键部署脚本
# 用法: bash deploy-invoke-claude-native.sh [--hermes-venv /path/to/venv] [--repo-path /path/to/repo]

set -euo pipefail

# ================== 配置自动探测 ==================
detect_hermes_venv() {
    local candidates=(
        "/usr/local/lib/hermes-agent/venv"           # systemd 标准路径
        "$HOME/.hermes/venv"                         # profile 标准路径
        "/opt/hermes-agent/venv"                     # 备用安装路径
    )
    
    for path in "${candidates[@]}"; do
        if [[ -d "$path" && -f "$path/bin/python" ]]; then
            echo "$path"
            return 0
        fi
    done
    
    echo "[ERROR] 无法自动探测 Hermes venv 路径，请用 --hermes-venv 手动指定" >&2
    return 1
}

detect_repo_path() {
    local candidates=(
        "/opt/workspace/git/invoke-claude-enhancement"
        "$HOME/workspace/git/invoke-claude-enhancement"
        "/root/invoke-claude-enhancement"
    )
    
    for path in "${candidates[@]}"; do
        if [[ -d "$path/.git" ]]; then
            echo "$path"
            return 0
        fi
    done
    
    # 未找到现有 repo，返回默认安装位置
    echo "/opt/workspace/git/invoke-claude-enhancement"
    return 0
}

detect_claude_worker_lib() {
    local venv_path="$1"
    local candidates=(
        "$venv_path/lib/python3.11/site-packages/tools/claude_worker_lib.py"
        "$venv_path/lib/python3.9/site-packages/tools/claude_worker_lib.py"
        "$venv_path/lib/python3.10/site-packages/tools/claude_worker_lib.py"
        "$venv_path/lib/python3.12/site-packages/tools/claude_worker_lib.py"
        "$venv_path/lib/python3.13/site-packages/tools/claude_worker_lib.py"
    )
    
    for path in "${candidates[@]}"; do
        if [[ -f "$path" ]]; then
            echo "$path"
            return 0
        fi
    done
    
    echo "[ERROR] 无法在 venv 中找到 claude_worker_lib.py" >&2
    return 1
}

# ================== 参数解析 ==================
HERMES_VENV=""
REPO_PATH=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --hermes-venv)
            HERMES_VENV="$2"
            shift 2
            ;;
        --repo-path)
            REPO_PATH="$2"
            shift 2
            ;;
        *)
            echo "未知参数: $1"
            echo "用法: $0 [--hermes-venv /path] [--repo-path /path]"
            exit 1
            ;;
    esac
done

# 自动探测未提供的路径
[[ -z "$HERMES_VENV" ]] && HERMES_VENV=$(detect_hermes_venv)
[[ -z "$REPO_PATH" ]] && REPO_PATH=$(detect_repo_path)

echo "=========================================="
echo "invoke_claude 原生实现部署"
echo "=========================================="
echo "Hermes venv: $HERMES_VENV"
echo "Repo 路径:   $REPO_PATH"
echo ""

# ================== 步骤 1: 安装依赖 ==================
echo "[1/5] 安装 Python 依赖..."
"$HERMES_VENV/bin/pip" install -q httpx tenacity structlog

# 检查 Python 版本，3.11 以下需要 tomli
PYTHON_VERSION=$("$HERMES_VENV/bin/python" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if python3 -c "import sys; exit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null; then
    : # Python >= 3.11, tomllib 原生支持
else
    echo "  检测到 Python $PYTHON_VERSION < 3.11，安装 tomli..."
    "$HERMES_VENV/bin/pip" install -q tomli
fi

# ================== 步骤 2: 克隆/更新 repo ==================
if [[ -d "$REPO_PATH/.git" ]]; then
    echo "[2/5] 更新现有 repo..."
    cd "$REPO_PATH"
    git fetch origin
    git checkout main
    git reset --hard origin/main
else
    echo "[2/5] 克隆 repo..."
    mkdir -p "$(dirname "$REPO_PATH")"
    git clone https://github.com/86669666/invoke-claude-enhancement.git "$REPO_PATH"
    cd "$REPO_PATH"
    git checkout main
fi

CURRENT_COMMIT=$(git rev-parse --short HEAD)
echo "  当前 commit: $CURRENT_COMMIT"

# ================== 步骤 3: 定位并备份 claude_worker_lib.py ==================
echo "[3/5] 定位 claude_worker_lib.py..."
CLAUDE_WORKER_LIB=$(detect_claude_worker_lib "$HERMES_VENV")

if [[ ! -f "$CLAUDE_WORKER_LIB.bak" ]]; then
    echo "  创建备份: ${CLAUDE_WORKER_LIB}.bak"
    cp "$CLAUDE_WORKER_LIB" "$CLAUDE_WORKER_LIB.bak"
else
    echo "  备份已存在，跳过"
fi

# ================== 步骤 4: 注入 bridge 集成代码 ==================
echo "[4/5] 注入 bridge 集成代码..."

# 检查是否已注入（幂等性）
if grep -q "\[INTEGRATION\] Check if native Python implementation" "$CLAUDE_WORKER_LIB"; then
    echo "  已注入 bridge 代码，跳过"
else
    # 生成 bridge 代码（带正确缩进的 Python 片段）
    cat > /tmp/bridge_inject.py <<'PYEOF'
import re
import sys

claude_worker_lib = sys.argv[1]
repo_path = sys.argv[2]

with open(claude_worker_lib, "r") as f:
    lines = f.readlines()

# 找到插入位置：def invoke_claude(...) -> Dict[str, Any]: 后一行
insert_idx = None
for i, line in enumerate(lines):
    if re.search(r'^\s*\) -> Dict\[str, Any\]:', line):
        insert_idx = i + 1
        break

if insert_idx is None:
    raise RuntimeError("无法找到 invoke_claude 函数签名结束位置")

# Bridge 集成代码模板
bridge_code = f'''    # [INTEGRATION] Check if native Python implementation should be used
    if os.getenv("INVOKE_CLAUDE_NATIVE", "").lower() in ("1", "true", "yes"):
        try:
            # Import bridge module from invoke-claude-enhancement repo
            import sys
            from pathlib import Path
            
            # Add repo src/python to path if not already present
            repo_src = Path("{repo_path}/src/python")
            if repo_src.exists() and str(repo_src) not in sys.path:
                sys.path.insert(0, str(repo_src))
            
            from bridge import invoke_claude_native
            
            return invoke_claude_native(
                prompt=prompt,
                workdir=workdir,
                model=model,
                timeout=timeout,
                proxy_url=proxy_url or _default_proxy_url(),
                effort=effort,
            )
        except Exception as e:
            # Fallback to Node.js path if native impl fails
            print(f"[WARN] Native implementation failed, falling back to Node.js: {{e}}", flush=True)
    
    # [ORIGINAL] Node.js Claude Code CLI path (backward compatible)
'''

lines.insert(insert_idx, bridge_code)

with open(claude_worker_lib, "w") as f:
    f.writelines(lines)

print(f"✓ bridge 代码已注入到第 {insert_idx} 行")
PYEOF
    
    "$HERMES_VENV/bin/python" /tmp/bridge_inject.py "$CLAUDE_WORKER_LIB" "$REPO_PATH"
    rm /tmp/bridge_inject.py
fi

# ================== 步骤 5: 清理 .pyc 缓存 ==================
echo "[5/5] 清理 Python 缓存..."
find "$HERMES_VENV" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
find "$HERMES_VENV" -type f -name "*.pyc" -delete 2>/dev/null || true

echo ""
echo "=========================================="
echo "✓ 部署完成"
echo "=========================================="
echo ""
echo "启用原生实现："
echo "  export INVOKE_CLAUDE_NATIVE=1"
echo ""
echo "验证部署（快速测试）："
echo "  $HERMES_VENV/bin/python -c 'import os; os.environ[\"INVOKE_CLAUDE_NATIVE\"]=\"1\"; from tools.claude_worker_lib import invoke_claude; print(invoke_claude(prompt=\"9+9=\", workdir=\"/tmp\", model=\"Klite\", timeout=30))'"
echo ""
echo "systemd service 永久启用："
echo "  1. 编辑 /etc/systemd/system/hermes-gateway-<name>.service"
echo "  2. 在 [Service] 段添加: Environment=\"INVOKE_CLAUDE_NATIVE=1\""
echo "  3. 重启: systemctl daemon-reload && systemctl restart hermes-gateway-<name>"
echo ""
