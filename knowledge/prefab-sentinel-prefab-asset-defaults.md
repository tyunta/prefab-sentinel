---
tool: prefab-sentinel-prefab-asset-defaults
version_tested: "prefab-sentinel 0.7.1"
last_updated: 2026-06-06
confidence: medium
---

# prefab-sentinel Prefab asset default workflow

Prefab asset 上の serialized default 値を変更・検証するための運用知識。対象は既存 Prefab に載っている UdonSharpBehaviour の primitive field、Unity UI component の初期値、host Prefab 内の nested Prefab override。

## 基本情報

PrefabSentinel には disk YAML を読む offline tool と、Unity Editor の live object / Prefab Stage を読む live Editor tool がある。

代表的な tool:

- `validate_refs(scope="Assets/.../Foo.prefab")`
- `validate_structure(asset_path="Assets/.../Foo.prefab")`
- `get_unity_symbols(asset_path=..., expand_nested=true)`
- `find_unity_symbol(asset_path=..., include_fields=true)`
- `editor_list_children(hierarchy_path=...)`
- `editor_set_property(hierarchy_path=..., component_type=..., property_name=..., value=...)`
- `editor_run_script(confirm=true, change_reason=...)`
- `editor_console(log_type_filter="error", classification_filter="fatal")`

offline tool は last-saved disk YAML を権威にする。live Editor tool は現在開いている Scene / Prefab Stage を権威にする。Bridge 接続中に unsaved live changes がある場合、offline symbol tool の `freshness` note は saved disk と live state の乖離を示す。

## 主要 API・概念

### target file validation

変更対象が明確なときは、まず file scope で検証する。

```text
validate_refs(scope="Assets/Feature/Prefab/Foo.prefab", details=true)
validate_structure(asset_path="Assets/Feature/Prefab/Foo.prefab")
```

feature scope 全体の検証は release gate として有用だが、package prefab や既存負債の diagnostics を含みやすい。小さい default 値変更では、target file validation と whole-scope validation を分けて扱う。

### UdonSharp primitive field

既存 UdonSharpBehaviour の primitive serialized field は、live Editor 上では `editor_set_property` で書ける。

```text
editor_set_property(
  hierarchy_path="/Root/Controller",
  component_type="MyUdonSharpBehaviour",
  property_name="someFloat",
  value="0.3"
)
```

UdonSharp field の ObjectReference 配線では `editor_set_udonsharp_field` を優先する。既存 Prefab Stage の primitive field で `editor_set_udonsharp_field` が path resolution に失敗する場合は、`editor_set_property` を fallback として使い、保存後に別経路で値を assert する。

### nested Prefab override

host Prefab 内に nested Prefab instance があり、host 側だけに追加された子や override がある場合、base nested Prefab asset には対象 GameObject が存在しない。

判断手順:

1. `get_unity_symbols(asset_path=hostPrefab, expand_nested=true)` で nested 構成を確認する。
2. `editor_list_children` で live Scene / Prefab Stage 上の実階層を確認する。
3. 対象が base nested Prefab asset に存在するか、host Prefab override として存在するかを分ける。
4. host override なら host Prefab asset を編集対象にする。

base nested Prefab asset だけを検査して対象が見つからない場合でも、host Prefab の nested override には存在することがある。

### asset-only write fallback

high-level writer が host Prefab 内 nested override を address できない場合は、`editor_run_script` で `PrefabUtility.LoadPrefabContents` を使う。

最小条件:

- `confirm=true` と非空 `change_reason` を付ける。
- `PrefabUtility.LoadPrefabContents(prefabPath)` で asset contents を開く。
- `Transform.Find(...)` / `GetComponent<T>()` の null は例外にする。
- `SerializedObject.FindProperty(...)` で field を書く。
- `PrefabUtility.SaveAsPrefabAsset(root, prefabPath)` と `AssetDatabase.SaveAssets()` を呼ぶ。
- `finally` で `PrefabUtility.UnloadPrefabContents(root)` を必ず呼ぶ。
- 保存後に `validate_refs` / `validate_structure` と値 assert を行う。

asset-only write は Scene instance を Prefab 保存する経路とは違い、scene-only instance 数や scene 上の一時変更を Prefab asset に取り込みにくい。

### value assert

default 値変更後は、同じ Prefab asset を `PrefabUtility.LoadPrefabContents` で開き、Unity API 上の値を assert する。

検証対象の例:

