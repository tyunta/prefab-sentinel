---
tool: udonsharp-sync-mode-continuous-vs-manual
version_tested: "VRC SDK 3.10.x / UdonSharp 1.x"
last_updated: 2026-05-10
confidence: high
---

# UdonSharp Sync Mode: Continuous vs Manual の選択基準

UdonSharp の `[UdonBehaviourSyncMode]` 選択は **データサイズと更新頻度** で決まる。Manual sync は payload 大きさが許す代わりに高頻度の `RequestSerialization` で rate limit に詰まり、Continuous sync は rate limit が緩い代わりに 1 回あたり ~200 bytes が上限となる。本稿は原因と判断基準を knowledge として固定する。

## 公式仕様 (簡約)

ソース: https://creators.vrchat.com/worlds/udon/networking/network-details/

| 項目 | Continuous | Manual |
|---|---|---|
| 1 回 serialize の上限 | 約 200 bytes | 約 280,496 bytes |
| 送出契機 | 自動 (~10Hz) | `RequestSerialization()` 呼び出し |
| 中間値復元 | あり (intermediate value approximation) | なし |
| rate limit | 主に bandwidth 全体 (~11KB/s 全 udon 合算) | **データサイズに比例して送信間隔が伸びる** |
| 想定用途 | 連続変化 (transform、erratic 値) | 離散的な状態変化 (chess 駒、score、toggle) |

> 「Each manually-synced object is rate limited as a factor of the data size.
> The more it sends, the more its send rate is limited.」
> ― 公式 doc

つまり Manual sync は「sync する変数が多い & 頻繁に書き換える」と **rate limit が急激に厳しくなる**。
**毎フレーム update する値を Manual sync に乗せると、データ量 × 高頻度で rate limit が発火し、
他クライアントへの到達が秒〜十数秒オーダーで遅れる**。

## 失敗例: Manual sync + 毎フレーム書き換え

### 構成

- `PlayerProbe : UdonSharpBehaviour` を VRCPlayerObject template にし、`[UdonBehaviourSyncMode(Manual)]`
- synced field: 毎フレーム更新の数値 field 1 個 + 離散的な状態 field 数個 (合計 7 field、~28 bytes)
- そのうち数値 field 1 個を Update() で毎フレーム書き換え + `RequestSerialization()` を 5Hz で呼んでいた

### 症状

- ローカルの表示は即時反応 (ローカル更新なので当然)
- **他プレイヤーから見ると、値の反映が 10 秒ぐらい遅れて伝わる**
- 状態遷移を伴うイベント表示も同じく遅延し、表示の時間窓内に届かないことすらあった

### 原因

Manual sync の rate limit は「データサイズ × 送信頻度」で動的に絞られる。
複数 player template が同時に 5Hz で `RequestSerialization` を呼ぶと、
**各 client が抱える manual sync 全体の bandwidth が ~11KB/s に達し**、送信間隔が秒オーダーに伸びる。
コードは正常、ネットワーク層が throttle しているだけ。

### 修正

`[UdonBehaviourSyncMode(Continuous)]` に切替。

```csharp
// Before:
[UdonBehaviourSyncMode(BehaviourSyncMode.Manual)]
public class PlayerProbe : UdonSharpBehaviour { ... }
// + Update() 内で 5Hz の RequestSerialization()

// After:
[UdonBehaviourSyncMode(BehaviourSyncMode.Continuous)]
public class PlayerProbe : UdonSharpBehaviour { ... }
// RequestSerialization() 不要、SDK が自動で ~10Hz 送出
```

これで他プレイヤーへの伝搬が ~100ms オーダーに収束。
Continuous の 200 bytes/serialization 上限内 (28 bytes) なので問題なく成立。

## 選択フローチャート

```
sync する total payload は何 bytes?
├─ 0 bytes (sync 不要) → BehaviourSyncMode.NoVariableSync
├─ ~200 bytes 以下 + 毎フレーム or 高頻度 (>1Hz) 更新 → Continuous
├─ ~200 bytes 以下 + 低頻度 (<1Hz) 更新 → Manual でも OK (どちらでも動く)
├─ 200 bytes 超 + 低頻度 (button click 等) → Manual 必須
└─ 200 bytes 超 + 高頻度 → 設計を見直す (sync 量を減らす / 分割する)
```

## 実装例の最終決定

- `PlayerProbe` (per-player template, 28 bytes, 数値 field を毎フレーム更新): **Continuous**
- `SceneHub` (scene-fixed, sync state なし): **NoVariableSync**
- 単発の全体通知: `SendCustomNetworkEvent(All, "...")` (sync field を経由しない)

## 「Manual で 1Hz だから大丈夫」と思った時の追加チェック

Manual の rate limit は **個別 behaviour 単位ではなく、client 全体の bandwidth budget で決まる**。
1 個だけなら 1Hz でも余裕でも、**80 player 同時参加で全員が 1Hz で書き換える** = 80Hz 相当。これで詰む。

→ 設計時に「同時参加人数 × 各人の更新頻度 × payload size」が ~11KB/s に収まるか試算する。
収まらなければ Continuous (= SDK 側の auto-throttle に任せる) か、sync 内容を粗く間引く。

## 関連 anti-pattern (公式 / 一般論)

- ❌ `Update()` 内で `RequestSerialization()` を毎フレーム呼ぶ (Manual): bandwidth 詰まり確定
- ❌ Continuous sync で 200 bytes 超を抱える (例: synced array): clogged で send fail
- ❌ Manual sync の `OnPreSerialization` 内で field を書き換える: serialize スコープを越えるので silent drop
- ✅ Continuous sync は「ownership 持ってる側が field を書く」だけ。`RequestSerialization` 不要
- ✅ Manual sync は「ownership 持ってる側が field を書いたら **必ず** `RequestSerialization()` を呼ぶ」。忘れると永久に伝わらない

## Sources

- 公式: https://creators.vrchat.com/worlds/udon/networking/network-details/
- 公式: https://creators.vrchat.com/worlds/udon/networking/variables/
- UdonSharp docs: https://udonsharp.docs.vrchat.com/networking-tips-&-tricks/
