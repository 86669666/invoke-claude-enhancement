# Hermes Claude Code 技能深度分析

**官方技能**：`https://github.com/NousResearch/hermes-agent/tree/main/skills/autonomous-ai-agents/claude-code`

## 核心发现

### 两种编排模式

#### 1. Print Mode（推荐用于自动化）

**特点**：
- 非交互式一次性执行，完成即退出
- **跳过所有对话框**（workspace trust、permission bypass）
- 支持结构化 JSON 输出（`--output-format json`）
- 无需 PTY，直接在 `terminal()` 中运行

**适用场景**：
- CI/CD 自动化
- 一次性代码任务（修复 bug、添加功能、重构）
- 结构化数据提取（`--json-schema`）
- 管道处理（`cat file | claude -p "analyze"`）

**Hermes 集成示例**：
```python
terminal(
    command="claude -p 'Add error handling to all API calls in src/' --allowedTools 'Read,Edit' --max-turns 10",
    workdir="/path/to/project",
    timeout=120
)
```

**关键参数**：
- `--max-turns <n>`：限制循环次数，防止失控（推荐 5-10）
- `--max-budget-usd <n>`：成本上限（最低 $0.05）
- `--allowedTools`：白名单工具（`Read`, `Edit`, `Write`, `Bash`）
- `--output-format json`：结构化输出，包含 `session_id`、`num_turns`、`total_cost_usd`
- `--bare`：跳过钩子/插件/MCP 发现，最快启动

#### 2. Interactive PTY Mode（需要 tmux）

**特点**：
- 完整 TUI 对话式 REPL
- 需要处理两个对话框（workspace trust + permission bypass）
- 支持多轮迭代（refactor → review → fix → test）
- 可用斜杠命令（`/compact`, `/review`, `/model`）

**适用场景**：
- 多轮迭代开发
- 需要人工决策的复杂任务
- 探索性编码会话
- 需要使用 Claude 斜杠命令

**Hermes 集成示例**：
```python
# 1. 创建 tmux 会话
terminal(command="tmux new-session -d -s claude-work -x 140 -y 40")

# 2. 启动 Claude Code（带 permission bypass）
terminal(command="tmux send-keys -t claude-work 'cd /project && claude --dangerously-skip-permissions \\\"your task\\\"' Enter")

# 3. 处理 workspace trust 对话框（Enter 默认 Yes）
terminal(command="sleep 4 && tmux send-keys -t claude-work Enter")

# 4. 处理 permission bypass 对话框（Down 然后 Enter 选 Yes）
terminal(command="sleep 3 && tmux send-keys -t claude-work Down && sleep 0.3 && tmux send-keys -t claude-work Enter")

# 5. 监控进度
terminal(command="sleep 15 && tmux capture-pane -t claude-work -p -S -60")

# 6. 发送后续指令
terminal(command="tmux send-keys -t claude-work 'Now add unit tests' Enter")

# 7. 退出
terminal(command="tmux send-keys -t claude-work '/exit' Enter")
```

**对话框处理陷阱**：
- Trust 对话框默认选项是 "Yes"（直接 Enter）
- Permission bypass 对话框默认是 "No, exit"（**必须先 Down 再 Enter**）
- Trust 只在首次访问目录时出现，之后缓存
- Permission bypass 每次使用 `--dangerously-skip-permissions` 都会出现

### 并行多任务模式

官方技能展示了如何同时运行多个 Claude 实例：

```python
# 任务 1：修复后端
terminal(command="tmux new-session -d -s task1 && tmux send-keys -t task1 'claude -p \"Fix auth bug\" --max-turns 10' Enter")

# 任务 2：写测试
terminal(command="tmux new-session -d -s task2 && tmux send-keys -t task2 'claude -p \"Write tests\" --max-turns 15' Enter")

# 任务 3：更新文档
terminal(command="tmux new-session -d -s task3 && tmux send-keys -t task3 'claude -p \"Update README\" --max-turns 5' Enter")

# 批量监控
terminal(command="sleep 30 && for s in task1 task2 task3; do echo '=== '$s' ==='; tmux capture-pane -t $s -p -S -5; done")
```

