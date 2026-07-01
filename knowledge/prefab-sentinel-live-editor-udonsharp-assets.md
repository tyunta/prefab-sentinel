---
tool: prefab-sentinel-live-editor-udonsharp-assets
version_tested: "prefab-sentinel 0.7.1"
last_updated: 2026-05-31
confidence: medium
---

# prefab-sentinel live Editor UdonSharp asset workflow

Unity Editor を開いた状態で、UdonSharp field と新規 Unity asset を同じ作業単位で扱うための運用知識。

## 基本情報

対象は PrefabSentinel Editor Bridge の live Editor 系 API と、UdonSharpBehaviour の serialized field 配線を組み合わせる authoring workflow。

主に使う API:

- `editor_refresh` / `editor_recompile`: C# / UdonSharp compile の完了確認
- `editor_set_udonsharp_field`: UdonSharpBehaviour の serialized field 書き込み
- `editor_run_script`: AssetDatabase 操作や専用 high-level API がない Editor 操作
- `validate_structure`: 保存後の scene / prefab YAML 構造検査
- `list_serialized_fields`: C# source 上の Unity serialized field 一覧
- `inspect_wiring`: saved asset 上の MonoBehaviour / UdonSharp wiring 検査

live Editor 系 API は `hierarchy_path` と現在開いている Editor scene / Prefab Stage を権威にする。offline 検査 API は saved disk YAML を権威にする。field 書き込み後は、必要に応じて scene / asset 保存を挟んでから offline 検査へ移る。

## 主要 API・概念

### UdonSharp field 書き込み

UdonSharpBehaviour の serialized field は `editor_set_udonsharp_field` を優先する。この API は UdonSharp proxy と backing `UdonBehaviour` の同期を前提にした writer であり、通常の `editor_set_property` より authoring chain に合う。

ObjectReference field には hierarchy path または asset path を渡す。RenderTexture / Material / Sprite などの project asset は `Assets/...` path で指定する。

### Script schema と saved wiring の分離

新規 serialized field を C# に追加した直後は、C# source schema と saved Unity asset の publicVariables が一時的にずれる。

- `list_serialized_fields` は C# source から field を列挙する。
- `inspect_wiring` は saved Unity asset の field wiring を読む。

したがって、field 追加直後に `inspect_wiring` だけで「field が存在しない」と判断しない。compile / UdonSharp serialization / scene save の境界を通した後に再検査する。

### AssetDatabase 操作

PrefabSentinel に専用 high-level API がない Unity asset 操作は、`editor_run_script` で `AssetDatabase` を使う。

代表例:

- RenderTexture asset の作成
- RenderTexture の width / height / format / filterMode / wrapMode 設定
- 作成済み asset の `AssetDatabase.MoveAsset` rename
- `AssetDatabase.SaveAssets()` と `AssetDatabase.Refresh()`
- `EditorSceneManager.SaveOpenScenes()`

`editor_run_script` は write-class tool なので、`confirm: true` と非空 `change_reason` を必ず付ける。

## 使い分け

### 新規 UdonSharp field を追加して配線する

推奨順:

1. C# source に serialized field を追加する。
2. `editor_refresh` または `editor_recompile` で compile を完了させる。
3. `list_serialized_fields(script_or_guid=...)` で field が source schema に存在することを確認する。
4. asset が未作成なら `editor_run_script` で AssetDatabase 作成を行う。
5. `editor_set_udonsharp_field(hierarchy_path, property_name, object_reference)` で live object に配線する。
6. `EditorSceneManager.SaveOpenScenes()` / `AssetDatabase.SaveAssets()` で保存する。
7. `validate_structure` で scene / prefab 構造を確認する。

`inspect_wiring` は saved wiring の検査として使う。ただし UdonSharp publicVariables の解析結果が source schema と食い違う場合は、`list_serialized_fields` と live Editor での確認を併用する。

### 作成直後 asset を rename する

作成直後の asset は PrefabSentinel の cached index にまだ見えていない場合がある。`rename_asset` が `ASSET_RENAME_NOT_FOUND` を返す場合は、`editor_run_script` で `AssetDatabase.MoveAsset(oldPath, newPath)` を使う。

`AssetDatabase.MoveAsset` は Unity 側の asset rename なので、`.meta` の GUID を保ったまま path を変更できる。rename 後は `AssetDatabase.SaveAssets()` と `AssetDatabase.Refresh()` を実行する。

### 参照確認

UdonSharp field に入った ObjectReference は、単純な asset reference search では拾えないことがある。`find_referencing_assets` が 0 件でも、UdonSharp publicVariables 内の参照が存在しないとは限らない。

参照確認が必要な場合は、次の順で確認する。

1. `editor_set_udonsharp_field` の success を確認する。
2. scene / asset を保存する。
3. `list_serialized_fields` で field が source schema に存在することを確認する。
4. 必要なら live Editor helper で field 現在値を読む。
5. `validate_structure` で saved asset の構造破損がないことを確認する。

## 落とし穴

### `inspect_wiring` が source schema 変更をすぐ反映しない

条件: UdonSharpBehaviour に新規 serialized field を追加し、compile / field 配線 / scene 保存を短時間で連続実行する。

症状: `list_serialized_fields` では新規 field が見えるが、`inspect_wiring` の field_count や fields 一覧に出ない。

回避策: `inspect_wiring` を唯一の権威にしない。C# schema は `list_serialized_fields`、live 配線は `editor_set_udonsharp_field` の success と live verification、saved structure は `validate_structure` で分けて確認する。

### `find_referencing_assets` が UdonSharp publicVariables を見落とす

条件: Project asset を UdonSharpBehaviour の public ObjectReference field に入れる。

症状: field へ配線済みでも `find_referencing_assets(asset_or_guid=...)` が 0 件を返す。

回避策: 0 件を未配線の証拠にしない。UdonSharp publicVariables / serialized program asset の object reference は、専用の field 検証経路で確認する。

### `editor_run_script` の temp script が target runtime class を直接参照できない

条件: `editor_run_script` の temporary Editor script から、プロジェクトの runtime assembly にある UdonSharpBehaviour 型を直接書く。

症状: `CS0246` などで target class を解決できない。

回避策: temporary script では target runtime class の直接型参照を避ける。Unity API の generic でない参照、SerializedObject、reflection、または PrefabSentinel の専用 API を使う。

### `editor_run_script` runtime exception の情報量が少ない

条件: `PrefabSentinelTempScript.Run()` 内で例外が発生する。

症状: MCP response が `Run() threw a runtime exception` のみになり、exception message / stack が分からない場合がある。

回避策: 失敗しやすい検証は小さい step に分ける。可能なら high-level API を優先し、`editor_run_script` は AssetDatabase 操作などに限定する。

### Unity generated YAML は `git diff --check` と相性が悪い

条件: scene / prefab / `.meta` / UdonSharp asset を Unity が保存する。

症状: `value: `、`m_Name: `、`assetBundleName: ` などの行で trailing whitespace が大量に報告される。

回避策: Unity serialized asset に対しては、`git diff --check` を唯一の品質ゲートにしない。`validate_structure` と Unity compile / console 検査を優先する。Unity YAML を手作業で整形しない。

## 関連 knowledge

- [prefab-sentinel-workflow-patterns](./prefab-sentinel-workflow-patterns.md)
- [prefab-sentinel-wiring-triage](./prefab-sentinel-wiring-triage.md)
- [prefab-sentinel-ugui-event-wiring](./prefab-sentinel-ugui-event-wiring.md)
- [udonsharp](./udonsharp.md)
