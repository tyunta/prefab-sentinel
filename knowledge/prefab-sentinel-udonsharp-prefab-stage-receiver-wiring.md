---
tool: prefab-sentinel-udonsharp-prefab-stage-receiver-wiring
version_tested: "prefab-sentinel 0.7.1 / Editor Bridge 0.7.0"
last_updated: 2026-06-07
confidence: high
---

# PrefabSentinel UdonSharp Prefab Stage Receiver Wiring

## 基本情報

UdonSharpBehaviour の public field に別の UdonSharpBehaviour を配線する場合、保存済み Prefab YAML と Unity Editor 上の Prefab Stage live state で扱いやすい API が異なる。

特に `UdonSharpBehaviour[] receivers` のような配列 field は、保存済み YAML に field 自体がまだ出ていない場合、offline patch 系 API では `Array.size` が見つからず失敗することがある。この場合は Prefab Stage を開き、live Editor API で serialized property を作ってから保存する。

## 主要 API・概念

### C# schema と Prefab wiring は別に確認する

- `list_serialized_fields`
  - UdonSharp C# 側に Inspector field として存在する項目を確認する。
  - 削除した field が Inspector schema から消えたかを見る用途に向く。
- `inspect_wiring`
  - Prefab に保存されている参照・null・壊れた参照を確認する。
  - `script_filter` で対象 component を絞れる。
- `find_unity_symbol(include_fields=true)`
  - 保存後に field の値が Prefab に残っているか確認する。

### Receiver 配列の基本形

Controller 側に `UdonSharpBehaviour[] receivers` のような field があり、共有状態やイベントを receiver に `SendCustomEvent` で通知する形は、以下の順序で確認・配線する。

1. Receiver component の serialized field が期待通りか `list_serialized_fields` で確認する。
2. Controller component の receiver 配列が Prefab 上に存在するか `find_unity_symbol(include_fields=true)` で確認する。
3. 未生成または空配列なら Prefab Stage live edit に切り替える。
4. `Array.size` を先に設定する。
5. `Array.data[n]` に receiver component reference を設定する。
6. Prefab を保存し、`find_unity_symbol` と `inspect_wiring` で再確認する。

### Prefab Stage path と offline symbol path の違い

Offline inspection の symbol path は Prefab root を含むことがある。

```text
RootObject/SubSystem/Controller/MonoBehaviour(ControllerBehaviour)
```

Prefab Stage の live Editor write API では、root を省いた hierarchy path が必要になることがある。

```text
/SubSystem/Controller
```

`editor_list_children` が root-inclusive path を返しても、`editor_select` / `editor_set_properties` が同じ path を受け付けるとは限らない。write 前に `editor_select` で resolver が通る path を確認する。

### Component suffix 付き object reference

同じ GameObject に複数 component がある場合、object reference は GameObject path だけでなく component type を suffix で指定する。

```text
/SubSystem/Receiver:ReceiverBehaviour
```

UdonSharpBehaviour 配列では、GameObject ではなく対象 UdonSharpBehaviour component を参照させる必要がある。

### 配列 field の設定順

配列 property は先に size を作り、そのあと各 element を設定する。

```text
receivers.Array.size
receivers.Array.data[0]
```

空配列を 1 要素にする場合の live edit は、概念的には以下の形になる。

```json
{
  "hierarchy_path": "/SubSystem/Controller",
  "component_type": "ControllerBehaviour",
  "properties": [
    { "property_name": "receivers.Array.size", "value": "1" },
    {
      "property_name": "receivers.Array.data[0]",
      "object_reference": "/SubSystem/Receiver:ReceiverBehaviour"
    }
  ]
}
```

## 使い分け

### offline API が向くケース

- 保存済み YAML に対象 field が既に存在する。
- 単純な object reference / primitive field の差し替え。
- dry-run diff を明確に見てから confirm したい。

### Prefab Stage live API が向くケース

- UdonSharp array field が保存済み YAML にまだ出ていない。
- `Array.size` の作成が必要。
- Unity の serialized property resolver に任せたほうが安全な field。
- Editor 上で生成される UdonSharp backing state と同期させたい。

### `editor_set_udonsharp_field` と `editor_set_properties`

- 単一 field の object reference / scalar 値なら `editor_set_udonsharp_field` を優先する。
- `Array.size` と `Array.data[n]` を同時に扱う場合は `editor_set_properties` が扱いやすい。

## 落とし穴

### `find_referencing_assets` は UdonSharp publicVariables を完全に表すとは限らない

UdonSharp の参照は generated asset / publicVariables / backing UdonBehaviour の層をまたぐため、単純な asset reference 逆引きだけでは取りこぼすことがある。

Prefab 上の UdonSharp field 配線は、`inspect_wiring` と `find_unity_symbol(include_fields=true)` で確認する。

### 削除済み GUID は通常の GUID 逆引きで探せないことがある

`.meta` が消えた GUID は project GUID index に存在しないため、通常の `find_referencing_assets` では探索対象にできないことがある。

削除後の残存 broken reference 確認は、対象 Prefab を絞って `validate_refs` を実行する。

### 広域 `validate_refs` は timeout / noise に注意する

大きい Unity project で広域に `validate_refs` を実行すると、timeout や既知ノイズで作業対象の問題が埋もれる。

変更した Prefab、影響を受ける Prefab、壊してよい旧 Prefab を分け、scope を絞って検証する。

### `script_filter` は diagnostics まで完全に絞らないことがある

`inspect_wiring(script_filter=...)` で component list が絞れても、diagnostics には対象外 component の null や重複参照が混ざることがある。

対象 component の wiring 結果と Prefab 全体 diagnostics を分けて読む。

### live edit 後は Prefab を明示保存する

Prefab Stage で `editor_set_properties` を使ったあと、変更を asset に残すには Prefab 保存 API を呼ぶ。

Scene 保存の案内が出ても、Prefab Stage の編集では Prefab asset の保存状態を確認する。

### UdonSharp serialize warning は非 fatal の場合がある

Prefab 保存時に UdonSharp の serialize callback warning が出ても、保存自体が成功し、後続の `find_unity_symbol` / `inspect_wiring` / `validate_refs` が通る場合がある。

warning の有無だけで失敗扱いせず、保存後の serialized field と broken reference を確認する。

## 検証パターン

1. `list_serialized_fields` で receiver 側 component の public serialized field を確認する。
2. `editor_open_prefab` で対象 Prefab を Prefab Stage に開く。
3. `editor_select` で rootless hierarchy path が write API に通ることを確認する。
4. `editor_set_properties` で `Array.size` と `Array.data[n]` を設定する。
5. `editor_safe_save_prefab` で Prefab asset を保存する。
6. `editor_close_prefab(save=false)` で明示保存済みの Prefab Stage を閉じる。
7. `find_unity_symbol(include_fields=true)` で receiver 配列の保存値を確認する。
8. `inspect_wiring(script_filter=...)` で null / broken reference がないことを確認する。
9. `validate_refs` を対象 Prefab に絞って実行する。

## 関連 knowledge

- [prefab-sentinel-wiring-triage](./prefab-sentinel-wiring-triage.md)
- [prefab-sentinel-workflow-patterns](./prefab-sentinel-workflow-patterns.md)
- [prefab-sentinel-live-editor-udonsharp-assets](./prefab-sentinel-live-editor-udonsharp-assets.md)
- [prefab-sentinel-ugui-event-wiring](./prefab-sentinel-ugui-event-wiring.md)
- [udonsharp](./udonsharp.md)
