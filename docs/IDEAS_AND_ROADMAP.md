# UnityTool Ideas And Roadmap

## Purpose
- Keep implementation ideas and execution order in one place.
- Distinguish between executable tasks and decision-required tasks.

## Current Status (Implemented)
- `validate refs` / `suggest ignore-guids` support:
  - `--ignore-guid`
  - `--ignore-guid-file`
- `suggest ignore-guids` supports:
  - `--out-ignore-guid-file`
  - `--out-ignore-guid-mode {replace|append}`
- Scope-aware GUID index for faster scans in multi-project repositories.
- `where_used` excludes heavy non-target directories by default.
- CLI `inspect where-used` with:
  - `--scope`
  - `--exclude`
  - `--max-usages`
- Markdown report includes noise-reduction summary metrics when ref-scan data exists.
- Added `scripts/benchmark_refs.py` for repeatable `validate refs` timing runs.
- `report export` supports markdown usage compaction:
  - `--md-max-usages`
  - `--md-omit-usages`
- `scripts/benchmark_refs.py` supports CSV output:
  - `--out-csv`
  - `--csv-append`
- `scripts/benchmark_refs.py` supports warm-up runs:
  - `--warmup-runs`
- Added `scripts/benchmark_history_to_csv.py` to combine JSON benchmarks into one CSV.

## Next Executable Tasks
- Add optional percentiles (p50/p90) to benchmark summary and CSV.
- Add benchmark filter options (by scope/severity) for history CSV export.

## Decision-Required Queue
- Decide default location/policy for ignore-guid files:
  - Option A: project local (`config/ignore_guids.txt`)
  - Option B: scope local (`<scope>/config/ignore_guids.txt`)
  - Option C: user managed only (no default path)
- Decide whether to allow automatic ignore-guid application in CI.

## Notes
- Keep read-only policy for Phase 1 behavior.
- Continue fail-fast for invalid input and missing required paths.
