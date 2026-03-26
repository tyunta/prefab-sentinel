# Editor Bridge & Inspect 改善 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add editor_screenshot pre-refresh, Nested Prefab symbol tree expansion, $scene handle fix for find_component, and inspect_materials Variant empty fix with Nested Prefab fallback.

**Architecture:** Four independent changes sharing a common constant (`CLASS_ID_PREFAB_INSTANCE`). Section A modifies only `mcp_server.py`. Section B adds nested expansion to `symbol_tree.py` + `session.py` + `orchestrator.py`. Section C is a one-line fix in `serialized_object.py`. Section D adds a third fallback to `material_inspector.py` + `orchestrator.py`.

**Tech Stack:** Python 3.12, unittest, `prefab_sentinel` package

**Spec:** `docs/superpowers/specs/2026-03-26-editor-bridge-inspect-improvements-design.md`

---

### Task 1: editor_screenshot pre-refresh (Section A)

**Files:**
- Modify: `prefab_sentinel/mcp_server.py:840-853`
- Modify: `tests/test_mcp_server.py`

- [ ] **Step 1: Write tests for refresh=True (2 calls) and refresh=False (1 call)**

In `tests/test_mcp_server.py`, add these tests after the existing `test_editor_screenshot_defaults` test:

```python
def test_editor_screenshot_refresh_true_calls_refresh_then_capture(self) -> None:
    server = create_server()
    with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
        _run(server.call_tool("editor_screenshot", {"refresh": True}))
    self.assertEqual(mock_send.call_count, 2)
    mock_send.assert_any_call(action="refresh_asset_database")
    # Second call is capture_screenshot
    calls = mock_send.call_args_list
    self.assertEqual(calls[0], call(action="refresh_asset_database"))
    self.assertEqual(calls[1], call(action="capture_screenshot", view="scene", width=0, height=0))

def test_editor_screenshot_refresh_false_skips_refresh(self) -> None:
    server = create_server()
    with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
        _run(server.call_tool("editor_screenshot", {"refresh": False}))
    mock_send.assert_called_once_with(action="capture_screenshot", view="scene", width=0, height=0)

def test_editor_screenshot_refresh_default_is_true(self) -> None:
    server = create_server()
    with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
        _run(server.call_tool("editor_screenshot", {}))
    self.assertEqual(mock_send.call_count, 2)
    self.assertEqual(mock_send.call_args_list[0], call(action="refresh_asset_database"))

def test_editor_screenshot_refresh_failure_still_captures(self) -> None:
    server = create_server()
    responses = [{"success": False, "error": "refresh failed"}, {"success": True, "data": {"output_path": "/shot.png"}}]
    with patch("prefab_sentinel.mcp_server.send_action", side_effect=responses) as mock_send:
        _, result = _run(server.call_tool("editor_screenshot", {"refresh": True}))
    self.assertEqual(mock_send.call_count, 2)
    self.assertTrue(result["success"])
```

Add `call` to the imports at the top of the test file:

```python
from unittest.mock import call, patch
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_mcp_server.py -k "test_editor_screenshot_refresh" -v`
Expected: FAIL (refresh parameter not yet accepted)

- [ ] **Step 3: Implement refresh parameter in editor_screenshot**

In `prefab_sentinel/mcp_server.py`, replace the `editor_screenshot` function (around line 841):

```python
@server.tool()
def editor_screenshot(
    view: str = "scene",
    width: int = 0,
    height: int = 0,
    refresh: bool = True,
) -> dict[str, Any]:
    """Capture a screenshot of the Unity Editor.

    Args:
        view: Which view to capture ("scene" or "game").
        width: Capture width in pixels (0 = current window size).
        height: Capture height in pixels (0 = current window size).
        refresh: Refresh the asset database before capturing (default True).
    """
    if refresh:
        try:
            send_action(action="refresh_asset_database")
        except Exception:
            logger.warning("Pre-screenshot refresh failed", exc_info=True)
    return send_action(action="capture_screenshot", view=view, width=width, height=height)
```

- [ ] **Step 4: Update existing screenshot tests**

Two existing tests are affected by the default `refresh=True`:

**`test_editor_screenshot_defaults`** expects 1 call → now 2. Update:

```python
def test_editor_screenshot_defaults(self) -> None:
    server = create_server()
    with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
        _run(server.call_tool("editor_screenshot", {}))
    # Default refresh=True means 2 calls: refresh + capture
    self.assertEqual(mock_send.call_count, 2)
    mock_send.assert_any_call(action="capture_screenshot", view="scene", width=0, height=0)
```

**`test_editor_screenshot_delegates`** passes `{"view": "game", "width": 1920}`. With default `refresh=True` it will make 2 calls. The test asserts `mock_response == result` on the return value, which still works (the return comes from the last `send_action` call). However, update it to also verify call count:

```python
def test_editor_screenshot_delegates(self) -> None:
    server = create_server()
    mock_response = {"success": True, "data": {"output_path": "/tmp/shot.png"}}
    with patch("prefab_sentinel.mcp_server.send_action", return_value=mock_response) as mock_send:
        _, result = _run(server.call_tool("editor_screenshot", {"view": "game", "width": 1920}))
    self.assertEqual(mock_response, result)
    # Default refresh=True: refresh + capture = 2 calls
    self.assertEqual(mock_send.call_count, 2)
```

- [ ] **Step 5: Run all tests**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_mcp_server.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add prefab_sentinel/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: add refresh parameter to editor_screenshot (default True)"
```

---

### Task 2: CLASS_ID_PREFAB_INSTANCE constant + yaml_helpers (Shared foundation)

**Files:**
- Modify: `prefab_sentinel/unity_yaml_parser.py:18-21`
- Modify: `tests/yaml_helpers.py`

- [ ] **Step 1: Add CLASS_ID_PREFAB_INSTANCE constant**

In `prefab_sentinel/unity_yaml_parser.py`, after line 21 (`CLASS_ID_MONOBEHAVIOUR = "114"`), add:

```python
CLASS_ID_PREFAB_INSTANCE = "1001"
```

- [ ] **Step 2: Add make_prefab_instance helper to yaml_helpers**

In `tests/yaml_helpers.py`, add after the `make_monobehaviour` function:

```python
def make_prefab_instance(
    file_id: str,
    source_guid: str,
    *,
    transform_parent: str = "0",
    stripped_children: list[tuple[str, str]] | None = None,
) -> str:
    """Build a PrefabInstance block (class ID 1001) with m_SourcePrefab.

    Args:
        file_id: FileID of the PrefabInstance.
        source_guid: GUID of the source prefab.
        transform_parent: FileID of the parent Transform (0 = root).
        stripped_children: Optional list of (file_id, class_id) for stripped blocks.
    """
    block = (
        f"--- !u!1001 &{file_id}\n"
        f"PrefabInstance:\n"
        f"  m_Modification:\n"
        f"    m_TransformParent: {{fileID: {transform_parent}}}\n"
        f"    m_Modifications: []\n"
        f"  m_SourcePrefab: {{fileID: 100100000, guid: {source_guid}, type: 3}}\n"
    )
    children_text = ""
    if stripped_children:
        for child_fid, child_class_id in stripped_children:
            children_text += f"--- !u!{child_class_id} &{child_fid} stripped\n"
    return block + children_text
