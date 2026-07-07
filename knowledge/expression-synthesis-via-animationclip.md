---
tool: expression-synthesis-via-animationclip
version_tested: "0.5.167"
last_updated: 2026-05-11
confidence: high
---

# Expression Synthesis via AnimationClip

VRChat アバター用の表情を「複数 BlendShape の weight 組み合わせ」として設計し、Unity AnimationClip (.anim) として永続化する pattern。FaceEmo / ModularAvatar / 独自 Animator から再生可能になり、表情合成のレシピを再利用可能な資産化できる。

## 概要 (L1)

VRChat の表情制御は AnimatorController の State にアタッチした AnimationClip を再生する形で行われる。1 表情 = 1 AnimationClip (clip 長 1 frame、各 BlendShape の weight を keyframe で指定)。

PrefabSentinel + 独自ヘルパー + LLM agent の組み合わせで、以下の workflow が組める:

1. アバターの全 BlendShape を screenshot + description 化 (上記 `blendshape-capture-pipeline.md`)
2. ユーザリクエスト (例: 「悲しい顔」「ドヤ顔」「驚き顔」) → LLM が description データから shape 候補を選定
3. shape + weight のレシピを JSON で記述
4. Editor MenuItem でレシピから AnimationClip 一括生成 (`ExpressionSaver.cs` pattern)
5. FaceEmo の Animation スロットに割当

## レシピ JSON フォーマット (L2)

```json
{
  "relative_path": "Body",
  "expressions": [
    {
      "name": "expression_name",
      "shapes": [
        {"name": "<blendshape_name>", "weight": <0-100>},
        ...
      ]
    },
    ...
  ]
}
```

- `relative_path`: AnimationClip 内で SkinnedMeshRenderer を指す相対パス (Animator が avatar root にあれば `Body`)
- `name`: 出力ファイル名 + AnimationClip 内 m_Name
- `shapes`: BlendShape の名前と weight のペア (1-15 個程度が典型)

## AnimationClip 生成コード (L2)

```csharp
var clip = new AnimationClip();
clip.frameRate = 60f;

foreach (var sw in exp.shapes) {
    // 2-keyframe constant curve at t=0 and t=1/60 for animator playback stability
    var curve = new AnimationCurve(
        new Keyframe(0f, sw.weight, 0f, 0f),
        new Keyframe(1f / 60f, sw.weight, 0f, 0f)
    );
    clip.SetCurve(relativePath, typeof(SkinnedMeshRenderer), "blendShape." + sw.name, curve);
}

AssetDatabase.CreateAsset(clip, "Assets/<dir>/" + exp.name + ".anim");
AssetDatabase.SaveAssets();
```

要点:
- `blendShape.<name>` プロパティが SkinnedMeshRenderer に対する binding パス
- `Keyframe` 2 つ作る (t=0 と t=1/60) ことで AnimatorController の State Length 検出を確実にする (1-keyframe だと再生時間 0 と認識されて即終了する unity バージョンがある)
- inSlope / outSlope = 0 で constant curve に (補間しない、固定 weight)
- `relativePath` は AnimationClip 内の `m_FloatCurves[].path` に保存される。Animator の動作 GameObject から SMR への相対パス

## 強度段階の設計 pattern (L3)

1 つの表情テーマ (e.g., 「笑顔」「困り顔」「驚き」「うっとり」) を複数強度で持たせると VRChat のジェスチャー切替で連続表現できる。Hybrid pattern:

- **Core shapes**: テーマ核となる 2-4 個の shape。全 level (L1〜L5) で共通。weight だけ段階的に上昇
- **Layered add-ons**: 高 level (L3+) で nuance を足す追加 shape

例 (「うっとり」テーマで L1〜L3 を組む場合):
```
Core: <たれ目系 eye shape> / <上瞼下げ系 eyelid> / <虹彩拡大> / <赤面 overlay> / <たれ眉> / <半開き mouth>
L1: core 全部 50 程度
L2: core 全部 75-100 + 追加 (下瞼上げ / 軽い tears / 軽い 舌)
L3: core 全部 80-100 + 上記 + さらに add-on (じと目 / 潤み虹彩 / うるみ highlight / 下瞼 cry / はんなり眉)
```

