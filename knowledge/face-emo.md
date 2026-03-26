---
tool: face-emo
version_tested: "1.7.0"
last_updated: 2026-03-26
confidence: medium
---

# FaceEmo

## 概要 (L1)

VRChat アバターの表情メニュー自動生成ツール。ハンドジェスチャーとコンタクトレシーバーの条件分岐で表情アニメーションを切り替える FX レイヤーを GUI ベースで構築し、Modular Avatar 経由で非破壊にアバターへ統合する。

**解決する問題**: VRChat の表情制御は FX Animator + ExpressionMenu + ExpressionParameters の三位一体で構成されるが、手動構築は煩雑でミスしやすい。FaceEmo は専用エディタウィンドウ内で表情パターン（Mode）・条件分岐（Branch）・ジェスチャー条件（Condition）をビジュアルに編集し、ビルド時に AnimatorController / ExpressionMenu / ExpressionParameters を自動生成する。

**NDMF との関係**: FaceEmo 本体は NDMF プラグインとして直接動作するわけではなく、自前の FxGenerator で FX レイヤーと MA コンポーネント（Merge Animator, Menu Item 等）を生成する。NDMF プラグインとしては BlinkDisabler（Transforming フェーズ）と TrackingControlDisabler（Generating フェーズ）の 2 つの補助プラグインのみ登録する。

**Modular Avatar への依存**: FaceEmo は生成した AnimatorController を MA Merge Animator でアバターに統合し、表情メニューを MA Menu Item で組み立てる。MA が未インストールの場合は起動時にエラーダイアログを表示して処理を中断する。

**データモデル**: 表情メニューの定義は ScriptableObject ツリー（SerializableMenu → SerializableMode/SerializableGroup → SerializableBranch → SerializableCondition/SerializableAnimation）としてシーン内の MenuRepositoryComponent に保持される。エディタ操作は UseCase 層を通じて Domain モデルを変更し、MonoBehaviour/ScriptableObject にシリアライズし直す。

**プラットフォーム**: VRChat 専用。VRCSDK3 Avatars が必須。

**最新バージョン**: 1.7.0。VPM リポジトリ `https://suzuryg.github.io/vpm-repos/vpm.json` から配布。

## コンポーネント一覧 (L1->L2)

### シーン配置コンポーネント (MonoBehaviour)

| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| FaceEmoLauncherComponent | FaceEmo エディタのエントリーポイント。設定・状態への参照を束ねる | シーン内に 1 つ配置。Inspector に「Launch」ボタンと設定 UI を表示。アバター直下またはシーン直下に置く |
| MenuRepositoryComponent | 表情メニューデータの実体を保持する | FaceEmoLauncherComponent と同じ GameObject に自動配置される |
| RestorationCheckpoint | バックアップからの復元ポイントを記録する | FaceEmo メニューを復元可能にするため、対象アバターとバックアップアセットへの参照を保持 |
| BlinkDisabler | ビルド時にまばたきを無効化するマーカー | NDMF Transforming フェーズで AvatarDescriptor の eyelidType を None に変更。フィールドなし |
| TrackingControlDisabler | ビルド時に FX レイヤーの TrackingControl を無効化するマーカー | NDMF Generating フェーズで FX レイヤー内の VRC_AnimatorTrackingControl の目・口トラッキングを NoChange に書き換える。フィールドなし |

### 設定用 ScriptableObject

| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| AV3Setting | アバター・表情生成に関する全設定を保持 | ターゲットアバター指定、まばたきクリップ、口形モーフ、コンタクトレシーバー、AFK 表情、メニュー項目の有効/無効、パラメーター既定値など |
| ExpressionEditorSetting | 表情エディタの設定 | 顔ブレンドシェイプのデリミタ文字列のみ |
| ThumbnailSetting | サムネイル撮影カメラの設定 | FOV、距離、カメラ位置・角度、アニメーション進捗、Inspector サムネイルサイズ |

### エディタ状態用 ScriptableObject

| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| HierarchyViewState | 階層ビューの TreeView 状態保存 | Editor 限定。TreeViewState を保持 |
| MenuItemListViewState | メニュー項目リストビューの TreeView 状態保存 | Editor 限定。TreeViewState + RootGroupId を保持 |
| InspectorViewState | Inspector ビューの開閉状態 | 各セクション（まばたき、口形、AFK 等）の折りたたみ状態を bool で保持 |
| ViewSelection | 現在の選択状態 | 階層ビュー選択、メニュー項目リスト選択、ブランチリスト選択を保持 |

