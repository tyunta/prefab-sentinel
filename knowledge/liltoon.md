---
tool: liltoon
version_tested: "2.3.2"
last_updated: 2026-03-28
confidence: medium
---

# lilToon

## 概要 (L1)

VRChat 向けの多機能トゥーンシェーダー。Built-in RP / URP / HDRP の 3 パイプラインに対応し、単一パッケージで動作する。Inspector UI からレンダリングモード（Opaque / Cutout / Transparent / Fur / Gem / Refraction）を切り替えると、内部で適切な Hidden シェーダーに自動差し替えされるため、ユーザーが見えるシェーダー選択は原則 `lilToon` 1 つ。

**解決する問題**: VRChat アバターで多用されるトゥーン表現（影色制御、リムライト、マットキャップ、アウトライン、ファー、エミッション、グリッター等）を、1 シェーダーに統合して提供する。個別シェーダーを組み合わせる煩雑さを排除し、Inspector で全機能をプリセット込みで操作できる。

**レンダリングパイプライン**: Built-in RP を主対象とし、URP / HDRP もサポート。VRChat は Built-in RP を使用するため、VRChat アバターでの利用は Built-in RP 前提。

**VRChat との関係**: VRChat SDK に依存しないが、VRChat のシェーダーフォールバック（`VRCFallback` タグ）に対応。v1.10.0 以降で VRC Light Volumes をサポートし、v2.0.0 で VRC Light Volumes 2.0.0 に更新。VPM リポジトリ `https://lilxyzw.github.io/vpm-repos/vpm.json` から配布。

**NDMF 連携**: NDMF に依存しないが、NDMF の `Apply on Play` 時にシェーダー最適化をスキップするオプションあり（v1.8.4+）。ビルド時に未使用機能のシェーダーキーワードをストリップして最適化する。

**最新バージョン**: 2.3.2 (2025-10-29)。Unity 2022.3 以上を要求。

## シェーダーバリアント一覧 (L1 -> L2)

lilToon のシェーダーは「ユーザーが選択するエントリポイント」と「Inspector が内部で差し替える Hidden シェーダー」の 2 層構造。

### エントリポイントシェーダー（ユーザーが直接選択可能）

| シェーダー名 | 用途 | GUID |
|---|---|---|
| `lilToon` | メインシェーダー。Inspector でレンダリングモードを切り替えると Hidden に差し替わる | `df12117ecd77c31469c224178886498e` |
| `_lil/lilToonMulti` | Shader Feature でバリアントを制御する統合版。GPU Instancing 対応 | `9294844b15dca184d914a632279b24e1` |
| `_lil/[Optional] lilToonFakeShadow` | 疑似影（地面投影影） | `00795bf598b44dc4e9bd363348e77085` |
| `_lil/[Optional] lilToonOutlineOnly` | アウトラインのみ描画（Opaque） | `fba17785d6b2c594ab6c0303c834da65` |
| `_lil/[Optional] lilToonOutlineOnlyCutout` | アウトラインのみ描画（Cutout） | `3b3957e6c393b114bab6f835b4ed8f5d` |
| `_lil/[Optional] lilToonOutlineOnlyTransparent` | アウトラインのみ描画（Transparent） | `0c762f24b85918a49812fc5690619178` |
| `_lil/[Optional] lilToonOverlay` | オーバーレイ描画 | `94274b8ef5d3af842b9427384cba3a8f` |
| `_lil/[Optional] lilToonOverlayOnePass` | オーバーレイ描画（OnePass） | `33e950d038b8dfd4f824f3985c2abfb7` |
| `_lil/[Optional] lilToonLiteOverlay` | Lite 版オーバーレイ | `d28e4b78ba8368e49a44f86c0291df58` |
| `_lil/[Optional] lilToonLiteOverlayOnePass` | Lite 版オーバーレイ（OnePass） | `dc9ded9f9d6f16c4e92cbb8f4269ae31` |
| `_lil/[Optional] lilToonFurOnlyTransparent` | ファーのみ描画（Transparent） | `33aad051c4a3a844a8f9330addb86a97` |
| `_lil/[Optional] lilToonFurOnlyCutout` | ファーのみ描画（Cutout） | `7ec9f85eb7ee04943adfe19c2ba5901f` |
| `_lil/[Optional] lilToonFurOnlyTwoPass` | ファーのみ描画（TwoPass） | `f8d9dfac6dbfaaf4c9c3aaf4bd8c955f` |

### Hidden シェーダー（Inspector が自動差し替え）

レンダリングモードの組み合わせで決まる。命名規則: `Hidden/lilToon{Variant}{RenderingMode}{Option}`

**Standard バリアント:**

| レンダリングモード | アウトラインなし | アウトライン付き |
|---|---|---|
| Opaque | `lilToon` (エントリポイント) | `Hidden/lilToonOutline` |
| Cutout | `Hidden/lilToonCutout` | `Hidden/lilToonCutoutOutline` |
| Transparent | `Hidden/lilToonTransparent` | `Hidden/lilToonTransparentOutline` |
| OnePassTransparent | `Hidden/lilToonOnePassTransparent` | `Hidden/lilToonOnePassTransparentOutline` |
| TwoPassTransparent | `Hidden/lilToonTwoPassTransparent` | `Hidden/lilToonTwoPassTransparentOutline` |
| Refraction | `Hidden/lilToonRefraction` | - |
| RefractionBlur | `Hidden/lilToonRefractionBlur` | - |
| Fur | `Hidden/lilToonFur` | - |
| FurCutout | `Hidden/lilToonFurCutout` | - |
| FurTwoPass | `Hidden/lilToonFurTwoPass` | - |
| Gem | `Hidden/lilToonGem` | - |

**Tessellation バリアント**: `Hidden/lilToonTessellation{RenderingMode}{Outline}` -- Standard と同じモード展開。

**Lite バリアント**: `Hidden/lilToonLite{RenderingMode}{Outline}` -- Standard からアニソトロピー、反射、Glitter、Backlight、Parallax 等を除いた軽量版。

**Multi バリアント**: `Hidden/lilToonMulti{Outline|Fur|Gem|Refraction}` -- Shader Feature ベースの統合版。

### レンダリングモード (RenderingMode enum)

| 値 | 名前 | 説明 |
|---|---|---|
| 0 | Opaque | 不透明。アウトライン利用可 |
| 1 | Cutout | アルファテスト。`_Cutoff` で閾値制御 |
| 2 | Transparent | 半透明。Normal / OnePass / TwoPass のサブモードあり |
| 3 | Refraction | 屈折。背景テクスチャをゆがめる |
| 4 | RefractionBlur | 屈折 + ブラー |
| 5 | Fur | ファー描画（Shell 方式） |
| 6 | FurCutout | ファー（Cutout） |
| 7 | FurTwoPass | ファー（TwoPass） |
| 8 | Gem | 宝石。屈折 + キュビックサンプリング |

### TransparentMode サブモード

