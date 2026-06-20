# Claude Code + LiteLLM 错误处理与重试策略

## 概述

本文档分析 Claude Code CLI 通过 LiteLLM Proxy 调用时的常见错误场景，并为每种错误类型设计相应的重试策略。

## 架构背景

```
invoke_claude 工具 → Claude Code CLI → ANTHROPIC_BASE_URL → LiteLLM Proxy (http://10.10.10.111:4100) → 上游模型
```

**关键问题**：
- 认证失败（密钥配置错误）
- 限流（上游模型过载）
- 网络不稳定（FSPVE 内网环境）
- 上游服务故障（LiteLLM proxy 重启/崩溃）

---

## 错误类型分析与重试策略

### 1. 认证错误（401/403）

#### 错误特征
- **HTTP 401 Unauthorized**: API 密钥无效、过期或缺失
- **HTTP 403 Forbidden**: 密钥有效但无权限访问资源

#### 错误原因
- `ANTHROPIC_API_KEY` 环境变量未设置
- LiteLLM Proxy 配置的密钥错误
- 密钥已被撤销或过期

#### 重试策略
- **是否可重试**: ❌ **否**
- **原因**: 认证问题是配置错误，重试不会解决问题
- **处理方式**: 立即失败，返回明确错误信息

#### 错误信息建议
```
认证失败 (401/403): 
- 检查 ANTHROPIC_API_KEY 环境变量是否正确设置
- 验证 LiteLLM Proxy 配置 (http://10.10.10.111:4100)
- 确认密钥未过期且有足够权限
```

---

### 2. 限流错误（429 Too Many Requests）

#### 错误特征
- **HTTP 429**: 请求速率超过限制
- 响应头可能包含 `Retry-After` 字段

#### 错误原因
- 上游模型 API 配额耗尽
- 并发请求过多
- 短时间内请求频率过高

#### 重试策略
- **是否可重试**: ✅ **是**
- **重试次数**: 5 次
- **退避策略**: 指数退避 + `Retry-After` 优先
- **基础延迟**: 2 秒
- **最大延迟**: 60 秒
- **抖动**: ±25% 随机抖动避免惊群效应

#### 计算公式
```python
if 'Retry-After' in response.headers:
    delay = int(response.headers['Retry-After'])
else:
    delay = min(base_delay * (2 ** attempt) + random.uniform(-jitter, jitter), max_delay)
```

---

### 3. 网络超时（Timeout）

#### 错误特征
- **连接超时**: 无法在指定时间内建立 TCP 连接
- **读取超时**: 连接建立后，等待响应超时

#### 错误原因
- FSPVE 内网网络抖动
- LiteLLM Proxy 负载过高响应慢
- 上游模型处理时间过长

#### 重试策略
- **是否可重试**: ✅ **是**
- **重试次数**: 3 次
- **退避策略**: 线性增长
- **基础延迟**: 5 秒
- **增量**: 每次 +5 秒（5s, 10s, 15s）
- **超时设置**: 
  - 连接超时: 10 秒
  - 读取超时: 120 秒（Claude 生成可能较慢）

---

### 4. 连接拒绝（Connection Refused）

#### 错误特征
- **ECONNREFUSED**: 目标端口未监听
- **errno 111** (Linux)

#### 错误原因
- LiteLLM Proxy 服务未启动
- 防火墙阻止连接
- 端口配置错误（非 4100）

#### 重试策略
- **是否可重试**: ⚠️ **有限重试**
- **重试次数**: 2 次
- **退避策略**: 快速重试
- **延迟**: 1 秒固定延迟
- **原因**: 如果服务刚重启可能需要几秒初始化，但多次失败说明服务未运行

#### 错误信息建议
```
连接被拒绝:
- LiteLLM Proxy 可能未运行，检查服务状态
- 验证地址: http://10.10.10.111:4100
- 检查防火墙规则和网络连通性
```

---

### 5. 上游故障（502/503）

#### 错误特征
- **HTTP 502 Bad Gateway**: LiteLLM Proxy 无法从上游获取有效响应
- **HTTP 503 Service Unavailable**: 服务暂时不可用（维护、过载）

#### 错误原因
- LiteLLM Proxy 正在重启
- 上游模型 API 故障
- Proxy 配置错误导致无法连接上游

