# inspect_wiring Null 分類ヒント — 設計仕様

**日付**: 2026-03-25
**スコープ**: inspect_wiring ツールの null ref 診断に文脈情報を追加

---

## 背景

MCP 統合テスト (`report_mcp_integration_test_20260324.md`) で、SoulLinkSystem.prefab の inspect_wiring が 18 件の null ref WARNING を報告した。これらは全てシーン配置時に外部注入される設計で、Prefab 単体では null が正常。しかし現状の出力では意図的 null と事故 null の区別がつかず、AI エージェントが不要な修正を試みるリスクがある。

## 設計方針

- **ヒューリスティック推定**: YAML パターンから null ref の文脈情報を導出する。C# ソース解析は将来拡張として残す。
- **閾値なし**: null 比率 (`null_ratio`) を数値で返し、判断は AI に委ねる。固定閾値によるバイナリ分類はしない。
- **振る舞い変更なし**: severity (WARNING)、success 判定、既存の diagnostic フォーマットは維持。純粋な情報追加。

## 変更内容

### 1. `udon_wiring.py` — `analyze_wiring()` の出力拡張

各コンポーネントの ObjectReference フィールド総数をカウントし、null ref 診断の evidence に追加する。

**ObjectReference フィールドの判定基準**: 既に `analyze_wiring()` が `iter_references()` で抽出しているフィールド群。`{fileID: ...}` または `{fileID: ..., guid: ...}` パターンを持つフィールドが対象。

**null ref diagnostic の evidence 拡張**:

```python
# 現状
evidence = {
    "field_name": "fadeController",
    "game_object": "ShareController",
    "component_type": "RigContext",
}

# 変更後
evidence = {
    "field_name": "fadeController",
    "game_object": "ShareController",
    "component_type": "RigContext",
    "null_ratio": "9/12",           # 新規: null_count/total_object_ref_count
    "null_field_names": [           # 新規: 同コンポーネントの全 null フィールド名
        "fadeController",
        "localSeatFadeController",
        "rigValidator",
        "menuController",
        "ipdSlider",
        "anchorViewModeToggle",
        "rightStickInput",
        "anchorPickupFreeze",
        "resetFollowOnDrop",
    ],
}
```

`null_ratio` は `"{null_count}/{total_object_ref_count}"` の文字列。AI が「9/12 = 75% null → バッチ注入設計の可能性」と推論できる。

`null_field_names` は同一コンポーネント内の全 null フィールド名リスト。AI がパターンを認識する材料（例: 全て UI 系のフィールド名 → シーン配線が妥当）。

### 2. `udon_wiring.py` — `ComponentWiring` の拡張

`ComponentWiring` dataclass (または同等の結果構造) に `object_ref_count: int` フィールドを追加。

```python
# コンポーネントごとの集計に追加
object_ref_count: int  # ObjectReference フィールドの総数
```

これにより `data.components[]` 経由で `null_reference_count` / `object_ref_count` の比率が取得可能になる。

### 3. orchestrator / MCP 層

変更不要。`analyze_wiring()` の戻り値と `ToolResponse` の既存構造をそのまま使う。`ComponentWiring` のフィールド追加と diagnostic の evidence 拡張は自動的に `to_dict()` / JSON シリアライズされる。

## 影響範囲

| ファイル | 変更内容 |
|----------|----------|
| `prefab_sentinel/udon_wiring.py` | `analyze_wiring()` の null ref 処理に null_ratio/null_field_names 追加、ComponentWiring に object_ref_count 追加 |
| `tests/test_udon_wiring.py` | null ref テストに evidence 検証追加、object_ref_count の検証追加 |

## やらないこと

- severity の変更（WARNING のまま）。
- C# ソース解析によるフィールド属性チェック（将来拡張）。
- allowlist / ignore 機構の追加。
- orchestrator / MCP 層の変更。
- 閾値ベースの自動分類（`likely_scene_injection: true/false` のようなバイナリ判定）。

## 検証基準

1. 全ユニットテスト pass
2. null ref diagnostic の evidence に `null_ratio` と `null_field_names` が含まれること
3. `ComponentWiring` に `object_ref_count` が含まれること
4. 既存の severity / success 判定が変わらないこと
