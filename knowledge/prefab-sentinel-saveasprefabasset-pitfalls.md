# prefab-sentinel: `PrefabUtility.SaveAsPrefabAsset` pitfalls

`PrefabUtility.SaveAsPrefabAsset` は単発で呼ぶと「黙ってコンポーネントを剥がす」「nested
override が orphan 化する」「TextMeshPro の必須参照を消す」など、保存自体は成功
(`success == true`) なのに saved asset が壊れているケースがある。`editor_safe_save_prefab`
MCP ツールは `protect_components` で守るべき型名を必須引数として受け取り、保存後に再
アタッチと差分レポートを返すことでこの罠を吸収する。

このドキュメントは「`SaveAsPrefabAsset` を直接呼んではいけないケース」のカタログ。代替
手段は **常に `editor_safe_save_prefab`** とその `protect_components` リスト。MCP 経由の
ワークフローでカバーできない `[MenuItem]` builder（Editor から直接 `LoadPrefabContents`
→ `SaveAsPrefabAsset` を回す形）向けのフォールバックパターンも併せて記載する。

> **Cross-reference**:
> - 大規模 UI prefab 構築のフロー全体は [prefab-sentinel-build-from-scratch.md](prefab-sentinel-build-from-scratch.md) を参照。
> - U# 全般 (proxy / CopyProxyToUdon / `OnBeforeSerialize` 不可) は [udonsharp.md](udonsharp.md) を参照。
> - user-driven compile 環境での compile 観察は [prefab-sentinel-workflow-patterns.md](prefab-sentinel-workflow-patterns.md) を参照。

## Mode 1: `VRC_UiShape` strip mode

**症状**: WorldSpace Canvas の GameObject に `VRC_UiShape` を attach した状態で
`PrefabUtility.SaveAsPrefabAsset` を呼ぶと、saved prefab には `VRC_UiShape` がついていない。
Editor 側のシーン instance には残っているので一見気づかない。

VRChat の laser raycast は Canvas + GraphicRaycaster + BoxCollider(isTrigger) +
**VRC_UiShape** + 非 UI layer のセットが揃っていないと UI button に反応しない。
`VRC_UiShape` が消えると panel 全ボタンが無反応になる。

**原因**: `VRC_UiShape` は VRC SDK Worlds の World-only コンポーネント。Avatar/World
兼用プロジェクトの `[ExecuteAlways]` パスが Avatar 文脈だと Strip 対象判定になるケースが
ある (VRChat SDK 内部の `IVRCSDKControlPanelBuilder` 経由のフィルタ)。VRCSDK3.dll 内型の
ため、Editor reload 時の script reference 解決で稀に "Missing Script" 状態になり
`SaveAsPrefabAsset` が strip するケースもある。U# の `OnBeforeSerialize` NRE が同時に
発生していると相関が高い。

**回避 (推奨: MCP 経由)**: `editor_safe_save_prefab` を
`protect_components=["VRC_UiShape"]` で呼ぶ。Bridge の safe-save handler は保存後の asset
を再 inspect し、`VRC_UiShape` が剥がれていたら `Undo.AddComponent` で再 attach してから
再保存する。response の `data.reattached_components` に `["VRC_UiShape"]` が含まれて
いれば strip が実際に発生したことを意味する。

**回避 (フォールバック: `[MenuItem]` builder)**: MCP を経由しない builder MenuItem の
場合は冒頭で reflection 再 attach する。冪等。

```csharp
private static void EnsureVrcUiShape(GameObject go) {
    var t = System.Type.GetType("VRC.SDK3.Components.VRCUiShape, VRCSDK3");
    if (t == null) { Debug.LogError("[Builder] VRCUiShape type not found"); return; }
    if (go.GetComponent(t) == null) go.AddComponent(t);
}

[MenuItem("Tools/.../Build Foo")]
private static void Build() {
    var root = PrefabUtility.LoadPrefabContents(Path);
    try {
        EnsureVrcUiShape(root);  // <-- 0 番目に呼ぶ
        // ... rest of build ...
        PrefabUtility.SaveAsPrefabAsset(root, Path);
    } finally {
        PrefabUtility.UnloadPrefabContents(root);
    }
}
```

## Mode 2: nested override strip mode

