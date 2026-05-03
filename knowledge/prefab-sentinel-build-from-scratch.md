# prefab-sentinel: 大規模 UI Prefab を MCP/Editor 経由でゼロから構築する

UI prefab を Builder script + MCP ツールでゼロから組む案件で踏んだ pitfalls と、
回避できる現実的なパターンの記録 (NadeVision PlaybackUi の chair UI 実装、2026-05-02〜03)。

## 全体方針: editor_run_script ではなく永続 [MenuItem] スクリプト

**editor_run_script は不安定。** 30+ コンポーネント / 70+ GameObject の hierarchy を一発で
組もうとすると、書き込み・compile・実行の往復で bridge state が `EDITOR_CTRL_RUN_SCRIPT_COMPILE`
(compile pending) に固着する。Unity 再起動でしか回復しない。

**正しい型:**

1. `Assets/Editor/_FooBuilder.cs` に `[MenuItem("Tools/.../Build Foo")]` で永続 script を Write
2. `editor_recompile` → DLL mtime polling で完了待ち (後述)
3. `editor_execute_menu_item` でメニュー実行
4. ロジック修正は同じ `.cs` を Edit して 2-3 を再実行

メニュー実行は冪等。bridge state にも依存しない。何度でも回せる。

## コンパイル待ち: DLL mtime polling

`editor_recompile` は AssetDatabase.Refresh をスケジュールするだけで即返る。
完了は `Library/ScriptAssemblies/Assembly-CSharp[-Editor].dll` の mtime が
ソース mtime より新しくなったタイミングで判定する。

```bash
EDIT=Assets/Editor/_MyBuilder.cs
DLL=Library/ScriptAssemblies/Assembly-CSharp-Editor.dll
E=$(stat -c %Y "$EDIT")
until [ "$(stat -c %Y "$DLL" 2>/dev/null || echo 0)" -gt "$E" ]; do sleep 0.3; done
```

- **重要**: 比較対象は `.cs` の mtime。`PREV=$(stat dll)` を loop 前に取って差分を待つと、
  loop 開始前にコンパイルが終わっていると永遠に exit しない。
- polling 0.3s。`sleep 2` は体感遅い。
- `run_in_background:true` で投げると並行作業可能。

ランタイム側 (`Scripts/*.cs`) のみの変更なら `Assembly-CSharp.dll` を見る。
Editor script なら `Assembly-CSharp-Editor.dll`。

## U# OnBeforeSerialize(unityObject==null) は非致命

`PrefabUtility.LoadPrefabContents` → `PrefabUtility.InstantiatePrefab(other_prefab, parent)`
→ `PrefabUtility.SaveAsPrefabAsset` の chain で、ネスト先 prefab に U# behaviour が含まれる場合、
保存中に以下の例外が頻発する:

```
ArgumentNullException: Value cannot be null. Parameter name: unityObject
  at OdinSerializer.UnitySerializationUtility.SerializeUnityObject(...)
  at UdonSharpBehaviour.OnBeforeSerialize()
  at PrefabUtility.SaveAsPrefabAsset(...)
```

**これは非致命**。Unity がログだけ吐いて save は通る。生成された prefab は正しい
PrefabInstance + 全 override を含む。`grep` で property override や fileID 参照を確認すれば
保存自体は完了している (verified: 81 PrefabInstance refs / 7 button bindings 全て persisted)。

**判定のコツ:**

1. menu 実行直後に **`editor_console` に Exception 5件 + `[MyMenu] Saved: ...` Log 1件**
   というパターンなら save 成功 (ログ Log_type "Log" が末尾にある)
2. prefab YAML を `grep -c "PrefabInstance"` `grep "propertyPath: ..."` で
   実際に override が入ったか直接検証する
3. console Exception だけ見て "失敗した" と判断しない

(注: U# 1.x の既知の振る舞い。`UdonSharpBehaviour.OnBeforeSerialize` は prefab save 中に
他の U# behaviour 経由で間接呼出される際、参照が一時的に null と判定されることがある。
SDK 内部実装に踏み込まない限り抑えられない)

