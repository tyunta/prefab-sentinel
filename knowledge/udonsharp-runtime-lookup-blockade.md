---
tool: udonsharp-runtime-lookup-blockade
version_tested: "VRC SDK 3.10.x / UdonSharp 1.x"
last_updated: 2026-05-10
confidence: high
---

# UdonSharp Runtime Lookup Blockade — `FindObjectsOfType` is dead, but `GetPlayerObjects` + `GetComponent<T>` revives the design space

UdonSharp runtime では `FindObjectsOfType<T>` / `(T)FindObjectsOfType(typeof(T))` が user-defined U# 型に対しては **すべて blocked** で、scene-wide の lookup は不能。ただし `Networking.GetPlayerObjects(VRCPlayerApi)` で player の root GameObject 群を取得し、各 GameObject に `GetComponent<UserU#Type>()` をかけると **U# 型の component が普通に取れる**（公式 doc に明示例あり）。これで slot pool 一切不要、本来の VRCPlayerObject pattern で per-player state が運用できる。詳細は本ファイル末尾「Resolution: VRCPlayerObject + GetPlayerObjects」節を参照。

旧来の「VRCPlayerObject では per-player UdonSharp ↔ scene-fixed sphere の双方向参照ができないので master-managed slot pool に倒すしかない」という結論は **誤り**であり、本ファイル中盤の「採用したパターン: Master-Managed Slot Pool」節は歴史的経緯として残しているのみで **新規設計では採用しない**。

関連知識:

- U# 全般 (compile 制約 / sync / ownership) は [udonsharp.md](udonsharp.md) を参照。
- VRCPlayerObject template の正しい立て方は [vrchat-vrcplayerobject-template-setup.md](vrchat-vrcplayerobject-template-setup.md) を参照。
- sync mode 選択は [udonsharp-sync-mode-continuous-vs-manual.md](udonsharp-sync-mode-continuous-vs-manual.md) を参照。
- `SaveAsPrefabAsset` 経由の OBS NRE は [prefab-sentinel-saveasprefabasset-pitfalls.md](prefab-sentinel-saveasprefabasset-pitfalls.md) を参照。

## 検証で blocked と確定した API (runtime UdonSharp)

| API | 結果 | エラーメッセージ |
|---|---|---|
| `(SceneHub)FindObjectsOfType(typeof(SceneHub))` | **blocked** | `Cannot use typeof on user-defined types` |
| `FindObjectsOfType<PlayerProbe>()` (generic, no typeof literal) | **blocked** | `Method is not exposed to Udon: 'FindObjectsOfType<PlayerProbe>()'` |
| `FindObjectOfType<T>()` for user U# types (runtime) | **blocked** (推定) | troubleshooting.md にも `Inspector references` を代替として記述 |
| `GameObject.Find("name").GetComponent<UserType>()` | **通る** | が、名前依存で fragile |
| `transform.GetComponent<UserType>()` (intra-prefab, 自身/子/親) | **通る** | これだけが「lookup なし配線」の解 |

ポイント: UdonSharp の troubleshooting テーブルは「`FindObjectOfType<T>()` → Inspector references」と 1 行ずつ書いてあるが、
**この行が 「runtime には全 variant が無い」を意味する** ことを実装着手前に明示理解しておくべき。
editor scripts (例: `CustomInspector.cs`) では `FindObjectsOfType<MyScript>()` が普通に動くので、
コードベース中の用例を見ると「使えそう」と誤認しやすい。

## なぜ VRCPlayerObject + 全 player 走査は破綻するか

VRChat Worlds の典型パターン:

1. `VRCSceneDescriptor.playerObjects` に `PlayerLogic.prefab` を登録
2. プレイヤー入室で SDK が prefab を **scene root として** instantiate
3. 各 player instance は scene-fixed `Sphere.prefab` を **参照** したい

ここで:

- prefab asset 上で `[SerializeField] Sphere sphere` に scene instance を drag → Unity が **保存時に剥がす** (regular prefab は scene ref 保持不可)
- runtime で `GameObject.Find` 以外の lookup → 全部 blocked
- 各 instance は `Networking.GetOwner(gameObject)` で自分の owner は分かるが、**他プレイヤーの per-player UdonBehaviour インスタンスを列挙する手段がない**

