# Hermes Integration Guide

## Overview

The supported Hermes integration no longer uses manual source patching or repo-path imports from a git worktree. The current safe contract is:

- Deploy the native bridge with `scripts/deploy-invoke-claude-native.sh`
- The installed bridge default path is `/usr/local/lib/hermes-agent/native/invoke_claude/bridge.py`
- The Hermes worker/plugin integration must come from `hermes-claude-plugin`, not ad hoc text injection into `claude_worker_lib.py`
- `INVOKE_CLAUDE_NATIVE=1` stays in the parent profile `.env`
- Claude child-process credentials stay in `HERMES_HOME/claude-plugin.env` only

## Safe Integration Contract

### 1. Deploy the bridge with the supported script

Use the repo deployment script instead of copying files or editing Hermes package code manually:

```bash
bash scripts/deploy-invoke-claude-native.sh
```

Optional explicit paths:

```bash
bash scripts/deploy-invoke-claude-native.sh \
  --hermes-venv /usr/local/lib/hermes-agent/venv \
  --repo-path /opt/workspace/git/invoke-claude-enhancement \
  --runtime-dir /usr/local/lib/hermes-agent/native/invoke_claude
```

The default installed runtime location is fixed to:

```text
/usr/local/lib/hermes-agent/native/invoke_claude/bridge.py
```

Do not point Hermes at `src/python/bridge.py` inside this repo in production.

### 2. Use the plugin-based worker contract

The worker-side integration is expected to come from `hermes-claude-plugin`. The safe worker contract is a plugin-aware Hermes build that already includes native bridge loading behavior such as:

- `ClaudePluginResolver`
- `_load_native_bridge`
- `invoke_claude(... config_path=...)`
- `api_key=resolved_api_key`

If those markers are missing, fix the Hermes worker/plugin version first. Do not manually inject import blocks, `sys.path` edits, or bridge snippets into `tools/claude_worker_lib.py`.

### 3. Keep feature flag in the parent env only

Enable native mode in the parent profile `.env`:

```bash
INVOKE_CLAUDE_NATIVE=1
```

This flag must remain in the parent profile environment. Do not move it into `claude-plugin.env`.

### 4. Keep Claude credentials child-only

Create the child-only plugin env file with `0600` permissions:

```bash
export HERMES_HOME=/usr/local/lib/hermes-agent
install -m 0600 /dev/null "$HERMES_HOME/claude-plugin.env"
```

Allowed keys in `HERMES_HOME/claude-plugin.env`:

```bash
CLAUDE_API_KEY=sk-...
CLAUDE_PROXY_URL=http://10.10.10.111:4100
CLAUDE_MODEL=Klite
```

Only the Claude child process should read that file. Do not put `INVOKE_CLAUDE_NATIVE`, unrelated parent settings, or extra secrets there.

## Validation

After deployment, verify the Hermes worker can import the installed bridge path cleanly:

```bash
/usr/local/lib/hermes-agent/venv/bin/python -c '
from tools.claude_worker_lib import invoke_claude
print(callable(invoke_claude))
'
```

If validation fails, check:

- The worker comes from a `hermes-claude-plugin` version with the required plugin markers
- The runtime bridge exists at `/usr/local/lib/hermes-agent/native/invoke_claude/bridge.py`
- `INVOKE_CLAUDE_NATIVE=1` is still in the parent profile `.env`
- `HERMES_HOME/claude-plugin.env` contains only `CLAUDE_API_KEY`, `CLAUDE_PROXY_URL`, and `CLAUDE_MODEL`

## Removed Unsafe Guidance

The following older instructions are obsolete and should not be used:

- Manually patching `tools/claude_worker_lib.py`
- Adding inline `sys.path.insert(...)` hacks to a git checkout
- Importing `bridge` directly from `/opt/workspace/git/invoke-claude-enhancement/src/python`
- Treating worker integration as text injection rather than a plugin/runtime contract
- Clearing `.pyc` files as part of a manual patch workflow