## editor_run_script で U# component を AddComponent する罠

**避けること:** Builder script で `gameObject.AddComponent<MyUdonSharpBehaviour>()` →
fields を埋めて → `PrefabUtility.SaveAsPrefabAsset` の流れ。

これは確実に上記の OnBeforeSerialize null exception を踏む。Builder 段階で
新規 U# component を proxy として追加すると backing UdonBehaviour の作成タイミングが
保存と競合する。

**回避策:**

A. `gameObject.AddUdonSharpComponent<T>()` (UdonSharpComponentExtensions の拡張メソッド)
   を使う。`UdonSharp.UdonSharpEditorUtility` ではなく拡張メソッド経由。
   ただし結局 nested prefab save で別の null 例外が発生することがある。

B. **Build と Wire を分離** + Wire 段階で AddUdonSharpComponent。Build は visuals のみ
   保存し、Wire で NadeVision.prefab に PlaybackUi instance を nest した後、
   instance に追加する。

C. **専用 U# behaviour を新規追加せず、既存 U# behaviour を拡張する** (本セッションで採用)。
   既存 Controller に public field を生やし `Update()` で UI 駆動するだけなら、
   nested prefab save 時に新規 publicVariables schema が混じらず安定する。

## editor_set_camera の挙動メモ

- **pivot mode (pivot+yaw+pitch+distance)** と **position mode (position+look_at)** は
  混在不可。同時指定すると pivot mode が優先され position は無視される。
- `position` だけ指定しても camera 位置は動かないことがある (rotation/distance は更新されるが
  position は preserved のまま)。**pivot 経由で更新する方が確実。**
- `orthographic: -1` は keep。`0`=perspective `1`=orthographic を明示しないと
  以前のセッションの ortho 状態を引き継ぐ。
- yaw 規約は不安定。`yaw=180` で +Z 方向に向くこともあれば `yaw=0` で +Z のこともあり、
  scene file 内の SceneView の最後の rotation 状態に依存する。試行錯誤で決める。

## editor_set_property: Quaternion 非対応

`m_LocalRotation` を `0,1,0,0` のような Quaternion 値で設定しようとすると
`EDITOR_CTRL_SET_PROP_TYPE_MISMATCH: Unsupported property type: Quaternion` で失敗する。

回転を変えたいときは:

- `m_LocalEulerAnglesHint` を Vector3 で設定 (これは serialized hint なので Inspector 表示用、
  実際の rotation には影響しない場合あり)
- `editor_run_script` 経由で `transform.localRotation = Quaternion.Euler(...)` を直接実行
- prefab YAML を直接編集 (variant-safe-edit から外れるので最終手段)

## editor_console の挙動メモ

- 引数なしで呼ぶと **古い順** に entry を返す (default 200件)。直近のエラーを取りたいなら
  `since_seconds: 30` 等を必須で指定。
- `log_type_filter: "error"` で例外/エラーのみ抽出 (warning も除外される)。
- max_entries は古い順カットなので `since_seconds + max_entries` 併用がベスト。
- 1 ターンで何度も呼ぶと出力が膨大になり tool result truncation を誘発する。
  `since_seconds` は必須。

## inspect_hierarchy の限界、get_unity_symbols が確実

巨大な multi-root prefab (e.g. 285+ GameObject) で `inspect_hierarchy` を呼ぶと、
最初の root だけ拾って "is_variant: true" と誤判定することがある。
**get_unity_symbols** は YAML を直接 parse するので確実。fileID と script_guid が
取れて wiring 確認に使える。

```python
# 良い: 全構造を確実に
get_unity_symbols(asset_path="...")

# 不確実: Variant 扱いになる場合あり
inspect_hierarchy(asset_path="...")
```

## editor_frame の罠

`editor_frame` (F key 相当) は selection の bounds を計算して camera をそこへ寄せるが、
**RectTransform を新しい localPosition に動かしても、Frame は古い pivot に対して計算する**
ことがある (UI element の bounds キャッシュ)。

