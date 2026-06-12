---
tool: vrchat-event-binding
version_tested: "VRC SDK 3.7+"
last_updated: 2026-05-12
confidence: high
---

# VRChat UnityEvent / Persistent Listener Binding

## L1: 基本概要

VRChat World では、`UnityEvent` の persistent listener（インスペクターで配線されたイベント）が `UnityEventFilter` によってビルド時にフィルタリングされる。許可されていない型のリスナー、または許可されていないターゲット型のリスナーは **ビルド時に自動的に剥奪 (strip)** される。インスペクターで配線したつもりでも、剥奪後はランタイムで何も起きない。

剥奪されないようにするには、リスナーの引数型と、ターゲットコンポーネントの型を VRChat の許可リストに合わせる必要がある。Udon 側からイベントを送る場合は、`UnityEventTools.AddStringPersistentListener` で文字列イベントを送る形に絞ると確実。

## L2: 許可されている persistent listener 引数型

> 出典: <https://creators.vrchat.com/worlds/udon/networking/event-binding/> および VRC SDK の `UnityEventFilter` 実装（`Packages/com.vrchat.worlds/Runtime/Udon/UnityEventFilter.cs`）。

| 引数型 | 用途 |
|--------|------|
| `void` | 引数なしのコールバック |
| `int` | 整数引数 |
| `float` | 浮動小数点引数 |
| `string` | 文字列引数（**推奨**: 強制送信パターンで使う） |
| `bool` | bool 引数 |
| `Object` (`UnityEngine.Object`) | Object 参照引数 |
| 文字列イベント送信エントリ (`SendCustomEvent` 経由) | UdonBehaviour へのイベント送信 |

これ以外の引数型（独自構造体、enum、配列、カスタムクラス等）の persistent listener は **ビルド時に剥奪される**。

## L2: 強制 string-event-send パターン

UdonBehaviour 側で受けるイベントは、`SendCustomEvent(string eventName)` で発火するのが VRChat の正規パターン。インスペクターから配線する場合は `UnityEventTools.AddStringPersistentListener` を使い、ターゲットを UdonBehaviour、メソッド名を `SendCustomEvent`、引数を発火したいイベント名（文字列）にする。

```csharp
using UnityEngine;
using UnityEngine.Events;
using UnityEditor.Events;
using VRC.Udon;
using VRC.Udon.Common.Interfaces;

// 例: Toggle.onValueChanged に UdonBehaviour.SendCustomEvent("OnToggle") を配線
UnityEventTools.AddStringPersistentListener(
    toggle.onValueChanged,           // 配線先 UnityEvent<bool>
    udonBehaviour.SendCustomEvent,   // ターゲットのメソッド (UdonBehaviour 上)
    "OnToggle"                       // 引数（イベント名）
);
```

このパターンは以下の理由で堅牢:

- 引数型が `string` なので `UnityEventFilter` の許可リストに常に通る
- ターゲットが `UdonBehaviour.SendCustomEvent` なので許可リスト UI イベントと一致する
- Udon 側は `public void OnToggle()` を定義するだけで受信できる
- `[NetworkCallable]` を付ければそのまま `SendCustomNetworkEvent` でも同じ受信エントリが使える

## L2: 許可されている UI イベント送信先テーブル（ミラー）

> 出典: <https://creators.vrchat.com/worlds/udon/networking/event-binding/#allowed-ui-event-targets>。

VRC SDK は以下の UI コンポーネントの persistent listener エントリを許可する。これら以外のコンポーネントへの配線は剥奪対象。

| ターゲット型 | 許可されているメソッド |
|------------|--------------------|
| `UnityEngine.UI.Button` | `onClick` 経由で `UdonBehaviour.SendCustomEvent` |
| `UnityEngine.UI.Toggle` | `onValueChanged` 経由で `UdonBehaviour.SendCustomEvent` |
| `UnityEngine.UI.Slider` | `onValueChanged` 経由で `UdonBehaviour.SendCustomEvent` |
| `UnityEngine.UI.Dropdown` / `TMP_Dropdown` | `onValueChanged` 経由で `UdonBehaviour.SendCustomEvent` |
| `UnityEngine.UI.InputField` / `TMP_InputField` | `onEndEdit` / `onValueChanged` 経由で `UdonBehaviour.SendCustomEvent` |
| `UnityEngine.UI.Scrollbar` | `onValueChanged` 経由で `UdonBehaviour.SendCustomEvent` |
| `UnityEngine.EventSystems.EventTrigger` | 各イベントエントリで `UdonBehaviour.SendCustomEvent` |

> 上記表は VRChat 公式ドキュメントの allowlist を反映する手動ミラー。リリース毎に上流が変わる可能性があるため、L3 のリンク先を必ず確認すること。

## L3: AddStringPersistentListener コードスニペット

エディタースクリプトで配線を自動生成する場合の参考実装。`UnityEventTools` は `UnityEditor.Events` 名前空間（`UnityEditor.dll`）。