- UdonSharpBehaviour の public primitive field
- `UnityEngine.UI.Slider.value`
- `TMPro.TextMeshProUGUI.text`
- `AudioSource.volume` / `panStereo` / `spatialBlend`

`validate_refs` と `validate_structure` は asset 破損を検出する。値そのものが期待値かどうかは、別途 property read / assert が必要になる。

## 使い分け

### high-level writer を優先する

単純な live object の field 書き込みなら、まず `editor_set_property` / `editor_set_udonsharp_field` を使う。これらは SerializedObject 経由で型を解決し、Undo / Editor state と相性が良い。

### offline symbol tool は構造把握に使う

`find_unity_symbol(include_fields=true)` / `get_unity_symbols(detail="fields")` は構造把握に使う。UdonSharp scalar field が表示されない場合があるため、field が出ないことを「field が存在しない」証拠にしない。

### nested override は host Prefab を編集する

対象 GameObject が host Prefab 内の nested override として存在するなら、base nested Prefab asset ではなく host Prefab asset を編集する。base asset を変更しても host-only override には届かない。

### file-scope gate と whole-scope gate を分ける

変更直後の regression 判定には target file scope を使う。プロジェクト全体の既存 missing GUID / optional null を含めた総合検査は、別 gate として扱う。

## 落とし穴

### live / offline の権威を混ぜる

条件: Editor Bridge が接続中で、live editor に未保存変更がある。

症状: offline symbol tool が `freshness` note を返し、last-saved disk YAML が live editor と乖離している可能性を示す。

回避策: live tool と offline tool の結果を混同しない。live 変更を保存してから offline 検査するか、live tool だけで確認する。`freshness` note が出た場合は、saved disk を権威にした判断を保留する。

### `inspect_wiring(script_filter=...)` の diagnostics を target-only と誤解する

条件: 大きい Prefab で `inspect_wiring` に `script_filter` を指定する。

症状: `components` は filter 対象に絞られても、diagnostics に Prefab 全体の null reference / duplicate reference warning が含まれることがある。

回避策: `components` slice と diagnostics を分けて読む。target script の null field を見る場合は、対象 component の `null_reference_count` / `null_field_names` を確認する。

### UdonSharp scalar field が symbol output に出ない

条件: UdonSharpBehaviour の `float` / `bool` / enum などの primitive public field を確認する。

症状: `find_unity_symbol(include_fields=true)` で ObjectReference array は見えるが、scalar field が properties に出ない。

回避策: 値確認には live SerializedObject read、`editor_run_script` assert、または専用 property read API を使う。symbol output に出ないことを未設定の証拠にしない。

### Prefab Stage の writer path resolution 差

条件: Prefab Stage 上の既存 UdonSharpBehaviour field を書く。

症状: `editor_list_children` では存在する hierarchy path が、特定 writer では GameObject not found になることがある。

回避策: `editor_set_udonsharp_field` と `editor_set_property` のどちらが対象 context で解決できるか確認する。片方が失敗した場合も、もう片方の SerializedObject writer が使える場合がある。最終的な値は保存後に assert する。

### Scene instance を Prefab asset 更新に使う

条件: scene 上の Prefab instance を `editor_safe_save_prefab` / `SaveAsPrefabAsset` で asset へ保存する。

症状: scene-only の子数、位置、未確定 override、一時変更を Prefab asset に取り込む可能性がある。

回避策: default 値だけを変える場合は Scene instance 保存ではなく、asset-only の `LoadPrefabContents` 経路または Prefab Stage 上の保存を使う。

### async `editor_run_script_submit` timeout

条件: temp script を async submit し、poll で完了を待つ。

症状: submit は accepted されるが、poll deadline elapsed で cleanup される。同期 `editor_run_script` では同じ処理が成功する場合がある。

回避策: 短い asset-only writer / assert では同期 `editor_run_script` を使う。async が必要な長い処理は小さく分割し、Console の compile error / fatal error を確認する。

## 関連 knowledge

- [prefab-sentinel-live-editor-udonsharp-assets](./prefab-sentinel-live-editor-udonsharp-assets.md)
- [prefab-sentinel-saveasprefabasset-pitfalls](./prefab-sentinel-saveasprefabasset-pitfalls.md)
- [prefab-sentinel-workflow-patterns](./prefab-sentinel-workflow-patterns.md)
- [prefab-sentinel-wiring-triage](./prefab-sentinel-wiring-triage.md)
