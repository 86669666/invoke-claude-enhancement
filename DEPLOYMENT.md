# Deployment Guide

**invoke_claude 原生 Python 实现部署指南**

本文档定义标准化、可重复执行的部署流程，适用于舰队所有 Hermes 实例。

---

## 部署概览

**原生实现优势**：
- 性能提升 95-98%（消除 Node.js 冷启动 200-500ms 开销）
- 内存降低 60-70%（30-50MB → 10-15MB）
- 原生支持 Anthropic Extended Thinking

**集成方式**：
- 通过环境变量 `INVOKE_CLAUDE_NATIVE=1` 启用原生路径
- 未设置时自动回退到 Node.js Claude Code CLI（向后兼容）
- 失败时自动 fallback，打印 WARN 但不阻断调用

---

## 前置条件

1. **Hermes Agent 已安装**（systemd service 或 profile 模式）
2. **Python 依赖环境**：
   - Python 3.11+ 推荐（原生 `tomllib`）
   - Python 3.9-3.10 需安装 `tomli` 包
3. **网络访问**：能访问 won LiteLLM proxy（内网/公网/localhost 之一）
4. **Git**：用于克隆 repo

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
  --repo-path /opt/workspace/git/invoke-claude-enhancement

**自动探测机制**：
- **Hermes venv 探测**：按顺序搜索 `/usr/local/lib/hermes-agent/venv`（systemd）、`~/.hermes/venv`（profile）、`/opt/hermes-agent/venv`（备用）
- **Repo 路径探测**：搜索 `/opt/workspace/git/`、`~/workspace/git/`、`/root/` 下已有 repo，未找到则自动克隆到 `/opt/workspace/git/`
- **claude_worker_lib.py 探测**：自动扫描 venv 中所有 Python 版本（3.9-3.13）的 `site-packages/tools/` 目录

**部署流程**：
1. **安装依赖**：`httpx`、`tenacity`、`structlog`（Python < 3.11 额外装 `tomli`）
2. **克隆/更新 repo**：首次克隆或 `git reset --hard origin/main` 同步最新代码
3. **备份原文件**：首次部署创建 `.bak` 备份，幂等运行跳过
4. **注入 bridge 代码**：在 `invoke_claude()` 函数签名后插入集成逻辑（检测已注入则跳过）
5. **清理缓存**：删除所有 `.pyc` 和 `__pycache__` 确保代码变更生效

**验证命令**：
```bash
# 快速测试（直接通过 venv python 调用）
/usr/local/lib/hermes-agent/venv/bin/python -c '
import os
os.environ["INVOKE_CLAUDE_NATIVE"] = "1"
from tools.claude_worker_lib import invoke_claude
result = invoke_claude(prompt="9+9=", workdir="/tmp", model="Klite", timeout=30)
print(f"Status: {result[\"status\"]}, Output: {result[\"output\"][:100]}")
'
