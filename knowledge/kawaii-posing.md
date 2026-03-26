---
tool: kawaii-posing
version_tested: "kawaiiposing 3.0.5 / posingsystem 3.0.6"
last_updated: 2026-03-26
confidence: medium
---

# 可愛いポーズツール (KawaiiPosing) + ゆにさきポーズシステム (PosingSystem)

## 概要 (L1)

VRChat アバター向けのポーズ固定ツール。3 点トラッキングやデスクトップモードでも座り・寝姿勢など多彩なポーズを自動で切り替えられるようにする。

**2 パッケージの関係**:
- **PosingSystem** (`jp.unisakistudio.posingsystem`): ベースシステム。ポーズレイヤーの定義、アニメーション管理、NDMF ビルドパイプライン統合、VRChat Expression メニュー生成を担う。ModularAvatar と NDMF に依存する。
- **KawaiiPosing** (`jp.unisakistudio.kawaiiposing`): PosingSystem を継承した製品パッケージ。立ち 15 種・椅子 20 種・床 17 種・うつ伏せ寝 16 種・あおむけ寝 17 種のプリセットアニメーションと、プレビルド機能、アバター別プリセット自動適用を同梱する。PosingSystem `^3.0.6` に依存する。

**動作原理**: コンポーネント自体は `IEditorOnly` でビルドに残らない。NDMF の Resolving フェーズで `PosingSystemConverter` プラグインが実行され、PosingSystem / KawaiiPosing コンポーネントの定義を解析して ModularAvatar コンポーネント (MergeAnimator 等) とアニメーターコントローラーに変換する。変換後はすべて MA/NDMF の標準パイプラインで処理される。

**プレビルド機能** (v3.0.0+): 毎回のアバタービルドで変換処理を走らせると時間がかかるため、Inspector のボタンで事前に MA コンポーネントへ変換しておく仕組み。`data` フィールドに JSON シリアライズされた定義のハッシュが保存され、変更がなければビルド時の再変換をスキップする。

**NDMF 実行順序**:
1. `PosingSystemConverter` (Resolving, AfterPlugin: MA): ポーズ定義 → MA コンポーネント変換
2. `PosingSystemConverter` (Generating, BeforePlugin: MA): トラッキング制御統合
3. `DuplicateEraserConverter` (Resolving): 重複 PosingSystem Prefab の除去
4. `DuplicateEraserConverter` (Optimizing, BeforePlugin: AvatarOptimizer): DuplicateEraser コンポーネント自体を除去

**プラットフォーム**: VRChat SDK 3.x。MA >= 1.15.1、NDMF >= 1.10.1 が必要。

**配布**: VPM パッケージ。GitHub リリースから zip で配布。
- KawaiiPosing: https://github.com/UnisakiStudio/KawaiiPosing
- PosingSystem: https://github.com/UnisakiStudio/PosingSystem

## コンポーネント一覧 (L1 -> L2)

### ポーズ制御

| コンポーネント名 | 用途 | 典型的な使用場面 | パッケージ |
|---|---|---|---|
| PosingSystem | ポーズレイヤー定義・アニメーション管理・Expression メニュー生成のベース | アバター直下に配置。Inspector でポーズの有効/無効、カスタムアニメーション設定、閾値調整を行う | posingsystem |
| KawaiiPosing | PosingSystem を継承した製品コンポーネント。機能的な追加フィールドはなし | アバター右クリック → "ゆにさきスタジオ" → "可愛いポーズツール追加" で Prefab ごと配置 | kawaiiposing |
| PosingOverride | 既存の立ち・しゃがみ・伏せ等のデフォルトモーションを上書き | アバターに独自の Stand/Crouch/Prone 姿勢がある場合に使用。ビルド時に自動実行オプションあり | posingsystem |
| DuplicateEraser | 同一 ID の重複オブジェクトをビルド時に除去 | 同じ Prefab を複数回配置してしまった場合の安全装置。`ID` フィールドで同一性判定 | posingsystem |

### Editor 専用 (ScriptableObject)

