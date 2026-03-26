---
tool: vrcfury
version_tested: "1.1288.0"
last_updated: 2026-03-26
confidence: medium
---

# VRCFury

## 概要 (L1)

非破壊アバター改変ツール。独自のビルドパイプラインを持ち、ビルド時にコンポーネントを処理してアバターを組み立てる。NDMF には依存せず、VRCFury 独自の hook で VRChat SDK のビルドプロセスに統合される。最終アバターにはランタイムコンポーネントが残らない（`IVrcfEditorOnly` → 新 SDK では `IEditorOnly` 継承、旧 SDK ではホワイトリストパッチで除去）。

**解決する問題**: アニメーター統合・トグル・ジェスチャー・SPS (Super Penetration System) などの設定を非破壊で構成する。手動でアニメーターレイヤーやパラメーターを組む煩雑さを排除し、コンポーネント1つで完結させる。

**NDMF との関係**: VRCFury は NDMF に依存しないが、NDMF がプロジェクトに存在する場合は NDMF 側が VRCFury のビルドフックを認識して実行順序を調整する（NDMF が VRCFury を呼ぶ側）。

**アーキテクチャ**: Modular Avatar と異なり、VRCFury は「1つの MonoBehaviour (`VRCFury`) + `[SerializeReference]` による多態」パターンを採用している。`VRCFury.content` フィールドに `FeatureModel` 派生クラスがシリアライズされ、1コンポーネント = 1機能となる。SPS 系 (HapticPlug, HapticSocket) と GlobalCollider のみ独立した MonoBehaviour を持つ。

**プラットフォーム**: VRChat アバター専用。`IVrcfEditorOnly` により VRChat SDK の IEditorOnly と互換。

**最新バージョン**: 1.1288.0 (Shiratsume プロジェクト)。VPM リポジトリ `https://vcc.vrcfury.com/` から配布。バージョニングは `1.NNNN.0` 形式（ビルド番号ベース）。

## コンポーネント一覧 (L1→L2)

### シリアライズアーキテクチャの特殊性

VRCFury は大半の機能を単一 MonoBehaviour `VRCFury` (GUID: `d9e94e501a2d4c95bff3d5601013d923`) の `content` フィールド (`[SerializeReference]`) に格納する。Prefab YAML 上では以下のように見える:

```yaml
MonoBehaviour:
  m_Script: {fileID: 11500000, guid: d9e94e501a2d4c95bff3d5601013d923, type: 3}
  content:
    rid: 1234567890  # SerializeReference の managed reference ID
```

`[SerializeReference]` は Unity の managedReferences テーブルに格納されるため、通常の `[SerializeField]` とは異なるシリアライズパスを持つ。prefab-sentinel で `propertyPath` を読む場合、`content` 以下のパスは `SerializeReference` 経由のアクセスとなる。

### トグル・アニメーション制御

| Feature クラス | 用途 | 典型的な使用場面 |
|---|---|---|
| Toggle | GameObject のトグル、スライダー、排他タグ | 衣装・アクセサリのオン/オフ。メニュー項目自動生成、ローカル/リモート分離、トランジション対応 |
| Puppet | 2軸パペットメニュー | 表情の XY 制御。stops リストで各方向の State を定義 |
| FullController | 既存アニメーターコントローラの統合 | 外部製ギミックの FX レイヤー統合。パス書き換え、パラメーター統合、グローバルパラメーター対応 |
| GestureDriver | ハンドジェスチャーによる状態駆動 | 表情切替。Hand(EITHER/LEFT/RIGHT/COMBO) x HandSign(8種) の組み合わせ、ウェイト対応 |
| SenkyGestureDriver | Senky式ジェスチャーシステム | 目・口・耳のプリセット表情。State ベースで各部位を個別制御 |

**State / Action システム**: Toggle, Puppet, GestureDriver 等の機能は `State` クラスを介してアクションを定義する。`State.actions` は `[SerializeReference] List<Action>` で、以下の Action 型を多態的に格納する:

| Action クラス | 用途 |
|---|---|
| ObjectToggleAction | GameObject の on/off/toggle |
| BlendShapeAction | ブレンドシェイプ値の設定 |
| MaterialAction | マテリアルスロットの差替 |
| MaterialPropertyAction | マテリアルプロパティの変更 (Float/Color/Vector/ST) |
| AnimationClipAction | アニメーションクリップの再生 |
| ScaleAction | オブジェクトスケールの変更 |
| FxFloatAction | FX パラメーター float 値の設定 |
| FlipbookAction | Poiyomi フリップブック制御 |
| FlipBookBuilderAction | フリップブック自動生成 (ページ = State リスト) |
| PoiyomiUVTileAction | Poiyomi UV タイル制御 |
| ShaderInventoryAction | シェーダーインベントリスロット制御 |
| SmoothLoopAction | 2状態間のスムーズループ |
| WorldDropAction | ワールドドロップ制約の適用 |
| ResetPhysboneAction | PhysBone のリセット |
| BlockBlinkingAction | まばたき抑制 (マーカー) |
| BlockVisemesAction | ビセム抑制 (マーカー) |
| DisableGesturesAction | ジェスチャー無効化 (マーカー) |
| SpsOnAction | SPS Plug の有効化 |

