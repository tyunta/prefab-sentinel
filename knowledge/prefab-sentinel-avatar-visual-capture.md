---
tool: prefab-sentinel-avatar-visual-capture
version_tested: "prefab-sentinel 0.7.1"
last_updated: 2026-05-31
confidence: low
---

# prefab-sentinel avatar visual capture

VRChat avatar を Unity Editor 上で撮影し、見た目確認・比較・レビュー用の画像を作るときの PrefabSentinel 運用パターン。

## 基本情報

PrefabSentinel は Unity serialized asset の検査・安全編集・Editor Bridge 操作に強い。avatar visual capture では、対象 avatar の scope 確定、SceneView camera の初期確認、bridge 接続先の検証、撮影コンポーネントや capture script の実行補助に使う。

runtime 物理、ClientSim、pose animation の再生後状態、表情 animation の適用結果は serialized asset の静的情報だけでは保証できない。これらが見た目確認に関わる場合は、PrefabSentinel の live Editor 操作と scene-side capture component を組み合わせる。

## 主要 API・概念

### Bridge target identity

複数 Unity project で Editor Bridge が有効な環境では、操作先の project を明示する。bridge 接続先がずれると、撮影対象・pose 適用結果・表情適用結果のすべてが無効になる。

capture workflow では、各操作ログまたは manifest に少なくとも以下を残すとよい。

| 項目 | 用途 |
|---|---|
| Unity project path / id | 複数 project 起動時の誤接続検出 |
| scene path | runtime capture の再現性 |
| target avatar root | pose / face / camera の基準 |
| capture resolution | crop derived 画像との区別 |
| pose id / face id / camera id | 撮影条件の監査 |

### Static inspection と runtime capture

Prefab / scene YAML inspection は、参照欠損、階層、material、serialized field の確認に使う。runtime capture は、ClientSim、PhysBone 風の揺れ、AnimationClip 再生、表情変更、数 frame 待機後の見た目を画像化するために使う。

### Native capture と crop derived image

既存画像を crop したものと、camera framing を変えて同じ resolution で撮り直したものは別物として扱う。顔寄り・上半身・全身などの構図を増やす場合は、可能な限り native resolution で再撮影し、crop derived 画像は manifest 上で区別する。

### Capture manifest

visual capture は画像ファイルだけでなく、撮影条件を manifest として残す。manifest は reject / replacement の追跡、撮影条件の再現、比較レビュー時の差分確認に使う。

## 使い分け

### PrefabSentinel inspection を使う場面

- target avatar root と asset scope を確定する
- Prefab / scene / material の参照欠損を調べる
- camera framing の初期値を試す
- animation clip / face clip の asset 存在を確認する
- scene-side capture component の配線を検査する

### scene-side capture component を使う場面

- ClientSim / Play Mode 中の物理反映後に撮影する
- pose と face を順番に切り替えて連続撮影する
- pose 切替後に数 frame 待ってから撮影する
- GameView / render camera で正確な resolution を保証する
- capture manifest を画像と同時に出力する

### PrefabSentinel screenshot だけに依存しない場面

- skirt / hair / accessory physics が見た目確認に関わる
- animation clip の binding が対象 avatar に本当に効くか不明
- 表情や瞬きの timing が画像品質に関わる
- 同じ pose だけではレビュー観点が不足する

## 落とし穴

### Bridge target の誤接続

条件: 複数 Unity project が開いており、それぞれに Editor Bridge が存在する。

症状: pose / face が反映されない、撮影対象が想定と違う、操作結果が一貫しない。

回避策: project target を明示し、bridge 応答の project path / scene / target root を確認してから capture を始める。

### AnimationClip 適用の silent failure

条件: pose / face clip の binding path が target avatar の階層や BlendShape 名と一致しない。

症状: clip は存在するが、撮影画像では pose / expression が変わらない。

回避策: capture 前に binding hit 数、未解決 binding、適用後に変化した Transform / BlendShape 数を確認する。確認 API がない場合は、少数枚の trial capture を必ず挟む。

### 物理未反映の静止撮影

条件: scene object を静的に撮影し、ClientSim / Play Mode / settle frames を通さない。

症状: skirt、hair、accessory が T-pose 由来または初期状態のまま写る。

回避策: runtime capture component で pose 切替後に数 frame 待機し、物理が落ち着いてから撮影する。

### crop derived 画像の混入

条件: 顔寄りや上半身構図を既存画像の crop で生成する。

症状: resolution は同じでも、camera perspective と detail distribution が native capture と異なる。

回避策: 構図差分は camera framing を変えて再撮影する。crop を使う場合は manifest に `derived_from` と crop rect を残す。

### pose variation 不足

条件: 手・腕・表情・カメラワークの variation が狭い状態で連続撮影する。

症状: 比較レビュー時に、対象の見た目差分ではなく同じ pose / expression / camera angle の癖ばかりが目立つ。

回避策: capture plan で手状態・腕位置・顔向き・表情・camera distance をカテゴリ化し、同じ状態が連続または過半数にならないようにする。

### eye blink / expression timing の混入

条件: 表情 animation や blink が有効なまま、連続撮影の任意 frame を採用する。

症状: 閉じ目、半目、非意図の表情が混ざる。

回避策: capture component 側で blink を固定または無効化し、撮影後に eye-open QA / manual reject / replacement の loop を設ける。

## 関連 knowledge

- [prefab-sentinel-workflow-patterns](./prefab-sentinel-workflow-patterns.md)
- [prefab-sentinel-editor-camera](./prefab-sentinel-editor-camera.md)
- [kawaii-posing](./kawaii-posing.md)
- [face-emo](./face-emo.md)