**症状**: ベース prefab を nest した GameObject をさらに `SaveAsPrefabAsset` する flow
(`LoadPrefabContents` → `InstantiatePrefab(other_prefab, parent)` → `SaveAsPrefabAsset`)
で、nested prefab に積んでいた modification override (RectTransform.m_AnchoredPosition、
MonoBehaviour 上の field 値、`Button.m_OnClick.m_PersistentCalls` 等) のうち、saved asset
の hierarchy 上に対応する target が存在しない (削除した / 階層を変えた / nested ではなく
フラット化された) override が **orphan modification** として saved asset に残る。Unity
の Inspector では override 行が "Missing target" として表示される。

実例: `PlaybackUi.prefab` の root 直下に空の `Inner` GameObject を挿入し、既存子要素を
`Inner` 配下へ reparent して save。次に `NadeVision.prefab` (PlaybackUi.prefab を nest)
を **任意のタイミングで save** すると、PrefabInstance.m_Modifications にあった以下の
override が **配列ごと丸ごと消える**:

- `Btn8K.m_OnClick.m_PersistentCalls.m_Calls.Array.size`
- `Btn8K.m_OnClick.m_PersistentCalls.m_Calls.Array.data[0].m_MethodName`
- `Btn8K.m_OnClick.m_PersistentCalls.m_Calls.Array.data[0].m_StringArgument`
- 他 5 button × ~6 entry = 約 40 modification

reparent された子要素 (Btn8K 等) の fileID は変わらないので override の `target.fileID`
は valid のまま。それでも Unity の prefab merge engine が「親が変わった子要素への
override は無効」と判定して strip する。

**原因**: `SaveAsPrefabAsset` は `m_Modifications` 配列を nest 元 PrefabInstance から
丸ごとコピーするが、target が解決できない override を捨てない。これは Unity の仕様で、
保存時に target 解決は走らない。逆に reparent 等で target chain が変わった場合、merge
engine が override を無効化して strip する。

**回避 (推奨: MCP 経由)**: `editor_safe_save_prefab` の response `data.orphan_modifications`
を見る。各エントリは `{ "target_object_path": "...", "property_path": "..." }` の形で、
orphan になった override の発生箇所を特定できる。`revert_overrides` でクリーンアップ
するか、`patch_apply` で nested 階層を再構築してから safe-save し直す。
`protect_components` に nest 元の component 型名を入れておくと、stripped されたケースは
Mode 1 として再アタッチで吸収される。

**回避 (フォールバック: Wire MenuItem の冪等再実行)**: PersistentListener / 配線系の
override が消えるケースは、Wire menu を冪等で全配線を毎回再設定する形に分離する。
Build menu のあと必ず Wire menu を実行するワークフロー。

```csharp
[MenuItem("Tools/.../Wire Foo")]
private static void Wire() {
    var nv = PrefabUtility.LoadPrefabContents(NadeVisionPath);
    try {
        var ui = FindByName(nv.transform, "PlaybackUi");
        var ctrlUb = UdonSharpEditorUtility.GetBackingUdonBehaviour(
            nv.GetComponentInChildren<NadeVisionLocalPlayerController>());

        WireButtonByPath(ui, "Inner/PlayPauseButton",     ctrlUb, "_OnPlayPauseTapped");
        WireButtonByPath(ui, "Inner/Q2_Quality/Btn8K",    ctrlUb, "_OnResolutionTapped_0");
        // ... etc
        WireFloatEvent(ui, "Inner/Q3_Audio/VolumeSlider", ctrlUb, "OnVolumeChanged");
        WireBoolEvent (ui, "Inner/Q3_Audio/MuteToggle",   ctrlUb, "OnMuteToggled");

        PrefabUtility.SaveAsPrefabAsset(nv, NadeVisionPath);
    } finally { PrefabUtility.UnloadPrefabContents(nv); }
}

private static void WireButtonByPath(Transform parent, string path,
    VRC.Udon.UdonBehaviour ub, string eventName) {
    var t = parent.Find(path);
    if (t == null) { Debug.LogWarning($"path '{path}' not found"); return; }
    var btn = t.GetComponent<Button>();
    if (btn == null) return;
    ClearPersistentListeners(btn.onClick);
    UnityEventTools.AddStringPersistentListener(btn.onClick,
        new UnityAction<string>(ub.SendCustomEvent), eventName);
}

private static void ClearPersistentListeners(UnityEventBase evt) {
    for (int i = evt.GetPersistentEventCount() - 1; i >= 0; i--)
        UnityEventTools.RemovePersistentListener(evt, i);
}
```