| 値 | 名前 | 説明 |
|---|---|---|
| 0 | Normal | 標準透過。前面・背面を 2 パスで描画 |
| 1 | OnePass | 1 パス描画。軽量だがソート問題あり |
| 2 | TwoPass | 2 パス描画。前面パスと背面パスが分離 |

## 操作パターン (L2)

### 基本セットアップ

1. マテリアルのシェーダーを `lilToon` に変更
2. `_MainTex` にメインテクスチャを設定
3. `_Color` でベースカラーを調整
4. Inspector でレンダリングモード (Opaque / Cutout / Transparent) を選択
5. 必要に応じてプリセット (Skin-Anime, Cloth-Outline 等) を適用

プリセット一覧: Skin (Anime / Flat / Illust / Outline / OutlineShadow), Cloth (Anime / Illust / Outline / Standard), Hair (Anime / Illust / Outline / OutlineRimLight / Standard), Inorganic (Glass / LiteGlass / Metal / Metal(MatCap)), Nature (Fur)

### アウトライン設定

1. Inspector で Outline を有効化（内部でシェーダーが `*Outline` バリアントに差し替え）
2. `_OutlineColor` でアウトライン色を設定
3. `_OutlineWidth` (Range 0-1) で太さ調整。`_OutlineFixWidth` (0-1) でカメラ距離による太さ変化を制御（1.0 = 画面上で常に同じ太さ）
4. `_OutlineWidthMask` テクスチャで部分的に太さを制御（顔など細部のアウトラインを消す）
5. `_OutlineVertexR2Width` で頂点カラーの R チャンネルによる太さ制御を設定

### エミッション設定

1. `_UseEmission` = 1 で有効化
2. `_EmissionMap` にエミッションテクスチャ設定
3. `_EmissionColor` (HDR) で色と強度を設定
4. `_EmissionBlend` で合成強度、`_EmissionBlendMode` で合成モードを指定
5. UV アニメーション: `_EmissionMap_ScrollRotate` で UV スクロール/回転
6. 点滅: `_EmissionBlink` (Vector4 = speed, strength, type, offset) で点滅アニメーション
7. グラデーション: `_EmissionUseGrad` + `_EmissionGradTex` で時間変化するグラデーション色
8. 2nd エミッションも同構造で独立設定可能 (`_UseEmission2nd` 系)

### 影色調整パターン

1. `_UseShadow` = 1 で有効化
2. 1st Shadow: `_ShadowColor` + `_ShadowBorder` (境界位置) + `_ShadowBlur` (ぼかし幅)
3. 2nd Shadow: `_Shadow2ndColor` + `_Shadow2ndBorder` + `_Shadow2ndBlur` で多段影
4. 3rd Shadow: `_Shadow3rdColor` 系で 3 段目（任意）
5. `_ShadowColorTex` でテクスチャによる影色指定（影色をテクスチャで直接塗る手法）
6. `_ShadowMainStrength` でメインカラーの影への反映度を制御
7. `_ShadowReceive` で Unity のリアルタイムシャドウの受け取り強度を調整
8. `_ShadowBorderColor` + `_ShadowBorderRange` で影の境界線に色を付与（アニメ調の影際ハイライト）

### MatCap 設定パターン

1. `_UseMatCap` = 1 で有効化
2. `_MatCapTex` にスフィアマッピング用テクスチャを設定
3. `_MatCapColor` (HDR) で色・強度を調整
4. `_MatCapBlendMode` で合成モード (Normal=0, Add=1, Screen=2, Multiply=3)
5. `_MatCapZRotCancel` = 1 でカメラの Z 回転を無視（VR での安定性向上）
6. `_MatCapPerspective` = 1 でパースペクティブ補正
7. 2nd MatCap も同構造で独立設定可能（`_UseMatCap2nd` 系）

## シェーダープロパティ リファレンス (L3)

ソースバージョン: 2.3.2 (Shiratsume) + 1.9.0 (UnityTool_sample) 差分検証済み
検証方法: .shader ファイルの Properties ブロック読み + .hlsl の CBUFFER 宣言との突合。inspect 実測なし -> confidence: medium

### Base / Lighting

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_Invisible` | Int (Bool) | 0 | メッシュを非表示にする |
| `_AsUnlit` | Range(0,1) | 0 | Unlit 寄りにする度合い。1.0 で完全 Unlit |
| `_Cutoff` | Range(-0.001,1.001) | 0.5 | アルファテスト閾値 |
| `_SubpassCutoff` | Range(0,1) | 0.5 | サブパス（背面描画等）のアルファテスト閾値 |
| `_FlipNormal` | Int (Bool) | 0 | 背面の法線を反転 |
| `_ShiftBackfaceUV` | Int (Bool) | 0 | 背面の UV をシフト |
| `_BackfaceForceShadow` | Range(0,1) | 0 | 背面を強制的に影にする |
| `_BackfaceColor` | Color (HDR) | (0,0,0,0) | 背面の色 |
| `_VertexLightStrength` | Range(0,1) | 0 | 頂点ライトの影響度 |
| `_LightMinLimit` | Range(0,1) | 0.05 | 最低明るさの下限。暗すぎる環境での視認性確保 |
| `_LightMaxLimit` | Range(0,10) | 1 | 明るさの上限 |
| `_BeforeExposureLimit` | Float | 10000 | 露出制限（HDRP 向け） |
| `_MonochromeLighting` | Range(0,1) | 0 | ライティングをモノクロ化する度合い |
| `_AlphaBoostFA` | Range(1,100) | 10 | Forward Add パスでのアルファブースト |
| `_lilDirectionalLightStrength` | Range(0,1) | 1 | ディレクショナルライトの影響度 |
| `_LightDirectionOverride` | Vector4 | (0.001,0.002,0.001,0) | ライト方向のオーバーライド (xyz=方向, w=0 で無効) |
| `_AAStrength` | Range(0,1) | 1 | アンチエイリアス影の強度 |
| `_UseDither` | Int (Bool) | 0 | ディザリング有効化 |
| `_DitherTex` | 2D | "white" | ディザパターンテクスチャ |
| `_DitherMaxValue` | Float | 255 | ディザの最大値 |
| `_EnvRimBorder` | Range(0,3) | 3.0 | VRC Light Volumes リムライトの境界 (v2.1.0+) |
| `_EnvRimBlur` | Range(0,1) | 0.35 | VRC Light Volumes リムライトのぼかし (v2.1.0+) |

### Main Color (1st)

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_Color` | Color (HDR) | (1,1,1,1) | メインカラー。[MainColor] |
| `_MainTex` | 2D | "white" | メインテクスチャ。[MainTexture] |
| `_MainTex_ScrollRotate` | Vector4 | (0,0,0,0) | UV スクロール/回転 (x=ScrollX, y=ScrollY, z=Angle, w=Rotation) |
| `_MainTexHSVG` | Vector4 | (0,1,1,1) | 色相/彩度/明度/ガンマ補正 (H,S,V,G) |
| `_MainGradationStrength` | Range(0,1) | 0 | グラデーションマップの適用強度 |
| `_MainGradationTex` | 2D | "white" | グラデーションマップ |
| `_MainColorAdjustMask` | 2D | "white" | 色調整のマスクテクスチャ |

