# invoke_claude Enhancement — 架构设计

## 项目背景

当前 Hermes Agent 的 `invoke_claude` 工具通过 Python 调用 Claude Code CLI（Node.js 实现），存在以下问题：

1. **冷启动开销**：每次调用启动 Node.js 进程，耗时 200-500ms
2. **硬编码配置**：proxy_url 等参数写死在代码中，无法灵活配置
3. **错误处理不足**：网络故障、限流、认证失败缺少自动重试
4. **网络拓扑无感知**：无法根据设备位置自动选择最优 LiteLLM 端点

## 设计目标

1. **性能提升**：消除 Node.js 启动开销，冷启动时间降低 95%+
2. **配置灵活**：支持多层级配置（环境变量 > 项目 > 用户 > 默认）
3. **健壮可靠**：智能错误处理与重试，适应网络波动
4. **拓扑感知**：自动检测设备位置，选择最优 base_url
5. **向后兼容**：零配置可用，不破坏现有调用

## 总体架构

### Phase 2: Python 原生实现（核心）

**方案选择**：放弃调用 Claude Code CLI，直接用 Python 调用 Anthropic Messages API

**理由**（来自性能分析）：
- 消除 Node.js 启动开销：**性能提升 95-98%**
- 内存占用降低 60-70%（30-50MB → 10-15MB）
- 保持 Python 生态优势（asyncio、httpx、rich）
- 实施周期短（1-2 天 MVP）

**架构图**：

```
┌─────────────────────────────────────────────────────────┐
│                   Hermes Agent                          │
│  ┌──────────────────────────────────────────────────┐  │
│  │          invoke_claude (Python)                  │  │
│  │  ┌────────────────────────────────────────────┐  │  │
│  │  │  1. ConfigLoader                           │  │  │
│  │  │     - 多层级配置合并                        │  │  │
│  │  │     - 网络拓扑自动检测                      │  │  │
│  │  │  2. RetryHandler                           │  │  │
│  │  │     - 智能错误分类                          │  │  │
│  │  │     - 指数退避 + 抖动                       │  │  │
│  │  │  3. AnthropicClient                        │  │  │
│  │  │     - httpx 异步调用                        │  │  │
│  │  │     - 流式/批量模式                         │  │  │
│  │  └────────────────────────────────────────────┘  │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
                        │
                        │ HTTPS (Anthropic Messages API)
                        ▼
┌─────────────────────────────────────────────────────────┐
│              won LiteLLM Proxy                          │
│  - FSPVE 内网: http://10.10.10.111:4100                │
│  - 公网设备:   https://llm.bai.one                      │
│  - 本机同设备: http://127.0.0.1:4100                    │
└─────────────────────────────────────────────────────────┘
                        │
                        ▼
                  Upstream Models
              (Claude Opus/Sonnet/Haiku)
```

### Phase 3: 多语言实现（可选）

**Go 实现**：
- 适用场景：CI/CD 环境无 Python、需要独立二进制
- 预期性能：冷启动 5-15ms，内存 8-15MB，二进制 8-15MB
- 技术栈：cobra (CLI) + viper (配置) + go-anthropic (SDK)

**Rust 实现**：
- 适用场景：性能极致优化、嵌入式/边缘设备
- 预期性能：冷启动 3-10ms，内存 5-10MB，二进制 3-8MB
- 技术栈：clap (CLI) + config (配置) + reqwest (HTTP)

**决策依据**：
- Python 版本稳定后再评估是否需要
- 仅在以下情况考虑 Go/Rust：
  1. CI 环境禁止 Python 依赖
  2. 需要分发独立二进制给非技术用户
  3. Python 版本在实际负载下仍有瓶颈

## 核心模块设计

### 1. ConfigLoader — 配置管理

**配置层级**（优先级从高到低）：

```
环境变量 (CLAUDE_*)
    ↓
项目级配置 (./.claude/config.toml)
    ↓
用户级配置 (~/.config/claude/config.toml)
    ↓
系统级配置 (/etc/claude/config.toml)
    ↓
硬编码默认值
```

**配置项定义**：

```toml
[claude]
bin_path = "/root/.hermes/node/bin/claude"  # 仅 CLI 模式需要
default_model = "claude-opus-4"
default_timeout = 900
max_retries = 3

[proxy]
url = "http://10.10.10.111:4100"
topology = "auto"  # auto | fspve_internal | public | localhost

[retry]
enabled = true
max_attempts = 5
initial_delay = 2.0
max_delay = 60.0
```

**网络拓扑自动检测**：

