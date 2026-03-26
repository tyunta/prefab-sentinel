---
tool: tex-trans-tool
version_tested: "1.0.1"
last_updated: 2026-03-26
confidence: medium
---

# TexTransTool (TTT)

## 概要 (L1)

非破壊テクスチャ変換ツール。NDMF プラグインとして動作し、ビルド時にテクスチャの合成・変換・アトラス化を実行する。最終アバターにはランタイムコンポーネントが残らない。

**解決する問題**: テクスチャの色替え、デカール貼り付け、髪グラデーション、アトラス化によるVRAM削減を、元のテクスチャやマテリアルを破壊せずに行う。PSD レイヤー構造をそのまま Unity 上で再現・編集できる MultiLayerImage 機能も備える。

**NDMF との関係**: TTT は NDMF に依存し、ビルドパイプライン内で実行される。ModularAvatar や AvatarOptimizer がインストール済みなら NDMF は追加不要。

**実行フェーズ**: TTT 独自の `TexTransPhase` で処理順を管理する。
- `MaterialModification` (5) — マテリアルプロパティ変更
- `BeforeUVModification` (1) — MultiLayerImage、TextureBlender 等
- `UVModification` (2) — UVCopy
- `AfterUVModification` (3) — デカール系（SimpleDecal、Gradation）
- `Optimizing` (4) — AtlasTexture、TextureConfigurator
- `PostProcessing` (6) — ColorDifferenceChanger、NearTransTexture

**安定性保証**: `ITexTransToolStableComponent` を実装するコンポーネントのみ、同一マイナーバージョン間でフィールド互換が保証される。実験的コンポーネントは予告なく変更・削除される可能性がある。v0.x 間はパッチバージョンでも破壊的変更あり。

**バージョン**: 1.0.1 (2024-12-26)。VPM リポジトリ `https://vpm.rs64.net/vpm.json` から配布。

## コンポーネント一覧 (L1 -> L2)

### 継承階層

```
MonoBehaviour
  └─ TexTransMonoBase (共通基底、_saveDataVersion)
       ├─ TexTransMonoBaseGameObjectOwned [DisallowMultipleComponent]
       │    ├─ TexTransBehavior (PhaseDefine)
       │    │    ├─ TexTransRuntimeBehavior (Apply 実行)
       │    │    │    ├─ SimpleDecal [Stable]
       │    │    │    ├─ SingleGradationDecal [Stable]
       │    │    │    ├─ DistanceGradationDecal
       │    │    │    ├─ AtlasTexture [Stable]
       │    │    │    ├─ MultiLayerImageCanvas
       │    │    │    ├─ TextureBlender [Stable]
       │    │    │    ├─ ColorDifferenceChanger
       │    │    │    ├─ MaterialModifier
       │    │    │    ├─ TextureConfigurator
       │    │    │    ├─ NearTransTexture
       │    │    │    ├─ UVCopy
       │    │    │    └─ ParallelProjectionWithLilToonDecal
       │    │    └─ TexTransCallEditorBehavior
       │    │         └─ MaterialOverrideTransfer
       │    ├─ AbstractLayer (MLI レイヤー基底)
       │    │    ├─ RasterLayer
       │    │    ├─ RasterImportedLayer
       │    │    ├─ LayerFolder
       │    │    ├─ SolidColorLayer
       │    │    ├─ HSLAdjustmentLayer
       │    │    ├─ HSVAdjustmentLayer
       │    │    ├─ LevelAdjustmentLayer
       │    │    ├─ SelectiveColoringAdjustmentLayer
       │    │    ├─ ColorizeLayer
       │    │    ├─ UnityGradationMapLayer
       │    │    ├─ YAxisFixedGradientLayer
       │    │    └─ AbstractImageLayer (拡張用)
       │    ├─ PhaseDefinition [Stable]
       │    └─ AbstractIslandSelector
       │         ├─ BoxIslandSelector [Stable]
       │         ├─ SphereIslandSelector [Stable]
       │         ├─ PinIslandSelector [Stable]
       │         ├─ AimIslandSelector
       │         ├─ MaterialIslandSelector
       │         ├─ RendererIslandSelector
       │         └─ SubMeshIndexIslandSelector
       ├─ TexTransAnnotation (マーカー/設定)
       │    ├─ SimpleDecalExperimentalFeature
       │    ├─ AtlasTextureExperimentalFeature
       │    ├─ PreviewGroup
       │    ├─ DomainDefinition
       │    ├─ IsActiveInheritBreaker
       │    └─ NegotiateAAOConfig
       └─ AsLayer (ICanBehaveAsLayer ラッパー)
```

### デカール (テクスチャ貼り付け)

