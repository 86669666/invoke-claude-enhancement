# invoke_claude 配置文件设计方案

## 一、配置格式对比分析

### 1.1 YAML

**优势：**
- 人类可读性强，层级结构清晰
- 支持注释，方便文档化
- 支持复杂数据结构（列表、字典、多行字符串）
- 社区广泛采用（Kubernetes、Docker Compose、GitHub Actions）
- Python 生态成熟（PyYAML、ruamel.yaml）

**劣势：**
- 对缩进敏感，容易出错
- 解析器性能相对较低
- 规范复杂，存在安全隐患（需禁用 `!python` 标签）
- 多种书写方式可能导致不一致

**适用场景：** 配置项较多、需要丰富注释、团队已习惯 YAML

### 1.2 TOML

**优势：**
- 语法简单明确，不易出错
- 明确的类型系统（字符串、整数、布尔、日期时间）
- 支持注释
- 解析性能好
- Rust/Python 生态支持良好（tomli/tomllib）
- 适合配置文件场景（Cargo、pip）

**劣势：**
- 深层嵌套表达繁琐（需使用 `[section.subsection]`）
- 不支持引用和锚点
- 社区相对较小

**适用场景：** 配置层级较浅、需要类型安全、追求简洁

### 1.3 JSON

**优势：**
- 标准化强，所有语言原生支持
- 解析速度快
- 工具链完善（linter、formatter、schema 验证）
- 结构严格，不易歧义

**劣势：**
- 不支持注释（需使用非标准扩展或外部文档）
- 可读性较差（尾随逗号、引号要求严格）
- 不适合手工编辑
- 不支持多行字符串

**适用场景：** 机器生成配置、需要严格验证、与 API 交互

### 1.4 推荐方案

**推荐使用 TOML**，理由：
1. invoke_claude 配置层级简单（1-2 层），TOML 足够表达
2. 类型明确减少错误（proxy_url 是字符串，max_retries 是整数）
3. 易于手工编辑，不依赖缩进
4. Python 3.11+ 内置 `tomllib`，无需外部依赖
5. 与 Rust 生态（如 Claude CLI 可能用 Rust 实现）兼容性好

**备选方案：** 同时支持 YAML（`.yaml`）和 TOML（`.toml`），按文件扩展名自动选择解析器，满足不同用户习惯。

---

## 二、配置层级设计

### 2.1 优先级顺序（高 → 低）

```
1. 环境变量 (CLAUDE_*)
2. 项目级配置 (./.claude/config.toml)
3. 用户级配置 (~/.config/claude/config.toml 或 ~/.claude.toml)
4. 系统级配置 (/etc/claude/config.toml) [可选]
5. 硬编码默认值 (代码中 DEFAULT_CONFIG)
```

### 2.2 各层级说明

| 层级 | 路径 | 用途 | 优先级 |
|------|------|------|--------|
| **环境变量** | `CLAUDE_*` | 临时覆盖、CI/CD、Docker 容器 | 最高 |
| **项目级** | `./claude/config.toml` 或 `./.clauderc.toml` | 项目特定配置（如特定代理、模型） | 高 |
| **用户级** | `~/.config/claude/config.toml` | 用户个人偏好（跨项目共享） | 中 |
| **系统级** | `/etc/claude/config.toml` | 组织/机器级默认值（多用户共享） | 低 |
| **默认值** | 代码内 `DEFAULT_CONFIG` | 保证向后兼容，无配置时可用 | 最低 |

### 2.3 配置文件查找顺序

```python
def find_config_files():
    """按优先级返回存在的配置文件路径列表（低 → 高）"""
    candidates = [
        "/etc/claude/config.toml",                    # 系统级
        "~/.config/claude/config.toml",               # XDG 标准
        "~/.claude.toml",                             # 用户级简写
        "./.claude/config.toml",                      # 项目级（目录）
        "./.clauderc.toml",                           # 项目级（rc 风格）
    ]
    return [Path(p).expanduser() for p in candidates if Path(p).expanduser().exists()]
```

---

