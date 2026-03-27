# activate_project ナレッジコンテキスト設計

**日付:** 2026-03-27
**由来:** MCP アクティベート時にエージェントが読むべきドキュメントを自動提示するアイデア

---

## 概要

2 つの仕組みを組み合わせて、AI エージェントの初動コンテキストを改善する:

1. **A: suggested_reads** — `activate_project` のレスポンスに「今すぐ読むべき」knowledge ファイルを返す
2. **B: MCP Resources** — knowledge/*.md を MCP Resource として登録し、作業中にオンデマンドで取得可能にする

---

## A: suggested_reads in activate_project

### マッピングロジック

`session.suggest_reads()` メソッドを追加。2 段構え:

1. **prefab-sentinel 自身の knowledge** (常に返す):
   - `prefab-sentinel-workflow-patterns.md`
   - `prefab-sentinel-patch-patterns.md`
   - `prefab-sentinel-wiring-triage.md`

2. **VRChat エコシステム knowledge** (script_name_map から検出):
   - script_name_map のスクリプト名やクラス名からキーワードマッチ
   - 例: `UdonSharpBehaviour` 検出 → `udonsharp.md`
   - 例: `VRC_SceneDescriptor` 検出 → `vrchat-sdk-worlds.md`

マッピングテーブル:

```python
_KEYWORD_TO_KNOWLEDGE: dict[str, str] = {
    "UdonSharp": "udonsharp.md",
    "UdonBehaviour": "udonsharp.md",
    "VRCSceneDescriptor": "vrchat-sdk-worlds.md",
    "VRC_SceneDescriptor": "vrchat-sdk-worlds.md",
    "VRCAvatarDescriptor": "vrchat-sdk-avatars.md",
    "ModularAvatar": "modular-avatar.md",
    "lilToon": "liltoon.md",
    "VRCFury": "vrcfury.md",
    "NDMF": "ndmf.md",
    "AvatarOptimizer": "avatar-optimizer.md",
}
```

検出ソース: `session.script_name_map()` のスクリプト名 (既に warm 済み)。追加のファイルスキャン不要。

### レスポンス

```json
{
  "data": {
    "suggested_reads": [
      "knowledge/prefab-sentinel-workflow-patterns.md",
      "knowledge/udonsharp.md",
      "knowledge/vrchat-sdk-worlds.md"
    ],
    "knowledge_hint": "Other knowledge files available: list with Glob('knowledge/*.md') or ListMcpResources"
  }
}
```

`suggested_reads` は `activate_project` のレスポンス `data` に追加。

---

## B: MCP Resources

knowledge/*.md を全件 MCP Resource として登録。

### URI スキーム

`knowledge://{filename}` (例: `knowledge://udonsharp.md`)

### 登録

```python
@server.resource("knowledge://{name}")
def read_knowledge(name: str) -> str:
    path = knowledge_dir / name
    return path.read_text(encoding="utf-8")
```

サーバー起動時に `knowledge/` ディレクトリをスキャンし、存在するファイルを動的に登録する。

### リスト

`ListMcpResources` で全 knowledge ファイルの URI + 概要を返す。概要はファイルの YAML frontmatter `description` から抽出 (存在すれば)。

---

## 変更ファイル

| ファイル | 変更 |
|---------|------|
| `prefab_sentinel/session.py` | `suggest_reads()` メソッド追加 (マッピングテーブル + script_name_map 検索) |
| `prefab_sentinel/mcp_server.py` | activate_project レスポンスに suggested_reads 追加、MCP Resources 登録 |
| `tests/test_session.py` | suggest_reads テスト |
| `tests/test_mcp_server.py` | ツール登録テスト更新 |

## スコープ外

- Skills の SKILL.md を Resources に登録 — Plugin 側で既にロード済み
- knowledge ファイル内容のスキャンによる動的マッピング — script_name_map ベースで十分
- knowledge ファイルの自動生成・更新 — 既存の手動運用を維持
