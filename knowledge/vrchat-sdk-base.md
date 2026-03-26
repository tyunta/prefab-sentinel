---
tool: vrchat-sdk-base
version_tested: "3.10.2"
last_updated: 2026-03-26
confidence: medium
---

# VRChat SDK Base (com.vrchat.base)

## 概要 (L1)

VRChat SDK の基盤パッケージ。アバター SDK (`com.vrchat.avatars`) とワールド SDK (`com.vrchat.worlds`) の共通依存として機能する。コンポーネント実装の大半は事前コンパイル済み DLL (`VRCSDKBase.dll`, `VRC.Dynamics.dll`, `VRC.SDK3.Dynamics.*.dll`) で提供される。

**パッケージ構成**:
- `Runtime/VRCSDK/Plugins/` — コア DLL 群（VRCSDKBase, VRC.Dynamics, PhysBone, Contact, Constraint）
- `Runtime/VRCSDK/Dependencies/` — Oculus Spatializer、バリデーション、マテリアルフォールバック
- `Editor/VRCSDK/Dependencies/` — ビルドパイプライン、コントロールパネル、API クライアント
- `ShaderLibrary/` — VRCTime.cginc（3.10.x で追加）

**依存パッケージ**: com.unity.burst, com.unity.collections, com.unity.mathematics, com.unity.nuget.newtonsoft-json, com.unity.timeline, com.unity.xr.management, com.unity.xr.oculus, com.unity.postprocessing, com.unity.ugui

**対応 Unity**: 2022.3

**バージョン**: 3.10.2（Shiratsume プロジェクト）、3.8.0（UnityTool_sample）

## コンポーネント一覧 (L1 → L2)

### Dynamics — PhysBone

| コンポーネント | 名前空間 | 用途 |
|---|---|---|
| VRCPhysBone | VRC.SDK3.Dynamics.PhysBone.Components | 揺れもの物理シミュレーション。髪、衣装、尻尾等に使用 |
| VRCPhysBoneCollider | VRC.SDK3.Dynamics.PhysBone.Components | PhysBone との衝突判定形状（Sphere/Capsule/Plane） |
| VRCPhysBoneRoot | VRC.SDK3.Dynamics.PhysBone.Components | PhysBone の実行タイミング制御 |

### Dynamics — Contact

| コンポーネント | 名前空間 | 用途 |
|---|---|---|
| VRCContactSender | VRC.SDK3.Dynamics.Contact.Components | コンタクト送信。コリジョンタグで受信側とマッチング |
| VRCContactReceiver | VRC.SDK3.Dynamics.Contact.Components | コンタクト受信。アニメーターパラメーターを駆動 |

### Dynamics — VRC Constraint

Unity 標準 Constraint の VRChat 最適化版。FreezeToWorld やネットワーク同期に対応。

| コンポーネント | 名前空間 | 用途 |
|---|---|---|
| VRCAimConstraint | VRC.SDK3.Dynamics.Constraint.Components | ターゲット方向への回転 |
| VRCLookAtConstraint | VRC.SDK3.Dynamics.Constraint.Components | ターゲットへの注視 |
| VRCParentConstraint | VRC.SDK3.Dynamics.Constraint.Components | 親子関係の擬似的な再現（位置+回転） |
| VRCPositionConstraint | VRC.SDK3.Dynamics.Constraint.Components | 位置の追従 |
| VRCRotationConstraint | VRC.SDK3.Dynamics.Constraint.Components | 回転の追従 |
| VRCScaleConstraint | VRC.SDK3.Dynamics.Constraint.Components | スケールの追従 |

### Audio

| コンポーネント | 名前空間 | 用途 |
|---|---|---|
| ONSPAudioSource | (global) | Oculus Spatializer 連携。空間音響パラメーター（gain, near/far, volumetric radius）を制御 |

### バリデーション

| クラス | 用途 |
|---|---|
| AvatarValidation | アバターのコンポーネントホワイトリスト、シェーダーホワイトリスト、ハードリミット定数 |
| WorldValidation | ワールドのコンポーネントホワイトリスト |
| AvatarPerformanceStats | パフォーマンスランクの計測結果保持 |

