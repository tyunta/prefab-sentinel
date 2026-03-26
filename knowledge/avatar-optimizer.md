---
tool: avatar-optimizer
version_tested: "1.9.8"
last_updated: 2026-03-26
confidence: medium
---

# AAO: Avatar Optimizer

## 概要 (L1)

非破壊アバター最適化ツール。NDMF (Non-Destructive Modular Framework) プラグインとして動作し、ビルド時にメッシュ統合・ブレンドシェイプ固定・不要ボーン削除・PhysBone 統合・テクスチャ縮小等の最適化を自動実行する。最終アバターにはランタイムコンポーネントが残らない（`IEditorOnly` 実装）。

**解決する問題**: VRChat アバターのパフォーマンスランク改善。手動でのメッシュ統合やブレンドシェイプ削除は不可逆で管理が困難だが、AAO は非破壊で適用・解除できる。

**NDMF との関係**: AAO は NDMF >= 1.8.0 に依存。NDMF のビルドパイプライン内で Resolving → Transforming → Optimizing の各フェーズに Pass を登録して実行する。

**コンポーネント階層**: 全コンポーネントは `AvatarTagComponent` (MonoBehaviour) を基底とする。メッシュ操作系は `EditSkinnedMeshComponent` (RequireComponent: Renderer)、アバター全体設定は `AvatarGlobalComponent` を基底とする。

**PrefabSafeSet / PrefabSafeMap**: 多くのコンポーネントは Unity の Prefab Variant でも安全に動作するよう、独自のシリアライズ構造 `PrefabSafeSet<T>` / `PrefabSafeMap<K,V>` を使用する。Inspector 上はリスト/セットに見えるが、propertyPath 構造が通常の配列と異なる。

**プラットフォーム**: VRChat SDK 3.7.0 以上。1.9.0 から非 VRC プラットフォームの実験的サポートあり。

**最新バージョン**: 1.9.8 (2026-03-17)。VPM リポジトリ `https://vpm.anatawa12.com/vpm.json` から配布。

## コンポーネント一覧 (L1→L2)

### 全体最適化

| コンポーネント名 | クラス名 | 用途 | 配置対象 |
|---|---|---|---|
| AAO Trace And Optimize | `TraceAndOptimize` | アバター全体の自動最適化（ブレンドシェイプ固定、未使用オブジェクト除去、PhysBone 最適化、アニメーター最適化、メッシュ統合、テクスチャ最適化） | アバタールート (DisallowMultipleComponent) |

Trace And Optimize はアバタールートに 1 つ配置するだけで動作し、内部でアニメーション解析・トレースを行い、安全に最適化できる箇所を自動判定する。手動コンポーネントより広範な最適化を自動で行うが、個別コンポーネントで明示指定した最適化と組み合わせても動作する。

### メッシュ操作 (EditSkinnedMeshComponent)

Renderer (SkinnedMeshRenderer / MeshRenderer) が付いた GameObject に配置する。

| コンポーネント名 | クラス名 | 用途 | 複数配置 |
|---|---|---|---|
| AAO Merge Skinned Mesh | `MergeSkinnedMesh` | 複数の SkinnedMeshRenderer / MeshRenderer を 1 つに統合 | 不可 |
| AAO Freeze BlendShapes | `FreezeBlendShape` | 指定ブレンドシェイプをビルド時に固定値で焼き込み | 不可 |
| AAO Remove Mesh By BlendShape | `RemoveMeshByBlendShape` | ブレンドシェイプで動く頂点のポリゴンを除去 | 可 (1.8.0+) |
| AAO Remove Mesh By Box | `RemoveMeshInBox` | バウンディングボックス内のポリゴンを除去 | 可 (1.8.0+) |
| AAO Remove Mesh By Mask | `RemoveMeshByMask` | マスクテクスチャで指定した領域のポリゴンを除去 | 不可 |
| AAO Remove Mesh By UV Tile | `RemoveMeshByUVTile` | UV タイル単位でポリゴンを除去 | 不可 |
| AAO Merge Toon Lit Material | `MergeToonLitMaterial` | ToonLit マテリアルのアトラス化 | 不可 |
| AAO Merge Material | `MergeMaterial` | 汎用マテリアルアトラス化 (1.9.0+) | 不可 |
| AAO Rename BlendShape | `RenameBlendShape` | ブレンドシェイプ名のリネーム | 不可 |