すべての Action は基底クラスに `desktopActive`, `androidActive`, `localOnly`, `remoteOnly` フィールドを持つ（プラットフォーム・ネットワーク条件付き実行）。

### アーマチュア・ボーン操作

| Feature クラス | 用途 | 典型的な使用場面 |
|---|---|---|
| ArmatureLink | 衣装のボーン階層をアバターにリンク | 衣装プレファブのルートに配置。propBone → linkTo (HumanBodyBones or GameObject) で接続先を指定。recursive で子ボーンも再帰マージ |
| BoneConstraint | ボーンをヒューマノイドボーンに制約 | 単一オブジェクトをヒューマノイドボーンに追従させる |
| ShowInFirstPerson | 一人称でヘッドアクセサリを表示 | Head ボーン配下のオブジェクトを一人称カメラで見えるようにする |
| HeadChopHead | ヘッドチョップ制御 | VRChat のヘッドボーン非表示に関する処理 (フィールドなし) |
| ConstraintRetarget | 制約のリターゲット | ヒューマノイドボーンを指定してコンストレイントをリターゲット |

**ArmatureLink の LinkTo**: 複数の接続先候補を優先順位付きで指定可能。`useBone=true` でヒューマノイドボーン、`useObj=true` で直接オブジェクト参照、両方 false で文字列パス (`offset`) を使用する。

### SPS (Super Penetration System)

| コンポーネント / Feature | 用途 | 典型的な使用場面 |
|---|---|---|
| VRCFuryHapticPlug (独立MonoBehaviour) | SPS プラグ | ペネトレーション用プラグ設定。自動リグ、ボーンマスク、深度アクション対応 |
| VRCFuryHapticSocket (独立MonoBehaviour) | SPS ソケット | ペネトレーション用ソケット設定。ライトモード(Hole/Ring/Auto)、タッチゾーン、深度アクション対応 |
| VRCFuryHapticTouchSender (独立MonoBehaviour) | タッチ送信 | 触覚フィードバック送信。radius のみ |
| VRCFuryHapticTouchReceiver (独立MonoBehaviour) | タッチ受信 | 触覚フィードバック受信。name + radius |
| SpsOptions (Feature) | SPS グローバル設定 | メニューアイコン・パス・ソケット保存設定 |

**DepthActionNew**: HapticPlug / HapticSocket で共有される深度駆動アクション。range (Vector2), units (Meters/Plugs/Local), smoothingSeconds, enableSelf, reverseClip で設定。

### コライダー

| コンポーネント / Feature | 用途 | 典型的な使用場面 |
|---|---|---|
| VRCFuryGlobalCollider (独立MonoBehaviour) | グローバルコライダー | radius + height + rootTransform で形状定義 |
| AdvancedCollider (Feature) | 詳細コライダー設定 | radius + height + rootTransform + colliderName。1.1288.0 で追加 |

### メニュー操作

| Feature クラス | 用途 | 典型的な使用場面 |
|---|---|---|
| MoveMenuItem | メニュー項目の移動 | fromPath → toPath でメニューパスを変更 |
| ReorderMenuItem | メニュー項目の並べ替え | path + position で順序を指定 |
| OverrideMenuSettings | メニュー設定の上書き | nextText + nextIcon で「次へ」ボタンをカスタマイズ |
| SetIcon | メニュー項目にアイコンを設定 | path でメニュー項目を指定し icon を設定 |

### アバター全体設定

| Feature クラス | 用途 | 典型的な使用場面 |
|---|---|---|
| FixWriteDefaults | Write Defaults の統一 | Auto/ForceOff/ForceOn/Disabled の4モード |
| DirectTreeOptimizer | Direct BlendTree 最適化 | フィールドなし。ビルド時に自動最適化 |
| BlendshapeOptimizer | ブレンドシェイプ最適化 | 未使用ブレンドシェイプの除去 |
| AvatarScale2 | アバタースケール制御 | フィールドなし。スケール変更の自動処理 |
| CrossEyeFix2 | 寄り目修正 | フィールドなし |
| AnchorOverrideFix2 | アンカーオーバーライド修正 | フィールドなし |
| BoundingBoxFix2 | バウンディングボックス修正 | フィールドなし |
| Slot4Fix | Slot 4 修正 | FX レイヤーのスロット4問題修正。フィールドなし |
| UnlimitedParameters | パラメーター上限回避 | includeBools + includePuppets で対象を選択 |
| MmdCompatibility | MMD ワールド互換 | disableLayers (レイヤー名リスト) + globalParam |
| RemoveBlinking | まばたき除去 | フィールドなし |
| RemoveHandGestures2 | ハンドジェスチャー除去 | フィールドなし |
| DescriptorDebug | デスクリプターデバッグ | フィールドなし |
| TPSIntegration2 | TPS 統合 | フィールドなし (レガシー互換) |
| OGBIntegration2 | OGB 統合 | フィールドなし (レガシー互換) |

