---
tool: avatar-compressor
version_tested: "0.6.0"
last_updated: 2026-03-26
confidence: medium
---

# Avatar Compressor

## 概要 (L1)

非破壊アバターテクスチャ圧縮ツール。NDMF プラグインとしてビルド時に動作し、テクスチャの複雑度を解析して最適な圧縮フォーマットとリサイズ倍率を自動選択する。最終アバターにはランタイムコンポーネントが残らない (`IEditorOnly`)。

**解決する問題**: VRChat アバターのテクスチャ VRAM 使用量の削減。テクスチャごとの複雑度に応じて圧縮レベルを変えることで、一律圧縮より品質劣化を抑えつつファイルサイズを削減する。

**NDMF との関係**: NDMF >= 1.10.0 に依存。`BuildPhase.Optimizing` フェーズで実行される。実行順序は Modular Avatar の後、TexTransTool / Avatar Optimizer の前。

**プラットフォーム**: VRChat アバター (VRChat SDK Avatars >= 3.10.0)。PC (DXT/BC) と Quest (ASTC) の圧縮フォーマットを自動選択する。

**最新バージョン**: 0.6.0 (2026-03-06)。VPM リポジトリ `https://vpm.limitex.dev/` から配布。GitHub: `https://github.com/Limitex/avatar-compressor`。

**パッケージ名**: `dev.limitex.avatar-compressor`

## コンポーネント一覧 (L1->L2)

### ランタイムコンポーネント

| コンポーネント名 | 型 | 用途 | 典型的な使用場面 |
|---|---|---|---|
| TextureCompressor | MonoBehaviour, IEditorOnly | テクスチャ圧縮の設定を保持する | アバタールート GameObject に配置。プリセット選択またはカスタム設定で圧縮パラメーターを指定 |

`[DisallowMultipleComponent]` — アバターに1つだけ配置する。複数存在する場合、最初のコンポーネントの設定のみ使用される。アバタールート以外に配置すると警告が出る。

### ScriptableObject アセット

| アセット名 | 型 | 用途 |
|---|---|---|
| CustomTextureCompressorPreset | ScriptableObject | カスタムプリセット設定を保存・共有する。複数アバター・プロジェクト間で再利用可能 |

Assets メニューの `Avatar Compressor/Texture Compressor/CustomTextureCompressorPreset` から作成する。`MenuPath` を設定するとカスタムプリセットメニューに表示される。`Lock` フラグでプリセットの編集を保護できる。

### 内蔵プリセット

6 段階の圧縮プリセット + 5 段階の中間カスタムプリセットを提供する。

| プリセット | Strategy | MaxDivisor | HighComplexityThreshold | LowComplexityThreshold | MinSourceSize | HQ Format |
|---|---|---|---|---|---|---|
| HighQuality | Combined | 2 | 0.3 | 0.1 | 1024 | Yes |
| Quality | Combined | 4 | 0.5 | 0.15 | 512 | Yes |
| **Balanced** (default) | Combined | 8 | 0.7 | 0.2 | 256 | Yes |
| Aggressive | Fast | 8 | 0.8 | 0.3 | 128 | No |
| Maximum | Fast | 16 | 0.9 | 0.4 | 64 | No |
| Custom | (user) | (user) | (user) | (user) | (user) | (user) |

中間カスタムプリセット (Built-in): `High Quality+`, `Quality+`, `Balanced+`, `Aggressive+`, `Maximum+` がパッケージに同梱されている (Lock=true)。

### 複雑度解析ストラテジー

| StrategyType | 説明 |
|---|---|
| Fast | 高速解析。サンプリングベース |
| HighAccuracy | 高精度解析。全ピクセル解析 |
| Perceptual | 知覚ベース解析。人間の視覚特性を考慮 |
| Combined (default) | 上記3つの加重平均。`FastWeight` / `HighAccuracyWeight` / `PerceptualWeight` で重みを制御 |

### 圧縮フォーマット

| プラットフォーム | 低複雑度 | 中複雑度 | 高複雑度 (HQ有効時) | ノーマルマップ |
|---|---|---|---|---|
| Desktop (PC) | DXT1 | DXT5 | BC7 | BC5 (alpha無し) / BC7 (alpha有り) |
| Mobile (Quest) | ASTC_8x8 | ASTC_6x6 | ASTC_4x4 | ASTC_4x4 |

### NDMF パイプライン実行順序

```
Modular Avatar → LAC Texture Compressor → TexTransTool → Avatar Optimizer
```

