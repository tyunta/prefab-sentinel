---
tool: modular-avatar
version_tested: "1.16.2"
last_updated: 2026-03-26
confidence: medium
---

# Modular Avatar

## 概要 (L1)

非破壊アバター改変ツール。NDMF (Non-Destructive Modular Framework) プラグインとして動作し、ビルド時にコンポーネントを処理してアバターを組み立てる。最終アバターにはランタイムコンポーネントが残らない。

**解決する問題**: 衣装・ギミック・メニューの追加/削除を手動で行う煩雑さ。ドラッグ&ドロップでプレファブを配置するだけで、ビルド時にアーマチュア統合・アニメーター統合・メニュー組み立てが自動実行される。

**NDMF との関係**: MA は NDMF >= 1.8.0 に依存。NDMF はビルドパイプラインを Resolving → Transforming → Optimizing の 3 フェーズで実行し、MA はこのフェーズ内で約 30 の Pass を逐次実行する。

**プラットフォーム**: 主対象は VRChat アバター (VRChat SDK 3.x)。一部 Pass は `[RunsOnPlatforms]` で VRChat 専用。1.13.0 から非 VRC プラットフォームの実験的サポートあり。

**最新バージョン**: 1.16.2 (2025-02-11)。VPM リポジトリ `https://vpm.nadena.dev/vpm.json` から配布。

## コンポーネント一覧 (L1→L2)

### アーマチュア・ボーン操作

| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| MA Merge Armature | 衣装のボーン階層をアバターのアーマチュアに統合する | 衣装プレファブのルートに配置。プレフィックス/サフィックスでボーン名マッチング。一致するボーンは参照を書き換え、一致しないボーンは子として追加 |
| MA Bone Proxy | オブジェクトを既存ボーンの子に配置する | ヘアピン、コライダー等の単一ボーンターゲットアクセサリ。Merge Armature より軽量 |
| MA Scale Adjuster | アバターのスケール調整 | スケール変更時のボーン位置補正 |
| MA World Fixed Object | ワールド座標に固定 | アバターサイズに依存しない位置固定オブジェクト |

**Merge Armature のロックモード**:
- Not locked: 編集時に独立
- Unidirectional (Base→Target): アバターに追従、手動調整を保持（デフォルト）
- Bidirectional: 双方向同期

### アニメーター・パラメーター

| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| MA Merge Animator | アニメーターコントローラを統合 | FX レイヤーを複数サブアニメーターに分割して管理。レイヤー優先度と Write Defaults 解析あり |
| MA Merge Motion (Blend Tree) | ブレンドツリーを Direct BlendTree 構造にマージ | パフォーマンス最適化。複数レイヤーを 1 つの Direct BlendTree に統合 |
| MA Parameters | パラメーター設定と名前空間管理 | パラメーター衝突回避。SHA256 ハッシュで一意名を生成。プレファブ間の名前分離 |
| MA Sync Parameter Sequence | パラメーター同期順序の制御 | ネットワーク同期の順序保証 |

### リアクティブオブジェクト

条件（メニューアイテムのパラメーター、親 GameObject の active 状態）に基づいてアニメーションを自動生成するシステム。

| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| MA Object Toggle | GameObject の active/inactive を条件で切替 | 衣装パーツのオン/オフ。activeSelf プロキシパラメーターを生成 |
| MA Shape Changer | ブレンドシェイプの値を条件で変更・削除 | 衣装着用時の体メッシュ貫通防止シェイプ駆動 |
| MA Material Swap | マテリアルを From/To ペアで条件付き差替 (1.13+) | カラーバリエーション切替。QuickSwapMode で同ディレクトリ/兄弟ディレクトリの自動マッチあり |
| MA Material Setter | マテリアルプロパティを条件で変更 | 色やパラメーターの条件付き変更 |
| MA Mesh Cutter | メッシュ頂点を条件で非表示 (1.13+) | NaNimation 技術でボーンウェイト操作。子に頂点フィルタコンポーネントを配置して範囲指定 |
| VertexFilterByAxis | 軸方向で頂点フィルタリング (1.13+) | Mesh Cutter の子に配置。center + axis で切断面を定義 |
| VertexFilterByBone | ボーンウェイトで頂点フィルタリング (1.13+) | Mesh Cutter の子に配置。指定ボーンのウェイト閾値で選別 |
| VertexFilterByMask | テクスチャマスクで頂点フィルタリング (1.13+) | Mesh Cutter の子に配置。白/黒で削除面を指定 |
| VertexFilterByShape | シェイプキーで頂点フィルタリング (1.13+) | Mesh Cutter の子に配置。シェイプキーの移動量閾値で選別 |

