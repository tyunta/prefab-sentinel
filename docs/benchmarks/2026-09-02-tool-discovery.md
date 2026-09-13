# Tool Discovery Benchmark — 2026-09-02

## Scope and reproducibility

This is an offline measurement of the currently registered MCP tool metadata. It
does not call an MCP tool, access Unity, or change the production discovery
surface. The run used the committed bilingual fixture and canonical tool catalog:

```bash
uv run --extra mcp python scripts/run_tool_discovery_benchmark.py \
  --fixture benchmarks/tool-discovery/queries.v1.json \
  --tools-doc docs/tools.md \
  --out-report /tmp/tool-discovery-report-1.json

uv run --extra mcp python scripts/run_tool_discovery_benchmark.py \
  --fixture benchmarks/tool-discovery/queries.v1.json \
  --tools-doc docs/tools.md \
  --out-report /tmp/tool-discovery-report-2.json
```

Both runs completed with `TOOL_DISCOVERY_BENCHMARK_OK`. Their byte streams were
identical (and therefore their parsed JSON objects were identical), contained no
non-finite number, and reported the following identity:

| Field | Value |
|---|---|
| Report schema | `tool-discovery-benchmark-report.v1` |
| Registry | 101 tools in 19 categories |
| Registry fingerprint | `c35ae6b2fa14c3f31e16c4db491c307a1a4913b4d0326c026620f1c6a4e240ce` |
| Fixture corpus | `corpus_invariants` role→`pair_id` bindings with code-owned v1 semantics plus 38 queries: 19 Japanese, 19 English |
| Fixture SHA-256 | `0f029224cea4a29e1c80af841535de2d79126de55c13abfcadac170f7b13b380` |
| Ranker | `weighted-idf-name3-description1.v1` (name ×3, description ×1) |

## Retrieval results

| Population | Queries | recall@1 | recall@3 | recall@5 | recall@8 | MRR | Zero-score queries |
|---|---:|---:|---:|---:|---:|---:|---:|
| Overall | 38 | 52.63% | 65.79% | 71.05% | 73.68% | 0.614274 | 2 |
| Japanese | 19 | 26.32% | 47.37% | 57.89% | 63.16% | 0.405025 | 2 |
| English | 19 | 78.95% | 84.21% | 84.21% | 84.21% | 0.823522 | 0 |

Safety metrics cover the 32 `safety_critical` cases and their top eight
candidates: 14 unsafe false-positive placements occurred in 10 queries, for an
unsafe false-positive query rate of 31.25%. These are lexical-routing evidence,
not a statement that the registered tools bypass their own runtime safeguards.

## Context cost

The full sorted tool schema is 101,457 UTF-8 bytes, estimated as 25,365 tokens.
The candidate rows are the minimum / mean / maximum across the 38 query-specific
rankings.

| Candidate count | Bytes (min / mean / max) | Estimated tokens (min / mean / max) |
|---:|---:|---:|
| 1 | 359 / 1,168.47 / 2,841 | 90 / 292.55 / 711 |
| 3 | 1,981 / 3,789.87 / 6,598 | 496 / 947.95 / 1,650 |
| 5 | 3,774 / 6,496.47 / 11,141 | 944 / 1,624.53 / 2,786 |
| 8 | 7,366 / 9,886.50 / 14,201 | 1,842 / 2,472.03 / 3,551 |

The estimator is `utf8-bytes-div-4-ceiling.v1`: it calculates
`ceil(utf8_bytes / 4)` over compact, sorted-key UTF-8 JSON. It is deliberately
provider- and model-independent, not an exact tokenizer, billing measure, or
context-window measure for any model; it is most representative of this
predominantly ASCII schema corpus.

## Queries outside the top eight

All ten cases whose first accepted tool ranked below eight are classified below.
The categories describe the observed lexical failure, not a new production
contract.

| Classification | Cases and first accepted rank | Evidence |
|---|---|---|
| Missing description vocabulary | `saved-property-set-ja` (12), `saved-property-set-en` (14) | The persisted single serialized-field intent did not retrieve `set_property` in either language. |
| Category vocabulary | `symbol-discovery-ja` (73), `symbol-discovery-en` (27) | Candidate/name/hierarchy wording did not align with the symbols category strongly enough; the Japanese query had a zero score. |
| Japanese/English mismatch | `asset-copy-safe-ja` (17; English 1), `project-status-ja` (64; English 1), `live-parent-change-ja` (14; English 1), `script-execution-ja` (52; English 2) | The matching English pair reaches the accepted tool in the top two or top one, while the Japanese wording does not. `script-execution-ja` is the other zero-score query. |
| Safety constraint | `transform-read-ja` (40), `transform-read-en` (26) | The read intent mentions a parent; the English top eight includes forbidden `editor_set_parent`, so a mutation-related term overpowers the read-only constraint. |

## Decision

**Improve descriptions.** Top-eight candidates reduce mean estimated context
from 25,365 to 2,472.03 tokens, but the same metadata-driven ranker currently
misroutes recurring vocabulary and safety-constrained intents. A staged catalog
would still need this lexical selection step, so it does not address the
observed cause. Follow-up work should evaluate concise, public bilingual
description vocabulary for read-only, mutation, symbol-discovery, and persisted
property intents, then rerun this unchanged fixture and command. This benchmark
does not change tool descriptions or any production MCP interface.
