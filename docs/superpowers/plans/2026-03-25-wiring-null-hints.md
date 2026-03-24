# inspect_wiring Null 分類ヒント Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** inspect_wiring の出力にコンポーネントごとの null ref 比率とフィールド名リストを追加し、AI が意図的 null と事故 null を自分で判断できるようにする。

**Architecture:** `ComponentWiring` dataclass に `null_field_names: list[str]` を追加。`analyze_wiring()` の既存ループ内で null フィールド名を収集。orchestrator のコンポーネント summary dict に `null_ratio` と `null_field_names` を追加。

**Tech Stack:** Python (udon_wiring.py, orchestrator.py), unittest

**Spec:** `docs/superpowers/specs/2026-03-25-wiring-null-hints-design.md`

---

### Task 1: `ComponentWiring` に `null_field_names` を追加 + テスト

**Files:**
- Modify: `prefab_sentinel/udon_wiring.py:10` (import に `field` 追加)
- Modify: `prefab_sentinel/udon_wiring.py:60-68` (ComponentWiring dataclass)
- Modify: `prefab_sentinel/udon_wiring.py:305-317` (analyze_wiring null ref loop)
- Modify: `tests/test_udon_wiring.py` (テスト追加)

- [ ] **Step 1: テスト追加 — `null_field_names` が設定されることを検証**

`tests/test_udon_wiring.py` の `AnalyzeWiringTests` クラスに追加:

```python
def test_null_field_names_populated(self) -> None:
    """ComponentWiring.null_field_names lists all null ref field names."""
    result = analyze_wiring(BASIC_MONOBEHAVIOUR, "test.prefab")
    # BASIC_MONOBEHAVIOUR has 1 component with myRef (valid) + myNullRef (null)
    self.assertEqual(len(result.components), 1)
    self.assertEqual(result.components[0].null_field_names, ["myNullRef"])

def test_null_field_names_empty_when_no_nulls(self) -> None:
    """ComponentWiring.null_field_names is empty when no null refs."""
    result = analyze_wiring(CLEAN_FILE, "test.prefab")
    for comp in result.components:
        self.assertEqual(comp.null_field_names, [])

def test_null_field_names_excludes_nested_structs(self) -> None:
    """Nested struct null refs are not included in null_field_names."""
    result = analyze_wiring(NESTED_STRUCT, "test.prefab")
    for comp in result.components:
        self.assertEqual(comp.null_field_names, [])
```

- [ ] **Step 2: テスト実行 — FAIL を確認**

Run: `uv run --extra test python -m unittest tests.test_udon_wiring.AnalyzeWiringTests.test_null_field_names_populated -v`
Expected: FAIL (`AttributeError: 'ComponentWiring' object has no attribute 'null_field_names'`)

- [ ] **Step 3: `ComponentWiring` に `null_field_names` フィールド追加**

`prefab_sentinel/udon_wiring.py` L10 の import に `field` を追加:

```python
from dataclasses import dataclass, field
```

L60-68 の `ComponentWiring` dataclass に追加:

```python
@dataclass(slots=True)
class ComponentWiring:
    file_id: str
    game_object_file_id: str
    script_guid: str
    fields: list[WiringField]
    block_start_line: int
    is_udon_sharp: bool
    backing_udon_file_id: str
    override_count: int = 0
    null_field_names: list[str] = field(default_factory=list)
```

- [ ] **Step 4: `analyze_wiring()` で null フィールド名を収集**

`prefab_sentinel/udon_wiring.py` L305-317 の null ref 検出ブロック内、`null_references.append(...)` の直前に 1 行追加:

```python
            if f.file_id == "0" and not f.guid:
                comp.null_field_names.append(f.name)  # ← 追加
                null_references.append(
```

- [ ] **Step 5: テスト実行 — 全テスト PASS**

Run: `uv run --extra test python scripts/run_unit_tests.py`
Expected: 全テスト PASS

- [ ] **Step 6: コミット**

```
feat(wiring): add null_field_names to ComponentWiring
```

---

### Task 2: orchestrator の summary dict に `null_ratio` / `null_field_names` 追加 + テスト

**Files:**
- Modify: `prefab_sentinel/orchestrator.py:749-761` (component summary dict construction)
- Modify: `tests/test_orchestrator.py` (orchestrator テスト追加)