| コンポーネント名 | 用途 | 安定 | Phase |
|---|---|---|---|
| SimpleDecal | テクスチャをメッシュに平行投影デカール | Yes | AfterUVModification |
| SimpleDecalExperimentalFeature | SimpleDecal の実験的拡張（深度・MLI オーバーライド） | No | (Annotation) |
| SingleGradationDecal | 単軸グラデーション投影 | Yes | AfterUVModification |
| DistanceGradationDecal | 距離ベースグラデーション投影 | No | AfterUVModification |
| ParallelProjectionWithLilToonDecal | lilToon の 2nd/3rd テクスチャスロットへ UV 書込+デカール | No | MaterialModification |

### テクスチャアトラス (VRAM 最適化)

| コンポーネント名 | 用途 | 安定 | Phase |
|---|---|---|---|
| AtlasTexture | マテリアル統合+テクスチャアトラス化 | Yes | Optimizing |
| AtlasTextureExperimentalFeature | AtlasTexture の実験的拡張（自動設定・個別チューニング） | No | (Annotation) |

### MultiLayerImage (PSD ライク画像編集)

| コンポーネント名 | 用途 | 安定 | Phase |
|---|---|---|---|
| MultiLayerImageCanvas | MLI のルートキャンバス。子 Transform のレイヤーを合成 | No | BeforeUVModification |
| RasterLayer | ラスタ画像レイヤー | No | (Layer) |
| RasterImportedLayer | PSD インポートされたラスタレイヤー | No | (Layer) |
| LayerFolder | レイヤーフォルダ（PassThrough 対応） | No | (Layer) |
| AsLayer | ICanBehaveAsLayer 実装を MLI レイヤーとして使用するアダプター | No | (Layer) |
| SolidColorLayer | 単色レイヤー | No | (Layer) |
| HSLAdjustmentLayer | HSL 色相・彩度・明度調整レイヤー | No | (Layer) |
| HSVAdjustmentLayer | HSV 色相・彩度・明度調整レイヤー | No | (Layer) |
| LevelAdjustmentLayer | レベル補正レイヤー (RGB/R/G/B 個別) | No | (Layer) |
| SelectiveColoringAdjustmentLayer | 特定色域の CMYK 調整レイヤー | No | (Layer) |
| ColorizeLayer | カラーライズレイヤー | No | (Layer) |
| UnityGradationMapLayer | Unity Gradient によるグラデーションマップレイヤー | No | (Layer) |
| YAxisFixedGradientLayer | Y 軸固定グラデーションレイヤー | No | (Layer) |

### 共通コンポーネント

| コンポーネント名 | 用途 | 安定 | Phase |
|---|---|---|---|
| TextureBlender | テクスチャに画像/色を合成 | Yes | BeforeUVModification |
| ColorDifferenceChanger | 色差分変換（ソース色 -> ターゲット色） | No | PostProcessing |
| MaterialModifier | マテリアルプロパティ上書き | No | MaterialModification |
| MaterialOverrideTransfer | マテリアルバリアントの差分転送 | No | MaterialModification |
| TextureConfigurator | テクスチャサイズ・圧縮設定 | No | Optimizing |
| NearTransTexture | 近傍転写テクスチャ（ソース -> ターゲット間の近距離テクスチャ転写） | No | PostProcessing |
| UVCopy | UV チャンネル間コピー | No | UVModification |

### グループ・制御

| コンポーネント名 | 用途 | 安定 | Phase |
|---|---|---|---|
| PhaseDefinition | 子コンポーネントの実行フェーズを指定 | Yes | - |
| PreviewGroup | プレビュートリガー用マーカー | No | (Annotation) |
| DomainDefinition | ドメイン範囲定義マーカー | No | (Annotation) |
| IsActiveInheritBreaker | activeInHierarchy 継承を遮断 | No | (Annotation) |
| NegotiateAAOConfig | AvatarOptimizer 連携設定 | No | (Annotation) |

### IslandSelector (UV アイランド選択)

| コンポーネント名 | 用途 | 安定 |
|---|---|---|
| BoxIslandSelector | ボックス範囲で UV アイランドを選択 | Yes |
| SphereIslandSelector | 球範囲で UV アイランドを選択 | Yes |
| PinIslandSelector | レイ+範囲で UV アイランドを選択 | Yes |
| AimIslandSelector | レイキャストで UV アイランドを選択 | No |
| MaterialIslandSelector | マテリアル指定でアイランドを選択 | No |
| RendererIslandSelector | レンダラー指定でアイランドを選択 | No |
| SubMeshIndexIslandSelector | SubMesh インデックスでアイランドを選択 | No |
| IslandSelectorAND / OR / NOT / XOR | 論理演算子 (子セレクターを組合せ) | No |