### Main Color 2nd / 3rd

2nd と 3rd は同一構造。プレフィックスが `_Main2nd` / `_Main3rd` で異なる。以下は 2nd の例。

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_UseMain2ndTex` | Int (Bool) | 0 | 2nd カラー有効化 |
| `_Color2nd` | Color (HDR) | (1,1,1,1) | 2nd カラー |
| `_Main2ndTex` | 2D | "white" | 2nd テクスチャ |
| `_Main2ndTexAngle` | Float | 0 | 回転角度 |
| `_Main2ndTex_ScrollRotate` | Vector4 | (0,0,0,0) | UV スクロール/回転 |
| `_Main2ndTex_UVMode` | Int (Enum) | 0 | UV モード (0=UV0, 1=UV1, 2=UV2, 3=UV3, 4=MatCap) |
| `_Main2ndTex_Cull` | Int (Enum) | 0 | カリングモード |
| `_Main2ndTexDecalAnimation` | Vector4 | (1,1,1,30) | デカールアニメーション (x=TilesX, y=TilesY, z=Frames, w=FPS) |
| `_Main2ndTexDecalSubParam` | Vector4 | (1,1,0,1) | デカルサブパラメーター |
| `_Main2ndTexIsDecal` | Int (Bool) | 0 | デカールモード |
| `_Main2ndTexIsLeftOnly` | Int (Bool) | 0 | 左半分のみ |
| `_Main2ndTexIsRightOnly` | Int (Bool) | 0 | 右半分のみ |
| `_Main2ndTexShouldCopy` | Int (Bool) | 0 | ミラーコピー |
| `_Main2ndTexShouldFlipMirror` | Int (Bool) | 0 | ミラー反転 |
| `_Main2ndTexShouldFlipCopy` | Int (Bool) | 0 | コピー反転 |
| `_Main2ndTexIsMSDF` | Int (Bool) | 0 | MSDF テクスチャモード |
| `_Main2ndBlendMask` | 2D | "white" | ブレンドマスク |
| `_Main2ndTexBlendMode` | Int (Enum) | 0 | ブレンドモード (0=Normal, 1=Add, 2=Screen, 3=Multiply) |
| `_Main2ndTexAlphaMode` | Int (Enum) | 0 | アルファモード |
| `_Main2ndEnableLighting` | Range(0,1) | 1 | ライティング適用度 |
| `_Main2ndDistanceFade` | Vector4 | (0.1,0.01,0,0) | 距離フェード設定 |
| `_Main2ndDissolveMask` | 2D | "white" | ディゾルブマスク |
| `_Main2ndDissolveNoiseMask` | 2D | "gray" | ディゾルブノイズ |
| `_Main2ndDissolveNoiseStrength` | Float | 0.1 | ノイズ強度 |
| `_Main2ndDissolveColor` | Color (HDR) | (1,1,1,1) | ディゾルブ色 |
| `_Main2ndDissolveParams` | Vector4 | (0,0,0.5,0.1) | ディゾルブパラメーター |
| `_Main2ndDissolvePos` | Vector4 | (0,0,0,0) | ディゾルブ位置 |

### Alpha Mask

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_AlphaMaskMode` | Int (Enum) | 0 | アルファマスクモード |
| `_AlphaMask` | 2D | "white" | アルファマスクテクスチャ |
| `_AlphaMaskScale` | Float | 1 | スケール |
| `_AlphaMaskValue` | Float | 0 | オフセット |

### Normal Map

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_UseBumpMap` | Int (Bool) | 0 | ノーマルマップ有効化 |
| `_BumpMap` | 2D (Normal) | "bump" | ノーマルマップテクスチャ |
| `_BumpScale` | Range(-10,10) | 1 | 強度 |
| `_UseBump2ndMap` | Int (Bool) | 0 | 2nd ノーマルマップ有効化 |
| `_Bump2ndMap` | 2D (Normal) | "bump" | 2nd ノーマルマップ |
| `_Bump2ndMap_UVMode` | Int (Enum) | 0 | UV モード (0-3) |
| `_Bump2ndScale` | Range(-10,10) | 1 | 2nd 強度 |
| `_Bump2ndScaleMask` | 2D | "white" | 2nd 強度マスク |

### Anisotropy

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_UseAnisotropy` | Int (Bool) | 0 | アニソトロピー有効化 |
| `_AnisotropyTangentMap` | 2D (Normal) | "bump" | タンジェントマップ |
| `_AnisotropyScale` | Range(-1,1) | 1 | スケール |
| `_AnisotropyScaleMask` | 2D | "white" | スケールマスク |
| `_AnisotropyTangentWidth` | Range(0,10) | 1 | タンジェント方向の幅 |
| `_AnisotropyBitangentWidth` | Range(0,10) | 1 | バイタンジェント方向の幅 |
| `_AnisotropyShift` | Range(-10,10) | 0 | シフト |
| `_AnisotropyShiftNoiseScale` | Range(-1,1) | 0 | ノイズ強度 |
| `_AnisotropySpecularStrength` | Range(0,10) | 1 | スペキュラ強度 |
| `_Anisotropy2ndTangentWidth` | Range(0,10) | 1 | 2nd タンジェント幅 |
| `_Anisotropy2ndBitangentWidth` | Range(0,10) | 1 | 2nd バイタンジェント幅 |
| `_Anisotropy2ndShift` | Range(-10,10) | 0 | 2nd シフト |
| `_Anisotropy2ndShiftNoiseScale` | Range(-1,1) | 0 | 2nd ノイズ強度 |
| `_Anisotropy2ndSpecularStrength` | Range(0,10) | 0 | 2nd スペキュラ強度 |
| `_AnisotropyShiftNoiseMask` | 2D | "white" | ノイズマスク |
| `_Anisotropy2Reflection` | Int (Bool) | 0 | Reflection に適用 |
| `_Anisotropy2MatCap` | Int (Bool) | 0 | MatCap に適用 |
| `_Anisotropy2MatCap2nd` | Int (Bool) | 0 | MatCap 2nd に適用 |

