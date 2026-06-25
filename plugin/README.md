# GitKB for Codex

GitKB gives Codex local code intelligence first: symbols, callers, callees, impact analysis, dead-code detection, semantic search, and service-edge queries through the `git-kb` CLI and MCP tools. Full GitKB setup adds the persistent knowledge graph for tasks, specs, decisions, architecture, and team sync.

## First Value

Install the plugin, open a Git repo, and ask Codex about the code:

- "What calls `authenticate()`?"
- "What's the blast radius of changing `src/auth.rs`?"
- "Find likely dead code in this project."
- "Index this repo with GitKB and summarize language support."

Codex should prefer local commands such as:

```bash
git-kb code doctor --json
git-kb code index
git-kb code symbols --json
git-kb code callers "<symbol>" --json
git-kb code callees "<symbol>" --json
git-kb code impact path/to/file --json
git-kb code dead --json
git-kb ai semantic "query" --json
```

Raw text search remains appropriate for exact strings, logs, config, docs, generated output, snapshots, and one-off file checks.

## Prerequisites

Install GitKB:

```bash
brew install gitkb/tap/gitkb
# or
curl -fsSL https://get.gitkb.com/install.sh | bash
```

If `git-kb` is missing, the plugin hooks fail open. Codex can still guide installation from the bundled skill.

## Optional Full KB Setup

When you want persistent tasks, project knowledge, sync, or the full repo-local Codex assets, initialize GitKB in the repo:

```bash
git-kb init
git-kb init codex
```

`git-kb init codex` remains the source of truth for canonical repo-local GitKB Codex assets. This marketplace plugin is a thin wrapper that delegates hooks to `git-kb hook codex`.

## What This Plugin Ships

- one focused code-intelligence skill;
- Codex lifecycle hooks that call `git-kb hook codex`;
- MCP configuration for `git-kb mcp`;
- no external harness-specific behavior;
- no vendored copy of the full canonical `git-kb init codex` skill set.

## Documentation

- [Getting Started](https://gitkb.com/docs/getting-started/quick-start/)
- [Code Intelligence](https://gitkb.com/docs/core-concepts/code-intelligence/)
- [MCP Setup](https://gitkb.com/docs/getting-started/mcp-setup/)

## License

MIT