L1 → L5 で「強くなる」だけでなく「nuance も追加される」設計で、ユーザ体感が「単なる weight 増加」を超える。

## MMD カテゴリ shape の併用判定 (L2)

ユーザのアバター使用シナリオで判断:

- **ダンスワールド利用しない** → MMD shape 併用 OK。「あ」「まばたき」「困る」「にこり」等は表情合成の primitive として強力。
- **ダンスワールド利用する** → MMD shape 排除。VRC_Viseme / Eye_type / Eyelid / Eyelashes / Eyebrow / Mouth_* / Cheek / Effect / PerfectSync のみ使用。

両用したい場合は、MMD shape を「動的に再生する FaceEmo の表情」では使わず、「ダンスワールドの自動駆動に任せる」形に分離。

## FaceEmo 統合 (L2)

生成した AnimationClip を FaceEmo の Animation スロットに割り当てる手順:

1. FaceEmo メインウィンドウを開く
2. 該当する Mode > Branch を選択
3. Animation スロット (Base / Left / Right / Both) に Project View から .anim をドラッグ&ドロップ
4. Branch に Condition (Hand + HandGesture) を追加 → 該当ジェスチャー時に AnimationClip が再生
5. FaceEmo > Build で AnimatorController / Expression Menu が再生成される

FaceEmo の細部は `face-emo.md` 参照。

## アバター横展開 (L3)

同じレシピを複数アバターに適用するパターン:

- 同じ系統のアバターなら BlendShape 名がほぼ共通、レシピ JSON をそのまま流用可
- 異なる系統のアバター間では BlendShape 名が違うので、name mapping 表を持って convert する helper を別途用意

## AnimationClip primitive 経由の永続化 (issue #243)

prefab-sentinel 0.5.171+ では、AnimationClip の inspect / create / apply に対応する 3 つの MCP ツールが追加された。capture 結果をそのまま AnimationClip に書き戻すための推奨経路。

### `editor_inspect_animation_clip(asset_path)`
- 既存の `.anim` を読み、`curves`（`relative_path`, `type`, `property`, `values` のリスト）、`length`、`frame_rate` を返す。
- read-only。レビュー / 差分目視に使う。

### `editor_create_animation_clip(target_dir, name, curves, confirm, change_reason)`
- 新規 `.anim` を `Assets/...` 配下に書く。
- `curves` の各エントリは `relative_path` / `type` / `property` / `value` の 4 フィールドで構成。
  - `value` が scalar → 1 keyframe（time=0）。
  - `value` が list → list 長分の keyframe（default frame rate でサンプリング）。
- writer なので `confirm=True` + 非空 `change_reason` を要求（CHANGE_REASON_REQUIRED）。
- 成功 envelope には `asset_path`（書き込んだファイルパス）と `curve_count`。

### `editor_apply_animation_clip(asset_path, target_hierarchy_path, confirm, change_reason)`
- 既存 clip を live hierarchy target に preview-apply する。Unity の AnimationMode で 1 Undo group にまとめてサンプリングするため、Undo 1 回でプレビューを完全に戻せる。
- writer audit gate あり。whitespace のみの `change_reason` は missing 扱い。
- bridge は Prefab Stage 認識のリゾルバ経由で target を解決するので、`editor_open_prefab` で stage を開いた状態でも Scene でも動く。
- 失敗コード: `EDITOR_CTRL_ANIMATION_CLIP_NOT_FOUND` / `_TARGET_NOT_FOUND` / `_APPLY_FAILED`。

## 関連 knowledge

- `blendshape-capture-pipeline.md` — capture phase の詳細
- `face-emo.md` — FaceEmo の運用
- `modular-avatar.md` — MA で AnimatorController を統合する
