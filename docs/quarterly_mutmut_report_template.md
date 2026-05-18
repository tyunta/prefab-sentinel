# Quarterly mutation-testing report — `<YYYY-Q#>`

This document is the canonical shape every quarterly mutation-testing run fills
in.  It is the integration target referenced from the "Mutation testing"
section of `TESTING.md` and is the artefact whose existence and section completeness
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

`[tool.mutmut].do_not_mutate` is, under mutmut 3.5.0, a **file-path
exclusion list**: `Config.should_ignore_for_mutation` evaluates each entry
with `fnmatch` against the *source file path* — it does not match code
structure, expressions, or mutant names. An entry such as
`prefab_sentinel/legacy_module.py` would exclude that whole file from
mutation generation.

The project runs with `do_not_mutate` **empty**: excluding any file path
would narrow the audited surface, which the campaign's Non-Goals forbid.
Record the state here each run.

| do_not_mutate entry (file-path glob) | Files excluded | Mutants no longer generated | Rationale |
|--------------------------------------|----------------|----------------------------:|-----------|
| _(none — the list is empty)_ | — | — | a file-path exclusion would narrow the audited surface |

Trivial construct-level survivors (logger calls, docstring-delimiter
mutations, equivalent cache-state mutations, …) are **not** suppressible via
`do_not_mutate` — it is a file-path list, not a construct matcher. They are
recorded in the survivor classification below.

### 3.1 Survivor classification

Every survived mutant on the audited surface is classified `critical` /
`trivial` / `equivalent`:

| Mutant (module.dotted.name) | Module | Classification | Disposition |
|-----------------------------|--------|----------------|-------------|
| `<mutant>` | `<module>` | critical / trivial / equivalent | `<kill-test PR / recorded-only / rationale>` |

- **critical** — the suite would kill it; add a value-pinning test (action
  item §5).
- **trivial** — structural noise (logger call, docstring delimiter, …);
  recorded here only. mutmut 3.5.0 has no construct-level suppression
  mechanism, so no configuration change follows.
- **equivalent** — produces identical observable behaviour; recorded with
  rationale.

---

## 4. do_not_mutate roster (configuration snapshot)

Records the exact `[tool.mutmut].do_not_mutate` list as it stood at the run.
The list is **empty**; `tests/test_mutmut_config.py` pins the empty state.

```
(empty — no file-path exclusions)
```

`do_not_mutate` accepts file-path globs only. Adding a path narrows the
audited surface and is a Non-Goal violation, so the list stays empty. Should
a deliberate audited-surface change ever be decided, both this section and
the `[tool.mutmut].do_not_mutate` entry are updated in the same PR with the
rationale recorded in §3.

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
  in `TESTING.md` and `CLAUDE.md`).
- **Mutation score** — `(killed + timeout) / (killed + survived + timeout)`;
  `not_checked` mutants are excluded from the denominator.
- **do_not_mutate entry** — entry in `[tool.mutmut].do_not_mutate`, evaluated
  by mutmut 3.5.0 with `fnmatch` against the *source file path* (not against
  code structure or mutant names). It is a whole-file exclusion list; the
  project keeps it empty so the audited surface is not narrowed.