**NaNimation**: メッシュ複製なしで条件付き頂点非表示を実現する技術。NaN ボーンを追加し、非表示にしたい頂点のボーンウェイトをそこへ割り当て、ボーンスケールのアニメーションで表示/非表示を制御する。

### メニュー

| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| MA Menu Item | VRChat Expression メニュー項目を階層内に分散配置 | プレファブ内にメニュー項目を同梱。ビルド時に MenuInstallHook が収集・組み立て |
| MA Menu Installer | メニューのインストール先を指定 | レガシー API。Menu Item の方が推奨 |
| MA Menu Install Target | メニューの挿入位置を指定 | 既存メニュー構造への差し込み |
| MA Menu Group | メニュー項目のグループ化 | サブメニュー構造の構築 |

### メッシュ・描画設定

| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| MA Mesh Settings | プローブアンカー・バウンズの設定 | 描画の一貫性確保。衣装プレファブに付与 |
| MA Blendshape Sync | ブレンドシェイプ状態の同期 | 衣装と本体メッシュのシェイプキー連動 |
| MA Remove Vertex Color | 頂点カラーの除去 | シェーダー互換性問題の回避 |
| MA Visible Head Accessory | 一人称で頭アクセサリを表示 | VRChat のヘッドボーン非表示を回避 |

### その他

| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| MA Replace Object | ビルド時にオブジェクトを差し替え | 条件付きオブジェクト置換 |
| MA Convert Constraints | Unity Constraints を VRChat 互換に変換 | VRChat の制約システムへの移行 |
| MA PhysBone Blocker | PhysBone の影響を遮断 | 衣装のボーンが PhysBone に巻き込まれるのを防止 |
| MA Global Collider | PhysBone コライダーを標準スロットに差替 (1.13+) | Head/Hand 等の標準コライダーをカスタム形状で上書き。radius/height/position/rotation で形状定義 |
| MA Platform Filter | プラットフォーム別のコンポーネント有効/無効 (1.13+) | Quest/PC で異なる構成。m_excludePlatform + m_platform で制御 |
| MA MMD Layer Control | MMD ワールド互換レイヤー制御 | MMD ワールドでの互換性確保 |
| MA Move Independently | 独立した移動 | 特定オブジェクトの独立トランスフォーム制御 |
| MA VRChat Settings | VRChat 固有設定の上書き | アバター全体の VRChat パラメーター |
| MA Rename VRChat Collision Tags | PhysBone/Contact コリジョンタグのリネーム | プレファブ間のタグ衝突回避 |
| MA World Scale Object | ワールドスケール制約の適用 | ワールドスケール基準のオブジェクト |

## 操作パターン (L2)

### 衣装追加の基本パターン

1. 衣装プレファブをアバター直下にドラッグ&ドロップ
2. 衣装ルートに **MA Merge Armature** を配置、mergeTarget にアバターの Armature を指定
3. プレフィックス/サフィックスが自動補完される（衣装ボーン名から推測）
4. 必要に応じて **MA Mesh Settings** でバウンズ・アンカーを統一
5. ビルド時にボーン統合が自動実行される

### 衣装トグルの基本パターン

1. 衣装オブジェクトに **MA Object Toggle** を配置
2. 親に **MA Menu Item** (Toggle) を配置
3. ビルド時に自動的に: パラメーター生成 → アニメーション生成 → メニュー組み立て
4. 手動でアニメーターやパラメーターを作る必要がない

