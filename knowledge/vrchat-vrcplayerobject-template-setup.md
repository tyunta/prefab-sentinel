---
tool: vrchat-vrcplayerobject-template-setup
version_tested: "VRC SDK 3.10.x / UdonSharp 1.x"
last_updated: 2026-05-10
confidence: high
---

# VRCPlayerObject Template の正しい立て方

VRChat Worlds で「per-player な UdonSharp state」を持たせる正攻法。**Component を template の root に付けて、template prefab の実体をシーンに 1 個置く**だけで、VRChat が入室プレイヤーごとに自動で複製・spawn する。

関連知識:

- U# 型を sphere 側から iterate するパターンは [udonsharp-runtime-lookup-blockade.md](udonsharp-runtime-lookup-blockade.md) の Resolution 節を参照。
- 永続化 (PlayerData) は [udonsharp.md](udonsharp.md) を参照。

## 公式仕様 (簡約)

ソース: https://creators.vrchat.com/worlds/udon/persistence/player-object/

- Template の作り方: **シーンに GameObject を置き、`VRCPlayerObject` コンポーネントを付ける**。それだけで template として認識される
- 任意で root か子に `UdonBehaviour`/UdonSharpBehaviour を付ける
- 永続化したい synced 変数があるなら `VRCEnablePersistence` を **UdonBehaviour と同じ GameObject に** 付ける
- VRChat は spawn 後に template 自体を automatically disable する。ただし scene には残る (delete してはいけない)
- Template は自身の component や子、シーン上の他オブジェクトを参照可。**逆方向 (scene → template) は不可**。シーン側は spawn された PlayerObject の instance 参照を runtime に取得して使う

## よくある誤解

### `VRCSceneDescriptor.playerObjects[]` は **存在しない**

過去のチュートリアルや古い記憶で「シーンルートの VRC_SceneDescriptor に playerObjects 配列があってそこに prefab を register する」と
信じ込んでしまうことがあるが、**そのフィールドは VRChat World SDK 3.x には存在しない**。
YAML 検査 (`mcp__plugin_prefab-sentinel_prefab-sentinel__inspect_hierarchy`) で
`VRCSceneDescriptor` の serialized fields を見ても該当配列は出てこない。

正しいのは「template prefab を **シーンに instance として置く**」だけ。register 操作は不要。

### Template prefab を `Assets/.../Prefabs/` に作っただけでは spawn されない

prefab asset を作っても、シーンに instance として置かなければ VRChat はそれを認識しない。
ビルド時にシーン中にある `VRCPlayerObject` コンポーネントを持つ GameObject を全列挙して template とする方式。

→ **対応**: `editor_instantiate(asset_path="Assets/.../PlayerProbe.prefab")` でシーンに置き、
`editor_save_scene` で保存。`editor_list_roots` で確認すると 4 root (VRCWorld / Floor / SceneHub / **PlayerProbe**) になる。

### Template に scene 参照を `[SerializeField]` で持たせると保存時に剥がされる

prefab asset は scene instance への参照を保持できない (GUID と fileID が解決不能)。
`SaveAsPrefabAsset` 直後の YAML を見ると当該フィールドが `{fileID: 0}` になる。

→ **対応**:
- 子側 (template 内) に置く参照は intra-prefab で OK (Inspector wire で保存される)
- scene-fixed 側 (sphere) への参照は `[HideInInspector]` + ランタイム push (scene 側の Update で代入)

## 最小実装

### 1. Template prefab の Builder (Editor)

