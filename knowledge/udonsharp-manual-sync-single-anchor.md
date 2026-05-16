---
tool: udonsharp-manual-sync-single-anchor
version_tested: "VRC SDK 3.7+ / UdonSharp 1.x / Unity 2022.3"
last_updated: 2026-05-11
confidence: high
---

# Manual Sync の単一 anchor + OnDeserialization diff パターン

`[UdonBehaviourSyncMode(BehaviourSyncMode.Manual)]` で **複数 synced field を持つ UB** が「何かが変わった」を検知して 1 度だけ反応するための設計パターン。

## L1: 問題

### `[FieldChangeCallback]` の race

UdonSharp の `[FieldChangeCallback(nameof(Prop))]` は **synced field が deserialize された瞬間に発火**する。複数 synced field がある場合、各 field の deserialize は順次起こるので:

```csharp
[UdonSynced] VRCUrl _syncedUrl;
[UdonSynced] float  _baselineTime;
[UdonSynced, FieldChangeCallback(nameof(BaselineMs))] long _baselineMs;

public long BaselineMs {
    get => _baselineMs;
    set {
        _baselineMs = value;
        // ← ここで _syncedUrl / _baselineTime が "まだ deserialize されていない" 可能性
        DoSomethingWithUrl(_syncedUrl);  // URL='' で abort 等の障害
    }
}
```

実害例: URL Submit イベント発火時に URL を読みに行くと空文字が返り、後続処理が abort。

### N-field 比較の冗長性

代替案として「全 field 個別に `_lastSeenX` を持って `OnDeserialization` 内で N 回 if 比較」は機能するが、field が増えるたびに比較箇所が増えるし、新 field 追加忘れがあると検知漏れになる。

## L2: 解決パターン

### 単一 anchor field を全 state 変更時に bump

`_baselineServerTimeMs : long` のように **server 時刻ベースの単調増加 field** を 1 つ持ち、**すべての state 変更時に必ず bump** する。anchor 以外の field は普通に synced。

```csharp
[UdonBehaviourSyncMode(BehaviourSyncMode.Manual)]
public class MyBridge : UdonSharpBehaviour {
    [UdonSynced] public VRCUrl _syncedUrl;
    [UdonSynced] public float  _baselineTime;
    [UdonSynced] public long   _baselineServerTimeMs;  // ← anchor
    [UdonSynced] public bool   _globalPlaying;
    [UdonSynced] public bool   _globalLoop;

    private long _lastSeenBaselineServerTimeMs;

    public override void OnDeserialization(DeserializationResult result) {
        if (_baselineServerTimeMs == _lastSeenBaselineServerTimeMs) return;  // diff 1 行
        _lastSeenBaselineServerTimeMs = _baselineServerTimeMs;
        if (listener != null) listener.OnSyncedStateChanged();
    }

    // すべての public setter が共通で Commit() を呼ぶ
    public void SetGlobalPause() {
        TakeOwnership();
        _baselineTime = ComputeCurrentSharedTime();
        _baselineServerTimeMs = Networking.GetServerTimeInMilliseconds();
        _globalPlaying = false;
        Commit();
    }

    public void SetGlobalResume() {
        TakeOwnership();
        _baselineServerTimeMs = Networking.GetServerTimeInMilliseconds();
        _globalPlaying = true;
        Commit();
    }

    public void SetGlobalSeek(float seconds) {
        TakeOwnership();
        _baselineTime = seconds;
        _baselineServerTimeMs = Networking.GetServerTimeInMilliseconds();
        Commit();
    }

    public void SetGlobalLoop(bool on) {
        if (_globalLoop == on) return;
        TakeOwnership();
        // ループ切替時も anchor を bump (時間軸を乱さない場合は baselineTime 維持または再計算)
        _baselineTime = ComputeCurrentSharedTime();
        _baselineServerTimeMs = Networking.GetServerTimeInMilliseconds();
        _globalLoop = on;
        Commit();
    }

    private void TakeOwnership() {
        if (!Networking.IsOwner(gameObject))
            Networking.SetOwner(Networking.LocalPlayer, gameObject);
    }

    private void Commit() {
        _lastSeenBaselineServerTimeMs = _baselineServerTimeMs;  // owner echo suppress
        RequestSerialization();
        if (listener != null) listener.OnSyncedStateChanged();   // owner self-fire
    }
}
```