### データ ScriptableObject (SerializableMenu ツリー)

| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| SerializableMenu | メニュー全体のルート | DefaultSelection + Registered/Unregistered のメニュー項目リスト |
| SerializableRegisteredMenuItemList | 登録済みメニュー項目リスト | Types/Ids/Modes/Groups のリストでメニュー構造を保持 |
| SerializableUnregisteredMenuItemList | 未登録メニュー項目リスト | 同上。削除予備として保持される項目 |
| SerializableMode | 1 つの表情モード | 表示名、トラッキング制御、まばたき/口形キャンセラー設定、アニメーション、ブランチリスト |
| SerializableGroup | メニューグループ（フォルダ） | 表示名 + 子メニュー項目リスト |
| SerializableBranch | 1 つの条件分岐 | トラッキング制御、トリガー使用フラグ、4 方向アニメーション（Base/Left/Right/Both）、条件リスト |
| SerializableCondition | 1 つのジェスチャー条件 | Hand + HandGesture + ComparisonOperator |
| SerializableAnimation | アニメーション参照 | AnimationClip の GUID 文字列のみ |

## 操作パターン (L2)

### 新規表情メニュー作成

1. Unity メニュー **FaceEmo > New Menu** を実行、またはアバターの Hierarchy アイコンをクリック
2. シーンに `FaceEmo` GameObject が作成され、FaceEmoLauncherComponent が付与される
3. Inspector の「Launch」ボタンで FaceEmo メインウィンドウが開く
4. 初回起動時、アバターの既存 FX レイヤーから表情パターンを自動インポートする（ExpressionImporter）
5. メインウィンドウで表情パターン（Mode）を追加・編集し、条件分岐（Branch）でジェスチャーに紐付ける

### ジェスチャー条件分岐の設定

1. Mode を選択し、Branch を追加する
2. Branch に Condition を追加: Hand（Left/Right/OneSide/Either/Both） + HandGesture（Neutral~ThumbsUp） + ComparisonOperator（Equals/NotEqual）
3. Branch ごとに Base / Left / Right / Both の 4 方向アニメーションを設定可能
4. IsLeftTriggerUsed / IsRightTriggerUsed でアナログフィスト入力への連動を制御
5. ビルド時に AnimatorController の State/Transition/Condition が自動生成される

### コンタクトレシーバー連動

1. AV3Setting の ContactReceivers に VRCContactReceiver を追加
2. ContactReceiverParameterNames にパラメーター名を設定
3. ProximityThreshold で近接判定の閾値を設定（デフォルト: 0.1）
4. ビルド時にコンタクトパラメーターが Expression Parameters に追加され、条件分岐で利用可能になる

### 複数アバターへの適用

1. AV3Setting の TargetAvatar にメインアバターを設定
2. SubTargetAvatars / SubTargetAvatarPaths にサブアバターを追加
3. 生成時に全対象アバターに同一の FX レイヤー構成が適用される

### バックアップと復元

1. FaceEmo は自動バックアップ機能を持ち、FX 生成時に ScriptableObject を .asset としてエクスポートする
2. **FaceEmo > Restore Menu** で .asset ファイルを選択して復元
3. RestorationCheckpoint コンポーネントがアバターに残っている場合、Hierarchy アイコンクリックで復元を提案する

## SerializedProperty リファレンス (L3)

ソースバージョン: 1.5.5 (UnityTool_sample) + 1.7.0 (Shiratsume) 差分検証済み
検証方法: .cs ソースコードの public フィールド読み + .meta ファイルの GUID 抽出（inspect 実測なし -> confidence: medium）

### Script GUID テーブル

#### シーン配置コンポーネント

| コンポーネント | GUID | 備考 |
|---|---|---|
| FaceEmoLauncherComponent | `f95fa6f118f057d43b99a38d869062c6` | エントリーポイント。Inspector に設定 UI |
| MenuRepositoryComponent | `a27497147d2a64643a03fadd236ad885` | メニューデータ保持 |
| RestorationCheckpoint | `9a44dd69b12f8e64294350e5f728904c` | [DisallowMultipleComponent] |
| BlinkDisabler | `b1cec12bf093dfe4f9580f58da3ec3cf` | マーカー (フィールドなし)。`#if VALID_VRCSDK3_AVATARS` |
| TrackingControlDisabler | `7295bd34fd3ed2d43a44ce3eb08bec9d` | マーカー (フィールドなし)。`#if VALID_VRCSDK3_AVATARS` |