### 体メッシュ貫通防止パターン

1. 衣装プレファブに **MA Shape Changer** を配置
2. ターゲットにアバターの Body メッシュを指定
3. 衣装で隠れる部分のシュリンクシェイプキーを駆動
4. 衣装トグルの条件と連動させると、衣装オフ時は元に戻る

### アクセサリ追加パターン（単一ボーン）

1. アクセサリオブジェクトに **MA Bone Proxy** を配置
2. Target にアバターの対象ボーン（Head, Hand 等）を指定
3. Attachment Mode: 汎用プレファブなら "As child at root"、アバター専用なら "As child keep world pose"

### メニュー構築パターン

1. 各機能プレファブ内に **MA Menu Item** を配置
2. サブメニューが必要なら **MA Menu Group** で囲む
3. 配置先を指定するなら **MA Menu Install Target** を使用
4. ビルド時に MenuInstallHook が全 Menu Item を収集してメニューツリーを構築

### パラメーター衝突回避パターン

1. プレファブルートに **MA Parameters** を配置
2. "内部パラメーター" に指定したパラメーターは自動的に SHA256 ハッシュでリネーム
3. 同じプレファブを複数配置しても衝突しない

## SerializedProperty リファレンス (L3)

ソースバージョン: 1.12.5 (PF-TEST) + 1.16.2 (Shiratsume) 差分検証済み

### Script GUID テーブル

| コンポーネント | GUID | 備考 |
|---|---|---|
| ModularAvatarMergeArmature | `2df373bf91cf30b4bbd495e11cb1a2ec` | |
| ModularAvatarBoneProxy | `42581d8044b64899834d3d515ab3a144` | |
| ModularAvatarMergeAnimator | `1bb122659f724ebf85fe095ac02dc339` | |
| ModularAvatarMergeBlendTree | `229dd561ca024a6588e388160921a70f` | |
| ModularAvatarParameters | `71a96d4ea0c344f39e277d82035bf9bd` | |
| ModularAvatarMenuItem | `3b29d45007c5493d926d2cd45a489529` | |
| ModularAvatarBlendshapeSync | `6fd7cab7d93b403280f2f9da978d8a4f` | |
| ModularAvatarMeshSettings | `560fdafd46c74b2db6422fdf0e7f2363` | |
| ModularAvatarVisibleHeadAccessory | `33dac8cfeaeb4c399ddd90597f849f70` | マーカーコンポーネント (フィールドなし) |
| ModularAvatarWorldFixedObject | `0e2d9f1d69e34b92a96e6cc162770fad` | マーカーコンポーネント (フィールドなし) |
| ModularAvatarWorldScaleObject | `e113c01563a14226b5e863befe6fe769` | マーカーコンポーネント (フィールドなし) |
| ModularAvatarConvertConstraints | `e362b3df8a3d478c82bf5ffe18f622e6` | マーカーコンポーネント (フィールドなし) |
| ModularAvatarPBBlocker | `a5bf908a199a4648845ebe2fd3b5a4bd` | マーカーコンポーネント (フィールドなし) |
| ModularAvatarReplaceObject | `7e949680c0864ee7b441d9b2c93b890b` | |
| ModularAvatarVRChatSettings | `89c938d7d8a741df99f2eda501b3a6fe` | |
| ModularAvatarMenuInstallTarget | `1fad1419b52a42ae89b0df52eb861e47` | internal class |
| ModularAvatarSyncParameterSequence | `934543afe4744213b5621aa13a67e3b4` | |
| ModularAvatarMMDLayerControl | `d1d979d3cedd4ddd969f414e2ea04fb8` | StateMachineBehaviour (非 GameObject) |
| MAMoveIndependently | `a8d5b07828ba4eefb9acc305478369d0` | MonoBehaviour 直接継承 |
| ModularAvatarRemoveVertexColor | `dc5f8bfae24244aeaedcd6c2bb7264f9` | |
| ModularAvatarScaleAdjuster | `09a660aa9d4e47d992adcac5a05dd808` | |
| ModularAvatarMenuGroup | `97e46a47dd8a425eb4ce9411defe313d` | |
| ModularAvatarMenuInstaller | `7ef83cb0c23d4d7c9d41021e544a1978` | |
| ModularAvatarShapeChanger | `2db441f589c3407bb6fb5f02ff8ab541` | |
| ModularAvatarObjectToggle | `a162bb8ec7e24a5abcf457887f1df3fa` | |
| ModularAvatarMaterialSetter | `0adf335711644e34b6c635e94ae61fa7` | |
| ModularAvatarMaterialSwap | `b259b73280ead4e4fbbdafc5e29175d1` | 1.13+ |
| ModularAvatarMeshCutter | `762726b8618cac7419e39bdc2b572b3d` | 1.13+ |
| ModularAvatarGlobalCollider | `49bb23f95a7baca4186efa68bc5891b6` | 1.13+ |
| ModularAvatarPlatformFilter | `8c8a67d5c01849629fa90c3b2eded93f` | 1.13+ |
| ModularAvatarRenameVRChatCollisionTags | `04802bf95b218724a9f4b97003067857` | 1.13+ |
| VertexFilterByAxisComponent | `660848d04d7443b5b6fcfb627e6be5ea` | 1.13+ |
| VertexFilterByBoneComponent | `f8e2c9a1b3d44c6d9a7e5f2c1b8d3e4f` | 1.13+ |
| VertexFilterByMaskComponent | `96a7b00b1dae4a02b61b29bf02241063` | 1.13+ |
| VertexFilterByShapeComponent | `da7788c69fae9ff4abae088a0dc92c5b` | 1.13+ |