```

- [ ] **Step 3: Run existing tests to confirm no regressions**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_symbol_tree.py tests/test_material_inspector.py -v`
Expected: PASS (no behavior changes, just constants and helpers added)

- [ ] **Step 4: Commit**

```bash
git add prefab_sentinel/unity_yaml_parser.py tests/yaml_helpers.py
git commit -m "feat: add CLASS_ID_PREFAB_INSTANCE constant and test helper"
```

---

### Task 3: SymbolKind.PREFAB_INSTANCE + SymbolNode.source_prefab (Section B — data model)

**Files:**
- Modify: `prefab_sentinel/symbol_tree.py:49-88`

- [ ] **Step 1: Write test for new enum variant and field**

In `tests/test_symbol_tree.py`, add a new test class:

```python
class TestSymbolNodePrefabInstance(unittest.TestCase):
    """SymbolNode with PREFAB_INSTANCE kind and source_prefab."""

    def test_prefab_instance_kind_value(self) -> None:
        self.assertEqual(SymbolKind.PREFAB_INSTANCE.value, "prefab_instance")

    def test_source_prefab_default_empty(self) -> None:
        from prefab_sentinel.symbol_tree import SymbolNode
        node = SymbolNode(
            kind=SymbolKind.PREFAB_INSTANCE,
            name="[PrefabInstance: test.prefab]",
            file_id="999",
            class_id="1001",
        )
        self.assertEqual(node.source_prefab, "")

    def test_to_dict_includes_source_prefab(self) -> None:
        from prefab_sentinel.symbol_tree import SymbolNode
        node = SymbolNode(
            kind=SymbolKind.PREFAB_INSTANCE,
            name="[PrefabInstance: Assets/Shirt.prefab]",
            file_id="999",
            class_id="1001",
            source_prefab="Assets/Shirt.prefab",
        )
        d = node.to_dict()
        self.assertEqual(d["kind"], "prefab_instance")
        self.assertEqual(d["source_prefab"], "Assets/Shirt.prefab")

    def test_to_dict_omits_empty_source_prefab(self) -> None:
        from prefab_sentinel.symbol_tree import SymbolNode
        node = SymbolNode(
            kind=SymbolKind.GAME_OBJECT,
            name="Root",
            file_id="100",
            class_id="1",
        )
        d = node.to_dict()
        self.assertNotIn("source_prefab", d)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_symbol_tree.py::TestSymbolNodePrefabInstance -v`
Expected: FAIL (PREFAB_INSTANCE not defined, source_prefab not a field)

- [ ] **Step 3: Add PREFAB_INSTANCE to SymbolKind**

In `prefab_sentinel/symbol_tree.py`, add to the `SymbolKind` enum after `PROPERTY`:

```python
class SymbolKind(StrEnum):
    GAME_OBJECT = "game_object"
    COMPONENT = "component"
    PROPERTY = "property"
    PREFAB_INSTANCE = "prefab_instance"
```

- [ ] **Step 4: Add source_prefab field to SymbolNode**

In `prefab_sentinel/symbol_tree.py`, add `source_prefab` after `properties` in the `SymbolNode` dataclass:

```python
    properties: dict[str, str] = field(default_factory=dict)
    source_prefab: str = ""
```

- [ ] **Step 5: Add source_prefab to to_dict()**

In `prefab_sentinel/symbol_tree.py`, in the `to_dict` method, add after `if self.properties:` block:

```python
        if self.source_prefab:
            result["source_prefab"] = self.source_prefab
```