## 操作パターン (L2)

### デカールで衣装にロゴ・模様を追加

1. アバター階層にデカール用 GameObject を作成し、**SimpleDecal** を追加
2. `DecalTexture` にデカール画像を設定
3. Transform の Position/Rotation/Scale でデカールの位置・向き・サイズを調整（Z 軸方向に投影）
4. `RendererSelector.Mode` が Auto なら全レンダラーに投影、`UseMaterialFilteringForAutoSelect` で対象マテリアルを絞る
5. `BackCulling = true` で裏面への投影を防止
6. `BlendTypeKey` で合成モードを選択（デフォルト: Normal）
7. `IslandSelector` を子に配置すると投影範囲を UV アイランド単位で絞り込める

### 髪にグラデーションをかける

1. **SingleGradationDecal** を髪のルート付近に配置
2. `Gradient` に Unity Gradient で色設定（Y 軸方向に 0->1 でマッピング）
3. Transform の Y 軸方向がグラデーション方向、Position がグラデーション起点
4. `RendererSelector.UseMaterialFilteringForAutoSelect = true`（デフォルト）で対象マテリアルを指定
5. `GradientClamp = true` で範囲外クランプ、`false` でテクスチャストレッチ

### テクスチャアトラス化で VRAM 削減

1. アバター直下に **AtlasTexture** を追加
2. `AtlasTargetMaterials` に統合対象のマテリアルをすべて追加
3. `AtlasSetting.AtlasTextureSize` でアトラステクスチャサイズを指定（デフォルト: 2048）
4. `AtlasSetting.TextureFineTuning` でテクスチャ個別設定（リサイズ、圧縮、カラースペース）
5. `MergeMaterialGroups` でマテリアル統合グループを定義。統合するとドローコールが削減される
6. `AtlasSetting.IslandPadding` でアイランド間パディングを調整（デフォルト: 0.01）

### PSD ライクなレイヤー合成で色替え

1. **MultiLayerImageCanvas** を配置し、`TargetTexture.SelectTexture` に対象テクスチャを指定
2. 子 GameObject にレイヤーコンポーネントを配置（下から上へ合成される = Transform の子順序が逆）
3. **SolidColorLayer** で単色塗りつぶし、**HSLAdjustmentLayer** で色相シフト
4. **LayerFolder** でグループ化し、`PassThrough` でフォルダ合成を透過モードにできる
5. 各レイヤーの `Opacity`, `BlendTypeKey`, `Clipping`, `LayerMask` で Photoshop と同様の制御
6. `AsLayer` コンポーネントで SimpleDecal 等を MLI レイヤーとして組み込める

## SerializedProperty リファレンス (L3)

ソースバージョン: 1.0.1 (Shiratsume)
検証方法: .cs ソースコード読み + .meta ファイルの GUID 抽出（inspect 実測なし -> confidence: medium）

### Script GUID テーブル

#### デカール系

| コンポーネント | GUID |
|---|---|
| SimpleDecal | `a1fe9512200d12a42ac84c26758a7a36` |
| SimpleDecalExperimentalFeature | `1b9b8cd178b3c3345bf5481e43acf4ab` |
| SingleGradationDecal | `b399477d38fcd3e4db79d5052517d489` |
| DistanceGradationDecal | `37825881cb41afa4bb04f643d0df1a86` |
| ParallelProjectionWithLilToonDecal | `1f1d66a3641a40d409edce87a95ff51f` |

#### アトラス系

| コンポーネント | GUID |
|---|---|
| AtlasTexture | `aae1d1b9de0f6d54f94e6d13f5b17274` |
| AtlasTextureExperimentalFeature | `6bef4653024fff420929b992af5b3239` |

#### MultiLayerImage 系

| コンポーネント | GUID |
|---|---|
| MultiLayerImageCanvas | `5b0549e95c4c1e14ea359df5590398ea` |
| RasterLayer | `b6d8f8a13627ebc4b99f56a31d3c4482` |
| RasterImportedLayer | `bb08ddc67ea11d54c9c74bac96b7a36f` |
| LayerFolder | `bee084a7e72b3074a9a3696108dfea21` |
| AsLayer | `2538bded2ef6e4a4da777121c81e58c0` |
| SolidColorLayer | `05986e499f490804085c6aeb35c24ebc` |
| HSLAdjustmentLayer | `6dbaee940c8093a44b6502f846fe3a96` |
| HSVAdjustmentLayer | `3a2491d8648ce474bb4e23d6e832c516` |
| LevelAdjustmentLayer | `b1e324ceadc1a9541b2260c0415f15ac` |
| SelectiveColoringAdjustmentLayer | `138028e403b2ecf4f91534a1f8fb9861` |
| ColorizeLayer | `185a72c99410fc64396236d840e00937` |
| UnityGradationMapLayer | `1bca7b920b7d3a3459f1ac2c5e11e95c` |
| YAxisFixedGradientLayer | `6bef515a7dca1db46b8af4a333c52947` |