### ビルド時操作

| Feature クラス | 用途 | 典型的な使用場面 |
|---|---|---|
| ApplyDuringUpload | アップロード時にアクション適用 | State ベースで一度だけ実行する操作。デフォルト表示状態の固定等 |
| DeleteDuringUpload | アップロード時に削除 | フィールドなし。配置されたオブジェクトをビルド時に除去 |

### 表情・アニメーション

| Feature クラス | 用途 | 典型的な使用場面 |
|---|---|---|
| Blinking | まばたき制御 | State + transitionTime + holdTime |
| Visemes | ビセム (口形状) 制御 | 14音素それぞれに State を定義 (PP, FF, TH, DD, kk, CH, SS, nn, RR, aa, E, I, O, U) |
| Talking | 発話時アクション | State 1つ |
| Toes | 足指制御 | down/up/splay の3 State |
| BlendShapeLink | ブレンドシェイプ同期 | ベースメッシュとリンクメッシュ間でブレンドシェイプを同期。include/exclude パターン対応 |

### セキュリティ

| Feature クラス | 用途 | 典型的な使用場面 |
|---|---|---|
| SecurityLock | PIN ロック | pinNumber で4桁 PIN を設定 |
| SecurityRestricted | セキュリティ制限付きマーカー | フィールドなし。FullController の toggleParam と連携 |

### デバッグ・表示

| Feature クラス | 用途 | 典型的な使用場面 |
|---|---|---|
| Gizmo | カスタムギズモ表示 | rotation + text + sphereRadius + arrowLength |

### レガシー (Migrate で変換される)

| Feature クラス | 変換先 | 備考 |
|---|---|---|
| Modes | 複数の Toggle に分解 | exclusiveTag で排他制御 |
| ObjectState | ApplyDuringUpload / DeleteDuringUpload | ACTIVATE/DEACTIVATE/DELETE をアクションに変換 |
| Breathing | Toggle + SmoothLoopAction | 呼吸アニメーションをトグル化 |
| WorldConstraint | Toggle + WorldDropAction | ワールドドロップをトグル化 |
| AvatarScale | AvatarScale2 | LegacyFeatureModel |
| CrossEyeFix | CrossEyeFix2 | LegacyFeatureModel |
| AnchorOverrideFix | AnchorOverrideFix2 | LegacyFeatureModel |
| BoundingBoxFix | BoundingBoxFix2 | LegacyFeatureModel |
| MakeWriteDefaultsOff / MakeWriteDefaultsOff2 | FixWriteDefaults (ForceOff) | LegacyFeatureModel |
| RemoveHandGestures | RemoveHandGestures2 | LegacyFeatureModel |
| OGBIntegration | OGBIntegration2 | LegacyFeatureModel |
| TPSIntegration | TPSIntegration2 | LegacyFeatureModel |

### その他独立 MonoBehaviour

| コンポーネント | GUID | 用途 |
|---|---|---|
| VRCFurySocketGizmo | `a93df76833a04889b400fea362da558c` | ソケットギズモ表示 |
| VRCFuryHideGizmoUnlessSelected | `973a3360b5c04a279549138b3d548290` | 選択時のみギズモ表示 |
| VRCFuryNoUpdateWhenOffscreen | `325fd324ef90492c8b08804b6f62cc0d` | オフスクリーン時の更新停止 |

## 操作パターン (L2)

### 衣装トグルの基本パターン

1. 衣装オブジェクトに `VRCFury` コンポーネントを追加
2. content に `Toggle` を設定
3. `name` にメニューパス (例: "Outfit/Jacket") を指定
4. `state.actions` に `ObjectToggleAction` (mode=TurnOn, obj=衣装オブジェクト) を追加
5. 必要に応じて `saved=true`, `defaultOn=true` を設定
6. ビルド時にパラメーター、アニメーション、メニュー項目が自動生成される

### 衣装ボーンリンクのパターン

1. 衣装ルートに `VRCFury` コンポーネントを追加
2. content に `ArmatureLink` を設定
3. `propBone` に衣装のアーマチュアルートを指定
4. `linkTo` に接続先 (HumanBodyBones.Hips 等) を設定
5. `recursive=true` でスキンメッシュのボーン参照を書き換え
6. `alignPosition/alignRotation/alignScale=true` で位置合わせ
7. `removeBoneSuffix` で衣装ボーンのサフィックスを除去してマッチング

### 表情ジェスチャーの設定パターン

1. アバタールートに `VRCFury` コンポーネントを追加
2. content に `GestureDriver` を設定
3. `gestures` リストに各ジェスチャー設定を追加:
   - `hand`: EITHER/LEFT/RIGHT/COMBO
   - `sign`: FIST/HANDOPEN/FINGERPOINT 等
   - `state.actions`: BlendShapeAction でブレンドシェイプを駆動
4. `enableWeight=true` でジェスチャーウェイト (握り込み量) に対応
5. `enableLockMenuItem` でジェスチャーロックメニューを自動生成