## 三、配置项定义

### 3.1 核心配置项

```toml
# ~/.config/claude/config.toml

[claude]
# Claude CLI 可执行文件路径（默认从 PATH 查找）
bin_path = "/usr/local/bin/claude"  # 可选，留空则用 shutil.which("claude")

# 默认使用的模型
default_model = "claude-3-7-sonnet-20250219"  # 或 claude-3-5-sonnet-20241022

[network]
# 代理服务器 URL（支持 HTTP/HTTPS/SOCKS5）
proxy_url = "http://10.10.10.111:4100"  # FSPVE 内网示例

# 网络拓扑标识（用于自动选择代理配置）
# 可选值：local, internal, external, auto
topology = "auto"

# 请求超时时间（秒）
default_timeout = 300

# 最大重试次数
max_retries = 3

# 重试间隔（秒）
retry_delay = 2

[network.topology_map]
# 不同网络拓扑的代理映射（topology = "auto" 时生效）
local = "http://127.0.0.1:4100"        # 本机同设备
internal = "http://10.10.10.111:4100"  # FSPVE 内网
external = "https://llm.bai.one"       # 公网

[advanced]
# 启用详细日志
verbose = false

# 日志文件路径
log_file = "~/.cache/claude/invoke.log"

# 是否验证 SSL 证书
verify_ssl = true

# 自定义请求头
[advanced.headers]
User-Agent = "Hermes-Claude-Invoker/1.0"
```

### 3.2 配置项规范表

| 配置项 | 类型 | 默认值 | 环境变量 | 描述 |
|--------|------|--------|----------|------|
| `claude.bin_path` | string | `"claude"` | `CLAUDE_BIN_PATH` | Claude CLI 路径 |
| `claude.default_model` | string | `"claude-3-7-sonnet-20250219"` | `CLAUDE_MODEL` | 默认模型 |
| `network.proxy_url` | string | `null` | `CLAUDE_PROXY_URL` | 代理地址 |
| `network.topology` | enum | `"auto"` | `CLAUDE_TOPOLOGY` | 网络拓扑 |
| `network.default_timeout` | integer | `300` | `CLAUDE_TIMEOUT` | 超时时间（秒） |
| `network.max_retries` | integer | `3` | `CLAUDE_MAX_RETRIES` | 最大重试次数 |
| `network.retry_delay` | integer | `2` | `CLAUDE_RETRY_DELAY` | 重试间隔（秒） |
| `advanced.verbose` | boolean | `false` | `CLAUDE_VERBOSE` | 详细日志 |
| `advanced.verify_ssl` | boolean | `true` | `CLAUDE_VERIFY_SSL` | SSL 验证 |

---

## 四、配置示例

### 4.1 场景一：FSPVE 内网设备

```toml
# ~/.config/claude/config.toml
[network]
proxy_url = "http://10.10.10.111:4100"
topology = "internal"
default_timeout = 300
max_retries = 5

[claude]
default_model = "claude-3-7-sonnet-20250219"
```

### 4.2 场景二：公网设备

```toml
# ~/.config/claude/config.toml
[network]
proxy_url = "https://llm.bai.one"
topology = "external"
verify_ssl = true

[claude]
default_model = "claude-3-5-sonnet-20241022"
```

### 4.3 场景三：本机同设备（最低延迟）

```toml
# ~/.config/claude/config.toml
[network]
proxy_url = "http://127.0.0.1:4100"
topology = "local"
default_timeout = 60

[advanced]
verbose = true
```

### 4.4 场景四：自动拓扑检测

```toml
# ~/.config/claude/config.toml
[network]
topology = "auto"  # 自动根据网络环境选择代理

[network.topology_map]
local = "http://127.0.0.1:4100"
internal = "http://10.10.10.111:4100"
external = "https://llm.bai.one"
```

### 4.5 场景五：项目特定配置（覆盖用户级）

```toml
# ./project/.claude/config.toml
[claude]
default_model = "claude-3-opus-20240229"  # 项目需要更强模型

[network]
proxy_url = "http://project-proxy.local:8080"  # 项目专用代理
max_retries = 10  # 项目允许更多重试
```