### Shadow

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_UseShadow` | Int (Bool) | 0 | 影有効化 |
| `_ShadowStrength` | Range(0,1) | 1 | 影の強度 |
| `_ShadowStrengthMask` | 2D | "white" | 強度マスク |
| `_ShadowStrengthMaskLOD` | Range(0,1) | 0 | 強度マスク LOD |
| `_ShadowBorderMask` | 2D | "white" | 境界マスク |
| `_ShadowBorderMaskLOD` | Range(0,1) | 0 | 境界マスク LOD |
| `_ShadowBlurMask` | 2D | "white" | ぼかしマスク |
| `_ShadowBlurMaskLOD` | Range(0,1) | 0 | ぼかしマスク LOD |
| `_ShadowAOShift` | Vector4 | (1,0,1,0) | AO シフト (1st Scale, 1st Offset, 2nd Scale, 2nd Offset) |
| `_ShadowAOShift2` | Vector4 | (1,0,1,0) | AO シフト (3rd Scale, 3rd Offset) |
| `_ShadowPostAO` | Int (Bool) | 0 | 境界プロパティを無視して AO 後に適用 |
| `_ShadowColorType` | Int (Enum) | 0 | 影色タイプ |
| `_ShadowColor` | Color | (0.82,0.76,0.85,1) | 1st 影色 |
| `_ShadowColorTex` | 2D | "black" | 1st 影色テクスチャ |
| `_ShadowNormalStrength` | Range(0,1) | 1.0 | 法線の影響度 |
| `_ShadowBorder` | Range(0,1) | 0.5 | 影の境界位置 |
| `_ShadowBlur` | Range(0,1) | 0.1 | ぼかし幅 |
| `_ShadowReceive` | Range(0,1) | 0 | リアルタイムシャドウ受け取り強度 |
| `_Shadow2ndColor` | Color | (0.68,0.66,0.79,1) | 2nd 影色 |
| `_Shadow2ndColorTex` | 2D | "black" | 2nd 影色テクスチャ |
| `_Shadow2ndNormalStrength` | Range(0,1) | 1.0 | 2nd 法線影響度 |
| `_Shadow2ndBorder` | Range(0,1) | 0.15 | 2nd 境界位置 |
| `_Shadow2ndBlur` | Range(0,1) | 0.1 | 2nd ぼかし幅 |
| `_Shadow2ndReceive` | Range(0,1) | 0 | 2nd リアルタイムシャドウ受け取り |
| `_Shadow3rdColor` | Color | (0,0,0,0) | 3rd 影色 |
| `_Shadow3rdColorTex` | 2D | "black" | 3rd 影色テクスチャ |
| `_Shadow3rdNormalStrength` | Range(0,1) | 1.0 | 3rd 法線影響度 |
| `_Shadow3rdBorder` | Range(0,1) | 0.25 | 3rd 境界位置 |
| `_Shadow3rdBlur` | Range(0,1) | 0.1 | 3rd ぼかし幅 |
| `_Shadow3rdReceive` | Range(0,1) | 0 | 3rd リアルタイムシャドウ受け取り |
| `_ShadowBorderColor` | Color | (1,0.1,0,1) | 影境界の色（影際ハイライト） |
| `_ShadowBorderRange` | Range(0,1) | 0.08 | 影境界の色の範囲 |
| `_ShadowMainStrength` | Range(0,1) | 0 | メインカラーの影への反映度 |
| `_ShadowEnvStrength` | Range(0,1) | 0 | 環境光の影への反映度 |
| `_ShadowMaskType` | Int (Enum) | 0 | マスクタイプ |
| `_ShadowFlatBorder` | Range(-2,2) | 1 | フラットシャドウの境界 |
| `_ShadowFlatBlur` | Range(0.001,2) | 1 | フラットシャドウのぼかし |

### Rim Shade

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_UseRimShade` | Int (Bool) | 0 | リムシェード有効化 |
| `_RimShadeColor` | Color | (0.5,0.5,0.5,1) | リムシェード色 |
| `_RimShadeMask` | 2D | "white" | マスク |
| `_RimShadeNormalStrength` | Range(0,1) | 1.0 | 法線影響度 |
| `_RimShadeBorder` | Range(0,1) | 0.5 | 境界 |
| `_RimShadeBlur` | Range(0,1) | 1.0 | ぼかし |
| `_RimShadeFresnelPower` | Range(0.01,50) | 1.0 | フレネル指数 |

### Backlight

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_UseBacklight` | Int (Bool) | 0 | バックライト有効化 |
| `_BacklightColor` | Color (HDR) | (0.85,0.8,0.7,1) | 色 |
| `_BacklightColorTex` | 2D | "white" | テクスチャ |
| `_BacklightMainStrength` | Range(0,1) | 0 | メインカラーの反映度 |
| `_BacklightNormalStrength` | Range(0,1) | 1.0 | 法線影響度 |
| `_BacklightBorder` | Range(0,1) | 0.35 | 境界 |
| `_BacklightBlur` | Range(0,1) | 0.05 | ぼかし |
| `_BacklightDirectivity` | Float | 5.0 | 指向性 |
| `_BacklightViewStrength` | Range(0,1) | 1 | 視線方向の影響度 |
| `_BacklightReceiveShadow` | Int (Bool) | 1 | シャドウ受け取り |
| `_BacklightBackfaceMask` | Int (Bool) | 1 | 背面マスク |

### Reflection

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_UseReflection` | Int (Bool) | 0 | リフレクション有効化 |
| `_Smoothness` | Range(0,1) | 1 | スムーズネス |
| `_SmoothnessTex` | 2D | "white" | スムーズネステクスチャ |
| `_Metallic` | Range(0,1) | 0 | メタリック [Gamma] |
| `_MetallicGlossMap` | 2D | "white" | メタリックマップ |
| `_Reflectance` | Range(0,1) | 0.04 | 反射率 [Gamma] |
| `_GSAAStrength` | Range(0,1) | 0 | 幾何学的スペキュラ AA |
| `_ApplySpecular` | Int (Bool) | 1 | スペキュラ適用 |
| `_ApplySpecularFA` | Int (Bool) | 1 | マルチライトスペキュラ |
| `_SpecularToon` | Int (Bool) | 1 | トゥーンスペキュラ |
| `_SpecularNormalStrength` | Range(0,1) | 1.0 | 法線影響度 |
| `_SpecularBorder` | Range(0,1) | 0.5 | 境界 |
| `_SpecularBlur` | Range(0,1) | 0.0 | ぼかし |
| `_ApplyReflection` | Int (Bool) | 0 | 環境マップ反射適用 |
| `_ReflectionNormalStrength` | Range(0,1) | 1.0 | 反射の法線影響度 |
| `_ReflectionColor` | Color (HDR) | (1,1,1,1) | 反射色 |
| `_ReflectionColorTex` | 2D | "white" | 反射色テクスチャ |
| `_ReflectionApplyTransparency` | Int (Bool) | 1 | 透明度適用 |
| `_ReflectionCubeTex` | Cube | "black" | キューブマップフォールバック |
| `_ReflectionCubeColor` | Color (HDR) | (0,0,0,1) | キューブマップ色 |
| `_ReflectionCubeOverride` | Int (Bool) | 0 | キューブマップで環境マップを上書き |
| `_ReflectionCubeEnableLighting` | Range(0,1) | 1 | フォールバックのライティング適用 |
| `_ReflectionBlendMode` | Int (Enum) | 1 | ブレンドモード |

