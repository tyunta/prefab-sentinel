---
tool: vrchat-sdk-avatars
version_tested: "3.10.2"
last_updated: 2026-03-26
confidence: medium
---

# VRChat SDK Avatars

## 概要 (L1)

VRChat のアバターシステム (Avatars 3.0) を構成する SDK パッケージ。`com.vrchat.avatars` (コンポーネント定義・エディタ) と `com.vrchat.base` (共通基盤・Dynamics DLL) の 2 パッケージで構成される。

**解決する問題**: VRChat ワールド内でプレイヤーが操作するアバターの定義。視点・リップシンク・アイトラッキング・表情メニュー・アニメーションレイヤー・物理演算 (PhysBone)・接触判定 (Contact)・制約 (Constraint) を宣言的に設定し、VRChat クライアントがランタイムで解釈・実行する。

**アーキテクチャ**: 主要コンポーネントは DLL (プリコンパイル済み) として配布され、ソースコードは非公開。エディタ拡張 (CustomEditor) は .cs で提供され、SerializedProperty パスの参照元となる。

**パッケージ依存**: `com.vrchat.avatars` → `com.vrchat.base` (同バージョン)。`com.vrchat.base` に PhysBone / Contact / Constraint の DLL が含まれる。

**最新バージョン**: 3.10.2 (Shiratsume プロジェクト)。比較対象: 3.8.0 (UnityTool_sample)。

## コンポーネント一覧 (L1 → L2)

### VRCAvatarDescriptor (中核コンポーネント)

アバターのルート GameObject に付与する必須コンポーネント。視点位置・リップシンク・アイトラッキング・アニメーションレイヤー・Expression メニュー/パラメーター・コライダー設定を統合管理する。

**カテゴリ別フィールド構成**:
- 基本設定: ViewPosition, ScaleIPD, unityVersion
- リップシンク: lipSync mode, VisemeSkinnedMesh, VisemeBlendShapes, JawBone
- アイトラッキング: enableEyeLook, customEyeLookSettings (Eye Movement, Eye Transforms, Eyelid)
- アニメーションレイヤー: customizeAnimationLayers, baseAnimationLayers[5], specialAnimationLayers[3]
- Expressions: customExpressions, expressionsMenu, expressionParameters
- 下半身: autoFootsteps, autoLocomotion
- コライダー: collider_head, collider_torso, collider_hand{L,R}, collider_foot{L,R}, collider_finger{Index,Middle,Ring,Little}{L,R}
- ポートレート: portraitCameraPositionOffset, portraitCameraRotationOffset
- ネットワーク: networkIDs
- アニメーション: AnimationPreset, animationHashSet

### Dynamics コンポーネント (com.vrchat.base)

| コンポーネント | 用途 | DLL |
|---|---|---|
| VRCPhysBone | ボーン物理演算 (揺れもの) | VRC.SDK3.Dynamics.PhysBone.dll |
| VRCPhysBoneCollider | PhysBone 用コライダー | VRC.SDK3.Dynamics.PhysBone.dll |
| VRCContactSender | 接触判定の送信側 | VRC.SDK3.Dynamics.Contact.dll |
| VRCContactReceiver | 接触判定の受信側 | VRC.SDK3.Dynamics.Contact.dll |

### Constraint コンポーネント (com.vrchat.base)

| コンポーネント | 用途 |
|---|---|
| VRCParentConstraint | 親制約 (位置+回転をソースに追従) |
| VRCPositionConstraint | 位置制約 |
| VRCRotationConstraint | 回転制約 |
| VRCScaleConstraint | スケール制約 |
| VRCAimConstraint | エイム制約 (指定方向をソースに向ける) |
| VRCLookAtConstraint | 注視制約 (Z 軸をソースに向ける) |

### StateMachineBehaviour コンポーネント

Animator の State/StateMachine に付与する振る舞い。GameObject ではなく AnimatorController 内に存在する。

| コンポーネント | 用途 |
|---|---|
| VRCAvatarParameterDriver | パラメーター値の Set/Add/Random/Copy |
| VRCAnimatorTrackingControl | 各部位のトラッキング/アニメーション切替 |
| VRCAnimatorLocomotionControl | ロコモーション有効/無効 |
| VRCAnimatorTemporaryPoseSpace | 一時的なポーズ空間の Enter/Exit |
| VRCAnimatorPlayAudio | オーディオ再生制御 |

### ScriptableObject アセット

| アセット型 | 用途 |
|---|---|
| VRCExpressionsMenu | Expression メニュー定義 (最大 8 コントロール/ページ) |
| VRCExpressionParameters | Expression パラメーター定義 (同期予算 256 bits) |

### その他