#### 共通コンポーネント系

| コンポーネント | GUID |
|---|---|
| TextureBlender | `7b5e27557115fba48af3b6f21c474f1c` |
| ColorDifferenceChanger | `29c5cd4df05feb14fb6830b1f650e489` |
| MaterialModifier | `108295c20eedc34458bf9cda9b8d4412` |
| MaterialOverrideTransfer | `8e14b2e949333c845aea2c2544581e07` |
| TextureConfigurator | `3d2ce1e40ea72ae47ae56639532e3602` |
| NearTransTexture | `d8a409377f92b9846a4c9a939e967925` |
| UVCopy | `5101b44e28126d1b0b7e31894bc5a8ce` |

#### グループ・制御系

| コンポーネント | GUID |
|---|---|
| PhaseDefinition | `01679410825bc994f8fef579d2bf4d19` |
| PreviewGroup | `1c54250d07e0dbd47bae80fc6c9242d6` |
| DomainDefinition | `c505736ce98d90443807418ec293c31f` |
| IsActiveInheritBreaker | `7e6bfb425267d78459958e64c4e4e215` |
| NegotiateAAOConfig | `7daa10e4219107f42b7af6fb3ee387f8` |

#### IslandSelector 系

| コンポーネント | GUID |
|---|---|
| BoxIslandSelector | `531265db5df6a5c44a6da0ea8b6229eb` |
| SphereIslandSelector | `a4190c7a32becf54aaf0e4f8ecea9c2d` |
| PinIslandSelector | `4f4975755f5e33a409da61de02a7be1f` |
| AimIslandSelector | `594d0eed048aac24d800d69b94d931d1` |
| MaterialIslandSelector | `4b60a3f589505ac40ab3717d0efc9198` |
| RendererIslandSelector | `9b586b00f85c0ef42893719be144f9f6` |
| SubMeshIndexIslandSelector | `8c6419f0aafead04489fb507069c6c25` |
| IslandSelectorAND | `85ff367561c3249439eef9643e07d156` |
| IslandSelectorOR | `8bce7cd6bc9815843a229364be81a0ae` |
| IslandSelectorNOT | `c255e6473250bdb4b972415765471bdb` |
| IslandSelectorXOR | `819595e91d093fb42bfb02e5b28ca785` |

### 共通型

**PropertyName** (Serializable struct — テクスチャプロパティ指定に使用):
- `_propertyName`: string — シェーダープロパティ名（デフォルト: `_MainTex`）
- `_useCustomProperty`: bool — カスタムプロパティ使用フラグ
- `_shaderName`: string — シェーダー名（"DefaultShader", "lilToon", "" 等）

**TextureSelector** (Serializable class — テクスチャ選択):
- `SelectTexture`: Texture2D — 対象テクスチャの直接参照

**DecalRendererSelector** (Serializable class — レンダラー選択):
- `Mode`: RendererSelectMode — Auto=0, Manual=1
- `UseMaterialFilteringForAutoSelect`: bool — Auto モードでマテリアルフィルタを使用
- `IsAutoIncludingDisableRenderers`: bool — 無効レンダラーも含める
- `AutoSelectFilterMaterials`: List\<Material\> — フィルタ用マテリアルリスト
- `ManualSelections`: List\<Renderer\> — 手動選択レンダラーリスト

**TextureCompressionData** (Serializable class — 圧縮設定):
- `FormatQualityValue`: FormatQuality — None=0, Low=1, Normal=2, High=3
- `UseOverride`: bool — 手動フォーマット指定
- `OverrideTextureFormat`: TextureFormat — 上書きフォーマット（デフォルト: BC7）
- `CompressionQuality`: int — 圧縮品質 0-100（デフォルト: 50）

**AbstractLayer** (レイヤー共通フィールド — MLI レイヤーの基底):
- `Opacity`: float — 不透明度 0-1（デフォルト: 1）
- `Clipping`: bool — クリッピング
- `BlendTypeKey`: string — 合成モード
- `LayerMask`: ILayerMask — レイヤーマスク（SerializeReference）

**LayerMask** (Serializable class — レイヤーマスク実装):
- `LayerMaskDisabled`: bool — マスク無効化
- `MaskTexture`: Texture2D — マスクテクスチャ