### ボーン操作

| コンポーネント名 | クラス名 | 用途 | 配置対象 |
|---|---|---|---|
| AAO Merge Bone | `MergeBone` | ボーンを親に統合（ウェイト再計算） | 統合するボーン |

### PhysBone 操作 (VRCSDK 依存)

| コンポーネント名 | クラス名 | 用途 | 配置対象 |
|---|---|---|---|
| AAO Merge PhysBone | `MergePhysBone` | 複数の VRCPhysBone を 1 つに統合 | 任意の GameObject |
| AAO Clear Endpoint Position | `ClearEndpointPosition` | PhysBone の Endpoint Position をクリア | VRCPhysBone と同じ GameObject |
| AAO Replace End Bone With Endpoint Position | `ReplaceEndBoneWithEndpointPosition` | 末端ボーンを Endpoint Position に置換 (1.9.0+) | VRCPhysBone と同じ GameObject |

### テクスチャ操作

| コンポーネント名 | クラス名 | 用途 | 配置対象 |
|---|---|---|---|
| AAO Max Texture Size | `MaxTextureSize` | 子孫のテクスチャ最大サイズを制限（mipmap 利用） (1.9.0+) | 任意の GameObject |

### その他

| コンポーネント名 | クラス名 | 用途 | 配置対象 |
|---|---|---|---|
| AAO Remove Zero Sized Polygon | `RemoveZeroSizedPolygon` | 面積ゼロのポリゴンを除去 | SkinnedMeshRenderer と同じ GameObject |
| AAO Make Children | `MakeChildren` | 指定オブジェクトを子に移動 | 任意の GameObject |
| AAO UnusedBonesByReferencesTool | `UnusedBonesByReferencesTool` | 未使用ボーン除去 [Obsolete: Trace And Optimize に統合済み] | アバタールート |

### レガシー（自動削除）

| コンポーネント名 | クラス名 | 用途 |
|---|---|---|
| Activator | `Activator` | 旧 ApplyOnPlay 用。検出されると自動削除 |
| GlobalActivator | `GlobalActivator` | 旧 ApplyOnPlay 用。検出されると自動削除 |
| AvatarActivator | `AvatarActivator` | 旧 ApplyOnPlay 用。検出されると自動削除 |

## 操作パターン (L2)

### 基本最適化パターン（Trace And Optimize のみ）

1. アバタールートに **Trace And Optimize** コンポーネントを 1 つ配置
2. デフォルト設定で以下が自動実行される:
   - 未使用ブレンドシェイプの固定 (`optimizeBlendShape`)
   - 未使用オブジェクト・ボーンの除去 (`removeUnusedObjects`)
   - PhysBone の最適化・自動統合 (`optimizePhysBone`)
   - アニメーターレイヤーの最適化 (`optimizeAnimator`)
   - SkinnedMesh の自動統合 (`mergeSkinnedMesh`)
   - テクスチャの自動最適化 (`optimizeTexture`)
3. MMD ワールド互換性はデフォルトで有効 (`mmdWorldCompatibility`)

### メッシュ統合パターン

1. 統合先の SkinnedMeshRenderer がある GameObject に **Merge Skinned Mesh** を配置
2. `renderersSet` に統合元の SkinnedMeshRenderer を追加
3. `staticRenderersSet` に統合元の MeshRenderer を追加（静的メッシュの場合）
4. `removeEmptyRendererObject` (デフォルト: true) で統合元の空 GameObject を自動削除
5. `blendShapeMode` で同名ブレンドシェイプの扱いを選択（RenameToAvoidConflict がデフォルト）

### 衣装メッシュ削除パターン（貫通防止）

1. 体メッシュの SkinnedMeshRenderer がある GameObject に **Remove Mesh By BlendShape** を配置
2. `shapeKeysSet` に衣装で隠れる部分のシュリンクブレンドシェイプ名を追加
3. ビルド時にそのブレンドシェイプで動く頂点のポリゴンが物理的に除去される
4. `tolerance` (デフォルト: 0.001) で頂点移動量の閾値を調整
5. `invertSelection` で選択を反転可能（指定ブレンドシェイプで動かない部分を除去）

### PhysBone 統合パターン