```csharp
using VRC.SDK3.Components;  // VRCPlayerObject が居る namespace

static void BuildPlayerPrefab(TMP_FontAsset fontAsset)
{
    GameObject root = new GameObject("PlayerProbe");

    // U# logic (位置計算 / sync field / per-player UI)
    var player = root.AddUdonSharpComponent<PlayerProbe>();

    // Template マーカー: これを付けないと VRChat に認識されず spawn されない
    root.AddComponent<VRCPlayerObject>();

    // 子参照 (intra-prefab、Inspector wire 可)
    GameObject headLabel = new GameObject("HeadLabel");
    headLabel.transform.SetParent(root.transform, false);
    var headTmp = headLabel.AddComponent<TextMeshPro>();
    if (fontAsset != null) headTmp.font = fontAsset;
    headTmp.fontSize = 0.5f;
    headTmp.alignment = TextAlignmentOptions.Center;
    headLabel.SetActive(false);

    // intra-prefab wire
    var playerSO = new SerializedObject(player);
    playerSO.FindProperty("headLabelText").objectReferenceValue = headTmp;
    playerSO.ApplyModifiedPropertiesWithoutUndo();

    PrefabUtility.SaveAsPrefabAsset(root, PlayerPrefabPath);
    Object.DestroyImmediate(root);
}
```

### 2. Template の U# class (sync 設定)

```csharp
[UdonBehaviourSyncMode(BehaviourSyncMode.Continuous)]  // または Manual。後述
public class PlayerProbe : UdonSharpBehaviour
{
    // intra-prefab 参照 (Inspector wire 済)
    public TMP_Text headLabelText;

    // scene-fixed sphere への参照 (template prefab には保存されない)
    [HideInInspector] public SceneHub sphere;

    // synced state (owner = この template が割り当てられたプレイヤー)
    [UdonSynced] public float liveValue;
    [UdonSynced] public bool isActive;
    // ...

    public override void OnPlayerRestored(VRCPlayerApi player) { /* PlayerData 復元 */ }
}
```

### 3. Scene 側からの iterate

```csharp
void Update() {
    int n = VRCPlayerApi.GetPlayerCount();
    VRCPlayerApi.GetPlayers(_players);
    for (int i = 0; i < n; i++) {
        VRCPlayerApi p = _players[i];
        if (p == null || !Utilities.IsValid(p)) continue;
        GameObject[] objs = Networking.GetPlayerObjects(p);
        if (objs == null) continue;
        for (int j = 0; j < objs.Length; j++) {
            if (objs[j] == null) continue;
            PlayerProbe pl = objs[j].GetComponent<PlayerProbe>();
            if (pl == null) continue;

            // sphere ref をローカル push (sync しない)
            if (pl.sphere == null) pl.sphere = this;

            // 集約処理 / UI 更新
            // ...
        }
    }
}
```

## 確認チェックリスト (build 直前)

- [ ] template prefab の root に `VRCPlayerObject` コンポーネントが付いている (`inspect_hierarchy --expand_monobehaviour`)
- [ ] template prefab の **instance がシーンルートに 1 個ある** (`editor_list_roots` で確認)
- [ ] template の U# class が `BehaviourSyncMode.Continuous` または `Manual` (後述の選択基準)
- [ ] template 内の参照は `[HideInInspector]` で Inspector からは触れないようにする (誤った scene wiring 事故を防ぐ)
- [ ] PlayerData を使うなら `VRCEnablePersistence` も同じ GameObject に付ける

## 起動 5 秒経って 1 個も template instance が見つからない場合の診断

scene 側 Update 内で `Networking.GetPlayerObjects(p)` の結果を集計し、5 秒間 0 個なら警告を出す:

```csharp
if (!_warnedNoPlayers && foundPlayers == 0 && (Time.realtimeSinceStartup - _startedAt) > 5f) {
    _warnedNoPlayers = true;
    Debug.LogWarning("[Feature] 5 秒経過したが <Template>U# instance が 1 個も見つからない。" +
                     "シーンに <Template>.prefab の instance が置かれているか確認してください。");
}
```

これが鳴る = template instance がシーンに置かれていない。`editor_instantiate` で復旧する。

## Sources

- 公式: https://creators.vrchat.com/worlds/udon/persistence/player-object/
- 公式リポジトリ: https://github.com/vrchat-community/creator-docs/blob/main/Docs/docs/worlds/udon/persistence/player-object.md