### 4.6 场景六：环境变量临时覆盖

```bash
# 临时使用不同代理运行
export CLAUDE_PROXY_URL="http://192.168.1.100:3128"
export CLAUDE_TIMEOUT=600
hermes chat
```

---

## 五、优先级合并逻辑

### 5.1 伪代码实现

```python
from pathlib import Path
from typing import Dict, Any, Optional
import os
import tomllib  # Python 3.11+

class ConfigLoader:
    """配置加载器，实现多层级合并逻辑"""
    
    DEFAULT_CONFIG = {
        "claude": {
            "bin_path": "claude",
            "default_model": "claude-3-7-sonnet-20250219",
        },
        "network": {
            "proxy_url": None,
            "topology": "auto",
            "default_timeout": 300,
            "max_retries": 3,
            "retry_delay": 2,
            "topology_map": {
                "local": "http://127.0.0.1:4100",
                "internal": "http://10.10.10.111:4100",
                "external": "https://llm.bai.one",
            },
        },
        "advanced": {
            "verbose": False,
            "log_file": "~/.cache/claude/invoke.log",
            "verify_ssl": True,
            "headers": {},
        },
    }
    
    CONFIG_PATHS = [
        "/etc/claude/config.toml",
        "~/.config/claude/config.toml",
        "~/.claude.toml",
        "./.claude/config.toml",
        "./.clauderc.toml",
    ]
    
    ENV_VAR_MAP = {
        "CLAUDE_BIN_PATH": "claude.bin_path",
        "CLAUDE_MODEL": "claude.default_model",
        "CLAUDE_PROXY_URL": "network.proxy_url",
        "CLAUDE_TOPOLOGY": "network.topology",
        "CLAUDE_TIMEOUT": "network.default_timeout",
        "CLAUDE_MAX_RETRIES": "network.max_retries",
        "CLAUDE_RETRY_DELAY": "network.retry_delay",
        "CLAUDE_VERBOSE": "advanced.verbose",
        "CLAUDE_VERIFY_SSL": "advanced.verify_ssl",
    }
    
    def load(self) -> Dict[str, Any]:
        """加载并合并所有层级的配置"""
        config = self._deep_copy(self.DEFAULT_CONFIG)
        
        # 1. 加载文件配置（从低到高优先级）
        for path_str in self.CONFIG_PATHS:
            path = Path(path_str).expanduser()
            if path.exists():
                file_config = self._load_toml(path)
                config = self._deep_merge(config, file_config)
        
        # 2. 加载环境变量（最高优先级）
        env_config = self._load_env_vars()
        config = self._deep_merge(config, env_config)
        
        # 3. 后处理：拓扑自动检测
        if config["network"]["topology"] == "auto":
            config["network"]["proxy_url"] = self._detect_topology(config)
        
        return config
    
    def _load_toml(self, path: Path) -> Dict[str, Any]:
        """加载单个 TOML 文件"""
        try:
            with open(path, "rb") as f:
                return tomllib.load(f)
        except Exception as e:
            print(f"Warning: Failed to load {path}: {e}")
            return {}
    
    def _load_env_vars(self) -> Dict[str, Any]:
        """从环境变量构建配置字典"""
        config = {}
        for env_key, config_key in self.ENV_VAR_MAP.items():
            value = os.getenv(env_key)
            if value is not None:
                # 类型转换
                value = self._convert_type(config_key, value)
                # 设置嵌套键（如 "network.proxy_url" -> config["network"]["proxy_url"]）
                self._set_nested(config, config_key, value)
        return config
    
    def _convert_type(self, key: str, value: str) -> Any:
        """根据配置键转换环境变量类型"""
        if "timeout" in key or "retries" in key or "delay" in key:
            return int(value)
        if "verbose" in key or "verify_ssl" in key:
            return value.lower() in ("true", "1", "yes")
        return value
    
    def _set_nested(self, config: Dict, key: str, value: Any):
        """设置嵌套字典的值（如 "a.b.c" -> config["a"]["b"]["c"]）"""
        keys = key.split(".")
        target = config
        for k in keys[:-1]:
            target = target.setdefault(k, {})
        target[keys[-1]] = value
    
    def _deep_merge(self, base: Dict, override: Dict) -> Dict:
        """深度合并两个字典（override 优先级高）"""
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result
    
    def _deep_copy(self, obj: Any) -> Any:
        """深拷贝对象"""
        import copy
        return copy.deepcopy(obj)
    
    def _detect_topology(self, config: Dict) -> str:
        """自动检测网络拓扑并返回对应代理 URL"""
        topology_map = config["network"]["topology_map"]
        
        # 检测逻辑（优先级：local > internal > external）
        if self._check_localhost_proxy():
            return topology_map.get("local")
        elif self._check_internal_network():
            return topology_map.get("internal")
        else:
            return topology_map.get("external")
    
    def _check_localhost_proxy(self) -> bool:
        """检查本地代理是否可用"""
        import socket
        try:
            with socket.create_connection(("127.0.0.1", 4100), timeout=1):
                return True
        except:
            return False
    
    def _check_internal_network(self) -> bool:
        """检查是否在内网环境（如 10.x.x.x 网段）"""
        import socket
        try:
            hostname = socket.gethostname()
            ip = socket.gethostbyname(hostname)
            return ip.startswith("10.") or ip.startswith("192.168.")
        except:
            return False


# 使用示例
def get_config() -> Dict[str, Any]:
    """全局配置获取接口"""
    loader = ConfigLoader()
    return loader.load()


# 在 invoke_claude 工具中使用
def invoke_claude(prompt: str, model: Optional[str] = None, proxy: Optional[str] = None):
    config = get_config()
    
    # 参数优先级：函数参数 > 配置文件
    final_model = model or config["claude"]["default_model"]
    final_proxy = proxy or config["network"]["proxy_url"]
    final_timeout = config["network"]["default_timeout"]
    
    # ... 执行 Claude CLI 调用
```

