# Agent Tools

Tools for inter-agent communication and shared knowledge.
Agent creation and removal are only available on the main agent.

## Available Tools

| Tool | Purpose | Availability |
|------|---------|-------------|
| `ask_agent.py` | Send a synchronous message to another agent (blocks until response) | All agents |
| `ask_agent_async.py` | Send an async message (returns immediately, response via Telegram) | All agents |
| `edit_shared_knowledge.py` | View or edit SHAREDMEMORY.md (synced to all agents) | All agents |
| `create_agent.py` | Create a new sub-agent (writes to `agents.json`, auto-detected) | Main only |
| `remove_agent.py` | Remove a sub-agent from the registry | Main only |
| `list_agents.py` | List all sub-agents and their configuration | Main only |

## Creating Sub-Agents

When creating a sub-agent:

1. The user **must provide** a Telegram bot token (created via @BotFather)
2. Choose a descriptive lowercase name (no spaces, e.g. `finanzius`, `codex`)
3. Configure provider and model based on the agent's purpose
4. The workspace is created automatically under `agents/<name>/`
5. The sub-agent starts automatically within seconds (FileWatcher)

```bash
python3 tools/agent_tools/create_agent.py \
  --name "agent-name" \
  --token "BOT_TOKEN" \
  --users "USER_ID1,USER_ID2" \
  [--provider claude] \
  [--model sonnet]
```

## Inter-Agent Communication

Each agent has its own memory, workspace, and session — they are independent.
Two modes are available:

### Synchronous (blocking)

Use `ask_agent.py` for quick lookups or simple questions. Your CLI turn
blocks until the response arrives.

```bash
python3 tools/agent_tools/ask_agent.py "agent-name" "Quick question"
```

### Asynchronous (fire-and-forget)

Use `ask_agent_async.py` for tasks that may take longer. Returns immediately
with a task_id. The response is delivered to your Telegram chat when ready.

```bash
python3 tools/agent_tools/ask_agent_async.py "agent-name" "Complex request that takes time"
```

Use async for code generation, analysis, research, or anything that may
take more than a few seconds. Use sync for quick lookups.

## Shared Knowledge

`SHAREDMEMORY.md` contains knowledge shared across all agents. Changes are
automatically synced into every agent's MAINMEMORY.md via the supervisor.

```bash
# View current shared knowledge
python3 tools/agent_tools/edit_shared_knowledge.py --show

# Append a fact
python3 tools/agent_tools/edit_shared_knowledge.py --append "New shared fact"

# Replace entire content
python3 tools/agent_tools/edit_shared_knowledge.py --set "Full new content"
```

When you learn something that is relevant to ALL agents (server facts, user
preferences, infrastructure changes), update shared knowledge instead of
only your own MAINMEMORY.md.

## Removing Sub-Agents

Removing a sub-agent stops its Telegram bot but **preserves its workspace**.
The workspace can be reused if the agent is re-created with the same name.

```bash
python3 tools/agent_tools/remove_agent.py "agent-name"
```
