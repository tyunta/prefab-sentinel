# MCP パラメータ命名統一 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 36 MCP ツールのパラメータ名を統一し、AI エージェントのバリデーションエラーを解消する。

**Architecture:** MCP 関数シグネチャと C# Editor Bridge ワイヤプロトコルを同時にリネーム。orchestrator / services 層は変更しない。MCP 関数内で新パラメータ名を受け取り、orchestrator 呼び出し時に旧名へ変換する。

**Tech Stack:** Python (mcp_server.py), C# (Unity Editor Bridge), Markdown (skills, README)

**Spec:** `docs/superpowers/specs/2026-03-25-mcp-param-naming-design.md`

---

### Task 1: MCP `asset_path` リネーム (items 1-10: `path` → `asset_path`)

**Files:**
- Modify: `prefab_sentinel/mcp_server.py` (10 関数のシグネチャ + 内部参照)
- Modify: `tests/test_mcp_server.py` (テスト引数名)

対象関数: `get_unity_symbols` (L164), `find_unity_symbol` (L187), `inspect_wiring` (L332), `inspect_variant` (L347), `diff_unity_symbols` (L368), `set_property` (L393), `add_component` (L513), `remove_component` (L613), `inspect_materials` (L1012), `validate_structure` (L1023)

- [ ] **Step 1: mcp_server.py — 10 関数のシグネチャで `path: str` → `asset_path: str` に変更**

各関数で:
- シグネチャの `path: str` を `asset_path: str` に変更
- 関数内の `path` 参照を `asset_path` に変更
- orchestrator 呼び出しの kwarg は旧名のまま: `target_path=asset_path`, `variant_path=asset_path` 等

注意: `_read_asset(path)` → `_read_asset(asset_path)` のように内部呼び出しも変更。レスポンス dict の `"asset_path": path` → `"asset_path": asset_path`。

- [ ] **Step 2: test_mcp_server.py — テスト引数名を追従**

テスト内の `"path":` → `"asset_path":` に変更。対象は items 1-10 に対応するテストケース。

- [ ] **Step 3: テスト実行**

Run: `uv run --extra test python scripts/run_unit_tests.py`
Expected: 全テスト PASS

- [ ] **Step 4: コミット**

```
feat(mcp): rename path to asset_path for 10 inspection tools
```

---

### Task 2: MCP 特殊パスリネーム (items 11-14: `scene_path`, `variant_path`, `prefab_path`, `max_depth`)

**Files:**
- Modify: `prefab_sentinel/mcp_server.py` (4 関数)
- Modify: `tests/test_mcp_server.py`

対象:
- `inspect_hierarchy` (L1034): `path` → `asset_path`, `max_depth` → `depth`
- `validate_runtime` (L1059): `scene_path` → `asset_path`
- `revert_overrides` (L1152): `variant_path` → `asset_path`
- `editor_instantiate` (L946): `prefab_path` → `asset_path`, `parent_path` → `hierarchy_path`

- [ ] **Step 1: mcp_server.py — 4 関数のシグネチャ + 内部参照を変更**

`inspect_hierarchy`:
- `path: str` → `asset_path: str`
- `max_depth: int | None = None` → `depth: int | None = None`
- orchestrator 呼び出し: `target_path=asset_path, max_depth=depth`

`validate_runtime`:
- `scene_path: str` → `asset_path: str`
- orchestrator 呼び出し: `scene_path=asset_path`

`revert_overrides`:
- `variant_path: str` → `asset_path: str`
- 内部呼び出し: `variant_path=asset_path`