```csharp
using UnityEditor;
using UnityEditor.Events;
using UnityEngine;
using UnityEngine.UI;
using VRC.Udon;

[CustomEditor(typeof(MyWiringHelper))]
public class MyWiringHelperEditor : Editor
{
    public override void OnInspectorGUI()
    {
        base.OnInspectorGUI();
        var helper = (MyWiringHelper)target;
        if (GUILayout.Button("Wire button → udon"))
        {
            UnityEventTools.AddStringPersistentListener(
                helper.button.onClick,
                helper.udon.SendCustomEvent,
                helper.eventName);
            EditorUtility.SetDirty(helper.button);
        }
    }
}
```

ポイント:

- `AddStringPersistentListener` の第 2 引数はメソッド参照（インスタンスメソッド or `static` メソッド）。VRChat では `UdonBehaviour.SendCustomEvent` を渡す
- 第 3 引数の文字列が persistent な引数として永続化される（プレハブ / シーンに保存される）
- 配線後は `EditorUtility.SetDirty(target)` でダーティーフラグを立てないと保存されない
- この API は Edit Mode 専用（ランタイムでは `AddListener(action)` を使うが、こちらは persistent ではないので保存されない）

## L2: 剥奪されたかどうかを確認する方法

- ビルド後に `UdonBehaviour.SendCustomEvent("XXX")` が呼ばれないとき、ビルドログに `UnityEventFilter: stripping ...` の警告があるか確認
- インスペクター上のコールバックリストで `<Missing>` 表示になっていないか確認（剥奪は静かに起きる場合があり、ログだけが頼りなことも）
- prefab-sentinel の `editor_console` で `UnityEventFilter` 文字列を含むログを抽出すると確認しやすい

## 実運用で学んだこと

### typed `UnityEvent<T>` への string-mode persistent listener 適用

> 出典: <https://docs.unity3d.com/ScriptReference/Events.UnityEventTools.AddStringPersistentListener.html> および VRC SDK の `UnityEventFilter` 実装。L2「強制 string-event-send パターン」節の延長。

| 項目 | 観察結果 |
|---|---|
| (a) typed event との互換性 | `UnityEventTools.AddStringPersistentListener(targetEvent, target.SendCustomEvent, "MethodName")` は **`UnityEvent<float>` / `UnityEvent<bool>` / `UnityEvent<BaseEventData>` でもそのまま呼べる**。call site は同じ 3 引数オーバーロード。typed event `T` を意識する必要はない。 |
| (b) typed payload の扱い | 受信側 `UdonBehaviour.SendCustomEvent(string)` には固定の文字列引数のみが渡る。**typed event 側の payload (Slider の float、Toggle の bool、EventTrigger の BaseEventData) は silently ignored** — Persistent listener が string モードで登録されているため Unity の event dispatcher は payload を渡さない。Slider 値や Toggle 値を Udon 側で読みたい場合は `SendCustomEvent` 側でターゲットコンポーネントを直接照会する必要がある。 |
| (c) build 時 strip mechanism | `UnityEventFilter` は persistent listener を「ターゲット型 × メソッド × 引数型」のホワイトリストで判定する。string-mode 経路のターゲットは `VRC.Udon.UdonBehaviour.SendCustomEvent(string)`、引数型は `string`。両方とも L2 の許可リストに入っているため、`UnityEvent<float>` などの typed event 側に登録されていても **listener エントリ自体は string モードなので strip されない**。typed event の payload 型 (`float` / `bool` / `BaseEventData`) は strip 判定の入力にならない。 |
| (d) caveat: 非許可 payload 型 | 上記 (c) は string-mode listener に限った話。同じ typed event に **非 string モード**（payload 型を直接受ける `AddPersistentListener<T>` 系）で登録した場合、`T` が許可型 (`int` / `float` / `string` / `bool` / `Object`) でないと strip される。例: カスタム enum を直接受ける listener、あるいは `BaseEventData` を `T` として受け取るターゲットメソッド (`void OnPointer(BaseEventData)` 等) を typed-mode で登録すると、`BaseEventData` は L2 の許可型表に無いため build 時に剥離される。**string モードに統一すれば payload 型に関わらず安全**というのが実用上のルール。 |

実装テンプレート（typed event を string モードで配線する Editor スクリプト）:

```csharp
using UnityEditor.Events;
using UnityEngine.UI;
using VRC.Udon;

void WirePlaybackControls(Slider volume, Toggle mute, Button play, UdonBehaviour ctrl)
{
    // UnityEvent<float> も UnityEvent<bool> も UnityEvent も同じ呼び出し形式。
    UnityEventTools.AddStringPersistentListener(
        volume.onValueChanged, ctrl.SendCustomEvent, "OnVolumeChanged");
    UnityEventTools.AddStringPersistentListener(
        mute.onValueChanged, ctrl.SendCustomEvent, "OnMuteToggled");
    UnityEventTools.AddStringPersistentListener(
        play.onClick, ctrl.SendCustomEvent, "OnPlayTapped");
}
```

Udon 側は `public void OnVolumeChanged()` / `public void OnMuteToggled()` /
`public void OnPlayTapped()` を定義するだけで受信できる（payload 引数なし）。Slider の
現在値は `OnVolumeChanged()` 内で `slider.value` を直接読む。

## 関連 knowledge

- [prefab-sentinel-ugui-event-wiring](./prefab-sentinel-ugui-event-wiring.md)