1. 任意の GameObject に **Merge PhysBone** を配置
2. `componentsSet` に統合したい VRCPhysBone コンポーネントを追加
3. 各パラメーター（Pull, Spring, Stiffness 等）の override/copy を設定
4. `makeParent` で統合先を親に設定するか選択
5. ビルド時に複数 PhysBone が 1 つに統合される

### テクスチャサイズ制限パターン（Android 対応）

1. アバタールートまたは制限したいオブジェクトに **Max Texture Size** を配置
2. `maxTextureSize` で上限を設定（デフォルト: 2048）
3. ビルド時に子孫のテクスチャが mipmap を利用して縮小される（再圧縮不要で高速）

## SerializedProperty リファレンス (L3)

ソースバージョン: 1.9.8 (Shiratsume) + 1.8.10 (UnityTool_sample) 差分検証済み
検証方法: .cs ソースコード読み + .meta ファイルの GUID 抽出（inspect 実測なし → confidence: medium）

### Script GUID テーブル

| コンポーネント | GUID | 備考 |
|---|---|---|
| TraceAndOptimize | `8ad67726f6714ccbbb27913837ce7b15` | AvatarGlobalComponent |
| MergeSkinnedMesh | `d95379eb5690423ebd102a3902be341b` | EditSkinnedMeshComponent |
| FreezeBlendShape | `0556b75ec8ef4868ab20ce8404c1edae` | EditSkinnedMeshComponent |
| RemoveMeshByBlendShape | `61169fc9b8aa6df4291a9fe95d961ec2` | EditSkinnedMeshComponent |
| RemoveMeshInBox | `a9fd0617dd174314b0a375fb2188510c` | EditSkinnedMeshComponent |
| RemoveMeshByMask | `92c51c31bb4e473d896f4f0e824d35b2` | EditSkinnedMeshComponent |
| RemoveMeshByUVTile | `9c28afa2e4d445a5b6c8beffa8daa0e2` | EditSkinnedMeshComponent |
| MergeToonLitMaterial | `885785ecaa724d6d8bb45dd0d62241f7` | EditSkinnedMeshComponent |
| MergeMaterial | `9c47a2477e3743d48a58cab026e1416d` | EditSkinnedMeshComponent, 1.9.0+ |
| RenameBlendShape | `71ba6c5e4332471bb8048f7890eaab73` | EditSkinnedMeshComponent |
| MergeBone | `1d42113ec3c34311b1548e7f0cbf46f2` | AvatarTagComponent, internal |
| MergePhysBone | `2650884bd6834672915418cf56ffbfde` | AvatarTagComponent, VRCSDK 依存 |
| ClearEndpointPosition | `63d518a37a53491c80d63a7f46a178af` | AvatarTagComponent, VRCSDK 依存 |
| ReplaceEndBoneWithEndpointPosition | `9ce1c76e60ae89645b7a36efc489cdba` | AvatarTagComponent, VRCSDK 依存, 1.9.0+ |
| MaxTextureSize | `acf2b2f991ee840d2a8ef37261ff8291` | AvatarTagComponent, 1.9.0+ |
| RemoveZeroSizedPolygon | `b87714a042f7485184e185edd88fec6c` | AvatarTagComponent, マーカー (フィールドなし) |
| MakeChildren | `e669ae62d2734783847b8ab34e452617` | AvatarTagComponent |
| UnusedBonesByReferencesTool | `7b270464bef14d6dbb216759c19a83a6` | AvatarGlobalComponent, [Obsolete] |
| TraceAndOptimizePlatformSettings | `5b939ba61a614579a09f92f8eec938cd` | ScriptableObject, Editor only, 1.9.0+ |

### コンポーネント別フィールド

#### Trace And Optimize

全フィールドが `internal` (スクリプト API なし、Inspector からのみ設定)。

| propertyPath | 型 | 説明 |
|---|---|---|
| `optimizeBlendShape` | bool | ブレンドシェイプ最適化 (デフォルト: true) [FormerlySerializedAs("freezeBlendShape")] |
| `removeUnusedObjects` | bool | 未使用オブジェクト除去 (デフォルト: true) |
| `preserveEndBone` | bool | 末端ボーンの保持 (デフォルト: false) |
| `removeZeroSizedPolygons` | bool | ゼロサイズポリゴン除去 (デフォルト: false, Advanced) |
| `optimizePhysBone` | bool | PhysBone 最適化 (デフォルト: true, VRCSDK 依存) |
| `optimizeAnimator` | bool | アニメーター最適化 (デフォルト: true) |
| `mergeSkinnedMesh` | bool | SkinnedMesh 自動統合 (デフォルト: true) |
| `allowShuffleMaterialSlots` | bool | マテリアルスロット順序の変更許可 (デフォルト: true) |
| `optimizeTexture` | bool | テクスチャ最適化 (デフォルト: true) |
| `mmdWorldCompatibility` | bool | MMD ワールド互換 (デフォルト: true) |
| `debugOptions` | DebugOptions | デバッグ用構造体 (内部専用、安定性保証なし) |