### MatCap (1st / 2nd)

1st と 2nd は同一構造。プレフィックスが `_MatCap` / `_MatCap2nd` で異なる。以下は 1st の例。

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_UseMatCap` | Int (Bool) | 0 | MatCap 有効化 |
| `_MatCapColor` | Color (HDR) | (1,1,1,1) | 色 |
| `_MatCapTex` | 2D | "white" | MatCap テクスチャ |
| `_MatCapMainStrength` | Range(0,1) | 0 | メインカラーの反映度 |
| `_MatCapBlendUV1` | Vector4 | (0,0,0,0) | UV1 ブレンド |
| `_MatCapZRotCancel` | Int (Bool) | 1 | Z 回転キャンセル |
| `_MatCapPerspective` | Int (Bool) | 1 | パースペクティブ補正 |
| `_MatCapVRParallaxStrength` | Range(0,1) | 1 | VR パララクス強度 |
| `_MatCapBlend` | Range(0,1) | 1 | ブレンド強度 |
| `_MatCapBlendMask` | 2D | "white" | ブレンドマスク |
| `_MatCapEnableLighting` | Range(0,1) | 1 | ライティング適用 |
| `_MatCapShadowMask` | Range(0,1) | 0 | シャドウマスク |
| `_MatCapBackfaceMask` | Int (Bool) | 0 | 背面マスク |
| `_MatCapLod` | Range(0,10) | 0 | ぼかし (MipMap LOD) |
| `_MatCapBlendMode` | Int (Enum) | 1 | ブレンドモード (0=Normal, 1=Add, 2=Screen, 3=Multiply) |
| `_MatCapApplyTransparency` | Int (Bool) | 1 | 透明度適用 |
| `_MatCapNormalStrength` | Range(0,1) | 1.0 | 法線影響度 |
| `_MatCapCustomNormal` | Int (Bool) | 0 | カスタム法線有効化 |
| `_MatCapBumpMap` | 2D (Normal) | "bump" | カスタム法線マップ |
| `_MatCapBumpScale` | Range(-10,10) | 1 | カスタム法線強度 |

### Rim Light

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_UseRim` | Int (Bool) | 0 | リムライト有効化 |
| `_RimColor` | Color (HDR) | (0.66,0.5,0.48,1) | リムライト色 |
| `_RimColorTex` | 2D | "white" | テクスチャ |
| `_RimMainStrength` | Range(0,1) | 0 | メインカラーの反映度 |
| `_RimNormalStrength` | Range(0,1) | 1.0 | 法線影響度 |
| `_RimBorder` | Range(0,1) | 0.5 | 境界 |
| `_RimBlur` | Range(0,1) | 0.65 | ぼかし |
| `_RimFresnelPower` | Range(0.01,50) | 3.5 | フレネル指数 |
| `_RimEnableLighting` | Range(0,1) | 1 | ライティング適用 |
| `_RimShadowMask` | Range(0,1) | 0.5 | シャドウマスク |
| `_RimBackfaceMask` | Int (Bool) | 1 | 背面マスク |
| `_RimVRParallaxStrength` | Range(0,1) | 1 | VR パララクス強度 |
| `_RimApplyTransparency` | Int (Bool) | 1 | 透明度適用 |
| `_RimDirStrength` | Range(0,1) | 0 | ライト方向依存リムの強度 |
| `_RimDirRange` | Range(-1,1) | 0 | ライト方向リム範囲 |
| `_RimIndirRange` | Range(-1,1) | 0 | 逆方向リム範囲 |
| `_RimIndirColor` | Color (HDR) | (1,1,1,1) | 逆方向リム色 |
| `_RimIndirBorder` | Range(0,1) | 0.5 | 逆方向リム境界 |
| `_RimIndirBlur` | Range(0,1) | 0.1 | 逆方向リムぼかし |
| `_RimBlendMode` | Int (Enum) | 1 | ブレンドモード |

### Glitter

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_UseGlitter` | Int (Bool) | 0 | グリッター有効化 |
| `_GlitterUVMode` | Int (Enum) | 0 | UV モード (0=UV0, 1=UV1) |
| `_GlitterColor` | Color (HDR) | (1,1,1,1) | 色 |
| `_GlitterColorTex` | 2D | "white" | カラーテクスチャ |
| `_GlitterColorTex_UVMode` | Int (Enum) | 0 | カラーテクスチャの UV モード |
| `_GlitterMainStrength` | Range(0,1) | 0 | メインカラーの反映度 |
| `_GlitterNormalStrength` | Range(0,1) | 1.0 | 法線影響度 |
| `_GlitterScaleRandomize` | Range(0,1) | 0 | サイズランダム化 |
| `_GlitterApplyShape` | Int (Bool) | 0 | シェイプテクスチャ適用 |
| `_GlitterShapeTex` | 2D | "white" | シェイプテクスチャ |
| `_GlitterAtras` | Vector4 | (1,1,0,0) | テクスチャアトラス設定 |
| `_GlitterAngleRandomize` | Int (Bool) | 0 | 角度ランダム化 |
| `_GlitterParams1` | Vector4 | (256,256,0.16,50) | (TilingX, TilingY, ParticleSize, Contrast) |
| `_GlitterParams2` | Vector4 | (0.25,0,0,0) | 追加パラメーター |
| `_GlitterPostContrast` | Float | 1 | ポストコントラスト |
| `_GlitterSensitivity` | Float | 0.25 | 感度 |
| `_GlitterEnableLighting` | Range(0,1) | 1 | ライティング適用 |
| `_GlitterShadowMask` | Range(0,1) | 0 | シャドウマスク |
| `_GlitterBackfaceMask` | Int (Bool) | 0 | 背面マスク |
| `_GlitterApplyTransparency` | Int (Bool) | 1 | 透明度適用 |
| `_GlitterVRParallaxStrength` | Range(0,1) | 0 | VR パララクス強度 |

### Emission (1st / 2nd)

1st と 2nd は同一構造。プレフィックスが `_Emission` / `_Emission2nd` で異なる。以下は 1st の例。

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_UseEmission` | Int (Bool) | 0 | エミッション有効化 |
| `_EmissionColor` | Color (HDR) | (1,1,1,1) | 色 |
| `_EmissionMap` | 2D | "white" | テクスチャ |
| `_EmissionMap_ScrollRotate` | Vector4 | (0,0,0,0) | UV スクロール/回転 |
| `_EmissionMap_UVMode` | Int (Enum) | 0 | UV モード (0=UV0, 1=UV1, 2=UV2, 3=UV3, 4=Rim) |
| `_EmissionMainStrength` | Range(0,1) | 0 | メインカラーの反映度 |
| `_EmissionBlend` | Range(0,1) | 1 | ブレンド強度 |
| `_EmissionBlendMask` | 2D | "white" | ブレンドマスク |
| `_EmissionBlendMask_ScrollRotate` | Vector4 | (0,0,0,0) | マスク UV スクロール/回転 |
| `_EmissionBlendMode` | Int (Enum) | 1 | ブレンドモード |
| `_EmissionBlink` | Vector4 | (0,0,3.14,0) | 点滅設定 (Speed, Strength, Type, Offset) |
| `_EmissionUseGrad` | Int (Bool) | 0 | グラデーション有効化 |
| `_EmissionGradTex` | 2D | "white" | グラデーションテクスチャ |
| `_EmissionGradSpeed` | Float | 1 | グラデーション速度 |
| `_EmissionParallaxDepth` | Float | 0 | パララクス深度 |
| `_EmissionFluorescence` | Range(0,1) | 0 | 蛍光 |