#### 重试策略
- **是否可重试**: ✅ **是**
- **重试次数**: 4 次
- **退避策略**: 指数退避
- **基础延迟**: 3 秒
- **最大延迟**: 45 秒
- **抖动**: ±20%

---

### 6. 其他网络错误

#### 错误特征
- **DNS 解析失败**: 无法解析主机名
- **连接重置**: Connection reset by peer
- **SSL/TLS 错误**: 证书验证失败

#### 重试策略
- **DNS 失败**: ❌ 不重试（配置错误）
- **连接重置**: ✅ 重试 2 次，延迟 2 秒
- **SSL 错误**: ❌ 不重试（证书问题）

---

## Python 实现示例

### 方案 1: 装饰器实现

```python
import time
import random
import functools
from typing import Callable, Type, Tuple
import requests
from requests.exceptions import Timeout, ConnectionError

class RetryConfig:
    """重试配置"""
    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential: bool = True,
        jitter: float = 0.25
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential = exponential
        self.jitter = jitter

# 错误类型到重试配置的映射
ERROR_RETRY_STRATEGIES = {
    401: RetryConfig(max_retries=0),  # 认证错误不重试
    403: RetryConfig(max_retries=0),  # 权限错误不重试
    429: RetryConfig(max_retries=5, base_delay=2.0, max_delay=60.0, jitter=0.25),
    502: RetryConfig(max_retries=4, base_delay=3.0, max_delay=45.0, jitter=0.20),
    503: RetryConfig(max_retries=4, base_delay=3.0, max_delay=45.0, jitter=0.20),
    504: RetryConfig(max_retries=3, base_delay=5.0, max_delay=30.0),
    'timeout': RetryConfig(max_retries=3, base_delay=5.0, exponential=False),  # 线性增长
    'connection_refused': RetryConfig(max_retries=2, base_delay=1.0, exponential=False),
    'connection_reset': RetryConfig(max_retries=2, base_delay=2.0),
}

def calculate_delay(attempt: int, config: RetryConfig, retry_after: int = None) -> float:
    """计算重试延迟"""
    if retry_after:
        return min(retry_after, config.max_delay)
    
    if config.exponential:
        delay = config.base_delay * (2 ** attempt)
    else:
        delay = config.base_delay * (attempt + 1)
    
    # 添加抖动
    if config.jitter > 0:
        jitter_amount = delay * config.jitter
        delay += random.uniform(-jitter_amount, jitter_amount)
    
    return min(delay, config.max_delay)

def get_error_message(status_code: int = None, error_type: str = None) -> str:
    """获取用户友好的错误信息"""
    messages = {
        401: "认证失败: 请检查 ANTHROPIC_API_KEY 环境变量",
        403: "权限被拒绝: 密钥无权限访问该资源",
        429: "请求过于频繁: 已达到速率限制，请稍后重试",
        502: "上游服务错误: LiteLLM Proxy 无法连接到上游模型",
        503: "服务暂时不可用: LiteLLM Proxy 可能正在重启",
        'timeout': "请求超时: 网络不稳定或服务响应过慢",
        'connection_refused': "连接被拒绝: LiteLLM Proxy (http://10.10.10.111:4100) 可能未运行",
    }
    return messages.get(status_code or error_type, "未知错误")

def should_retry(exception: Exception, response=None) -> Tuple[bool, str, RetryConfig]:
    """判断是否应该重试"""
    # HTTP 状态码错误
    if response and hasattr(response, 'status_code'):
        status = response.status_code
        if status in ERROR_RETRY_STRATEGIES:
            config = ERROR_RETRY_STRATEGIES[status]
            return config.max_retries > 0, str(status), config
    
    # 网络异常
    if isinstance(exception, Timeout):
        config = ERROR_RETRY_STRATEGIES['timeout']
        return True, 'timeout', config
    
    if isinstance(exception, ConnectionError):
        error_str = str(exception).lower()
        if 'refused' in error_str:
            config = ERROR_RETRY_STRATEGIES['connection_refused']
            return True, 'connection_refused', config
        elif 'reset' in error_str:
            config = ERROR_RETRY_STRATEGIES['connection_reset']
            return True, 'connection_reset', config
    
    # 默认不重试
    return False, 'unknown', RetryConfig(max_retries=0)

def retry_with_strategy(func: Callable) -> Callable:
    """
    智能重试装饰器，根据错误类型自动选择重试策略
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        attempt = 0
        last_exception = None
        last_response = None
        
        while True:
            try:
                response = func(*args, **kwargs)
                
                # 检查 HTTP 响应状态
                if hasattr(response, 'status_code') and response.status_code >= 400:
                    should_retry_flag, error_type, config = should_retry(None, response)
                    
                    if not should_retry_flag or attempt >= config.max_retries:
                        # 构建详细错误信息
                        error_msg = get_error_message(status_code=response.status_code)
                        raise Exception(f"{error_msg} (HTTP {response.status_code})")
                    
                    # 计算延迟（优先使用 Retry-After）
                    retry_after = response.headers.get('Retry-After')
                    retry_after = int(retry_after) if retry_after and retry_after.isdigit() else None
                    delay = calculate_delay(attempt, config, retry_after)
                    
                    print(f"⚠️  HTTP {response.status_code} - 重试 {attempt + 1}/{config.max_retries}，等待 {delay:.1f}秒...")
                    time.sleep(delay)
                    attempt += 1
                    last_response = response
                    continue
                
                return response
                
            except Exception as e:
                should_retry_flag, error_type, config = should_retry(e)
                last_exception = e
                
                if not should_retry_flag or attempt >= config.max_retries:
                    error_msg = get_error_message(error_type=error_type)
                    raise Exception(f"{error_msg}: {str(e)}") from e
                
                delay = calculate_delay(attempt, config)
                print(f"⚠️  {error_type} - 重试 {attempt + 1}/{config.max_retries}，等待 {delay:.1f}秒...")
                time.sleep(delay)
                attempt += 1
        
        # 理论上不会到达这里
        if last_exception:
            raise last_exception
        if last_response:
            return last_response
    
    return wrapper

# 使用示例
@retry_with_strategy
def call_claude_via_litellm(prompt: str, timeout: int = 120) -> dict:
    """调用 Claude Code CLI"""
    response = requests.post(
        'http://10.10.10.111:4100/v1/messages',
        headers={
            'anthropic-version': '2023-06-01',
            'x-api-key': os.environ.get('ANTHROPIC_API_KEY', ''),
            'content-type': 'application/json'
        },
        json={
            'model': 'claude-3-5-sonnet-20241022',
            'max_tokens': 4096,
            'messages': [{'role': 'user', 'content': prompt}]
        },
        timeout=(10, timeout)  # (连接超时, 读取超时)
    )
    return response
```