- [ ] **Step 1: テスト追加 — orchestrator の dict に `null_ratio` と `null_field_names` が含まれること**

`tests/test_orchestrator.py` の `InspectWiringTests` クラスに追加。テストパターンは既存の `test_script_name_and_game_object_name` (L411-440) に従う:

```python
def test_null_ratio_and_null_field_names_in_output(self) -> None:
    """inspect_wiring output includes null_ratio and null_field_names per component."""
    import tempfile
    from tests.yaml_helpers import YAML_HEADER, make_gameobject, make_monobehaviour

    text = (
        YAML_HEADER
        + make_gameobject("100", "MyObj", ["200"])
        + make_monobehaviour("200", "100")
        + "  validRef: {fileID: 100}\n"
        + "  nullRef1: {fileID: 0}\n"
        + "  nullRef2: {fileID: 0}\n"
    )
    with tempfile.NamedTemporaryFile(suffix=".prefab", mode="w", delete=False) as f:
        f.write(text)
        f.flush()
        orch = _make_orchestrator()
        with patch(
            "prefab_sentinel.orchestrator.find_project_root",
            side_effect=Exception("no project"),
        ):
            result = orch.inspect_wiring(f.name)

    self.assertTrue(result.success)
    comps = result.data["components"]
    self.assertEqual(len(comps), 1)
    self.assertEqual(comps[0]["null_ratio"], "2/3")
    self.assertEqual(comps[0]["null_field_names"], ["nullRef1", "nullRef2"])
```

- [ ] **Step 2: テスト実行 — FAIL を確認**

Run: `uv run --extra test python -m unittest tests.test_orchestrator.InspectWiringTests.test_null_ratio_and_null_field_names_in_output -v`
Expected: FAIL (`KeyError: 'null_ratio'`)

- [ ] **Step 3: orchestrator.py の cd dict に `null_ratio` と `null_field_names` を追加**

`prefab_sentinel/orchestrator.py` L749-761 の `cd` dict 構築。`"field_count"` の後、`"fields"` の前に 2 行追加:

```python
            cd: dict[str, object] = {
                "file_id": comp.file_id,
                "game_object_file_id": comp.game_object_file_id,
                "game_object_name": _go_name(comp.game_object_file_id),
                "script_guid": comp.script_guid,
                "script_name": guid_to_name.get(comp.script_guid, ""),
                "is_udon_sharp": comp.is_udon_sharp,
                "field_count": len(comp.fields),
                "null_ratio": f"{len(comp.null_field_names)}/{len(comp.fields)}",  # 追加
                "null_field_names": comp.null_field_names,                          # 追加
                "fields": field_dicts,
            }
```

- [ ] **Step 4: テスト実行 — 全テスト PASS**

Run: `uv run --extra test python scripts/run_unit_tests.py`
Expected: 全テスト PASS

- [ ] **Step 5: コミット**

```
feat(wiring): add null_ratio and null_field_names to inspect_wiring output
```

---

### Task 3: 検証 + 完了記録

**Files:**
- Modify: `tasks/todo.md`

- [ ] **Step 1: 全テスト実行**

Run: `uv run --extra test python scripts/run_unit_tests.py`
Expected: 全テスト PASS

- [ ] **Step 2: 動作確認**

```bash
uv run python -c "
from prefab_sentinel.udon_wiring import analyze_wiring
YAML = '''%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!1 &100000
GameObject:
  m_Name: TestObject
  m_Component:
  - component: {fileID: 100001}
--- !u!114 &100001
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_GameObject: {fileID: 100000}
  m_Script: {fileID: 11500000, guid: aabbccdd11223344aabbccdd11223344, type: 3}
  myRef: {fileID: 100000}
  myNullRef: {fileID: 0}
  anotherNull: {fileID: 0}
'''
result = analyze_wiring(YAML, 'test.prefab')
comp = result.components[0]
print(f'null_field_names: {comp.null_field_names}')
print(f'null_ratio: {len(comp.null_field_names)}/{len(comp.fields)}')
"
```

Expected:
```
null_field_names: ['myNullRef', 'anotherNull']
null_ratio: 2/3
```

- [ ] **Step 3: tasks/todo.md に完了記録を追記**

- [ ] **Step 4: コミット**

```
docs: record wiring null hints completion in todo.md
```