## プラットフォーム制約 (L2)

### レイヤー構成

VRChat は Unity のレイヤー 0-21 を予約し、ワールドクリエイターはレイヤー 22-31 を自由に使用できる。

| Layer | 名前 | 用途 |
|---|---|---|
| 0 | Default | Unity デフォルト。VRChat の Avatar Pedestal に使用 |
| 3 | Item | ユーザーが配置したアイテム |
| 4 | Water | VRChat の Portal とミラー |
| 5 | UI | Unity UI デフォルト |
| 6-7 | reserved | VRChat 予約（使用不可） |
| 8 | Interactive | Unity/VRChat 未使用 |
| 9 | Player | リモートプレイヤーのアバター |
| 10 | PlayerLocal | ローカルプレイヤーのアバター |
| 11 | Environment | Unity/VRChat 未使用 |
| 12 | UiMenu | VRChat ネームプレート |
| 13 | Pickup | VRChat Pickup デフォルト |
| 14 | PickupNoEnvironment | Pickup 同士のみ衝突 |
| 15-16 | StereoLeft/Right | 未使用 |
| 17 | Walkthrough | プレイヤーと衝突しないコライダー |
| 18 | MirrorReflection | ミラー内のローカルプレイヤー描画 |
| 19 | InternalUI | VRChat 内部 UI |
| 20 | HardwareObjects | 物理ハードウェアの仮想表現 |
| 21 | reserved4 | VRChat 予約 |
| 22-31 | (ユーザー定義) | カスタムレイヤー（ワールドのみ） |

**制約**: MirrorReflection と InternalUI はカメラのカリングマスクで変更不可。

### ネットワーク制約

| 項目 | 値 |
|---|---|
| Udon 帯域上限 | 約 11 KB/秒 |
| Manual sync 1回あたり最大 | 約 280,496 bytes |
| Continuous sync 1回あたり最大 | 約 200 bytes |

**同期モード**:
- **Continuous**: 高頻度・中間値不要のデータ向け。VRChat が補間・圧縮を自動適用。
- **Manual**: 低頻度・全値重要のデータ向け。`RequestSerialization()` で明示送信。送信サイズに応じてレート制限が適用される。

**同期変数サイズ**: bool=1B, int=1-8B, float=4B, Vector3=12B, Quaternion=16B, Color=16B, Color32=4B, string=2B/文字

### パフォーマンスランク (アバター)

#### PC

| メトリクス | Excellent | Good | Medium | Poor |
|---|---|---|---|---|
| ポリゴン数 | 32,000 | 70,000 | 70,000 | 70,000 |
| Texture Memory | 40 MB | 75 MB | 110 MB | 150 MB |
| Skinned Mesh | 1 | 2 | 8 | 16 |
| Material Slots | 4 | 8 | 16 | 32 |
| PhysBone Components | 4 | 8 | 16 | 32 |
| PhysBone Transforms | 16 | 64 | 128 | 256 |
| PhysBone Colliders | 4 | 8 | 16 | 32 |
| PhysBone Collision Checks | 32 | 128 | 256 | 512 |
| Contacts | 8 | 16 | 24 | 32 |
| Constraint Count | 100 | 250 | 300 | 350 |
| Constraint Depth | 20 | 50 | 80 | 100 |
| Bones | 75 | 150 | 256 | 400 |
| Animators | 1 | 4 | 16 | 32 |

#### Mobile

| メトリクス | Excellent | Good | Medium | Poor |
|---|---|---|---|---|
| ポリゴン数 | 7,500 | 10,000 | 15,000 | 20,000 |
| Texture Memory | 10 MB | 18 MB | 25 MB | 40 MB |
| Skinned Mesh | 1 | 1 | 2 | 2 |
| Material Slots | 1 | 1 | 2 | 4 |
| PhysBone Components | 0 | 4 | 6 | 8 |
| PhysBone Transforms | 0 | 16 | 32 | 64 |
| Contacts | 2 | 4 | 8 | 16 |
| Constraint Count | 30 | 60 | 120 | 150 |
| Constraint Depth | 5 | 15 | 35 | 50 |
| Bones | 75 | 90 | 150 | 150 |

