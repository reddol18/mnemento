# Runtime and development folders

Other agent sessions use Mnemento over MCP while it is being developed. They must keep working when the development
folder has half-written code, failing tests or a long benchmark running. So the MCP server never runs from the
development folder.

## Layout

| folder | what runs there | who changes it |
|---|---|---|
| `mnemento/` (development, branch `main`) | editing, `pytest`, benchmarks, worktrees for long measurements | the implementing session |
| `mnemento-runtime/` (git worktree, branch `runtime`) | the MCP server (`uv --directory …/mnemento-runtime run mnemento-mcp`) | only the update step below |

- The runtime worktree has its own `.venv` (`uv sync` once after creating or updating it), so a dependency change in
  development does not touch it.
- The real ledger and the user's schema copies live outside both folders (`MNEMENTO_DB`, `MNEMENTO_SCHEMAS` in the MCP
  config, e.g. `~/.mnemento/`). Tests use in-memory or temporary databases; benchmarks use their own generated
  databases under `bench/`; neither ever opens the real ledger. Local scripts that read or write the real ledger live
  outside the repository and run only with the user's approval.
- The MCP server answers the handshake before it opens the database (first tool call builds the Keeper), so a
  database held by another writer cannot make a client time out at connect.

## Setup (once)

```bash
git worktree add -B runtime ../mnemento-runtime main
uv --directory ../mnemento-runtime sync
# MCP config (Claude Code): command "uv", args ["--directory", "<abs path>/mnemento-runtime", "run", "mnemento-mcp"],
# env MNEMENTO_DB / MNEMENTO_SCHEMAS pointing outside the repository
```

## Updating the runtime — only when an issue is finished

An issue is finished when its tests pass, CI passes, the measurements it requires are recorded and its commits are on
`main`. Then:

```bash
git -C ../mnemento-runtime merge --ff-only main
uv --directory ../mnemento-runtime sync
```

Sessions pick the new code up on their next MCP start (`/mcp` reconnect in Claude Code). If the new runtime misbehaves,
go back with `git -C ../mnemento-runtime reset --hard <previous commit>` and `uv sync`; record why in the issue.

Never edit files inside `mnemento-runtime/`, and never run `pytest` or benchmarks there.
