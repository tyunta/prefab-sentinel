---
tool: unity-prefab-packaging-position-offset
version_tested: "Unity 2022.3.22f1"
last_updated: 2026-05-24
confidence: high
---

# Unity Prefab Packaging Position Offset

シーン内 GameObject を非ゼロの world position に置いた状態で Prefab を保存すると、保存される prefab の root Transform は **負の補正** を local position に持ち、子 Transform はそれを相殺する **正のオフセット** を持つ。別シーンに instantiate して root position の override で 0 に戻すと、子達は補正ぶんずれた world position に出現する。

## 基本情報

- 対象 API: `PrefabUtility.SaveAsPrefabAsset` / Hierarchy 右クリック「Create > Prefab」/ Hierarchy ドラッグ→ Project View
- 観測対象: 保存される `.prefab` 内の root `Transform.m_LocalPosition` と children の `m_LocalPosition`
- 影響範囲: Prefab を別シーンや別文脈で instantiate する全ワークフロー
- Unity 公式ドキュメント: <https://docs.unity3d.com/Manual/CreatingPrefabs.html> (Position behavior は明示されないが Hierarchy → Project drag の挙動として確認可能)

## 主要 API・概念

### Packaging 時の Position 焼き込み

「Save as Prefab」を実行する瞬間、Unity は

1. 元の GameObject の **world position** を保持したまま prefab を生成する。
2. Prefab 内の root Transform の `m_LocalPosition` は、Hierarchy parent が無い前提で `world position` がそのまま入る。
3. ただし、prefab を別シーンに instantiate した時に「すべての子の world position が保持される」よう、prefab 内の各子 Transform は `parent.localPosition + child.localPosition = child.original_world_position` が成立するようにシフトされる。

具体的に root が world `(0, Y_pkg, 0)` で保存された場合:

| Transform | Prefab 内 `m_LocalPosition` |
|-----------|----------------------------|
| Root | `(0, Y_pkg, 0)` (直接入る) |
| Child A (元の world `(x_a, y_a, z_a)`) | `(x_a, y_a - 0, z_a)` (root の world は 0 と仮定された相対) |

ただし環境によっては (特に Hierarchy 内で root の親 GameObject が `(0, -Y_pkg, 0)` を持つ階層に root が居た場合)、root に **`(0, -Y_pkg, 0)`** が入り、各子に **`+Y_pkg` 加算** される変則パターンも観測される。後者は **Sub-Hierarchy from Prefab** (`PrefabUtility.SaveAsPrefabAssetAndConnect` 系) 経由でしばしば発生する。

### 別シーン instantiate 時の Position 解決

PrefabInstance を別シーンに作成し、`m_LocalPosition.y` を override で `0` にすると:

```
新 world Y of root = 0 (override)
新 world Y of child = 0 (root) + child.m_LocalPosition.y (prefab default)
                    = child.m_LocalPosition.y
```

Prefab 内で子の local Y に `+Y_pkg` が焼き込まれていれば、子は world Y = `Y_pkg` に出現する。**子の override を入れていなければ補正は効かない**。

## 使い分け

### 「Save as Prefab」前のチェックリスト

| やる | やらない |
|------|---------|
| 対象 GameObject を world `(0, 0, 0)` に移動してから保存 | 非ゼロ位置でそのまま保存 |
| 保存後、prefab を新しい空シーンに instantiate し world position を inspect 確認 | 元シーンでの見た目だけで確認完了とする |
| 保存後、prefab の YAML を grep し `m_LocalPosition` のずれを確認 | YAML を見ない |

### Packaging 後に offset 焼き込みが発覚した場合の修復

**Option A: Unpack + 子 Position 再計算 (推奨)**

```csharp
var go = GameObject.Find("/Target/PrefabInstance");
PrefabUtility.UnpackPrefabInstance(go, PrefabUnpackMode.Completely, InteractionMode.AutomatedAction);
// unpack 後、各子の localPosition.y を -= Y_pkg で補正
foreach (Transform child in go.transform) {
    var lp = child.localPosition;
    if (lp.y > 大きな閾値) {
        child.localPosition = new Vector3(lp.x, lp.y - Y_pkg, lp.z);
    }
}
```

