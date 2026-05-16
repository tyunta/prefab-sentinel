---
tool: vrchat-shader-development
version_tested: "VRC SDK 3.7+ / Unity 2022.3"
last_updated: 2026-03-30
confidence: low
---

# VRChat シェーダー開発の概要

## 概要 (L1)

VRChat は Unity の **Built-in Render Pipeline** で動作する。シェーダーは ShaderLab + HLSL で記述する。URP / HDRP は VRChat では使用不可。

C# スクリプトによるシェーダー制御（MaterialPropertyBlock の動的設定等）はワールドでは Udon 経由で可能だが、アバターでは不可。シェーダー単体で完結する設計が必要。

## ShaderLab の基本構造 (L1)

```hlsl
Shader "Custom/MyShader"
{
    Properties
    {
        _MainTex ("Texture", 2D) = "white" {}
        _Color ("Color", Color) = (1,1,1,1)
    }
    SubShader
    {
        Tags { "RenderType"="Opaque" "VRCFallback"="Toon" }

        Pass
        {
            HLSLPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma multi_compile_instancing  // GPU Instancing 必須

            #include "UnityCG.cginc"

            struct appdata
            {
                float4 vertex : POSITION;
                float2 uv : TEXCOORD0;
                UNITY_VERTEX_INPUT_INSTANCE_ID  // SPS-I 対応
            };

            struct v2f
            {
                float4 pos : SV_POSITION;
                float2 uv : TEXCOORD0;
                UNITY_VERTEX_OUTPUT_STEREO  // SPS-I 対応
            };

            sampler2D _MainTex;
            float4 _Color;

            v2f vert(appdata v)
            {
                v2f o;
                UNITY_SETUP_INSTANCE_ID(v);           // SPS-I
                UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(o);  // SPS-I
                o.pos = UnityObjectToClipPos(v.vertex);
                o.uv = v.uv;
                return o;
            }

            float4 frag(v2f i) : SV_Target
            {
                UNITY_SETUP_STEREO_EYE_INDEX_POST_VERTEX(i);  // SPS-I
                return tex2D(_MainTex, i.uv) * _Color;
            }
            ENDHLSL
        }
    }
    FallBack "Diffuse"
}
```

### 構造の階層

| 要素 | 役割 |
|------|------|
| `Shader "名前"` | シェーダー定義のルート。マテリアルの Inspector に表示される名前 |
| `Properties` | Inspector に公開するパラメータ。マテリアルに保存される |
| `SubShader` | GPU 機能レベルごとのシェーダー実装。上から順に試行される |
| `Tags` | レンダリング順序、フォールバック指定等のメタデータ |
| `Pass` | 1 回のレンダリングパス。頂点・フラグメントシェーダーを含む |
| `FallBack` | すべての SubShader が非対応の場合の代替シェーダー |

## VRChat 固有の制約 (L1)

### Single Pass Stereo Instanced (SPS-I)

VRChat は VR レンダリングに SPS-I を使用。すべてのシェーダーで対応が必須。

**必須マクロ:**
- `UNITY_VERTEX_INPUT_INSTANCE_ID` — appdata 構造体に追加
- `UNITY_VERTEX_OUTPUT_STEREO` — v2f 構造体に追加
- `UNITY_SETUP_INSTANCE_ID(v)` — 頂点シェーダー冒頭
- `UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(o)` — 頂点シェーダー内
- `UNITY_SETUP_STEREO_EYE_INDEX_POST_VERTEX(i)` — フラグメントシェーダー冒頭

**Depth テクスチャ:** `sampler2D _CameraDepthTexture` ではなく `UNITY_DECLARE_DEPTH_TEXTURE(_CameraDepthTexture)` を使う。

### GPU Instancing

`#pragma multi_compile_instancing` の追加が必須。マテリアル側でも「Enable GPU Instancing」を有効にする。

### シェーダーキーワード制限

| 種類 | 上限 | 備考 |
|------|------|------|
| グローバルキーワード | 256（Unity 全体で共有） | Unity 内部で ~60 使用済み。VRChat では ~140 スロットが実質上限 |
| ローカルキーワード | 64（シェーダーごと） | `shader_feature_local` / `multi_compile_local` で宣言 |

**ベストプラクティス:**
- `[Toggle]` プロパティの使用を避ける（自動でグローバルキーワードを消費する）
- ローカルキーワード（`_local` サフィックスの pragma）を優先する
- 不要なキーワードは材料からクリアする（VRC SDK 付属のキーワード除去ツールあり）
- グローバルキーワード枯渇でワールドのポストプロセスが壊れるリスクがある

### ジオメトリシェーダー・テッセレーション

PC では動作するが、Quest/Android では非対応。SPS-I との組み合わせでは追加のステレオ設定が必要（頂点→ジオメトリ→フラグメントの各ステージで STEREO マクロを伝播する）。

### C# スクリプト不可（アバター）