`editor_instantiate`:
- `prefab_path: str` → `asset_path: str`
- `parent_path: str = ""` → `hierarchy_path: str = ""`
- kwargs dict: `{"prefab_path": asset_path, "parent_path": hierarchy_path}` (send_action 呼び出しは Task 4 で C# と同時に変更)

- [ ] **Step 2: test_mcp_server.py — テスト引数名を追従**

**ツール呼び出し引数** (MCP パラメータ名) のみ変更:
- `"max_depth"` → `"depth"`, `"path":` → `"asset_path":` (inspect_hierarchy テスト L2085 — ツール呼び出し dict 内)
- `"scene_path"` → `"asset_path"` (validate_runtime テスト — ツール呼び出し dict 内)
- `"variant_path"` → `"asset_path"` (revert_overrides テスト — ツール呼び出し dict 内)
- `"prefab_path"` → `"asset_path"`, `"parent_path"` → `"hierarchy_path"` (editor_instantiate テスト L1867-1904 — ツール呼び出し dict 内のみ)

**`assert_called_once_with` の orchestrator/impl kwarg は旧名のまま維持**:
- L2089: `target_path=...`, `max_depth=...` — orchestrator 層なので変更しない
- L2112, L2139: `scene_path=...` — orchestrator 層なので変更しない
- L1994-1995, L2024: `variant_path=...` — impl 層なので変更しない
- L1873-1874, L1886-1887: `prefab_path=..., parent_path=...` — wire protocol kwarg なので Task 4 で変更

- [ ] **Step 3: テスト実行**

Run: `uv run --extra test python scripts/run_unit_tests.py`
Expected: 全テスト PASS

- [ ] **Step 4: コミット**

```
feat(mcp): rename scene_path/variant_path/prefab_path/max_depth params
```

---

### Task 3: MCP `script_or_guid` + `renderer_path` + `list_depth` リネーム (items 15-21)

**Files:**
- Modify: `prefab_sentinel/mcp_server.py` (5 関数)
- Modify: `tests/test_mcp_server.py`

対象:
- `list_serialized_fields` (L718): `script_path_or_guid` → `script_or_guid`
- `validate_field_rename` (L740): `script_path_or_guid` → `script_or_guid`
- `editor_get_material_property` (L879): `renderer_path` → `hierarchy_path`
- `editor_set_material` (L978): `renderer_path` → `hierarchy_path`
- `editor_list_children` (L850): `list_depth` → `depth`

- [ ] **Step 1: mcp_server.py — 5 関数のシグネチャ + 内部参照を変更**

`list_serialized_fields` / `validate_field_rename`:
- `script_path_or_guid: str` → `script_or_guid: str`
- orchestrator 呼び出し: `script_path_or_guid=script_or_guid`

`editor_get_material_property` / `editor_set_material`:
- `renderer_path: str` → `hierarchy_path: str`
- send_action 呼び出し: `renderer_path=hierarchy_path` (Task 4 で C# と同時に変更)

`editor_list_children`:
- `list_depth: int = 1` → `depth: int = 1`
- send_action 呼び出し: `list_depth=depth` (Task 4 で C# と同時に変更)

- [ ] **Step 2: test_mcp_server.py — テスト引数名を追従**

**ツール呼び出し引数** (MCP パラメータ名) のみ変更:
- `script_path_or_guid` → `script_or_guid` (L938, L961, L998, L1023, L1030, L1580)
- `"renderer_path"` → `"hierarchy_path"` (L1792, L1803, L1914 — ツール呼び出し dict 内のみ)
- `"list_depth"` → `"depth"` (L1766 — ツール呼び出し dict 内のみ)

**`assert_called_once_with` の wire protocol kwarg は Task 4 まで旧名のまま維持**:
- L1774: `list_depth=1` → Task 4 で `depth=1` に変更
- L1796, L1807: `renderer_path="/Body"` → Task 4 で `hierarchy_path="/Body"` に変更
- L1920: `renderer_path="/Body"` → Task 4 で `hierarchy_path="/Body"` に変更

- [ ] **Step 3: テスト実行**

Run: `uv run --extra test python scripts/run_unit_tests.py`
Expected: 全テスト PASS

- [ ] **Step 4: コミット**

```
feat(mcp): rename script_path_or_guid/renderer_path/list_depth params
```

---

### Task 4: C# Editor Bridge フィールド名統一

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs`
- Modify: `tools/unity/PrefabSentinel.UnityIntegrationTests.cs`
- Modify: `prefab_sentinel/mcp_server.py` (send_action の kwarg 名を新名に変更)

C# `EditorControlRequest` フィールドリネーム:
- `prefab_path` → `asset_path` (L63)
- `parent_path` → `hierarchy_path` (L64)
- `renderer_path` → `hierarchy_path` (L71 — `EditorControlRequest` のみ)
- `list_depth` → `depth` (L81)

**対象外**: `MaterialSlotEntry.renderer_path` (L122) はレスポンス DTO フィールド（レンダラー固有のパス情報）であり、リクエストパラメータとは意味が異なる。リネームしない。

- [ ] **Step 1: EditorControlBridge.cs — EditorControlRequest フィールド名を変更**

構造体フィールド:
- L63: `public string prefab_path` → `public string asset_path`
- L64: `public string parent_path` → `public string hierarchy_path`
- L71: `public string renderer_path` → `public string hierarchy_path` (`EditorControlRequest` のみ。`MaterialSlotEntry.renderer_path` (L122) は変更しない)
- L81: `public int list_depth` → `public int depth`

Handler メソッド内の `request.prefab_path` / `request.parent_path` / `request.renderer_path` / `request.list_depth` を全て新名に変更。

エラーメッセージ文字列も更新:
- L477: `"prefab_path is required"` → `"asset_path is required"`
- L581: `"renderer_path is required"` → `"hierarchy_path is required"`
- L820: `"renderer_path is required for get_material_property"` → `"hierarchy_path is required for get_material_property"`

- [ ] **Step 2: IntegrationTests.cs — テスト内の JSON フィールド名を変更**

- `"prefab_path"` → `"asset_path"` (L2089, L2108)
- `"parent_path"` → `"hierarchy_path"` (L2109)
- `"renderer_path"` → `"hierarchy_path"` (L2205, L2242, L2276)

- [ ] **Step 3: mcp_server.py — send_action の kwarg 名を新名に合わせる**

Task 2-3 で暫定的に旧名で渡していた send_action 呼び出しを新名に変更:
- `editor_instantiate`: kwargs dict を `{"asset_path": asset_path, "hierarchy_path": hierarchy_path}` に変更
- `editor_get_material_property`: `renderer_path=hierarchy_path` → `hierarchy_path=hierarchy_path`
- `editor_set_material`: `renderer_path=hierarchy_path` → `hierarchy_path=hierarchy_path`
- `editor_list_children`: `list_depth=depth` → `depth=depth`

- [ ] **Step 4: test_mcp_server.py — wire protocol assertion を新名に変更**

Task 2-3 で旧名のまま残した `assert_called_once_with` の kwarg を新名に:
- L1774: `list_depth=1` → `depth=1`
- L1796, L1807: `renderer_path="/Body"` → `hierarchy_path="/Body"`
- L1920: `renderer_path="/Body"` → `hierarchy_path="/Body"`
- L1873-1874: `prefab_path=..., parent_path=...` → `asset_path=..., hierarchy_path=...`
- L1886-1887: 同上

**変更しない assert_called_once_with**:
- orchestrator 呼び出し検証 (L2089 `target_path=`, L2112 `scene_path=`, L1994 `variant_path=` 等) — orchestrator 層のパラメータ名は変更対象外。

- [ ] **Step 5: テスト実行**

Run: `uv run --extra test python scripts/run_unit_tests.py`
Expected: 全テスト PASS

- [ ] **Step 6: コミット**

```
feat(bridge): align C# EditorControlRequest field names with MCP params
```

---

### Task 5: ドキュメント更新 + バージョンバンプ

**Files:**
- Modify: `skills/guide/SKILL.md` (パラメータ名多数)
- Modify: `skills/udon-log-triage/SKILL.md` (`scene_path` パラメータ記述)
- Modify: `README.md`

注意: `skills/variant-safe-edit/SKILL.md` と `skills/prefab-reference-repair/SKILL.md` は旧パラメータ名を含まないため変更不要。

- [ ] **Step 1: skills/guide/SKILL.md のパラメータ名を更新**

`guide/SKILL.md` は MCP ツールリファレンスなのでパラメータ名が多数出現する。**パラメータ記述のコンテキストのみ** を変更し、パッチ計画の JSON 例やファイルパスの一般的な `path` は変更しない:
- MCP ツールパラメータとして記述されている `path` → `asset_path`
- `scene_path` → `asset_path`
- `script_path_or_guid` → `script_or_guid`
- `max_depth` → `depth`
- `list_depth` → `depth`

- [ ] **Step 2: skills/udon-log-triage/SKILL.md の `scene_path` を更新**

- L22: `scene_path` パラメータ → `asset_path`

- [ ] **Step 3: README.md のパラメータ名を更新**

- [ ] **Step 2: README.md のパラメータ名を更新**

MCP ツール一覧セクションの旧パラメータ名を新名に変更。

- [ ] **Step 3: コミット**

```
docs: update parameter names in skills and README for v0.5.0
```

---

### Task 6: 検証 + バージョンバンプ

**Files:**
- Modify: `pyproject.toml` (version bump)

- [ ] **Step 1: 旧パラメータ名の残存確認**

以下のファイル群で旧パラメータ名が残っていないことを grep で確認:
- `prefab_sentinel/mcp_server.py`
- `tests/test_mcp_server.py`
- `skills/**/*.md`
- `README.md`

検索対象: `script_path_or_guid`, `renderer_path` (mcp_server.py 内), `list_depth` (mcp_server.py 内), `max_depth` (mcp_server.py 内), `variant_path` (mcp_server.py シグネチャ), `scene_path` (mcp_server.py シグネチャ), `prefab_path` (mcp_server.py シグネチャ)

注意: orchestrator / C# の内部変数名に旧名が残るのは想定通り。

- [ ] **Step 2: 全テスト実行**

Run: `uv run --extra test python scripts/run_unit_tests.py`
Expected: 全テスト PASS、1120 件

- [ ] **Step 3: MCP ツール登録数確認**

MCP サーバーを起動して `tools/list` で 36 ツール登録を確認。

- [ ] **Step 4: 手動 MCP スモーク**

`activate_project` → `validate_refs` → `inspect_hierarchy` の 3 ツールを新パラメータ名で呼び出し、正常応答を確認。

- [ ] **Step 5: バージョンバンプ**

Run: `uv run bump-my-version bump minor`
Expected: v0.4.x → v0.5.0

- [ ] **Step 6: コミット**

```
chore: bump version to v0.5.0 (MCP parameter naming unification)
```

- [ ] **Step 7: tasks/todo.md に完了記録を追記**