| コンポーネント | 用途 |
|---|---|
| PipelineManager (VRC.Core) | アバターの blueprintId・アップロード状態管理 |
| VRCPerPlatformOverrides | プラットフォーム別アバター設定 (3.10+ 新規) |

## パフォーマンスランク基準 (L2)

アバターのパフォーマンスランクは各メトリクスの最悪値で決定される (1 項目でも超えると下位ランクに落ちる)。VeryPoor は Poor の上限を超えたもの。

### PC

| メトリクス | Excellent | Good | Medium | Poor |
|---|---|---|---|---|
| Triangles | 32,000 | 70,000 | 70,000 | 70,000 |
| Bounds Size | 2.5m | 4m | 5x6x5m | 5x6x5m |
| Texture Memory | 40 MB | 75 MB | 110 MB | 150 MB |
| Skinned Meshes | 1 | 2 | 8 | 16 |
| Basic Meshes | 4 | 8 | 16 | 24 |
| Material Slots | 4 | 8 | 16 | 32 |
| PhysBone Components | 4 | 8 | 16 | 32 |
| PhysBone Transforms | 16 | 64 | 128 | 256 |
| PhysBone Colliders | 4 | 8 | 16 | 32 |
| PhysBone Collision Checks | 32 | 128 | 256 | 512 |
| Contacts | 8 | 16 | 24 | 32 |
| Constraint Count | 100 | 250 | 300 | 350 |
| Constraint Depth | 20 | 50 | 80 | 100 |
| Animators | 1 | 4 | 16 | 32 |
| Bones | 75 | 150 | 256 | 400 |
| Lights | 0 | 0 | 0 | 1 |
| Particle Systems | 0 | 4 | 8 | 16 |
| Total Particles Active | 0 | 300 | 1,000 | 2,500 |
| Trail Renderers | 1 | 2 | 4 | 8 |
| Line Renderers | 1 | 2 | 4 | 8 |
| Cloths | 0 | 1 | 1 | 1 |
| Audio Sources | 1 | 4 | 8 | 8 |

### Mobile (Android/iOS/Meta Quest)

| メトリクス | Excellent | Good | Medium | Poor |
|---|---|---|---|---|
| Triangles | 7,500 | 10,000 | 15,000 | 20,000 |
| Bounds Size | 2.5m | 4m | 5x6x5m | 5x6x5m |
| Texture Memory | 10 MB | 18 MB | 25 MB | 40 MB |
| Skinned Meshes | 1 | 1 | 2 | 2 |
| Basic Meshes | 1 | 1 | 2 | 2 |
| Material Slots | 1 | 1 | 2 | 4 |
| PhysBone Components | 0 | 4 | 6 | 8 |
| PhysBone Transforms | 0 | 16 | 32 | 64 |
| PhysBone Colliders | 0 | 4 | 8 | 16 |
| PhysBone Collision Checks | 0 | 16 | 32 | 64 |
| Contacts | 2 | 4 | 8 | 16 |
| Constraint Count | 30 | 60 | 120 | 150 |
| Constraint Depth | 5 | 15 | 35 | 50 |
| Animators | 1 | 1 | 1 | 2 |
| Bones | 75 | 90 | 150 | 150 |
| Particle Systems | 0 | 0 | 0 | 2 |
| Total Particles Active | 0 | 0 | 0 | 200 |
| Trail Renderers | 0 | 0 | 0 | 1 |
| Line Renderers | 0 | 0 | 0 | 1 |

## プラットフォーム制約 (L2)

### Expressions (Menu + Parameters)

- **メニュー**: 1 ページあたり最大 8 コントロール (`VRCExpressionsMenu.MAX_CONTROLS`)
- **サブメニュー深さ**: 実装上の再帰制限は 16 (`IsSubmenuRecursive` の depth limit)
- **コントロール種別**: Button, Toggle, Sub-Menu, Two-Axis Puppet, Four-Axis Puppet, Radial Puppet
- **パラメーター同期予算**: 256 bits (`VRCExpressionParameters.MAX_PARAMETER_COST`)
  - Bool: 1 bit
  - Int: 8 bits (0-255)
  - Float: 8 bits (-1.0 to 1.0, signed fixed-point)
- **パラメーター総数上限**: 8,192 (synced + unsynced)
- **ビルトインパラメーター**: IsLocal, Viseme, GestureLeft/Right, VelocityX/Y/Z 等は予算を消費しない
- **パラメーター属性**: saved (永続化), synced (ネットワーク同期), defaultValue

### PhysBone 制約