**モバイル特記**: Very Poor を超過すると、Show Avatar 状態に関わらず全 PhysBone / Contact / VRC Constraint が強制除去される。

### ハードリミット定数 (AvatarValidation)

| 定数 | 値 |
|---|---|
| MAX_AVD_PHYSBONES_PER_AVATAR | 256 |
| MAX_AVD_COLLIDERS_PER_AVATAR | 256 |
| MAX_AVD_CONTACTS_PER_AVATAR | 256 |
| MAX_AVD_CONSTRAINTS_PER_AVATAR | 2,000 |

### モバイルシェーダーホワイトリスト

`AvatarValidation.ShaderWhiteList` に定義。これ以外のシェーダーはフォールバックマテリアルに差し替えられる。

- `VRChat/Mobile/Standard Lite`
- `VRChat/Mobile/Diffuse`
- `VRChat/Mobile/Bumped Diffuse`
- `VRChat/Mobile/Bumped Mapped Specular`
- `VRChat/Mobile/Toon Lit`
- `VRChat/Mobile/MatCap Lit`
- `VRChat/Mobile/Particles/Additive`
- `VRChat/Mobile/Particles/Multiply`
- `VRChat/Mobile/Toon Standard`
- `VRChat/Mobile/Toon Standard (Outline)` — クライアントホワイトリスト外、非 Outline 版にフォールバック

### ビルドパイプライン

Editor スクリプトからビルドプロセスに介入するためのコールバックインターフェース群。

| インターフェース | メソッド | 実行タイミング |
|---|---|---|
| `IVRCSDKBuildRequestedCallback` | `OnBuildRequested(VRCSDKRequestedBuildType)` → bool | ビルド開始前。`false` を返すとビルド中止 |
| `IVRCSDKPreprocessAvatarCallback` | `OnPreprocessAvatar(GameObject)` → bool | アバタービルドの前処理。NDMF/MA 等はここで動作 |
| `IVRCSDKPostprocessAvatarCallback` | `OnPostprocessAvatar()` | アバタービルドの後処理 |
| `IVRCSDKPreprocessSceneCallback` | `OnPreprocessScene(Scene)` | シーンビルドの前処理 |
| `IVRCSDKPostprocessSceneCallback` | `OnPostprocessScene(Scene)` | シーンビルドの後処理 |
| `IPreprocessCallbackBehaviour` | `OnPreprocess()` | シーン上のコンポーネントとして配置可能 |
| `IEditorOnly` | (マーカー) | SDK バリデーションで Editor-only として扱う |

**`callbackOrder`**: 全コールバックに共通。小さい値ほど先に実行される。NDMF は -1025 を使用。

**Public SDK API** (`IVRCSdkBuilderApi`): ビルド/アップロードの進捗イベント (`OnSdkBuildStart`, `OnSdkBuildProgress`, `OnSdkBuildSuccess`, `OnSdkBuildError`, `OnSdkUploadStart`, `OnSdkUploadProgress` 等) を提供。

### 許可されたビルドターゲット

`EnvConfig` で制御:
- `StandaloneWindows64` (PC)
- `Android` (Quest) — Graphics API: OpenGLES3 のみ
- `iOS` — Graphics API: Metal のみ

### コンポーネントの一般的な制約

- **アバターコンポーネントホワイトリスト**: `AvatarValidation.ComponentTypeWhiteListSdk3` に列挙されたコンポーネントのみアバターで使用可能。未登録コンポーネントはビルド時に除去される。
- **PhysBone 1コンポーネントあたり上限**: 256 Transform（rootTransform + 全子孫）。
- **VRC Constraint の FreezeToWorld**: ワールド座標に固定。`RebakeOffsetsWhenUnfrozen` でフリーズ解除時のオフセット再計算を制御。
- **VRC Constraint Sources**: `VRCConstraintSourceKeyableList` で管理。各ソースに Weight を持つ。
- **Contact の collisionTags**: Sender と Receiver で一致するタグがあるとき接触判定が発生。
- **Contact の contentTypes (DynamicsUsageFlags)**: PhysBone との衝突判定フラグ。

