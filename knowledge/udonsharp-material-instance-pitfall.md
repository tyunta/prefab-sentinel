---
tool: udonsharp-material-instance-pitfall
version_tested: "VRChat World SDK 3.7+"
last_updated: 2026-05-24
confidence: high
---

# UdonSharp Material Instance Pitfall

UdonSharp で `renderer.material` access を伴う書き込みをすると、 元 `sharedMaterial` の copy が作られて renderer に attach される。copy は元 asset の static property snapshot。別 UB が runtime に asset の property (特に `mainTexture`) を変更しても instance には sync しない。動画 player 系で「shader property override したら動画が真っ白になる」 typical 症状の原因。

## 基本情報

- 対象 API: `UnityEngine.Renderer.material` (= instance 化、 unique copy 生成) / `UnityEngine.Renderer.sharedMaterial` (= global asset 直)
- 比較先 API: `UnityEngine.MaterialPropertyBlock` (= per-renderer property override、 sharedMaterial を維持しつつ property だけ override)
- 公式 docs: <https://docs.unity3d.com/2022.3/Documentation/ScriptReference/Renderer-material.html> / <https://docs.unity3d.com/2022.3/Documentation/ScriptReference/MaterialPropertyBlock.html>
- 対象 use case: shader property を runtime に renderer ごとに変えたいケース (per-player rotation / color tint / 視差 offset 等)

## 主要 API・概念

### `Renderer.material` (instance) の挙動

- getter access 時に sharedMaterial の deep copy を作成 → 新 Material instance を renderer に assign
- 以後 `renderer.material` access は同 instance を返す
- instance は元 asset の **全 property を copy** (= shader / `_MainTex` / `_Color` / float / vector / texture 含む)
- **以後の sharedMaterial 変更は instance に反映されない** (= 別 object として独立)

### `Renderer.sharedMaterial` (global asset 直)

- getter / setter は asset 参照そのもの
- 変更すると asset 自体が dirty 化、 同 asset 使用の全 renderer に影響
- editor では asset 保存される、 runtime では memory 上のみ変更

### `MaterialPropertyBlock` (per-renderer override)

- per-renderer の property override 機構、 Material asset は touch しない
- `renderer.GetPropertyBlock(block)` で current override 取得 / `renderer.SetPropertyBlock(block)` で適用
- Material の base property (= asset の `_MainTex` 等) は維持、 block の property だけが render time に上書き
- block 自体は light state (= 既存 sharedMaterial の動画 texture 等 bind を維持)
- UdonSharp whitelist: VRChat World SDK 3.7+ で `MaterialPropertyBlock` constructor / `SetFloat` / `SetVector` / `SetColor` / `Renderer.GetPropertyBlock` / `SetPropertyBlock` は使用可

## 使い分け

| やりたい事 | 使う API |
|---|---|
| 全 renderer に同じ shader property 適用 (= global) | `sharedMaterial.SetFloat(...)` (asset 直、 全 renderer に反映) |
| renderer ごとに違う shader property、 ただし `mainTexture` は asset 共通 | **`MaterialPropertyBlock` (推奨)** |
| renderer ごとに完全独立した Material 状態 (= shader 自体も別、 texture も別) | `renderer.material` で instance 化 + `material.shader = ...` で個別設定 |
| Material asset を runtime に dirty 化したくない (= editor 開発時の意図しない change 防止) | `MaterialPropertyBlock` (= asset 触らない) |

判断指針: **「per-renderer に変えたいのは property の一部だけ」 なら `MaterialPropertyBlock` が default 解**。`renderer.material` instance 化は「Material 自体を renderer ごとに分けたい」 (= shader 自体を変える等) 限定。

## 落とし穴

### `renderer.material` instance 化で sharedMaterial の動画 texture が反映されない

**症状**: 動画 player (= AVPro / VRC Video Player) が `sharedMaterial.mainTexture = videoTex` で texture bind する系で、 別 UB が `renderer.material.SetFloat(...)` で shader property 設定すると、 当該 renderer の動画 texture が消えて **白一色** で表示される。

**再現条件**:
1. 動画 player UB が asset (= sharedMaterial) の `mainTexture` を runtime に bind
2. 別 UB が `renderer.material.SetFloat("_Foo", value)` 等で property 操作 (= material instance 化)
3. instance 化のタイミング次第で、 instance には asset の **default `_MainTex` (= shader Properties の `"white" {}` fallback)** が copy される
4. instance は以後 sharedMaterial の動画 texture 更新を受け取らない
5. shader が `_MainTex` を sample → white texture → 真っ白

**回避**: `MaterialPropertyBlock` で per-renderer property override に置き換え。Material asset は touch しないため、 sharedMaterial 経由の動画 texture bind がそのまま反映される。

```csharp
private MaterialPropertyBlock propBlock;

private void SetRendererProperty(Renderer mr, string propName, float value)
{
    if (propBlock == null) propBlock = new MaterialPropertyBlock();
    mr.GetPropertyBlock(propBlock);
    propBlock.SetFloat(propName, value);
    mr.SetPropertyBlock(propBlock);
}
```

### `MaterialPropertyBlock` で texture override は限定的

- `SetTexture` も whitelist にあり可能だが、 video player の `_MainTex` を block で override すると元 asset の bind を遮断する形になる (= block の override が優先)
- 動画 texture を block 経由で bind するなら、 動画 player の bind 経路も block 経由に統一すべき
- 通常は **「動画 texture は sharedMaterial 経由」 + 「動的 property は block 経由」** の分離が clean

### `renderer.material` の instance 化は 1 度だけ

- 初回 getter access で instance 化 → 以後 access は同 instance を返す
- 「sharedMaterial に戻したい」 は通常できない (= 元 asset 参照は失われる、 別途参照保持必要)
- editor では `material` access が editor 起動中も instance 化される → editor 操作で意図せず instance 化するケースあり (= UdonSharp script editor 内で `renderer.material.SetFloat` を debug 用に書いたまま残す等)

### `renderer.material` を子 GameObject の MeshRenderer で呼ぶと親 prefab に影響しない

- instance 化された material は当該 renderer 専用、 同 prefab 内の他 renderer (= 同 asset 使用) には影響しない
- ただし scene 保存時に instance の state は保存されない (= runtime only state)、 editor では sharedMaterial に戻る

## 関連 knowledge

- [udonsharp.md](./udonsharp.md) — UdonSharp 全般 API / 制限
- [udonsharp-runtime-lookup-blockade.md](./udonsharp-runtime-lookup-blockade.md) — `FindObjectsOfType` 等の runtime 検索 API の制限
- [prefab-sentinel-material-operations.md](./prefab-sentinel-material-operations.md) — Material 操作の PrefabSentinel 経由パターン