- **1 コンポーネントあたり最大トランスフォーム**: 256 (root + 全子を含む)
- **バウンディングボックス最大**: 10x10x10m
- **Polar Limit**: 64 を超えるとパフォーマンス問題が発生しうる
- **コライダー形状**: Sphere, Capsule, Plane
- **コライダー最大サイズ**: radius 3m, height 6m (post-scaling)
- **Grab/Pose フィルター**: Self/Others を個別に許可/拒否
- **パラメーター連動**: `{prefix}_IsGrabbed`, `{prefix}_IsPosed`, `{prefix}_Angle`, `{prefix}_Stretch`, `{prefix}_Squish`
- **Constraint との共存禁止**: 同一 GameObject に PhysBone と Constraint を同時配置してはならない

### Quest/Android 固有の制約

- **シェーダー**: VRChat/Mobile シリーズのみ (Toon Standard, Standard Lite, Bumped Diffuse, Matcap Lit, Toon Lit 等)
- **完全無効化コンポーネント**: Dynamic Bone, Cloth, Camera, Light, Audio Source, Physics Objects (Rigidbody/Collider/Joint), Unity Constraints, FinalIK
- **PhysBone ハード制限**: Very Poor ランクの値がハード上限として機能
- **GPU Instancing**: 全マテリアルで有効化が推奨
- **Particle Systems**: 大幅に制限 (Poor ランクまで 0)

## 操作パターン (L2)

### アバター基本セットアップ

1. アバターのルート GameObject に **VRCAvatarDescriptor** を配置
2. PipelineManager が自動追加される (blueprintId でアップロード先を識別)
3. ViewPosition を目の間の少し前方に設定 (一人称視点の基準)
4. LipSync を VisemeBlendShape モードに設定し、Face Mesh と 15 Viseme をマッピング
5. Eye Look を有効化し、左右の Eye Transform と Eyelid 設定を行う
6. Playable Layers を Customize し、Base/Additive/Gesture/Action/FX に AnimatorController を割り当て
7. Expressions で Menu と Parameters アセットを参照設定

### PhysBone セットアップ (髪・衣装の揺れもの)

1. 揺れもののルートボーンに **VRCPhysBone** を配置
2. rootTransform が空の場合、コンポーネントの GameObject が root になる
3. endpointPosition を設定して末端を定義 (root にコンポーネントがある場合は必須)
4. Forces (Pull/Spring/Stiffness/Gravity) を調整
5. Limits (Angle/Hinge/Polar) で可動範囲を制限
6. 必要に応じて VRCPhysBoneCollider を配置し、colliders 配列に参照
7. Grab/Pose を許可する場合は parameter プレフィックスを設定してアニメーターと連動

### Expression メニュー構築

1. VRCExpressionParameters アセットを作成し、必要なパラメーターを定義
2. VRCExpressionsMenu アセットを作成し、コントロールを追加 (最大 8/ページ)
3. サブメニューは別の VRCExpressionsMenu アセットを参照
4. VRCAvatarDescriptor の expressionsMenu / expressionParameters に設定
5. 同期予算 (256 bits) を超えないよう CalcTotalCost で確認

### VRC Constraint によるギミック

1. 対象 GameObject に VRCParentConstraint / VRCRotationConstraint 等を配置
2. Sources に追従先 Transform を設定 (最大 8 ソース、source0-source7 の固定配列)
3. Weight で影響度を制御
4. SolveInLocalSpace / FreezeToWorld でローカル空間解決やワールド固定を選択
5. Locked=true で現在のオフセットをベイク

## SerializedProperty リファレンス (L3)

ソースバージョン: 3.10.2 (Shiratsume) + 3.8.0 (UnityTool_sample) 差分検証済み
検証方法: Editor .cs ソースの FindProperty() 呼び出し + .prefab YAML からのフィールド名抽出 + .dll.meta からの GUID 取得 (inspect 実測なし -> confidence: medium)

### DLL GUID テーブル

DLL ベースのコンポーネントは `{fileID: <class_hash>, guid: <dll_guid>, type: 3}` で参照される。fileID は namespace+classname のハッシュ。

| DLL | GUID | 含まれるコンポーネント |
|---|---|---|
| VRCSDK3A.dll | `67cc4cb7839cd3741b63733d5adf0442` | VRCAvatarDescriptor, VRCExpressionParameters, VRCExpressionsMenu, StateMachineBehaviours |
| VRC.SDK3.Dynamics.PhysBone.dll | `2a2c05204084d904aa4945ccff20d8e5` | VRCPhysBone, VRCPhysBoneCollider |
| VRC.SDK3.Dynamics.Contact.dll | `80f1b8067b0760e4bb45023bc2e9de66` | VRCContactSender, VRCContactReceiver |
| VRC.SDK3.Dynamics.Constraint.dll | `58e2f01a24261a14cb82e6d3399e8b16` | VRCParentConstraint 他 6 種 |
| VRCSDKBase.dll | `db48663b319a020429e3b1265f97aff1` | 基底クラス (VRC_AvatarDescriptor 等) |
| VRCCore-Editor.dll | `4ecd63eff847044b68db9453ce219299` | PipelineManager |