## 操作パターン (L2)

### PhysBone セットアップパターン

1. 揺らしたいボーンチェーンの親に **VRCPhysBone** を配置
2. `rootTransform` を設定（省略時は自身の Transform）
3. `pull`/`spring`/`stiffness`/`gravity` で物理パラメーターを調整（各パラメーターに Curve を使用可能）
4. 衝突が必要なら **VRCPhysBoneCollider** を別 GameObject に配置し、PhysBone の `colliders` リストに追加
5. `parameter` にプレフィックスを設定すると、`{parameter}_IsGrabbed`, `{parameter}_IsPosed`, `{parameter}_Angle`, `{parameter}_Stretch`, `{parameter}_Squish` がアニメーターパラメーターとして自動公開される

### Contact を使ったインタラクションパターン

1. 接触を発生させたいオブジェクトに **VRCContactSender** を配置
2. 接触を検知したいオブジェクトに **VRCContactReceiver** を配置
3. 両方の `collisionTags` に共通のタグを設定
4. Receiver の `parameter` にアニメーターパラメーター名を設定
5. `receiverType` で出力形式を選択（Constant, OnEnter, Proximity）
6. `allowSelf`/`allowOthers` で自分/他人との接触を制御

### VRC Constraint による追従パターン

1. 追従元オブジェクトに **VRCParentConstraint** を配置
2. `Sources` にターゲット Transform と Weight を追加
3. `Locked` を true にしてオフセットを固定
4. `FreezeToWorld` を有効にするとワールド座標に固定（Constraint 無効時もワールド座標を維持）
5. ワールド固定解除時に `RebakeOffsetsWhenUnfrozen` で再計算するか選択

### ビルドコールバック介入パターン

1. `IVRCSDKBuildRequestedCallback` を実装したクラスを作成（Editor assembly に配置）
2. `callbackOrder` でコールバックの実行順を制御
3. `OnBuildRequested` で事前バリデーション。`false` を返すとビルドを中止可能
4. より深い介入が必要なら `IVRCSDKPreprocessAvatarCallback` でアバターの GameObject を直接編集

## SerializedProperty リファレンス (L3)

ソースバージョン: 3.10.2 (Shiratsume) + 3.8.0 (UnityTool_sample) 差分検証済み
検証方法: DLL リフレクション (`[SerializeField]` / public フィールド列挙) + .meta ファイルの GUID 抽出（inspect 実測なし → confidence: medium）

### DLL Assembly GUID テーブル

DLL コンポーネントは `{fileID, guid}` の組み合わせで参照される。guid は DLL の .meta ファイルから、fileID はクラス名のハッシュから算出される。

| DLL | Assembly GUID | 含まれるコンポーネント |
|---|---|---|
| VRCSDKBase.dll | `db48663b319a020429e3b1265f97aff1` | VRC_EventHandler, VRCTestMarker 等（レガシー・基盤型） |
| VRC.Dynamics.dll | `cdfe97a8253414b4bb5dd295880489bd` | VRCPhysBoneBase, ContactBase, VRCConstraintBase（基底クラス） |
| VRC.SDK3.Dynamics.PhysBone.dll | `2a2c05204084d904aa4945ccff20d8e5` | VRCPhysBone, VRCPhysBoneCollider, VRCPhysBoneRoot |
| VRC.SDK3.Dynamics.Contact.dll | `80f1b8067b0760e4bb45023bc2e9de66` | VRCContactSender, VRCContactReceiver |
| VRC.SDK3.Dynamics.Constraint.dll | `58e2f01a24261a14cb82e6d3399e8b16` | VRCAim/LookAt/Parent/Position/Rotation/ScaleConstraint |
| SDKBase-Legacy.dll | `3d5d4d6a234c8bf47a3b99f4580f9f76` | レガシー SDK2 互換型 |

### Script GUID テーブル (.cs ファイル)

