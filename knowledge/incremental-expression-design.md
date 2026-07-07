---
tool: incremental-expression-design
version_tested: "0.5.167"
last_updated: 2026-05-11
confidence: high
---

# Incremental Expression Design — Base State + Recipe Override Pattern

VRChat アバターに「テーマ × 強度」の表情ライブラリ (e.g., 笑顔 L1-L5, うっとり L1-L5) を構築する際の `base_state + preview_recipe + AnimationClip` の三段階 idiom。FaceEmo / ModularAvatar / 独自 Animator から運用可能な .anim 群を作る pattern。

## 概要 (L1)

VRChat 表情ライブラリ構築の典型課題:
- アバターの「素の顔」(base) が既に細かく設定されている (preset eye shape / eyebrow / hilite override 等)
- ユーザは「base + 表情 nuance」を追加したい (base を壊さず)
- 同一テーマで強度違い (subtle → max) のグラデーションが欲しい
- 結果を AnimationClip 化して FaceEmo 等で gesture 切替に使いたい

この pattern では:
1. **base_state.json**: avatar の現在の非ゼロ blendshape を snapshot
2. **_preview_recipe.json**: 個別の表情 recipe (shape + weight 配列)
3. **AnimationClip**: recipe 単独の curve (base には触らない、override 差分のみ)

これにより:
- AnimatorController で base anim → expression anim → base anim の遷移時、base shape は AnimatorController 側で常に動かないので残る
- expression clip は override 分だけ持つので軽量 (5-10 curve)
- base が将来変わっても expression は relative に追従

## 三本柱 MenuItem (L2)

`Assets/.../_BlendShapeReports/Editor/ExpressionPreview.cs` の MenuItem 3 つで完結:

```csharp
[MenuItem("Tools/PrefabSentinel/BlendShape Capture/Snapshot Base State")]
public static void SnapshotBaseState() {
    // For each SkinnedMeshRenderer specified in preview_config.json:
    //   Iterate all blendshapes, collect non-zero weights, write to base_state.json
}

[MenuItem("Tools/PrefabSentinel/BlendShape Capture/Apply Preview Recipe")]
public static void ApplyPreviewRecipe() {
    // 1. Reset all blendshapes to 0
    // 2. Apply base_state.json
    // 3. Apply _preview_recipe.json (overrides base)
}

[MenuItem("Tools/PrefabSentinel/BlendShape Capture/Reset To Base")]
public static void ResetToBase() {
    // 1. Reset all blendshapes to 0
    // 2. Apply base_state.json
}
```

ファイル schema:

```json
// preview_config.json (semi-permanent per avatar)
{ "body_path": "/AvatarRoot/Body" }

// base_state.json (auto-generated)
{
  "shapes": [
    {"name": "eye_shape_ottori02", "weight": 50},
    {"name": "brow_tare01", "weight": 84.9},
    ...
  ]
}

// _preview_recipe.json (transient, overwritten per iteration)
{
  "name": "egao_L3",
  "shapes": [
    {"name": "mouth_close_smile01", "weight": 30},
    {"name": "mouth_open_smile02", "weight": 30},
    ...
  ]
}
```

## 反復 iteration workflow (L2)

LLM 駆動の場合の standard loop:

```
1. Edit _preview_recipe.json (write new shape/weight)
2. editor_execute_menu_item "Apply Preview Recipe"    [1 MCP call]
3. editor_set_camera (closeup pose 維持)              [1 MCP call]
4. editor_screenshot                                  [1 MCP call]
5. bash cp screenshot → expressions/screenshots/<name>_DRAFT.png
6. (Read screenshot to verify, or ask user)
7. Iterate: 2 → 6 with adjusted weights, or proceed to next level
```

1 iteration = 3 MCP call (vs 個別 set_blend_shape で 15-30 call 相当)。

承認後、`expressions.json` に追記 → `Tools/PrefabSentinel/BlendShape Capture/Save Expressions From JSON` で `.anim` 群を一括生成。

## Hybrid level design philosophy (L3)

L1〜L5 の強度グラデは 2 層で組む:

### Core shapes (テーマ核)
全 level で共有、weight だけ段階的に上昇。テーマのアイデンティティを担保。

例 (`uttori` で):
- Core: `eyelid_upper_down01` (上瞼下げ = 半目化)
- weight: L1=8 → L2=25 → L3=50 → L4=70 → L5=85

### Layered add-ons (層追加)
高 level でのみ nuance を足す追加 shape。

例 (同じ `uttori` で):
- L3 から `mouth_open_halfopen01` 登場 (脱力で口緩む)
- L4 から `brow_sad01` 登場 (眉頭わずか下げ = 感情深度の物理症状)
- L5 で `tears_01` 登場 (満足の余涙)

これにより L1 → L5 は「同じ shape の単純な weight 増加」ではなく「強度 + nuance の累積」になる。

## 既存テーマとの干渉回避 (L2)

複数テーマ (例: `egao`, `jiai`, `uttori`, `moe`, `koakuma`) を同じアバターで運用する場合、各テーマの "核 shape" を直交させると合成時の衝突が減る:

| テーマ (例) | 核 shape (避けるべき conflict) |
|-----------|-------------------------------|
| egao (笑顔) | `mouth_close_smile` / `mouth_open_smile` (口角) |
| jiai (慈愛) | `eyelid_close_maroyaka01` (柔丸閉じ = 女神顔) |
| uttori (うっとり) | `eyelid_upper_down01` (上瞼下げ = 半目) |
| moe (萌え) | `iris_big` + `HL_main_heart/4star01` (虹彩拡大 + 特殊hilite) |
| koakuma (小悪魔) | `mouth_close_niyari` 系 (にやり) |

これらが直交していれば、FaceEmo の AnimatorController で別 layer に置く時 conflict 減。

## Thumbnail vs Full resolution の罠 (L3)

LLM (Claude Code 等) で screenshot を Read tool 経由で参照する場合、800x800 PNG が ~128-256 px に downscale される。これにより:

- **見える**: 口の開閉 / 眉位置 / 頬染め色 / 大きな mesh deformation
- **見えない (thumbnail)**: iris/虹彩の大きさ変化 / hilite shape (heart/star) / 睫毛形状 / 瞳孔
- **見えない (closeup でも)**: UV texture animation shape / overlay shape (tears/sweat 系一部)

実用 implication:
- LLM 駆動の表情 iteration で texture/UV 系 shape を recipe に含めても LLM 自身は verify 不可
- texture shape の効果は user 側で Unity 原寸確認に依存
- → core shape は mesh-deformation 系を主軸にし、texture shape は補助的に積む設計が iteration 効率良い

### 緩和策

- 撮影 zoom を眼領域に絞る (camera size 0.06 程度) → eye 細部が thumbnail でも見える
- ただし全体の表情バランス確認には不向き → main view + closeup の 2 段階撮影
- bridge に `editor_screenshot` の `crop_roi` パラメータがあれば理想

## 同名アバターの「ベースの顔」差異 (L3)

同一アバター系の典型 base 構成 (snapshot 結果から):

```
eye_shape_ottori02       50    # おっとり目 (癒し系基調)
eyelash_top05_hide       100   # カスタム睫毛スタイル (一部非表示)
eyelash_top06_hide       100
eyelash_top07_hide       100
eyelash_side01_hide      100
eyelash_side02_hide      100
eyelash_bottom01_hide    100
iris_04                  100   # 虹彩プリセット 04
HL_adjust_big            100   # 大きめハイライト調整
HL_main_hide             100   # デフォルトハイライト非表示
HL_main_round_wide       100   # 丸ワイドハイライトで置換
brow_maro                35.4  # 麻呂眉 slight
brow_tare01              84.9  # たれ眉 strong
```

この組合せが「癒し系・優しい・たれ目」キャラ identity を作っている。表情合成の override はこの上に乗る。

例えば `egao_L5` 満面の笑みでも `brow_tare01=84.9` (base) は維持され、その上に `eyelid_close_smile00=45` が乗る (override)。base の brow_tare01 を override で 0 にすると「キャラ性が消える」ので避ける。

### HL_main_* 系の置換ルール

base が `HL_main_round_wide=100` の状態で別 hilite shape (heart, 4star, urumi 等) に切り替える場合:

- override recipe で `HL_main_round_wide: 0` を明示
- 同時に `HL_main_heart: 100` (or 4star01, urumi 等) を指定

両方 100 にすると Unity 上で重複適用される (混在 hilite) ことがあり、設計通りの hilite shape にならない。

## AnimationClip 永続化 (L2)

承認済 recipe を AnimationClip に変換する helper の最小実装:

```csharp
var clip = new AnimationClip();
clip.frameRate = 60f;

foreach (var sw in exp.shapes) {
    // 2-keyframe constant curve = AnimatorController の State 検出を確実にする
    var curve = new AnimationCurve(
        new Keyframe(0f, sw.weight, 0f, 0f),
        new Keyframe(1f / 60f, sw.weight, 0f, 0f)
    );
    clip.SetCurve(relativePath, typeof(SkinnedMeshRenderer),
                  "blendShape." + sw.name, curve);
}

AssetDatabase.CreateAsset(clip, outPath);
```

ポイント:
- `relativePath` は AnimationClip 内の SkinnedMeshRenderer への相対パス (e.g., `Body`)
- `blendShape.<name>` が SkinnedMeshRenderer の binding パス
- 2-keyframe constant 形 (t=0 と t=1/60、同値) で State Length=1/60 sec を保証
- inSlope / outSlope = 0 で純 constant curve (補間しない)
- recipe に含まれていない shape は curve なし → Animator 動作で「default」(=0) に戻る

## まとめ

`base_state + preview_recipe + AnimationClip` の 3 段階 idiom は:
- LLM 駆動の table-top iteration に向く
- base に手を付けず override 差分のみ管理
- AnimationClip 化で FaceEmo 等の運用フローに直結
- thumbnail 駆動の verification 限界を理解した上で mesh 系 shape を core に据える

## 関連 knowledge

- `blendshape-capture-pipeline.md` — 全 BlendShape の系統 capture
- `expression-synthesis-via-animationclip.md` — AnimationClip 化の基礎
- `face-emo.md` — 生成 .anim を gesture スロットに配する