#### ScriptableObject (設定・状態)

| コンポーネント | GUID | 備考 |
|---|---|---|
| AV3Setting | `904fd4f188254004cb8f5eb7a37696f5` | 主要設定 ScriptableObject |
| ExpressionEditorSetting | `294daafd96024e5489c161b17411f68e` | |
| ThumbnailSetting | `d747fe3a0e865a744bf3089471d08cb9` | |
| HierarchyViewState | `989072d51955664428235995d3a28df9` | |
| InspectorViewState | `42193839c8662644baa84d0b33219fdc` | |
| MenuItemListViewState | `ed0c630b627aa9546923655e8666b11a` | |
| ViewSelection | `5e7ad1060d5efe946871660ee08ece27` | |
| FaceEmoProject | `7be7775a607f623429b8b28205ec923d` | バックアップ用。SerializableMenu + 設定を束ねる |

#### ScriptableObject (データツリー)

| コンポーネント | GUID | 備考 |
|---|---|---|
| SerializableMenu | `9647093156341204ebc7009fd5eb82fe` | メニュールート |
| SerializableRegisteredMenuItemList | `cd2295b21600dea45bf32e23f7c95ac4` | SerializableMenuItemListBase 継承 |
| SerializableUnregisteredMenuItemList | `276dbe76146125d448c6a6508e4d30bd` | SerializableMenuItemListBase 継承 |
| SerializableMenuItemListBase | `4e6b5a9aa05e45c43ba243cb5fe1ae32` | abstract 基底 |
| SerializableMode | `2ee80d5507582354ea3b42e7d62fc751` | |
| SerializableGroup | `4c08764e51db88f429233363a86a2ca0` | SerializableMenuItemListBase 継承 |
| SerializableBranch | `1304eddb2a8bff34fbb5646000ec7366` | |
| SerializableCondition | `b0111c0ff6243e74c815b86bac09d282` | |
| SerializableAnimation | `d9a95bc36ddef3d458cba0494a3fc3c7` | |

### 共通型

**BlendShape** (Serializable class -- AV3Setting の MouthMorphs, ExcludedBlendShapes で使用):
- `_path`: string -- Animator からの Transform パス [SerializeField]
- `_name`: string -- ブレンドシェイプ名 [SerializeField]

### コンポーネント別フィールド

#### FaceEmoLauncherComponent

| propertyPath | 型 | 説明 |
|---|---|---|
| `InstanceId` | int | インスタンス ID |
| `AV3Setting` | AV3Setting | VRC アバター設定への参照 |
| `ThumbnailSetting` | ThumbnailSetting | サムネイル設定への参照 |
| `ExpressionEditorSetting` | ExpressionEditorSetting | 表情エディタ設定への参照 |
| `HierarchyViewState` | HierarchyViewState | 階層ビュー状態への参照 |
| `MenuItemListViewState` | MenuItemListViewState | メニュー項目リストビュー状態への参照 |
| `ViewSelection` | ViewSelection | 選択状態への参照 |
| `InspectorViewState` | InspectorViewState | Inspector ビュー状態への参照 |

全フィールドに `[HideInInspector]` が条件付き（`#if !SHOW_FACE_EMO_FIELDS`）で付与。通常の Inspector では非表示。

#### MenuRepositoryComponent

| propertyPath | 型 | 説明 |
|---|---|---|
| `SerializableMenu` | SerializableMenu | メニューデータ本体への参照 |

#### RestorationCheckpoint

| propertyPath | 型 | 説明 |
|---|---|---|
| `TargetAvatar` | MonoBehaviour | 対象アバター（VRCAvatarDescriptor を MonoBehaviour として保持） |
| `LatestBackup` | ScriptableObject | 最新バックアップ (FaceEmoProject) への参照 |

#### AV3Setting (ScriptableObject)