### 既存アニメーターの統合パターン

1. ギミックのルートオブジェクトに `VRCFury` コンポーネントを追加
2. content に `FullController` を設定
3. `controllers` に統合するアニメーターコントローラとレイヤータイプ (FX 等) を追加
4. `menus` にメニューアセットとプレフィックスを追加
5. `prms` にパラメーターアセットを追加
6. `rewriteBindings` でアニメーションパスを書き換え (from → to)
7. `globalParams` でグローバルパラメーターを指定
8. `rootObjOverride` でアニメーションパスの基準オブジェクトを変更

## SerializedProperty リファレンス (L3)

ソースバージョン: 1.1288.0 (Shiratsume) + 1.1227.0 (UnityTool_sample) 差分検証済み
検証方法: .cs ソースコード読み + .meta ファイルの GUID 抽出（inspect 実測なし → confidence: medium）

### Script GUID テーブル

**独立 MonoBehaviour (GameObject に直接アタッチ)**:

| コンポーネント | GUID | 備考 |
|---|---|---|
| VRCFury (メインコンテナ) | `d9e94e501a2d4c95bff3d5601013d923` | content フィールドに FeatureModel を格納 |
| VRCFuryHapticPlug | `2d4cd252f8c146639507ecd55bc2b48a` | SPS Plug |
| VRCFuryHapticSocket | `703106f8586c4f64b5aa39b6b4676684` | SPS Socket |
| VRCFuryHapticTouchSender | `26089cfa1cdd4901ad7650f312669353` | |
| VRCFuryHapticTouchReceiver | `f70fb861c8004cd4b5af94fd3fd7da0e` | |
| VRCFuryGlobalCollider | `8028d33728ef49f5b9d44b564480aeff` | |
| VRCFurySocketGizmo | `a93df76833a04889b400fea362da558c` | ランタイムギズモ |
| VRCFuryHideGizmoUnlessSelected | `973a3360b5c04a279549138b3d548290` | |
| VRCFuryNoUpdateWhenOffscreen | `325fd324ef90492c8b08804b6f62cc0d` | VRCFuryPlayComponent 継承 |

**注意**: Feature クラス (Toggle, ArmatureLink 等) は独立した Script GUID を持たない。すべて `VRCFury` (GUID: `d9e94e501a2d4c95bff3d5601013d923`) の `content` フィールド内に `[SerializeReference]` で格納される。YAML 上では `managedReferences` セクションに型情報付きで記録される。

### VRCFury コンテナの共通フィールド

| propertyPath | 型 | 説明 |
|---|---|---|
| `version` | int | アップグレードバージョン (内部管理) |
| `unityVersion` | string | 保存時の Unity バージョン |
| `vrcfuryVersion` | string | 保存時の VRCFury バージョン |
| `content` | FeatureModel (SerializeReference) | 機能本体。managedReferences に格納 |
| `config.features` | List\<FeatureModel\> (SerializeReference) | [Obsolete] 旧形式。content に移行済み |

### GuidWrapper 共通構造

多くのアセット参照は `GuidWrapper<T>` で包まれている (`GuidAnimationClip`, `GuidMaterial`, `GuidTexture2d`, `GuidController`, `GuidMenu`, `GuidParams`)。

| propertyPath (相対) | 型 | 説明 |
|---|---|---|
| `.id` | string | `"guid:fileID"` 形式のアセット識別子 |
| `.objRef` | Object | Unity オブジェクト直接参照 |

### Feature 別フィールド

#### Toggle
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `name` | string | メニューパス |
| `state` | State | アクションセット |
| `saved` | bool | パラメーター保存 |
| `slider` | bool | スライダーモード |
| `sliderInactiveAtZero` | bool | スライダー値0で非アクティブ |
| `defaultOn` | bool | デフォルトオン |
| `exclusiveOffState` | bool | 排他オフ状態 |
| `enableExclusiveTag` | bool | 排他タグ有効 |
| `exclusiveTag` | string | 排他タグ名 (カンマ区切り) |
| `securityEnabled` | bool | セキュリティ対応 |
| `hasExitTime` | bool | Exit Time 使用 |
| `enableIcon` | bool | アイコン有効 |
| `icon` | GuidTexture2d | メニューアイコン |
| `enableDriveGlobalParam` | bool | グローバルパラメーター駆動有効 |
| `driveGlobalParam` | string | 駆動するグローバルパラメーター名 |
| `separateLocal` | bool | ローカル/リモート分離 |
| `localState` | State | ローカル専用アクション |
| `hasTransition` | bool | トランジション有効 |
| `transitionStateIn` / `transitionStateOut` | State | トランジションイン/アウト |
| `transitionTimeIn` / `transitionTimeOut` | float | トランジション時間 |
| `localTransitionStateIn` / `localTransitionStateOut` | State | ローカルトランジション |
| `localTransitionTimeIn` / `localTransitionTimeOut` | float | ローカルトランジション時間 |
| `simpleOutTransition` | bool | 簡易アウトトランジション (デフォルト: true) |
| `defaultSliderValue` | float | デフォルトスライダー値 [0-1] |
| `useGlobalParam` | bool | グローバルパラメーター使用 |
| `globalParam` | string | グローバルパラメーター名 |
| `holdButton` | bool | ホールドボタンモード |
| `invertRestLogic` | bool | 休止ロジック反転 |
| `expandIntoTransition` | bool | トランジション展開 (デフォルト: true) |

