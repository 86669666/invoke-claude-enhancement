# Hermes Integration Guide

## Overview

The Python native implementation can be integrated into Hermes Agent's `invoke_claude` tool to eliminate Node.js startup overhead and reduce memory footprint.

## Performance Improvements

Based on real-world benchmarks:

| Metric | Native (Python) | Node.js | Improvement |
|--------|----------------|---------|-------------|
| Avg Latency | 2.391s | 2.841s | **+15.8%** |
| Memory Delta | +9.5 MB | +0.0 MB | Lower baseline |
| Startup Overhead | ~0ms | ~200-500ms | Eliminated |

**Note:** Memory delta measures incremental increase per call. Native implementation shows +9.5 MB due to loading Python modules on first call; subsequent calls reuse loaded modules. Node.js shows 0 MB delta because it spawns separate processes that don't affect parent memory measurement.

## Integration Steps

### 1. Locate Hermes Tool

Find the `invoke_claude` function in Hermes codebase:
```bash
find /usr/local/lib/hermes-agent -name "claude_worker_lib.py" -o -name "*invoke*claude*.py"
```

Typical path: `/usr/local/lib/hermes-agent/venv/lib/python3.11/site-packages/tools/claude_worker_lib.py`

### 2. Add Bridge Logic

Insert the following code at the start of the `invoke_claude()` function (after parameter validation, before the Node.js CLI invocation):

```python
# [Bridge] Check if native Python implementation is enabled
native_env = os.environ.get("INVOKE_CLAUDE_NATIVE", "").lower()
if native_env in ("1", "true", "yes"):
    try:
        import sys
        sys.path.insert(0, "/opt/workspace/git/invoke-claude-enhancement/src/python")
        from bridge import use_native_implementation, invoke_claude_native, NATIVE_IMPL_AVAILABLE
        
        if NATIVE_IMPL_AVAILABLE:
            logger.info("Using native Python implementation")
            return invoke_claude_native(
                prompt=prompt,
                workdir=workdir,
                model=model,
                effort=effort,
                timeout=timeout,
                max_turns=max_turns,
                allowed_tools=allowed_tools,
                disallowed_tools=disallowed_tools,
                output_format=output_format,
                proxy_url=proxy_url,
            )
        else:
            logger.warning("INVOKE_CLAUDE_NATIVE=1 but native implementation not available, falling back to Node.js")
    except Exception as e:
        logger.warning(f"Failed to use native implementation: {e}, falling back to Node.js")

# [Original] Fallback to Node.js CLI path
# ... existing Node.js invocation code ...
```

### 3. Install Dependencies

Ensure the Hermes venv has required packages:
```bash
/usr/local/lib/hermes-agent/venv/bin/pip install httpx tenacity structlog
```

### 4. Clear Python Cache

After modifying `claude_worker_lib.py`:
```bash
find /usr/local/lib/hermes-agent/venv -name "*.pyc" -delete
```

### 5. Test Native Path

```bash
export ANTHROPIC_API_KEY="sk-..."
export INVOKE_CLAUDE_NATIVE=1

python3 << 'PYTHON'
import sys
sys.path.insert(0, "/usr/local/lib/hermes-agent/venv/lib/python3.11/site-packages")
from tools.claude_worker_lib import invoke_claude

result = invoke_claude(
    prompt="What is 2+2?",
    workdir="/tmp",
    model="Klite",
    proxy_url="http://10.10.10.111:4100",
)
print(f"Status: {result['status']}")
print(f"Native: {result.get('native_impl', False)}")
print(f"Output: {result['output'][:100]}")
PYTHON
```

Expected output:
```
Status: completed
Native: True
Output: 2 + 2 equals 4.
```

### 6. Test Backward Compatibility

Unset the environment variable to verify fallback:
```bash
unset INVOKE_CLAUDE_NATIVE

python3 << 'PYTHON'
# ... same test code ...
PYTHON
```

Expected output should include `'cmd'` key (Node.js path indicator).

## Usage in Hermes

Once integrated, Hermes users can enable native implementation globally or per-session:

```bash
# Enable for current shell session
export INVOKE_CLAUDE_NATIVE=1

# Enable in Hermes config (system-wide)
echo 'export INVOKE_CLAUDE_NATIVE=1' >> /etc/hermes/env

# Enable for specific Hermes profile
echo 'export INVOKE_CLAUDE_NATIVE=1' >> ~/.hermes/profiles/<profile>/env
```

## Troubleshooting

**Issue:** `ModuleNotFoundError: No module named 'bridge'`
- Verify the repo path in `sys.path.insert()` matches your clone location
- Check file exists: `/opt/workspace/git/invoke-claude-enhancement/src/python/bridge.py`

**Issue:** Native implementation not activating
- Verify environment variable: `echo $INVOKE_CLAUDE_NATIVE`
- Check Hermes logs for "Using native Python implementation" message
- Ensure no typos in the bridge integration code

**Issue:** API authentication failure
- Verify `ANTHROPIC_API_KEY` is set correctly
- For won LiteLLM, ensure correct base_url and virtual key

## Model Compatibility

Won LiteLLM available models:
- `Klite` — Claude Sonnet (standard)
- `KADV` — Claude Opus (advanced)
- `KMID` — Claude Haiku (lightweight)
- `EYES` — Claude with vision

Standard Anthropic model names (e.g., `claude-sonnet-4-20250514`) are **not** supported on won LiteLLM. Use the short names above.

## Next Steps

- **Production deployment:** Consider installing this repo as a Python package in Hermes venv
- **Performance monitoring:** Add metrics collection to track latency/memory in production
- **Configuration:** Move repo path to Hermes config instead of hardcoding in bridge logic