### Script fileID テーブル

| コンポーネント | fileID | DLL GUID |
|---|---|---|
| VRCAvatarDescriptor | `542108242` | `67cc4cb7839cd3741b63733d5adf0442` |
| VRCPhysBone | `1661641543` | `2a2c05204084d904aa4945ccff20d8e5` |
| VRCPhysBoneCollider | `-1631200402` | `2a2c05204084d904aa4945ccff20d8e5` |
| VRCParentConstraint | `1788371120` | `58e2f01a24261a14cb82e6d3399e8b16` |
| PipelineManager | `-1427037861` | `4ecd63eff847044b68db9453ce219299` |

### VRCAvatarDescriptor フィールド

#### 基本設定

| propertyPath | 型 | 説明 |
|---|---|---|
| `Name` | string | アバター名 (内部用) |
| `ViewPosition` | Vector3 | 一人称視点の位置 (アバターローカル座標) |
| `Animations` | int | アニメーション種別 (0=Male, 1=Female 等、レガシー) |
| `ScaleIPD` | bool | IPD スケーリング有効 |
| `unityVersion` | string | 最後に保存した Unity バージョン |
| `portraitCameraPositionOffset` | Vector3 | ポートレートカメラ位置オフセット |
| `portraitCameraRotationOffset` | Quaternion | ポートレートカメラ回転オフセット |
| `networkIDs` | array | ネットワーク ID リスト |

#### LipSync

| propertyPath | 型 | 説明 |
|---|---|---|
| `lipSync` | LipSyncStyle (enum) | Default=0, JawFlapBlendShape=1, JawFlapBone=2, VisemeBlendShape=3, VisemeParameterOnly=4 |
| `lipSyncJawBone` | Transform | JawFlapBone モード用の顎ボーン |
| `lipSyncJawClosed` | Quaternion | 顎の閉じ回転 |
| `lipSyncJawOpen` | Quaternion | 顎の開き回転 |
| `VisemeSkinnedMesh` | SkinnedMeshRenderer | Viseme/JawFlap 用のメッシュ |
| `MouthOpenBlendShapeName` | string | JawFlapBlendShape モード用のブレンドシェイプ名 |
| `VisemeBlendShapes` | string[15] | 15 Viseme のブレンドシェイプ名 (sil, PP, FF, TH, DD, kk, CH, SS, nn, RR, aa, E, ih, oh, ou) |

#### Eye Look

| propertyPath | 型 | 説明 |
|---|---|---|
| `enableEyeLook` | bool | アイトラッキング有効 |
| `customEyeLookSettings.eyeMovement.confidence` | float | 視線の安定度 (0-1) |
| `customEyeLookSettings.eyeMovement.excitement` | float | 視線の活発度 (0-1) |
| `customEyeLookSettings.leftEye` | Transform | 左目ボーン |
| `customEyeLookSettings.rightEye` | Transform | 右目ボーン |
| `customEyeLookSettings.eyesLookingStraight.linked` | bool | 左右連動 |
| `customEyeLookSettings.eyesLookingStraight.left` | Quaternion | 正面視の左目回転 |
| `customEyeLookSettings.eyesLookingStraight.right` | Quaternion | 正面視の右目回転 |
| `customEyeLookSettings.eyesLookingUp` | EyeRotations | 上方視 (同構造) |
| `customEyeLookSettings.eyesLookingDown` | EyeRotations | 下方視 |
| `customEyeLookSettings.eyesLookingLeft` | EyeRotations | 左方視 |
| `customEyeLookSettings.eyesLookingRight` | EyeRotations | 右方視 |
| `customEyeLookSettings.eyelidType` | EyelidType (enum) | None=0, Bones=1, Blendshapes=2 |
| `customEyeLookSettings.upperLeftEyelid` | Transform | 上まぶた左 (Bones モード) |
| `customEyeLookSettings.upperRightEyelid` | Transform | 上まぶた右 |
| `customEyeLookSettings.lowerLeftEyelid` | Transform | 下まぶた左 |
| `customEyeLookSettings.lowerRightEyelid` | Transform | 下まぶた右 |
| `customEyeLookSettings.eyelidsDefault` | EyelidRotations | デフォルトまぶた回転 |
| `customEyeLookSettings.eyelidsClosed` | EyelidRotations | 閉じまぶた回転 |
| `customEyeLookSettings.eyelidsLookingUp` | EyelidRotations | 上方視まぶた回転 |
| `customEyeLookSettings.eyelidsLookingDown` | EyelidRotations | 下方視まぶた回転 |
| `customEyeLookSettings.eyelidsSkinnedMesh` | SkinnedMeshRenderer | まぶたブレンドシェイプ用メッシュ |
| `customEyeLookSettings.eyelidsBlendshapes` | byte[] | まぶたブレンドシェイプインデックス (バイナリ) |