---

### 方案 2: 上下文管理器实现

```python
import subprocess
from contextlib import contextmanager
from typing import Generator, Optional

class RetryContext:
    """重试上下文管理器"""
    def __init__(self, operation_name: str = "操作"):
        self.operation_name = operation_name
        self.attempt = 0
        self.max_attempts = 3
        self.should_continue = True
    
    def retry(self, error_type: str, config: RetryConfig) -> bool:
        """判断是否继续重试"""
        if self.attempt >= config.max_retries:
            return False
        
        delay = calculate_delay(self.attempt, config)
        print(f"⚠️  {self.operation_name} 失败 ({error_type}) - "
              f"重试 {self.attempt + 1}/{config.max_retries}，等待 {delay:.1f}秒...")
        time.sleep(delay)
        self.attempt += 1
        return True

@contextmanager
def retry_context(operation_name: str = "操作") -> Generator[RetryContext, None, None]:
    """提供重试上下文"""
    ctx = RetryContext(operation_name)
    try:
        yield ctx
    finally:
        pass

# 使用示例
def call_claude_cli_with_retry(prompt: str) -> str:
    """使用上下文管理器实现重试"""
    with retry_context("Claude CLI 调用") as ctx:
        while True:
            try:
                result = subprocess.run(
                    ['claude', '--prompt', prompt],
                    env={
                        **os.environ,
                        'ANTHROPIC_BASE_URL': 'http://10.10.10.111:4100'
                    },
                    capture_output=True,
                    text=True,
                    timeout=120
                )
                
                # 检查返回码
                if result.returncode != 0:
                    stderr = result.stderr.lower()
                    
                    # 解析错误类型
                    if 'authentication' in stderr or '401' in stderr or '403' in stderr:
                        raise Exception(get_error_message(401))
                    
                    elif '429' in stderr or 'rate limit' in stderr:
                        if not ctx.retry('429', ERROR_RETRY_STRATEGIES[429]):
                            raise Exception(get_error_message(429))
                        continue
                    
                    elif 'connection refused' in stderr:
                        if not ctx.retry('connection_refused', 
                                       ERROR_RETRY_STRATEGIES['connection_refused']):
                            raise Exception(get_error_message(error_type='connection_refused'))
                        continue
                    
                    elif '502' in stderr or '503' in stderr:
                        status = 502 if '502' in stderr else 503
                        if not ctx.retry(str(status), ERROR_RETRY_STRATEGIES[status]):
                            raise Exception(get_error_message(status))
                        continue
                    
                    else:
                        raise Exception(f"Claude CLI 失败: {result.stderr}")
                
                return result.stdout
            
            except subprocess.TimeoutExpired:
                if not ctx.retry('timeout', ERROR_RETRY_STRATEGIES['timeout']):
                    raise Exception(get_error_message(error_type='timeout'))
                continue
            
            except Exception as e:
                # 未预期的异常，不重试
                raise
```