| propertyPath | 型 | 説明 |
|---|---|---|
| `TargetAvatar` | MonoBehaviour | 対象 VRCAvatarDescriptor (MonoBehaviour として保持) |
| `TargetAvatarPath` | string | 対象アバターのパス |
| `SubTargetAvatars` | List\<MonoBehaviour\> | サブターゲットアバター |
| `SubTargetAvatarPaths` | List\<string\> | サブターゲットアバターのパス |
| `MARootObjectPrefab` | GameObject | MA ルートオブジェクトのプレファブ |
| `UseBlinkClip` | bool | まばたきクリップ使用 (デフォルト: false) |
| `BlinkClip` | AnimationClip | まばたきアニメーションクリップ |
| `MouthMorphBlendShapes` | List\<string\> | 口形モーフ名リスト [Obsolete] |
| `MouthMorphs` | List\<BlendShape\> | 口形モーフリスト |
| `MouthMorphs.Array.data[n]._path` | string | Transform パス |
| `MouthMorphs.Array.data[n]._name` | string | ブレンドシェイプ名 |
| `UseMouthMorphCancelClip` | bool | 口形キャンセルクリップ使用 (デフォルト: false) |
| `MouthMorphCancelClip` | AnimationClip | 口形キャンセルアニメーション |
| `ExcludedBlendShapes` | List\<BlendShape\> | 除外ブレンドシェイプリスト |
| `AdditionalSkinnedMeshes` | List\<SkinnedMeshRenderer\> | 追加スキンドメッシュ |
| `AdditionalSkinnedMeshPaths` | List\<string\> | 追加スキンドメッシュパス |
| `AdditionalToggleObjects` | List\<GameObject\> | 追加トグルオブジェクト |
| `AdditionalToggleObjectPaths` | List\<string\> | 追加トグルオブジェクトパス |
| `AdditionalTransformObjects` | List\<GameObject\> | 追加 Transform オブジェクト |
| `AdditionalTransformObjectPaths` | List\<string\> | 追加 Transform オブジェクトパス |
| `DisableFxDuringDancing` | bool | ダンス中 FX 無効化 (デフォルト: false) |
| `ContactReceivers` | List\<MonoBehaviour\> | コンタクトレシーバーリスト |
| `ContactReceiverPaths` | List\<string\> | コンタクトレシーバーパス |
| `ContactReceiverParameterNames` | List\<string\> | コンタクトパラメーター名 |
| `ProximityThreshold` | float | 近接閾値 (デフォルト: 0.1) |
| `ChangeAfkFace` | bool | AFK 表情変更 (デフォルト: false) |
| `AfkExitDurationSeconds` | float | AFK 解除遷移時間 (デフォルト: 0) |
| `AfkEnterFace` | AnimationClip | AFK 開始表情 |
| `AfkFace` | AnimationClip | AFK 中表情 |
| `AfkExitFace` | AnimationClip | AFK 解除表情 |
| `SmoothAnalogFist` | bool | アナログフィスト平滑化 (デフォルト: true) |
| `TransitionDurationSeconds` | double | 遷移時間 (デフォルト: 0.1) |
| `GenerateExMenuThumbnails` | bool | ExMenu サムネイル生成 (デフォルト: true) |
| `GammaCorrectionValueForExMenuThumbnails` | float | サムネイルガンマ補正値 (デフォルト: 1.35) |
| `ReplaceBlink` | bool | まばたき置換 (デフォルト: true) |
| `DisableTrackingControls` | bool | トラッキングコントロール無効化 (デフォルト: true) |
| `AddParameterPrefix` | bool | パラメーターにプレフィックス追加 (デフォルト: true) |
| `MatchAvatarWriteDefaults` | bool | Write Defaults 自動合わせ (デフォルト: true) |
| `AddConfig_EmoteSelect` | bool | エモート選択メニュー追加 (デフォルト: true) |
| `EmoteSelect_UseFolderInsteadOfPager` | bool | ページャーの代わりにフォルダ使用 **1.7.0 で追加** |
| `AddConfig_BlinkOff` | bool | まばたきオフメニュー追加 (デフォルト: true) |
| `AddConfig_DanceGimmick` | bool | ダンスギミックメニュー追加 (デフォルト: true) |
| `AddConfig_ContactLock` | bool | コンタクトロックメニュー追加 (デフォルト: true) |
| `AddConfig_Override` | bool | オーバーライドメニュー追加 (デフォルト: true) |
| `AddConfig_Voice` | bool | ボイスメニュー追加 (デフォルト: false) |
| `AddConfig_EmoteLock` | bool | エモートロックメニュー追加 (デフォルト: true) **1.7.0 で追加** |
| `AddConfig_ModeSwitch` | bool | モード切替メニュー追加 (デフォルト: true) **1.7.0 で追加** |
| `AddConfig_HandPattern_Swap` | bool | ハンドパターンスワップメニュー追加 (デフォルト: true) |
| `AddConfig_HandPattern_DisableLeft` | bool | 左手無効メニュー追加 (デフォルト: true) |
| `AddConfig_HandPattern_DisableRight` | bool | 右手無効メニュー追加 (デフォルト: true) |
| `AddConfig_Controller_Quest` | bool | Quest コントローラーメニュー追加 (デフォルト: true) |
| `AddConfig_Controller_Index` | bool | Index コントローラーメニュー追加 (デフォルト: true) |
| `DefaultValue_ContactLock` | bool | コンタクトロック既定値 (デフォルト: false) |
| `DefaultValue_Override` | bool | オーバーライド既定値 (デフォルト: true) |
| `DefaultValue_Voice` | bool | ボイス既定値 (デフォルト: false) |
| `DefaultValue_HandPattern_Swap` | bool | ハンドパターンスワップ既定値 (デフォルト: false) |
| `DefaultValue_HandPattern_DisableLeft` | bool | 左手無効既定値 (デフォルト: false) |
| `DefaultValue_HandPattern_DisableRight` | bool | 右手無効既定値 (デフォルト: false) |
| `DefaultValue_Controller_Quest` | bool | Quest コントローラー既定値 (デフォルト: false) |
| `DefaultValue_Controller_Index` | bool | Index コントローラー既定値 (デフォルト: false) |
| `ExpressionDefaults_ChangeDefaultFace` | bool | 表情デフォルト: デフォルト顔変更 (デフォルト: false) |
| `ExpressionDefaults_UseAnimationNameAsDisplayName` | bool | 表情デフォルト: アニメーション名を表示名に (デフォルト: false) |
| `ExpressionDefaults_EyeTrackingEnabled` | bool | 表情デフォルト: アイトラッキング有効 (デフォルト: true) |
| `ExpressionDefaults_MouthTrackingEnabled` | bool | 表情デフォルト: マウストラッキング有効 (デフォルト: true) |
| `ExpressionDefaults_BlinkEnabled` | bool | 表情デフォルト: まばたき有効 (デフォルト: true) |
| `ExpressionDefaults_MouthMorphCancelerEnabled` | bool | 表情デフォルト: 口形キャンセラー有効 (デフォルト: true) |
| `LastOpenedAnimationPath` | string | 最後に開いたアニメーションパス |
| `LastSavedAnimationPath` | string | 最後に保存したアニメーションパス |