| コンポーネント名 | 用途 | 典型的な使用場面 | パッケージ |
|---|---|---|---|
| PosingSystemPresetDefines | アバター別プリセット定義を格納する ScriptableObject | Prefab 配置時にアバターの GUID/名前マッチングで自動プリセット適用 | posingsystem (Editor) |
| PosingSystemThumbnailPack | メニューサムネイルパックのマーカー ScriptableObject | Inspector で thumbnailPackObject に設定するとメニューアイコンを差し替え | posingsystem (Runtime) |

## 操作パターン (L2)

### ポーズツール導入の基本パターン

1. アバターの VRCAvatarDescriptor オブジェクトを Hierarchy で右クリック
2. "ゆにさきスタジオ" → "可愛いポーズツール追加" を選択
3. KawaiiPosing Prefab がアバター直下にインスタンス化される
4. アバターに対応するプリセットが見つかった場合、ダイアログで自動適用を確認
5. プレビルドダイアログで「はい」を選ぶと MA コンポーネントへの事前変換が実行される
6. アバターをアップロードすると、NDMF パイプラインで残りの処理が自動実行される

### ポーズのカスタマイズパターン

1. KawaiiPosing / PosingSystem の Inspector を開く
2. `defines` リストの各 LayerDefine (ポーズカテゴリ) を展開
3. 各 AnimationDefine の `enabled` をオフにして不要なポーズを無効化
4. `animationClip` に任意の AnimationClip をドロップして差し替え
5. `adjustmentClip` にアバター固有の調整アニメーションを設定 (v3.0.0+)
6. "プレビルド" ボタンで MA コンポーネントを再生成

### デフォルトモーション上書きパターン

1. アバター直下に空の GameObject を作成し、PosingOverride コンポーネントを付与
2. `overrideDefines` に OverrideAnimationDefine を追加
3. `stateType` で対象 (StandWalkRun, Crouch, Prone, Jump 等) を選択
4. `animationClip` に差し替えるモーションを設定
5. ビルド時に該当のアニメーションステートが自動上書きされる

### 他商品連携パターン

1. 右クリックメニュー → "ゆにさきスタジオ" → "他商品連携用Prefabs" から連携 Prefab を追加
2. 対応商品: 可愛い座りツール、添い寝ツール、少女モーション集、VRC 想定移動モーション、ごろ寝システム EX
3. 連携 Prefab は PosingSystem の LayerDefine として追加され、メニューに統合される
4. DuplicateEraser が重複を自動除去するため、同一 Prefab の多重配置は安全

## SerializedProperty リファレンス (L3)