### 共通型

**AvatarObjectReference** (Serializable class — 多くのコンポーネントで使用):
- `referencePath`: string — アバタールートからの相対パス
- `targetObject`: GameObject — 直接参照 (internal)

**ReactiveComponent** : AvatarTagComponent (ShapeChanger, ObjectToggle, MaterialSetter の基底):
- `m_inverted`: bool — 条件を反転

### コンポーネント別フィールド

#### MA Merge Armature
| propertyPath | 型 | 説明 |
|---|---|---|
| `mergeTarget.referencePath` | string | アバター側のターゲットボーンパス |
| `mergeTarget.targetObject` | GameObject | ターゲットボーン直接参照 |
| `prefix` | string | ボーン名マッチング時に除去するプレフィックス |
| `suffix` | string | ボーン名マッチング時に除去するサフィックス |
| `legacyLocked` | bool | 旧 "locked" フィールド [FormerlySerializedAs] |
| `LockMode` | ArmatureLockMode | ロックモード (Legacy=0, NotLocked=1, BaseToMerge=2, BidirectionalExact=3) |
| `mangleNames` | bool | ボーン名の衝突回避リネーム |

#### MA Bone Proxy
| propertyPath | 型 | 説明 |
|---|---|---|
| `boneReference` | HumanBodyBones | ヒューマノイドボーン参照 (enum int) |
| `subPath` | string | ボーンからの相対パス |
| `attachmentMode` | BoneProxyAttachmentMode | Unset=0, AsChildAtRoot=1, AsChildKeepWorldPose=2, AsChildKeepRotation=3, AsChildKeepPosition=4 |

#### MA Merge Animator
| propertyPath | 型 | 説明 |
|---|---|---|
| `animator` | RuntimeAnimatorController | 統合するアニメーターコントローラ |
| `layerType` | VRCAvatarDescriptor.AnimLayerType | レイヤー種別 (デフォルト: FX) |
| `deleteAttachedAnimator` | bool | 元の Animator コンポーネントを削除 |
| `pathMode` | MergeAnimatorPathMode | Relative=0, Absolute=1 |
| `matchAvatarWriteDefaults` | bool | Write Defaults をアバターに合わせる |
| `relativePathRoot.referencePath` | string | 相対パスの基準 |
| `layerPriority` | int | レイヤー優先度 |
| `mergeAnimatorMode` | MergeAnimatorMode | Append=0, Replace=1 |