**EyelidRotations 構造**: `upper.linked`, `upper.left` (Quaternion), `upper.right` (Quaternion), `lower.linked`, `lower.left`, `lower.right`

#### Animation Layers

| propertyPath | 型 | 説明 |
|---|---|---|
| `customizeAnimationLayers` | bool | カスタムレイヤー有効 |
| `baseAnimationLayers` | CustomAnimLayer[] | 基本 5 レイヤー |
| `baseAnimationLayers.Array.data[n].type` | AnimLayerType (enum) | Base=0, Additive=2, Gesture=3, Action=4, FX=5 |
| `baseAnimationLayers.Array.data[n].animatorController` | RuntimeAnimatorController | コントローラー参照 |
| `baseAnimationLayers.Array.data[n].mask` | AvatarMask | レイヤーマスク |
| `baseAnimationLayers.Array.data[n].isDefault` | bool | デフォルトコントローラー使用 |
| `baseAnimationLayers.Array.data[n].isEnabled` | bool | レイヤー有効 |
| `specialAnimationLayers` | CustomAnimLayer[] | 特殊 3 レイヤー |
| `specialAnimationLayers.Array.data[n].type` | AnimLayerType (enum) | Sitting=6, TPose=7, IKPose=8 |
| `AnimationPreset` | Object | アニメーションプリセット |
| `animationHashSet` | AnimationHashPair[] | アニメーション名→ハッシュマッピング |

**AnimLayerType enum**: Base=0, (1=deprecated), Additive=2, Gesture=3, Action=4, FX=5, Sitting=6, TPose=7, IKPose=8

**Humanoid vs Generic**: Humanoid は 5 Base + 3 Special。Generic は Base, Action, FX の 3 Base + 3 Special (Additive/Gesture はヒューマノイド専用)。

#### Expressions

| propertyPath | 型 | 説明 |
|---|---|---|
| `customExpressions` | bool | カスタム Expression 有効 |
| `expressionsMenu` | VRCExpressionsMenu | メニューアセット参照 |
| `expressionParameters` | VRCExpressionParameters | パラメーターアセット参照 |

#### Lower Body

| propertyPath | 型 | 説明 |
|---|---|---|
| `autoFootsteps` | bool | 3/4 点トラッキング時の自動フットステップ |
| `autoLocomotion` | bool | 6 点トラッキング時のロコモーション強制 |

#### Colliders (Avatar Dynamics 用)

各コライダーは `ColliderConfig` 構造体。PhysBone の標準コライダースロットとして機能する。

| propertyPath | 型 | 説明 |
|---|---|---|
| `collider_head` | ColliderConfig | 頭コライダー |
| `collider_torso` | ColliderConfig | 胴体コライダー |
| `collider_handL` / `collider_handR` | ColliderConfig | 手コライダー (左/右) |
| `collider_footL` / `collider_footR` | ColliderConfig | 足コライダー (左/右) |
| `collider_fingerIndexL` / `R` | ColliderConfig | 人差し指コライダー |
| `collider_fingerMiddleL` / `R` | ColliderConfig | 中指コライダー |
| `collider_fingerRingL` / `R` | ColliderConfig | 薬指コライダー |
| `collider_fingerLittleL` / `R` | ColliderConfig | 小指コライダー |

**ColliderConfig 構造体**:
| サブパス | 型 | 説明 |
|---|---|---|
| `.state` | State (enum) | Disabled=0, Automatic=1, Custom=2 (Disabled は非表示、Automatic はヒューマノイドボーンから自動計算) |
| `.isMirrored` | bool | 左右ミラー |
| `.transform` | Transform | コライダーの基準 Transform (Automatic モードで自動設定) |
| `.radius` | float | コライダー半径 |
| `.height` | float | コライダー高さ (カプセル) |
| `.position` | Vector3 | オフセット位置 |
| `.rotation` | Quaternion | オフセット回転 |

### VRCPhysBone フィールド