**IslandSelectAsLayerMask** (Serializable class — IslandSelector をレイヤーマスクとして使用):
- `IslandSelector`: AbstractIslandSelector — アイランドセレクター参照
- `MaskPadding`: float — マスクパディング（デフォルト: 5）

### コンポーネント別フィールド

#### SimpleDecal
| propertyPath | 型 | 説明 |
|---|---|---|
| `RendererSelector` | DecalRendererSelector | レンダラー選択（上記共通型参照） |
| `DecalTexture` | Texture2D | デカールテクスチャ |
| `BlendTypeKey` | string | 合成モード |
| `Color` | Color | カラー乗算 (デフォルト: white) |
| `TargetPropertyName` | PropertyName | 対象テクスチャプロパティ (デフォルト: _MainTex) |
| `Padding` | float | パディング (デフォルト: 5) |
| `DownScaleAlgorithm` | string | ダウンスケールアルゴリズム |
| `FixedAspect` | bool | アスペクト比固定 (デフォルト: true) |
| `BackCulling` | bool | 裏面カリング (デフォルト: true)。[FormerlySerializedAs("SideChek")][FormerlySerializedAs("SideCulling")] |
| `IslandSelector` | AbstractIslandSelector | アイランドセレクター (nullable) |

#### SimpleDecalExperimentalFeature
| propertyPath | 型 | 説明 |
|---|---|---|
| `OverrideDecalTextureWithMultiLayerImageCanvas` | MultiLayerImageCanvas | デカールテクスチャを MLI でオーバーライド |
| `UseDepth` | bool | 深度使用 |
| `DepthInvert` | bool | 深度反転 |

#### SingleGradationDecal
| propertyPath | 型 | 説明 |
|---|---|---|
| `RendererSelector` | DecalRendererSelector | レンダラー選択 (デフォルト: UseMaterialFilteringForAutoSelect=true) |
| `Gradient` | Gradient | グラデーション |
| `Alpha` | float | アルファ 0-1 (デフォルト: 1) |
| `GradientClamp` | bool | グラデーションクランプ (デフォルト: true) |
| `IslandSelector` | AbstractIslandSelector | アイランドセレクター (nullable) |
| `BlendTypeKey` | string | 合成モード |
| `TargetPropertyName` | PropertyName | 対象テクスチャプロパティ |
| `Padding` | float | パディング (デフォルト: 5) |

#### DistanceGradationDecal
| propertyPath | 型 | 説明 |
|---|---|---|
| `RendererSelector` | DecalRendererSelector | レンダラー選択 (デフォルト: UseMaterialFilteringForAutoSelect=true) |
| `GradationMinDistance` | float | グラデーション最小距離 (デフォルト: 0) |
| `GradationMaxDistance` | float | グラデーション最大距離 (デフォルト: 0) |
| `Gradient` | Gradient | グラデーション |
| `Alpha` | float | アルファ 0-1 (デフォルト: 1) |
| `GradientClamp` | bool | グラデーションクランプ (デフォルト: true) |
| `IslandSelector` | AbstractIslandSelector | アイランドセレクター (nullable) |
| `BlendTypeKey` | string | 合成モード |
| `TargetPropertyName` | PropertyName | 対象テクスチャプロパティ |
| `Padding` | float | パディング (デフォルト: 5) |

#### AtlasTexture
| propertyPath | 型 | 説明 |
|---|---|---|
| `AtlasTargetMaterials` | List\<Material\> | アトラス対象マテリアル |
| `IslandSizePriorityTuner` | List\<IIslandSizePriorityTuner\> | アイランドサイズ優先度調整 (SerializeReference) |
| `MergeMaterialGroups` | List\<MaterialMergeGroup\> | マテリアル統合グループ |
| `MergeMaterialGroups.Array.data[n].Group` | List\<Material\> | 統合グループ |
| `MergeMaterialGroups.Array.data[n].Reference` | Material | 参照マテリアル (nullable) |
| `AllMaterialMergeReference` | Material | 全マテリアル統合参照 (nullable) |
| `AtlasSetting` | AtlasSetting | アトラス設定 (下記参照) |

