---
tool: vrchat-avpro-custom-shader-gamma
version_tested: "VRChat World SDK 3.7+ + Unity 2022.3 (Linear color space)"
last_updated: 2026-05-24
confidence: high
---

# VRChat AVPro Custom Shader Gamma 補正

VRChat (= Linear color space project) で AVPro 動画 texture を custom shader で `tex2D` 生 sample すると、 sRGB encoded 値が linear pipeline に流れ、 display 出力時に gamma 適用で約 2.2 倍明るく見える (= 全体的に白飛び)。AVPro 公式 shader は `_ApplyGamma` toggle で内部 gamma 変換を実装、 custom shader は同等の補正を `GammaToLinearSpace(col.rgb)` で frag 内に入れる必要がある。

## 基本情報

- 対象 plugin: RenderHeads AVPro Video (VRChat client 内蔵)
- 対象 player: `VRCAVProVideoPlayer` (= UnityEngine.Video.VideoPlayer ではなく VRChat AVPro wrapper)
- 対象現象: Linear color space project の custom shader で AVPro texture を sample → 全体白飛び
- 公式参考:
  - [AVPro Video Shaders doc](https://www.renderheads.com/content/docs/AVProVideo/articles/usage-shaders.html)
  - [AVPro Linear color space issue #673](https://github.com/RenderHeads/UnityPlugin-AVProVideo/issues/673)
  - [VRChat AVPro custom shader UV solution](https://ask.vrchat.com/t/avpro-flipped-uvs-upside-down-in-custom-shaders-solution/8001)
- 関連 macro: `GammaToLinearSpace()` (`UnityCG.cginc` 内、 `pow(x, 2.2)` 多項式近似)

## 主要 API・概念

### `_ApplyGamma` Material property

AVPro 公式 shader (例: `AVProVideo/Unlit/Transparent`) は `_ApplyGamma` Float property を持つ:

- `_ApplyGamma = 0` (Gamma color space project) → 補正なし
- `_ApplyGamma = 1` (Linear color space project) → frag 内で sRGB → linear 変換 (= 暗くなる)

VRChat client は Linear color space で render するため、 多くのケースで `_ApplyGamma = 1` が default 動作。VRChat 配布の AVPro 関連 prefab の screen Material 確認すると `_ApplyGamma` field 込みで設定済み。

### custom shader での実装パターン

Properties 宣言:

```hlsl
[Toggle] _ApplyGamma ("Apply Gamma (linear color space で AVPro texture 補正)", Float) = 1
```

uniform:

```hlsl
float _ApplyGamma;
```

frag 内、 `tex2D` 直後で補正:

```hlsl
fixed4 col = tex2D(_MainTex, uv);
if (_ApplyGamma > 0.5)
{
    col.rgb = GammaToLinearSpace(col.rgb);
}
```

`GammaToLinearSpace` は `UnityCG.cginc` 標準 macro、 `multi_compile` 不要 (= shader variant 制限がある event world / regulation 系で使用可)。

### UV 反転 (合わせて発生する典型問題)

AVPro 動画 texture は platform / decoder によって upside-down で texture に書き込まれる (例: D3D11 で y flip 必要)。custom shader で sample すると上下逆に見える。

対処:
- Material の `_MainTex_ST` で `Scale.y = -1, Offset.y = 1` 設定 → `tex2D` の sample 時に uv.y が `1 - uv.y` で flip される
- もしくは shader 内で `o.uv = TRANSFORM_TEX(v.uv, _MainTex)` macro で `_MainTex_ST` を自動適用

`_MainTex_ST` 経由なら Material Inspector で Scale (1, -1) / Offset (0, 1) を設定するだけで shader 改造不要。

## 使い分け

### `_ApplyGamma` を on / off にするケース

| Project color space | `_ApplyGamma` |
|---|---|
| Linear (= VRChat default) | **1** (on、 sRGB → linear 補正で正常 brightness) |
| Gamma | 0 (off、 補正不要) |
| Custom HDRP / URP | 別途、 render pipeline の sRGB texture 解釈次第 |

判断: project の Color Space (`Edit > Project Settings > Player > Other Settings > Color Space`) が Linear なら on、 Gamma なら off。

### 補正方向の選択

`GammaToLinearSpace` (sRGB → linear) = 暗くする方向。逆方向 (= 明るくする `LinearToGammaSpace`) が必要なケースは稀 (= 通常は AVPro texture が sRGB encoded として明るく見える側の補正)。実機で明るすぎるなら `GammaToLinearSpace` を試す、 暗すぎるなら `LinearToGammaSpace` を試す、 の順。

### `_ApplyGamma` を toggle で外せるようにする理由

- editor 上で gamma color space で確認する開発フェーズ
- 別 shader / texture pipeline (= URP / HDRP) に転用するケース
- AVPro texture でない通常 sRGB texture を sample する compatibility mode

## 落とし穴

### 公式 shader をそのまま使うと重複補正で暗くなる

AVPro 公式 `AVProVideo/Unlit/Transparent` 等を流用しつつ shader 内で `GammaToLinearSpace` を追加すると 2 重補正 → 過剰に暗い。custom shader で「公式 shader の logic を **置き換える**」 場合のみ `GammaToLinearSpace` を入れる、 「公式 shader に追加して使う」 ケースでは入れない。

### `_ApplyGamma` を Material asset で 1 に固定すると Linear / Gamma 両 project で動かない

`_ApplyGamma` を Inspector で 1 に固定 (= toggle 削除 / hardcode) すると、 Gamma color space project に shader を持ち込んだ際に 2 重補正で暗くなる。**toggle として残す + project ごとに Material 設定** が portable。

### Render Pipeline 移行で `GammaToLinearSpace` 挙動が変わる可能性

URP / HDRP に移行すると Linear color space で sRGB texture の auto 変換が走るケースがあり、 custom shader 内の手動補正と衝突する。Built-in RP → URP 移行時に `_ApplyGamma` の挙動再確認が必須。

### `_ApplyGamma = 1` でも極端なケースで色味ずれる

`GammaToLinearSpace` は `pow(x, 2.2)` 近似多項式 (= 正確な sRGB → linear ではなく簡略式)。HDR 動画や色域広い content では精密な sRGB → linear 変換 (= IEC 61966-2-1 仕様準拠) を実装する custom function 必要。一般 SDR 動画では多項式近似で十分。

### AVPro texture の sRGB flag が platform ごとに違う

External texture (= AVPro が native level で生成して Unity に export) の sRGB flag は plugin version / platform で挙動異なる ([issue #673](https://github.com/RenderHeads/UnityPlugin-AVProVideo/issues/673), [issue #2028](https://github.com/RenderHeads/UnityPlugin-AVProVideo/issues/2028))。Mac / Android / Windows でそれぞれ確認推奨。

## 関連 knowledge

- [udonsharp-material-instance-pitfall.md](./udonsharp-material-instance-pitfall.md) — 動画 texture 系で `renderer.material` instance 化により texture loss する罠
