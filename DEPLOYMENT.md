# Deployment Guide

**invoke_claude 原生 Python 实现部署指南**

本文档定义标准化、可重复执行的部署流程，适用于舰队所有 Hermes 实例。

---

## 部署概览

**原生实现优势**：
- **实测性能提升**：延迟改进 +15.8%（Node.js 4.98s → 原生 4.19s），内存降低 60-70%（30-50MB → 10-15MB）
  - _注_：Phase 1 预测值为「性能提升 95-98%」，实测改进主要在内存占用和冷启动消除；总延迟受网络/模型响应时间主导
- 原生支持 Anthropic Extended Thinking

**集成方式**：
- 原生 bridge 文件安装到稳定运行目录：`/usr/local/lib/hermes-agent/native/invoke_claude`
- 现有 Hermes worker 必须已经具备 child-only 插件集成能力；部署脚本只校验，不改写 `tools/claude_worker_lib.py`
- `INVOKE_CLAUDE_NATIVE=1` 保留在父 profile 的 `.env`
- `HERMES_HOME/claude-plugin.env`（权限 `0600`）仅供子进程读取，且只允许包含 `CLAUDE_API_KEY`、`CLAUDE_PROXY_URL`、`CLAUDE_MODEL`

---

## 前置条件

1. **Hermes Agent 已安装**（systemd service 或 profile 模式）
2. **Python 依赖环境**：
   - Python 3.11+ 推荐（原生 `tomllib`）
   - Python 3.9-3.10 需安装 `tomli` 包
3. **网络访问**：能访问 won LiteLLM proxy（内网/公网/localhost 之一）
4. **Claude Code CLI（fallback 路径用）**：
   - **安装检测**：`which claude && claude --version`
   - **登录状态检测**：`claude auth status` → `loggedIn: true/false`
   - **未安装时**：原生路径仍可用，但 fallback 哑火（不阻断部署，仅影响容错）
   - **nologin（未登录）时**：fallback 静默失败，建议登录：`claude auth login`
   - _注_：原生实现（`INVOKE_CLAUDE_NATIVE=1`）走 LiteLLM 不依赖 CLI；CLI 仅在原生路径失败时 fallback

---

## 自动化部署脚本

### 1. 一键部署（推荐）

**脚本路径**: `scripts/deploy-invoke-claude-native.sh`

**用法**:
```bash
# 全自动探测（推荐）
bash scripts/deploy-invoke-claude-native.sh

# 手动指定路径
bash scripts/deploy-invoke-claude-native.sh \
  --hermes-venv /usr/local/lib/hermes-agent/venv \
  --repo-path /opt/workspace/git/invoke-claude-enhancement \
  --runtime-dir /usr/local/lib/hermes-agent/native/invoke_claude
```

**child-only 凭据文件**：
```bash
export HERMES_HOME=/usr/local/lib/hermes-agent
install -m 0600 /dev/null "$HERMES_HOME/claude-plugin.env"

# 父 profile 的 .env:
# INVOKE_CLAUDE_NATIVE=1
#
# claude-plugin.env 中仅写入：
# CLAUDE_API_KEY=sk-...
# CLAUDE_PROXY_URL=http://10.10.10.111:4100
# CLAUDE_MODEL=Klite
```

**自动探测机制**：
- **Hermes venv 探测**：按顺序搜索 `/usr/local/lib/hermes-agent/venv`（systemd）、`~/.hermes/venv`（profile）、`/opt/hermes-agent/venv`（备用）
- **Repo 路径探测**：默认使用当前脚本所在 repo 根目录，可通过 `--repo-path` 覆盖
- **Runtime 路径探测**：默认固定使用 `/usr/local/lib/hermes-agent/native/invoke_claude`，仅在显式传入 `--runtime-dir` 时覆盖
- **claude_worker_lib.py 探测**：自动扫描 venv 中所有 Python 版本（3.9-3.13）的 `site-packages/tools/` 目录

**部署流程**：
1. **按需安装依赖**：先导入检查 `httpx`、`structlog`（Python < 3.11 额外检查 `tomli`），仅安装缺失项
2. **校验 worker 标记**：必须已存在 `ClaudePluginResolver`、`_load_native_bridge`、`api_key=resolved_api_key`、`invoke_claude(... config_path=...)`，且 bridge 路径不能仍指向 git worktree
3. **备份并原子替换运行目录**：将原生 bridge 文件安装到稳定 runtime；若后续 smoke 验证失败也会回滚
4. **检查 child-only 凭据文件**：仅提示/校正 `HERMES_HOME/claude-plugin.env` 权限为 `0600`，且不写入 feature flag
5. **语法/编译/导入 smoke 验证**：验证已安装模块与现有 worker 可导入；失败时恢复旧 runtime
6. **不重启服务**：部署脚本不会执行 service restart

**验证命令**：
```bash
# 快速测试（直接通过 venv python 调用）
/usr/local/lib/hermes-agent/venv/bin/python -c '
from tools.claude_worker_lib import invoke_claude
print(callable(invoke_claude))
'
```

如果该验证失败，先检查两点：
- `tools/claude_worker_lib.py` 是否已经包含 `ClaudePluginResolver`、`_load_native_bridge`、`api_key=resolved_api_key`、`config_path`
- 父 profile 的 `.env` 是否包含 `INVOKE_CLAUDE_NATIVE=1`，且 `HERMES_HOME/claude-plugin.env` 仅包含 `CLAUDE_API_KEY`、`CLAUDE_PROXY_URL`、`CLAUDE_MODEL`
