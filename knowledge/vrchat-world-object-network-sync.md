---
tool: vrchat-world-object-network-sync
version_tested: "VRChat World SDK 3.7+"
last_updated: 2026-05-24
confidence: medium
---

# VRChat World Object Network Sync — Transform / SetActive 非同期の活用

VRChat world に普通に置いた GameObject の `Transform` / `GameObject.activeSelf` は **default で network 非同期**。 VRC_ObjectSync 等の sync component を attach しない限り、 各 player の Udon が `transform.position = ...` / `SetActive(true)` で動かしても他 player の view では initial state のままで sync しない。これを利用すると **Layer 10 PlayerLocal / VRC_PlayerObject template 等の特殊機能を持ち出さずに「per-player local visibility / position」 が実現できる**。

## 基本情報

- 対象: VRChat World SDK 3.x の Udon / UdonSharp scene 内 GameObject
- 関連 sync component:
  - `VRC.SDK3.Components.VRCObjectSync` (= 明示的に transform sync する場合のみ attach)
  - `VRC.SDK3.Components.VRCPickup` (= pickup pickup / drop で transform sync する場合)
  - `[UdonSynced]` attribute (= UB の field network sync、 GameObject state 自体は sync しない)
- 関連 alternative:
  - `Layer 10 PlayerLocal` (= avatar の self mesh 用、 world object も同 layer で render 制御可能だが scene 構造変更必要)
  - VRC_PlayerObject template (= player join 時に instantiate される object、 重い feature)

## 主要 API・概念

### default で非同期な GameObject state

| state | 非同期 | sync 化に必要なもの |
|---|---|---|
| `Transform.position` / `rotation` / `localScale` | **yes** (各 client 独立) | `VRCObjectSync` or 自前 `[UdonSynced]` field + 反映 logic |
| `GameObject.activeSelf` / `SetActive(...)` | **yes** | 同上 (= `[UdonSynced] bool isActive` + `OnDeserialization` で SetActive) |
| `Renderer.enabled` | **yes** | 同上 |
| `Animator` state | 通常 yes | sync が必要なら `VRC_AnimatorPlayAudio` 等 |
| `[UdonSynced]` 付き field | **no** (= sync する) | RequestSerialization etc |

つまり scene に普通に置いた object を local UB で動かしても、 他 player の client では initial state のままで static。

### 利用パターン

#### Per-player local visibility (= 自分にだけ見える)

```csharp
public GameObject mySphere;

public void OnLocalAction()
{
    mySphere.SetActive(true);  // 自分の view にだけ表示、 他 player には影響しない
}
```

mySphere は scene 内に初期 SetActive(false) で配置。各 player の Udon が SetActive(true) で local 表示 → 他 player の Udon は SetActive 呼ばない → 他 player の view では SetActive(false) のまま invisible。

#### Per-player local position (= 自分にだけ動く)

```csharp
public Transform myAnchor;

public void OnLocalTrigger()
{
    Vector3 headPos = Networking.LocalPlayer.GetTrackingData(VRCPlayerApi.TrackingDataType.Head).position;
    myAnchor.position = headPos;  // 自分の view にだけ反映、 他 player の view では initial position
}
```

#### Per-player local Material property override

[udonsharp-material-instance-pitfall.md](./udonsharp-material-instance-pitfall.md) の `MaterialPropertyBlock` 経由が default 解。block 自体も per-renderer state で各 player の Udon が独立に local 上書き。

## 使い分け

### この pattern が向くケース

- 「自分にだけ見える UI / object」 (= help text, debug visualizer, ローカル mini-map)
- 「自分の頭/手に追従する object」 (= per-player の HUD、 視線 cursor)
- 「自分専用の動画 panorama 視聴球」 (= per-eye stereo viewer)
- 「自分だけ trigger できるエフェクト」 (= local-only particle, sound)

### この pattern が向かないケース

- 「全 player に同時表示」 (= 共有 prop) → sync 必要
- 「physics object として全 player に影響」 (= 投げる ball など) → VRC_Pickup + VRC_ObjectSync
- 「Master が制御して全 player が見る」 (= 状態同期) → `[UdonSynced]` + `IsOwner` チェック

### Layer 10 PlayerLocal との比較

| 案 | 利点 | 欠点 |
|---|---|---|
| transform / SetActive 非同期 | 標準 GameObject、 特別 setup 不要 | object は world に存在 (= 物理 / culling 上は影響、 ただし render は SetActive(false) で disable) |
| Layer 10 PlayerLocal | render culling で「自分の Camera だけ render」 が自動 | avatar self mesh 用の layer なので意図しない側面挙動の可能性、 Camera CullingMask 制御次第 |
| VRC_PlayerObject template | player ごとに instance 生成 (= 完全分離) | feature が重い、 setup 複雑、 player 数だけ instance 生まれる cost |

判断: 「local visibility だけ」 なら **transform / SetActive 非同期で十分**。Layer 10 や VRC_PlayerObject は「object の存在そのもの」 を player ごとに分けたい場合のみ採用。

## 落とし穴

### `[UdonSynced]` 付き field は sync する (= 例外)

通常 GameObject state は非同期だが、 UB の field に `[UdonSynced]` を付けた場合は当該 field 値が sync する。「field を sync して `OnDeserialization` で SetActive を呼ぶ」 構造を作ると、 結果として SetActive 状態が他 player にも反映される (= 意図的に sync させる場合)。

非同期挙動を期待しているのに `[UdonSynced]` を付けてしまうケースに注意。

### VRC_ObjectSync / VRC_Pickup attach で sync 化

VRC_ObjectSync component が attach されていれば transform は sync する。Inspector で VRC_ObjectSync / VRC_Pickup の有無を確認しないと、 期待した非同期挙動にならない。

### Master client 切替時に initial state に戻らない

非同期 state はあくまで「local state」、 player が join / leave しても他 player の state には影響しないが、 自分の client 内では UB instance が destroy されない限り保持される。Master 切替時にも各 player の local state はそのまま。

### Scene 全 player 共有の trap (= 同一 GameObject)

非同期挙動でも GameObject 自体は scene に 1 個。physics collision や trigger は world state を共有する。「自分の view では position A だが、 物理判定は initial position B」 のような場合、 trigger / collision は意図しない場所で起きる。

回避: 物理が絡まない pure render-only object に限定して非同期 transform を使う、 もしくは Collider を SetActive(false) と一緒に disable する。

### Editor preview と runtime 挙動の差

Unity Editor の Scene view では「scene 内 GameObject の現在 state」 が見える (= 各 player の local state ではなく editor 視点の単一 state)。Runtime に複数 client で挙動確認しないと「他 player の view でどう見えるか」 が分からない。

VRChat の `Build & Test` の `Number of Clients > 1` 設定で多 client 起動して検証推奨。

### VRC_AvatarPedestal / VRC_Mirror 等 SDK component 内部で sync するケース

一部 SDK component は内部に sync logic を持ち、 transform 操作が予期せず他 player に伝播する場合がある。SDK component を attach した GameObject の transform を非同期前提で動かす場合は事前に挙動確認。

## 関連 knowledge

- [udonsharp.md](./udonsharp.md) — UdonSharp 全般、 `[UdonSynced]` / `Networking` API
- [udonsharp-manual-sync-single-anchor.md](./udonsharp-manual-sync-single-anchor.md) — Manual sync 系
- [udonsharp-material-instance-pitfall.md](./udonsharp-material-instance-pitfall.md) — Material override の per-renderer 化
- [udonsharp-sync-mode-continuous-vs-manual.md](./udonsharp-sync-mode-continuous-vs-manual.md) — sync mode の選択