#### MA Merge Blend Tree
| propertyPath | 型 | 説明 |
|---|---|---|
| `BlendTree` | Object | ブレンドツリー [Obsolete] |
| `PathMode` | MergeAnimatorPathMode | Relative=0, Absolute=1 |
| `RelativePathRoot.referencePath` | string | 相対パスの基準 |

#### MA Parameters
| propertyPath | 型 | 説明 |
|---|---|---|
| `parameters` | List\<ParameterConfig\> | パラメーター設定リスト |
| `parameters.Array.data[n].nameOrPrefix` | string | パラメーター名またはプレフィックス |
| `parameters.Array.data[n].remapTo` | string | リマップ先の名前 |
| `parameters.Array.data[n].internalParameter` | bool | 内部パラメーター (SHA256 リネーム対象) |
| `parameters.Array.data[n].isPrefix` | bool | プレフィックスモード |
| `parameters.Array.data[n].syncType` | ParameterSyncType | NotSynced=0, Int=1, Float=2, Bool=3 |
| `parameters.Array.data[n].localOnly` | bool | ローカル専用 |
| `parameters.Array.data[n].defaultValue` | float | デフォルト値 |
| `parameters.Array.data[n].saved` | bool | 保存対象 |
| `parameters.Array.data[n].hasExplicitDefaultValue` | bool | 明示的デフォルト値あり |
| `parameters.Array.data[n].m_overrideAnimatorDefaults` | bool | アニメーターデフォルト上書き |

#### MA Menu Item
| propertyPath | 型 | 説明 |
|---|---|---|
| `Control` | VRCExpressionsMenu.Control | VRChat メニューコントロール定義 |
| `MenuSource` | SubmenuSource | MenuAsset=0, Children=1 |
| `menuSource_otherObjectChildren` | GameObject | 子メニューソースオブジェクト |
| `isSynced` | bool | ネットワーク同期 (デフォルト: true) |
| `isSaved` | bool | 保存対象 (デフォルト: true) |
| `isDefault` | bool | デフォルト値 |
| `automaticValue` | bool | 自動値割り当て |
| `label` | string | 表示ラベル [Multiline] |

#### MA Blendshape Sync
| propertyPath | 型 | 説明 |
|---|---|---|
| `Bindings` | List\<BlendshapeBinding\> | 同期バインディングリスト |
| `Bindings.Array.data[n].ReferenceMesh.referencePath` | string | 参照メッシュのパス |
| `Bindings.Array.data[n].Blendshape` | string | ソースブレンドシェイプ名 |
| `Bindings.Array.data[n].LocalBlendshape` | string | ローカルブレンドシェイプ名 |

#### MA Mesh Settings
| propertyPath | 型 | 説明 |
|---|---|---|
| `InheritProbeAnchor` | InheritMode | Inherit=0, Set=1, DontSet=2, SetOrInherit=3 |
| `ProbeAnchor.referencePath` | string | プローブアンカーのパス |
| `InheritBounds` | InheritMode | バウンズ継承モード |
| `RootBone.referencePath` | string | ルートボーンのパス |
| `Bounds` | Bounds | バウンズ設定 (center/size) |

#### MA Shape Changer
| propertyPath | 型 | 説明 |
|---|---|---|
| `m_inverted` | bool | 条件反転 (ReactiveComponent 継承) |
| `m_shapes` | List\<ChangedShape\> | [FormerlySerializedAs("Shapes")] |
| `m_shapes.Array.data[n].Object.referencePath` | string | ターゲットメッシュのパス |
| `m_shapes.Array.data[n].ShapeName` | string | ブレンドシェイプ名 |
| `m_shapes.Array.data[n].ChangeType` | ShapeChangeType | Delete=0, Set=1 |
| `m_threshold` | float | 頂点移動量の閾値 (デフォルト: 0.01) **1.16.2 で追加** |
| `m_shapes.Array.data[n].Value` | float | 設定値 |