**适用场景**：
- 独立模块的并行开发
- A/B 方案并行探索
- 分层任务（后端 + 前端 + 文档）

## 最佳实践总结

### 1. 优先 Print Mode

除非明确需要多轮交互，否则总是用 `-p`：
- 无需处理对话框
- 结构化输出便于解析
- 成本可控（`--max-turns`）
- 集成更简单

### 2. 合理设置 `--max-turns`

- 简单任务（code review）：1-3 turns
- 中等任务（add feature）：5-10 turns
- 复杂任务（refactor module）：10-20 turns
- **避免无限制**：防止失控循环和成本爆炸

### 3. 工具白名单

根据任务最小化权限：
- 代码审查：`--allowedTools 'Read'`
- 编辑现有文件：`--allowedTools 'Read,Edit'`
- 创建新文件：`--allowedTools 'Read,Write'`
- 运行测试：`--allowedTools 'Read,Edit,Bash'`

### 4. 使用 `--bare` 加速 CI

CI/脚本场景下用 `--bare` 跳过不必要的初始化：
```bash
claude --bare -p "Run tests" --allowedTools 'Read,Bash' --max-turns 5
```

需要 `ANTHROPIC_API_KEY` 环境变量（跳过 OAuth）。

### 5. 监控交互式会话

用 `tmux capture-pane` 检查进度：
- `❯` 在底部 = Claude 已完成或等待输入
- `●` 行 = Claude 正在使用工具
- `ctrl+o to expand` = 工具输出被截断

### 6. 会话恢复与管理

```bash
# 继续上次会话（同目录）
claude -p "Continue previous work" --continue --max-turns 5

# 恢复指定会话
claude -p "Pick up from session X" --resume <session_id> --max-turns 10

# 禁止持久化（CI 场景）
claude -p "One-off task" --no-session-persistence
```

### 7. 成本控制

```bash
# 预算上限
claude -p "Expensive task" --max-budget-usd 0.50 --max-turns 20

# 过载时回退到便宜模型
claude -p "Normal task" --fallback-model haiku --max-turns 10
```

### 8. 结构化输出

需要解析结果时用 JSON：
```bash
claude -p "List all functions in src/" --output-format json --max-turns 5 > output.json
```

返回字段：
- `session_id`：会话 ID（可用于恢复）
- `num_turns`：实际循环次数
- `total_cost_usd`：总成本
- `result`：最终结果文本
- `subtype`：`success` / `error_max_turns` / `error_budget`

### 9. 上下文管理

交互模式下监控上下文使用率（`/context` 命令）：
- < 70%：正常运行
- 70-85%：精度下降，考虑 `/compact`
- \> 85%：幻觉风险高，必须 `/compact` 或 `/clear`

### 10. 清理资源

tmux 会话不会自动清理：
```bash
tmux kill-session -t claude-work
```

## 与 Hermes `invoke_claude` 的对比

### 当前 `invoke_claude` 实现

- 封装了 Claude Code CLI 调用
- 支持后台运行管理（`invoke_claude_start` + `poll` + `cancel`）
- 默认 `output_format="json"`
- 默认 `proxy_url` 指向 claude-code-proxy（已废弃，现直连 won LiteLLM）

### 官方技能的额外价值

1. **tmux 编排模式**：官方技能展示了如何用 tmux 管理交互式会话，`invoke_claude` 当前不支持
2. **对话框处理**：详细说明了两个对话框的处理逻辑（trust + permission bypass）
3. **并行多任务**：展示了如何同时运行多个 Claude 实例
4. **成本与性能优化**：`--bare`、`--allowedTools`、`--fallback-model` 等实用技巧

### 改进方向

基于官方技能，`invoke_claude` 可以增强：

