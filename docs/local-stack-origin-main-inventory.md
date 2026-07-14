# Fork Main Inventory

This inventory records the decision for every commit that was unique to
`origin/main` before resetting the fork to `upstream/main`. It is the evidence
required by the main-alignment gate in `local-stack-maintenance.md`.

| Fork commit | Decision | Current evidence |
|---|---|---|
| `56e57a1` | Covered upstream | Telegram `/command@bot` handling and tests are present in `messenger/telegram/media.py` and `tests/messenger/telegram/test_media.py`. |
| `a755d0f` | Covered upstream | `/ductor/` container-path resolution and Windows tests are present in `files/tags.py` and `tests/files/test_tags.py`. |
| `ab4e269` | Covered and strengthened upstream | Docker Codex stdin behavior is present in `cli/codex_provider.py` and `tests/cli/test_codex_provider.py`. |
| `25d4d20` | Drop | Merge topology only; its functional conflict resolutions are covered by the three fixes above. |
| `41c1999` | Drop | Merge topology only; its second parent is already an ancestor of current upstream. |
| `b7e5091` | Keep local to Git metadata | `.worktrees/` is ignored in `.git/info/exclude`; a personal worktree path does not belong in the product branch. |
| `b2e1ad0` | Drop | P0 state kernel, Soul, and automatic memory extraction conflict with the explicit vault-retrieval architecture. |
| `f351b22` | Partially superseded; drop remainder | Windows Zone 2 behavior and portable PM2 configuration were rebuilt independently; P0 and vendored design-skill content are intentionally removed. |
| `1de531a` | Covered upstream; drop P0 remainder | Its upstream bug fixes are ancestors of current upstream; the remaining changes only maintain the removed P0 runtime. |
| `8bbc5af` | Drop | Formatting and governance changes are limited to the removed P0 implementation. |
| `a7b4542` | Drop | Upstream merge topology plus conflict handling for the removed P0 runtime. |
| `73b805e` | Drop | WIP capability preselection, state migration, and automatic memory synthesis are intentional divergence reductions. |
| `ca5a4bb` | Drop | Integration merge of the removed P0 stack; no independent product behavior. |
| `14c42da` | Superseded | Upstream covers its Windows, Matrix, MIME, and sender fixes; local commit `9335858` independently preserves Gemini Docker auth selection. |

## Reset Gate

No required runtime behavior remains solely in these 14 commits. The fork can
be aligned only after the rebuilt local-stack passes its complete test suite,
is cut over in PM2, and the runtime identity matches the rebuilt branch HEAD.

The reset itself remains a deliberate operator action because it rewrites
`origin/main` with `--force-with-lease`.