エミッショングラデーション内部パラメーター (`[HideInInspector]`): `_egci`, `_egai`, `_egc0`-`_egc7`, `_ega0`-`_ega7` (1st), `_e2gci`, `_e2gai`, `_e2gc0`-`_e2gc7`, `_e2ga0`-`_e2ga7` (2nd)

### Parallax

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_UseParallax` | Int (Bool) | 0 | パララックス有効化 |
| `_UsePOM` | Int (Bool) | 0 | Parallax Occlusion Mapping 有効化 |
| `_ParallaxMap` | 2D | "gray" | パララックスマップ |
| `_Parallax` | Float | 0.02 | 深度スケール |
| `_ParallaxOffset` | Float | 0.5 | オフセット |

### Distance Fade

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_DistanceFadeColor` | Color (HDR) | (0,0,0,1) | フェード色 |
| `_DistanceFade` | Vector4 | (0.1,0.01,0,0) | (Start, End, Strength, 未使用) |
| `_DistanceFadeMode` | Int (Enum) | 0 | フェードモード |
| `_DistanceFadeRimColor` | Color (HDR) | (0,0,0,0) | リム方向フェード色 |
| `_DistanceFadeRimFresnelPower` | Range(0.01,50) | 5.0 | リムフレネル指数 |

### AudioLink

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_UseAudioLink` | Int (Bool) | 0 | AudioLink 有効化 |
| `_AudioLinkDefaultValue` | Vector4 | (0,0,2,0.75) | (Strength, BlinkStrength, BlinkSpeed, BlinkThreshold) |
| `_AudioLinkUVMode` | Int (Enum) | 1 | UV モード |
| `_AudioLinkUVParams` | Vector4 | (0.25,0,0,0.125) | UV パラメーター |
| `_AudioLinkStart` | Vector4 | (0,0,0,0) | 開始位置 |
| `_AudioLinkMask` | 2D | "blue" | マスク |
| `_AudioLinkMask_ScrollRotate` | Vector4 | (0,0,0,0) | マスク UV スクロール |
| `_AudioLinkMask_UVMode` | Int (Enum) | 0 | マスク UV モード |
| `_AudioLink2Main2nd` | Int (Bool) | 0 | Main 2nd へ適用 |
| `_AudioLink2Main3rd` | Int (Bool) | 0 | Main 3rd へ適用 |
| `_AudioLink2Emission` | Int (Bool) | 0 | Emission へ適用 |
| `_AudioLink2EmissionGrad` | Int (Bool) | 0 | Emission グラデーションへ適用 |
| `_AudioLink2Emission2nd` | Int (Bool) | 0 | Emission 2nd へ適用 |
| `_AudioLink2Emission2ndGrad` | Int (Bool) | 0 | Emission 2nd グラデーションへ適用 |
| `_AudioLink2Vertex` | Int (Bool) | 0 | 頂点変形へ適用 |
| `_AudioLinkVertexUVMode` | Int (Enum) | 1 | 頂点 UV モード |
| `_AudioLinkVertexUVParams` | Vector4 | (0.25,0,0,0.125) | 頂点 UV パラメーター |
| `_AudioLinkVertexStart` | Vector4 | (0,0,0,0) | 頂点開始位置 |
| `_AudioLinkVertexStrength` | Vector4 | (0,0,0,1) | 頂点変形強度 (xyz=方向, w=スケール) |
| `_AudioLinkAsLocal` | Int (Bool) | 0 | ローカル AudioLink モード |
| `_AudioLinkLocalMap` | 2D | "black" | ローカルマップ |
| `_AudioLinkLocalMapParams` | Vector4 | (120,1,0,0) | ローカルマップパラメーター |

### Dissolve

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_DissolveMask` | 2D | "white" | ディゾルブマスク |
| `_DissolveNoiseMask` | 2D | "gray" | ノイズマスク |
| `_DissolveNoiseMask_ScrollRotate` | Vector4 | (0,0,0,0) | ノイズ UV スクロール |
| `_DissolveNoiseStrength` | Float | 0.1 | ノイズ強度 |
| `_DissolveColor` | Color (HDR) | (1,1,1,1) | ディゾルブ色 |
| `_DissolveParams` | Vector4 | (0,0,0.5,0.1) | (Mode, Shape, Border, Blur) |
| `_DissolvePos` | Vector4 | (0,0,0,0) | ディゾルブ位置 |

### ID Mask

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_IDMaskCompile` | Int (Bool) | 0 | コンパイル有効化（ランタイムでは無視） |
| `_IDMaskFrom` | Int (Enum) | 8 | ソース (0-7=UV0-7, 8=VertexID) |
| `_IDMask1`-`_IDMask8` | Int (Bool) | 0 | マスク 1-8 の有効化 |
| `_IDMaskIsBitmap` | Int (Bool) | 0 | ビットマップモード |
| `_IDMaskIndex1`-`_IDMaskIndex8` | Int | 0 | マスクインデックス |
| `_IDMaskControlsDissolve` | Int (Bool) | 0 | ディゾルブ制御 |
| `_IDMaskPrior1`-`_IDMaskPrior8` | Int (Bool) | 0 | 優先フラグ |

### UDIM Discard

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_UDIMDiscardCompile` | Int (Bool) | 0 | 有効化 |
| `_UDIMDiscardUV` | Int (Enum) | 0 | UV チャンネル (0-3) |
| `_UDIMDiscardMode` | Int (Enum) | 0 | モード (0=Vertex, 1=Pixel) |
| `_UDIMDiscardRow{0-3}_{0-3}` | Int (Bool) | 0 | 4x4 グリッドの各セルの表示/非表示 |