**DebugOptions** (内部構造体、保存形式の安定性保証なし):
- `debugOptions.exclusions`: GameObject[] -- T&O 除外対象
- `debugOptions.gcDebug`: InternalGcDebugPosition -- GC デバッグ位置
- `debugOptions.noSweepComponents` ... `debugOptions.skipCompleteGraphToEntryExit`: 各種最適化パスの個別スキップフラグ

#### Merge Skinned Mesh

| propertyPath | 型 | 説明 |
|---|---|---|
| `renderersSet` | PrefabSafeSet\<SkinnedMeshRenderer\> | 統合元 SkinnedMeshRenderer のセット |
| `staticRenderersSet` | PrefabSafeSet\<MeshRenderer\> | 統合元 MeshRenderer のセット |
| `doNotMergeMaterials` | PrefabSafeSet\<Material\> | 統合しないマテリアルのセット |
| `removeEmptyRendererObject` | bool | 統合元の空 GameObject を除去 (デフォルト: true) |
| `skipEnablementMismatchedRenderers` | bool | 有効/無効が異なるレンダラーをスキップ (デフォルト: false, v1 では true) |
| `copyEnablementAnimation` | bool | 有効/無効アニメーションをコピー |
| `blendShapeMode` | BlendShapeMode | MergeSameName=0, RenameToAvoidConflict=1, TraditionalCompability=2 |

#### Freeze BlendShape

| propertyPath | 型 | 説明 |
|---|---|---|
| `shapeKeysSet` | PrefabSafeSet\<string\> | 固定するブレンドシェイプ名のセット |

#### Remove Mesh By BlendShape

| propertyPath | 型 | 説明 |
|---|---|---|
| `shapeKeysSet` | PrefabSafeSet\<string\> | 対象ブレンドシェイプ名のセット |
| `tolerance` | double | 頂点移動量閾値 (デフォルト: 0.001) |
| `invertSelection` | bool | 選択反転 (デフォルト: false, 1.9.0+) |

#### Remove Mesh In Box

| propertyPath | 型 | 説明 |
|---|---|---|
| `boxes` | BoundingBox[] | バウンディングボックス配列 |
| `boxes.Array.data[n].center` | Vector3 | ボックス中心 |
| `boxes.Array.data[n].size` | Vector3 | ボックスサイズ |
| `boxes.Array.data[n].rotation` | Quaternion | ボックス回転 |
| `removeInBox` | bool | true: ボックス内を除去, false: ボックス外を除去 (デフォルト: true) |

#### Remove Mesh By Mask

| propertyPath | 型 | 説明 |
|---|---|---|
| `materials` | MaterialSlot[] | マテリアルスロットごとのマスク設定 |
| `materials.Array.data[n].enabled` | bool | このスロットを有効にするか |
| `materials.Array.data[n].mask` | Texture2D | マスクテクスチャ |
| `materials.Array.data[n].mode` | RemoveMode | RemoveBlack=0, RemoveWhite=1 |

#### Remove Mesh By UV Tile

| propertyPath | 型 | 説明 |
|---|---|---|
| `materials` | MaterialSlot[] | マテリアルスロットごとのタイル除去設定 |
| `materials.Array.data[n].removeTile0` ... `removeTile15` | bool | UV タイル 0-15 の除去フラグ (4x4 グリッド) |
| `materials.Array.data[n].uvChannel` | UVChannel | TexCoord0-7 |

#### Merge Bone

| propertyPath | 型 | 説明 |
|---|---|---|
| `avoidNameConflict` | bool | 名前衝突回避 (デフォルト: true on Reset) |

#### Merge PhysBone

PhysBone の各パラメーターに対して `override` (bool) と `value` のペア構造。