- [ ] **Step 6: Run tests**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_symbol_tree.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add prefab_sentinel/symbol_tree.py tests/test_symbol_tree.py
git commit -m "feat: add SymbolKind.PREFAB_INSTANCE and SymbolNode.source_prefab"
```

---

### Task 4: Nested Prefab expansion in SymbolTree.build() (Section B — core logic)

**Files:**
- Modify: `prefab_sentinel/symbol_tree.py:111-278`
- Test: `tests/test_symbol_tree.py`

- [ ] **Step 1: Write tests for nested expansion**

In `tests/test_symbol_tree.py`, add a new test class. Import `Path`, `make_prefab_instance` and `CLASS_ID_PREFAB_INSTANCE`:

```python
import tempfile
from pathlib import Path
from prefab_sentinel.unity_yaml_parser import CLASS_ID_PREFAB_INSTANCE
from tests.yaml_helpers import make_prefab_instance
```

```python
class TestSymbolTreeNestedExpansion(unittest.TestCase):
    """SymbolTree.build with expand_nested=True."""

    CHILD_GUID = "aabbccdd11223344aabbccdd11223344"

    def _write_child_prefab(self, tmpdir: Path) -> Path:
        """Write a simple child prefab file and return its Path."""
        child_path = tmpdir / "Assets" / "Child.prefab"
        child_path.parent.mkdir(parents=True, exist_ok=True)
        child_text = (
            YAML_HEADER
            + make_gameobject("500", "ChildRoot", ["600"])
            + make_transform("600", "500")
            + make_meshrenderer("700", "500")
        )
        child_path.write_text(child_text)
        return child_path

    def _parent_text_with_instance(self) -> str:
        return (
            YAML_HEADER
            + make_gameobject("100", "Avatar", ["200"])
            + make_transform("200", "100")
            + make_prefab_instance("300", self.CHILD_GUID, transform_parent="200")
        )

    def test_expand_nested_false_skips_prefab_instances(self) -> None:
        text = self._parent_text_with_instance()
        tree = SymbolTree.build(text, "test.prefab", expand_nested=False)
        # Only the Avatar root — PrefabInstance block is not a GO
        self.assertEqual(len(tree.roots), 1)
        self.assertEqual(tree.roots[0].name, "Avatar")

    def test_expand_nested_true_without_guid_map_skips(self) -> None:
        text = self._parent_text_with_instance()
        tree = SymbolTree.build(text, "test.prefab", expand_nested=True, guid_to_asset_path=None)
        self.assertEqual(len(tree.roots), 1)

    def test_expand_nested_true_creates_marker_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            child_path = self._write_child_prefab(Path(tmpdir))
            guid_map = {self.CHILD_GUID: child_path}
            text = self._parent_text_with_instance()
            tree = SymbolTree.build(text, "test.prefab", expand_nested=True, guid_to_asset_path=guid_map)

            # Avatar root should have a PREFAB_INSTANCE child
            avatar = tree.roots[0]
            pi_nodes = [c for c in avatar.children if c.kind == SymbolKind.PREFAB_INSTANCE]
            self.assertEqual(len(pi_nodes), 1)
            pi = pi_nodes[0]
            self.assertEqual(pi.class_id, "1001")
            self.assertIn("Child.prefab", pi.source_prefab)

    def test_expanded_prefab_instance_has_child_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            child_path = self._write_child_prefab(Path(tmpdir))
            guid_map = {self.CHILD_GUID: child_path}
            text = self._parent_text_with_instance()
            tree = SymbolTree.build(text, "test.prefab", expand_nested=True, guid_to_asset_path=guid_map)

            avatar = tree.roots[0]
            pi = [c for c in avatar.children if c.kind == SymbolKind.PREFAB_INSTANCE][0]
            child_gos = [c for c in pi.children if c.kind == SymbolKind.GAME_OBJECT]
            self.assertEqual(len(child_gos), 1)
            self.assertEqual(child_gos[0].name, "ChildRoot")

    def test_unresolvable_guid_creates_unresolved_marker(self) -> None:
        text = self._parent_text_with_instance()
        guid_map: dict[str, Path] = {}  # GUID not in map
        tree = SymbolTree.build(text, "test.prefab", expand_nested=True, guid_to_asset_path=guid_map)

        avatar = tree.roots[0]
        pi_nodes = [c for c in avatar.children if c.kind == SymbolKind.PREFAB_INSTANCE]
        self.assertEqual(len(pi_nodes), 1)
        pi = pi_nodes[0]
        self.assertIn("Unresolved", pi.name)
        self.assertEqual(pi.children, [])

    def test_prefab_instance_marker_in_file_id_index(self) -> None:
        text = self._parent_text_with_instance()
        guid_map: dict[str, Path] = {}
        tree = SymbolTree.build(text, "test.prefab", expand_nested=True, guid_to_asset_path=guid_map)
        # The PrefabInstance marker node (fileID 300) should be in the index
        node = tree.resolve_file_id("300")
        self.assertIsNotNone(node)
        self.assertEqual(node.kind, SymbolKind.PREFAB_INSTANCE)

    def test_nested_child_nodes_not_in_file_id_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            child_path = self._write_child_prefab(Path(tmpdir))
            guid_map = {self.CHILD_GUID: child_path}
            text = self._parent_text_with_instance()
            tree = SymbolTree.build(text, "test.prefab", expand_nested=True, guid_to_asset_path=guid_map)
            # Child prefab's fileIDs (500, 600, 700) should NOT be in parent index
            self.assertIsNone(tree.resolve_file_id("500"))
            self.assertIsNone(tree.resolve_file_id("600"))

    def test_file_read_failure_creates_unresolved_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a child path that exists but is unreadable
            child_path = Path(tmpdir) / "Assets" / "Bad.prefab"
            child_path.parent.mkdir(parents=True, exist_ok=True)
            child_path.write_bytes(b"\x80\x81\x82")  # invalid UTF-8
            guid_map = {self.CHILD_GUID: child_path}
            text = self._parent_text_with_instance()
            tree = SymbolTree.build(text, "test.prefab", expand_nested=True, guid_to_asset_path=guid_map)
            avatar = tree.roots[0]
            pi_nodes = [c for c in avatar.children if c.kind == SymbolKind.PREFAB_INSTANCE]
            self.assertEqual(len(pi_nodes), 1)
            self.assertIn("Unresolved", pi_nodes[0].name)
            self.assertEqual(pi_nodes[0].children, [])

    def test_depth_limit_stops_expansion(self) -> None:
        text = self._parent_text_with_instance()
        guid_map: dict[str, Path] = {}
        # _depth=10 should skip expansion
        tree = SymbolTree.build(text, "test.prefab", expand_nested=True, guid_to_asset_path=guid_map, _depth=10)
        avatar = tree.roots[0]
        pi_nodes = [c for c in avatar.children if c.kind == SymbolKind.PREFAB_INSTANCE]
        # Should not create PI nodes at all when depth exceeded
        self.assertEqual(len(pi_nodes), 0)

    def test_to_dict_on_expanded_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            child_path = self._write_child_prefab(Path(tmpdir))
            guid_map = {self.CHILD_GUID: child_path}
            text = self._parent_text_with_instance()
            tree = SymbolTree.build(text, "test.prefab", expand_nested=True, guid_to_asset_path=guid_map)
            overview = tree.to_overview(depth=3)
            self.assertTrue(len(overview) > 0)
            # Find the PREFAB_INSTANCE node in serialized output
            avatar = overview[0]
            pi_dicts = [c for c in avatar.get("children", []) if c.get("kind") == "prefab_instance"]
            self.assertEqual(len(pi_dicts), 1)
            self.assertIn("source_prefab", pi_dicts[0])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_symbol_tree.py::TestSymbolTreeNestedExpansion -v`
Expected: FAIL (expand_nested parameter not accepted)

- [ ] **Step 3: Implement expand_nested in SymbolTree.build()**

In `prefab_sentinel/symbol_tree.py`, update the existing `unity_yaml_parser` import (line 22) to include `CLASS_ID_PREFAB_INSTANCE`:

```python
from prefab_sentinel.unity_yaml_parser import (
    CLASS_ID_MONOBEHAVIOUR,
    CLASS_ID_PREFAB_INSTANCE,
    TRANSFORM_CLASS_IDS,
    ComponentInfo,
    parse_components,
    parse_game_objects,
    parse_transforms,
    split_yaml_blocks,
)
```

Add a new import after the existing imports (after line 30):

```python
from prefab_sentinel.unity_assets import SOURCE_PREFAB_PATTERN, decode_text_file, normalize_guid
```

Add constants before the `SymbolTree` class (after the `_DUP_SEGMENT_RE` line):

```python
_MAX_NESTED_DEPTH = 10
_TRANSFORM_PARENT_RE = re.compile(r"m_TransformParent:\s*\{fileID:\s*(\d+)")
```

Update the `build()` signature and add expansion logic. The key changes to `build()`:

1. Add parameters: `expand_nested: bool = False`, `guid_to_asset_path: dict[str, Path] | None = None`, `_depth: int = 0`
2. After building roots (line 272), if `expand_nested` and `guid_to_asset_path` and `_depth < _MAX_NESTED_DEPTH`:
   - Scan blocks for class_id == CLASS_ID_PREFAB_INSTANCE
   - For each, extract m_SourcePrefab GUID via SOURCE_PREFAB_PATTERN
   - Resolve GUID → Path → read file → recursive build → create marker node
   - Attach marker node as child of the appropriate root GO (by Transform parent hierarchy, or as a root-level peer)
3. Register PrefabInstance marker nodes in file_id_index, but NOT their child nodes

The full updated `build()` method:

```python
@classmethod
def build(
    cls,
    text: str,
    asset_path: str = "",
    guid_to_script_name: dict[str, str] | None = None,
    *,
    include_properties: bool = False,
    expand_nested: bool = False,
    guid_to_asset_path: dict[str, Path] | None = None,
    _depth: int = 0,
) -> SymbolTree:
    """Build a symbol tree from Unity YAML text.

    Args:
        text: Raw Unity YAML content.
        asset_path: Asset file path (for display/identification).
        guid_to_script_name: Optional map of script GUID -> class name
            for resolving MonoBehaviour script names.
        include_properties: When True, populate property-level nodes
            for MonoBehaviour serialized fields.
        expand_nested: When True, expand PrefabInstance nodes into
            their child Prefab's tree (recursive).
        guid_to_asset_path: GUID -> Path map for resolving nested Prefabs.
            Required when expand_nested=True.
        _depth: Internal recursion depth counter (do not set externally).
    """
    script_map = guid_to_script_name or {}

    blocks = split_yaml_blocks(text)
    if not blocks:
        return cls(asset_path=asset_path)

    game_objects = parse_game_objects(blocks)
    transforms = parse_transforms(blocks)
    components = parse_components(blocks)

    # Build wiring data for property extraction
    wiring_by_fid: dict[str, list[tuple[str, str]]] = {}
    if include_properties:
        wiring = analyze_wiring(text, asset_path or "<unknown>")
        for comp in wiring.components:
            fields: list[tuple[str, str]] = []
            for f in comp.fields:
                fields.append((f.name, f.value))
            if fields:
                wiring_by_fid[comp.file_id] = fields

    # Maps for hierarchy traversal
    go_to_transform: dict[str, str] = {}
    transform_to_go: dict[str, str] = {}
    for t in transforms.values():
        if t.game_object_file_id:
            go_to_transform[t.game_object_file_id] = t.file_id
            transform_to_go[t.file_id] = t.game_object_file_id

    file_id_index: dict[str, SymbolNode] = {}

    # ... (keep _component_name, _build_component_node, _build_go_node unchanged) ...

    # Find root GameObjects
    root_go_fids: list[str] = []
    for go_fid in game_objects:
        t_fid = go_to_transform.get(go_fid, "")
        if t_fid and t_fid in transforms and transforms[t_fid].father_file_id in ("0", ""):
            root_go_fids.append(go_fid)

    roots = [_build_go_node(fid, 0) for fid in root_go_fids]

    # --- Nested Prefab expansion ---
    if expand_nested and guid_to_asset_path and _depth < _MAX_NESTED_DEPTH:
        for block in blocks:
            if block.class_id != CLASS_ID_PREFAB_INSTANCE:
                continue
            source_match = SOURCE_PREFAB_PATTERN.search(block.text)
            if source_match is None:
                continue
            guid = normalize_guid(source_match.group(2))
            child_path = guid_to_asset_path.get(guid)

            resolved = False
            child_text = ""
            if child_path is not None and child_path.exists():
                try:
                    child_text = decode_text_file(child_path)
                    resolved = True
                except (OSError, UnicodeDecodeError):
                    resolved = False

            if resolved:
                rel_path = child_path.as_posix()  # type: ignore[union-attr]
                child_tree = cls.build(
                    child_text,
                    rel_path,
                    guid_to_script_name,
                    include_properties=include_properties,
                    expand_nested=True,
                    guid_to_asset_path=guid_to_asset_path,
                    _depth=_depth + 1,
                )
                marker = SymbolNode(
                    kind=SymbolKind.PREFAB_INSTANCE,
                    name=f"[PrefabInstance: {rel_path}]",
                    file_id=block.file_id,
                    class_id=CLASS_ID_PREFAB_INSTANCE,
                    children=child_tree.roots,
                    source_prefab=rel_path,
                )
            else:
                marker = SymbolNode(
                    kind=SymbolKind.PREFAB_INSTANCE,
                    name=f"[Unresolved: {guid}]",
                    file_id=block.file_id,
                    class_id=CLASS_ID_PREFAB_INSTANCE,
                    children=[],
                    source_prefab=guid,
                )
            file_id_index[block.file_id] = marker

            # Attach to parent GO via m_TransformParent
            tp_match = _TRANSFORM_PARENT_RE.search(block.text)
            parent_t_fid = tp_match.group(1) if tp_match else "0"
            parent_go_fid = transform_to_go.get(parent_t_fid, "")
            parent_node = file_id_index.get(parent_go_fid)
            if parent_node is not None:
                parent_node.children.append(marker)
            else:
                roots.append(marker)

    return cls(
        asset_path=asset_path,
        roots=roots,
        _file_id_index=file_id_index,
    )
