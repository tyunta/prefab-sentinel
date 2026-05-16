---
tool: vrchat-ui-commit-on-release-pattern
version_tested: "VRC SDK 3.7+ / UdonSharp 1.x / Unity 2022.3"
last_updated: 2026-05-11
confidence: high
---

# Slider drag を commit-on-release で broadcast に流すパターン

VRChat World UI で **Slider を broadcast (synced) に直結すると、drag 中に onValueChanged が毎フレーム発火**して帯域消費と他クライアントの jitter が酷くなる。"離した時の値だけ sync する" pattern。

## L1: 問題

### Slider.onValueChanged は per-frame fire

`UnityEngine.UI.Slider.onValueChanged` は **値が変わるたび** (= drag 中はほぼ毎 frame) に fire する。これを直接 synced 書込に流すと:

- bandwidth: drag 1 秒 = 30-60 回 `RequestSerialization` (Manual Sync でも 1 message/sec の VRChat throttle で 30+ 回が圧縮されて送られる)
- visual jitter: 他 client が seek slider に追随して video position が震える
- multi-player race: 複数 client が同時に slider 操作したら最後の per-frame 書込が勝つ (運次第)

broadcast 用途 (= 全員で値を共有する seek bar、stage lighting intensity 等) では「ユーザーが drag を完了した値だけ」が意味を持つ。

## L2: 解決パターン (EventTrigger PointerDown / PointerUp)

### 構造

```
SeekSlider (Slider component + EventTrigger component)
├─ Background (Image)
├─ Fill Area
│   └─ Fill (Image)
└─ Handle Slide Area
    └─ Handle (Image)
```

### イベント配線 (string-mode persistent listener)

| UI イベント | Udon メソッド | 役割 |
|---|---|---|
| Slider.onValueChanged | `OnSeekDragPreview` | drag 中のローカル時刻表示更新のみ。sync は走らない |
| EventTrigger PointerDown | `_OnSeekDragStart` | drag 開始フラグセット |
| EventTrigger PointerUp | `_OnSeekDragEnd` | drag 終了。ここで初めて `bridge.SetGlobalSeek(slider.value)` を呼ぶ |

### UdonSharp 実装

```csharp
[UdonBehaviourSyncMode(BehaviourSyncMode.None)]
public class MyControlPanel : UdonSharpBehaviour {
    public MyBridge bridge;
    public Slider seekSlider;
    public TextMeshProUGUI seekTimeText;

    private bool _isDraggingSeek;

    public void _OnSeekDragStart() {
        _isDraggingSeek = true;
    }

    public void OnSeekDragPreview() {  // Slider.onValueChanged
        _isDraggingSeek = true;  // PointerDown 漏れに対する safety
        // local preview だけ更新 (broadcast 系メソッドは呼ばない)
        if (seekSlider != null) {
            int sec = (int)seekSlider.value;
            seekTimeText.text = FormatTime(sec);
        }
    }

    public void _OnSeekDragEnd() {  // EventTrigger PointerUp
        _isDraggingSeek = false;
        if (bridge != null && seekSlider != null) {
            bridge.SetGlobalSeek(seekSlider.value);  // ← 1 操作 = 1 sync 書込
        }
    }

    private void Update() {
        // drag 中は Bridge から来る他 client の値を反映しない (自分の操作 preserved)
        if (_isDraggingSeek) return;
        if (bridge == null || seekSlider == null) return;
        float shared = bridge.GetSharedTimeSeconds();
        seekSlider.SetValueWithoutNotify(shared);
    }
}
```

### Inspector 配線手順 (Editor / MCP 経由)

Button.onClick 用の `UnityEventTools.AddStringPersistentListener` と同じ呼び方で、**typed UnityEvent (`UnityEvent<float>` / `UnityEvent<BaseEventData>` 等) に対しても動作する**:

```csharp
// in an Editor script
using UnityEditor.Events;
using UnityEngine.EventSystems;

var ub = uiPanel.GetComponent<UdonBehaviour>();  // target

// Slider.onValueChanged は UnityEvent<float> だが、string-mode listener で OK
// (float 引数は silently ignored、固定 string argument "OnSeekDragPreview" が SendCustomEvent に渡る)
UnityEventTools.AddStringPersistentListener(
    slider.onValueChanged, ub.SendCustomEvent, "OnSeekDragPreview");

// EventTrigger PointerDown / PointerUp
var eventTrigger = sliderGo.AddComponent<EventTrigger>();
var downEntry = new EventTrigger.Entry { eventID = EventTriggerType.PointerDown,
                                          callback = new EventTrigger.TriggerEvent() };
UnityEventTools.AddStringPersistentListener(
    downEntry.callback, ub.SendCustomEvent, "_OnSeekDragStart");
eventTrigger.triggers.Add(downEntry);

var upEntry = new EventTrigger.Entry { eventID = EventTriggerType.PointerUp,
                                        callback = new EventTrigger.TriggerEvent() };
UnityEventTools.AddStringPersistentListener(
    upEntry.callback, ub.SendCustomEvent, "_OnSeekDragEnd");
eventTrigger.triggers.Add(upEntry);
```

## L3: 落とし穴

### 1. PointerUp が EventTrigger を経由しないケース

VR controller が slider 領域**外**で release (pointer が slider から外れた状態で grip 離す) → EventTrigger PointerUp が発火しない可能性。

**対策**: `OnSeekDragPreview` 内で `_isDraggingSeek = true` を冗長に書く (safety)、次の操作 (PauseResume 押下等) で `_isDraggingSeek` を reset する、または `Update()` で「最後の OnValueChanged から N 秒経過したら自動 commit」。実害は「drag が一時的に開けっぱなしになり、自動 sync が来ない」だけで操作不能になる訳ではない。

### 2. `Slider.SetValueWithoutNotify` を使わないと無限ループ

Bridge から来た値で slider を更新する際、`slider.value = X` だと `onValueChanged` が再発火 → preview 更新 → Bridge 書込 → 無限ループ。**必ず `SetValueWithoutNotify(X)` を使う**。

### 3. drag 中の sync 雪崩

`OnSeekDragPreview` の中で誤って `bridge.SetGlobalSeek` を呼ぶと per-frame sync になる。preview とは「local UI 表示の更新だけ、broadcast は触らない」と厳格に分離する。

### 4. Slider が機能するための sub-objects 必須

`editor_create_ui_element(type="Slider")` 系の tool は Slider component だけ作成して終わるケースが多い。`Background` / `Fill Area` (中に `Fill`) / `Handle Slide Area` (中に `Handle`) を子に作って、Slider component の `fillRect` / `handleRect` / `targetGraphic` に bind しないと UI として動作しない (drag ハンドル無し)。

`editor_run_script` で `PrefabUtility.LoadPrefabContents` + 1 transaction で全部組むのが現状確実。

## L4: 適用場面

| 適用すべき | 適用すべきでない |
|---|---|
| Seek bar (video の global 再生位置) | Volume slider が完全 local (broadcast 不要) |
| Stage lighting intensity (全員で共有) | UI tweak slider (個人画面の見た目調整) |
| Camera FOV broadcast | per-frame の連続値が意味を持つもの (例: 描画 effect の連続変動) |

## L5: 関連 pattern

- **VRChat の UI cursor 要件** (`vrchat-event-binding.md` を参照): WorldSpace Canvas + GraphicRaycaster + VRCUiShape + BoxCollider + 非 UI レイヤー + EventSystem
- **string-mode persistent listener** が `UnityEventFilter` に剥離されない理由: target が `SendCustomEvent(string)` (= `UnityAction<string>`) で、string mode で固定 argument を持つので "typed event の typed call" にならない
- **Manual Sync の single anchor**: `udonsharp-manual-sync-single-anchor.md` (本 knowledge dir 内)。`bridge.SetGlobalSeek` は anchor を bump して 1 度だけ全 client に通知する設計と整合

## 参考

- Unity Scripting Reference: [Slider.SetValueWithoutNotify](https://docs.unity3d.com/2022.3/Documentation/ScriptReference/UI.Slider.SetValueWithoutNotify.html)
- Unity Scripting Reference: [UnityEventTools.AddStringPersistentListener](https://docs.unity3d.com/2022.3/Documentation/ScriptReference/Events.UnityEventTools.AddStringPersistentListener.html)