#### MA Object Toggle
| propertyPath | 型 | 説明 |
|---|---|---|
| `m_inverted` | bool | 条件反転 |
| `m_objects` | List\<ToggledObject\> | トグル対象リスト |
| `m_objects.Array.data[n].Object.referencePath` | string | ターゲットオブジェクトのパス |
| `m_objects.Array.data[n].Active` | bool | 条件成立時の active 状態 |

#### MA Material Setter
| propertyPath | 型 | 説明 |
|---|---|---|
| `m_inverted` | bool | 条件反転 |
| `m_objects` | List\<MaterialSwitchObject\> | マテリアル切替リスト |
| `m_objects.Array.data[n].Object.referencePath` | string | ターゲットレンダラーのパス |
| `m_objects.Array.data[n].Material` | Material | 設定するマテリアル |
| `m_objects.Array.data[n].MaterialIndex` | int | マテリアルスロット番号 |

#### MA Scale Adjuster
| propertyPath | 型 | 説明 |
|---|---|---|
| `m_Scale` | Vector3 | スケール値 (デフォルト: 1,1,1) |
| `legacyScaleProxy` | Transform | [FormerlySerializedAs("scaleProxy")] |

#### MA Replace Object
| propertyPath | 型 | 説明 |
|---|---|---|
| `targetObject.referencePath` | string | 差替対象のパス |

#### MA Menu Installer
| propertyPath | 型 | 説明 |
|---|---|---|
| `menuToAppend` | VRCExpressionsMenu | 追加するメニューアセット |
| `installTargetMenu` | VRCExpressionsMenu | インストール先メニュー |

#### MA Menu Install Target
| propertyPath | 型 | 説明 |
|---|---|---|
| `installer` | ModularAvatarMenuInstaller | 関連する MenuInstaller 参照 |

#### MA Menu Group
| propertyPath | 型 | 説明 |
|---|---|---|
| `targetObject` | GameObject | グループの子メニューソース |

#### MA Sync Parameter Sequence
| propertyPath | 型 | 説明 |
|---|---|---|
| `PrimaryPlatform` | Platform | PC=0, Android=1, iOS=2 |
| `Parameters` | VRCExpressionParameters | パラメーターアセット参照 |

#### MA VRChat Settings
| propertyPath | 型 | 説明 |
|---|---|---|
| `m_mmdWorldSupport` | bool | MMD ワールドサポート (デフォルト: true) |

#### MA MMD Layer Control (StateMachineBehaviour)
| propertyPath | 型 | 説明 |
|---|---|---|
| `m_DisableInMMDMode` | bool | MMD モード時に無効化 |

#### MA Move Independently
| propertyPath | 型 | 説明 |
|---|---|---|
| `m_groupedBones` | GameObject[] | グループ化されたボーン |

#### MA Remove Vertex Color
| propertyPath | 型 | 説明 |
|---|---|---|
| `Mode` | RemoveMode | Remove=0, DontRemove=1 |

#### MA Material Swap (1.13+)
| propertyPath | 型 | 説明 |
|---|---|---|
| `m_inverted` | bool | 条件反転 |
| `m_root.referencePath` | string | ルートオブジェクトのパス |
| `m_swaps` | List\<MatSwap\> | マテリアル差替ペアリスト |
| `m_swaps.Array.data[n].From` | Material | 差替元マテリアル |
| `m_swaps.Array.data[n].To` | Material | 差替先マテリアル |
| `m_quickSwapMode` | QuickSwapMode | None=0, SameDirectory=1, SiblingDirectory=2 |

#### MA Mesh Cutter (1.13+)
| propertyPath | 型 | 説明 |
|---|---|---|
| `m_inverted` | bool | 条件反転 |
| `m_object.referencePath` | string | ターゲットメッシュのパス |
| `m_multiMode` | MeshCutterMultiMode | VertexUnion=0, VertexIntersection=1 |

子に VertexFilter 系コンポーネントを配置して切断範囲を指定する。

#### VertexFilterByAxisComponent (1.13+)
| propertyPath | 型 | 説明 |
|---|---|---|
| `m_center` | Vector3 | 切断面の中心 |
| `m_axis` | Vector3 | 切断方向 (デフォルト: Vector3.left) |