#### ExpressionEditorSetting (ScriptableObject)

| propertyPath | 型 | 説明 |
|---|---|---|
| `FaceBlendShapeDelimiter` | string | 顔ブレンドシェイプのデリミタ (デフォルト: 空文字列) |

#### ThumbnailSetting (ScriptableObject)

| propertyPath | 型 | 説明 |
|---|---|---|
| `Main_FOV` | float | メインカメラ FOV (デフォルト: 20) |
| `Main_Distance` | float | メインカメラ距離 (デフォルト: 0.6) |
| `Main_CameraPosX` | float | カメラ X 位置 (デフォルト: 0.5) |
| `Main_CameraPosY` | float | カメラ Y 位置 (デフォルト: 0.55) |
| `Main_CameraAngleH` | float | カメラ水平角度 (デフォルト: 0) |
| `Main_CameraAngleV` | float | カメラ垂直角度 (デフォルト: -5) |
| `Main_AnimationProgress` | float | アニメーション進捗 (デフォルト: 1.0) **1.7.0 で追加** |
| `Inspector_Width` | int | Inspector サムネイル幅 (デフォルト: 256) |
| `Inspector_Height` | int | Inspector サムネイル高さ (デフォルト: 256) |
| `Main_Width` | int | メインサムネイル幅 [Obsolete] (デフォルト: 180) |
| `Main_Height` | int | メインサムネイル高さ [Obsolete] (デフォルト: 150) |
| `GestureTable_Width` | int | ジェスチャーテーブルサムネイル幅 [Obsolete] (デフォルト: 110) |
| `GestureTable_Height` | int | ジェスチャーテーブルサムネイル高さ [Obsolete] (デフォルト: 85) |