```python
def detect_network_topology() -> str:
    """
    自动检测设备位置，选择最优 base_url
    
    规则：
    - 能连接 10.10.10.111:4100 → fspve_internal
    - 能连接 127.0.0.1:4100 → localhost
    - 否则 → public
    """
    if can_connect("10.10.10.111", 4100, timeout=1):
        return "fspve_internal"
    if can_connect("127.0.0.1", 4100, timeout=1):
        return "localhost"
    return "public"

TOPOLOGY_BASE_URLS = {
    "fspve_internal": "http://10.10.10.111:4100",
    "localhost": "http://127.0.0.1:4100",
    "public": "https://llm.bai.one"
}
```

### 2. RetryHandler — 错误处理与重试

**错误分类与策略**（详见 `docs/research/retry-strategy.md`）：

| 错误类型 | 状态码 | 重试次数 | 退避策略 | 延迟范围 |
|---------|--------|---------|---------|---------|
| 认证失败 | 401/403 | 0 | N/A | 立即失败 |
| 限流 | 429 | 5 | 指数 + 抖动 | 2-60s |
| 网络超时 | Timeout | 3 | 线性增长 | 5-15s |
| 连接拒绝 | ConnRefused | 2 | 固定延迟 | 1s |
| 上游故障 | 502/503 | 4 | 指数 + 抖动 | 3-45s |

**实现方式**：

```python
@retry_with_strategy(
    retryable_errors=[429, 502, 503, TimeoutError, ConnectionError],
    max_attempts=5,
    strategy="exponential"
)
async def call_anthropic_api(messages, model, **kwargs):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url}/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            json={"model": model, "messages": messages, **kwargs},
            timeout=kwargs.get("timeout", 900)
        )
        response.raise_for_status()
        return response.json()
```

### 3. AnthropicClient — API 调用层

**核心功能**：

1. **同步/异步双模式**：
   - `invoke_claude()`：同步接口，兼容现有调用
   - `invoke_claude_async()`：异步接口，支持并发

2. **流式/批量双输出**：
   - `output_format="json"`：等待完成返回完整结果
   - `output_format="stream-json"`：实时流式输出（SSE）

3. **工具调用支持**：
   - 解析 Anthropic Messages API 的 `tool_use` 块
   - 执行工具调用并返回结果
   - 支持多轮工具调用循环（max_turns 限制）

**接口设计**：

```python
def invoke_claude(
    prompt: str,
    model: str = None,          # 优先级：参数 > 配置 > 默认值
    workdir: str = None,
    timeout: int = None,
    max_turns: int = 20,
    allowed_tools: List[str] = None,
    output_format: str = "json",
    effort: str = "medium",     # low | medium | high | max | auto
    **kwargs
) -> Dict[str, Any]:
    """
    调用 Claude Code 完成任务
    
    Returns:
        {
            "type": "result",
            "subtype": "success" | "error_max_turns" | "error_budget",
            "result": "最终输出文本",
            "session_id": "uuid",
            "num_turns": 3,
            "total_cost_usd": 0.0787,
            "duration_ms": 10276,
            "usage": {"input_tokens": 5, "output_tokens": 603}
        }
    """
```

## 实施路线图

### Phase 1: 调研与立项（✅ 已完成）

- [x] 精读官方 Claude Code 技能
- [x] GitHub 立项（`86669666/invoke-claude-enhancement`）
- [x] 多子助理协同调研（配置设计 + 错误处理 + 性能分析）
- [x] 架构设计文档（本文档）

**交付物**：
- `docs/research/config-design.md` (19KB)
- `docs/research/retry-strategy.md` (23KB)
- `docs/research/performance-analysis.md` (7.6KB)
- `docs/hermes-claude-code-skill-analysis.md` (11KB)
- `docs/design/architecture.md` (本文档)

### Phase 2: Python 原生实现（⏳ 1-2 周）

**Week 1: MVP**
- [ ] 实现 ConfigLoader（TOML 解析 + 多层级合并）
- [ ] 实现 RetryHandler（装饰器 + 错误分类）
- [ ] 实现 AnthropicClient（同步调用 Messages API）
- [ ] 集成到现有 `invoke_claude` 接口（环境变量开关）
- [ ] 单元测试（覆盖率 > 80%）

**Week 2: 完整特性**
- [ ] 异步模式（`invoke_claude_async`）
- [ ] 流式输出（SSE 解析）
- [ ] 工具调用支持（tool_use 循环）
- [ ] 网络拓扑自动检测
- [ ] 集成测试（真实调用 won LiteLLM）
- [ ] 文档更新（API 文档 + 使用示例）

**Week 3: 舰队推广**
- [ ] 在可乐实例验证
- [ ] 推广到招财（同设备）
- [ ] 推广到旺财/奶茶（同内网）
- [ ] 收集反馈，迭代优化