### Outline

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_OutlineColor` | Color (HDR) | (0.6,0.56,0.73,1) | アウトライン色 |
| `_OutlineTex` | 2D | "white" | テクスチャ |
| `_OutlineTex_ScrollRotate` | Vector4 | (0,0,0,0) | UV スクロール |
| `_OutlineTexHSVG` | Vector4 | (0,1,1,1) | 色相/彩度/明度/ガンマ |
| `_OutlineLitColor` | Color (HDR) | (1,0.2,0,0) | ライティング色 |
| `_OutlineLitApplyTex` | Int (Bool) | 0 | メインテクスチャの色を適用 |
| `_OutlineLitScale` | Float | 10 | ライティングスケール |
| `_OutlineLitOffset` | Float | -8 | ライティングオフセット |
| `_OutlineLitShadowReceive` | Int (Bool) | 0 | シャドウ受け取り |
| `_OutlineWidth` | Range(0,1) | 0.08 | 太さ |
| `_OutlineWidthMask` | 2D | "white" | 太さマスク |
| `_OutlineFixWidth` | Range(0,1) | 0.5 | 距離による太さ固定度 (1=画面上一定) |
| `_OutlineVertexR2Width` | Int (Enum) | 0 | 頂点カラー R の使用法 |
| `_OutlineDeleteMesh` | Int (Bool) | 0 | メッシュ削除 |
| `_OutlineVectorTex` | 2D (Normal) | "bump" | アウトライン方向テクスチャ |
| `_OutlineVectorUVMode` | Int (Enum) | 0 | UV モード |
| `_OutlineVectorScale` | Range(-10,10) | 1 | ベクタースケール |
| `_OutlineEnableLighting` | Range(0,1) | 1 | ライティング適用 |
| `_OutlineZBias` | Float | 0 | Z バイアス |
| `_OutlineDisableInVR` | Int (Bool) | 0 | VR で無効化 |

### Tessellation

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_TessEdge` | Range(1,100) | 10 | エッジ長 |
| `_TessStrength` | Range(0,1) | 0.5 | 強度 |
| `_TessShrink` | Range(0,1) | 0.0 | 縮小 |
| `_TessFactorMax` | IntRange(1,8) | 3 | 最大テッセレーション係数 |

### Fur (Fur バリアント専用)

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_FurNoiseMask` | 2D | "white" | ファーノイズテクスチャ |
| `_FurMask` | 2D | "white" | ファーマスク |
| `_FurLengthMask` | 2D | "white" | 長さマスク |
| `_FurVectorTex` | 2D (Normal) | "bump" | ファー方向テクスチャ |
| `_FurVectorScale` | Range(-10,10) | 1 | ベクタースケール |
| `_FurVector` | Vector4 | (0,0,1,0.02) | ファー方向 (xyz=方向, w=長さ) |
| `_FurGravity` | Range(0,1) | 0.25 | 重力影響 |
| `_FurRandomize` | Float | 0 | ランダム化 |
| `_FurAO` | Range(0,1) | 0 | アンビエントオクルージョン |
| `_FurLayerNum` | IntRange(1,3) | 2 | レイヤー数 |
| `_FurRootOffset` | Range(-1,0) | 0 | 根元オフセット |
| `_FurCutoutLength` | Float | 0.8 | カットアウト長 |
| `_FurTouchStrength` | Range(0,1) | 0 | タッチ反応強度 |
| `_FurRimColor` | Color | (0,0,0,1) | リム色 |
| `_FurRimFresnelPower` | Range(0.01,50) | 3.0 | リムフレネル指数 |
| `_FurRimAntiLight` | Range(0,1) | 0.5 | リムアンチライト |

### Refraction (Refraction バリアント専用)

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_RefractionStrength` | Range(-1,1) | 0.1 | 屈折強度 |
| `_RefractionFresnelPower` | Range(0.01,10) | 0.5 | フレネル指数 |
| `_RefractionColorFromMain` | Int (Bool) | 0 | メインカラーから色を取得 |
| `_RefractionColor` | Color | (1,1,1,1) | 屈折色 |

### Gem (Gem バリアント専用)

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_RefractionStrength` | Range(-1,1) | 0.5 | 屈折強度 |
| `_RefractionFresnelPower` | Range(0.01,10) | 1.0 | フレネル指数 |
| `_GemChromaticAberration` | Range(0,1) | 0.02 | 色収差 |
| `_GemEnvContrast` | Float | 2.0 | 環境コントラスト |
| `_GemEnvColor` | Color (HDR) | (1,1,1,1) | 環境色 |
| `_GemParticleLoop` | Float | 8 | パーティクルループ |
| `_GemParticleColor` | Color (HDR) | (4,4,4,1) | パーティクル色 |
| `_GemVRParallaxStrength` | Range(0,1) | 1 | VR パララクス強度 |

### Multi バリアント制御

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_UseOutline` | Int (Bool) | 0 | アウトライン有効化 |
| `_TransparentMode` | Int (Enum) | 0 | レンダリングモード (0=Opaque, 1=Cutout, 2=Transparent, 3=Refraction, 4=Fur, 5=FurCutout, 6=Gem) |
| `_UseClippingCanceller` | Int (Bool) | 0 | クリッピングキャンセラー |
| `_AsOverlay` | Int (Bool) | 0 | オーバーレイとして描画 |

### Advanced (Rendering State)