#### InspectorViewState (ScriptableObject)

| propertyPath | 型 | 説明 |
|---|---|---|
| `IsApplyingToMultipleAvatarsOpened` | bool | 複数アバター適用セクション展開 |
| `IsBlinkOpened` | bool | まばたきセクション展開 |
| `IsMouthMorphBlendShapesOpened` | bool | 口形モーフセクション展開 |
| `IsExcludedBlendShapesOpened` | bool | 除外ブレンドシェイプセクション展開 |
| `IsAddtionalSkinnedMeshesOpened` | bool | 追加スキンドメッシュセクション展開 (typo: Addtional) |
| `IsAddtionalToggleOpened` | bool | 追加トグルセクション展開 (typo: Addtional) |
| `IsAddtionalTransformOpened` | bool | 追加 Transform セクション展開 (typo: Addtional) |
| `IsDanceGimmickOpened` | bool | ダンスギミックセクション展開 |
| `IsContactOpened` | bool | コンタクトセクション展開 |
| `IsAFKOpened` | bool | AFK セクション展開 |
| `IsThumbnailOpened` | bool | サムネイルセクション展開 |
| `IsExpressionsMenuItemsOpened` | bool | 表情メニュー項目セクション展開 |
| `IsAvatarApplicationOpened` | bool | アバター適用セクション展開 |
| `IsDefaultsOpened` | bool | デフォルトセクション展開 |
| `IsEditorSettingOpened` | bool | エディタ設定セクション展開 |

#### ViewSelection (ScriptableObject)

| propertyPath | 型 | 説明 |
|---|---|---|
| `HierarchyView` | string | 階層ビューの選択 ID |
| `MenuItemListView` | string | メニュー項目リストビューの選択 ID |
| `BranchListView` | int | ブランチリストビューの選択インデックス |

注意: `GestureTableView` は ValueTuple のため Unity シリアライズ対象外。

#### SerializableMenu (ScriptableObject)

| propertyPath | 型 | 説明 |
|---|---|---|
| `Version` | double | データバージョン (デフォルト: 1.0) |
| `DefaultSelection` | string | デフォルト選択の ID |
| `Registered` | SerializableRegisteredMenuItemList | 登録済み項目リスト |
| `Unregistered` | SerializableUnregisteredMenuItemList | 未登録項目リスト |

#### SerializableMenuItemListBase / Registered / Unregistered (ScriptableObject)

| propertyPath | 型 | 説明 |
|---|---|---|
| `Types` | List\<MenuItemType\> | 項目種別リスト (Mode=0, Group=1) |
| `Ids` | List\<string\> | 項目 ID リスト |
| `Modes` | List\<SerializableMode\> | モードリスト |
| `Groups` | List\<SerializableGroup\> | グループリスト |

#### SerializableMode (ScriptableObject)

| propertyPath | 型 | 説明 |
|---|---|---|
| `ChangeDefaultFace` | bool | デフォルト顔変更 |
| `DisplayName` | string | 表示名 |
| `UseAnimationNameAsDisplayName` | bool | アニメーション名を表示名に使用 |
| `EyeTrackingControl` | EyeTrackingControl | アイトラッキング制御 (Tracking=0, Animation=1) |
| `MouthTrackingControl` | MouthTrackingControl | マウストラッキング制御 (Tracking=0, Animation=1) |
| `BlinkEnabled` | bool | まばたき有効 |
| `MouthMorphCancelerEnabled` | bool | 口形キャンセラー有効 |
| `Animation` | SerializableAnimation | モードアニメーション |
| `Branches` | List\<SerializableBranch\> | 条件分岐リスト |

#### SerializableGroup (ScriptableObject, extends SerializableMenuItemListBase)

| propertyPath | 型 | 説明 |
|---|---|---|
| `DisplayName` | string | グループ表示名 |
| (+ SerializableMenuItemListBase のフィールド) | | |

#### SerializableBranch (ScriptableObject)