結果として「集約計算 (= 全 player の state 走査) を sphere 側で実装する」が不可能になる。
Inspector wiring に固執するなら **設計の前提を変えるしかない**。

## 採用したパターン: Master-Managed Slot Pool

VRCPlayerObject を **使わない**。1 個の scene-fixed prefab に slot child を MaxPlayers 個ぶら下げ、master が
入退室で owner を割当てる。全参照が **intra-prefab** で完結するので Inspector wiring だけで成立する。

### 構造

```
SceneHub.prefab (scene-fixed, world に 1 個)
├─ SceneHub (UdonBehaviour, Manual Sync, master-owned)
│   ├─ public PlayerSlot[] slots = new PlayerSlot[80];
│   ├─ [UdonSynced] public int[] slotAssignments;   // -1 = 空き、playerId = 割当済
│   ├─ public override void OnPlayerJoined(VRCPlayerApi p) { ... master が AssignFreeSlot }
│   └─ public override void OnPlayerLeft(VRCPlayerApi p)   { ... master が MasterReset }
├─ Visual / Collider / Animator / TMP ラベル等
└─ Slots/ (organizer)
    ├─ Slot_00 (UdonBehaviour, Manual Sync, owner = 割当プレイヤー)
    │   ├─ public SceneHub sphere;             // 親を Inspector で wire (intra-prefab)
    │   ├─ public TMPro.TMP_Text headLabelText;       // 子 TMP を wire
    │   ├─ [UdonSynced] per-player state field 群
    │   ├─ Update() — owner 限定で state 計算 + 状態遷移
    │   ├─ LateUpdate() — slot.transform を owner.GetBonePosition(Head) に追従
    │   ├─ OnPlayerRestored(p) — owner かつ p.isLocal で PlayerData.TryGetFloat
    │   └─ OnOwnershipTransferred(p) — owner 確定で SyncTick / ReadBest 起動
    ├─ Slot_01 ... Slot_79
```

### Master 側の slot 割当

```csharp
public override void OnPlayerJoined(VRCPlayerApi player) {
    if (Networking.LocalPlayer == null || !Networking.LocalPlayer.isMaster) return;
    EnsureSlotAssignmentsArray();
    AssignFreeSlot(player);
}

void AssignFreeSlot(VRCPlayerApi player) {
    for (int i = 0; i < slots.Length; i++) {
        if (slots[i] == null) continue;
        if (slotAssignments[i] != -1) continue;
        slotAssignments[i] = player.playerId;
        Networking.SetOwner(player, slots[i].gameObject);
        if (Networking.IsOwner(gameObject)) RequestSerialization();
        return;
    }
    Debug.LogWarning("[SceneHub] 空き slot なし");
}
```

`slotAssignments[]` は **sphere 側に持つ** (master-owned)。slot 側の `[UdonSynced]` はそのプレイヤー owner が持つ run state。
責務が分かれるので race condition も少ない。

### Player leave 時の cleanup

```csharp
public override void OnPlayerLeft(VRCPlayerApi player) {
    if (!Networking.LocalPlayer.isMaster) return;
    int leavingId = player.playerId;
    for (int i = 0; i < slots.Length; i++) {
        if (slotAssignments[i] != leavingId) continue;
        // VRChat は player leave 時に該当 slot の ownership を master に auto-transfer 済み。
        // Master が新しい owner として MasterReset を呼んで synced field を初期化。
        if (!Networking.IsOwner(slots[i].gameObject))
            Networking.SetOwner(Networking.LocalPlayer, slots[i].gameObject);
        slots[i].MasterReset();
        slotAssignments[i] = -1;
    }
    if (Networking.IsOwner(gameObject)) RequestSerialization();
}
```

### Slot 側 OnOwnershipTransferred の二重受け

`OnPlayerRestored` (Persistence 復元) と `OnOwnershipTransferred` (master assign) のどちらが先に来るか保証されないので、
両方で同じ `ReadBestFromPersistence` を冪等に呼べる構造にしておく:

```csharp
public override void OnPlayerRestored(VRCPlayerApi player) {
    if (!player.isLocal || !Networking.IsOwner(gameObject) || player.isMaster) return;
    ReadBestFromPersistence(player);
}

public override void OnOwnershipTransferred(VRCPlayerApi player) {
    if (player == null || !player.isLocal) return;
    ScheduleSyncTick();
    if (!player.isMaster) ReadBestFromPersistence(player);
}
```