1. **交互式模式支持**：
   - 新增 `invoke_claude_interactive()` 函数
   - 自动创建 tmux 会话 + 处理对话框
   - 返回 tmux session_id 供后续交互

2. **并行任务编排**：
   - 新增 `invoke_claude_parallel()` 函数
   - 接受任务列表，自动分配 tmux 会话
   - 批量监控进度，汇总结果

3. **更细粒度的参数控制**：
   - 暴露 `--allowedTools`、`--bare`、`--fallback-model`
   - 支持 `--json-schema` 结构化提取

4. **成本与性能监控**：
   - 解析 JSON 输出中的 `total_cost_usd`、`num_turns`
   - 提供累计成本统计

## 官方技能中的隐藏宝藏

### CLAUDE.md — 项目上下文文件

Claude Code 自动加载项目根目录的 `CLAUDE.md`，用于持久化项目上下文：

```markdown
# Project: My API

## Architecture
- FastAPI backend with SQLAlchemy ORM
- PostgreSQL database, Redis cache

## Key Commands
- `make test` — run full test suite
- `make lint` — ruff + mypy

## Code Standards
- Type hints on all public functions
- 2-space indentation for YAML
- No wildcard imports
```

**关键建议**："Be specific" — 别写 "Write good code"，写 "Use 2-space indentation for JS"。

### Hooks — 事件驱动自动化

在 `.claude/settings.json` 中配置钩子：

```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Write(*.py)",
      "hooks": [{"type": "command", "command": "ruff check --fix $CLAUDE_FILE_PATHS"}]
    }],
    "PreToolUse": [{
      "matcher": "Bash",
      "hooks": [{"type": "command", "command": "if echo \"$CLAUDE_TOOL_INPUT\" | grep -q 'rm -rf'; then exit 2; fi"}]
    }]
  }
}
```

8 种钩子类型：
- `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Notification`, `Stop`, `SubagentStop`, `PreCompact`, `SessionStart`

### Custom Subagents

在 `.claude/agents/` 定义专用子助理：

```markdown
# .claude/agents/security-reviewer.md
---
name: security-reviewer
description: Security-focused code review
model: opus
tools: [Read, Bash]
---
You are a senior security engineer. Review code for:
- Injection vulnerabilities (SQL, XSS, command injection)
- Authentication/authorization flaws
```

调用：`@security-reviewer review the auth module`

Claude 可以编排多个 agent："Use @db-expert to optimize queries, then @security to audit the changes."

### MCP Integration

添加外部工具服务器：

```bash
# GitHub integration
claude mcp add -s user github -- npx @modelcontextprotocol/server-github

# PostgreSQL queries
claude mcp add -s local postgres -- npx @anthropic-ai/server-postgres --connection-string postgresql://localhost/mydb
```

## 结论

官方 `claude-code` 技能是 **生产级编排指南**，不仅是使用说明。关键收获：

1. **Print Mode 优先**：90% 场景下足够，更简单、更可控
2. **tmux 是交互模式的唯一可靠方式**：PTY alone 不够，需要 `capture-pane` 和 `send-keys`
3. **对话框处理是陷阱**：Permission bypass 默认选项是 "No"，必须先 Down
4. **并行多任务是杀手锏**：多个独立 tmux 会话 + 批量监控
5. **成本控制是必需品**：`--max-turns` + `--max-budget-usd` 防止失控

**对 `invoke_claude` 的最大启发**：当前实现覆盖了 Print Mode 核心功能，但缺少交互式编排和并行任务支持。如果要增强，优先级应该是：

1. 暴露更多 CLI 参数（`--allowedTools`, `--bare`, `--fallback-model`）
2. 解析并暴露成本与性能指标（`total_cost_usd`, `num_turns`）
3. 添加交互式模式支持（如果有多轮需求）
4. 添加并行任务编排（如果有并行需求）

**但根据性能分析报告**：最高优先级应该是 **Python 原生实现**（直接调用 Anthropic API），消除 Node.js 启动开销，性能提升 95-98%。其他增强是锦上添花。