| propertyPath | 型 | 説明 |
|---|---|---|
| `version` | int | PhysBone バージョン (0=1.0, 1=1.1) |
| `integrationType` | IntegrationType (enum) | Simplified=0, Advanced=1 |
| `rootTransform` | Transform | ルートボーン (空=自身の GameObject) |
| `ignoreTransforms` | Transform[] | 除外するトランスフォーム |
| `ignoreOtherPhysBones` | bool | 他 PhysBone の影響を無視 (デフォルト: true) |
| `endpointPosition` | Vector3 | 末端位置 |
| `multiChildType` | MultiChildType (enum) | Ignore=0, First=1, Average=2 |
| `pull` | float | 元に戻る力 |
| `pullCurve` | AnimationCurve | Pull のボーン位置カーブ |
| `spring` | float | バネ力 (Simplified のみ) |
| `springCurve` | AnimationCurve | Spring カーブ |
| `stiffness` | float | 剛性 (Advanced のみ) |
| `stiffnessCurve` | AnimationCurve | Stiffness カーブ |
| `gravity` | float | 重力影響 |
| `gravityCurve` | AnimationCurve | Gravity カーブ |
| `gravityFalloff` | float | 重力減衰 |
| `gravityFalloffCurve` | AnimationCurve | GravityFalloff カーブ |
| `immobileType` | ImmobileType (enum) | AllMotion=0, World=1 |
| `immobile` | float | 固定度 |
| `immobileCurve` | AnimationCurve | Immobile カーブ |
| `allowCollision` | AllowType (enum) | True=0, False=1, Other=2 |
| `collisionFilter.allowSelf` | bool | 自身との衝突 |
| `collisionFilter.allowOthers` | bool | 他者との衝突 |
| `radius` | float | コリジョン半径 |
| `radiusCurve` | AnimationCurve | Radius カーブ |
| `colliders` | VRCPhysBoneCollider[] | コライダー参照リスト |
| `limitType` | LimitType (enum) | None=0, Angle=1, Hinge=2, Polar=3 |
| `maxAngleX` | float | X 軸最大角度 |
| `maxAngleXCurve` | AnimationCurve | MaxAngleX カーブ |
| `maxAngleZ` | float | Z 軸最大角度 |
| `maxAngleZCurve` | AnimationCurve | MaxAngleZ カーブ |
| `limitRotation` | Vector3 | リミット回転 (Pitch/Yaw/Roll) |
| `limitRotationXCurve` | AnimationCurve | LimitRotationX カーブ |
| `limitRotationYCurve` | AnimationCurve | LimitRotationY カーブ |
| `limitRotationZCurve` | AnimationCurve | LimitRotationZ カーブ |
| `allowGrabbing` | AllowType (enum) | True=0, False=1, Other=2 |
| `grabFilter.allowSelf` | bool | 自身によるグラブ |
| `grabFilter.allowOthers` | bool | 他者によるグラブ |
| `allowPosing` | AllowType (enum) | True=0, False=1, Other=2 |
| `poseFilter.allowSelf` | bool | 自身によるポーズ |
| `poseFilter.allowOthers` | bool | 他者によるポーズ |
| `snapToHand` | bool | グラブ時に手にスナップ |
| `grabMovement` | float | グラブ移動速度 (0=物理, 1=即時) |
| `maxStretch` | float | 最大伸び |
| `maxStretchCurve` | AnimationCurve | MaxStretch カーブ |
| `maxSquish` | float | 最大圧縮 |
| `maxSquishCurve` | AnimationCurve | MaxSquish カーブ |
| `stretchMotion` | float | モーションベースの伸び |
| `stretchMotionCurve` | AnimationCurve | StretchMotion カーブ |
| `isAnimated` | bool | アニメーション対応 (true でないとアニメーションで値変更不可) |
| `resetWhenDisabled` | bool | 無効化時にリセット |
| `parameter` | string | アニメーターパラメーターのプレフィックス |
| `showGizmos` | bool | ギズモ表示 |
| `boneOpacity` | float | ボーンギズモ透明度 |
| `limitOpacity` | float | リミットギズモ透明度 |

**エディタ専用 foldout フィールド** (非ランタイム): `foldout_transforms`, `foldout_forces`, `foldout_collision`, `foldout_stretchsquish`, `foldout_limits`, `foldout_grabpose`, `foldout_options`, `foldout_gizmos`

### VRCPhysBoneCollider フィールド

| propertyPath | 型 | 説明 |
|---|---|---|
| `rootTransform` | Transform | コライダーの基準トランスフォーム (空=自身) |
| `shapeType` | ShapeType (enum) | Sphere=0, Capsule=1, Plane=2 |
| `insideBounds` | bool | 内側バウンド (true=ボーンを内側に閉じ込める) |
| `radius` | float | コライダー半径 |
| `height` | float | カプセル高さ |
| `position` | Vector3 | オフセット位置 |
| `rotation` | Quaternion | オフセット回転 |
| `bonesAsSpheres` | bool | ボーンをスフィアとして扱う |

### VRC Constraint 共通フィールド

全 VRC Constraint 型に共通するフィールド (VRCParentConstraint で確認)。