**AtlasSetting (Serializable class)**:
| propertyPath | 型 | 説明 |
|---|---|---|
| `AtlasSetting.AtlasTextureSize` | int | アトラスサイズ (デフォルト: 2048, PowerOfTwo) |
| `AtlasSetting.CustomAspect` | bool | カスタムアスペクト |
| `AtlasSetting.AtlasTextureHeightSize` | int | 高さサイズ (デフォルト: 2048, PowerOfTwo) |
| `AtlasSetting.AtlasTargetUVChannel` | UVChannel | 対象 UV チャンネル (デフォルト: UV0) |
| `AtlasSetting.UsePrimaryMaximumTexture` | bool | 最大テクスチャを基準に使用 |
| `AtlasSetting.PrimaryTextureProperty` | PropertyName | 基準テクスチャプロパティ |
| `AtlasSetting.ForceSizePriority` | bool | サイズ優先度強制 |
| `AtlasSetting.IslandPadding` | float | アイランドパディング 0-0.05 (デフォルト: 0.01) |
| `AtlasSetting.IncludeDisabledRenderer` | bool | 無効レンダラー含む。[FormerlySerializedAs("IncludeDisableRenderer")] |
| `AtlasSetting.BackGroundColor` | Color | 背景色 (デフォルト: white) |
| `AtlasSetting.PixelNormalize` | bool | ピクセル正規化 (デフォルト: true) |
| `AtlasSetting.DownScaleAlgorithm` | string | ダウンスケールアルゴリズム |
| `AtlasSetting.ForceSetTexture` | bool | テクスチャ強制設定 |
| `AtlasSetting.AtlasIslandRelocator` | IIslandRelocatorProvider | アイランド配置アルゴリズム (SerializeReference) |
| `AtlasSetting.TextureFineTuning` | List\<ITextureFineTuning\> | テクスチャ微調整リスト (SerializeReference) |

#### AtlasTextureExperimentalFeature
| propertyPath | 型 | 説明 |
|---|---|---|
| `UnsetTextures` | List\<TextureSelector\> | 除外テクスチャ |
| `TextureIndividualFineTuning` | List\<TextureIndividualTuning\> | テクスチャ個別微調整 |
| `AutoTextureSizeSetting` | bool | 自動テクスチャサイズ |
| `AutoReferenceCopySetting` | bool | 自動参照コピー |
| `AutoMergeTextureSetting` | bool | 自動マージテクスチャ |

#### MultiLayerImageCanvas
| propertyPath | 型 | 説明 |
|---|---|---|
| `TargetTexture` | TextureSelector | 対象テクスチャ。[FormerlySerializedAs("TextureSelector")] |
| `tttImportedCanvasDescription` | TTTImportedCanvasDescription | PSD インポート情報 (HideInInspector) |

#### TextureBlender
| propertyPath | 型 | 説明 |
|---|---|---|
| `TargetTexture` | TextureSelector | 対象テクスチャ |
| `BlendTexture` | Texture2D | 合成テクスチャ (nullable) |
| `Color` | Color | カラー乗算 (デフォルト: white) |
| `BlendTypeKey` | string | 合成モード |

#### ColorDifferenceChanger
| propertyPath | 型 | 説明 |
|---|---|---|
| `TargetTexture` | TextureSelector | 対象テクスチャ |
| `DifferenceSourceColor` | Color | 差分元色 (デフォルト: gray) |
| `TargetColor` | Color | ターゲット色 (デフォルト: gray) |

#### MaterialModifier
| propertyPath | 型 | 説明 |
|---|---|---|
| `TargetMaterial` | Material | 対象マテリアル |
| `IsOverrideShader` | bool | シェーダー上書き |
| `OverrideShader` | Shader | 上書きシェーダー (nullable) |
| `IsOverrideRenderQueue` | bool | RenderQueue 上書き |
| `OverrideRenderQueue` | int | RenderQueue 値 (デフォルト: 2000) |
| `OverrideProperties` | List\<MaterialProperty\> | 上書きプロパティリスト |

#### MaterialOverrideTransfer
| propertyPath | 型 | 説明 |
|---|---|---|
| `TargetMaterial` | Material | 対象マテリアル |
| `MaterialVariantSource` | Material | バリアントソースマテリアル |

#### TextureConfigurator
| propertyPath | 型 | 説明 |
|---|---|---|
| `TargetTexture` | TextureSelector | 対象テクスチャ |
| `OverrideTextureSetting` | bool | テクスチャ設定上書き |
| `TextureSize` | int | テクスチャサイズ (デフォルト: 2048, PowerOfTwo) |
| `DownScaleAlgorithm` | string | ダウンスケールアルゴリズム |
| `MipMap` | bool | MipMap 使用 (デフォルト: true) |
| `MipMapGenerationAlgorithm` | string | MipMap 生成アルゴリズム |
| `OverrideCompression` | bool | 圧縮設定上書き |
| `CompressionSetting` | TextureCompressionData | 圧縮設定 |

