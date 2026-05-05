# Quarterly mutation-testing report — `<YYYY-Q#>`

This document is the canonical shape every quarterly mutation-testing run fills
in.  It is the integration target referenced from `README.md` §14.5
"運用カデンス" and is the artefact whose existence and section completeness
satisfy the redefined acceptance basis for issue #149 / #170 (PR-level
option A).

Replace the placeholder text in each section with the actual run data; do not
remove the section headings or the table headers — they are the contract that
`tests/test_mutmut_config.py` pins by value.

---

## 1. Run context

| Field | Value |
|-------|-------|
| Run date (UTC) | `<YYYY-MM-DD>` |
| Quarter | `<YYYY-Q#>` |
| `mutmut` version | `<x.y.z>` |
| Parallelism (`--max-children`) | `<int>` |
| Auditor | `<github-handle>` |
| Repository commit | `<git rev-parse HEAD>` |
| Score-aggregation script | `scripts/mutmut_score_report.py` |

The CSV emitted by
`uv run python scripts/mutmut_score_report.py --audited-only --format csv`
is appended to `reports/mutmut_history.csv` as the canonical machine-readable
companion to this human-readable report.

---

## 2. Per-audited-module mutation-score history

Records the mutation score for each of the six audited modules across runs
so the trend (uplift, regression, plateau) is visible at a glance.  The
"current" column reflects the run this report describes; the "previous N"
columns reflect the immediately preceding quarterly runs (oldest on the
right).  Empty cells indicate the module was added to the audited list
after that run.

| Audited module | Current | Previous 1 | Previous 2 | Previous 3 | Threshold |
|----------------|--------:|-----------:|-----------:|-----------:|----------:|
| `prefab_sentinel.services.reference_resolver` | `<n.n%>` | `<n.n%>` | `<n.n%>` | `<n.n%>` | `<n.n%>` |
| `prefab_sentinel.services.prefab_variant` | `<n.n%>` | `<n.n%>` | `<n.n%>` | `<n.n%>` | `<n.n%>` |
| `prefab_sentinel.services.serialized_object.patch_validator` | `<n.n%>` | `<n.n%>` | `<n.n%>` | `<n.n%>` | `<n.n%>` |
| `prefab_sentinel.services.runtime_validation.classification` | `<n.n%>` | `<n.n%>` | `<n.n%>` | `<n.n%>` | `<n.n%>` |
| `prefab_sentinel.orchestrator_postcondition` | `<n.n%>` | `<n.n%>` | `<n.n%>` | `<n.n%>` | `<n.n%>` |
| `prefab_sentinel.orchestrator_validation` | `<n.n%>` | `<n.n%>` | `<n.n%>` | `<n.n%>` | `<n.n%>` |

Notes:

- The "Threshold" column is the audited-module operational target documented
  in `CLAUDE.md` ("Mutation testing 運用").
- A module that drops below its threshold relative to the prior quarter is
  raised as a regression bullet in §5 (Action items).
- A module that rises above its threshold for two consecutive quarters is
  promoted as a stable-uplift bullet in §5.

---

## 3. Suppression-impact section

Records the survivor-count delta produced by each entry in
`[tool.mutmut].do_not_mutate` so the cost of a suppression is visible.  The
"survivors with suppression" column counts mutants that survived under the
current configuration; the "survivors without suppression" column counts
mutants that survived a parallel run with that one pattern temporarily
removed.  The "delta" column is `without − with`: a positive number means
the suppression hides that many additional surviving mutants from the
audited surface.

| Suppression pattern | Survivors with suppression | Survivors without suppression | Delta | Classification | Notes |
|---------------------|---------------------------:|------------------------------:|------:|----------------|-------|
| `*logger.*` | `<int>` | `<int>` | `<int>` | trivial / equivalent / critical | `<rationale>` |
| `*log.debug*` | `<int>` | `<int>` | `<int>` | trivial / equivalent / critical | `<rationale>` |
| `*log.info*` | `<int>` | `<int>` | `<int>` | trivial / equivalent / critical | `<rationale>` |
| `*log.warning*` | `<int>` | `<int>` | `<int>` | trivial / equivalent / critical | `<rationale>` |
| `*log.error*` | `<int>` | `<int>` | `<int>` | trivial / equivalent / critical | `<rationale>` |
| `*log.critical*` | `<int>` | `<int>` | `<int>` | trivial / equivalent / critical | `<rationale>` |
| `*"""*` | `<int>` | `<int>` | `<int>` | trivial / equivalent / critical | `<rationale>` |
| `*'''*` | `<int>` | `<int>` | `<int>` | trivial / equivalent / critical | `<rationale>` |
| `*_text_cache.get*` | `<int>` | `<int>` | `<int>` | trivial / equivalent / critical | `<rationale>` |
| `*_guid_map*` | `<int>` | `<int>` | `<int>` | trivial / equivalent / critical | `<rationale>` |
| `*invalidate_*_cache*` | `<int>` | `<int>` | `<int>` | trivial / equivalent / critical | `<rationale>` |

Notes:

- A "critical" classification means the suppression is hiding a
  non-equivalent mutant that the suite would otherwise kill — that pattern
  must be removed and replaced with a value-pinning test.
- A "trivial" classification (logger calls, structural noise) keeps the
  pattern in the configuration.
- An "equivalent" classification (mutations that produce identical
  observable behaviour) keeps the pattern; the rationale column states why.
- New equivalent-mutation patterns are added to `[tool.mutmut].do_not_mutate`
  only when this table records a non-trivial measured delta and the
  classification is `equivalent` — issue #182's "no blind expansion" rule.

---

## 4. Suppression-pattern roster (configuration snapshot)

Records the exact `[tool.mutmut].do_not_mutate` list as it stood at the run.
This snapshot is what the `tests/test_mutmut_config.py` configuration test
pins by literal-string equality at the run commit.

```
*logger.*
*log.debug*
*log.info*
*log.warning*
*log.error*
*log.critical*
*"""*
*'''*
*_text_cache.get*
*_guid_map*
*invalidate_*_cache*
```

When a quarterly run adds, removes, or rewrites a pattern, both this section
and the `[tool.mutmut].do_not_mutate` entry must be updated in the same PR
and the rationale recorded in §3.

---

## 5. Action items

- `<critical-class survivor>` — owner `<github-handle>` — target PR ETA
  `<YYYY-Q#>`.
- `<regression bullet>` — owner `<github-handle>` — target PR ETA
  `<YYYY-Q#>`.
- `<stable-uplift bullet>` — promote / hold per the trend table.

The action-item list closes the loop between the score history (§2), the
suppression-impact analysis (§3), and the next quarterly run.

---

## 6. Glossary

- **Audited module** — one of the six modules listed in §2 (also documented
  in `README.md` §14.5 and `CLAUDE.md`).
- **Mutation score** — `(killed + timeout) / (killed + survived + timeout)`;
  `not_checked` mutants are excluded from the denominator.
- **Suppression pattern** — entry in `[tool.mutmut].do_not_mutate` matched
  against mutant names via fnmatch-style globs.
- **Suppression delta** — `survivors_without_suppression − survivors_with_suppression`
  for the pattern, measured by re-running the audited surface with the
  pattern temporarily removed.