メインパスとアウトラインパスでそれぞれ独立した描画ステート。アウトラインは `_Outline` プレフィックス付き。

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_Cull` | Int (Enum) | 2 | カリング (0=Off, 1=Front, 2=Back) |
| `_SrcBlend` / `_DstBlend` | Int (BlendMode) | 1 / 0 | ブレンドモード |
| `_SrcBlendAlpha` / `_DstBlendAlpha` | Int (BlendMode) | 1 / 10 | アルファブレンドモード |
| `_BlendOp` / `_BlendOpAlpha` | Int (BlendOp) | 0 / 0 | ブレンド演算 |
| `_ZClip` | Int (Bool) | 1 | Z クリッピング |
| `_ZWrite` | Int (Bool) | 1 | Z 書き込み |
| `_ZTest` | Int (CompareFunction) | 4 | Z テスト (4=LessEqual) |
| `_StencilRef` | IntRange(0,255) | 0 | ステンシル参照値 |
| `_StencilReadMask` / `_StencilWriteMask` | IntRange(0,255) | 255 | ステンシルマスク |
| `_StencilComp` | Float (CompareFunction) | 8 | ステンシル比較 (8=Always) |
| `_StencilPass` / `_StencilFail` / `_StencilZFail` | Float (StencilOp) | 0 | ステンシル操作 |
| `_OffsetFactor` / `_OffsetUnits` | Float | 0 | ポリゴンオフセット |
| `_ColorMask` | Int | 15 | カラーマスク (RGBA=15) |
| `_AlphaToMask` | Int (Bool) | 0 | アルファトゥマスク |
| `_lilShadowCasterBias` | Float | 0 | シャドウキャスターバイアス |

### 内部制御・互換用

| プロパティ名 | 型 | デフォルト値 | 用途 |
|---|---|---|---|
| `_lilToonVersion` | Int | 45 | バージョン番号 (内部管理用) |
| `_BaseColor` | Color | (1,1,1,1) | SRP 互換用 (未使用) |
| `_BaseMap` | 2D | "white" | SRP 互換用 (未使用) |
| `_BaseColorMap` | 2D | "white" | SRP 互換用 (未使用) |
| `_Ramp` | 2D | "white" | VRChat シャドウランプ (v2.0.0+) |

### 1.9.0 -> 2.3.2 変更サマリー

- **追加**: `_EnvRimBorder`, `_EnvRimBlur` (VRC Light Volumes リムライト制御, v2.1.0+)
- **追加**: `_Ramp` (VRChat ToonStandard フォールバック用シャドウランプ, v2.0.0+)
- **削除**: `_BitKey0`-`_BitKey31`, `_Keys`, `_IgnoreEncryption` (メッシュ暗号化機能の廃止, v2.0.0)
- **新シェーダーファイル**: `ltspass_bakeramp.shader` (ランプベイクユーティリティ, v1.10.0+)
- **破壊的変更**: Fur の Shrink モード廃止、Subdivision モードに統一 (v2.0.0)

### Shader GUID テーブル (主要)

| シェーダー | ファイル | GUID |
|---|---|---|
| lilToon (メイン) | lts.shader | `df12117ecd77c31469c224178886498e` |
| Hidden/lilToonCutout | lts_cutout.shader | `85d6126cae43b6847aff4b13f4adb8ec` |
| Hidden/lilToonTransparent | lts_trans.shader | `165365ab7100a044ca85fc8c33548a62` |
| Hidden/lilToonOutline | lts_o.shader | `efa77a80ca0344749b4f19fdd5891cbe` |
| Hidden/lilToonCutoutOutline | lts_cutout_o.shader | `3b4aa19949601f046a20ca8bdaee929f` |
| Hidden/lilToonTransparentOutline | lts_trans_o.shader | `3c79b10c7e0b2784aaa4c2f8dd17d55e` |
| Hidden/lilToonOnePassTransparent | lts_onetrans.shader | `b269573b9937b8340b3e9e191a3ba5a8` |
| Hidden/lilToonTwoPassTransparent | lts_twotrans.shader | `6a77405f7dfdc1447af58854c7f43f39` |
| Hidden/lilToonFur | lts_fur.shader | `55706696b2bdb5d4d8541b89e17085c8` |
| Hidden/lilToonGem | lts_gem.shader | `a8d94439709469942bc7dcc9156ba110` |
| Hidden/lilToonRefraction | lts_ref.shader | `dce3f3e248acc7b4daeda00daf616b4d` |
| Hidden/lilToonRefractionBlur | lts_ref_blur.shader | `3fb94a39b2685ee4d9817dcaf6542d99` |
| Hidden/lilToonLite | ltsl.shader | `381af8ba8e1740a41b9768ccfb0416c2` |
| _lil/lilToonMulti | ltsmulti.shader | `9294844b15dca184d914a632279b24e1` |
| _lil/[Optional] lilToonFakeShadow | lts_fakeshadow.shader | `00795bf598b44dc4e9bd363348e77085` |
| Hidden/ltspass_opaque | ltspass_opaque.shader | `61b4f98a5d78b4a4a9d89180fac793fc` |
| Hidden/ltspass_cutout | ltspass_cutout.shader | `ad219df2a46e841488aee6a013e84e36` |
| Hidden/ltspass_transparent | ltspass_transparent.shader | `2683fad669f20ec49b8e9656954a33a8` |

## 実運用で学んだこと

### MatCap マスクによる装飾テキスト/柄の表示原理 (2026-03-26 実測)
- `_MatCapBlendMask` に UV アトラスと同サイズのマスクテクスチャを設定
- マスクの白い領域にのみ `_MatCapTex`（例: 金色球体）が MatCap として合成される
- 衣装の「金文字ロゴ」「金属装飾」等はこの仕組みで実現されていることが多い
- 文字/柄を消すには: マスクの該当 UV 領域を黒塗り
- 別柄にするには: マスクに別パターンを白で描く → MatCap の質感で柄が表示される
- マスクはメインテクスチャと同じ UV を共有するため、UV アトラス上の位置特定が必要
- confidence: high

### _MainTexHSVG による髪色調整 (2026-03-28 実測)
- `_MainTexHSVG` = (H色相シフト, S彩度倍率, V明度倍率, Gガンマ)、デフォルト (0, 1, 1, 1)
- **透き通った金髪の作り方**: S=0.45〜0.55（彩度控えめで透明感）、V=1.2〜1.3（明るめ）、H=0.03（黄金方向への微調整）
- S を 0.6 以上にするとオレンジ/赤みが残る。0.4 以下だとくすんで灰色に近づく
- V を 1.3 以上にすると薄い部分（猫耳先端等）が白飛びする
- テクスチャの元色が重要。暗い茶色ベース（tuki）より明るいベース（yume）の方が金色を作りやすい
- `_MainGradationStrength` はカスタムランプの影響度。強すぎると HSVG の調整を打ち消すので 0.3〜0.5 程度に抑える
- confidence: high

### 髪の透き通り表現 (2026-03-28 実測)
- シェーダー `lts_twotrans_o`（TwoPass Transparent + Outline）が前提
- `_BacklightColor` を HDR 値（>1.0）にすると逆光時に髪を透かした光の表現が強くなる
- 金髪の場合: (2.0〜2.2, 1.8〜2.0, 1.5〜1.6) で暖色系の透き通りが出る
- `_BacklightBorder` / `_BacklightDirectivity` で透き通りの範囲・指向性を制御
- confidence: high

### 複数テクスチャスロットの同期 (2026-03-28 実測)
- liltoon の髪マテリアルでは `_MainTex` / `_BaseMap` / `_BaseColorMap` / `_ShadowColorTex` / `_OutlineTex` に同じテクスチャが設定されていることが多い
- テクスチャを差し替える場合は全スロットを揃えて変更する。`_MainTex` だけ変えると影色やアウトラインが旧テクスチャの色のまま残る
- confidence: high

### カラーテーマの統一調整 (2026-03-28 実測)
- 髪色を変更する場合、以下のカラープロパティを全て整合させる必要がある:
  - `_Color`: メインティント
  - `_ShadowColor` / `_Shadow2ndColor`: 影色（「少し茶色」等の深み）
  - `_ShadowBorderColor`: 影境界色
  - `_MatCapColor`: ツヤの色
  - `_RimColor` / `_RimShadeColor`: 縁光の色
  - `_ReflectionColor`: 反射色
  - `_OutlineColor`: アウトライン色
  - `_BacklightColor`: 透過光色
- 1つでも合わない色が残ると違和感が出る
- confidence: high

### UV アトラス共有マテリアルの swap テクニック (2026-03-26 実測)
- 同一衣装の別カラバリ（Ichigo, Gingham, Antique, Goth 等）は UV レイアウトが共通
- 特定メッシュのマテリアルだけを別バリエーションに `editor_set_material` で swap すると、そのパーツだけ柄/色が変わる
- メインテクスチャだけでなく、ノーマルマップ・MatCap マスク等の付随テクスチャもバリエーション間で UV が一致するため、マテリアル丸ごとの swap が安全
- confidence: high