#### ArmatureLink
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `propBone` | GameObject | リンク元のボーンルート |
| `linkTo` | List\<LinkTo\> | 接続先リスト (優先順位付き) |
| `linkTo.Array.data[n].useBone` | bool | ヒューマノイドボーン使用 (デフォルト: true) |
| `linkTo.Array.data[n].bone` | HumanBodyBones | ヒューマノイドボーン |
| `linkTo.Array.data[n].useObj` | bool | オブジェクト参照使用 |
| `linkTo.Array.data[n].obj` | GameObject | 接続先オブジェクト |
| `linkTo.Array.data[n].offset` | string | パスオフセット |
| `removeBoneSuffix` | string | ボーン名から除去するサフィックス |
| `removeParentConstraints` | bool | 親コンストレイント除去 (デフォルト: true) |
| `forceMergedName` | string | 強制マージ名 |
| `forceOneWorldScale` | bool | ワールドスケール統一 |
| `recursive` | bool | 再帰マージ |
| `alignPosition` | bool | 位置合わせ |
| `alignRotation` | bool | 回転合わせ |
| `alignScale` | bool | スケール合わせ |
| `autoScaleFactor` | bool | 自動スケール係数 (デフォルト: true) |
| `scalingFactorPowersOf10Only` | bool | 10のべき乗のみ (デフォルト: true) |
| `skinRewriteScalingFactor` | float | スキン書き換えスケール係数 (デフォルト: 1) |

#### FullController
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `controllers` | List\<ControllerEntry\> | コントローラーリスト |
| `controllers.Array.data[n].controller` | GuidController | アニメーターコントローラ |
| `controllers.Array.data[n].type` | AnimLayerType | レイヤー種別 (デフォルト: FX) |
| `menus` | List\<MenuEntry\> | メニューリスト |
| `menus.Array.data[n].menu` | GuidMenu | メニューアセット |
| `menus.Array.data[n].prefix` | string | メニュープレフィックス |
| `prms` | List\<ParamsEntry\> | パラメーターリスト |
| `prms.Array.data[n].parameters` | GuidParams | パラメーターアセット |
| `smoothedPrms` | List\<SmoothParamEntry\> | スムージングパラメーター |
| `smoothedPrms.Array.data[n].name` | string | パラメーター名 |
| `smoothedPrms.Array.data[n].smoothingDuration` | float | スムージング時間 (デフォルト: 0.2) |
| `smoothedPrms.Array.data[n].range` | SmoothingRange | ZeroToInfinity=0, NegOneToOne=1, Neg10kTo10k=2 |
| `globalParams` | List\<string\> | グローバルパラメーター名リスト |
| `allNonsyncedAreGlobal` | bool | 非同期パラメーターをすべてグローバルに |
| `ignoreSaved` | bool | saved 設定を無視 |
| `toggleParam` | string | トグルパラメーター名 |
| `rootObjOverride` | GameObject | ルートオブジェクト上書き |
| `rootBindingsApplyToAvatar` | bool | ルートバインディングをアバターに適用 |
| `rewriteBindings` | List\<BindingRewrite\> | パス書き換えルール |
| `rewriteBindings.Array.data[n].from` | string | 書き換え元 |
| `rewriteBindings.Array.data[n].to` | string | 書き換え先 |
| `rewriteBindings.Array.data[n].delete` | bool | 削除フラグ |
| `allowMissingAssets` | bool | 欠損アセット許容 |
| `injectSpsDepthParam` | string | SPS 深度パラメーター注入 |
| `injectSpsVelocityParam` | string | SPS 速度パラメーター注入 |

#### GestureDriver
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `gestures` | List\<Gesture\> | ジェスチャー設定リスト |
| `gestures.Array.data[n].hand` | Hand | EITHER=0, LEFT=1, RIGHT=2, COMBO=3 |
| `gestures.Array.data[n].sign` | HandSign | NEUTRAL=0, FIST=1, HANDOPEN=2, FINGERPOINT=3, VICTORY=4, ROCKNROLL=5, HANDGUN=6, THUMBSUP=7 |
| `gestures.Array.data[n].comboSign` | HandSign | COMBO 時の反対側ジェスチャー |
| `gestures.Array.data[n].state` | State | アクションセット |
| `gestures.Array.data[n].customTransitionTime` | bool | カスタムトランジション時間有効 |
| `gestures.Array.data[n].transitionTime` | float | トランジション時間 |
| `gestures.Array.data[n].enableLockMenuItem` | bool | ロックメニュー有効 |
| `gestures.Array.data[n].lockMenuItem` | string | ロックメニューパス |
| `gestures.Array.data[n].enableExclusiveTag` | bool | 排他タグ有効 |
| `gestures.Array.data[n].exclusiveTag` | string | 排他タグ名 |
| `gestures.Array.data[n].enableWeight` | bool | ウェイト対応 |