| コンポーネント | GUID | 備考 |
|---|---|---|
| ONSPAudioSource | `e503ea6418d27594caa33b93cac1b06a` | Oculus Spatializer。.cs ファイルの MonoImporter |
| ONSPSettings | `ad074644ff568a14187a3690cfbd7534` | Spatializer グローバル設定 |

### コンポーネント別フィールド

#### VRCPhysBone (VRC.SDK3.Dynamics.PhysBone.Components)

基底クラス `VRCPhysBoneBase` のフィールドを継承。

| propertyPath | 型 | 説明 |
|---|---|---|
| `version` | VRCPhysBoneBase.Version | PhysBone バージョン |
| `integrationType` | IntegrationType | 積分方式 |
| `rootTransform` | Transform | ルートボーン（null 時は自身） |
| `ignoreTransforms` | List\<Transform\> | 除外 Transform リスト |
| `ignoreOtherPhysBones` | bool | 他の PhysBone との干渉回避 |
| `endpointPosition` | Vector3 | 末端ボーンの仮想先端位置 |
| `multiChildType` | MultiChildType | 複数子ボーン時の扱い |
| `pull` | float | 復元力 |
| `pullCurve` | AnimationCurve | 復元力のボーンチェーン内分布 |
| `spring` | float | バネ力（振動性） |
| `springCurve` | AnimationCurve | |
| `stiffness` | float | 剛性 |
| `stiffnessCurve` | AnimationCurve | |
| `gravity` | float | 重力影響度 |
| `gravityCurve` | AnimationCurve | |
| `gravityFalloff` | float | 重力減衰（姿勢依存） |
| `gravityFalloffCurve` | AnimationCurve | |
| `immobileType` | ImmobileType | 不動モード種別 |
| `immobile` | float | 不動度 |
| `immobileCurve` | AnimationCurve | |
| `allowCollision` | AdvancedBool | 衝突許可 |
| `collisionFilter` | PermissionFilter | 衝突権限フィルタ |
| `radius` | float | 衝突半径 |
| `radiusCurve` | AnimationCurve | |
| `colliders` | List\<VRCPhysBoneColliderBase\> | コライダー参照リスト |
| `limitType` | LimitType | 角度制限種別 |
| `maxAngleX` | float | X 軸最大角度 |
| `maxAngleXCurve` | AnimationCurve | |
| `maxAngleZ` | float | Z 軸最大角度 |
| `maxAngleZCurve` | AnimationCurve | |
| `limitRotation` | Vector3 | 制限回転オフセット |
| `limitRotationXCurve` | AnimationCurve | |
| `limitRotationYCurve` | AnimationCurve | |
| `limitRotationZCurve` | AnimationCurve | |
| `allowGrabbing` | AdvancedBool | グラブ許可 [FormerlySerializedAs("isGrabbable")] |
| `grabFilter` | PermissionFilter | グラブ権限フィルタ |
| `allowPosing` | AdvancedBool | ポーズ許可 [FormerlySerializedAs("isPoseable")] |
| `poseFilter` | PermissionFilter | ポーズ権限フィルタ |
| `snapToHand` | bool | 手にスナップ |
| `grabMovement` | float | グラブ時の移動速度 |
| `maxStretch` | float | 最大伸長 |
| `maxStretchCurve` | AnimationCurve | |
| `maxSquish` | float | 最大圧縮 |
| `maxSquishCurve` | AnimationCurve | |
| `stretchMotion` | float | 伸長時のモーション |
| `stretchMotionCurve` | AnimationCurve | |
| `isAnimated` | bool | アニメーションで駆動される |
| `resetWhenDisabled` | bool | 無効時にリセット |
| `parameter` | string | アニメーターパラメータープレフィックス |

#### VRCPhysBoneCollider (VRC.SDK3.Dynamics.PhysBone.Components)

基底クラス `VRCPhysBoneColliderBase` のフィールドを継承。

| propertyPath | 型 | 説明 |
|---|---|---|
| `rootTransform` | Transform | コライダーの基準 Transform |
| `shapeType` | ShapeType | 形状（Sphere, Capsule, Plane） |
| `insideBounds` | bool | 内側判定 |
| `radius` | float | 半径 |
| `height` | float | 高さ（Capsule のみ） |
| `position` | Vector3 | オフセット位置 |
| `rotation` | Quaternion | オフセット回転 |
| `bonesAsSpheres` | bool | ボーンを球として扱う |
| `globalCollisionFlags` | DynamicsUsageFlags | グローバル衝突フラグ |