```

Note: The inner functions `_component_name`, `_build_component_node`, `_build_go_node` remain unchanged from the current code. Only the method signature and the post-root expansion block are new.

- [ ] **Step 4: Run tests**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_symbol_tree.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/symbol_tree.py tests/test_symbol_tree.py
git commit -m "feat: add Nested Prefab expansion to SymbolTree.build()"
```

---

### Task 5: PrefabInstance transparent path resolution (Section B — resolve)

**Files:**
- Modify: `prefab_sentinel/symbol_tree.py:310-356`
- Test: `tests/test_symbol_tree.py`

- [ ] **Step 1: Write test for transparent PrefabInstance resolution**

In `tests/test_symbol_tree.py`, add to `TestSymbolTreeNestedExpansion`:

```python
def test_resolve_through_prefab_instance(self) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        child_path = self._write_child_prefab(Path(tmpdir))
        guid_map = {self.CHILD_GUID: child_path}
        text = self._parent_text_with_instance()
        tree = SymbolTree.build(text, "test.prefab", expand_nested=True, guid_to_asset_path=guid_map)

        # Should resolve "Avatar" directly
        matches = tree.resolve("Avatar")
        self.assertEqual(len(matches), 1)

        # Should resolve "ChildRoot" through the PrefabInstance boundary
        matches = tree.resolve("ChildRoot")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].name, "ChildRoot")

def test_resolve_nested_path_through_prefab_instance(self) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        child_path = self._write_child_prefab(Path(tmpdir))
        guid_map = {self.CHILD_GUID: child_path}
        text = self._parent_text_with_instance()
        tree = SymbolTree.build(text, "test.prefab", expand_nested=True, guid_to_asset_path=guid_map)

        # ChildRoot/MeshRenderer should resolve through PrefabInstance
        matches = tree.resolve("ChildRoot/MeshRenderer")
        self.assertEqual(len(matches), 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_symbol_tree.py::TestSymbolTreeNestedExpansion::test_resolve_through_prefab_instance -v`