`AddStringPersistentListener` のオーバーロード型ごと (UnityEvent / UnityEvent\<float\> /
UnityEvent\<bool\>) に helper を分けると generic constraint 問題を避けられる。

## Mode 3: TextMeshPro font-asset requirement mode

**症状**: `TextMeshProUGUI` を含む prefab を `SaveAsPrefabAsset` で保存すると、saved asset
側の `m_fontAsset` が null PPtr (`{fileID: 0}`) になっている。シーン instance では正しく
`LiberationSans SDF` などが入っているが、prefab を再 instantiate すると "Default Font
Asset" のロード失敗で TMP テキストが表示されない。あるいは `TMP_Settings.defaultFontAsset`
にフォールバックしても **fontSize の体感サイズが既存 TMP と大きくズレる** (1/3〜1/2 程度
に縮小) ため、prefab 単独で見ると壊れて見える。

**原因**: TMP の `TextMeshProUGUI.OnEnable` 実装が `TMP_Settings.defaultFontAsset` を
初期化時に解決するパスがあり、`SaveAsPrefabAsset` の serialize タイミングで
`m_fontAsset` が一時的に null として捕捉されることがある。`fontAsset` が null PPtr で
保存されると、再 instantiate 時に Resources からのフォールバック読み込みも走らない
(font-asset が prefab 自体の serialized field として明示的に required 扱いされるため)。

**回避 (推奨: MCP 経由)**: `editor_safe_save_prefab` を
`protect_components=["TextMeshProUGUI"]` で呼んでもコンポーネント自体は剥がれていない
ため再アタッチは発生しないが、保存後に `editor_get_serialized_property` 等で
`m_fontAsset` を読み、null PPtr なら fontAsset を明示的に書き戻す手順を入れる。長期的
には TMP 側で `fontAsset` を runtime 初期化する component を別途用意し、prefab 内部
では参照しない設計に倒すのが安定。

**回避 (フォールバック: builder MenuItem の明示代入)**: builder で TMP コンポーネントを
作るときに最初から明示代入する。

```csharp
const string LiberationSansSdfPath =
    "Assets/TextMesh Pro/Resources/Fonts & Materials/LiberationSans SDF.asset";

var fontAsset = AssetDatabase.LoadAssetAtPath<TMP_FontAsset>(LiberationSansSdfPath);
if (fontAsset == null) { Debug.LogError("[Builder] font asset missing"); return; }

var tmp = go.AddComponent<TextMeshProUGUI>();
tmp.font = fontAsset;
tmp.text = "Foo";
tmp.fontSize = 14;
```

fontAsset 割当後は既存 TMP labels と同じ fontSize 単位で見た目が揃う。