Unpack すると Prefab connection は失われるが、Position 整合が取れる。元 prefab を新しく作り直すコストと比較して判断。

**Option B: 各子の `m_LocalPosition` を PrefabInstance override で個別設定**

```csharp
var go = GameObject.Find("/Target/PrefabInstance/Child");
go.transform.localPosition = correct_pos;  // override を作る
EditorUtility.SetDirty(go);
```

Prefab connection を維持できるが、override が大量に増える。Prefab を再 instantiate する度に手動補正が必要。

**Option C: 元シーンに戻って (0,0,0) で再 packaging**

最も clean だが、元シーンが残っている場合のみ。

### Option 選択基準

| 状況 | 推奨 |
|------|------|
| Prefab を 1 つのシーンでしか使わない | Option A (Unpack) |
| 同 prefab を多シーンで使い、子の Position が固定 | Option B (override) |
| 元シーンが存在し再 packaging できる | Option C (再 packaging) |

## 落とし穴

### Validator が runtime でなく static position を見る

VRChat World 系の content validator (公式 SDK + 各種 third-party) は asset を静的に走査するため、Runtime に script が `SetPositionAndRotation` で再配置する GameObject でも、**Edit-mode の static position が validation 対象**。Position 焼き込みのオフセットによって範囲外 / 境界外と判定される事例あり。

回避: GameObject の static position は常に validation 通過位置に置き、Runtime の再配置に依存しない。

### MeshRenderer.bounds が下限を割る

子が world Y=0 ちょうど + MeshRenderer.extents Y=N の組合せで、bounds Y = `[-N, +N]` になり、配置許容範囲が Y∈[0, max] のような下限 0 を持つ環境ではその下限を割る。Position 焼き込み問題と組み合わさると、子の Y がずれた先で bounds 違反になる。

回避: extents を持つ MeshRenderer は world Y ≥ extents.y となる位置に置く (extents 分の余裕)。

### `PrefabUtility.UnpackPrefabInstance` の `InteractionMode`

`InteractionMode.UserAction` は Undo を生成し dialogue を出す。Editor script から呼ぶ場合は `InteractionMode.AutomatedAction` を使う (Undo 生成のみ、dialogue なし)。

```csharp
PrefabUtility.UnpackPrefabInstance(go, PrefabUnpackMode.Completely, InteractionMode.AutomatedAction);
```

`PrefabUnpackMode`:
- `Completely`: ネストした PrefabInstance も全部 unpack
- `OutermostRoot`: 最外殻のみ unpack (内側 nested prefab は残る)

## 検出パターン

Position 焼き込みの兆候を grep で検出:

```bash
# Prefab YAML 内で root Transform の極端な local position を grep
grep -B 2 "m_LocalPosition: {x: 0, y: -[0-9]\{3\}" path/to/prefab.prefab
# 子 Transform で +500 系の値を grep
grep "m_LocalPosition: {x: [0-9.-]*, y: [4-9][0-9][0-9]" path/to/prefab.prefab
```

または Editor script で audit:

```csharp
// 各 PrefabInstance 子の world position vs local position 比較
foreach (var go in scene.GetRootGameObjects()) {
    foreach (var t in go.GetComponentsInChildren<Transform>(true)) {
        if (Mathf.Abs(t.position.y) > 100 && Mathf.Abs(t.parent?.position.y ?? 0) < 1) {
            Debug.LogWarning($"{t.name}: world.y={t.position.y}, parent.y={t.parent?.position.y}");
        }
    }
}
```

## 関連 knowledge

- [prefab-sentinel-saveasprefabasset-pitfalls.md](./prefab-sentinel-saveasprefabasset-pitfalls.md) — `SaveAsPrefabAsset` の component strip 系 pitfall
- [prefab-sentinel-build-from-scratch.md](./prefab-sentinel-build-from-scratch.md) — 大規模 prefab 構築フロー