#### VertexFilterByBoneComponent (1.13+)
| propertyPath | 型 | 説明 |
|---|---|---|
| `m_bone.referencePath` | string | フィルタ対象ボーンのパス |
| `m_threshold` | float | ウェイト閾値 (0-1, デフォルト: 0.01) |

#### VertexFilterByMaskComponent (1.13+)
| propertyPath | 型 | 説明 |
|---|---|---|
| `m_materialIndex` | int | マテリアルスロット番号 |
| `m_maskTexture` | Texture2D | マスクテクスチャ |
| `m_deleteMode` | ByMaskMode | DeleteBlack=0, DeleteWhite=1 |

#### VertexFilterByShapeComponent (1.13+)
| propertyPath | 型 | 説明 |
|---|---|---|
| `m_threshold` | float | 移動量閾値 (デフォルト: 0.001) |
| `m_shapes` | string[] | フィルタ対象のシェイプキー名 |

#### MA Global Collider (1.13+)
| propertyPath | 型 | 説明 |
|---|---|---|
| `m_manualRemap` | bool | 手動リマップ |
| `m_colliderToHijack` | GlobalCollider | Head, Torso, HandLeft/Right, FingerIndex/Middle/Ring/LittleLeft/Right, FootLeft/Right, None |
| `m_lowPriority` | bool | 低優先度 |
| `m_rootTransform.referencePath` | string | ルートトランスフォームのパス |
| `m_copyHijackedShape` | bool | ハイジャック先の形状をコピー |
| `m_visualizeGizmo` | bool | ギズモ表示 (デフォルト: true) |
| `m_radius` | float | コライダー半径 (デフォルト: 0.05) |
| `m_height` | float | コライダー高さ (デフォルト: 0.2) |
| `m_position` | Vector3 | コライダー位置 |
| `m_rotation` | Quaternion | コライダー回転 |

#### MA Platform Filter (1.13+)
| propertyPath | 型 | 説明 |
|---|---|---|
| `m_excludePlatform` | bool | プラットフォームを除外 (デフォルト: true) |
| `m_platform` | string | 対象プラットフォーム |

#### MA Rename VRChat Collision Tags (1.13+)
| propertyPath | 型 | 説明 |
|---|---|---|
| `configs` | List\<RenameCollisionTagConfig\> | リネーム設定リスト |
| `configs.Array.data[n].name` | string | 元のタグ名 |
| `configs.Array.data[n].autoRename` | bool | 自動リネーム |
| `configs.Array.data[n].renameTo` | string | リネーム先 |

### 設計上の注意点

- **AvatarObjectReference** は `referencePath` (文字列パス) と `targetObject` (直接参照) の二重参照構造。プレファブ内では `referencePath` が主、ビルド時に `targetObject` へ解決される。
- **FormerlySerializedAs** が 3 箇所: `legacyLocked`←"locked", `legacyScaleProxy`←"scaleProxy", `m_shapes`←"Shapes"。旧バージョンからのマイグレーション時に注意。
- マーカーコンポーネント (フィールドなし) が 5 種: VisibleHeadAccessory, WorldFixedObject, WorldScaleObject, ConvertConstraints, PBBlocker。存在のみで機能する。
- **MMDLayerControl** は StateMachineBehaviour 継承で、GameObject ではなく Animator State Machine に付与される唯一のコンポーネント。

### 1.12.5 → 1.16.2 変更サマリー

- **新コンポーネント 9 種**: MaterialSwap, MeshCutter, 4 VertexFilter, GlobalCollider, PlatformFilter, RenameVRChatCollisionTags
- **フィールド追加 1 件**: ShapeChanger に `m_threshold` (float)
- **構造変更**: MenuItem が PortableMenuControl でプラットフォーム抽象化。Control が nullable に。非 VRCSDK 環境でも動作可能に。
- **非推奨**: SyncParameterSequence のフィールドが `[Obsolete]` に（1.16 以降は暗黙管理）
- **削除コンポーネント**: なし

## 実運用で学んだこと

(なし — Phase 3-4 の検証で追記する)
