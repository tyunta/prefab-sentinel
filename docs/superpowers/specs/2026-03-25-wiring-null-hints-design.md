# inspect_wiring Null 分類ヒント — 設計仕様

**日付**: 2026-03-25
**スコープ**: inspect_wiring ツールの null ref 出力に文脈情報を追加

---

## 背景

MCP 統合テスト (`report_mcp_integration_test_20260324.md`) で、SoulLinkSystem.prefab の inspect_wiring が 18 件の null ref WARNING を報告した。これらは全てシーン配置時に外部注入される設計で、Prefab 単体では null が正常。しかし現状の出力では意図的 null と事故 null の区別がつかず、AI エージェントが不要な修正を試みるリスクがある。

## 設計方針

- **ヒューリスティック推定**: YAML パターンから null ref の文脈情報を導出する。C# ソース解析は将来拡張として残す。
- **閾値なし**: null 比率 (`null_ratio`) を数値で返し、判断は AI に委ねる。固定閾値によるバイナリ分類はしない。
- **振る舞い変更なし**: severity (WARNING)、success 判定、`Diagnostic` の型定義は維持。情報追加はコンポーネント summary dict に集約する。

## 変更内容

### 1. `udon_wiring.py` — `analyze_wiring()` の集計拡張

各コンポーネントの null ref フィールド名リストを `ComponentWiring` 結果に追加する。

**ObjectReference フィールドの判定基準**: `analyze_wiring()` が `iter_references()` で抽出するフィールド群。`{fileID: ...}` または `{fileID: ..., guid: ...}` パターンを持つフィールドが対象。フィールド総数は既存の `len(comp.fields)` で取得可能（orchestrator が `field_count` として出力済み）。

**`ComponentWiring` に追加するフィールド**:

```python
null_field_names: list[str]  # null ref ({fileID: 0}) のフィールド名リスト
```

`null_field_names` は `analyze_wiring()` 内で各コンポーネントの null ref を集計した後に設定する。現状のフィールドイテレーションは1パスで null ref を検出しているため、null フィールド名の収集もこの1パスで行える。

**注意**: `Diagnostic.evidence` は `str` 型（contracts.py L34）であり、変更しない。null ref の個別 diagnostic は現状通り `evidence=f.value`（YAML 文字列）のまま。

### 2. `orchestrator.py` — コンポーネント summary dict の拡張

orchestrator の `inspect_wiring` ハンドラがコンポーネント summary dict を手動構築している箇所（L736-761 付近）に、2 フィールドを追加:

```python
cd = {
    # ... 既存フィールド ...
    "null_ratio": f"{comp.null_reference_count}/{len(comp.fields)}",  # 新規
    "null_field_names": comp.null_field_names,                         # 新規
}
```

`null_ratio` は `"{null_count}/{total_field_count}"` の文字列。AI が「9/12 = 75% null → バッチ注入設計の可能性」と推論できる。

`null_field_names` は同一コンポーネント内の全 null フィールド名リスト。AI がパターンを認識する材料（例: 全て UI 系のフィールド名 → シーン配線が妥当）。

### 3. MCP 層

変更不要。orchestrator が返す `ToolResponse.to_dict()` をそのまま透過する。

### エッジケース

- **ObjectReference フィールドが 0 個のコンポーネント**: `null_ratio` は `"0/0"`。null diagnostics は発生しないため実害なし。
- **Variant オーバーライドで null にされたフィールド**: `null_ratio` はベース + オーバーライド後の最終状態を反映する。「ベースでは non-null だったが Variant で null になった」という区別は `is_overridden` フラグで既に表現されている。将来拡張として区別可能だが、初期実装では不要。
- **ネスト構造内の null ref**: 既存の `analyze_wiring()` がネスト構造を除外する挙動（`test_nested_struct_children_excluded`）を維持。`null_field_names` にも含まれない。

## 影響範囲

| ファイル | 変更内容 |
|----------|----------|
| `prefab_sentinel/udon_wiring.py` | `ComponentWiring` に `null_field_names` 追加、`analyze_wiring()` で null フィールド名を収集 |
| `prefab_sentinel/orchestrator.py` | コンポーネント summary dict に `null_ratio`, `null_field_names` 追加 |
| `tests/test_udon_wiring.py` | `null_field_names` の検証追加 |
| `tests/test_mcp_server.py` | inspect_wiring レスポンスに新フィールドの検証追加（既存テストの mock 戻り値は変更不要） |

## やらないこと

- severity の変更（WARNING のまま）。
- `Diagnostic` 型 / `evidence` フィールドの型変更。
- C# ソース解析によるフィールド属性チェック（将来拡張）。
- allowlist / ignore 機構の追加。
- 閾値ベースの自動分類（`likely_scene_injection: true/false` のようなバイナリ判定）。
- MCP 層の変更。

## 検証基準

1. 全ユニットテスト pass
2. コンポーネント summary dict に `null_ratio` と `null_field_names` が含まれること
3. `null_ratio` が `"N/M"` 形式の文字列で、N = null ref 数、M = 全 ObjectReference フィールド数
4. 既存の severity / success 判定が変わらないこと
5. `Diagnostic.evidence` が `str` 型のまま変更されていないこと