Expected: FAIL (PrefabInstance not transparent in resolve)

- [ ] **Step 3: Add PrefabInstance flattening to _resolve_segments**

In `prefab_sentinel/symbol_tree.py`, modify `_resolve_segments` (line 310) to flatten PrefabInstance nodes. The only change is adding a flatten step at the top and replacing all `candidates` references with `flat_candidates`. Note: MonoBehaviour-specific matching lives in `_segment_matches()` (a separate method) and is NOT affected by this change.

Current method (lines 310-356):

```python
def _resolve_segments(
    self,
    candidates: list[SymbolNode],
    segments: list[str],
    seg_idx: int,
) -> list[SymbolNode]:
    if seg_idx >= len(segments):
        return candidates

    # Flatten PrefabInstance nodes: replace with their children
    flat_candidates: list[SymbolNode] = []
    for node in candidates:
        if node.kind == SymbolKind.PREFAB_INSTANCE:
            flat_candidates.extend(node.children)
        else:
            flat_candidates.append(node)

    segment = segments[seg_idx]
    is_last = seg_idx == len(segments) - 1

    # Handle "#N" disambiguation at this level
    dup_match = _DUP_SEGMENT_RE.match(segment)
    if dup_match:
        base_name = dup_match.group(1)
        target_idx = int(dup_match.group(2))
        count = 0
        for node in flat_candidates:
            if node.name == base_name:
                if count == target_idx:
                    if is_last:
                        return [node]
                    return self._resolve_segments(
                        node.children, segments, seg_idx + 1
                    )
                count += 1
        return []

    # Match current segment against flat candidates
    matched = [n for n in flat_candidates if self._segment_matches(n, segment)]

    if is_last:
        return matched

    # Recurse into children of matched nodes for next segment
    results: list[SymbolNode] = []
    for node in matched:
        results.extend(
            self._resolve_segments(node.children, segments, seg_idx + 1)
        )
    return results
```