### 5.2 合并示例演示

假设存在以下配置：

**1. 硬编码默认值：**
```python
{"network": {"proxy_url": None, "max_retries": 3, "timeout": 300}}
```

**2. 用户级配置 (`~/.config/claude/config.toml`)：**
```toml
[network]
proxy_url = "http://10.10.10.111:4100"
max_retries = 5
```

**3. 项目级配置 (`./.claude/config.toml`)：**
```toml
[network]
max_retries = 10
```

**4. 环境变量：**
```bash
export CLAUDE_TIMEOUT=600
```

**最终合并结果：**
```python
{
    "network": {
        "proxy_url": "http://10.10.10.111:4100",  # 来自用户级配置
        "max_retries": 10,                         # 来自项目级配置（覆盖用户级）
        "timeout": 600                             # 来自环境变量（最高优先级）
    }
}
```

---

## 六、向后兼容性保证

### 6.1 兼容策略

1. **无配置文件时回退默认值**
   - 所有配置项在 `DEFAULT_CONFIG` 中有默认值
   - 无需配置文件即可运行（保持当前硬编码行为）

2. **渐进式迁移**
   - 用户可逐步添加配置项，无需一次性配置所有选项
   - 部分配置的文件仍有效（与默认值合并）

3. **环境变量覆盖**
   - 临时需求无需修改配置文件，用环境变量即可

4. **配置文件可选**
   - 配置文件不存在时不报错，静默使用默认值
   - 方便 Docker/CI 环境部署

### 6.2 迁移路径

**当前硬编码方式（保持兼容）：**
```python
invoke_claude(prompt, proxy="http://10.10.10.111:4100")
```

**迁移后方式一（推荐）：**
```toml
# ~/.config/claude/config.toml
[network]
proxy_url = "http://10.10.10.111:4100"
```
```python
invoke_claude(prompt)  # 自动读取配置
```

**迁移后方式二（参数覆盖）：**
```python
invoke_claude(prompt, proxy="http://custom-proxy.local:8080")  # 仍支持显式参数
```

