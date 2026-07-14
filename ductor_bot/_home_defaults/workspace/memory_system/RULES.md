# Knowledge Retrieval

The zhihaol Markdown vault is the durable knowledge source. SQLite indexes are
read-only, rebuildable projections. Provider sessions own conversation context.

## Retrieval

- Use `python3 tools/agent_tools/vault_search.py "query"` for durable project
  or domain knowledge only when the current task needs it.
- Use the smallest source-backed passage that answers the question.
- A miss means no context; do not substitute unrelated legacy memory.

## Writes

The default vault integration is read-only. Write only when the user explicitly
asks and an authorized absolute vault root has been resolved and
containment-checked. Never infer a writable path from a relative search result.
Do not write to SQLite indexes or create a parallel database-only truth source.

Do not store one-off requests, temporary debugging noise, or duplicate facts.
Never narrate internal retrieval mechanics to the user.