**回避 (MCP 経由 / 推奨, issue #195)**: `editor_create_ui_element(type="TextMeshProUGUI", ...)`
を呼ぶと、`properties.font` 未指定時に Bridge handler が上記 `LiberationSans SDF.asset`
を canonical default として `AssetDatabase.LoadAssetAtPath<TMP_FontAsset>` で解決し、
`tmp.font` に代入する。同 asset がプロジェクトに存在しない場合は
`EDITOR_CTRL_CREATE_UI_TMP_FONT_MISSING` warning を返し、GameObject は作成されるが
font は未代入のまま（呼び出し元が明示的に font asset を指定するか、TextMeshPro
Essentials package を import するかを判断できる）。`color` も `properties.color`
で第一級指定できる。

## まとめ (3 modes)

| Mode | 検知 | 自動修復 | 残作業 |
|------|------|----------|--------|
| `VRC_UiShape` strip | `data.reattached_components` に `VRC_UiShape` が乗る | safe-save handler が `Undo.AddComponent` で再アタッチ | なし (再保存も自動) |
| nested override strip | `data.orphan_modifications` に target+property が乗る | なし (列挙のみ) | `revert_overrides` / Wire menu 再実行で個別に cleanup |
| TMP fontAsset required | response 後の手動 `editor_get_serialized_property` 検査 | なし | font-asset を明示的に書き戻す |

「`PrefabUtility.SaveAsPrefabAsset` を直接 `editor_run_script` で呼ぶ」のは原則禁止。
常に `editor_safe_save_prefab` 経由で `protect_components` を明示する。`[MenuItem]`
builder で直接呼ぶ場合は上記フォールバックパターンを適用する。

---

## 周辺 builder MenuItem の落とし穴

`[MenuItem]` builder で SaveAsPrefab フローを組むときに併せて踏みやすい罠。MCP の
`editor_*` ツールでカバーされているが、Editor 直駆動だと自分で書く必要がある。

### `editor_create_primitive` / `GameObject.CreatePrimitive` は 3D primitive のみ

`PrimitiveType` enum は `Cube` / `Sphere` / `Cylinder` / `Capsule` / `Plane` / `Quad`
のみ。UI element (Image / TextMeshProUGUI 等) は別経路:

```csharp
var go = new GameObject(name, typeof(RectTransform), typeof(Image));
go.transform.SetParent(parent, worldPositionStays: false);
go.GetComponent<Image>().color = Color.white;
```

builder script の中でこのパターンを `EnsureChild` / `EnsureComponent<T>` ヘルパに包んで
冪等化する。

### `UdonSharpEditorUtility.AddUdonSharpComponent` は static には存在しない

実体は `GameObject` の **extension method** で `UdonSharpEditor` namespace。

```csharp
// ✗ CS0117: no static method 'AddUdonSharpComponent'
var hint = (PlaybackUiHintController)UdonSharpEditorUtility.AddUdonSharpComponent(
    controllerGo, typeof(PlaybackUiHintController));

// ✓ extension method
using UdonSharpEditor;
var hint = controllerGo.AddUdonSharpComponent<PlaybackUiHintController>();
```

`Type.GetType` で `AddComponent` するとバッキング `UdonBehaviour` が生成されず runtime
で動かない (proxy のみ存在)。必ず U# 専用 API を使う。

### `PlayerData.TryGetBool` の C# signature

VRChat 公式 doc は graph 表現で「2 outputs: value, success」と表示するが、C# は 3 引数
+ 戻り値 bool:

```csharp
// ✗ 4 引数版は存在しない (CS1501)
bool s; bool v;
PlayerData.TryGetBool(player, key, out v, out s);

// ✓ 3 引数 + 戻り値 bool
bool value;
bool success = PlayerData.TryGetBool(player, key, out value);
```

`PlayerData.SetBool` も local player のみで `(string key, bool value)` の 2 引数 (player
引数なし)。`OnPlayerRestored` event 受信前の `SetBool` は無視されるので、event 待ちに
する必要がある。

### `OnBeforeSerialize` override は U# 1.x では不可

`udonsharp_obs_nre` ノイズ抑制目的で `public override void OnBeforeSerialize() {}` を
書くと CS0115。`UdonSharp.UdonSharpBehaviour` 1.x の base クラスがこのメソッドを
explicit interface implementation で持っていて virtual 公開していないため。詳細は
[udonsharp.md](udonsharp.md) の同名節。

ノイズは無視するしかない。bridge 側で `NonFatalExceptionClassifier` が non-fatal 分類
するため、`editor_console(classification_filter="non_fatal")` で除外可能。

### `editor_recompile_and_wait` は user-driven compile と競合する

ユーザーが Unity Editor を前面で操作している環境では auto refresh が走るので、MCP 経由
で `editor_recompile_and_wait` を呼ぶと重複 trigger になる。`editor_console(log_type_filter="error")`
を直接読む方が snappy。詳細は [prefab-sentinel-workflow-patterns.md](prefab-sentinel-workflow-patterns.md)
の同名節。

initial deploy 時 (bridge protocol mismatch 状態) などは `editor_recompile_and_wait` を
能動的に呼ぶ価値あり。ユーザーに「コンパイル走らせるね」と一言断る。

---

## チェックリスト

`SaveAsPrefabAsset` ベースの builder/wirer ワークフローでは:

| カテゴリ | チェックリスト |
|---|---|
| **構造** | nested prefab に中間ノードを挿入したら親側 wire を必ず再実行 |
| **VRC components** | VRC_UiShape は `protect_components` または reflection で冪等再 attach |
| **U# component 追加** | `GameObject.AddUdonSharpComponent<T>()` extension のみ使う |
| **TMP** | font asset を明示代入 |
| **Compile 確認** | user 環境では `editor_console(log_type_filter="error")` を直接読む |
| **builder/wirer 構造** | Build と Wire を 2 段に分け、両方とも冪等 (`EnsureChild` / `EnsureComponent<T>`) |

builder/wirer は Build と Wire の 2 段に分けて両方とも冪等。`EnsureChild` /
`EnsureComponent<T>` パターンで 何度でも再実行できる形に保つ。