`PlayerData.TryGetFloat` が「PlayerData 未復元」段階では false を返すだけなので、無駄な実行コストはあっても害はない。
2 回目の発火で正しい値に置き換わる。

## なぜ「全体を 1 prefab」に収められるか

ユーザー視点では 1 prefab を world に置くだけ。VRCPlayerObject を使う要件を外すことと引き換えに:

- `VRCSceneDescriptor.playerObjects` 登録が **不要**
- 配線がすべて intra-prefab → Inspector wiring が機能、`GameObject.Find` の名前依存が消える
- Persistence は VRCPlayerObject 必須ではなく `PlayerData.SetFloat / TryGetFloat` を slot owner から呼べば等価
- per-player 同期は slot child の `[UdonSynced]` で per-owner Manual Sync (責務は同等)

トレードオフ:

- 同時人数が **prefab 内 slot 数で固定** (動的拡張不可)。上限人数を固定できる要件なら許容できる
- Master 退出時は VRChat の auto-transfer で次の master が引き継ぐ。`OnPlayerLeft` の master 判定は **新 master の視点で** 動くので継続性あり
- prefab 内 child が増える (slot 数に比例し、80 slot 規模では数百 GameObject / Component になる)。Inspector で開くと重いが runtime には影響軽微

## 関連する MCP 運用

### 80 child の prefab を 1 ショットで生成する

`editor_run_script` は temp .cs の compile が 30 秒の bridge timeout に引っかかりやすい。
**persistent MenuItem builder** を `Assets/<feature>/Editor/<Builder>.cs` に commit して、
`editor_execute_menu_item` で叩くのが正攻法 (bridge 自身がエラーメッセージで推奨している)。

```csharp
[MenuItem("Tools/SceneHub/Build Prefab")]
public static void BuildPrefab() {
    GameObject root = new GameObject("SceneHub");
    var sphere = root.AddUdonSharpComponent<SceneHub>();
    // ... slot を 80 個生成、SerializedObject 経由で sphere の slots[] に流し込む
    PrefabUtility.SaveAsPrefabAsset(root, PrefabPath);
    Object.DestroyImmediate(root);
}
```

`SerializedObject(sphere).FindProperty("slots").arraySize = 80` → `GetArrayElementAtIndex(i).objectReferenceValue = slot`
で intra-prefab 配列配線が一括で済む。

### 配線の検証

```
validate_refs(scope="Assets/<feature>/Prefabs/<root>.prefab", details=true)
inspect_wiring(asset_path="Assets/<feature>/Prefabs/<root>.prefab", udon_only=true, page_size=500)
```