- [ ] **Step 4: Run all tests**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_symbol_tree.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/symbol_tree.py tests/test_symbol_tree.py
git commit -m "feat: transparent PrefabInstance path resolution in SymbolTree"
```

---

### Task 6: Session cache bypass for expand_nested (Section B — session)

**Files:**
- Modify: `prefab_sentinel/session.py:105-143`

- [ ] **Step 1: Write test**

There are no dedicated session tests in the project (session is tested through mcp_server integration). Add a focused unit test in `tests/test_symbol_tree.py`:

```python
class TestSessionCacheBypass(unittest.TestCase):
    """Session.get_symbol_tree bypasses cache when expand_nested=True."""

    def test_expand_nested_bypasses_cache(self) -> None:
        from prefab_sentinel.session import ProjectSession
        session = ProjectSession()
        text = YAML_HEADER + make_gameobject("100", "Root", ["200"]) + make_transform("200", "100")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.prefab"
            path.write_text(text)
            # First call caches
            tree1 = session.get_symbol_tree(path, text)
            # Second call with expand_nested should NOT return cached
            tree2 = session.get_symbol_tree(path, text, expand_nested=True)
            # They should be different objects (not cached)
            self.assertIsNot(tree1, tree2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_symbol_tree.py::TestSessionCacheBypass -v`
Expected: FAIL (expand_nested parameter not accepted)

- [ ] **Step 3: Add expand_nested parameter to get_symbol_tree**

In `prefab_sentinel/session.py`, modify `get_symbol_tree`:

```python
def get_symbol_tree(
    self,
    path: Path,
    text: str,
    *,
    include_properties: bool = False,
    expand_nested: bool = False,
    guid_to_asset_path: dict[str, Path] | None = None,
) -> SymbolTree:
    """Return a SymbolTree, using mtime-based caching.

    When *expand_nested* is True, the cache is bypassed (expanded trees
    depend on child prefab files whose mtime is not tracked).
    """
    if not expand_nested:
        mtime = self._stat_mtime(path)
        if mtime is not None:
            cached = self._symbol_cache.get(path)
            if (
                cached is not None
                and cached.mtime == mtime
                and (not include_properties or cached.include_properties)
            ):
                return cached.tree

    tree = SymbolTree.build(
        text,
        str(path),
        self.script_name_map(),
        include_properties=include_properties,
        expand_nested=expand_nested,
        guid_to_asset_path=guid_to_asset_path,
    )

    if not expand_nested:
        mtime = self._stat_mtime(path)
        if mtime is not None:
            self._symbol_cache[path] = _SymbolCacheEntry(
                mtime=mtime,
                include_properties=include_properties,
                tree=tree,
            )

    return tree
```

- [ ] **Step 4: Run tests**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_symbol_tree.py tests/test_mcp_server.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/session.py tests/test_symbol_tree.py
git commit -m "feat: bypass session cache for expand_nested=True"
```

---

### Task 7: MCP tool expand_nested parameter (Section B — MCP wiring)

**Files:**
- Modify: `prefab_sentinel/mcp_server.py:180-200`
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: Write delegation test**

In `tests/test_mcp_server.py`, add the test. Note: `session` is a closure variable inside `create_server()`, so we cannot import it directly. Instead, patch `SymbolTree.build` and verify the `expand_nested` argument propagates:

```python
def test_get_unity_symbols_expand_nested(self) -> None:
    server = create_server()
    mock_tree = MagicMock(to_overview=MagicMock(return_value=[]))
    with (
        patch("prefab_sentinel.mcp_server._read_asset", return_value=("yaml", Path("/test.prefab"))),
        patch("prefab_sentinel.symbol_tree.SymbolTree.build", return_value=mock_tree) as mock_build,
    ):
        _run(server.call_tool("get_unity_symbols", {"asset_path": "test.prefab", "expand_nested": True}))
    mock_build.assert_called_once()
    _, kwargs = mock_build.call_args
    self.assertTrue(kwargs.get("expand_nested"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_mcp_server.py -k "test_get_unity_symbols_expand_nested" -v`
Expected: FAIL

- [ ] **Step 3: Add expand_nested parameter to get_unity_symbols MCP tool**

In `prefab_sentinel/mcp_server.py`, update `get_unity_symbols`:

```python
@server.tool()
def get_unity_symbols(
    asset_path: str,
    depth: int = 1,
    expand_nested: bool = False,
) -> dict[str, Any]:
    """Get the symbol tree (GameObject/Component hierarchy) of a Unity asset.

    Args:
        asset_path: Asset file path (.prefab, .unity, .asset).
        depth: Expansion depth. 0=root GOs only, 1=GOs+components,
               2=components+properties.
        expand_nested: Expand Nested Prefab instances into the tree.
    """
    text, resolved = _read_asset(asset_path)
    include_props = depth >= 2
    guid_to_asset_path = None
    if expand_nested and session.project_root:
        from prefab_sentinel.unity_assets import collect_project_guid_index
        guid_to_asset_path = collect_project_guid_index(
            session.project_root, include_package_cache=False,
        )
    tree = session.get_symbol_tree(
        resolved, text,
        include_properties=include_props,
        expand_nested=expand_nested,
        guid_to_asset_path=guid_to_asset_path,
    )
    return {
        "asset_path": asset_path,
        "depth": depth,
        "symbols": tree.to_overview(depth=depth),
    }
```

- [ ] **Step 4: Run all tests**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_mcp_server.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: add expand_nested parameter to get_unity_symbols MCP tool"
```

---

### Task 8: Scene $scene handle fix for find_component (Section C)

**Files:**
- Modify: `prefab_sentinel/services/serialized_object.py` (in `_validate_scene_add_component_op`)
- Test: `tests/test_services.py`

- [ ] **Step 1: Write tests for find_component with $scene and add_component rejection**

In `tests/test_services.py`, add:

```python
def test_dry_run_scene_find_component_accepts_scene_handle(self) -> None:
    svc = SerializedObjectService()
    response = svc.dry_run_resource_plan(
        resource={
            "id": "scene",
            "kind": "scene",
            "path": "Assets/Test.unity",
            "mode": "open",
        },
        ops=[
            {"op": "open_scene"},
            {
                "op": "find_component",
                "target": "$scene",
                "type": "Camera",
                "result": "cam",
            },
            {"op": "save_scene"},
        ],
    )
    self.assertTrue(response.success)
    self.assertEqual("SER_DRY_RUN_OK", response.code)

def test_dry_run_scene_add_component_rejects_scene_handle(self) -> None:
    svc = SerializedObjectService()
    response = svc.dry_run_resource_plan(
        resource={
            "id": "scene",
            "kind": "scene",
            "path": "Assets/Test.unity",
            "mode": "open",
        },
        ops=[
            {"op": "open_scene"},
            {
                "op": "add_component",
                "target": "$scene",
                "type": "Light",
                "result": "light",
            },
            {"op": "save_scene"},
        ],
    )
    self.assertFalse(response.success)
    # Should report handle kind mismatch
    self.assertTrue(any("game object" in d.detail or "game object" in d.evidence for d in response.diagnostics))
```

- [ ] **Step 2: Run tests to verify current behavior**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_services.py -k "test_dry_run_scene_find_component_accepts_scene_handle or test_dry_run_scene_add_component_rejects_scene_handle" -v`
Expected: `find_component` test FAILS (currently rejects $scene), `add_component` test PASSES (already rejects)

- [ ] **Step 3: Fix _validate_scene_add_component_op**

In `prefab_sentinel/services/serialized_object.py`, find `_validate_scene_add_component_op` and change the `expected_kind` for the `_require_handle_ref` call:

```python
expected_kind = {"scene", "game_object"} if op_name == "find_component" else "game_object"
object_handle = self._require_handle_ref(
    target=ctx.target,
    index=index,
    field="target",
    op=op,
    known_handles=ctx.known_handles,
    diagnostics=ctx.diagnostics,
    expected_kind=expected_kind,
)
```

- [ ] **Step 4: Run tests**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_services.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/services/serialized_object.py tests/test_services.py
git commit -m "fix: allow find_component with \$scene handle in scene mode"
```

---

### Task 9: MaterialInspectionResult.diagnostics + RendererMaterials.source_prefab (Section D — data model)

**Files:**
- Modify: `prefab_sentinel/material_inspector.py:150-167`

- [ ] **Step 1: Write test for new fields**

In `tests/test_material_inspector.py`, add:

```python
class TestMaterialDataModelExtensions(unittest.TestCase):
    """New fields: RendererMaterials.source_prefab and MaterialInspectionResult.diagnostics."""

    def test_renderer_materials_source_prefab_default(self) -> None:
        r = RendererMaterials(
            game_object_name="Body",
            renderer_type="SkinnedMeshRenderer",
            file_id="100",
            slots=[],
        )
        self.assertEqual(r.source_prefab, "")

    def test_material_inspection_result_diagnostics_default(self) -> None:
        result = MaterialInspectionResult(
            target_path="test.prefab",
            is_variant=False,
            base_prefab_path=None,
            renderers=[],
        )
        self.assertEqual(result.diagnostics, [])

    def test_format_materials_includes_diagnostics(self) -> None:
        result = MaterialInspectionResult(
            target_path="test.prefab",
            is_variant=False,
            base_prefab_path=None,
            renderers=[],
            diagnostics=["No renderers found in base or nested prefabs"],
        )
        text = format_materials(result)
        self.assertIn("[diagnostic]", text)
        self.assertIn("No renderers found", text)
```

Add imports at top of file:

```python
from prefab_sentinel.material_inspector import RendererMaterials, MaterialInspectionResult
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_material_inspector.py::TestMaterialDataModelExtensions -v`
Expected: FAIL

- [ ] **Step 3: Add source_prefab to RendererMaterials**

In `prefab_sentinel/material_inspector.py`, update `RendererMaterials`:

```python
@dataclass(slots=True)
class RendererMaterials:
    """Material slots for a single renderer component."""

    game_object_name: str
    renderer_type: str
    file_id: str
    slots: list[MaterialSlot]
    source_prefab: str = ""
```

- [ ] **Step 4: Add diagnostics to MaterialInspectionResult**

```python
from dataclasses import dataclass, field

@dataclass(slots=True)
class MaterialInspectionResult:
    """Complete material inspection result."""

    target_path: str
    is_variant: bool
    base_prefab_path: str | None
    renderers: list[RendererMaterials]
    diagnostics: list[str] = field(default_factory=list)
```

Note: Add `field` to the existing `from dataclasses import dataclass` import.

- [ ] **Step 5: Update format_materials to show diagnostics**

In `prefab_sentinel/material_inspector.py`, update `format_materials`:

```python
def format_materials(result: MaterialInspectionResult) -> str:
    """Format material inspection result as human-readable text."""
    if not result.renderers and not result.diagnostics:
        return "(no renderer components found)"

    lines: list[str] = []
    if not result.renderers:
        lines.append("(no renderer components found)")
    for renderer in result.renderers:
        lines.append(f"{renderer.game_object_name} ({renderer.renderer_type})")
        if not renderer.slots:
            lines.append("  (no materials)")
        for slot in renderer.slots:
            name = slot.material_name or "(none)"
            path_part = f" ({slot.material_path})" if slot.material_path else ""
            if result.is_variant:
                marker = "[override]" if slot.is_override else "[inherited]"
                lines.append(f"  [{slot.index}] {name}{path_part}  {marker}")
            else:
                lines.append(f"  [{slot.index}] {name}{path_part}")
    for diag in result.diagnostics:
        lines.append(f"[diagnostic] {diag}")
    return "\n".join(lines)
```

- [ ] **Step 6: Run all material tests**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_material_inspector.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add prefab_sentinel/material_inspector.py tests/test_material_inspector.py
git commit -m "feat: add source_prefab and diagnostics to material inspection data model"
```

---

### Task 10: Nested Prefab fallback in _inspect_variant_materials (Section D — core logic)

**Files:**
- Modify: `prefab_sentinel/material_inspector.py:324-447`
- Test: `tests/test_material_inspector.py`

- [ ] **Step 1: Write tests for nested fallback**

In `tests/test_material_inspector.py`, add:

```python
import tempfile
from pathlib import Path
from tests.yaml_helpers import (
    YAML_HEADER,
    make_gameobject,
    make_transform,
    make_prefab_instance,
    make_skinned_mesh_renderer,
)


class TestNestedPrefabMaterialFallback(unittest.TestCase):
    """Section D: Variant with renderer in nested (child) prefab."""

    CHILD_GUID = "cc112233445566778899aabbccddeeff"
    MAT_GUID = "aaaa1111bbbb2222cccc3333dddd4444"

    def _setup_project(self, tmpdir: Path) -> tuple[Path, Path]:
        """Create a project with base prefab containing PrefabInstance + child prefab with renderer."""
        project = tmpdir / "Assets"
        project.mkdir()

        # Child prefab with a SkinnedMeshRenderer
        child = project / "Child.prefab"
        child_text = (
            YAML_HEADER
            + make_gameobject("500", "Body", ["600", "700"])
            + make_transform("600", "500")
            + make_skinned_mesh_renderer("700", "500", material_guids=[self.MAT_GUID])
        )
        child.write_text(child_text)

        # Child.prefab.meta
        meta = project / "Child.prefab.meta"
        meta.write_text(f"fileFormatVersion: 2\nguid: {self.CHILD_GUID}\n")

        # Material .mat file
        mat = project / "TestMat.mat"
        mat.write_text("%YAML 1.1\n--- !u!21 &2100000\nMaterial:\n  m_Name: TestMat\n")
        mat_meta = project / "TestMat.mat.meta"
        mat_meta.write_text(f"fileFormatVersion: 2\nguid: {self.MAT_GUID}\n")

        # Base prefab — has PrefabInstance but NO renderer blocks
        base = project / "Base.prefab"
        base_text = (
            YAML_HEADER
            + make_gameobject("100", "Avatar", ["200"])
            + make_transform("200", "100")
            + make_prefab_instance("300", self.CHILD_GUID)
        )
        base.write_text(base_text)

        base_guid = "11112222333344445555666677778888"
        base_meta = project / "Base.prefab.meta"
        base_meta.write_text(f"fileFormatVersion: 2\nguid: {base_guid}\n")

        # Variant referencing base — also has no renderer blocks
        variant = project / "Variant.prefab"
        variant_text = (
            YAML_HEADER
            + f"--- !u!1001 &100100000\n"
            + f"PrefabInstance:\n"
            + f"  m_Modification:\n"
            + f"    m_TransformParent: {{fileID: 0}}\n"
            + f"    m_Modifications: []\n"
            + f"  m_SourcePrefab: {{fileID: 100100000, guid: {base_guid}, type: 3}}\n"
        )
        variant.write_text(variant_text)

        return tmpdir, variant

    def test_nested_fallback_finds_renderer_in_child_prefab(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root, variant = self._setup_project(Path(tmpdir))
            result = inspect_materials(str(variant), project_root=project_root)
            self.assertGreater(len(result.renderers), 0)
            self.assertEqual(result.renderers[0].game_object_name, "Body")

    def test_nested_fallback_sets_source_prefab(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root, variant = self._setup_project(Path(tmpdir))
            result = inspect_materials(str(variant), project_root=project_root)
            self.assertGreater(len(result.renderers), 0)
            self.assertIn("Child.prefab", result.renderers[0].source_prefab)

    def test_nested_fallback_no_renderer_anywhere_produces_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project = Path(tmpdir) / "Assets"
            project.mkdir()

            # Child prefab with NO renderer
            child = project / "Child.prefab"
            child_text = (
                YAML_HEADER
                + make_gameobject("500", "Empty", ["600"])
                + make_transform("600", "500")
            )
            child.write_text(child_text)
            meta = project / "Child.prefab.meta"
            meta.write_text(f"fileFormatVersion: 2\nguid: {self.CHILD_GUID}\n")

            # Base with PrefabInstance
            base = project / "Base.prefab"
            base_text = (
                YAML_HEADER
                + make_gameobject("100", "Avatar", ["200"])
                + make_transform("200", "100")
                + make_prefab_instance("300", self.CHILD_GUID)
            )
            base.write_text(base_text)
            base_guid = "11112222333344445555666677778888"
            base_meta = project / "Base.prefab.meta"
            base_meta.write_text(f"fileFormatVersion: 2\nguid: {base_guid}\n")

            # Variant
            variant = project / "Variant.prefab"
            variant_text = (
                YAML_HEADER
                + f"--- !u!1001 &100100000\n"
                + f"PrefabInstance:\n"
                + f"  m_Modification:\n"
                + f"    m_TransformParent: {{fileID: 0}}\n"
                + f"    m_Modifications: []\n"
                + f"  m_SourcePrefab: {{fileID: 100100000, guid: {base_guid}, type: 3}}\n"
            )
            variant.write_text(variant_text)

            result = inspect_materials(str(variant), project_root=Path(tmpdir))
            self.assertEqual(len(result.renderers), 0)
            self.assertGreater(len(result.diagnostics), 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_material_inspector.py::TestNestedPrefabMaterialFallback -v`
Expected: FAIL

- [ ] **Step 3: Implement Nested Prefab fallback**

In `prefab_sentinel/material_inspector.py`, add import:

```python
from prefab_sentinel.unity_yaml_parser import CLASS_ID_PREFAB_INSTANCE
```

In `_inspect_variant_materials`, after the `_build_stripped_renderer_materials` fallback (after line 440), add the third fallback:

```python
    # Fallback 2: stripped renderers (existing)
    if not renderers:
        renderers = _build_stripped_renderer_materials(
            base_blocks, base_text, variant_text,
            guid_index, project_root, material_overrides,
        )

    # Fallback 3: Nested Prefab expansion — renderer in a child prefab
    diagnostics: list[str] = []  # always initialized (used in return below)
    if not renderers and base_text is not None:
        renderers, diagnostics = _collect_nested_renderers(
            base_text, guid_index, project_root,
        )

    return MaterialInspectionResult(
        target_path=target_path,
        is_variant=True,
        base_prefab_path=base_prefab_path_str,
        renderers=renderers,
        diagnostics=diagnostics,
    )
```

Add the new `_collect_nested_renderers` function:

```python
def _collect_nested_renderers(
    base_text: str,
    guid_index: dict[str, Path],
    project_root: Path,
) -> tuple[list[RendererMaterials], list[str]]:
    """Collect renderers from Nested Prefab instances in *base_text*.

    Returns (renderers, diagnostics).
    """
    blocks = split_yaml_blocks(base_text)
    renderers: list[RendererMaterials] = []

    for block in blocks:
        if block.class_id != CLASS_ID_PREFAB_INSTANCE:
            continue
        source_match = SOURCE_PREFAB_PATTERN.search(block.text)
        if source_match is None:
            continue
        child_guid = normalize_guid(source_match.group(2))
        child_path = guid_index.get(child_guid)
        if child_path is None or not child_path.exists():
            continue
        try:
            child_text = decode_text_file(child_path)
        except (OSError, UnicodeDecodeError):
            continue

        child_result = _inspect_base_materials(
            str(child_path), child_text, project_root, guid_index,
        )
        # Stamp source_prefab on each renderer
        try:
            rel = child_path.resolve().relative_to(project_root.resolve()).as_posix()
        except ValueError:
            rel = child_path.as_posix()
        for r in child_result.renderers:
            r.source_prefab = rel
        renderers.extend(child_result.renderers)

    diagnostics: list[str] = []
    if not renderers:
        diagnostics.append(
            "No renderer blocks found in base prefab or nested prefabs"
        )
    return renderers, diagnostics
```

- [ ] **Step 4: Run tests**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_material_inspector.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/material_inspector.py tests/test_material_inspector.py
git commit -m "feat: add Nested Prefab fallback for variant material inspection"
```

---

### Task 11: Orchestrator wiring for Section D fields (Section D — orchestrator)

**Files:**
- Modify: `prefab_sentinel/orchestrator.py:935-975`

- [ ] **Step 1: Write test**

The orchestrator `inspect_materials` is tested via integration — add a focused check in `tests/test_material_inspector.py`:

```python
class TestOrchestratorMaterialsSerialization(unittest.TestCase):
    """Orchestrator includes source_prefab and diagnostics in response."""

    def test_orchestrator_includes_source_prefab(self) -> None:
        from prefab_sentinel.orchestrator import Phase1Orchestrator
        with tempfile.TemporaryDirectory() as tmpdir:
            # Reuse the nested setup from TestNestedPrefabMaterialFallback
            project = Path(tmpdir) / "Assets"
            project.mkdir()

            child_guid = "cc112233445566778899aabbccddeeff"
            mat_guid = "aaaa1111bbbb2222cccc3333dddd4444"

            child = project / "Child.prefab"
            child_text = (
                YAML_HEADER
                + make_gameobject("500", "Body", ["600", "700"])
                + make_transform("600", "500")
                + make_skinned_mesh_renderer("700", "500", material_guids=[mat_guid])
            )
            child.write_text(child_text)
            (project / "Child.prefab.meta").write_text(f"fileFormatVersion: 2\nguid: {child_guid}\n")
            (project / "TestMat.mat").write_text("%YAML 1.1\n--- !u!21 &2100000\nMaterial:\n  m_Name: TestMat\n")
            (project / "TestMat.mat.meta").write_text(f"fileFormatVersion: 2\nguid: {mat_guid}\n")

            base = project / "Base.prefab"
            base_text = (
                YAML_HEADER
                + make_gameobject("100", "Avatar", ["200"])
                + make_transform("200", "100")
                + make_prefab_instance("300", child_guid)
            )
            base.write_text(base_text)
            base_guid = "11112222333344445555666677778888"
            (project / "Base.prefab.meta").write_text(f"fileFormatVersion: 2\nguid: {base_guid}\n")

            variant = project / "Variant.prefab"
            variant_text = (
                YAML_HEADER
                + f"--- !u!1001 &100100000\n"
                + f"PrefabInstance:\n"
                + f"  m_Modification:\n"
                + f"    m_TransformParent: {{fileID: 0}}\n"
                + f"    m_Modifications: []\n"
                + f"  m_SourcePrefab: {{fileID: 100100000, guid: {base_guid}, type: 3}}\n"
            )
            variant.write_text(variant_text)

            orch = Phase1Orchestrator.default(project_root=Path(tmpdir))
            response = orch.inspect_materials(str(variant))
            self.assertTrue(response.success)
            renderers = response.data.get("renderers", [])
            self.assertGreater(len(renderers), 0)
            self.assertIn("source_prefab", renderers[0])
            self.assertIn("Child.prefab", renderers[0]["source_prefab"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_material_inspector.py::TestOrchestratorMaterialsSerialization -v`
Expected: FAIL (source_prefab not in renderer dict)

- [ ] **Step 3: Update orchestrator inspect_materials serialization**

In `prefab_sentinel/orchestrator.py`, in the `inspect_materials` method, update the renderer serialization loop (around line 947):

```python
        renderer_data.append({
            "game_object_name": renderer.game_object_name,
            "renderer_type": renderer.renderer_type,
            "file_id": renderer.file_id,
            "slot_count": len(renderer.slots),
            "slots": slot_data,
            **({"source_prefab": renderer.source_prefab} if renderer.source_prefab else {}),
        })
```

After the renderer loop, add diagnostics to the data dict (around line 963):

```python
        if result.diagnostics:
            data["diagnostics"] = result.diagnostics
```

- [ ] **Step 4: Run all tests**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/orchestrator.py tests/test_material_inspector.py
git commit -m "feat: serialize source_prefab and diagnostics in orchestrator inspect_materials"
```

---

### Task 12: Update README and final validation

**Files:**
- Modify: `README.md` (if needed for new parameters)

- [ ] **Step 1: Run full test suite**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 2: Run mypy**

Run: `cd /mnt/d/git/prefab-sentinel && uv run mypy prefab_sentinel/ --ignore-missing-imports`
Expected: no errors

- [ ] **Step 3: Run ruff**

Run: `cd /mnt/d/git/prefab-sentinel && uv run ruff check prefab_sentinel/ tests/`
Expected: no errors

- [ ] **Step 4: Commit any documentation updates**

```bash
git add README.md
git commit -m "docs: update README for editor_screenshot refresh and expand_nested"
```