---

### 方案 3: 简化的装饰器（适合快速集成）

```python
def simple_retry(max_retries: int = 3, base_delay: float = 2.0):
    """简化版重试装饰器"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    error_str = str(e).lower()
                    
                    # 不可重试的错误
                    if any(x in error_str for x in ['401', '403', 'authentication', 'unauthorized']):
                        raise
                    
                    # 最后一次尝试
                    if attempt == max_retries:
                        raise
                    
                    # 计算延迟
                    delay = base_delay * (2 ** attempt)
                    print(f"重试 {attempt + 1}/{max_retries}，等待 {delay:.1f}秒...")
                    time.sleep(delay)
            
        return wrapper
    return decorator

# 使用
@simple_retry(max_retries=3, base_delay=2.0)
def invoke_claude(prompt: str) -> str:
    # 调用逻辑
    pass
```

---

## 集成到现有工具的建议

### invoke_claude 工具改造

```python
def invoke_claude_robust(prompt: str, timeout: int = 120) -> dict:
    """
    健壮的 Claude 调用，内置重试逻辑
    
    Returns:
        dict: {
            'success': bool,
            'output': str,
            'error': str | None,
            'attempts': int
        }
    """
    @retry_with_strategy
    def _call():
        result = subprocess.run(
            ['claude', '--prompt', prompt],
            env={
                **os.environ,
                'ANTHROPIC_BASE_URL': 'http://10.10.10.111:4100',
                'ANTHROPIC_API_KEY': os.environ.get('ANTHROPIC_API_KEY', '')
            },
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        if result.returncode != 0:
            # 解析 stderr 中的 HTTP 状态码
            stderr = result.stderr
            if '401' in stderr:
                raise requests.exceptions.HTTPError(response=type('obj', (), {'status_code': 401})())
            elif '429' in stderr:
                raise requests.exceptions.HTTPError(response=type('obj', (), {'status_code': 429})())
            elif 'connection refused' in stderr.lower():
                raise ConnectionError('Connection refused')
            # ... 其他错误解析
        
        return result
    
    try:
        result = _call()
        return {
            'success': True,
            'output': result.stdout,
            'error': None,
            'attempts': 1  # 可以从装饰器传递
        }
    except Exception as e:
        return {
            'success': False,
            'output': '',
            'error': str(e),
            'attempts': 0
        }
```

---

## 监控与日志建议

### 关键指标记录

```python
import logging
from datetime import datetime

class RetryLogger:
    """重试监控日志"""
    def __init__(self):
        self.logger = logging.getLogger('claude_retry')
        self.metrics = {
            'total_calls': 0,
            'successful_calls': 0,
            'failed_calls': 0,
            'retry_counts': {},
            'error_types': {}
        }
    
    def log_attempt(self, attempt: int, error_type: str, delay: float):
        """记录重试尝试"""
        self.logger.warning(
            f"[{datetime.now().isoformat()}] 重试 attempt={attempt} "
            f"error={error_type} delay={delay:.2f}s"
        )
    
    def log_success(self, attempts: int):
        """记录成功"""
        self.metrics['total_calls'] += 1
        self.metrics['successful_calls'] += 1
        if attempts > 1:
            self.metrics['retry_counts'][attempts] = \
                self.metrics['retry_counts'].get(attempts, 0) + 1
    
    def log_failure(self, error_type: str, attempts: int):
        """记录最终失败"""
        self.metrics['total_calls'] += 1
        self.metrics['failed_calls'] += 1
        self.metrics['error_types'][error_type] = \
            self.metrics['error_types'].get(error_type, 0) + 1
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        success_rate = (self.metrics['successful_calls'] / 
                       max(self.metrics['total_calls'], 1) * 100)
        return {
            **self.metrics,
            'success_rate': f"{success_rate:.2f}%"
        }
```