#### NearTransTexture
| propertyPath | 型 | 説明 |
|---|---|---|
| `TransSourceRenderer` | Renderer | ソースレンダラー |
| `SourceMaterialSlot` | int | ソースマテリアルスロット (デフォルト: 0) |
| `SourcePropertyName` | PropertyName | ソースプロパティ名 |
| `TransTargetRenderer` | Renderer | ターゲットレンダラー |
| `TargetMaterialSlot` | int | ターゲットマテリアルスロット (デフォルト: 0) |
| `TargetPropertyName` | PropertyName | ターゲットプロパティ名 |
| `BlendTypeKey` | string | 合成モード |
| `FadeStartDistance` | float | フェード開始距離 (デフォルト: 0.01) |
| `MaxDistance` | float | 最大距離 (デフォルト: 0.1) |
| `Padding` | float | パディング (デフォルト: 5) |

#### UVCopy
| propertyPath | 型 | 説明 |
|---|---|---|
| `TargetMeshes` | List\<Mesh\> | 対象メッシュ |
| `CopySource` | UVChannel | コピー元 UV (デフォルト: UV0) |
| `CopyTarget` | UVChannel | コピー先 UV (デフォルト: UV1) |

#### ParallelProjectionWithLilToonDecal
| propertyPath | 型 | 説明 |
|---|---|---|
| `TargetMaterial` | Material | 対象マテリアル |
| `DecalTexture` | Texture2D | デカールテクスチャ |
| `Color` | Color | カラー (デフォルト: white) |
| `MSDFTexture` | bool | MSDF テクスチャ |
| `CullMode` | lilToonCullMode | カリングモード (Off=0, Front=1, Back=2) |
| `TransparentMode` | lilToonTransparentMode | 透過モード (None=0, Replace=1, Multiply=2, Add=3, Subtract=4) |
| `ShaderBlendingMode` | lilToonBlendingMode | 合成モード (Normal=0, Add=1, Screen=2, Multiply=3) |
| `ReplaceTextureTarget` | ReplaceTexture | 書込先 (Texture2nd=2, Texture3rd=3) |
| `WriteUVTarget` | int | 書込 UV チャンネル 1-3 (デフォルト: 1) |
| `IslandSelector` | AbstractIslandSelector | アイランドセレクター (nullable) |

#### PhaseDefinition
| propertyPath | 型 | 説明 |
|---|---|---|
| `TexTransPhase` | TexTransPhase | 実行フェーズ |

#### NegotiateAAOConfig
| propertyPath | 型 | 説明 |
|---|---|---|
| `UVEvacuationAndRegisterToAAO` | bool | UV 退避+AAO 登録 (デフォルト: true) |
| `OverrideEvacuationUVChannel` | bool | 退避 UV チャンネル上書き |
| `OverrideEvacuationUVChannelIndex` | int | 退避 UV チャンネル 1-7 (デフォルト: 7) |
| `AAORemovalToIsland` | bool | AAO 削除をアイランドに適用 (デフォルト: true)。[FormerlySerializedAs("AAORemovalToIslandDisabling")] |

#### MLI レイヤー共通 (AbstractLayer 継承)

全レイヤーが `Opacity`, `Clipping`, `BlendTypeKey`, `LayerMask` を持つ（上記共通型参照）。以下は追加フィールドのみ。

#### RasterLayer
| propertyPath | 型 | 説明 |
|---|---|---|
| `RasterTexture` | Texture2D | ラスタテクスチャ (nullable) |

#### RasterImportedLayer
| propertyPath | 型 | 説明 |
|---|---|---|
| `ImportedImage` | TTTImportedImage | PSD インポート画像 (nullable) |

#### LayerFolder
| propertyPath | 型 | 説明 |
|---|---|---|
| `PassThrough` | bool | パススルーモード |

#### SolidColorLayer
| propertyPath | 型 | 説明 |
|---|---|---|
| `Color` | Color | 単色 [ColorUsage(false)] (デフォルト: white) |

#### HSLAdjustmentLayer
| propertyPath | 型 | 説明 |
|---|---|---|
| `Hue` | float | 色相 -1 to 1 |
| `Saturation` | float | 彩度 -1 to 1 |
| `Lightness` | float | 明度 -1 to 1 |

#### HSVAdjustmentLayer
| propertyPath | 型 | 説明 |
|---|---|---|
| `Hue` | float | 色相 -1 to 1 |
| `Saturation` | float | 彩度 -1 to 1 |
| `Value` | float | 明度 -1 to 1 |

#### LevelAdjustmentLayer
| propertyPath | 型 | 説明 |
|---|---|---|
| `RGB` | Level | RGB チャンネル一括 |
| `Red` | Level | R チャンネル |
| `Green` | Level | G チャンネル |
| `Blue` | Level | B チャンネル |

**Level (Serializable class)**:
- `InputFloor`: float — 入力下限 0-0.99 (デフォルト: 0)
- `InputCeiling`: float — 入力上限 0.01-1 (デフォルト: 1)
- `Gamma`: float — ガンマ 0.1-9.9 (デフォルト: 1)
- `OutputFloor`: float — 出力下限 0-1 (デフォルト: 0)
- `OutputCeiling`: float — 出力上限 0-1 (デフォルト: 1)

