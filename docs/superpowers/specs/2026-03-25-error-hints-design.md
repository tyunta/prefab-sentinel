# Phase 5.3: "Did you mean...?" エラーヒント — Design Spec

## 背景

MCP 統合テストで、AI エージェントが symbol_path やプロパティ名を 1-2 文字間違えてバリデーションエラーになるケースが頻発した。エラーレスポンスに類似候補を含めることで、エージェントが自力でリトライできるようにする。

## スコープ

| # | エラーパス | コード | 候補ソース | 実装場所 |
|---|---|---|---|---|
| A | symbol_path 不一致 | `SYMBOL_NOT_FOUND` | SymbolTree 全ノード名 | `mcp_server.py` (3 箇所: `set_property`, `add_component`, `remove_component`) |
| B | material プロパティ名不一致 | `MAT_PROP_NOT_FOUND` | `_list_all_property_names()` | `material_asset_inspector.py` |
| C | シェーダープロパティ名不一致 | `EDITOR_CTRL_PROPERTY_NOT_FOUND` | `shader.GetPropertyCount()` ループ | `UnityEditorControlBridge.cs` |

### スコープ外のエラーパス

- `editor_set_material` — マテリアルスロット番号とGUID で操作するため、シェーダープロパティ名を受け取らない。`EDITOR_CTRL_PROPERTY_NOT_FOUND` は発生しない。
- `EDITOR_CTRL_OBJECT_NOT_FOUND` — 候補リスト（シーン全 GameObject）の取得コストが高い。
- `SYMBOL_AMBIGUOUS` — 既に複数候補が情報として返されている。

## アルゴリズム

### Python

`difflib.get_close_matches()` を使用。外部依存なし。

```python
# prefab_sentinel/fuzzy_match.py
from difflib import get_close_matches

def suggest_similar(
    word: str,
    candidates: Iterable[str],
    *,
    n: int = 3,
    cutoff: float = 0.6,
) -> list[str]:
    """Return up to *n* candidates similar to *word*.

    Uses difflib.SequenceMatcher (ratio >= cutoff).
    Returns empty list when no candidate exceeds the threshold.
    """
    return get_close_matches(word, list(candidates), n=n, cutoff=cutoff)
```

薄いラッパーだが、閾値のデフォルト値を一箇所に集約し、テスト対象を明確にするために独立モジュールにする。

### C#

Unity は標準ライブラリに fuzzy match がないため、Levenshtein 距離を手実装する。

```csharp
static int LevenshteinDistance(string a, string b) { ... }

static List<string> SuggestSimilar(string word, IEnumerable<string> candidates, int maxResults = 3)
```

閾値: `距離 <= max(word.Length, candidate.Length) * 0.4`。

### Python / C# 間のアルゴリズム差異

Python は `SequenceMatcher`（最長共通部分列ベース）、C# は Levenshtein（編集距離ベース）で内部アルゴリズムが異なる。転置や繰り返し文字で結果が異なりうるが、主な利用対象（シェーダープロパティ名: 短い ASCII 文字列、symbol_path: 階層パス）ではいずれも 1-2 文字の typo を十分検出できる。Python/C# 間の結果の完全一致は目標としない。

## レスポンス形式

既存のエンベロープ構造を変更しない。`data` オブジェクトに `suggestions` フィールドを追加する。

### Python 側 (A: SYMBOL_NOT_FOUND)

```json
{
  "success": false,
  "severity": "error",
  "code": "SYMBOL_NOT_FOUND",
  "message": "No component found at symbol path: 'Cube/MeshRendrer'",
  "data": {
    "asset_path": "Assets/Prefabs/Player.prefab",
    "symbol_path": "Cube/MeshRendrer",
    "suggestions": ["Cube/MeshRenderer", "Cube/MeshFilter"]
  },
  "diagnostics": []
}
```

### Python 側 (B: MAT_PROP_NOT_FOUND)

```json
{
  "success": false,
  "severity": "error",
  "code": "MAT_PROP_NOT_FOUND",
  "message": "Property '_Colr' not found in Hair.mat",
  "data": {
    "available_properties": ["_Color", "_MainTex", "_BumpMap"],
    "suggestions": ["_Color"]
  },
  "diagnostics": [{"detail": "Available: _Color, _MainTex, _BumpMap"}]
}
```

`available_properties` は既存フィールドとして維持。`suggestions` は fuzzy match で絞り込んだサブセット。

### C# 側 (C: EDITOR_CTRL_PROPERTY_NOT_FOUND)

```json
{
  "success": false,
  "severity": "error",
  "code": "EDITOR_CTRL_PROPERTY_NOT_FOUND",
  "message": "Property '_Colr' not found on shader 'lilToon'",
  "data": {
    "suggestions": ["_Color", "_ColorMask"]
  },
  "diagnostics": []
}
```

### `suggestions` フィールドの出現ルール

- **Python 側**: `suggestions` はエラーレスポンスの `data` にのみ含める。成功レスポンスには含めない。
- **C# 側**: `EditorControlData` は `JsonUtility.ToJson()` で全フィールドをシリアライズするため、`suggestions` フィールドを追加すると全レスポンス（成功含む）に `"suggestions": []` が出現する。空配列は無害なので許容する。