回避策: 一度 select 解除して再 select、または `editor_set_camera` で直接 pivot 指定。

## 推奨ワークフロー: Generate → Build → Wire の3段階分離

UI prefab 構築では menu を3段階に分割すると変更影響範囲が局所化されて回しやすい:

1. **Generate** — テクスチャ/sprite/material 等のディスク資源を生成。
   コードでパラメータ調整しても prefab は触らない。
2. **Build** — visual 階層 (Image / TMP / Slider / Toggle / Button) を組んで
   `Foo.prefab` に保存。**U# component 追加はしない。**
3. **Wire** — `NadeVision.prefab` を `LoadPrefabContents` で開いて、
   `Foo.prefab` を `InstantiatePrefab` で nest し、以下の 3 ツールで配線する
   （issue #119 で MCP に追加済み。生 C# を `editor_run_script` に書く必要はない）:

   - `editor_add_udonsharp_component` — U# behaviour を upsert で配置（追加 or 既存再利用）。
     `UdonSharpUndo.AddComponent`（内部で `Undo.AddComponent` + `RunBehaviourSetupWithUndo`）
     → 初期 `fields_json` 適用 → `CopyProxyToUdon` を 1 トランザクションで実行。
     `was_existing` / `applied_fields` / `udon_program_asset_path` が応答に乗るので
     再実行による進捗管理が容易。
   - `editor_set_udonsharp_field` — 既存 U# component の単一フィールドを `SerializedObject` 経由で
     書き込み、同じトランザクションで `CopyProxyToUdon` を回す。`VRCUrl` フィールドは
     内側の `url` 文字列を直接書き込む（呼び側で wrapper を構築する必要はない）。
   - `editor_wire_persistent_listener` — `Button.onClick` / `Slider.onValueChanged` 等から
     `UdonBehaviour.SendCustomEvent("On…")` への persistent listener を string モードで追加。
     `UnityEventTools.AddStringPersistentListener` を裏で叩く。target / method / mode / arg が
     一致する既存 listener があるとノーオプ（idempotent）。

各段階は独立 menu にする。途中で挙動疑問があったら該当段階だけ再実行できる。

## Prefab YAML を直接 grep してデバッグ

MCP の tool result が信用できないとき (途中で truncate / cache stale)、最終手段は
`grep` で prefab YAML を直接見る。

```bash
# Wire 結果検証
grep -c "edc2fe034044d2c4cba4ffd27e4db59d" NadeVision.prefab  # PrefabInstance count
grep -E "value: (_On|On)[A-Z]" NadeVision.prefab | sort | uniq -c  # listener event names
grep -B1 -A2 "propertyPath: controller" NadeVision.prefab  # field override 確認
```

特に **wire が成功したのに console に Exception** が出るときは grep 検証が決定的。

## 文字を画像化せずフォントのまま使うべき場面

UI に少量のラベル (8〜30字) を載せる場合、TMP_FontAsset を作って TMP_TextUGUI で
描画する方が prefab 容量・runtime コスト・再利用性すべてで有利。

ただし **TMP_FontAsset の生成は TTF/OTF が必要**。プロジェクトに源 fonts (Inter / JetBrains Mono 等) が
無いと作れない。fallback として LiberationSans SDF (TMP 標準) で代替し、ユーザに後で
差し替えてもらう運用が現実的。

procedural な sprite 生成 (角丸矩形 / 円 / アイコン) は Texture2D + 9-slice import で
完全自動化できる。`spritePixelsPerUnit` を 4x にして resolution を 4x 上げると
edge が滑らかになる (visual size は同じ)。

## 関連

- [prefab-sentinel-workflow-patterns.md](prefab-sentinel-workflow-patterns.md) — 既存の workflow パターン
- [udonsharp.md](udonsharp.md) — UdonSharp 全般 (proxy / backing UB / publicVariables)
- [vrchat-sdk-worlds.md](vrchat-sdk-worlds.md) — World SDK の component 一般