#### Puppet
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `name` | string | メニュー名 |
| `saved` | bool | パラメーター保存 |
| `slider` | bool | 1軸スライダーモード |
| `stops` | List\<Stop\> | 各方向の状態 |
| `stops.Array.data[n].x` | float | X 座標 |
| `stops.Array.data[n].y` | float | Y 座標 |
| `stops.Array.data[n].state` | State | アクションセット |
| `defaultX` | float | デフォルト X 値 |
| `defaultY` | float | デフォルト Y 値 |
| `enableIcon` | bool | アイコン有効 |
| `icon` | GuidTexture2d | メニューアイコン |

#### BlendShapeLink
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `linkSkins` | List\<LinkSkin\> | リンク対象スキンメッシュ |
| `linkSkins.Array.data[n].renderer` | SkinnedMeshRenderer | スキンメッシュレンダラー |
| `baseObj` | string | ベースオブジェクト名 |
| `includeAll` | bool | すべてのブレンドシェイプを含む (デフォルト: true) |
| `exactMatch` | bool | 厳密マッチ |
| `excludes` | List\<Exclude\> | 除外リスト |
| `excludes.Array.data[n].name` | string | 除外するブレンドシェイプ名 |
| `includes` | List\<Include\> | 明示的に含めるリスト |
| `includes.Array.data[n].nameOnBase` | string | ベース側のブレンドシェイプ名 |
| `includes.Array.data[n].nameOnLinked` | string | リンク側のブレンドシェイプ名 |

#### SecurityLock
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `pinNumber` | string | PIN 番号 |

#### FixWriteDefaults
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `mode` | FixWriteDefaultsMode | Auto=0, ForceOff=1, ForceOn=2, Disabled=3 |

#### UnlimitedParameters
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `includeBools` | bool | Bool パラメーターを含む |
| `includePuppets` | bool | Puppet パラメーターを含む |

#### MmdCompatibility
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `disableLayers` | List\<DisableLayer\> | 無効化レイヤーリスト |
| `disableLayers.Array.data[n].name` | string | レイヤー名 |
| `globalParam` | string | グローバルパラメーター名 |

#### Gizmo
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `rotation` | Vector3 | 回転 |
| `text` | string | テキスト |
| `sphereRadius` | float | 球半径 |
| `arrowLength` | float | 矢印長 |

#### SpsOptions
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `menuIcon` | GuidTexture2d | メニューアイコン |
| `menuPath` | string | メニューパス |
| `saveSockets` | bool | ソケット状態を保存 |

#### AdvancedCollider
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `radius` | float | 半径 (デフォルト: 0.1) |
| `height` | float | 高さ |
| `rootTransform` | Transform | ルートトランスフォーム |
| `colliderName` | string | コライダー名 |

#### MoveMenuItem
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `fromPath` | string | 移動元パス |
| `toPath` | string | 移動先パス |

#### ReorderMenuItem
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `path` | string | メニューパス |
| `position` | int | 位置 |

#### OverrideMenuSettings
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `nextText` | string | 「次へ」テキスト |
| `nextIcon` | GuidTexture2d | 「次へ」アイコン |

#### SetIcon
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `path` | string | メニューパス |
| `icon` | GuidTexture2d | アイコン |

#### BoneConstraint
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `obj` | GameObject | 対象オブジェクト |
| `bone` | HumanBodyBones | ヒューマノイドボーン |

#### ConstraintRetarget
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `bone` | HumanBodyBones | ヒューマノイドボーン |

#### ApplyDuringUpload
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `action` | State | 適用するアクション [DoNotApplyRestingState] |

#### Blinking
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `state` | State | まばたき状態 |
| `transitionTime` | float | トランジション時間 (デフォルト: -1 = 自動) |
| `holdTime` | float | ホールド時間 (デフォルト: -1 = 自動) |

#### Visemes
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `instant` | bool | 即時切替 |
| `state_PP` ... `state_U` | State | 各音素の状態 (14種) |

#### Talking
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `state` | State | 発話時状態 |

#### Toes
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `down` | State | 下向き状態 |
| `up` | State | 上向き状態 |
| `splay` | State | 開き状態 |

#### SenkyGestureDriver
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `eyesClosed` / `eyesHappy` / `eyesSad` / `eyesAngry` | State | 目の表情 |
| `mouthBlep` / `mouthSuck` / `mouthSad` / `mouthAngry` / `mouthHappy` | State | 口の表情 |
| `earsBack` | State | 耳の表情 |
| `transitionTime` | float | トランジション時間 (デフォルト: -1 = 自動) |

#### ZawooIntegration
| propertyPath (content. 以下) | 型 | 説明 |
|---|---|---|
| `submenu` | string | サブメニューパス |

### 独立 MonoBehaviour 別フィールド