#### SelectiveColoringAdjustmentLayer
| propertyPath | 型 | 説明 |
|---|---|---|
| `RedsCMYK` | Vector4 | 赤色域 CMYK 調整 |
| `YellowsCMYK` | Vector4 | 黄色域 CMYK 調整 |
| `GreensCMYK` | Vector4 | 緑色域 CMYK 調整 |
| `CyansCMYK` | Vector4 | シアン色域 CMYK 調整 |
| `BluesCMYK` | Vector4 | 青色域 CMYK 調整 |
| `MagentasCMYK` | Vector4 | マゼンタ色域 CMYK 調整 |
| `WhitesCMYK` | Vector4 | 白色域 CMYK 調整 |
| `NeutralsCMYK` | Vector4 | 中間色域 CMYK 調整 |
| `BlacksCMYK` | Vector4 | 黒色域 CMYK 調整 |
| `IsAbsolute` | bool | 絶対値モード |

#### ColorizeLayer
| propertyPath | 型 | 説明 |
|---|---|---|
| `Color` | Color | カラーライズ色 [ColorUsage(false)] (デフォルト: white) |

#### UnityGradationMapLayer
| propertyPath | 型 | 説明 |
|---|---|---|
| `Gradation` | Gradient | グラデーションマップ |

#### YAxisFixedGradientLayer
| propertyPath | 型 | 説明 |
|---|---|---|
| `Gradient` | Gradient | Y 軸グラデーション |

#### BoxIslandSelector
| propertyPath | 型 | 説明 |
|---|---|---|
| `IsAll` | bool | 全頂点がボックス内の場合のみ選択 |

#### SphereIslandSelector
| propertyPath | 型 | 説明 |
|---|---|---|
| `IsAll` | bool | 全頂点が球内の場合のみ選択 |

#### PinIslandSelector
| propertyPath | 型 | 説明 |
|---|---|---|
| `IslandSelectorRange` | float | 選択範囲 (デフォルト: 0.1) |

#### MaterialIslandSelector
| propertyPath | 型 | 説明 |
|---|---|---|
| `Materials` | List\<Material\> | 選択マテリアルリスト |

#### RendererIslandSelector
| propertyPath | 型 | 説明 |
|---|---|---|
| `RendererList` | List\<Renderer\> | 選択レンダラーリスト |

#### SubMeshIndexIslandSelector
| propertyPath | 型 | 説明 |
|---|---|---|
| `SelectSubMeshIndex` | int | 選択 SubMesh インデックス (デフォルト: 0) |

#### AimIslandSelector / IslandSelectorAND / OR / NOT / XOR
フィールドなし（Transform の位置/向きで動作、または子セレクターを論理演算）。

### 設計上の注意点

- **TexTransPhase が Unity コンポーネント単位で固定**: 各コンポーネントの `PhaseDefine` が返す値は固定。`PhaseDefinition` コンポーネントで子コンポーネントのフェーズを明示的にオーバーライドできる。
- **FormerlySerializedAs** が 4 箇所: `BackCulling`<-"SideChek"<-"SideCulling"、`TargetTexture`<-"TextureSelector"、`IncludeDisabledRenderer`<-"IncludeDisableRenderer"、`AAORemovalToIsland`<-"AAORemovalToIslandDisabling"。
- **SerializeReference 使用**: `AbstractLayer.LayerMask`、`AtlasSetting.TextureFineTuning`、`AtlasSetting.AtlasIslandRelocator`、`AtlasTexture.IslandSizePriorityTuner` は `[SerializeReference]` で多態的にシリアライズされる。YAML 上は `type: {class: ..., ns: ..., asm: ...}` 形式。
- **ITexTransToolStableComponent**: SimpleDecal, SingleGradationDecal, AtlasTexture, TextureBlender, PhaseDefinition, BoxIslandSelector, SphereIslandSelector, PinIslandSelector のみ安定。それ以外は実験的。
- **マーカーコンポーネント**: DomainDefinition, PreviewGroup, IsActiveInheritBreaker はフィールドなし。存在のみで機能する。
- **MLI レイヤー順序**: Transform の子は下（最後のインデックス）から上に合成される。Photoshop のレイヤーパネルと逆順に相当。
- **依存パッケージ**: `net.rs64.tex-trans-core` (コア計算エンジン) は別パッケージ。`TTCE-Unity` ディレクトリに WebGPU/GPU 実装がバンドルされる。

## 実運用で学んだこと

(なし -- Phase 3-4 の検証で追記する)