### 候補なしの場合

`suggestions` は空配列 `[]` を返す。フィールド自体は常に存在させる（呼び出し側の null チェック不要にするため）。

## C# DTO 変更

### EditorControlData

`suggestions` フィールドを追加:

```csharp
public string[] suggestions = System.Array.Empty<string>();
```

`JsonUtility` は全フィールドをシリアライズするため、全レスポンスに `"suggestions": []` が含まれるようになる（後方互換性に問題なし — 追加フィールドは無視されるのが JSON の慣例）。

### BuildError オーバーロード

現行の `BuildError(string code, string message)` は `EditorControlData` を生成するが `suggestions` を設定できない。`EditorControlData` を受け取るオーバーロードを追加する:

```csharp
static EditorControlResponse BuildError(string code, string message, EditorControlData data)
{
    return new EditorControlResponse
    {
        success = false,
        severity = "error",
        code = code,
        message = message,
        data = data,
    };
}
```

`EDITOR_CTRL_PROPERTY_NOT_FOUND` を返す箇所でこのオーバーロードを使い、`data.suggestions` を設定する。

## 候補リスト取得

### A: SymbolTree ノード名

`SymbolNotFoundError` が発生した時点で SymbolTree は構築済み。全ノードの symbol path を収集して候補リストにする。

```python
def _collect_symbol_paths(tree: SymbolTree) -> list[str]:
    """Collect all symbol paths from a tree for suggestion purposes."""
    paths: list[str] = []
    def _walk(nodes: list[SymbolNode], prefix: str) -> None:
        for node in nodes:
            path = f"{prefix}/{node.name}" if prefix else node.name
            paths.append(path)
            _walk(node.children, path)
    _walk(tree.roots, "")
    return paths
```

このヘルパーは `mcp_server.py` のモジュールレベルに置く（`_read_asset` 等の既存ヘルパーと同じパターン）。

**性能**: エラーパスでのみ実行されるため、大規模シーン（数千ノード）でもユーザー体験への影響はない。`SequenceMatcher` は候補あたり O(n*m) だが、候補数 × 文字列長が小さい（シンボル名は短い）ため問題にならない。

### B: Material プロパティ名

既存の `_list_all_property_names(text)` をそのまま使用。

### C: シェーダープロパティ名

`shader.GetPropertyCount()` + `shader.GetPropertyName(i)` でループ。`HandleGetMaterialProperty` / `HandleSetMaterialProperty` 内の既存の `FindPropertyIndex` 呼び出し直後で、not-found 分岐に入った場合のみ実行する。

## 影響範囲

| ファイル | 変更内容 |
|---|---|
| `prefab_sentinel/fuzzy_match.py` | 新規: `suggest_similar()` |
| `prefab_sentinel/mcp_server.py` | `SYMBOL_NOT_FOUND` の 3 箇所に suggestions 追加 + `_collect_symbol_paths` ヘルパー（モジュールレベル） |
| `prefab_sentinel/material_asset_inspector.py` | `MAT_PROP_NOT_FOUND` に suggestions 追加 |
| `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` | `EditorControlData.suggestions` フィールド追加、`BuildError` オーバーロード追加、`LevenshteinDistance` + `SuggestSimilar` 静的メソッド追加、`EDITOR_CTRL_PROPERTY_NOT_FOUND` に suggestions 追加 |
| `tests/test_fuzzy_match.py` | 新規: `suggest_similar()` ユニットテスト |
| `tests/test_mcp_server.py` | `SYMBOL_NOT_FOUND` レスポンスの suggestions 検証 |
| `tests/test_material_write.py` | `MAT_PROP_NOT_FOUND` レスポンスの suggestions 検証 |
| `README.md` | Phase 5.3 の記述追加（エラーヒント機能） |

## やらないこと

- `message` テキストに "Did you mean X?" を埋め込む — 構造化データ (`data.suggestions`) で十分
- 部分文字列一致 — YAGNI。Levenshtein で十分
- `difflib` 以外の外部依存
- orchestrator / services 層への変更 — MCP ツール層と material inspector のみ
- SymbolTree に `all_paths()` メソッドを追加する — ヘルパー関数で十分（traversal は 6 行）

## テスト

### Python (自動)

| テスト | 内容 |
|---|---|
| `suggest_similar` 単体 | typo → 正解候補、完全不一致 → 空、大小文字混在、空リスト入力 |
| MCP `SYMBOL_NOT_FOUND` | レスポンス `data` に `suggestions` キーが存在し、型が `list` |
| Material `MAT_PROP_NOT_FOUND` | レスポンス `data` に `suggestions` キーが存在 |

### C# (手動, Editor Bridge 必要)

| テスト | 手順 |
|---|---|
| typo → 候補あり | `_Colr` → `suggestions` に `_Color` 含有 |
| 完全不一致 → 空 | `_ZZZZZZZZ` → `suggestions` が空配列 |

## 検証基準

1. 全ユニットテスト pass（既存 + 新規）
2. `suggest_similar` の typo テスト: 1-2 文字違いで正解候補がトップに来る
3. `SYMBOL_NOT_FOUND` / `MAT_PROP_NOT_FOUND` レスポンスに `suggestions` フィールド存在
4. C# コンパイルチェック pass