### Phase 3: 多语言实现（⏳ 可选，1-2 周）

**仅在以下情况启动**：
1. Python 版本在生产负载下仍有瓶颈
2. 明确需要独立二进制（CI 环境禁止 Python）
3. 有足够开发资源

**Go 实现**（1 周）：
- [ ] CLI 框架（cobra）+ 配置管理（viper）
- [ ] HTTP 客户端（go-anthropic 或 resty）
- [ ] 错误处理与重试
- [ ] 单元测试 + 集成测试
- [ ] 性能基准测试（vs Python）

**Rust 实现**（1 周）：
- [ ] CLI 框架（clap）+ 配置管理（config）
- [ ] HTTP 客户端（reqwest + tokio）
- [ ] 错误处理与重试
- [ ] 单元测试 + 集成测试
- [ ] 性能基准测试（vs Python/Go）

**性能基准测试**：
- [ ] 冷启动时间对比（Python/Go/Rust）
- [ ] 内存占用对比（RSS/Heap）
- [ ] 二进制体积对比（stripped）
- [ ] 吞吐量测试（100 并发请求）
- [ ] 结果写入 `docs/benchmarks.md`

## 技术选型

### Python 实现

| 组件 | 技术选型 | 理由 |
|-----|---------|-----|
| HTTP 客户端 | httpx | 异步支持 + SSE 流式输出 + HTTP/2 |
| 配置解析 | tomllib (3.11+) | 标准库原生支持 TOML |
| 重试机制 | tenacity | 成熟的重试库，支持多种策略 |
| 日志 | structlog | 结构化日志，便于监控 |
| 测试 | pytest + pytest-asyncio | 异步测试支持 |
| 类型检查 | mypy | 静态类型保证 |

### Go 实现（可选）

| 组件 | 技术选型 | 理由 |
|-----|---------|-----|
| CLI 框架 | cobra | 业界标准，kubectl/docker 同款 |
| 配置管理 | viper | 多源配置 + 热重载 |
| HTTP 客户端 | resty | 友好的 API + 重试内置 |
| 测试 | testing + testify | 标准库 + 断言库 |

### Rust 实现（可选）

| 组件 | 技术选型 | 理由 |
|-----|---------|-----|
| CLI 框架 | clap (derive) | 编译时验证 + 自动生成 help |
| 配置管理 | config | 多源配置支持 |
| HTTP 客户端 | reqwest + tokio | 异步高性能 + 流式支持 |
| 错误处理 | anyhow + thiserror | 错误链 + 自定义错误类型 |
| 测试 | cargo test | 标准工具链 |

## 验收标准

### Phase 1（✅ 已完成）
- [x] GitHub repo 创建，README/LICENSE/CI 齐全
- [x] 3 份子助理调研报告（配置/错误处理/性能）
- [x] 架构设计文档（本文档）
- [x] 官方技能精读总结

### Phase 2（Python 原生实现）
- [ ] 配置文件功能完整（TOML + 环境变量 + 多层级）
- [ ] 网络拓扑自动检测（FSPVE/公网/本机）
- [ ] 错误处理健壮（401/403/429/超时/上游故障）
- [ ] 单元测试覆盖率 > 80%
- [ ] 集成测试通过（真实调用 won LiteLLM）
- [ ] 性能提升验证（冷启动 < 50ms，内存 < 20MB）
- [ ] 至少 2 个舰队成员试用反馈

### Phase 3（多语言实现，可选）
- [ ] Go 版本功能等价 Python，性能基准测试完成
- [ ] Rust 版本功能等价 Python，性能基准测试完成
- [ ] `docs/benchmarks.md` 包含三语言横向对比
- [ ] 三版本集成测试一致性验证

## 后续优化方向

Phase 2 稳定后可考虑：

1. **Prompt 缓存优化**：复用 system prompt，降低输入 token 成本
2. **批量请求**：聚合多个小任务，减少网络往返
3. **本地模型支持**：ollama/llamafile 集成（离线场景）
4. **监控与可观测**：Prometheus metrics + OpenTelemetry traces
5. **Web UI**：可视化配置管理与任务监控

## 总结

本项目的核心价值是 **Python 原生实现**（Phase 2），预期性能提升 95%+，开发周期短，风险低。

Go/Rust 实现（Phase 3）是锦上添花，仅在明确需求时启动。

关键成功因素：
1. **配置灵活**：零配置可用，高级用户可深度定制
2. **健壮可靠**：自动重试瞬时故障，明确失败给出诊断
3. **拓扑感知**：自动选择最优端点，适应网络环境
4. **向后兼容**：不破坏现有调用，渐进式迁移
