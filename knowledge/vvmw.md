---
tool: vvmw
version_tested: "VizVid 2026-05 build / VRC SDK 3.7+ / UdonSharp 2023.12+"
last_updated: 2026-05-02
confidence: medium
---

# VVMW (VizVid) 統合パターン

> NadeVision (180° SBS Skybox 動画プレイヤー) 構築過程で得た知見。実環境 + ClientSim + multi-PC で検証済み。

## L1: 基本構造

VizVid は VRChat ワールド向けの汎用動画プレイヤーフロントエンド ([GitHub](https://github.com/JLChnToZ/VVMW))。`Core` + `playerHandlers[]` (AVPro / Unity / ImageViewer) の二層で、解像度・backend を `playerType` byte (1-N) で切替える。

### 主要 API
| メソッド / プロパティ | 用途 |
|---|---|
| `Core.PlayUrl(VRCUrl pc, VRCUrl quest, byte playerType)` | URL ロード開始。**自動的に Play まで進む** (load → autoplay) |
| `Core.Play() / Pause() / Stop()` | 再生制御 |
| `Core.Time` (float, 秒) / `Core.Progress` (0-1) | 再生位置 (get/set 両対応で seek 可能) |
| `Core.Duration` | 動画長 (live は 0 / Infinity) |
| `Core.IsReady` | activeHandler.IsReady && !isLoading |
| `Core.Volume` / `Core.Muted` | 音量・mute |
| `Core._AddListener(UdonSharpBehaviour callback)` | event listener 登録 (runtime OK) |

### Event listener パターン

`Core` は `UdonSharpEventSender` を継承しており、`_AddListener` で listener を登録すると以下の event 名がメソッドとして呼ばれる:

| Event 名 | タイミング |
|---|---|
| `_OnVideoBeginLoad` | `PlayUrl` 直後 |
| `_onVideoReady` | backend が load 完了 (autoplay 直前) |
| `_onVideoStart` | 再生開始 |
| `OnVideoPlay` / `OnVideoPause` | Play/Pause 遷移 |
| `_onVideoEnd` | 動画終了 (loop OFF) |
| `_onVideoLoop` | loop で先頭に戻った |
| `_OnVideoError` | エラー発生 |

listener 側はこのメソッド名を **public void** で実装する (UdonSharp の SendCustomEvent 経由)。

```csharp
public class MyController : UdonSharpBehaviour
{
    public Core core;

    private void Start()
    {
        if (core != null) core._AddListener(this);
    }

    public void _onVideoReady()
    {
        // load 完了直後の処理
    }
}
```

## L2: 落とし穴

### `Core.synced = false` の autoplay 強制 (2026-05-02 検証済み)

**症状**: `Core.synced = false` (Inspector で OFF / prefab で `synced: 0`) の構成で `Core.PlayUrl` した直後、listener 経由で `Core.Pause()` を呼んでも **直後に強制 Play される**。NadeVision の「View OFF + Local OFF の状態でもプレイヤー上で動画が動き始める」症状の根本原因。

**Why**: `Core.OnVideoReady()` (Core.cs:556) の実装が:

```csharp
SendEvent("_onVideoReady");        // listener 発火
if (!synced) {
    activeHandler.Play();           // ← unconditional
    return;
}
```

listener が `Core.Pause()` を呼んで `state=PAUSED` に変えても、`!synced` 分岐は **state を見ずに `activeHandler.Play()` を強制** する。`synced=true` なら直後の switch case PAUSED 分岐で Play が抑制されるが、`synced=false` では効かない。

**回避策**: 1 frame 遅延 Pause を予約することで autoplay 直後に Pause を当てる:

```csharp
public void _onVideoReady()
{
    if (currentState != WATCHING)
    {
        SendCustomEventDelayedFrames(nameof(_PauseAfterLoad), 1);
    }
}

public void _PauseAfterLoad()
{
    if (currentState == WATCHING) return;
    if (core != null) core.Pause();
}
```

副作用: 1 frame (~16ms at 60fps) の autoplay リーク (audio が一瞬鳴る可能性) は許容するしかない。VVMW に "load only / preload" API は無い。

### `synced=false` で `RequestSync` no-op (Core.cs:771)

`Core.Pause()` 等は内部で `RequestSync()` を呼ぶが、`synced=false` だと `RequestSerialization` は走らず early-return する (= 帯域消費なし)。設計通り「再生制御は完全 local」を意図した構成。NadeVision のように **video 同期 (URL/baseline) を別 UB (NadeVisionSyncBridge) で済ませる** 場合に有効。

### マルチモジュール音声の build-time strip (既知)

複数の `playerHandlers[]` (AVPro 8K / AVPro 6K / Unity 4K / Image Viewer) で **AudioSource を共有** すると、build 時 PreProcess の last-wins で 1 モジュールしか音が出ない。各 handler ごとに **専用 AudioSource を 1 つずつ用意** する必要がある (VVMW 既知の制約)。

### Volume/Mute の同期問題

`synced=false` の場合、Volume/Mute は完全 local。ただし VVMW UI Handler は別途 sync する設計があるので、独自 UI から `Core.Volume = X` を直接書く構成にする (UI Handler を bypass)。

## L3: NadeVision 統合パターン (実例)

### 構成

```
NadeVision/
├── VVMW (On-Screen Controls)/    # VizVid 標準階層 (Core + handlers + audio)
│   └── Core (synced=false)        # VizVid Core
└── PlaybackUi/                     # 独自 UI (PlayPause / Volume / Resolution)
    └── NadeVisionLocalPlayerController  # VVMW Core を listener 登録、Pause 等を呼ぶ
```

### Listener 登録タイミング
`Start()` 内で `core._AddListener(this)` を呼ぶ。Start order は U# で undefined だが、`_AddListener` は配列追加のみで Core の初期化に依存しないので順序問題なし。

### State machine と video state の対応
- `Idle/Sat`: video 停止 (Pause、必要なら裏ロード)
- `Watching`: video 再生 (Play、共有時計合流)

`_onVideoReady` listener で `state != Watching` なら 1 frame 遅延 Pause、`Watching` なら何もしない (autoplay → 共有時計位置に SeekToShared で seek)。

### 共有時計合流 (Master Seek なし)
Bridge に `_baselineTime + _baselineServerTimeMs` を sync。各 client が Play 押した瞬間 `expected = (now - baseline)/1000 + baselineTime` で計算した正規化位置に `Core.Progress = expected/duration` で seek。各 client は **自分のタイミング** で合流する (Master が他人を強制 seek しない)。drift は 5 秒間隔で `_DriftCheck` が再合流させる (threshold 1.5s)。

## 参考

- VizVid GitHub: https://github.com/JLChnToZ/VVMW
- 公式 docs: https://xtlcdn.github.io/VizVid/docs/
- Core.cs (autoplay 強制箇所): `Packages/idv.jlchntoz.vvmw/Runtime/VVMW/Core.cs:556-571`
- Core_Timing.cs (seek 実装): `Packages/idv.jlchntoz.vvmw/Runtime/VVMW/Core_Timing.cs:98-115`
- UdonSharpEventSender.cs (`_AddListener`): `Packages/idv.jlchntoz.vrcw-foundation/Runtime/UdonSharpEventSender.cs:30`