| propertyPath | 型 | 説明 |
|---|---|---|
| `IsActive` | bool | 制約有効 |
| `GlobalWeight` | float | グローバルウェイト |
| `TargetTransform` | Transform | ターゲット (空=自身) |
| `SolveInLocalSpace` | bool | ローカル空間で解決 |
| `FreezeToWorld` | bool | ワールド座標に固定 |
| `RebakeOffsetsWhenUnfrozen` | bool | 解除時にオフセット再ベイク |
| `Locked` | bool | オフセットをロック |
| `Sources.source0` - `Sources.source7` | ConstraintSource | 固定 8 スロットのソース配列 |

**ConstraintSource 構造体**:
| サブパス | 型 | 説明 |
|---|---|---|
| `.SourceTransform` | Transform | ソーストランスフォーム |
| `.Weight` | float | ソースウェイト |
| `.ParentPositionOffset` | Vector3 | 位置オフセット (ParentConstraint) |
| `.ParentRotationOffset` | Vector3 | 回転オフセット (ParentConstraint) |

**設計上の注意**: Sources は Unity の可変長リストではなく **source0-source7 の固定 8 スロット構造**。未使用スロットは SourceTransform={fileID:0}。`Sources.Array.data[n]` ではなく `Sources.source0` のようにアクセスする。

### VRCExpressionParameters フィールド (ScriptableObject)

| propertyPath | 型 | 説明 |
|---|---|---|
| `parameters` | Parameter[] | パラメーター配列 |
| `parameters.Array.data[n].name` | string | パラメーター名 |
| `parameters.Array.data[n].valueType` | ValueType (enum) | Int=0, Float=1, Bool=2 |
| `parameters.Array.data[n].defaultValue` | float | デフォルト値 |
| `parameters.Array.data[n].saved` | bool | セッション間で永続化 |
| `parameters.Array.data[n].networkSynced` | bool | ネットワーク同期 |
| `isEmpty` | bool | パラメーターリストが意図的に空 |

### VRCAvatarParameterDriver フィールド (StateMachineBehaviour)

| propertyPath | 型 | 説明 |
|---|---|---|
| `localOnly` | bool | ローカルのみで実行 |
| `debugString` | string | デバッグ文字列 |
| `parameters` | Parameter[] | パラメーター操作リスト |
| `parameters.Array.data[n].type` | ChangeType (enum) | Set=0, Add=1, Random=2, Copy=3 |
| `parameters.Array.data[n].name` | string | 操作先パラメーター名 |
| `parameters.Array.data[n].value` | float | 設定値 (Set/Add) |
| `parameters.Array.data[n].valueMin` | float | ランダム最小値 |
| `parameters.Array.data[n].valueMax` | float | ランダム最大値 |
| `parameters.Array.data[n].chance` | float | ランダム確率 (Bool 用) |
| `parameters.Array.data[n].preventRepeats` | bool | ランダム繰り返し防止 (Int 用) |
| `parameters.Array.data[n].source` | string | コピー元パラメーター名 (Copy) |
| `parameters.Array.data[n].convertRange` | bool | 範囲変換有効 (Copy) |
| `parameters.Array.data[n].sourceMin` | float | コピー元範囲最小 |
| `parameters.Array.data[n].sourceMax` | float | コピー元範囲最大 |
| `parameters.Array.data[n].destMin` | float | コピー先範囲最小 |
| `parameters.Array.data[n].destMax` | float | コピー先範囲最大 |

### VRCAnimatorTrackingControl フィールド (StateMachineBehaviour)

| propertyPath | 型 | 説明 |
|---|---|---|
| `trackingHead` | TrackingType (enum) | NoChange=0, Tracking=1, Animation=2 |
| `trackingLeftHand` | TrackingType | 左手 |
| `trackingRightHand` | TrackingType | 右手 |
| `trackingHip` | TrackingType | 腰 |
| `trackingLeftFoot` | TrackingType | 左足 |
| `trackingRightFoot` | TrackingType | 右足 |
| `trackingLeftFingers` | TrackingType | 左指 |
| `trackingRightFingers` | TrackingType | 右指 |
| `trackingEyes` | TrackingType | 目・まぶた |
| `trackingMouth` | TrackingType | 口・顎 |
| `debugString` | string | デバッグ文字列 |

### VRCAnimatorLocomotionControl フィールド (StateMachineBehaviour)

| propertyPath | 型 | 説明 |
|---|---|---|
| `disableLocomotion` | bool | ロコモーション無効化 |
| `debugString` | string | デバッグ文字列 |

### VRCAnimatorTemporaryPoseSpace フィールド (StateMachineBehaviour)

