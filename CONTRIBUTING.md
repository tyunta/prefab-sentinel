# Contributing to Prefab Sentinel

Thanks for taking the time to contribute. This file is the entry point for code, documentation, and infrastructure changes. [README.md](./README.md) is the documentation map; the spec of record lives in the per-area satellite docs it links (ARCHITECTURE.md, docs/api-reference.md, docs/execution-reference.md, TESTING.md, CONFIGURATION.md), and the operational rules of record are [CLAUDE.md](./CLAUDE.md). When a change touches behavior or rules documented there, update the same section in the same PR.

## Development setup

Prefab Sentinel uses [uv](https://docs.astral.sh/uv/) for dependency management. Python 3.11 or newer is required.

```bash
git clone https://github.com/tyunta/prefab-sentinel.git
cd prefab-sentinel
uv sync --extra test --extra lint --extra mcp
```

The `mcp` extra is required for any test or script that imports the MCP server (issue #217). `watch` adds the optional `watchfiles` cache invalidator.

### Running the MCP server directly

For plugin development or debugging you can launch the MCP server straight from a cloned repository, without going through plugin distribution:

```bash
uv sync --extra mcp --extra watch
uv run prefab-sentinel-mcp                                       # stdio transport (default)
uv run prefab-sentinel-mcp --project-root /path/to/unity/project # pin the Unity project root
uv run prefab-sentinel-mcp --transport streamable-http           # HTTP transport
```

The server keeps caches across requests: `activate_project` warms the GUID index, script-name map, and symbol tree, and the `watch` extra invalidates them on `.meta` / `.cs` / asset changes. `--project-root` is optional — the target project can also be declared per session via `activate_project`.

## Running tests and lint

```bash
uv run --extra test --extra mcp python scripts/run_unit_tests.py
uv run ruff check prefab_sentinel/ tests/ scripts/ tools/
uv run mypy prefab_sentinel/
```

`scripts/run_unit_tests.py` wraps `unittest-parallel` with the project's preflight checks (stale `mutants/` directory detection, `mcp` extra detection, `unittest_parallel` availability). Pass through extra arguments such as `-k patch_apply` or `-j 4` to scope or parallelize the run.

Mutation testing is opt-in and runs quarterly, not in CI. See [TESTING.md (Mutation testing)](./TESTING.md#mutation-testing) for the audited module list and value-pinning rules.

## Commit conventions and pre-commit hook

- Commit messages reference the closing issue with `Closes #N` (full close) or `Refs #N` (partial scope). The convention is enforced by review, not by CI; the `Closes #N` token also appears in the matching [CHANGELOG.md](./CHANGELOG.md) entry as a cross-reference.
- User-visible changes append an entry under `## [Unreleased]` in [CHANGELOG.md](./CHANGELOG.md) in the same commit that introduces the change. The changelog is manually curated (Keep a Changelog 1.1.0); patch-bump granularity is excluded and `git log` is the source of truth for that level.
- The pre-commit hook is tracked at `.githooks/pre-commit` and runs `ruff check`, the `bump-my-version` patch bump, `uv lock`, and `scripts/check_bridge_constants.py` (cross-language constant drift detection). Enable it once per clone with:

```bash
git config core.hooksPath .githooks
```

With `core.hooksPath` set, Git ignores `.git/hooks/` and uses the tracked hook directly, so the hook itself stays under version control.

- Minor and major bumps are manual: `uv run bump-my-version bump minor` or `... bump major`. The patch bump on every commit is intentional; do not set `SKIP_BUMP=1` unless an explicit operational reason exists. Version strings live in four places (`pyproject.toml`, `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs`) and are kept in sync by `[tool.bumpversion]`.
- If a hook fails, fix the underlying issue and create a new commit. Do not bypass with `--no-verify` outside an explicit operational carve-out.

## Pull request workflow

1. Branch off `main`. Branch names are free-form; descriptive names such as `fix/broken-pptr-detection` or `docs/readme-pypi-reconcile` are preferred.
2. Make focused commits. Squashing happens at merge time, but a clean commit-by-commit story helps review.
3. Open the PR using the [`.github/PULL_REQUEST_TEMPLATE.md`](./.github/PULL_REQUEST_TEMPLATE.md) (GitHub loads it automatically). Fill in `Summary`, `Related issues`, and the `Checklist` honestly — empty checkboxes are a stronger review signal than unchecked truths.
4. Reviewers verify: tests pass and exercise the changed surface; the relevant spec docs and CLAUDE.md are in sync with the diff when applicable; `CHANGELOG.md [Unreleased]` carries an entry for user-visible changes; the closing-issue link reflects what shipped.
5. CI must be green at merge. If a hook or check is flaky, fix the root cause rather than re-running until it passes.

A reviewer's first read tends to be the spec-doc sections that the diff touches, then the new tests, then the code. Order the PR description and commits to support that path.

For larger refactors, open a draft PR early and link the planning notes (a doc section sketch or an issue with `Refs #N`). It keeps review surface small and gives co-maintainers a chance to flag scope drift before the diff grows.