| propertyPath | 型 | 説明 |
|---|---|---|
| `EyeTrackingControl` | EyeTrackingControl | アイトラッキング制御 |
| `MouthTrackingControl` | MouthTrackingControl | マウストラッキング制御 |
| `BlinkEnabled` | bool | まばたき有効 |
| `MouthMorphCancelerEnabled` | bool | 口形キャンセラー有効 |
| `IsLeftTriggerUsed` | bool | 左トリガー使用 |
| `IsRightTriggerUsed` | bool | 右トリガー使用 |
| `BaseAnimation` | SerializableAnimation | ベースアニメーション |
| `LeftHandAnimation` | SerializableAnimation | 左手アニメーション |
| `RightHandAnimation` | SerializableAnimation | 右手アニメーション |
| `BothHandsAnimation` | SerializableAnimation | 両手アニメーション |
| `Conditions` | List\<SerializableCondition\> | 条件リスト |

#### SerializableCondition (ScriptableObject)

| propertyPath | 型 | 説明 |
|---|---|---|
| `Hand` | Hand | 対象の手 (Left=0, Right=1, OneSide=2, Either=3, Both=4) |
| `HandGesture` | HandGesture | ジェスチャー (Neutral=0, Fist=1, HandOpen=2, Fingerpoint=3, Victory=4, RockNRoll=5, HandGun=6, ThumbsUp=7) |
| `ComparisonOperator` | ComparisonOperator | 比較演算子 (Equals=0, NotEqual=1) |

#### SerializableAnimation (ScriptableObject)

| propertyPath | 型 | 説明 |
|---|---|---|
| `GUID` | string | AnimationClip アセットの GUID |

### 設計上の注意点

- **VRCAvatarDescriptor を MonoBehaviour として保持**: AV3Setting や RestorationCheckpoint は VRCAvatarDescriptor を直接参照せず、MonoBehaviour 型で保持する。これは VRCSDK のコンパイルエラー時に ScriptableObject 参照が壊れるのを防ぐため。使用時にキャストする。
- **パスと参照の二重保持**: AV3Setting 内の多くの参照（TargetAvatar/TargetAvatarPath, SubTargetAvatars/SubTargetAvatarPaths, AdditionalSkinnedMeshes/AdditionalSkinnedMeshPaths 等）はオブジェクト参照と文字列パスの両方を保持する。シリアライズの堅牢性のため。
- **データツリーは SubAsset 構造**: SerializableMenu 以下のオブジェクトは `AssetDatabase.AddObjectToAsset` で親アセットの SubAsset として保存される。バックアップ (.asset) やメニューリポジトリ内で入れ子の ScriptableObject ツリーを形成する。
- **マーカーコンポーネント 2 種**: BlinkDisabler と TrackingControlDisabler はフィールドを持たないマーカー。存在するだけで NDMF ビルド時にアバター設定を書き換える。`#if VALID_VRCSDK3_AVATARS` で条件コンパイル。
- **InspectorViewState の typo**: `IsAddtionalSkinnedMeshesOpened` 等の `Addtional` は `Additional` の typo だが、シリアライズ済みデータとの互換性のため修正不可。
- **FaceEmoLauncherComponent のフィールド非表示**: 全フィールドに `[HideInInspector]` が条件付きで付与される（`#if !SHOW_FACE_EMO_FIELDS`）。通常の Inspector では見えず、カスタムエディタ (FaceEmoLauncher) が代わりに UI を描画する。prefab-sentinel で wiring を確認する場合はこの点に注意。

### 1.5.5 -> 1.7.0 変更サマリー

- **新フィールド 3 件 (AV3Setting)**: `EmoteSelect_UseFolderInsteadOfPager` (bool), `AddConfig_EmoteLock` (bool), `AddConfig_ModeSwitch` (bool)
- **新フィールド 1 件 (ThumbnailSetting)**: `Main_AnimationProgress` (float)
- **非推奨化 (ThumbnailSetting)**: `Main_Width`, `Main_Height`, `GestureTable_Width`, `GestureTable_Height` が [Obsolete] に
- **新コンポーネント**: なし
- **削除コンポーネント**: なし
- **Runtime ファイル構成**: 完全に同一（差分は上記フィールド追加のみ）
- **GUID**: 全 Script GUID が v1.5.5 と v1.7.0 で一致（互換性あり）

## 実運用で学んだこと

(なし -- Phase 3-4 の検証で追記する)