| propertyPath | 型 | 説明 |
|---|---|---|
| `makeParent` | bool | 統合先を親にする |
| `componentsSet` | PrefabSafeSet\<VRCPhysBoneBase\> | 統合する PhysBone のセット |
| `versionConfig.override` / `.value` | bool / VRCPhysBoneBase.Version | バージョン設定 |
| `endpointPositionConfig.override` | EndPointPositionConfigStruct.Override | Clear=0, Copy=1, Override=2 |
| `endpointPositionConfig.value` | Vector3 | Override 時の値 |
| `integrationTypeConfig.override` / `.value` | bool / VRCPhysBoneBase.IntegrationType | 積分タイプ |
| `pullConfig.override` / `.value` / `.curve` | bool / float / AnimationCurve | Pull [0-1] |
| `springConfig` | CurveFloatConfig | Spring (Momentum) [0-1] |
| `stiffnessConfig` | CurveFloatConfig | Stiffness [0-1] |
| `gravityConfig` | CurveFloatConfig | Gravity [-1, 1] |
| `gravityFalloffConfig` | CurveFloatConfig | Gravity Falloff [0-1] |
| `immobileTypeConfig.override` / `.value` | bool / VRCPhysBoneBase.ImmobileType | Immobile タイプ |
| `immobileConfig` | CurveFloatConfig | Immobile [0-1] |
| `limitTypeConfig.override` / `.value` | bool / VRCPhysBoneBase.LimitType | Limit タイプ |
| `maxAngleXConfig` | CurveFloatConfig | Max Angle X [0-180] |
| `maxAngleZConfig` | CurveFloatConfig | Max Angle Z [0-90] |
| `limitRotationConfig.override` | LimitRotationConfigStruct.Override | Copy=0, Override=1, Fix=2 |
| `radiusConfig` | CurveFloatConfig | Collision Radius |
| `allowCollisionConfig.override` / `.value` / `.filter` | bool / AdvancedBool / PermissionFilter | Collision 許可 |
| `collidersConfig.override` | CollidersConfigStruct.Override | Copy=0, Override=1, Merge=2 |
| `collidersConfig.value` | List\<VRCPhysBoneColliderBase\> | コライダーリスト |
| `stretchMotionConfig` | CurveFloatConfig | Stretch Motion [0-1] |
| `maxStretchConfig` | CurveFloatConfig | Max Stretch |
| `maxSquishConfig` | CurveFloatConfig | Max Squish [0-1] |
| `allowGrabbingConfig` | PermissionConfig | Grabbing 許可 |
| `allowPosingConfig` | PermissionConfig | Posing 許可 |
| `grabMovementConfig.override` / `.value` | bool / float | Grab Movement [0-1] |
| `snapToHandConfig.override` / `.value` | bool / bool | Snap to Hand |
| `parameterConfig.value` | string | パラメーター名 (ValueOnly) |
| `isAnimatedConfig.value` | bool | IsAnimated (ValueOnly) |
| `resetWhenDisabledConfig.override` / `.value` | bool / bool | 無効化時リセット |

#### Clear Endpoint Position

マーカーコンポーネント (フィールドなし)。VRCPhysBone と同じ GameObject に配置。

#### Replace End Bone With Endpoint Position (1.9.0+)

| propertyPath | 型 | 説明 |
|---|---|---|
| `kind` | ReplaceEndBoneWithEndpointPositionKind | Average=0, Override=1 |
| `overridePosition` | Vector3 | Override 時の位置 |

#### Max Texture Size (1.9.0+)

| propertyPath | 型 | 説明 |
|---|---|---|
| `maxTextureSize` | MaxTextureSizeValue | Max4096=4096, Max2048=2048, Max1024=1024, Max512=512, Max256=256, Max128=128, Max64=64 |

#### Merge Material (1.9.0+)

| propertyPath | 型 | 説明 |
|---|---|---|
| `merges` | MergeInfo[] | マージ設定配列 |
| `merges.Array.data[n].referenceMaterial` | Material | 参照マテリアル |
| `merges.Array.data[n].mergedFormat` | MergedTextureFormat | テクスチャフォーマット (Default=0, DXT5, ASTC_6x6 等) |
| `merges.Array.data[n].textureSize` | Vector2Int | テクスチャサイズ (デフォルト: 1024x1024) |
| `merges.Array.data[n].source` | MergeSource[] | ソース配列 |
| `merges.Array.data[n].source.Array.data[m].material` | Material | ソースマテリアル |
| `merges.Array.data[n].source.Array.data[m].targetRect` | Rect | アトラス内配置矩形 |
| `merges.Array.data[n].textureConfigOverrides` | TextureConfigOverride[] | テクスチャ別サイズ上書き |

