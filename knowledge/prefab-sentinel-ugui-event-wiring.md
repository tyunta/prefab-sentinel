---
tool: prefab-sentinel-ugui-event-wiring
version_tested: "prefab-sentinel 0.7.1 / Editor Bridge 0.7.1"
last_updated: 2026-06-13
confidence: high
---

# PrefabSentinel uGUI UnityEvent / Udon Event Wiring

## 基本情報

対象は Unity uGUI component の persistent listener を PrefabSentinel で検査・修復する authoring workflow。

公式参照:

- [VRChat UI Events](https://creators.vrchat.com/worlds/udon/ui-events/)
- [VRChat Udon Special Nodes: SendCustomEvent](https://creators.vrchat.com/worlds/udon/graph/special-nodes)
- [UnityEventTools.AddStringPersistentListener](https://docs.unity3d.com/ScriptReference/Events.UnityEventTools.AddStringPersistentListener.html)

VRChat world の uGUI から Udon / UdonSharp へイベントを送る基本形は、persistent listener の target を `VRC.Udon.UdonBehaviour`、method を `SendCustomEvent`、persistent string argument を Udon event name にする構成。

UdonSharpBehaviour の C# proxy component は Inspector 上で見つけやすいが、uGUI persistent listener の送信先としては backing `UdonBehaviour` を対象にする。`Slider.onValueChanged` のような typed `UnityEvent<T>` でも、string-mode listener は typed payload を Udon event method へ渡さない。受信側は `public void OnValueChanged()` のような引数なし event method を用意し、必要な値は serialized field 経由で対象 UI component から読む。

PrefabSentinel の通常 symbol inspection は GameObject、Component、field 参照の把握に向く。一方、uGUI persistent listener は component field のさらに内側にある UnityEvent serialized data なので、配線不良の診断では persistent call list を明示的に読む必要がある。

## 主要 API・概念

### Persistent listener の正規形

VRChat uGUI event から Udon event を起動する listener は、保存後の serialized data が概念的に次の条件を満たす。

| 項目 | 期待値 |
|------|--------|
| target component | backing `VRC.Udon.UdonBehaviour` |
| method name | `SendCustomEvent` |
| persistent listener mode | string mode (`PersistentListenerMode.String`) |
| string argument | Udon / UdonSharp の public event method name |
| call state | 実行可能な状態 |

Unity serialized data では、persistent call に次のような field が並ぶ。

| SerializedProperty | 意味 |
|--------------------|------|
| `m_Target` | 呼び出し先 `UnityEngine.Object` reference |
| `m_TargetAssemblyTypeName` | target の assembly-qualified type name |
| `m_MethodName` | 呼び出す method name |
| `m_Mode` | persistent listener mode |
| `m_Arguments.m_StringArgument` | string-mode listener の固定引数 |
| `m_CallState` | listener の実行状態 |

Unity の `PersistentListenerMode.String` は integer value `5` として保存される。`m_Mode = 5` で `m_MethodName = SendCustomEvent`、`m_Arguments.m_StringArgument` が event name なら、typed `UnityEvent<float>` / `UnityEvent<bool>` 上でも Udon へ string event を送る listener として扱える。

### uGUI event ごとの serialized path

代表的な uGUI event は次の property 配下に persistent call list を持つ。

| Component | UnityEvent field |
|-----------|------------------|
| `Button` | `m_OnClick` |
| `Toggle` | `m_OnValueChanged` |
| `Slider` | `m_OnValueChanged` |
| `Dropdown` / `TMP_Dropdown` | `m_OnValueChanged` |
| `InputField` / `TMP_InputField` | `m_OnEndEdit` または `m_OnValueChanged` |

Persistent call list の共通 path は、対象 event field の下の `m_PersistentCalls.m_Calls`。例えば `Slider.onValueChanged` は `m_OnValueChanged.m_PersistentCalls.m_Calls` を読む。

### PrefabSentinel で見るべき階層

`find_unity_symbol(include_fields=true)` は UI component の基本 field を把握する入口として使う。`Slider` なら `m_TargetGraphic`、`m_FillRect`、`m_HandleRect` などの UI 内部参照を確認できる。

Persistent listener の target / method / argument まで必要な場合は、Unity serialized object として event field を読む。PrefabSentinel に専用 high-level inspector がない操作では、Editor Bridge の `editor_run_script` から `SerializedObject` / `SerializedProperty` を使って persistent call list を dump する。

### Diagnostic dump の最低項目

Persistent listener の診断レポートには、最低限次の項目を出す。

- asset path または scene path
- hierarchy path
- component type
- event property path
- persistent call index
- target object path
- target component type
- method name
- mode integer
- string argument
- call state

出力は「listener があるか」だけで終わらせない。target が UdonSharp proxy か backing `UdonBehaviour` かを判別できる情報と、string argument の実値が必要になる。

### Source Prefab と有効な配線

UI 部品を子 Prefab として再利用している構成では、子 Prefab asset には persistent listener が存在せず、親 Prefab または Scene instance の override として listener が入ることがある。

そのため「UI Prefab 単体で listener count が 0」だけでは未配線と判断しない。実際に runtime で使われる親 Prefab / Scene instance 側の effective listener を検査する。

## 使い分け

### UI component の参照不良を疑う場合

見た目や操作不能が主症状の場合は、まず UI component 自体の参照を確認する。

- `Slider.m_FillRect`
- `Slider.m_HandleRect`
- `Selectable.m_TargetGraphic`
- `Graphic.raycastTarget`
- `CanvasGroup.interactable`

これらは persistent listener とは別の問題。`find_unity_symbol(include_fields=true)` と UI hierarchy inspection で切り分ける。

### Udon event が呼ばれない場合

UI は反応しているが Udon method が呼ばれない場合は、persistent listener を優先して検査する。

確認する分岐:

- listener count が 0
- target が backing `UdonBehaviour` ではない
- method が `SendCustomEvent` ではない
- mode が string mode ではない
- string argument が Udon event name と一致しない
- 親 Prefab / Scene instance 側ではなく子 Prefab asset だけを見ている

### 既存の正常 UI と比較する場合

同じ Prefab 内に正常に動く `Button` / `Toggle` / `Slider` がある場合は、その persistent call と比較する。

比較軸:

- target component の type
- target object の hierarchy path
- method name
- mode
- string argument
- call state

特に同一 GameObject に複数の UdonBehaviour 系 component がある場合、path だけでは target を一意に説明できない。serialized `m_Target` の実体を確認し、必要なら component suffix や component index を含む表現で書き込み対象を明確にする。

### 修復 API の選択

UnityEvent の persistent listener を作成する場合は、Unity Editor API の `UnityEventTools.AddStringPersistentListener` を使う。既存 listener を置き換える場合は、対象 event の persistent listener を削除してから string-mode listener を追加する。

保存対象が Prefab asset なら Prefab asset を保存し、Scene instance なら Scene を保存する。live Editor state と saved disk state は分けて検証する。

## 落とし穴

### UdonSharp proxy を target にしている

条件: persistent listener の target が UdonSharpBehaviour の C# proxy component になっている。

症状: Inspector 上では何かが配線されているように見えるが、VRChat UI event の許可された送信経路と一致せず、Udon event が起動しない。

回避策: target を backing `VRC.Udon.UdonBehaviour` にし、method を `SendCustomEvent`、string argument を event name にする。

### `Slider.onValueChanged` の float payload を Udon event method が受け取る前提にする

条件: `Slider.onValueChanged` が `UnityEvent<float>` なので、Udon method 側も float parameter を受け取ると仮定する。

症状: string-mode listener では slider value が event method parameter として渡らない。

回避策: Udon event method は引数なしにし、method 内で serialized `Slider` reference の `value` を読む。

### 子 Prefab asset だけを検査する

条件: UI を子 Prefab として配置し、親 Prefab / Scene instance で listener override を持つ。

症状: 子 Prefab asset の listener count は 0 だが、実際の使用箇所には listener が存在する。または逆に、子 Prefab を修正しても runtime instance の override が変わらない。

回避策: source Prefab と effective Prefab / Scene instance を区別し、実際に使われる asset scope で persistent call list を検査する。

### `find_unity_symbol` だけで event 配線を判断する

条件: UI component の field inspection で `m_TargetGraphic` などが見えているため、event wiring も確認済みと判断する。

症状: UI 内部参照は正常でも persistent listener が未配線または target mismatch のまま残る。

回避策: persistent listener の診断では `m_PersistentCalls.m_Calls` を読む。通常の component field inspection と event listener inspection を別物として扱う。

### 保存前の live state を成功扱いにする

条件: Editor 上の UnityEvent を更新したあと、Prefab asset / Scene を保存せずに offline validation へ進む。

症状: live Editor 上では listener があるが、保存済み asset には反映されていない。

回避策: write 後に対象 asset を保存し、保存済み state に対して persistent call dump、`validate_structure`、`validate_refs` を実行する。

## 検証パターン

1. `find_unity_symbol(include_fields=true)` で UI component と receiver object の存在を確認する。
2. 対象 event field の `m_PersistentCalls.m_Calls` を dump する。
3. listener count、target type、method name、mode、string argument、call state を確認する。
4. target が UdonSharp proxy なら backing `UdonBehaviour` へ差し替える。
5. listener を作る場合は `UnityEventTools.AddStringPersistentListener(event, udon.SendCustomEvent, eventName)` を使う。
6. Prefab asset または Scene を保存する。
7. 保存済み state で persistent call list を再 dump する。
8. `validate_structure` で serialized asset の構造破損がないことを確認する。
9. `validate_refs` で missing GUID / fileID がないことを確認する。
10. `editor_console` で compile error と `UnityEventFilter` 系 warning を確認する。

`validate_refs` は参照破損の検査であり、UnityEvent の target / method / string argument の意味的正しさまでは保証しない。persistent call dump と validation を併用する。

## 関連 knowledge

- [vrchat-event-binding](./vrchat-event-binding.md)
- [prefab-sentinel-live-editor-udonsharp-assets](./prefab-sentinel-live-editor-udonsharp-assets.md)
- [prefab-sentinel-udonsharp-prefab-stage-receiver-wiring](./prefab-sentinel-udonsharp-prefab-stage-receiver-wiring.md)
- [prefab-sentinel-wiring-triage](./prefab-sentinel-wiring-triage.md)