期待値:
- `validate_refs.broken_count == 0`
- `inspect_wiring.null_reference_count == 0` (udon_only=true なら U# field の null だけが集計される、TMP の optional 参照ノイズは除外される)
- `duplicate_reference_count > 0` は **設計意図確認のシグナル** として活用可能 (例: 80 slot が同一 sphere を指す → 期待通り)

## まとめ

| 状況 | 採るパターン |
|---|---|
| per-player state を VRCPlayerObject で運用 | **`Networking.GetPlayerObjects(p)` + `GetComponent<U#Type>()` で sphere 側から iterate** (下記 Resolution 節) |
| 「scene 上 1 つの XX を発見して参照」(singleton) | `[SerializeField]` で intra-prefab に組み込む。lookup API は使わない |
| Inspector wiring が出来ない設計 (例: 動的生成 / 別 prefab) | `GameObject.Find(name)` 受け入れ、name を設計上の必須要件に格上げ。脆弱性として明記 |
| MCP 経由で大規模 prefab 生成 | `editor_run_script` ではなく persistent `[MenuItem]` builder + `editor_execute_menu_item` |

---

## Resolution: VRCPlayerObject + `Networking.GetPlayerObjects` + `GetComponent<U#Type>()`

公式 doc が明示する正攻法。`FindObjectsOfType<T>` が U# 型で blocked でも、
**player 単位で root GameObject を取得 → そこに紐づく component を取る** 経路は完全に開いている。

### 公式 doc が示すシグネチャ (PlayerObject)

`creators.vrchat.com/worlds/udon/persistence/player-object/` のサンプル:

```csharp
public CustomPlayerObjectScript Find(VRCPlayerApi player) {
    GameObject[] objects = Networking.GetPlayerObjects(player);
    for (int i = 0; i < objects.Length; i++) {
        if (!Utilities.IsValid(objects[i])) continue;
        CustomPlayerObjectScript foundScript = objects[i].GetComponentInChildren<CustomPlayerObjectScript>();
        if (Utilities.IsValid(foundScript)) return foundScript;
    }
    return null;
}
```

**`CustomPlayerObjectScript` は user-defined U# 型**。`GetComponentInChildren<T>()` も `GetComponent<T>()` も
U# 型に対して通る (`UdonBehaviour` 抽象型に対してだけ generic が通らないという制約はあるが、user 派生型では問題ない)。

### sphere 側 Update から全 player を走査する使い方

```csharp
PlayerProbe FindPlayerInstance(VRCPlayerApi p) {
    if (p == null || !Utilities.IsValid(p)) return null;
    GameObject[] objs = Networking.GetPlayerObjects(p);
    if (objs == null) return null;
    for (int j = 0; j < objs.Length; j++) {
        if (objs[j] == null) continue;
        PlayerProbe pl = objs[j].GetComponent<PlayerProbe>();
        if (pl != null) return pl;
    }
    return null;
}

void Update() {
    int n = VRCPlayerApi.GetPlayerCount();
    VRCPlayerApi.GetPlayers(_players); // pre-allocated buffer
    for (int i = 0; i < n; i++) {
        VRCPlayerApi p = _players[i];
        if (p == null || !Utilities.IsValid(p)) continue;
        PlayerProbe pl = FindPlayerInstance(p);
        if (pl == null) continue;
        // 集約 / per-player UI 計算
    }
}
```

呼び出しコストは「`VRCPlayerApi.GetPlayers` で N 人 → 各 player ごとに `GetPlayerObjects` (短い配列) → `GetComponent`」。
80 人想定でも 1 frame に収まる。80 player template の iteration でも 60Hz を維持できることを runtime 観察で確認済み。

### scene → player template 双方向の参照確立

scene-fixed の sphere を template から参照する素直な方法は**逆方向の push**:

```csharp
// PlayerProbe.cs (template 側、シーン参照を持てない制約)
[HideInInspector] public SceneHub sphere;  // Inspector では絶対に wire しない

// SceneHub.cs (scene-fixed)
void Update() {
    // ... iterate
    if (pl.sphere == null) pl.sphere = this;  // local 限定の push (sync しない)
}
```

template 側は scene 参照を保持できない (公式 doc:「scene components cannot reference Templates...
your scene objects must use direct references to spawned PlayerObjects instead」) が、
**ランタイム push は各クライアントローカルなだけなので OK**。`[HideInInspector]` で Inspector 操作を封じておけば事故もない。
sync 不要 (各クライアントが自分のローカル状態として保持)。

### 別経路: `Networking.FindComponentInPlayerObjects`

```csharp
Component foundComponent = Networking.FindComponentInPlayerObjects(targetPlayer, referenceChildTransform);
```

template 上の component への参照を、その target player の spawn 済 instance での同位置 component に解決する API。
**Inspector で template の特定子を予め拾っておきたい場合の橋渡し**。
sphere 側に「template 内の HeadLabel TMP の reference を Inspector で wire」しておけば、
runtime にこの API で player ごとの instance HeadLabel を解決できる。

### 何が制約として残るか

- `FindObjectsOfType<UserU#Type>()` は依然 blocked。**runtime に scene-wide な user-type lookup は無い**
- 解は「player を起点に locate する」「Inspector で intra-prefab に wire する」「name で `GameObject.Find` する」の 3 択
- master-managed slot pool は **互換性が極端に厳しい / 動的人数に追従できない** ため、今後採用しない (歴史節として残す)
- VRCPlayerObject template は **scene に 1 個実体置き** + 自動 disable される。`VRCSceneDescriptor.playerObjects[]` のような配列登録フィールドは **存在しない** (誤って探さないこと)

### 検証日とソース

- 公式 doc: https://creators.vrchat.com/worlds/udon/persistence/player-object/
- udonsharp-runtime: `Networking.GetPlayerObjects` は VRC.SDKBase 名前空間、Udon whitelist 通過済
