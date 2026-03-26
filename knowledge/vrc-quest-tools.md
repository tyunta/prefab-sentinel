---
tool: vrc-quest-tools
version_tested: "2.11.5"
last_updated: 2026-03-26
confidence: medium
---

# VRCQuestTools

## 概要 (L1)

VRChat アバターの Android (Quest / PICO) 対応を自動化するエディター拡張。PC 向けアバターを数ステップで Android アップロード可能な状態に変換する。NDMF プラグインとして動作し、ビルド時に非破壊でマテリアル変換・コンポーネント削除・テクスチャ最適化を実行する。

**解決する問題**: Android プラットフォームでは使用可能なシェーダーが VRChat/Mobile/* に限定され、Constraints や一部コンポーネントが非対応、PhysBone 数に厳しい制限がある。手動変換は煩雑でミスが生じやすい。VRCQuestTools はアバターとマテリアルを複製して元を保持しつつ、変換プロセスを自動化する。

**NDMF との関係**: NDMF は任意依存。NDMF がある場合は非破壊パイプライン (Resolving → Generating → Transforming → Optimizing) で処理が実行される。NDMF がない場合はレガシーモード（手動変換メニュー経由）で動作する。一部コンポーネント (`INdmfComponent` 実装) は NDMF 必須。

**プラットフォーム**: VRChat SDK 3.x (Avatars >= 3.3.0) 依存。PC / Android 両プラットフォーム向けアバターのビルドを制御する。

**最新バージョン**: 2.11.5 (2025-01-23)。VPM リポジトリ `https://kurotu.github.io/vpm-repos/vpm.json` から配布。

**対応シェーダー (入力)**: Standard, Standard (Specular), Unlit, UTS2 (UnityChanToonShader), Arktoon, AXCS (ArxCharacterShaders), Sunao, lilToon, Poiyomi。VirtualLens2 も認識するが変換対象外。未知のシェーダーは Standard として扱われる。

**変換先シェーダー (出力)**: VRChat/Mobile/Toon Lit, VRChat/Mobile/MatCap Lit, VRChat/Mobile/Toon Standard (VRCSDK 3.8.1+)。またはユーザー指定マテリアルへの完全差替 (Material Replacement)。

## コンポーネント一覧 (L1→L2)

### アバター変換 (ルートコンポーネント)

| コンポーネント名 | 用途 | NDMF 必須 | 典型的な使用場面 |
|---|---|---|---|
| VQT Avatar Converter Settings | アバター全体の変換設定を保持する。マテリアル変換方式・PhysBone 保持対象・頂点カラー除去・テクスチャ圧縮等を一括管理 | No | アバタールートに配置。レガシーモード (手動変換) と NDMF モード両方で使用 |
| VQT Converted Avatar | 変換済みアバターのマーカー。NDMF ビルド時に MA 等が生成した非対応コンポーネントの自動除去を制御 | No | 変換後のアバターに自動付与。手動配置不要 |
| VQT Material Conversion Settings | マテリアル変換設定のみを保持する軽量版。AvatarConverterSettings が無い場合のフォールバック | Yes | 非破壊ワークフローでマテリアル変換のみ必要な場合 |

### プラットフォーム分岐

| コンポーネント名 | 用途 | NDMF 必須 | 典型的な使用場面 |
|---|---|---|---|
| VQT Platform Target Settings | ビルドターゲットを Auto / PC / Android で指定し、他の Platform 系コンポーネントの判定基準を上書きする | Yes | アバタールートに配置。テスト時に手動でプラットフォームを切り替える |
| VQT Platform Component Remover | 同一 GameObject 上のコンポーネントをプラットフォーム別に除去する | Yes | PC 用の RemoveMeshByBlendShape を Android ビルドでは残し、PC ビルドでは除去する等 |
| VQT Platform GameObject Remover | この コンポーネントが付いた GameObject 自体をプラットフォーム別に除去する | Yes | PC 専用パーティクルオブジェクトを Android ビルドで除外する等 |

### マテリアル操作

| コンポーネント名 | 用途 | NDMF 必須 | 典型的な使用場面 |
|---|---|---|---|
| VQT Material Swap | マテリアルの Original→Replacement ペアリストに基づいて差替する | Yes | PC 用 lilToon マテリアルを Android 用 Toon Lit マテリアルに個別差替 |

### メッシュ操作

| コンポーネント名 | 用途 | NDMF 必須 | 典型的な使用場面 |
|---|---|---|---|
| VQT Mesh Flipper | メッシュのポリゴン反転または両面化を行う。マスクテクスチャで部分的に適用可能 | Yes | Android のモバイルシェーダーが裏面描画非対応のため、裏地が見えるスカート等を両面化する |
| VQT Vertex Color Remover | メッシュから頂点カラーを除去する。Toon Lit シェーダーでの黒表示問題を解消 | No | 共有メッシュに影響するため注意が必要。シーン内で即時適用される (ExecuteInEditMode) |

### ユーティリティ

| コンポーネント名 | 用途 | NDMF 必須 | 典型的な使用場面 |
|---|---|---|---|
| VQT Network ID Assigner | PhysBone にネットワーク ID をハッシュベースで割り当てる。オブジェクト追加/削除で ID が変わらない | No | PC/Android 間の PhysBone 同期を維持するため、アバタールートに配置 |
| VQT Menu Icon Resizer | Expressions メニューアイコンのリサイズ・圧縮・除去をビルド時に実行 | Yes | MA 等が大量のアイコンを生成する場合のビルドサイズ削減 |

## 操作パターン (L2)

### NDMF 非破壊変換 (推奨パターン)

1. アバタールートに **VQT Avatar Converter Settings** を配置
2. Default Material Conversion を Toon Lit / MatCap Lit / Toon Standard から選択
3. 個別マテリアルに異なる変換方式を指定する場合は Additional Material Conversion Settings に追加
4. 保持する PhysBone / PhysBone Collider / Contact を選択（デフォルトは全保持）
5. Unity のビルドターゲットを Android に切り替えてビルド → NDMF パイプラインが自動で変換を実行
6. 元のアバターは変更されない

### プラットフォーム別 GameObject 除外

1. PC 専用オブジェクト (パーティクル、高負荷エフェクト等) に **VQT Platform GameObject Remover** を配置
2. 「Keep on Android」のチェックを外す（Android ビルド時に除去される）
3. 必要に応じてアバタールートに **VQT Platform Target Settings** を配置し、テスト用にプラットフォームを強制指定

### マテリアル個別差替パターン

1. アバターの任意の階層に **VQT Material Swap** を配置
2. materialMappings に Original → Replacement のペアを登録
3. Android ビルド時に自動的にマテリアルが差し替わる
4. NDMF Phase は AvatarConverterSettings または MaterialConversionSettings の設定に従う（なければ Auto）

### 裏面表示対応パターン (Mesh Flipper)

1. 裏面が見えるメッシュの Renderer がある GameObject に **VQT Mesh Flipper** を配置
2. Direction を BothSides (両面化) または Flip (反転) に設定
3. 部分的に適用する場合は useMask を有効にしてマスクテクスチャを指定
4. processingPhase でポリゴン削減ツールとの処理順を制御
5. enabledOnPC / enabledOnAndroid でプラットフォーム別の有効/無効を制御

### コンポーネント別プラットフォーム制御

1. プラットフォーム別に除去したいコンポーネントがある GameObject に **VQT Platform Component Remover** を配置
2. Inspector で各コンポーネントの PC / Android チェックボックスを設定
3. チェックが外れたプラットフォームのビルド時にそのコンポーネントが除去される

## NDMF パイプライン実行順序 (L2)

VRCQuestTools は NDMF の 4 フェーズに Pass を配置する。他プラグインとの実行順序制約が多い。

### Resolving フェーズ
VirtualLens2 / Modular Avatar より前に実行。
1. **BuildTargetConfigurationPass** — PlatformTargetSettings からビルドターゲットを決定
2. **PlatformGameObjectRemoverPass** — プラットフォーム条件で GameObject を除去
3. **PlatformComponentRemoverPass** — プラットフォーム条件でコンポーネントを除去
4. **AvatarConverterResolvingPass** — 変換前の解決処理

### Generating フェーズ
5. **AssignNetworkIDsPass** — PhysBone にネットワーク ID を割り当て

### Transforming フェーズ
TexTransTool / Modular Avatar / lilycalInventory の後に実行。
6. **AvatarConverterTransformingPass** — マテリアル変換 (ndmfPhase=Transforming の場合)
7. **MeshFlipperPass** — メッシュ反転/両面化 (BeforePolygonReduction)。MantisLODEditor より前
8. **RemoveVertexColorPass** — 頂点カラー除去。MantisLODEditor の後

### Optimizing フェーズ
TexTransTool / PosingSystem / 各種ポリゴン削減ツールの後、AvatarOptimizer より前に実行。
9. **AvatarConverterOptimizingPass** — マテリアル変換 (ndmfPhase=Optimizing/Auto の場合)
10. **MeshFlipperAfterPolygonReductionPass** — メッシュ反転/両面化 (AfterPolygonReduction)
11. **RemoveUnsupportedComponentsPass** — Android 非対応コンポーネントの除去
12. **MenuIconResizerPass** — メニューアイコンのリサイズ/圧縮
13. **RemoveVRCQuestToolsComponentsPass** — VQT 自身のコンポーネントを除去
14. **CheckTextureFormatPass** — テクスチャフォーマットの検証 (AvatarOptimizer の後)

## SerializedProperty リファレンス (L3)

ソースバージョン: 2.11.5 (Shiratsume)
検証方法: Runtime/*.cs ソースコード読み + .meta ファイルの GUID 抽出（inspect 実測なし → confidence: medium）

### Script GUID テーブル

| コンポーネント | GUID | 備考 |
|---|---|---|
| AvatarConverterSettings | `04745580e9f923e4594936c3c4bca9c1` | アバタールート用。レガシー + NDMF 両対応 |
| ConvertedAvatar | `0c3d92978aca6b2449bd5dde9cdbbae1` | マーカーコンポーネント (フィールドなし) |
| MaterialConversionSettings | `a5c71331dc030c74bac4bee2e17c30f7` | NDMF 専用の軽量マテリアル変換設定 |
| MaterialSwap | `bbe45f5e73194bc42befaa923565ddd9` | マテリアル差替 |
| MenuIconResizer | `7355e00d72aae944b9e57eb7609aa972` | メニューアイコンリサイズ |
| MeshFlipper | `a7eacb5c40892e74b8f87dc915eebf0d` | メッシュ反転/両面化 |
| NetworkIDAssigner | `f74eb3e516efff9469dc1d3562cf1f3a` | マーカーコンポーネント (フィールドなし) |
| PlatformComponentRemover | `018c94f1c1f806f449e19a9809861706` | コンポーネント別プラットフォーム除去 |
| PlatformGameObjectRemover | `76fe5b747e0458940b2c12ea93f53010` | GameObject プラットフォーム除去 |
| PlatformTargetSettings | `80d5e356736828d41b64a45e43f572cf` | ビルドターゲット指定 |
| VertexColorRemover | `f055e14e1beba894ea68aedffde8ada6` | 頂点カラー除去 |
| VRCQuestToolsEditorOnly | `555ee7815c853114ebd2e0031e2143a0` | 基底クラス (直接配置しない) |

### 共通型

**IMaterialConvertSettings** (interface — SerializeReference で多態的にシリアライズされる):
変換方式の切替は `[SerializeReference]` による多態で実現される。実体は以下の 4 種:
- `ToonLitConvertSettings` — Toon Lit 変換
- `MatCapLitConvertSettings` — MatCap Lit 変換
- `ToonStandardConvertSettings` — Toon Standard 変換 (VRCSDK 3.8.1+)
- `MaterialReplaceSettings` — マテリアル完全差替

**AdditionalMaterialConvertSettings** (Serializable class):
- `targetMaterial`: Material — 変換対象マテリアル
- `materialConvertSettings`: IMaterialConvertSettings — 個別変換設定 ([SerializeReference])

**PlatformComponentRemoverItem** (Serializable class):
- `component`: Component — 対象コンポーネント参照
- `removeOnPC`: bool — PC ビルド時に除去
- `removeOnAndroid`: bool — Android ビルド時に除去

### コンポーネント別フィールド

#### VQT Avatar Converter Settings
| propertyPath | 型 | 説明 |
|---|---|---|
| `defaultMaterialConvertSettings` | IMaterialConvertSettings | デフォルトマテリアル変換設定 ([SerializeReference]) |
| `additionalMaterialConvertSettings` | AdditionalMaterialConvertSettings[] | マテリアル個別変換設定 ([SerializeReference]) |
| `removeAvatarDynamics` | bool | Avatar Dynamics (PhysBone/Contact) を除去するか (デフォルト: true) |
| `physBonesToKeep` | VRCPhysBone[] | 保持する PhysBone の配列 |
| `physBoneCollidersToKeep` | VRCPhysBoneCollider[] | 保持する PhysBone Collider の配列 |
| `contactsToKeep` | ContactBase[] | 保持する Contact の配列 |
| `animatorOverrideControllers` | AnimatorOverrideController[] | 変換時に適用する Animator Override Controller ([NonReorderable]) |
| `removeVertexColor` | bool | 頂点カラーを除去するか (デフォルト: true) |
| `removeExtraMaterialSlots` | bool | サブメッシュ数を超える余分なマテリアルスロットを除去するか (デフォルト: true) |
| `compressExpressionsMenuIcons` | bool | 未圧縮のメニューアイコンを圧縮するか (デフォルト: true) |
| `ndmfPhase` | AvatarConverterNdmfPhase | NDMF 実行フェーズ (Transforming=0, Optimizing=1, Auto=2; デフォルト: Auto) |

#### VQT Material Conversion Settings
| propertyPath | 型 | 説明 |
|---|---|---|
| `defaultMaterialConvertSettings` | IMaterialConvertSettings | デフォルトマテリアル変換設定 ([SerializeReference]) |
| `additionalMaterialConvertSettings` | AdditionalMaterialConvertSettings[] | マテリアル個別変換設定 ([SerializeReference]) |
| `removeExtraMaterialSlots` | bool | 余分なマテリアルスロットを除去するか (デフォルト: true) |
| `ndmfPhase` | AvatarConverterNdmfPhase | NDMF 実行フェーズ (デフォルト: Auto) |

#### VQT Material Swap
| propertyPath | 型 | 説明 |
|---|---|---|
| `materialMappings` | List\<MaterialMapping\> | マテリアル差替ペアのリスト |
| `materialMappings.Array.data[n].originalMaterial` | Material | 差替元マテリアル |
| `materialMappings.Array.data[n].replacementMaterial` | Material | 差替先マテリアル |

#### VQT Menu Icon Resizer
| propertyPath | 型 | 説明 |
|---|---|---|
| `resizeModePC` | TextureResizeMode | PC 用リサイズモード (DoNotResize=-1, Remove=0, Max64x64=64, Max128x128=128; デフォルト: DoNotResize) |
| `resizeModeAndroid` | TextureResizeMode | Android 用リサイズモード (デフォルト: DoNotResize) |
| `compressTextures` | bool | 未圧縮テクスチャを圧縮するか (デフォルト: true) |
| `mobileTextureFormat` | MobileTextureFormat | Android 用テクスチャフォーマット (デフォルト: ASTC_8x8) |

#### VQT Mesh Flipper
| propertyPath | 型 | 説明 |
|---|---|---|
| `direction` | MeshFlipperMeshDirection | メッシュ方向 (Flip=0, BothSides=1; デフォルト: BothSides) |
| `enabledOnPC` | bool | PC ビルドで有効か (デフォルト: false) |
| `enabledOnAndroid` | bool | Android ビルドで有効か (デフォルト: true) |
| `useMask` | bool | マスクテクスチャを使用するか (デフォルト: false) |
| `maskTexture` | Texture2D | マスクテクスチャ |
| `maskMode` | MeshFlipperMaskMode | マスクモード (FlipWhite=0, FlipBlack=1; デフォルト: FlipWhite) |
| `processingPhase` | MeshFlipperProcessingPhase | 処理フェーズ (BeforePolygonReduction=0, AfterPolygonReduction=1; デフォルト: AfterPolygonReduction) |

#### VQT Platform Component Remover
| propertyPath | 型 | 説明 |
|---|---|---|
| `componentSettings` | PlatformComponentRemoverItem[] | コンポーネント別の除去設定配列 |
| `componentSettings.Array.data[n].component` | Component | 対象コンポーネント参照 |
| `componentSettings.Array.data[n].removeOnPC` | bool | PC ビルド時に除去 |
| `componentSettings.Array.data[n].removeOnAndroid` | bool | Android ビルド時に除去 |

#### VQT Platform GameObject Remover
| propertyPath | 型 | 説明 |
|---|---|---|
| `removeOnPC` | bool | PC ビルド時に GameObject を除去 (デフォルト: false) |
| `removeOnAndroid` | bool | Android ビルド時に GameObject を除去 (デフォルト: false) |

#### VQT Platform Target Settings
| propertyPath | 型 | 説明 |
|---|---|---|
| `buildTarget` | BuildTarget | ビルドターゲット (Auto=0, PC=1, Android=2; デフォルト: Auto) |

#### VQT Vertex Color Remover
| propertyPath | 型 | 説明 |
|---|---|---|
| `includeChildren` | bool | 子オブジェクトのレンダラーも対象にするか (デフォルト: false) |
| `active` | bool | [Obsolete] 旧 active フラグ。v2 以降は enabled を使用 (serializedVersion マイグレーション済み) |

### マテリアル変換設定の SerializedProperty

`[SerializeReference]` で多態的にシリアライズされるため、propertyPath は `defaultMaterialConvertSettings.` のプレフィックス付きでアクセスする。

#### ToonLitConvertSettings
| propertyPath (相対) | 型 | 説明 |
|---|---|---|
| `generateQuestTextures` | bool | テクスチャを生成するか (デフォルト: true) |
| `maxTextureSize` | TextureSizeLimit | 最大テクスチャサイズ (NoLimit=0, 256/512/1024/2048; デフォルト: Max1024x1024) |
| `mobileTextureFormat` | MobileTextureFormat | テクスチャフォーマット (ASTC_4x4/5x5/6x6/8x8/10x10/12x12; デフォルト: ASTC_6x6) |
| `mainTextureBrightness` | float | テクスチャ輝度 [0-1] (デフォルト: 0.83)。Toon Lit は間接光で約 150% になるため 1.0 では明るすぎる |
| `generateShadowFromNormalMap` | bool | 法線マップから影を生成するか (デフォルト: true) |

#### MatCapLitConvertSettings
ToonLitConvertSettings の全フィールドに加えて:
| propertyPath (相対) | 型 | 説明 |
|---|---|---|
| `matCapTexture` | Texture | MatCap テクスチャ |

#### ToonStandardConvertSettings
| propertyPath (相対) | 型 | 説明 |
|---|---|---|
| `generateQuestTextures` | bool | テクスチャを生成するか (デフォルト: true) |
| `maxTextureSize` | TextureSizeLimit | 最大テクスチャサイズ (デフォルト: Max1024x1024) |
| `mobileTextureFormat` | MobileTextureFormat | テクスチャフォーマット (デフォルト: ASTC_6x6) |
| `fallbackShadowRamp` | Texture2D | 未対応マテリアルのフォールバック用シャドウランプテクスチャ |

#### MaterialReplaceSettings
| propertyPath (相対) | 型 | 説明 |
|---|---|---|
| `material` | Material | 差替先マテリアル |

### 設計上の注意点

- **SerializeReference 多態**: `defaultMaterialConvertSettings` と `additionalMaterialConvertSettings[n].materialConvertSettings` は `[SerializeReference]` で型情報付きシリアライズされる。YAML 上では `type: {class: ToonLitConvertSettings, ns: KRT.VRCQuestTools.Models, asm: ...}` の形式で記録される。prefab-sentinel の inspect では managedReference として表示される。
- **VRCQuestToolsEditorOnly 基底クラス**: 全コンポーネントが `MonoBehaviour` + `IEditorOnly` を継承する基底クラスを持つ。ビルド時にストリップされる前提の EditorOnly コンポーネント。
- **Obsolete フィールド**: VertexColorRemover の `active` フィールドは `serializedVersion` によるマイグレーションで `enabled` に移行済み。
- **共有メッシュへの副作用**: VertexColorRemover は `ExecuteInEditMode` で共有メッシュを直接変更するため、同じメッシュを使う全アバターに影響する。
- **AvatarConverterNdmfPhase.Auto の解決**: VRCFury がアバターに存在する場合は Transforming、それ以外は Optimizing に解決される。VRCFury の存在チェックはリフレクションで行われる。
- **マーカーコンポーネント**: ConvertedAvatar と NetworkIDAssigner はシリアライズ対象フィールドを持たない。存在のみで機能する。
- **PlatformGameObjectRemover のフィールド名**: Inspector 表示は「Keep on PC / Keep on Android」だが、SerializedProperty は `removeOnPC` / `removeOnAndroid` で論理が反転している（Inspector 側で表示を反転）。

## 実運用で学んだこと

(なし — Phase 3-4 の検証で追記する)