| propertyPath | 型 | 説明 |
|---|---|---|
| `enterPoseSpace` | bool | ポーズ空間に入る (false=出る) |
| `fixedDelay` | bool | 固定遅延 (false=正規化遅延) |
| `delayTime` | float | 遅延時間 (秒 or %) |
| `debugString` | string | デバッグ文字列 |

### VRCAnimatorPlayAudio フィールド (StateMachineBehaviour)

| propertyPath | 型 | 説明 |
|---|---|---|
| `SourcePath` | string | AudioSource のアバタールート相対パス |
| `PlaybackOrder` | Order (enum) | Random / UniqueRandom / Roundabout / Parameter |
| `ParameterName` | string | Parameter モード用の Int パラメーター名 |
| `Clips` | AudioClip[] | 再生クリップ配列 |
| `ClipsApplySettings` | ApplySettings (enum) | AlwaysApply / ApplyIfStopped / NeverApply |
| `Pitch` | Vector2 | ランダムピッチ範囲 (x=min, y=max) |
| `PitchApplySettings` | ApplySettings | ピッチ適用設定 |
| `Volume` | Vector2 | ランダムボリューム範囲 (x=min, y=max) |
| `VolumeApplySettings` | ApplySettings | ボリューム適用設定 |
| `Loop` | bool | ループ再生 |
| `LoopApplySettings` | ApplySettings | ループ適用設定 |
| `DelayInSeconds` | float | PlayOnEnter 遅延 (0-60 秒) |
| `StopOnEnter` | bool | ステート進入時に停止 |
| `PlayOnEnter` | bool | ステート進入時に再生 |
| `StopOnExit` | bool | ステート退出時に停止 |
| `PlayOnExit` | bool | ステート退出時に再生 |

**ビルドバリデーション要件 (SDK 3.10.2)**:
- VRCAnimatorPlayAudio の `Clips` で参照される AudioClip は、`Load In Background` を有効にする必要がある（`Decompress On Load` タイプの場合）。無効だとビルド時に `Avatar validation failed` エラーで拒否される。AudioClip の .meta ファイルで `loadInBackground: 1` を設定すること。

### PipelineManager フィールド

| propertyPath | 型 | 説明 |
|---|---|---|
| `launchedFromSDKPipeline` | bool | SDK パイプラインから起動 |
| `completedSDKPipeline` | bool | SDK パイプライン完了 |
| `blueprintId` | string | VRChat アバター ID |
| `contentType` | int | コンテンツ種別 (0=Avatar) |
| `assetBundleUnityVersion` | string | AssetBundle のビルド Unity バージョン |
| `fallbackStatus` | int | フォールバック状態 |

### 3.8.0 → 3.10.2 変更サマリー

- **新コンポーネント**: VRCPerPlatformOverrides (プラットフォーム別アバターオーバーライド)
- **削除**: VRCAnimatorRemeasureAvatar (エディタ側のみ存在した模様)
- **DLL 変更**: VRCSDK3A.dll, VRC.SDK3.Dynamics.PhysBone.dll が更新 (バイナリ差異あり)
- **GUID 変更なし**: DLL meta の GUID は 3.8.0 と 3.10.2 で同一
- **エディタ変更**: Colliders エディタ、ParameterDriver エディタ、ExpressionsControlOptions が更新。AvatarSelector/StyleField/PerPlatformOverrides エレメントが追加

### 設計上の注意点

- **DLL ベースのコンポーネント**: MonoBehaviour のソースコードは非公開 DLL 内にある。フィールド名は .prefab YAML と Editor .cs の FindProperty() 呼び出しから逆引きする必要がある。
- **VRC Constraint Sources の固定配列**: Unity の標準的な `Array.data[n]` パスではなく `source0`-`source7` の固定名フィールド。prefab-sentinel で配列として扱う場合は特殊処理が必要。
- **ColliderConfig の state=Automatic**: Automatic モードでは transform フィールドが {fileID:0} でも、エディタがヒューマノイドボーンから自動計算して表示する。YAML 上は transform が空に見えるが実際には機能している。
- **animationHashSet**: VRCAvatarDescriptor はアニメーションステート名とハッシュのマッピングを保持する。ビルド時に AnimatorController から自動生成される内部データ。
- **eyelidsBlendshapes のバイナリ形式**: `eyelidsBlendshapes` フィールドは int[] をバイトシリアライズした hex 文字列 (例: `ffffffff2b0100002e010000` = [-1, 299, 302])。ブレンドシェイプインデックスを直接格納。
- **StateMachineBehaviour は AnimatorController 内**: VRCAvatarParameterDriver 等はプレファブ YAML 内ではなく .controller アセット内に埋め込まれる。fileID は AnimatorController アセット内のローカル ID。

## 実運用で学んだこと

(なし -- Phase 3-4 の検証で追記する)
