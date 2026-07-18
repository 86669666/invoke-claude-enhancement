# invoke_claude Enhancement

**invoke_claude 工具增强：舰队标准化 + 配置文件化 + 多语言实现**

## 项目目标

增强 Hermes Agent 的 `invoke_claude` 工具，实现：
- 配置文件化（TOML/YAML/环境变量层级）
- 网络拓扑感知（自动选择正确的 base_url）
- 健壮错误处理（401/403/429 自动重试）
- 多语言实现（Python/Go/Rust 性能对比）

## 项目状态

**Phase 1: 调研与立项** ✅ 已完成

- [x] 精读官方 Claude Code 技能
- [x] GitHub 立项完成（org `86669666`）
- [x] 多子助理协同调研完成（3份报告）
- [x] 架构设计文档
- **结论**: 推荐 Python 原生实现（性能提升 95-98%，内存 -60~70%）

**Phase 2: Python 原生实现** ✅ 已完成

- [x] ConfigLoader 四层合并 + 网络拓扑自动检测（PR #1, commit `35a882a`）
- [x] AnthropicClient httpx 同步实现 + Extended Thinking 支持
- [x] RetryHandler 装饰器 + 错误分类 + jitter 防惊群
- [x] Bridge 环境变量开关 `INVOKE_CLAUDE_NATIVE=1` + fallback
- [x] CI 配置（pytest + black + pylint）
- **实测性能**: 延迟改进 +15.8%（Node.js 4.98s → 原生 4.19s），内存 -60~70%（30-50MB → 10-15MB）

**Phase 2.5: Hermes 工具集成 + 舰队推广** ✅ 已完成

- [x] 原生 bridge 安装脚本（稳定运行目录 + 不改写 worker）
- [x] FSPVE 舰队推广（招财/旺财/奶茶/可乐）— 已部署 ✅
- [x] 标准化部署文档 + 自动化脚本（PR #3, commit `a877f18`）
- HMPVE 舰队待推广 ⏳
- Windows 环境待验证 ⏳

**Phase 3: 多语言实现** ⏳ 待评估

- Go/Rust 实现按需启动（仅在 Python 版本仍有瓶颈时考虑）

## 配置

### Child-Only 配置文件（推荐）

```bash
export HERMES_HOME=/usr/local/lib/hermes-agent
install -m 0600 /dev/null "$HERMES_HOME/claude-plugin.env"

# 父 profile 的 .env:
# INVOKE_CLAUDE_NATIVE=1
#
# 写入 child-only 配置，避免把 Claude 凭据暴露到父进程环境
# 该文件仅包含：
# CLAUDE_API_KEY=sk-...
# CLAUDE_PROXY_URL=http://...
# CLAUDE_MODEL=Klite
```

## 舰队推广状态

| 实例   | 主机            | 状态  | 版本        | 部署日期    | 备注                     |
|--------|-----------------|-------|-------------|-------------|--------------------------| 
| 招财   | FSPVE VM100     | ✅    | a877f18     | 2026-06-21  | Python 3.13, 首个验证实例 |
| 旺财   | FSPVE VM111     | ✅    | a877f18     | 2026-06-21  | 与奶茶共享 venv          |
| 奶茶   | FSPVE VM111     | ✅    | a877f18     | 2026-06-21  | 与旺财共享 venv          |
| 可乐   | FSPVE VM100     | ✅    | a877f18     | 2026-06-21  | 与招财同机               |
| 花卷   | HMPVE VM103     | ⏳    | -           | -           | 待推广                   |
| 战马   | HMPVE VM103     | ⏳    | -           | -           | 待推广（与花卷同机）     |
| 来福   | HMPVE VM102     | ⏳    | -           | -           | 待推广                   |
| 初一   | Windows 47.251.184.187 | ⏳    | -           | -           | 待验证 Windows 兼容性     |
| 小满   | FSPVE VM112     | ⏳    | -           | -           | 待推广                   |

## 快速开始

### 1. 部署到新实例

```bash
# 克隆 repo（如未克隆）
cd /opt/workspace/git
git clone git@github.com:86669666/invoke-claude-enhancement.git
cd invoke-claude-enhancement

# 运行自动化部署脚本（三层自动探测 + 幂等；仅安装原生 bridge 文件）
bash scripts/deploy-invoke-claude-native.sh
```

详见 [DEPLOYMENT.md](DEPLOYMENT.md)

### 2. 验证部署

```bash
/usr/local/lib/hermes-agent/venv/bin/python -c '
from tools.claude_worker_lib import invoke_claude
print(callable(invoke_claude))
'
```

## 目录结构

```
.
├── docs/
│   ├── research/          # 子助理调研报告
│   └── design/            # 架构与设计文档
├── src/
│   ├── python/            # Python 原生实现
│   ├── go/                # Go 实现（Phase 3）
│   └── rust/              # Rust 实现（Phase 3）
├── scripts/
│   └── deploy-invoke-claude-native.sh  # 自动化部署脚本
├── tests/                 # 测试用例
├── .github/workflows/     # CI 配置
├── DEPLOYMENT.md          # 部署文档
└── HERMES_INTEGRATION.md  # Hermes 集成文档
```

## 调研成果（Phase 1）

### 配置文件设计
- **推荐格式**：TOML（Python 3.11+ 原生支持，语法简洁）
- **优先级**：环境变量 > 项目级 > 用户级 > 系统级 > 默认值
- **配置项**：`claude_bin_path`, `proxy_url`, `default_model`, `default_timeout`, `max_retries`, `network_topology`
- 详见：[docs/research/config-design.md](docs/research/config-design.md)

### 错误处理策略
- **401/403 认证错误**：不重试，立即失败
- **429 限流**：重试5次，指数退避（2-60秒）
- **网络超时**：重试3次，线性增长（5s→10s→15s）
- **502/503 上游故障**：重试4次，指数退避（3-45秒）
- 详见：[docs/research/retry-strategy.md](docs/research/retry-strategy.md)

### 性能优化方向
- **核心发现**：当前 Python 调用 Node.js 子进程（Claude Code CLI），冷启动开销 200-500ms
- **推荐方案**：Python 原生实现，直接调用 Anthropic API（消除 Node.js 启动开销）
- **实测结果**：延迟改进 +15.8%，内存 -60~70%
- **Go/Rust 重写**：仅在 Python 方案仍有瓶颈时考虑（独立二进制适合 CI/无 Python 环境场景）
- 详见：[docs/research/performance-analysis.md](docs/research/performance-analysis.md)

## 开发规范

- 开发前 `git pull` → 开分支 → 提 PR → CI 绿 → squash merge
- Python 用 pytest + black + pylint
- Go 用 `testing` + `golangci-lint`（Phase 3）
- Rust 用 `cargo test` + `clippy`（Phase 3）
- 核心逻辑测试覆盖率 > 80%

## License

MIT

## 相关项目

- [Hermes Agent](https://github.com/NousResearch/hermes-agent)
- [Claude Code CLI](https://code.claude.com/docs/en/cli-reference)
- [舰队运维脚本](https://github.com/86669666/fleet-ops-scripts)