ソースバージョン: kawaiiposing 3.0.5 (Shiratsume) + posingsystem 3.0.6 (Shiratsume)、2.2.5 (PF-TEST) と差分検証済み
検証方法: Runtime/*.cs ソースコード読み + .meta ファイルの GUID 抽出 (inspect 実測なし -> confidence: medium)

### Script GUID テーブル

| コンポーネント | GUID | パッケージ | 備考 |
|---|---|---|---|
| KawaiiPosing | `3b8567e1c28a0e44e8329e1e580e10bd` | kawaiiposing | PosingSystem を継承。v3.0.5 で固有フィールドなし |
| PosingSystem | `e3e0024a473359d4b8be8f7430848a54` | posingsystem | メインコンポーネント |
| PosingOverride | `ce6c21eb904abfa47ba60b69c92e20a0` | posingsystem | デフォルトモーション上書き |
| DuplicateEraser | `64057a377e671e7498a11e1eec12457d` | posingsystem | 重複除去マーカー |

**GUID 安定性**: Shiratsume (v3.0.x) と PF-TEST (v2.2.5) で全 4 コンポーネントの GUID が一致。パッケージバージョン間で GUID は安定している。

### コンポーネント別フィールド

#### PosingSystem

PosingSystem は `IEditorOnly` を実装しており、ビルド後のアバターには残らない。フィールドはすべてエディタ時のみ有効。

| propertyPath | 型 | 説明 |
|---|---|---|
| `developmentMode` | bool | 開発モード [HideInInspector] (デフォルト: false) |
| `settingName` | string | 設定名 |
| `isIconDisabled` | bool | アイコン生成無効 (Quest 用、デフォルト: false) |
| `isIconSmall` | bool | アイコンサイズ縮小 (デフォルト: false) **v3.0.2+** |
| `mergeTrackingControl` | bool | トラッキング機能統合 [HideInInspector] (デフォルト: true) **v3.0.2+** |
| `autoImportAvatarAnimations` | bool | アバター姿勢設定自動インポート [HideInInspector] (デフォルト: true) **v3.0.2+** |
| `previewAvatarObject` | GameObject | プレビュー用アバター [HideInInspector] **v3.0.0+** |
| `defines` | List\<LayerDefine\> | ポーズレイヤー定義リスト |
| `overrideDefines` | List\<OverrideAnimationDefine\> | デフォルトモーション上書き定義 **v3.0.0+** |
| `SubmenuRoot` | Transform | サブメニューのルート Transform |
| `data` | string | プレビルド済み JSON (変更検出用) |
| `savedInstanceId` | string | 保存済みインスタンス ID **v3.0.0+** |
| `thumbnailPackObject` | Object | サムネイルパック ScriptableObject **v3.0.0+** |

**LayerDefine** (Serializable class):
| propertyPath (prefix: `defines.Array.data[n].`) | 型 | 説明 |
|---|---|---|
| `menuName` | string | Expression メニューでの表示名 |
| `description` | string | 説明文 |
| `stateMachineName` | string | Animator の StateMachine 名 |
| `paramName` | string | VRChat パラメーター名 |
| `icon` | Texture2D | メニューアイコン |
| `locomotionTypeValue` | int | ロコモーションタイプ値 (姿勢カテゴリ識別) |
| `animations` | List\<AnimationDefine\> | このレイヤーに属するアニメーション定義 |

**BaseAnimationDefine** (AnimationDefine/OverrideAnimationDefine の基底):
| propertyPath (suffix) | 型 | 説明 |
|---|---|---|
| `enabled` | bool | 有効/無効 (デフォルト: true) |
| `isRotate` | bool | 回転有効 |
| `rotate` | int | 回転角度 |
| `isMotionTime` | bool | MotionTime パラメーター使用 |
| `motionTimeParamName` | string | MotionTime パラメーター名 |
| `animationClip` | Motion | アニメーションクリップ |
| `previewImage` | Texture2D | プレビュー画像 |
| `adjustmentClip` | AnimationClip | アバター固有調整クリップ **v3.0.0+** |

**AnimationDefine** (BaseAnimationDefine を継承):
| propertyPath (prefix: `defines.Array.data[n].animations.Array.data[m].`) | 型 | 説明 |
|---|---|---|
| `displayName` | string | 表示名 |
| `initial` | bool | 初期選択 |
| `initialSet` | bool | 初期値設定済み |
| `isCustomIcon` | bool | カスタムアイコン使用 |
| `icon` | Texture2D | カスタムアイコン画像 |
| `typeParameterValue` | int | タイプパラメーター値 (自動割り当て) |
| `syncdParameterValue` | int | 同期パラメーター値 (自動割り当て) |

**OverrideAnimationDefine** (BaseAnimationDefine を継承):
| propertyPath (prefix: `overrideDefines.Array.data[n].`) | 型 | 説明 |
|---|---|---|
| `stateType` | AnimationStateType | 対象ステート: StandWalkRun=0, Stand=1, Crouch=2, Prone=3, Jump=4, ShortFall=5, ShortLanding=6, LongFall=7, LongLanding=8, AvatarSelect=9 **v3.0.0+ で AvatarSelect 追加** |
| `animationClip` | Motion | 上書きアニメーション |

#### KawaiiPosing

PosingSystem を継承。v3.0.5 では追加の SerializedField はない (v2.2.5 では `isKawaiiPosingLicensed: bool [HideInInspector]` があったが v3.0.5 で削除)。

全フィールドは PosingSystem と同一。Script GUID が異なるため、コンポーネント識別時は `3b8567e1c28a0e44e8329e1e580e10bd` を使用する。

#### PosingOverride

| propertyPath | 型 | 説明 |
|---|---|---|
| `ビルド時自動実行` | bool | ビルド時の自動実行フラグ [HideInInspector] (デフォルト: false)。日本語フィールド名に注意 |
| `deleteExistingLayer` | bool | 既存レイヤー削除 [HideInInspector] (デフォルト: true) |
| `mergeTrackingControl` | bool | トラッキング制御統合 [HideInInspector] (デフォルト: true) |
| `deleteExistingTrackingControl` | bool | 既存トラッキング制御削除 [HideInInspector] (デフォルト: false) |

**注意**: v2.2.5 では `defines: List<OverrideDefine>` (内部に `type: AnimationStateType` と `animation: Motion`) が存在したが、v3.0.0 で PosingSystem 本体の `overrideDefines` に統合され、PosingOverride 側の `defines` は削除された。v2 系と v3 系で構造が大きく異なる。

#### DuplicateEraser

| propertyPath | 型 | 説明 |
|---|---|---|
| `ID` | string | 重複判定用の識別子。同一 ID のコンポーネントが複数存在する場合、最初のもの以外がビルド時に除去される |

### 2.2.5 -> 3.0.x 変更サマリー

- **PosingSystem 構造変更**:
  - `AnimationDefine` を `BaseAnimationDefine` + `AnimationDefine` の継承構造に分割
  - `OverrideAnimationDefine` を PosingSystem 内のネストクラスとして追加 (PosingOverride から移動)
  - 新フィールド: `isIconSmall`, `mergeTrackingControl`, `autoImportAvatarAnimations`, `previewAvatarObject`, `overrideDefines`, `savedInstanceId`, `thumbnailPackObject`
  - `adjustmentClip` を BaseAnimationDefine に追加 (アニメーション調整機能)
  - `AnimationStateType` に `AvatarSelect` 追加
  - `PreviewMask` 定数 (= 21) 追加
  - `isWarning`, `isError`, `previousErrorCheckTime` (NonSerialized) 追加
  - `menuImage` (AnimationDefine 内) 削除
- **KawaiiPosing**: `isKawaiiPosingLicensed` フィールド削除
- **PosingOverride**: `defines: List<OverrideDefine>` 削除。オーバーライド定義は PosingSystem 側の `overrideDefines` に一本化
- **PosingSystem (PF-TEST v2.2.5)**: `isPosingSystemLicensed` フィールドあり (v3.0.x で削除)
- **新機能**: プレビルド、アニメーション調整、プリセット自動適用

### 設計上の注意点

- **IEditorOnly**: 全 4 コンポーネントが `IEditorOnly` を実装。ビルド後のアバターには残らない。`PosingSystemConverter` がビルド時に MA コンポーネント (MergeAnimator 等) に変換する。
- **日本語フィールド名**: `PosingOverride.ビルド時自動実行` は日本語の propertyPath を持つ。YAML パッチや inspect 時に注意。
- **data フィールドの役割**: `PosingSystem.data` は JSON シリアライズされた定義のハッシュ。プレビルド済みかどうか、および定義が変更されたかの検出に使用される。`data` が空または現在の定義と不一致の場合、ビルド時に毎回変換が走る。
- **プリセット適用のマッチング**: Prefab 配置時に、アバターの PrefabVariant チェーンを遡って GUID ハッシュ (MD5) と名前の両方でプリセットを検索する。
- **NDMF 実行順序の制約**: Resolving フェーズで `PosingSystemConverter` は MA の後 (`AfterPlugin: nadena.dev.modular-avatar`) に実行される。MA の Resolving パスが先に走ってから PosingSystem の変換が行われるため、PosingSystem が生成した MA コンポーネントは次のビルドフェーズ以降で MA に処理される。一方、Generating フェーズのトラッキング制御統合は MA の前 (`BeforePlugin: nadena.dev.modular-avatar`) に実行される。

## 実運用で学んだこと

(なし -- Phase 3-4 の検証で追記する)