### Listener 側 snapshot diff (差分判定は呼び出し先で)

Bridge は「何か変わった」だけ通知、何が変わったかは listener が自分で snapshot 比較して判定。

```csharp
public class MyController : UdonSharpBehaviour {
    public MyBridge bridge;
    private VRCUrl _snapUrl;
    private bool   _snapPlaying;
    private bool   _snapLoop;

    private void Start() {
        // late-joiner 対応: Bridge の現状から snapshot 初期化
        if (bridge != null) {
            _snapUrl = bridge._syncedUrl;
            _snapPlaying = bridge._globalPlaying;
            _snapLoop = bridge._globalLoop;
        }
    }

    public void OnSyncedStateChanged() {  // Bridge から push
        if (bridge == null) return;
        bool urlChanged    = !VRCUrlMatches(bridge._syncedUrl, _snapUrl);
        bool playingFlip   = bridge._globalPlaying != _snapPlaying;
        bool loopFlip      = bridge._globalLoop != _snapLoop;

        if (urlChanged)        ApplyUrlChange();
        else if (playingFlip)  ApplyPlayingChange(bridge._globalPlaying);
        else if (loopFlip)     ApplyLoopChange(bridge._globalLoop);
        else                   ApplyPureSeek();  // baselineTime のみ変化

        _snapUrl = bridge._syncedUrl;
        _snapPlaying = bridge._globalPlaying;
        _snapLoop = bridge._globalLoop;
    }
}
```

## L3: 不変条件

- **anchor field は server time ベースで単調増加**。`Networking.GetServerTimeInMilliseconds()` は instance 単位で単調、巻き戻らない。
- **anchor を bump せず synced field を書き換える経路を作らない**。OnDeserialization で diff 検出できなくなる。
- **owner は OnDeserialization が呼ばれない**ので、`Commit()` 内で明示的に listener に通知 + `_lastSeen` を bump して echo を防ぐ。
- **Start 順序非依存**: Listener の `Start` で Bridge から現状読み込み (late joiner 対応)、`OnDeserialization` で再走査。両方走っても idempotent。

## L4: 利点と制約

### 利点
- **diff 検出 1 行** (`if (_baselineServerTimeMs == _lastSeenBaselineServerTimeMs) return;`)
- field 追加時に Bridge 側の検知ロジックを変更不要 (Listener 側で snapshot 比較を追加するだけ)
- FieldChangeCallback の race を構造的に回避 (全 field deserialize 後に 1 度だけ fire)

### 制約
- 「何が変わったか」の情報は Bridge → Listener の callback に乗らない。Listener が自分で snapshot を持って比較する責務。
- すべての state 変更で必ず anchor を bump する規律が必要。1 つ忘れると沈黙伝播 (= 検知漏れ)。setter を共通の `Commit()` に集約することで強制する。

## L5: 適用場面

| 適用すべき | 適用すべきでない |
|---|---|
| synced field 3 個以上 + 複合的に変化する | synced field 1 個のみ |
| 「state 全体が変わった」を観測したい (個別 field の遷移は二次) | 個別 field の遷移を厳密にトレースしたい (FieldChangeCallback で OK) |
| Owner 自身も synced 変更時に同じロジックを実行したい | Owner は別経路 (UI ボタン押下の直接 handler 等) で OK |

## 検証経路

- 同 instance に複数 client (ClientSim 2 player or 実 VRChat instance) で:
  - Owner が `SetGlobalPause()` 連打 → 他 client で `OnSyncedStateChanged` が連打分発火し、毎回 snapshot 比較で正しい分岐に入る
  - Owner 離脱 → 別 client が owner 取得 → `SetGlobalResume()` → 全 client で反映
  - Late joiner 入室 → Start で snapshot 初期化、OnDeserialization で再走査、結果は他 client と一致

## 参考

- VRChat UdonSynced docs: https://creators.vrchat.com/worlds/udon/networking/network-components/
- `udonsharp-sync-mode-continuous-vs-manual.md` (本 knowledge dir 内、Manual Sync の前提条件)
