# prefab-sentinel: `PrefabUtility.SaveAsPrefabAsset` pitfalls

`PrefabUtility.SaveAsPrefabAsset` は単発で呼ぶと「黙ってコンポーネントを剥がす」「nested
override が orphan 化する」「TextMeshPro の必須参照を消す」など、保存自体は成功 (`success
== true`) なのに saved asset が壊れているケースがある。`editor_safe_save_prefab` MCP ツール
は `protect_components` で守るべき型名を必須引数として受け取り、保存後に再アタッチと差分
レポートを返すことでこの罠を吸収する。

このドキュメントは「`SaveAsPrefabAsset` を直接呼んではいけないケース」の最小カタログ。
代替手段は **常に `editor_safe_save_prefab`** とその `protect_components` リスト。

> **Cross-reference**: 大規模 UI prefab 構築のフロー全体は
> [prefab-sentinel-build-from-scratch.md](prefab-sentinel-build-from-scratch.md) を参照。

## Mode 1: `VRC_UiShape` strip mode

**症状**: WorldSpace Canvas の GameObject に `VRC_UiShape` を attach した状態で
`PrefabUtility.SaveAsPrefabAsset` を呼ぶと、saved prefab には `VRC_UiShape` がついていない。
Editor 側のシーン instance には残っているので一見気づかない。

**原因**: `VRC_UiShape` は VRC SDK Worlds の World-only コンポーネント。Avatar/World 兼用
プロジェクトの `[ExecuteAlways]` パスが Avatar 文脈だと Strip 対象判定になるケースがある
(VRChat SDK 内部の `IVRCSDKControlPanelBuilder` 経由のフィルタ)。

**回避**: `editor_safe_save_prefab` を `protect_components=["VRC_UiShape"]` で呼ぶ。Bridge
の safe-save handler は保存後の asset を再 inspect し、`VRC_UiShape` が剥がれていたら
`Undo.AddComponent` で再 attach してから再保存する。response の `data.reattached_components`
に `["VRC_UiShape"]` が含まれていれば strip が実際に発生したことを意味する。

## Mode 2: nested override strip mode

**症状**: ベース prefab を nest した GameObject をさらに `SaveAsPrefabAsset` する flow
(`LoadPrefabContents` → `InstantiatePrefab(other_prefab, parent)` → `SaveAsPrefabAsset`)
で、nested prefab に積んでいた modification override (RectTransform.m_AnchoredPosition,
MonoBehaviour 上の field 値など) のうち、saved asset の hierarchy 上に対応する target
が存在しない (削除した / 階層を変えた / nested ではなくフラット化された) override が
**orphan modification** として saved asset に残る。Unity の Inspector では override 行が
"Missing target" として表示される。

**原因**: `SaveAsPrefabAsset` は `m_Modifications` 配列を nest 元 PrefabInstance から丸ごと
コピーするが、target が解決できない override を捨てない。これは Unity の仕様で、保存時に
target 解決は走らない。

**回避**: `editor_safe_save_prefab` の response `data.orphan_modifications` を見る。各
エントリは `{ "target_object_path": "...", "property_path": "..." }` の形で、orphan に
なった override の発生箇所を特定できる。`revert_overrides` でクリーンアップするか、
`patch_apply` で nested 階層を再構築してから safe-save し直す。`protect_components` に
nest 元の component 型名を入れておくと、stripped されたケースは Mode 1 として再アタッチ
で吸収される。

## Mode 3: TextMeshPro font-asset requirement mode

**症状**: `TextMeshProUGUI` を含む prefab を `SaveAsPrefabAsset` で保存すると、saved asset
側の `m_fontAsset` が null PPtr (`{fileID: 0}`) になっている。シーン instance では正しく
`LiberationSans SDF` などが入っているが、prefab を再 instantiate すると "Default Font
Asset" のロード失敗で TMP テキストが表示されない。

**原因**: TMP の `TextMeshProUGUI.OnEnable` 実装が `TMP_Settings.defaultFontAsset` を初期化
時に解決するパスがあり、`SaveAsPrefabAsset` の serialize タイミングで `m_fontAsset` が一時
的に null として捕捉されることがある。`fontAsset` が null PPtr で保存されると、再
instantiate 時に Resources からのフォールバック読み込みも走らない (font-asset が prefab 自体の
serialized field として明示的に required 扱いされるため)。

**回避**: `editor_safe_save_prefab` を `protect_components=["TextMeshProUGUI"]` で呼んでも
コンポーネント自体は剥がれていないため再アタッチは発生しないが、保存後に
`editor_get_serialized_property` 等で `m_fontAsset` を読み、null PPtr なら fontAsset を
明示的に書き戻す手順を入れる。長期的には TMP 側で `fontAsset` を runtime 初期化する
component を別途用意し、prefab 内部では参照しない設計に倒すのが安定。

## まとめ

| Mode | 検知 | 自動修復 | 残作業 |
|------|------|----------|--------|
| `VRC_UiShape` strip | `data.reattached_components` に `VRC_UiShape` が乗る | safe-save handler が `Undo.AddComponent` で再アタッチ | なし (再保存も自動) |
| nested override strip | `data.orphan_modifications` に target+property が乗る | なし (列挙のみ) | `revert_overrides` で個別に cleanup |
| TMP fontAsset required | response 後の手動 `editor_get_serialized_property` 検査 | なし | font-asset を明示的に書き戻す |

「`PrefabUtility.SaveAsPrefabAsset` を直接 `editor_run_script` で呼ぶ」のは原則禁止。常に
`editor_safe_save_prefab` 経由で `protect_components` を明示する。