`BuildPhase.Optimizing` で実行。`TextureCompressorPass` が全テクスチャを処理し、`AnimatorServicesContext` 経由でアニメーション参照も更新する。

## 操作パターン (L2)

### 基本的なテクスチャ圧縮

1. アバタールート GameObject に `TextureCompressor` コンポーネントを追加 (メニュー: `Avatar Compressor/LAC Texture Compressor`)
2. プリセットを選択 (デフォルト: Balanced)
3. Editor UI のプレビューで圧縮後の VRAM 推定値を確認
4. アバターをビルド — NDMF がビルド時に自動圧縮

### テクスチャ個別制御 (Frozen Textures)

1. プレビューリストから特定テクスチャの Freeze ボタンをクリック
2. 個別に Divisor (1/2/4/8/16)、Format (Auto/DXT1/DXT5/BC5/BC7/ASTC各種)、Skip (圧縮除外) を設定
3. Frozen Textures は自動圧縮の対象外となり、指定した設定で固定処理される
4. テクスチャは GUID で識別されるため、ファイル移動・リネームに耐性がある

## SerializedProperty リファレンス (L3)

ソースバージョン: 0.6.0 (Shiratsume プロジェクト)
検証方法: Runtime/*.cs ソースコード読み + .meta ファイルの GUID 抽出 (inspect 実測なし -> confidence: medium)

### Script GUID テーブル

| コンポーネント / アセット | GUID | 備考 |
|---|---|---|
| TextureCompressor | `cca75ca1a6d56f94990dc957069dc8a9` | MonoBehaviour, IEditorOnly |
| CustomTextureCompressorPreset | `3e08ef92a8ff53745a49f46c30027804` | ScriptableObject |
| FrozenTextureSettings | `bc848fb8600cc654c842f341028f89d0` | Serializable class (独立 GUID あるが MonoBehaviour ではない) |
| ExcludedPathPresets | `f87d3fe656dccc8468a7e59db0f9368c` | static class (コンポーネントではない) |

### TextureCompressor フィールド

全フィールドが `public` で宣言されている (`[SerializeField]` ではなく直接 public)。

| propertyPath | 型 | デフォルト値 | 説明 |
|---|---|---|---|
| `Preset` | CompressorPreset (enum) | Balanced (2) | プリセット選択。HighQuality=0, Quality=1, Balanced=2, Aggressive=3, Maximum=4, Custom=5 |
| `CustomPresetAsset` | CustomTextureCompressorPreset | null | カスタムプリセットアセット参照 |
| `Strategy` | AnalysisStrategyType (enum) | Combined (3) | 複雑度解析ストラテジー。Fast=0, HighAccuracy=1, Perceptual=2, Combined=3 |
| `FastWeight` | float [0-1] | 0.3 | Combined ストラテジーの Fast 重み |
| `HighAccuracyWeight` | float [0-1] | 0.5 | Combined ストラテジーの HighAccuracy 重み |
| `PerceptualWeight` | float [0-1] | 0.2 | Combined ストラテジーの Perceptual 重み |
| `HighComplexityThreshold` | float [0-1] | 0.7 | この値以上の複雑度テクスチャは最小圧縮 |
| `LowComplexityThreshold` | float [0-1] | 0.2 | この値以下の複雑度テクスチャは最大圧縮 |
| `MinDivisor` | int [1-4] | 1 | 最小リサイズ除数 (1=リサイズなし) |
| `MaxDivisor` | int [2-16] | 8 | 最大リサイズ除数 |
| `MaxResolution` | int | 2048 | 出力最大解像度 |
| `MinResolution` | int | 64 | 出力最小解像度 |
| `ForcePowerOfTwo` | bool | true | 出力を 2 のべき乗に強制 |
| `ProcessMainTextures` | bool | true | メインテクスチャ (_MainTex, _BaseMap 等) を処理 |
| `ProcessNormalMaps` | bool | true | ノーマルマップを処理 |
| `ProcessEmissionMaps` | bool | true | エミッションテクスチャを処理 |
| `ProcessOtherTextures` | bool | true | その他テクスチャ (metallic, roughness 等) を処理 |
| `MinSourceSize` | int | 256 | この解像度より大きいテクスチャのみ処理 |
| `SkipIfSmallerThan` | int | 128 | この解像度以下のテクスチャをスキップ |
| `ExcludedPaths` | List\<string\> | ["Packages/com.vrcfury.temp/"] | 除外パスプレフィックスリスト |
| `TargetPlatform` | CompressionPlatform (enum) | Auto (0) | ターゲットプラットフォーム。Auto=0, Desktop=1, Mobile=2 |
| `UseHighQualityFormatForHighComplexity` | bool | true | 高複雑度テクスチャに BC7/ASTC_4x4 を使用 |
| `EnableLogging` | bool | true | ビルドログ出力 |
| `FrozenTextures` | List\<FrozenTextureSettings\> | [] | 個別圧縮設定リスト |

### FrozenTextureSettings フィールド

`[Serializable]` class。TextureCompressor の `FrozenTextures` 配列要素。

| propertyPath | 型 | デフォルト値 | 説明 |
|---|---|---|---|
| `FrozenTextures.Array.data[n].TextureGuid` | string | null | テクスチャアセットの GUID。[FormerlySerializedAs("TexturePath")] |
| `FrozenTextures.Array.data[n].Divisor` | int | 1 | リサイズ除数。有効値: 1, 2, 4, 8, 16 |
| `FrozenTextures.Array.data[n].Format` | FrozenTextureFormat (enum) | Auto (0) | フォーマットオーバーライド。Auto=0, DXT1=1, DXT5=2, BC5=3, BC7=4, ASTC_4x4=5, ASTC_6x6=6, ASTC_8x8=7 |
| `FrozenTextures.Array.data[n].Skip` | bool | false | true で圧縮を完全スキップ |

### CustomTextureCompressorPreset フィールド

ScriptableObject。TextureCompressor と同じ圧縮パラメーターに加え、プリセット管理用フィールドを持つ。

| propertyPath | 型 | デフォルト値 | 説明 |
|---|---|---|---|
| `Lock` | bool | false | プリセット編集ロック |
| `Description` | string | "" | プリセット説明文 [TextArea(2,4)] |
| `MenuPath` | string | "" | カスタムメニュー内のパス (例: "Quest/Optimized")。空=メニュー非表示 |
| `MenuOrder` | int | 1000 | メニュー表示順。小さい値が先 |
| `Strategy` | AnalysisStrategyType | Combined | (TextureCompressor と同一) |
| `FastWeight` | float | 0.3 | (TextureCompressor と同一) |
| `HighAccuracyWeight` | float | 0.5 | (TextureCompressor と同一) |
| `PerceptualWeight` | float | 0.2 | (TextureCompressor と同一) |
| `HighComplexityThreshold` | float | 0.7 | (TextureCompressor と同一) |
| `LowComplexityThreshold` | float | 0.2 | (TextureCompressor と同一) |
| `MinDivisor` | int | 1 | (TextureCompressor と同一) |
| `MaxDivisor` | int | 8 | (TextureCompressor と同一) |
| `MaxResolution` | int | 2048 | (TextureCompressor と同一) |
| `MinResolution` | int | 64 | (TextureCompressor と同一) |
| `ForcePowerOfTwo` | bool | true | (TextureCompressor と同一) |
| `MinSourceSize` | int | 256 | (TextureCompressor と同一) |
| `SkipIfSmallerThan` | int | 128 | (TextureCompressor と同一) |
| `TargetPlatform` | CompressionPlatform | Auto | (TextureCompressor と同一) |
| `UseHighQualityFormatForHighComplexity` | bool | true | (TextureCompressor と同一) |

### 設計上の注意点

- **IEditorOnly 実装**: `TextureCompressor` は `VRC.SDKBase.IEditorOnly` を実装しており、ビルド時にコンポーネントが削除される。NDMF パス内で処理が完了した後、`CleanupComponents` で明示的に Destroy される。
- **FormerlySerializedAs**: `FrozenTextureSettings.TextureGuid` に `[FormerlySerializedAs("TexturePath")]` がある。v0.4.0 で GUID ベース識別に移行した際のマイグレーション用。
- **フィールドは全て public**: `[SerializeField] private` ではなく `public` フィールドで宣言されている。Inspector のカスタムエディタ (`TextureCompressorEditor`) から直接アクセスされる設計。
- **CustomPresetAsset は ObjectReference**: TextureCompressor から CustomTextureCompressorPreset への ScriptableObject 参照。prefab-sentinel の `inspect_wiring` で配線として検出される。
- **ExcludedPaths のデフォルト**: `ExcludedPathPresets.GetDefaultPaths()` で初期化される。現在は `Packages/com.vrcfury.temp/` のみ。
- **Divisor のバリデーション**: `SetFrozenSettings` で有効値 (1, 2, 4, 8, 16) を検証し、無効値は最近接有効値に丸められる。

## 実運用で学んだこと

(なし -- Phase 3-4 の検証で追記する)
