# activate_project ナレッジコンテキスト設計

**日付:** 2026-03-27
**由来:** MCP アクティベート時にエージェントが読むべきドキュメントを自動提示するアイデア

---

## 概要

2 つの仕組みを組み合わせて、AI エージェントの初動コンテキストを改善する:

1. **A: suggested_reads** — `activate_project` のレスポンスに「今すぐ読むべき」knowledge ファイルを返す
2. **B: MCP Resources** — knowledge/*.md を MCP Resource として登録し、作業中にオンデマンドで取得可能にする

---

## 前提: knowledge ディレクトリのパス解決

`knowledge/` ディレクトリはリポジトリルートに存在し、`prefab_sentinel/` パッケージには含まれない。パス解決は `Path(__file__).parent.parent / "knowledge"` で行う (source tree / editable install 前提)。ディレクトリが存在しない場合は空リストを返す (wheel 配布ではナレッジ機能は利用不可)。

---

## A: suggested_reads in activate_project

### マッピングロジック

`session.suggest_reads()` メソッドを追加。3 段構え:

1. **prefab-sentinel 自身の knowledge** (常に返す):
   - `prefab-sentinel-workflow-patterns.md`
   - `prefab-sentinel-patch-patterns.md`
   - `prefab-sentinel-wiring-triage.md`
   - `prefab-sentinel-variant-patterns.md`
   - `prefab-sentinel-material-operations.md`
   - `prefab-sentinel-editor-camera.md`

2. **VRChat エコシステム knowledge** (キーワード検出):
   - 検出ソース 1: `script_name_map()` の **値** (ファイル stem = クラス名)
   - 検出ソース 2: `guid_index` の **値** (アセットパス) — shader, `.asmdef` 等の非 C# アセットをカバー
   - マッチングアルゴリズム: 各キーワード K について、ソースの値に **case-insensitive substring** として K が含まれるかチェック

3. **knowledge_hint** でその他のファイルへの導線を提供

マッピングテーブル:

```python
_KEYWORD_TO_KNOWLEDGE: dict[str, str] = {
    # C# scripts (detected via script_name_map values)
    "UdonSharp": "udonsharp.md",
    "UdonBehaviour": "udonsharp.md",
    "VRCSceneDescriptor": "vrchat-sdk-worlds.md",
    "VRC_SceneDescriptor": "vrchat-sdk-worlds.md",
    "VRCAvatarDescriptor": "vrchat-sdk-avatars.md",
    "ModularAvatar": "modular-avatar.md",
    "VRCFury": "vrcfury.md",
    "AvatarOptimizer": "avatar-optimizer.md",
    # Non-C# assets (detected via guid_index asset paths)
    "lilToon": "liltoon.md",
    "liltoon": "liltoon.md",
    "NDMF": "ndmf.md",
    "nadena.dev.ndmf": "ndmf.md",
}
```

**検出できないナレッジファイルの扱い:** マッピングテーブルにないコミュニティツール (kawaii-posing, face-emo 等) は `suggested_reads` に含まれない。`knowledge_hint` + MCP Resources (B) でカバーする。新ツールのナレッジを追加した際はマッピングテーブルも更新する。

**`vrchat-sdk-base.md`** は VRChat SDK 共通基盤知識。VRC 系キーワードが 1 つでもマッチしたら一緒に返す。

### 重複排除と順序

- `set` で重複排除し、`sorted()` で安定した順序を返す
- prefab-sentinel knowledge が先、エコシステム knowledge が後

### レスポンス

```json
{
  "data": {
    "suggested_reads": [
      "knowledge/prefab-sentinel-workflow-patterns.md",
      "knowledge/prefab-sentinel-patch-patterns.md",
      "knowledge/udonsharp.md",
      "knowledge/vrchat-sdk-base.md",
      "knowledge/vrchat-sdk-worlds.md"
    ],
    "knowledge_hint": "Other knowledge files available via Glob('knowledge/*.md') or MCP Resources (resource://prefab-sentinel/knowledge/)"
  }
}
```

`suggested_reads` は `activate_project` のレスポンス `data` に追加。

---

## B: MCP Resources

knowledge/*.md を全件 MCP Resource として登録。

### URI スキーム

`resource://prefab-sentinel/knowledge/{filename}` (例: `resource://prefab-sentinel/knowledge/udonsharp.md`)

MCP SDK の標準 `resource://` スキームにプロジェクト名前空間を付与。

### パス解決

```python
_KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"
```

存在チェック: `_KNOWLEDGE_DIR.is_dir()` が False なら Resources を登録しない。

### 登録

```python
for md_file in sorted(_KNOWLEDGE_DIR.glob("*.md")):
    uri = f"resource://prefab-sentinel/knowledge/{md_file.name}"
    name = md_file.stem  # e.g. "udonsharp"

    @server.resource(uri, name=name, description=_extract_description(md_file))
    def read_knowledge(*, _path: Path = md_file) -> str:
        return _path.read_text(encoding="utf-8")
```

### 概要の抽出

YAML frontmatter に `description` フィールドがあればそれを使う。なければ `tool` フィールドがあれば `"{tool} knowledge"` を返す。どちらもなければファイル名の stem をそのまま返す。

```python
def _extract_description(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            import yaml
            try:
                fm = yaml.safe_load(text[3:end])
                if isinstance(fm, dict):
                    if "description" in fm:
                        return fm["description"]
                    if "tool" in fm:
                        return f"{fm['tool']} knowledge"
            except Exception:
                pass
    return path.stem
```

注意: `yaml` は既存依存 (`pyyaml`) として利用可能。

---

## 変更ファイル

| ファイル | 変更 |
|---------|------|
| `prefab_sentinel/session.py` | `suggest_reads()` メソッド追加 (マッピングテーブル + script_name_map/guid_index 検索) |
| `prefab_sentinel/mcp_server.py` | activate_project レスポンスに suggested_reads 追加、MCP Resources 動的登録 |
| `tests/test_session.py` | suggest_reads テスト (キーワードマッチ、空 map、重複排除、knowledge 未存在) |
| `tests/test_mcp_server.py` | ツール登録テスト更新 |

## スコープ外

- Skills の SKILL.md を Resources に登録 — Plugin 側で既にロード済み
- knowledge ファイル内容のフルテキストスキャンによる動的マッピング — パフォーマンスコスト高
- knowledge ファイルの自動生成・更新 — 既存の手動運用を維持
- wheel 配布での knowledge 対応 — editable/source install 前提で十分