---

## 七、实施建议

### 7.1 开发阶段

1. **Phase 1：基础实现**
   - 实现 TOML 解析和多层级合并
   - 支持用户级和项目级配置
   - 添加环境变量覆盖

2. **Phase 2：高级特性**
   - 实现网络拓扑自动检测
   - 添加配置验证（schema validation）
   - 提供配置生成工具 (`claude config init`)

3. **Phase 3：生态集成**
   - 与 Hermes profiles 集成（`~/.hermes/profiles/kele/claude.toml`）
   - 添加配置热重载
   - 提供配置管理 CLI (`claude config get/set`)

### 7.2 文档和工具

**初始化工具：**
```bash
$ claude config init
创建配置文件到 ~/.config/claude/config.toml
选择网络环境：
  1) 本机 (127.0.0.1:4100)
  2) 内网 (10.10.10.111:4100)
  3) 公网 (llm.bai.one)
  4) 自定义
选择 [1]: 2
配置已保存！
```

**配置检查工具：**
```bash
$ claude config show
配置来源：
  ✓ 默认值
  ✓ 用户级: ~/.config/claude/config.toml
  ✗ 项目级: 未找到
  ✓ 环境变量: CLAUDE_TIMEOUT=600

当前配置：
  claude.bin_path: /usr/local/bin/claude
  claude.default_model: claude-3-7-sonnet-20250219
  network.proxy_url: http://10.10.10.111:4100 (来源: 用户级)
  network.timeout: 600 (来源: 环境变量)
  network.max_retries: 3 (来源: 默认值)
```

### 7.3 测试用例

```python
def test_config_priority():
    """测试配置优先级"""
    # 准备测试环境
    os.environ["CLAUDE_TIMEOUT"] = "600"
    write_config("~/.config/claude/config.toml", {"network": {"timeout": 300}})
    
    config = ConfigLoader().load()
    assert config["network"]["timeout"] == 600  # 环境变量优先

def test_default_fallback():
    """测试无配置时的默认值回退"""
    # 清空所有配置
    clean_all_configs()
    
    config = ConfigLoader().load()
    assert config["network"]["proxy_url"] is None
    assert config["network"]["max_retries"] == 3

def test_topology_auto_detect():
    """测试网络拓扑自动检测"""
    config = {"network": {"topology": "auto", "topology_map": {...}}}
    proxy = ConfigLoader()._detect_topology(config)
    assert proxy in ["http://127.0.0.1:4100", "http://10.10.10.111:4100", "https://llm.bai.one"]
```

---

## 八、总结

### 8.1 核心设计原则

1. **默认可用**：无配置文件时使用硬编码默认值，保证向后兼容
2. **灵活覆盖**：多层级配置 + 环境变量，适应不同场景
3. **简洁明确**：TOML 格式，类型安全，易于手工编辑
4. **自动适配**：支持网络拓扑检测，减少手动配置

### 8.2 配置层级总结

| 优先级 | 层级 | 路径示例 | 用途 |
|--------|------|----------|------|
| 1（最高） | 环境变量 | `CLAUDE_PROXY_URL` | 临时覆盖、CI/CD |
| 2 | 项目级 | `./.claude/config.toml` | 项目特定配置 |
| 3 | 用户级 | `~/.config/claude/config.toml` | 个人偏好 |
| 4 | 系统级 | `/etc/claude/config.toml` | 组织默认 |
| 5（最低） | 硬编码 | `DEFAULT_CONFIG` | 兜底保证 |

### 8.3 下一步行动

1. 实现 `ConfigLoader` 类到 Hermes 代码库
2. 修改 `invoke_claude` 工具集成配置加载器
3. 添加配置生成和验证工具
4. 编写用户文档和迁移指南
5. 在 FSPVE 内网环境测试多拓扑场景

---

**文档版本：** 1.0  
**最后更新：** 2026-06-20  
**适用范围：** Hermes invoke_claude 工具配置外部化  
**联系人：** Kele (Hermes profile)