#### VRCFuryHapticPlug
| propertyPath | 型 | 説明 |
|---|---|---|
| `autoRenderer` | bool | レンダラー自動検出 (デフォルト: true) |
| `autoPosition` | bool | 位置自動検出 (デフォルト: true) |
| `autoLength` | bool | 長さ自動検出 (デフォルト: true) |
| `useBoneMask` | bool | ボーンマスク使用 (デフォルト: true) |
| `textureMask` | GuidTexture2d | テクスチャマスク |
| `length` | float | 長さ |
| `autoRadius` | bool | 半径自動検出 (デフォルト: true) |
| `radius` | float | 半径 |
| `name` | string | 表示名 |
| `unitsInMeters` | bool | 単位がメートル |
| `configureTps` | bool | TPS 設定有効 |
| `enableSps` | bool | SPS 有効 (デフォルト: true) |
| `spsAutorig` | bool | SPS 自動リグ (デフォルト: true) |
| `spsBlendshapes` | List\<string\> | SPS ブレンドシェイプリスト |
| `configureTpsMesh` | List\<Renderer\> | TPS メッシュリスト |
| `spsAnimatedEnabled` | float | SPS アニメーション有効値 (デフォルト: 1) |
| `useLegacyRendererFinder` | bool | レガシーレンダラー検索 |
| `addDpsTipLight` | bool | DPS チップライト追加 |
| `postBakeActions` | State | ベイク後アクション [DoNotApplyRestingState] |
| `spsOverrun` | bool | SPS オーバーラン (デフォルト: true) |
| `depthActions2` | List\<DepthActionNew\> | 深度アクションリスト |
| `useHipAvoidance` | bool | ヒップ回避 (デフォルト: true) |

#### VRCFuryHapticSocket
| propertyPath | 型 | 説明 |
|---|---|---|
| `addLight` | AddLight | None=0, Hole=1, Ring=2, Auto=3, RingOneWay=4 |
| `name` | string | 表示名 |
| `enableHandTouchZone2` | EnableTouchZone | Auto=0, On=1, Off=2 |
| `length` | float | 長さ |
| `unitsInMeters` | bool | 単位がメートル (デフォルト: true) |
| `addMenuItem` | bool | メニュー項目追加 (デフォルト: true) |
| `menuIcon` | GuidTexture2d | メニューアイコン |
| `enableAuto` | bool | 自動有効 (デフォルト: true) |
| `position` | Vector3 | 位置オフセット |
| `rotation` | Vector3 | 回転オフセット |
| `depthActions2` | List\<DepthActionNew\> | 深度アクションリスト |
| `depthActions2.Array.data[n].actionSet` | State | アクションセット |
| `depthActions2.Array.data[n].range` | Vector2 | 距離範囲 (デフォルト: -0.25, 0) |
| `depthActions2.Array.data[n].units` | DepthActionUnits | Meters=0, Plugs=1, Local=2 |
| `depthActions2.Array.data[n].enableSelf` | bool | 自己有効 |
| `depthActions2.Array.data[n].smoothingSeconds` | float | スムージング秒数 |
| `depthActions2.Array.data[n].reverseClip` | bool | クリップ反転 |
| `activeActions` | State | アクティブ時アクション |
| `useHipAvoidance` | bool | ヒップ回避 (デフォルト: true) |
| `enablePlugLengthParameter` | bool | プラグ長さパラメーター有効 |
| `plugLengthParameterName` | string | プラグ長さパラメーター名 |
| `enablePlugWidthParameter` | bool | プラグ幅パラメーター有効 |
| `plugWidthParameterName` | string | プラグ幅パラメーター名 |

#### VRCFuryGlobalCollider
| propertyPath | 型 | 説明 |
|---|---|---|
| `radius` | float | 半径 (デフォルト: 0.1) |
| `height` | float | 高さ (デフォルト: 0) |
| `rootTransform` | Transform | ルートトランスフォーム |

#### VRCFuryHapticTouchSender
| propertyPath | 型 | 説明 |
|---|---|---|
| `radius` | float | 半径 (デフォルト: 0.1) |

#### VRCFuryHapticTouchReceiver
| propertyPath | 型 | 説明 |
|---|---|---|
| `name` | string | 名前 |
| `radius` | float | 半径 (デフォルト: 0.1) |

### State / Action の SerializedProperty 構造

State は `[SerializeReference] List<Action>` を持つ。Prefab YAML 上では managedReferences に格納される。

#### Action 共通フィールド
| propertyPath (相対) | 型 | 説明 |
|---|---|---|
| `desktopActive` | bool | Desktop のみ有効 |
| `androidActive` | bool | Android のみ有効 |
| `localOnly` | bool | ローカルのみ |
| `remoteOnly` | bool | リモートのみ |

#### ObjectToggleAction
| propertyPath (相対) | 型 | 説明 |
|---|---|---|
| `obj` | GameObject | 対象オブジェクト |
| `mode` | Mode | TurnOn=0, TurnOff=1, Toggle=2 |

