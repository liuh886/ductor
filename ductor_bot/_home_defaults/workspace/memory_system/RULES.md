# Knowledge Retrieval

The zhihaol Markdown vault is the durable knowledge source. SQLite indexes are
read-only, rebuildable projections. Provider sessions own conversation context.

## Retrieval

- Use `python3 tools/agent_tools/vault_search.py "query"` for durable project
  or domain knowledge only when the current task needs it.
- Use the smallest source-backed passage that answers the question.
- A miss means no context; do not substitute unrelated legacy memory.

## Writes

Write durable facts, preferences, and decisions to the relevant vault/project
Markdown file only when the user asks or the workflow explicitly requires it.
Do not write to SQLite indexes or create a parallel database-only truth source.

Do not store one-off requests, temporary debugging noise, or duplicate facts.
Never narrate internal retrieval mechanics to the user.