#### Merge Toon Lit Material

| propertyPath | 型 | 説明 |
|---|---|---|
| `merges` | MergeInfo[] | マージ設定配列 |
| `merges.Array.data[n].mergedFormat` | MergedTextureFormat | テクスチャフォーマット |
| `merges.Array.data[n].textureSize` | Vector2Int | テクスチャサイズ (デフォルト: 2048x2048) |
| `merges.Array.data[n].source` | MergeSource[] | ソース配列 |
| `merges.Array.data[n].source.Array.data[m].materialIndex` | int | マテリアルスロット番号 |
| `merges.Array.data[n].source.Array.data[m].targetRect` | Rect | アトラス内配置矩形 |

#### Rename BlendShape

| propertyPath | 型 | 説明 |
|---|---|---|
| `nameMap` | PrefabSafeMap\<string, string\> | 旧名 → 新名のマッピング |

#### Make Children

| propertyPath | 型 | 説明 |
|---|---|---|
| `executeEarly` | bool | 早期実行フラグ |
| `children` | PrefabSafeSet\<Transform\> | 子にするオブジェクトのセット |

#### Remove Zero Sized Polygon

マーカーコンポーネント (フィールドなし)。

#### UnusedBonesByReferencesTool [Obsolete]

| propertyPath | 型 | 説明 |
|---|---|---|
| `preserveEndBone` | bool | 末端ボーン保持 (デフォルト: true) |
| `detectExtraChild` | bool | 追加子オブジェクト検出 (デフォルト: false) |

### PrefabSafeSet の propertyPath 構造

AAO の多くのフィールドは `PrefabSafeSet<T>` / `PrefabSafeMap<K,V>` でシリアライズされる。通常の Unity 配列 (`Array.data[n]`) とは異なる内部構造を持つため、propertyPath でのアクセス時に注意が必要。PrefabSafeSet は Prefab Variant チェーンでの差分 (追加/削除) を安全に管理するために設計されている。

### 設計上の注意点

- **FormerlySerializedAs**: `TraceAndOptimize.optimizeBlendShape` ← "freezeBlendShape" (1.8.0 でリネーム)。
- **Initialize パターン**: `MergeSkinnedMesh`, `RemoveMeshByBlendShape`, `RemoveMeshInBox`, `RemoveMeshByMask`, `MergePhysBone` はスクリプトから `AddComponent` 後に `Initialize(version)` を呼ぶ必要がある。呼ばない場合、将来のデフォルト値変更の影響を受ける。
- **internal フィールド**: ほとんどのシリアライズフィールドは `internal` で、パブリック API はプロパティ経由で提供される。`TraceAndOptimize` は完全にスクリプト API なし（Inspector 専用）。
- **VRCSDK 条件コンパイル**: `MergePhysBone`, `ClearEndpointPosition`, `ReplaceEndBoneWithEndpointPosition` は `#if AAO_VRCSDK3_AVATARS` で囲まれており、非 VRCSDK 環境ではコンパイルされない。
- **マーカーコンポーネント**: `RemoveZeroSizedPolygon`, `ClearEndpointPosition` はフィールドなしで、存在のみで機能する。

### 1.8.10 → 1.9.8 変更サマリー

- **新コンポーネント 4 種**: MergeMaterial, MaxTextureSize, ReplaceEndBoneWithEndpointPosition, TraceAndOptimizePlatformSettings (ScriptableObject)
- **フィールド追加**: RemoveMeshByBlendShape に `invertSelection` (bool), TraceAndOptimize に `optimizeTexture`, `allowShuffleMaterialSlots`, `mergeSkinnedMesh`
- **フィールドリネーム**: TraceAndOptimize の `freezeBlendShape` → `optimizeBlendShape`
- **複数配置解禁**: RemoveMeshByBlendShape, RemoveMeshInBox が AllowMultipleComponent に
- **削除コンポーネント**: なし

## 実運用で学んだこと

(なし -- Phase 3-4 の検証で追記する)