#### BlendShapeAction
| propertyPath (相対) | 型 | 説明 |
|---|---|---|
| `blendShape` | string | ブレンドシェイプ名 |
| `blendShapeValue` | float | 値 (デフォルト: 100) |
| `renderer` | Renderer | 対象レンダラー (null = allRenderers) |
| `allRenderers` | bool | 全レンダラー対象 (デフォルト: true) |

#### MaterialAction
| propertyPath (相対) | 型 | 説明 |
|---|---|---|
| `renderer` | Renderer | 対象レンダラー |
| `materialIndex` | int | マテリアルスロット |
| `mat` | GuidMaterial | 設定するマテリアル |

#### MaterialPropertyAction
| propertyPath (相対) | 型 | 説明 |
|---|---|---|
| `renderer2` | GameObject | 対象レンダラーの GameObject |
| `affectAllMeshes` | bool | 全メッシュに影響 |
| `propertyName` | string | プロパティ名 |
| `propertyType` | Type | Float=0, Color=1, Vector=2, St=3, LegacyAuto=4 |
| `value` | float | Float 値 |
| `valueVector` | Vector4 | Vector 値 |
| `valueColor` | Color | Color 値 (デフォルト: white) |

#### ScaleAction
| propertyPath (相対) | 型 | 説明 |
|---|---|---|
| `obj` | GameObject | 対象オブジェクト |
| `scale` | float | スケール値 (デフォルト: 1) |

#### FxFloatAction
| propertyPath (相対) | 型 | 説明 |
|---|---|---|
| `name` | string | パラメーター名 |
| `value` | float | 値 (デフォルト: 1) |

#### AnimationClipAction
| propertyPath (相対) | 型 | 説明 |
|---|---|---|
| `clip` | GuidAnimationClip | アニメーションクリップ |

#### FlipbookAction
| propertyPath (相対) | 型 | 説明 |
|---|---|---|
| `renderer` | Renderer | 対象レンダラー |
| `frame` | int | フレーム番号 |

#### FlipBookBuilderAction
| propertyPath (相対) | 型 | 説明 |
|---|---|---|
| `pages` | List\<FlipBookPage\> | ページリスト |
| `pages.Array.data[n].state` | State | ページの State |

#### PoiyomiUVTileAction
| propertyPath (相対) | 型 | 説明 |
|---|---|---|
| `renderer` | Renderer | 対象レンダラー |
| `row` | int | 行 |
| `column` | int | 列 |
| `dissolve` | bool | ディゾルブ使用 |
| `renamedMaterial` | string | リネームされたマテリアル名 |

#### ShaderInventoryAction
| propertyPath (相対) | 型 | 説明 |
|---|---|---|
| `renderer` | Renderer | 対象レンダラー |
| `slot` | int | スロット番号 (デフォルト: 1) |

#### SmoothLoopAction
| propertyPath (相対) | 型 | 説明 |
|---|---|---|
| `state1` | State | 状態1 |
| `state2` | State | 状態2 |
| `loopTime` | float | ループ時間 (デフォルト: 5) |

#### WorldDropAction
| propertyPath (相対) | 型 | 説明 |
|---|---|---|
| `obj` | GameObject | 対象オブジェクト |

#### ResetPhysboneAction
| propertyPath (相対) | 型 | 説明 |
|---|---|---|
| `physBone` | VRCPhysBone | リセット対象の PhysBone |

#### SpsOnAction
| propertyPath (相対) | 型 | 説明 |
|---|---|---|
| `target` | VRCFuryHapticPlug | 対象の SPS Plug |

### 設計上の注意点

- **SerializeReference 多態**: `VRCFury.content` と `State.actions` は `[SerializeReference]` で多態シリアライズされる。Prefab YAML 上では `managedReferences` セクションに `type: {class: Toggle, ns: VF.Model.Feature, asm: VRCFury}` のような型情報付きで記録される。これは通常の `[SerializeField]` とは異なるシリアライズパスであり、prefab-sentinel の propertyPath 解決時に注意が必要。
- **GuidWrapper**: アセット参照は `GuidWrapper<T>` で包まれ、`id` (文字列) と `objRef` (直接参照) の二重参照構造を持つ。`id` は `"guid:fileID"` 形式。
- **VrcfUpgradeableMonoBehaviour**: `version`, `unityVersion`, `vrcfuryVersion` がすべての VRCFury コンポーネントに存在する。`version` フィールドはマイグレーションに使われ、内部的にインクリメントされる。
- **IVrcfEditorOnly**: すべての VRCFury コンポーネントは `IVrcfEditorOnly` を実装し、VRChat SDK の `IEditorOnly` と互換。ビルド済みアバターには VRCFury コンポーネントが残らない。
- **LegacyFeatureModel**: 空クラスから始まった Feature は Unity の制約により `NewFeatureModel` にフィールド追加できないため、`LegacyFeatureModel.Migrate()` で新クラスに変換される。
- **1.1227.0 → 1.1288.0 変更**: `AdvancedCollider` Feature が追加された。その他の Feature / StateAction / Component の構造変更なし。

## 実運用で学んだこと

(なし -- Phase 3-4 の検証で追記する)
