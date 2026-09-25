# Knowledge Retrieval

The zhihaol Markdown vault is the durable knowledge source. SQLite indexes are
read-only, rebuildable projections. Provider sessions own conversation context.

## Retrieval

- Prefer `python tools/user_tools/knowledge_router.py search "query" --json` when
  the Router is installed (`python`, not `python3`, which is a Store placeholder here).
  Use `contexts brief NAME --json` for a functional Context
  and `contexts state ID --json` for explicitly recorded state. These commands read
  the shared Router registry and event links, not a separate per-agent knowledge copy.
- Only if the Router is not installed, retain `tools/agent_tools/vault_search.py` for
  legacy read-only retrieval. Never treat an integrity failure as an empty result.
- Use the smallest source-backed passage that answers the question.
- A miss means no context; do not substitute unrelated legacy memory.

## Writes

The default vault integration is read-only. Write only when the user explicitly
asks and an authorized absolute vault root has been resolved and
containment-checked. Never infer a writable path from a relative search result.
Do not write to SQLite indexes or create a parallel database-only truth source.

Do not store one-off requests, temporary debugging noise, or duplicate facts.
Never narrate internal retrieval mechanics to the user.
