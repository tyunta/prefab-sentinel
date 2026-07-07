---
tool: blendshape-capture-pipeline
version_tested: "0.5.167"
last_updated: 2026-05-11
confidence: high
---

# BlendShape Capture Pipeline

VRChat アバターの大量 BlendShape (典型 500-1500 個) を Unity Editor 上で系統的に撮影し、AnimationClip として永続化する pipeline の構築 know-how。BlendShape 数が多い汎用アバター系で特に有用。

## 概要 (L1)

VRChat アバターは Body SkinnedMeshRenderer に 500-1500 個の BlendShape を持つことが多い。これらを系統的に撮影し、各 shape の効果を視認できるデータベースを構築すると、後段で:

- 表情合成 (LLM agent が「悲しい顔」「驚き」等のリクエストから shape 組み合わせを提案)
- FaceEmo / VRChat Expression Menu のレシピ作成
- 公開ドキュメント (シェイプキー一覧)

に活用できる。

## 必須 workaround (L2): SkinnedMeshRenderer.forceMatrixRecalculationPerRender

**`Camera.Render()` を独自スクリプトで叩く場合、BlendShape の変化が screenshot に反映されないバグがある。** Unity Editor (Edit Mode) では SkinnedMeshRenderer の skinning matrix がフレーム間でキャッシュされ、BlendShape weight 変更後でも `Camera.Render()` が古い skinning を流用してしまう。

回避策: `Camera.Render()` 直前に全 SMR で `forceMatrixRecalculationPerRender = true` をセット。撮影後 false に戻して per-frame overhead を回避。

```csharp
var smrs = Object.FindObjectsOfType<SkinnedMeshRenderer>();
foreach (var smr in smrs) smr.forceMatrixRecalculationPerRender = true;

// ... 撮影 ...
cam.targetTexture = rt;
cam.Render();
cam.targetTexture = prevTarget;
// ... ReadPixels ...

foreach (var smr in smrs) smr.forceMatrixRecalculationPerRender = false;
```

これは PrefabSentinel の `HandleCaptureScreenshot` 内で既に実装済 (`PrefabSentinel.UnityEditorControlBridge.CameraView.cs`)。独自ヘルパースクリプトを書く時は同パターンを必ず使う。

## SceneView camera 状態の不安定さ (L2)

大量 (>500) の `Camera.Render()` を一度に回すと、SceneView state が internal で破綻し、**全 screenshot が baseline (avatar 不在の空画像) になる症状**を確認。md5 ハッシュ比較で全 PNG が同一になる。

復旧手順:
1. Bridge の `editor_screenshot` (`Tools/PrefabSentinel` 系 MCP) を 1 度叩く
2. その後で独自スクリプトを再実行 → 正常 capture できる

bridge の screenshot が SceneView state を内部 refresh する副作用があると推測。原因は未特定。**運用上は「menu_item 大量実行の前後で bridge screenshot を 1 度挟む」防御パターンを採用すると安全**。

## 構成パターン: 永続 Editor helper script (L2)

`editor_run_script` は bridge state stuck で詰まることがあるため、**`Assets/<機能ルート>/Editor/` に `[MenuItem]` 付き永続スクリプトを置き、`editor_execute_menu_item` で叩く** pattern が確実。

```csharp
// 例: Editor/BlendShapeCapture.cs
public static class BlendShapeCapture
{
    [MenuItem("Tools/<MyTool>/Capture")]
    public static void Run() {
        // ここで Camera.Render() + 全 SMR forceMatrixRecalc + WriteAllBytes
    }
}
```

このパターンで MCP 側は `editor_execute_menu_item` の 1 回呼び出しで Unity 内ロジックを全実行できる。再利用可能で、Bridge state に左右されない。

## 出力フォーマット推奨 (L3)

`Assets/<root>/_BlendShapeReports/<avatar_name>/` 配下に:

- `capture_config.json` — schema v2:
  ```json
  {
    "body_path": "/AvatarRoot/Body",
    "default_weights": [100],
    "shapes": [
      {"name": "eye_shape_X", "slug": "sNNNN"},
      {"name": "eyelid_Y", "slug": "sMMMM", "weights": [25, 50, 75, 100]}
    ]
  }
  ```
- `blendshapes.json` — 機械可読 (schema v3+): name/slug/category/mode/description/screenshots paths
- `camera_state.json` — SceneView state (再撮影用)
- `screenshots/<slug>_<weight>.png` — PNG ファイル群
- `screenshots/_baseline_0.png` — 全 shape weight=0 の baseline (共用)

slug は `s{NNNN}` (4桁 padding) 形式が robust (日本語 shape 名でも安全)。

## サブ agent 並列 description 化 (L2)

1000+ shape の screenshot を 1 個ずつ Opus が読むと context 圧迫 + 高コスト。Sonnet 4.6 sub-agent を 10 並列で dispatch し、各 agent が 65 shape を読んで JSON で description を返す pattern が効率的。

- 1 agent あたり ~50-100k 入力 token, ~5k 出力 token
- 並列 10 agent で 1000 shape を 5-10 分でカバー
- Opus 単独より 5-10x 安い

注意点:
- 各 agent には baseline screenshot を 1 回読ませて視覚的アンカーにする
- `needs_decomposition` フラグで agent が「視認困難」を返せる契約を作る (closeup 再撮影で 2nd pass を回せる)
- thumbnail 解像度 (Read tool downscale) で見える shape vs 見えない shape を把握する: Pupil 系 UV texture, tears/sweat overlay は thumbnail では見えにくい

## 2 段階撮影 pattern (main + closeup) (L3)

1 回の face frame 撮影だけだと、目細部 (pupil shape / iris pattern / eyelash) の差が thumbnail downscale で潰れる。**2 段階撮影** が有効:

1. **Pass 1 (main face frame)**: SceneView size 0.18 程度。avatar の顔全体 + 肩までを撮影。全 shape 1 周。
2. **Pass 2 (closeup)**: SceneView size 0.09 程度 (2x zoom)。Pass 1 で `needs_decomposition: true` だった shape のみを再撮影。目の細部 (虹彩/瞳孔/睫毛/眉) が thumbnail でも視認可能になる。

Camera state は両方とも `camera_state.json` に保存して再現可能に。

## MMD カテゴリ shape の扱い (L3)

一部のアバター系には `---MMD---` カテゴリ (あ/まばたき/にこり/困る/笑い/怒り/ウィンク 等の shape 群) があり、これは VRChat MMD ダンスワールドが自動駆動するための shape 群。

**FaceEmo や ExpressionMenu で独自表情を作る場合、MMD カテゴリの shape を使うとダンスワールドで競合する**。ダンスワールド入場時に強制的に weight 上書きされ表情が崩れる、というユーザ事例あり。

ポリシー:
- ダンスワールドで踊らないアバターなら MMD shape 利用 OK
- ダンス兼用アバターなら MMD 除外、`VRC_Viseme / Eye_type / Eyelid / Eyebrow / Mouth_* / Effect / PerfectSync` 等のプリミティブで表情を組成

## 関連 knowledge

- `prefab-sentinel-editor-camera.md` — SceneView camera 操作の細部
- `prefab-sentinel-workflow-patterns.md` — bridge state stuck 対策
- `face-emo.md` — FaceEmo に AnimationClip を組み込む手順
