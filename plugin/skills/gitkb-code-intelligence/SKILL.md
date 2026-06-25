---
name: gitkb-code-intelligence
description: Use when Codex or a GitKB hook points you at GitKB for callers, definitions, references, usage analysis, blast radius, or symbol search.
---

# GitKB Code Intelligence

Use GitKB's AST-backed code intelligence for code relationships:

```bash
git-kb code symbols "<name>" --json
git-kb code symbols --file <path> --json
git-kb code callers "<symbol-or-id>" --json
git-kb code callees "<symbol-or-id>" --json
git-kb code impact <path> --json
git-kb ai semantic "<query>" --json
```

When MCP tools are enabled, prefer the matching GitKB MCP tools for structured output.

Raw `rg`, `grep`, `find`, and file reads are still appropriate for exact strings, logs, config, docs, generated output, snapshots, and one-off content checks.

If code intelligence returns no results for code that exists, run:

```bash
git-kb code doctor --json
git-kb code index
```