#### VRCContactSender (VRC.SDK3.Dynamics.Contact.Components)

基底クラス `ContactBase` → `ContactSender` のフィールドを継承。

| propertyPath | 型 | 説明 |
|---|---|---|
| `rootTransform` | Transform | コンタクト形状の基準 |
| `shapeType` | ShapeType | 形状（Sphere, Capsule） |
| `radius` | float | 半径 |
| `height` | float | 高さ |
| `position` | Vector3 | オフセット位置 |
| `rotation` | Quaternion | オフセット回転 |
| `localOnly` | bool | ローカル専用 |
| `contentTypes` | DynamicsUsageFlags | コンテンツタイプフラグ |
| `collisionTags` | List\<string\> | コリジョンタグ |

#### VRCContactReceiver (VRC.SDK3.Dynamics.Contact.Components)

ContactSender の全フィールドに加えて:

| propertyPath | 型 | 説明 |
|---|---|---|
| `allowSelf` | bool | 自身との接触を許可 |
| `allowOthers` | bool | 他者との接触を許可 |
| `receiverType` | ReceiverType | 受信タイプ（Constant, OnEnter, Proximity） |
| `parameter` | string | 駆動するアニメーターパラメーター名 |
| `minVelocity` | float | 最小速度閾値 |

#### VRC Constraint 共通フィールド (VRCConstraintBase)

全 6 種の VRC Constraint が継承する共通フィールド。

| propertyPath | 型 | 説明 |
|---|---|---|
| `IsActive` | bool | 有効/無効 |
| `GlobalWeight` | float | グローバルウェイト |
| `TargetTransform` | Transform | 自身の Transform（省略時は自身） |
| `SolveInLocalSpace` | bool | ローカル空間で解決 |
| `FreezeToWorld` | bool | ワールド座標に固定 |
| `RebakeOffsetsWhenUnfrozen` | bool | フリーズ解除時にオフセット再計算 |
| `Locked` | bool | オフセットをロック |
| `Sources` | VRCConstraintSourceKeyableList | ソース Transform + Weight のリスト |

#### VRCParentConstraint

VRCConstraintBase 共通フィールドに加えて:

| propertyPath | 型 | 説明 |
|---|---|---|
| `PositionAtRest` | Vector3 | ソースなし時の位置 |
| `AffectsPositionX/Y/Z` | bool | 各軸の位置影響 |
| `RotationAtRest` | Vector3 | ソースなし時の回転 |
| `AffectsRotationX/Y/Z` | bool | 各軸の回転影響 |

#### VRCPositionConstraint

| propertyPath | 型 | 説明 |
|---|---|---|
| `PositionAtRest` | Vector3 | ソースなし時の位置 |
| `PositionOffset` | Vector3 | 位置オフセット |
| `AffectsPositionX/Y/Z` | bool | 各軸の影響 |

#### VRCRotationConstraint

| propertyPath | 型 | 説明 |
|---|---|---|
| `RotationAtRest` | Vector3 | ソースなし時の回転 |
| `RotationOffset` | Vector3 | 回転オフセット |
| `AffectsRotationX/Y/Z` | bool | 各軸の影響 |

#### VRCScaleConstraint

| propertyPath | 型 | 説明 |
|---|---|---|
| `ScaleAtRest` | Vector3 | ソースなし時のスケール |
| `ScaleOffset` | Vector3 | スケールオフセット |
| `AffectsScaleX/Y/Z` | bool | 各軸の影響 |

#### VRCAimConstraint

VRCConstraintBase + WorldUpConstraint 共通フィールドに加えて:

| propertyPath | 型 | 説明 |
|---|---|---|
| `AffectsRotationX/Y/Z` | bool | 各軸の回転影響 |
| `AimAxis` | Vector3 | エイム軸 |
| `UpAxis` | Vector3 | アップ軸 |
| `WorldUp` | WorldUpType | ワールドアップの計算方法 |
| `WorldUpVector` | Vector3 | ワールドアップベクトル |
| `RotationAtRest` | Vector3 | ソースなし時の回転 |
| `RotationOffset` | Vector3 | 回転オフセット |
| `WorldUpTransform` | Transform | ワールドアップ Transform |

#### VRCLookAtConstraint

| propertyPath | 型 | 説明 |
|---|---|---|
| `Roll` | float | ロール角 |
| `UseUpTransform` | bool | アップ Transform を使用 |
| `RotationAtRest` | Vector3 | ソースなし時の回転 |
| `RotationOffset` | Vector3 | 回転オフセット |
| `WorldUpTransform` | Transform | ワールドアップ Transform |

#### ONSPAudioSource

.cs ファイル直接定義のコンポーネント。

| propertyPath | 型 | 説明 |
|---|---|---|
| `enableSpatialization` | bool | 空間音響有効 (デフォルト: true) |
| `gain` | float | ゲイン 0-24 dB (デフォルト: 0) |
| `useInvSqr` | bool | 逆二乗減衰 |
| `near` | float | 近距離 (デフォルト: 0.25) |
| `far` | float | 遠距離 (デフォルト: 250) |
| `volumetricRadius` | float | ボリュメトリック半径 0-1000 (デフォルト: 0) |
| `reverbSend` | float | リバーブセンド -60 to 20 dB (デフォルト: 0) |
| `enableRfl` | bool | 反射有効 |

### Audio 定数 (AudioManagerSettings)

| 定数 | 値 | 単位 |
|---|---|---|
| VoiceGain | 15 | dB |
| VoiceMaxRange | 25 | meters |
| MinVoiceSendDistance | 25 | meters |
| RoomAudioGain | 10 | dB |
| RoomAudioMaxRange | 80 | meters |
| AvatarAudioMaxGain | 10 | dB |
| AvatarAudioMaxRange | 40 | meters |

### 設計上の注意点

- **DLL コンポーネントの参照方式**: DLL 内のコンポーネントは `{fileID, guid}` で参照される。guid は DLL の .meta ファイルの値、fileID はクラスの完全修飾名から算出されるハッシュ（.cs の MonoScript GUID とは異なる）。
- **FormerlySerializedAs が 2 箇所**: `allowGrabbing`←"isGrabbable", `allowPosing`←"isPoseable"。PhysBone バージョンアップ時の互換性維持。
- **AdvancedBool 型**: PhysBone の `allowCollision`, `allowGrabbing`, `allowPosing` に使用。単純な bool ではなく、パーミッション制御を含む拡張型。
- **PermissionFilter 型**: `collisionFilter`, `grabFilter`, `poseFilter` で使用。自分/他人/フレンドの権限を個別制御。
- **VRCConstraintSourceKeyableList**: Constraint の Sources はカスタムリスト型。通常の `List<>` とは異なるシリアライズ構造を持つ可能性がある。
- **PhysBoneRoot**: `timing` フィールド (`RootTiming`) で PhysBone の実行タイミングを制御する特殊コンポーネント。

### 3.8.0 → 3.10.2 変更サマリー

- **新ファイル**: `ShaderLibrary/VRCTime.cginc`（シェーダーから UTC/ローカル時刻にアクセス可能に）
- **新ファイル**: `ToonStandardEditor.cs`（Mobile Toon Standard シェーダーの Inspector GUI）
- **Editor 追加**: `VRCAssetReview.cs`, `VRCAvatarStyle.cs`, `Selector.cs`, `VPMProjectManifest.cs`, `VRCAnalyticsTools.cs`, `VRCInspectorBase.cs`, `ComponentVersionMigrator.cs`, `ComponentVersionUI.cs`, `DynamicsSetup.cs`, `VRCUndoPostProcessor.cs`, VTP 関連
- **DLL 構成**: 変更なし（同一 DLL セット）
- **依存パッケージ**: 変更なし

## 実運用で学んだこと

(なし -- Phase 3-4 の検証で追記する)