---

## 测试建议

### 模拟错误场景

```python
import pytest
from unittest.mock import Mock, patch

def test_retry_on_429():
    """测试 429 限流重试"""
    mock_response = Mock()
    mock_response.status_code = 429
    mock_response.headers = {'Retry-After': '2'}
    
    call_count = 0
    
    @retry_with_strategy
    def mock_call():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return mock_response
        mock_response.status_code = 200
        return mock_response
    
    result = mock_call()
    assert call_count == 3
    assert result.status_code == 200

def test_no_retry_on_401():
    """测试 401 不重试"""
    mock_response = Mock()
    mock_response.status_code = 401
    
    @retry_with_strategy
    def mock_call():
        return mock_response
    
    with pytest.raises(Exception) as exc:
        mock_call()
    
    assert '认证失败' in str(exc.value)

def test_exponential_backoff():
    """测试指数退避"""
    config = RetryConfig(base_delay=1.0, exponential=True, jitter=0)
    
    delays = [calculate_delay(i, config) for i in range(5)]
    expected = [1.0, 2.0, 4.0, 8.0, 16.0]
    
    assert delays == expected
```

---

## 配置建议

### 环境变量配置

```bash
# 重试配置
export CLAUDE_RETRY_MAX_ATTEMPTS=5
export CLAUDE_RETRY_BASE_DELAY=2.0
export CLAUDE_RETRY_MAX_DELAY=60.0

# 超时配置
export CLAUDE_CONNECTION_TIMEOUT=10
export CLAUDE_READ_TIMEOUT=120

# LiteLLM Proxy 配置
export ANTHROPIC_BASE_URL=http://10.10.10.111:4100
export ANTHROPIC_API_KEY=your_key_here
```

### 配置文件示例

```yaml
# claude_config.yaml
retry:
  strategies:
    authentication_error:  # 401/403
      max_retries: 0
      fail_fast: true
    
    rate_limit:  # 429
      max_retries: 5
      base_delay: 2.0
      max_delay: 60.0
      backoff: exponential
      jitter: 0.25
    
    timeout:
      max_retries: 3
      base_delay: 5.0
      backoff: linear
    
    upstream_error:  # 502/503
      max_retries: 4
      base_delay: 3.0
      max_delay: 45.0
      backoff: exponential
    
    connection_refused:
      max_retries: 2
      base_delay: 1.0
      backoff: linear

timeouts:
  connect: 10
  read: 120

litellm:
  base_url: http://10.10.10.111:4100
  health_check_endpoint: /health
```

---

## 总结

### 关键原则

1. **区分可重试与不可重试错误**：认证错误立即失败，网络问题智能重试
2. **遵守 Retry-After**：429 响应时优先使用服务端指定的延迟
3. **指数退避 + 抖动**：避免惊群效应，分散重试流量
4. **合理的超时设置**：连接快速超时，读取给予足够时间
5. **明确的错误信息**：失败时提供可操作的诊断建议
6. **监控与可观测性**：记录重试次数、错误类型、成功率

### 推荐方案

- **生产环境**：使用 **方案 1（装饰器实现）**，功能完整、可配置性强
- **快速集成**：使用 **方案 3（简化装饰器）**，代码简洁、易于理解
- **复杂流程**：使用 **方案 2（上下文管理器）**，精细控制重试逻辑

### 下一步行动

1. 在 `invoke_claude` 工具中集成重试装饰器
2. 添加日志记录，监控重试频率和失败模式
3. 配置环境变量，支持动态调整重试参数
4. 添加健康检查，启动时验证 LiteLLM Proxy 可达性
5. 编写单元测试，覆盖各种错误场景