アバターでは C# による MaterialPropertyBlock 操作やランタイムシェーダー切替が不可。シェーダーアニメーション（Animator でマテリアルプロパティを駆動）で代替する。ワールドでは Udon から `VRCShader.SetGlobalFloat` 等で制御可能。

## VRCFallback タグシステム (L1)

他ユーザーがシェーダーをブロックした際のフォールバック先を指定する仕組み。

```hlsl
Tags { "VRCFallback"="ToonCutout" }
```

### 指定可能な値

| タグ値 | フォールバック先 |
|--------|-----------------|
| `Unlit` | Unlit |
| `VertexLit` | VertexLit |
| `Toon` | Toon Lit |
| `ToonCutout` | Toon Lit (Cutout) |
| `ToonOutline` | Toon Lit (Outline) |
| `ToonOutlineCutout` | Toon Lit (Outline + Cutout) |
| `Transparent` | Unlit/Transparent |
| `Fade` | Unlit/Transparent |
| `Cutout` | Standard (Cutout) |
| `ToonStandard` | VRChat/Mobile/Toon Standard |
| `ToonStandardOutline` | VRChat/Mobile/Toon Standard (Outline) |
| 未指定・その他 | Standard |

- 同名の Properties (`_MainTex`, `_Color` 等) はフォールバック先に自動コピーされる
- `ToonStandard` / `ToonStandardOutline` は排他（組み合わせ不可）

## VRChat シェーダーグローバル変数 (L2)

VRChat がランタイムで設定するグローバルシェーダー変数。`uniform` で宣言して使用する。

### カメラ・ミラー検出

```hlsl
uniform float _VRChatCameraMode;
// 0: 通常レンダリング
// 1: VR ハンドヘルドカメラ
// 2: デスクトップハンドヘルドカメラ
// 3: スクリーンショット

uniform float _VRChatMirrorMode;
// 0: 通常（ミラーなし）
// 1: VR ミラー
// 2: デスクトップミラー

uniform float3 _VRChatMirrorCameraPos;  // ミラーカメラのワールド座標
```

**使用例:**
```hlsl
bool isMirror() { return _VRChatMirrorMode != 0; }
bool isCamera() { return _VRChatCameraMode != 0; }
```

### 時刻

VRChat SDK の `VRCTime.cginc` をインクルードして使用:
- `VRC_GetUTCUnixTimeInSeconds()` — UTC Unix タイムスタンプ
- `VRC_GetNetworkTimeInMilliseconds()` — ネットワーク同期時刻

### AudioLink

コミュニティ標準の音声リアクティブシステム。`_AudioTexture` テクスチャをグローバルに設定し、シェーダーからサンプリングして音声データを取得する。VRChat は `VRCShader` API で `_AudioTexture` を特別扱いする。

## Quest / Android の制約 (L1)

Quest（Android）ではカスタムシェーダーが使用不可。SDK 付属のホワイトリストシェーダーのみ許可される。

### ホワイトリストシェーダー（VRChat/Mobile 配下）

| シェーダー | 特徴 |
|-----------|------|
| Standard Lite | Unity Standard ベースの軽量版 |
| Toon Lit | シェーディングなしのフラット描画 |
| **Toon Standard** | フル機能トゥーン（影ランプ、メタリック、AO、エミッション、リムライト対応）。PC/Android/iOS 共通 |
| Particles 系 | パーティクル用バリアント |
| Skybox 系 | スカイボックス用 |

**Toon Standard が推奨。** PC/Quest クロスプラットフォームで使用可能。

## 主要コミュニティシェーダー (L1)

| シェーダー | 特徴 | Quest 対応 |
|-----------|------|-----------|
| **lilToon** | 日本で最も普及。1 シェーダーで多機能。Inspector UI から自動バリアント切替 | ビルド時最適化あり（キーワードストリップ） |
| **Poiyomi** | 英語圏で最も普及。モジュール構造で機能選択。AudioLink 統合 | 一部機能で Quest ビルド対応 |
| **orels Unity Shaders** | PBR ベースのシェーダーコレクション | — |
| **Sunao Shader** | 軽量トゥーンシェーダー | — |

## シェーダーデバッグのヒント (L1)

- **RenderDoc**: VRChat のフレームをキャプチャしてシェーダーの実行を解析できる
- **shadertrixx** (cnlohr): VRChat シェーダー開発のテクニック集。ミラー検出、カメラ判定、SPS-I 対応のパターンが豊富
- **pema99/shader-knowledge**: シェーダーの Tips & Tricks 集

## prefab-sentinel との関連 (L2)

- `inspect_materials`: マテリアルのシェーダー名、プロパティ値の確認
- `inspect_material_asset`: シェーダープロパティの詳細（型、値、テクスチャ参照）
- `editor_set_material_property`: シェーダープロパティの値変更
- `editor_get_material_property`: シェーダープロパティの読み取り

マテリアルの `m_Shader` フィールドは GUID + fileID でシェーダーアセットを参照する。lilToon のように Inspector が内部で Hidden シェーダーに差し替えるタイプは、YAML 上のシェーダー GUID がユーザーの選択と異なる場合がある。
