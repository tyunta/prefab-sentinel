---
tool: lilycal-inventory
version_tested: "1.5.2"
last_updated: 2026-03-26
confidence: medium
---

# lilycalInventory

## 概要 (L1)

非破壊アバターインベントリツール。NDMF プラグインとして動作し、ビルド時にコンポーネント設定からアニメーション・メニュー・パラメーターを自動生成する。最終アバターにはランタイムコンポーネントが残らない（`IEditorOnly` 実装）。

**解決する問題**: VRChat アバターのオブジェクトトグル・衣装切替・BlendShape 操作・マテリアル変更を、AnimatorController やメニューを手作業で組む必要なく、コンポーネントを付けるだけで実現する。

**NDMF との関係**: Modular Avatar より前の Resolving フェーズでコンポーネントを探索・変換し、Transforming フェーズでアニメーション生成・メニュー構築を行う。マテリアル操作は MA・TexTransTool の後に実行される。Optimizing フェーズでマテリアル不要プロパティ除去を行う。

**Modular Avatar 連携**: MA がインストール済みの場合、生成したメニューを MA Menu Group 配下に配置できる。`AsMAMergeAnimator` コンポーネントで生成 AnimatorController を MA Merge Animator 経由で統合可能。`parentOverrideMA` フィールドで MA MenuItem を直接参照してメニュー階層を制御できる。

**作者**: lilxyzw (lilLab)。VPM リポジトリ `https://lilxyzw.github.io/vpm-repos/vpm.json` から配布。MIT ライセンス。

**最新バージョン**: 1.5.2 (2025-09-26)。

## コンポーネント一覧 (L1 → L2)

### メニュー生成コンポーネント（トグル・切替）

| コンポーネント名 | 用途 | パラメーター型 | 典型的な使用場面 |
|---|---|---|---|
| LI ItemToggler | オブジェクトのオン/オフ切替 | Bool | 衣装パーツ・アクセサリの表示切替。自身は切替対象に含まない（自身も含めたい場合は Prop を使う） |
| LI CostumeChanger | 排他的な衣装切替 | Int | 複数衣装の排他選択。各衣装に個別のトグル対象・BlendShape・マテリアル操作を設定可能 |
| LI SmoothChanger | 無段階パラメーター操作 | Float | BlendShape やマテリアルプロパティの連続変化。RadialPuppet メニューを自動生成 |
| LI Prop | 自身をオン/オフするトグル | Bool | ビルド時に ItemToggler に変換される。自身の GameObject を切替対象に自動追加する簡易版 |
| LI AutoDresser | 衣装オブジェクトへの簡易着せ替え | (変換後 Int) | ビルド時に CostumeChanger に変換される。衣装オブジェクトに付けるだけで着せ替えメニューに登録 |
| LI Preset | 複数コンポーネントの一括制御 | Bool (non-Synced) | 複数の ItemToggler/CostumeChanger の状態を一括切替。パラメーターメモリを消費しない（non-Synced） |

### 設定・補助コンポーネント

| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| LI AutoDresserSettings | AutoDresser 変換時の CostumeChanger 設定 | アバタールートに1つだけ配置。AutoDresser → CostumeChanger 変換時の isSave/isLocalOnly を制御 |
| LI MenuFolder | メニューのフォルダ構造 | サブメニュー構造の構築。メニュー名に `/` を含めると自動でフォルダが生成される |
| LI AsMAMergeAnimator | 生成 AnimatorController を MA 経由で統合 | MA インストール時のみ有効。レイヤー優先度を設定可能 |

### マテリアル・メッシュ操作コンポーネント

| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| LI MaterialModifier | マテリアル設定値の一括統一 | 衣装とアバターのライティング設定差異を解消。ソースマテリアルの指定プロパティを全マテリアルにコピー |
| LI MaterialOptimizer | マテリアル不要プロパティ除去 | ビルド時に使用していないシェーダープロパティを除去して最適化。マーカーコンポーネント（フィールドなし） |
| LI AutoFixMeshSettings | メッシュ描画設定の自動統一 | Bounds・AnchorOverride・ShadowCasting 等を全 Renderer に統一 |

### ユーティリティコンポーネント

| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| LI Comment | Prefab/GameObject へのコメント | 多言語対応のメモ。Markdown 表示対応。ビルド時に自動削除される |

## 操作パターン (L2)

### オブジェクトトグルの基本パターン

1. トグルしたいオブジェクトに **LI Prop** を配置
2. Prop がビルド時に **ItemToggler** に変換され、自身の GameObject が切替対象に自動追加される
3. メニュー名・アイコンを設定
4. ビルド時に Bool パラメーター → AnimationClip → メニュー項目が自動生成される

**応用**: 複数オブジェクトを同時に切替える場合は **LI ItemToggler** を使い、`parameter.objects` 配列に対象を列挙する。

### 衣装切替パターン（AutoDresser）

1. 各衣装オブジェクトに **LI AutoDresser** を配置
2. オプション: アバタールートに **LI AutoDresserSettings** を配置して保存・ローカル設定を制御
3. ビルド時に全 AutoDresser が1つの **CostumeChanger** に変換される
4. Int パラメーター（ビット圧縮対応）で排他切替メニューが自動生成される

**注意**: CostumeChanger の `costumes` 配列に `autoDresser` フィールドがある場合、その AutoDresser の parameter 設定がマージされ、AutoDresser 自身のオブジェクトトグルも自動追加される。

### 衣装切替パターン（CostumeChanger 直接）

1. 任意の GameObject に **LI CostumeChanger** を配置
2. `costumes` 配列に各衣装を登録
3. 各衣装の `parametersPerMenu` でオブジェクトトグル・BlendShape・マテリアル置換を設定
4. デフォルト衣装のインデックスを `defaultValue` で指定
5. ビルド時に Int パラメーター（isLocalOnly 時はビット圧縮）でメニュー生成

### 明るさ・BlendShape 連続調整パターン

1. 任意の GameObject に **LI SmoothChanger** を配置
2. `frames` 配列に各フレーム（0%〜100% の値）を登録
3. 各フレームの `parametersPerMenu` でマテリアルプロパティ値・BlendShape 値を設定
4. ビルド時に Float パラメーター → BlendTree → RadialPuppet メニューが自動生成
5. フレーム間は自動的に線形補間される

### マテリアル設定統一パターン

1. アバタールート配下に **LI MaterialModifier** を配置
2. `referenceMaterial` にコピー元マテリアルを指定
3. `properties` に統一したいプロパティ名を列挙
4. `ignoreMaterials` に除外マテリアルを設定
5. ビルド時に MA・TTT の処理後にマテリアルクローン→プロパティコピーが実行される

### プリセット一括切替パターン

1. 各衣装に ItemToggler / CostumeChanger / SmoothChanger を配置済みの状態で
2. **LI Preset** を配置し、`presetItems` に対象コンポーネントと値を列挙
3. ビルド時に non-Synced Bool パラメーター → ParameterDriver で各コンポーネントの値を一括変更
4. プリセット数に関わらず AnimatorController に追加されるレイヤーは1つ

## SerializedProperty リファレンス (L3)

ソースバージョン: 1.5.2 (Shiratsume)
検証方法: Runtime/*.cs ソースコード読み + .meta ファイルの GUID 抽出（inspect 実測なし → confidence: medium）

### Script GUID テーブル

| コンポーネント | GUID | 備考 |
|---|---|---|
| ItemToggler | `5128bb4e814dacb42ad2a73e67b48051` | |
| CostumeChanger | `ac2ec1d226fb0294fb92399092948f79` | |
| SmoothChanger | `291572d63b1b08e4fa25277a9f74f5e9` | |
| Prop | `087baea558988624b9b705d9d3413b8d` | ビルド時に ItemToggler に変換 |
| AutoDresser | `26542ee29b57fbe4282b6b1d83d23350` | ビルド時に CostumeChanger に変換 |
| AutoDresserSettings | `044a88a254a3a0d46b2ba90996506ade` | |
| Preset | `b4db304b08de704498909efb6b7d8ea2` | |
| MenuFolder | `7c10be65beb28d84e8d1255a66d5b5a7` | |
| AsMAMergeAnimator | `7594b4ab06ff1204fbb55194713ec73a` | MA 連携用 |
| MaterialModifier | `133158e040b575f489ec70d00bae871e` | |
| MaterialOptimizer | `dee6276d2c062244b901e025a56938d4` | マーカーコンポーネント (フィールドなし) |
| AutoFixMeshSettings | `ea5d6f6438f2f3b489b0ad17ba721605` | |
| Comment | `3e03dc963a874ad43bb69dcfbc3d7445` | ビルド時に最初に削除 |

**注意**: GUID は MonoScript (.cs ファイル) に対応する。1ファイル1コンポーネントの構成のため、コンポーネント型と一意に対応する。ParametersPerMenu 等の `[Serializable]` クラスは ParametersPerMenu.cs (GUID: `6b26b225f51dc914a9155ad483b2e82a`) に定義されているが、MonoBehaviour ではないためコンポーネント GUID としては使用されない。

### 共通基底クラス

**AvatarTagComponent** (全コンポーネントの基底):
- `MonoBehaviour` 継承 + `IEditorOnly` 実装（VRCSDK3 環境時）
- シリアライズフィールドなし

**MenuBaseComponent** : AvatarTagComponent (メニュー生成コンポーネントの基底):

| propertyPath | 型 | 説明 |
|---|---|---|
| `menuName` | string | メニュー表示名。`/` を含むと自動でフォルダ階層を生成 |
| `parentOverride` | MenuFolder | 親フォルダの上書き指定 |
| `icon` | Texture2D | メニューアイコン |
| `parentOverrideMA` | Object / ModularAvatarMenuItem | MA MenuItem による親メニュー上書き (MA+VRCSDK3A 環境時) |

**MenuBaseDisallowMultipleComponent** : MenuBaseComponent
- `[DisallowMultipleComponent]` 付き。ItemToggler, CostumeChanger, SmoothChanger が継承。

### 共通シリアライズクラス

**ParametersPerMenu** (各メニュー操作コンポーネントの設定):

| propertyPath | 型 | 説明 |
|---|---|---|
| `objects` | ObjectToggler[] | オブジェクトトグル設定 |
| `objects.Array.data[n].obj` | GameObject | トグル対象オブジェクト |
| `objects.Array.data[n].value` | bool | 条件成立時の active 状態 |
| `blendShapeModifiers` | BlendShapeModifier[] | BlendShape 変更設定 |
| `blendShapeModifiers.Array.data[n].skinnedMeshRenderer` | SkinnedMeshRenderer | 対象メッシュ |
| `blendShapeModifiers.Array.data[n].blendShapeNameValues` | BlendShapeNameValue[] | シェイプキー名と値のペア配列 |
| `blendShapeModifiers.Array.data[n].blendShapeNameValues.Array.data[m].name` | string | BlendShape 名 |
| `blendShapeModifiers.Array.data[n].blendShapeNameValues.Array.data[m].value` | float | BlendShape 値 |
| `materialReplacers` | MaterialReplacer[] | マテリアル置換設定 |
| `materialReplacers.Array.data[n].renderer` | Renderer | 対象レンダラー |
| `materialReplacers.Array.data[n].replaceTo` | Material[] | 置換先マテリアル配列 |
| `materialPropertyModifiers` | MaterialPropertyModifier[] | マテリアルプロパティ変更設定 |
| `materialPropertyModifiers.Array.data[n].renderers` | Renderer[] | 対象レンダラー配列 |
| `materialPropertyModifiers.Array.data[n].floatModifiers` | FloatModifier[] | Float プロパティ変更 |
| `materialPropertyModifiers.Array.data[n].floatModifiers.Array.data[m].propertyName` | string | シェーダープロパティ名 |
| `materialPropertyModifiers.Array.data[n].floatModifiers.Array.data[m].value` | float | 設定値 |
| `materialPropertyModifiers.Array.data[n].vectorModifiers` | VectorModifier[] | Vector プロパティ変更 |
| `materialPropertyModifiers.Array.data[n].vectorModifiers.Array.data[m].propertyName` | string | シェーダープロパティ名 |
| `materialPropertyModifiers.Array.data[n].vectorModifiers.Array.data[m].value` | Vector4 | 設定値 |
| `materialPropertyModifiers.Array.data[n].vectorModifiers.Array.data[m].disableX` | bool | X 成分を無効化 |
| `materialPropertyModifiers.Array.data[n].vectorModifiers.Array.data[m].disableY` | bool | Y 成分を無効化 |
| `materialPropertyModifiers.Array.data[n].vectorModifiers.Array.data[m].disableZ` | bool | Z 成分を無効化 |
| `materialPropertyModifiers.Array.data[n].vectorModifiers.Array.data[m].disableW` | bool | W 成分を無効化 |
| `clips` | AnimationClip[] | 追加 AnimationClip |

### コンポーネント別フィールド

#### LI ItemToggler

| propertyPath | 型 | 説明 |
|---|---|---|
| `isSave` | bool | パラメーター保存対象 (デフォルト: true) |
| `isLocalOnly` | bool | ローカル専用 (デフォルト: false) |
| `autoFixDuplicate` | bool | パラメーター名重複自動回避 (デフォルト: true) |
| `parameter` | ParametersPerMenu | アニメーション設定 (上記共通クラス参照) |
| `defaultValue` | bool | デフォルト値 (デフォルト: false) |

ParametersPerMenu のフィールドは `parameter.objects.Array.data[n].obj` のようにプレフィックス `parameter.` で参照される。

#### LI CostumeChanger

| propertyPath | 型 | 説明 |
|---|---|---|
| `isSave` | bool | パラメーター保存対象 (デフォルト: true) |
| `isLocalOnly` | bool | ローカル専用 (デフォルト: false)。true 時は Int をビット圧縮して Bool で同期 |
| `autoFixDuplicate` | bool | パラメーター名重複自動回避 (デフォルト: true) |
| `costumes` | Costume[] | 衣装設定配列 |
| `costumes.Array.data[n].menuName` | string | 衣装メニュー名 |
| `costumes.Array.data[n].icon` | Texture2D | 衣装アイコン |
| `costumes.Array.data[n].parentOverride` | MenuFolder | 衣装の親フォルダ上書き |
| `costumes.Array.data[n].parentOverrideMA` | Object / ModularAvatarMenuItem | MA による親メニュー上書き |
| `costumes.Array.data[n].autoDresser` | AutoDresser | 連携する AutoDresser 参照 |
| `costumes.Array.data[n].parametersPerMenu` | ParametersPerMenu | 衣装ごとのアニメーション設定 (共通クラス参照) |
| `defaultValue` | int | デフォルト衣装インデックス (デフォルト: 0) |

衣装内の ParametersPerMenu は `costumes.Array.data[n].parametersPerMenu.objects.Array.data[m].obj` のようにネストされる。

#### LI SmoothChanger

| propertyPath | 型 | 説明 |
|---|---|---|
| `isSave` | bool | パラメーター保存対象 (デフォルト: true) |
| `isLocalOnly` | bool | ローカル専用 (デフォルト: false) |
| `autoFixDuplicate` | bool | パラメーター名重複自動回避 (デフォルト: true) |
| `defaultFrameValue` | float | パペット初期値 (パーセンテージ) |
| `frames` | Frame[] | フレーム設定配列 |
| `frames.Array.data[n].frameValue` | float | フレーム値 (パーセンテージ) |
| `frames.Array.data[n].parametersPerMenu` | ParametersPerMenu | フレームごとのアニメーション設定 (共通クラス参照) |

フレーム内の ParametersPerMenu は `frames.Array.data[n].parametersPerMenu.objects.Array.data[m].obj` のようにネストされる。

#### LI Prop

| propertyPath | 型 | 説明 |
|---|---|---|
| `isSave` | bool | パラメーター保存対象 (デフォルト: true) |
| `isLocalOnly` | bool | ローカル専用 (デフォルト: false) |
| `autoFixDuplicate` | bool | パラメーター名重複自動回避 (デフォルト: true) |
| `parameter` | ParametersPerMenu | 連動パラメーター設定 (共通クラス参照) |

`parameter.` プレフィックスで ParametersPerMenu フィールドにアクセス。ビルド時に自身の GameObject を `parameter.objects` に自動追加して ItemToggler に変換される。

#### LI AutoDresser

| propertyPath | 型 | 説明 |
|---|---|---|
| `parameter` | ParametersPerMenu | 連動パラメーター設定 (共通クラス参照) |

`parameter.` プレフィックスでアクセス。ビルド時に CostumeChanger の1衣装に変換される。

#### LI AutoDresserSettings

| propertyPath | 型 | 説明 |
|---|---|---|
| `isSave` | bool | パラメーター保存対象 (デフォルト: true) |
| `isLocalOnly` | bool | ローカル専用 (デフォルト: false) |
| `autoFixDuplicate` | bool | パラメーター名重複自動回避 (デフォルト: true) |

`[DisallowMultipleComponent]`。アバターに1つのみ。AutoDresser → CostumeChanger 変換時にこの設定値が使われる。

#### LI Preset

| propertyPath | 型 | 説明 |
|---|---|---|
| `autoFixDuplicate` | bool | パラメーター名重複自動回避 (デフォルト: true) |
| `presetItems` | PresetItem[] | 制御対象リスト |
| `presetItems.Array.data[n].obj` | MenuBaseComponent | 対象コンポーネント (ItemToggler, CostumeChanger 等) |
| `presetItems.Array.data[n].value` | float | 設定値 (Bool → 0/1, Int → インデックス, Float → 0-1) |

#### LI MenuFolder

MenuBaseComponent のフィールドのみ (menuName, parentOverride, icon, parentOverrideMA)。追加フィールドなし。

#### LI AsMAMergeAnimator

| propertyPath | 型 | 説明 |
|---|---|---|
| `layerPriority` | int | MA Merge Animator のレイヤー優先度 (デフォルト: 0) |

#### LI MaterialModifier

| propertyPath | 型 | 説明 |
|---|---|---|
| `referenceMaterial` | Material | コピー元マテリアル |
| `ignoreMaterials` | Material[] | 除外マテリアル配列 |
| `properties` | string[] | コピー対象プロパティ名配列 |

#### LI AutoFixMeshSettings

| propertyPath | 型 | 説明 |
|---|---|---|
| `ignoreRenderers` | Renderer[] | 除外レンダラー配列 |
| `meshSettings` | MeshSettings | メッシュ設定 (インライン Serializable) |
| `meshSettings.updateWhenOffscreen` | bool | 画面外でも更新 (デフォルト: false) |
| `meshSettings.rootBone` | Transform | ルートボーン |
| `meshSettings.autoCalculateBounds` | bool | Bounds 自動計算 (デフォルト: true) |
| `meshSettings.bounds` | Bounds | Bounds 設定 (autoCalculateBounds=false 時のみ有効) |
| `meshSettings.castShadows` | ShadowCastingMode | シャドウ設定 (デフォルト: On) |
| `meshSettings.receiveShadows` | bool | シャドウ受信 (デフォルト: true) |
| `meshSettings.lightProbes` | LightProbeUsage | LightProbe 設定 (デフォルト: BlendProbes) |
| `meshSettings.reflectionProbes` | ReflectionProbeUsage | ReflectionProbe 設定 (デフォルト: BlendProbes) |
| `meshSettings.anchorOverride` | Transform | アンカーオーバーライド |
| `meshSettings.motionVectors` | MotionVectorGenerationMode | モーションベクター (デフォルト: Object) |
| `meshSettings.dynamicOcclusion` | bool | 動的オクルージョン (デフォルト: true) |
| `meshSettings.skinnedMotionVectors` | bool | スキンドモーションベクター (デフォルト: true) |

#### LI Comment

| propertyPath | 型 | 説明 |
|---|---|---|
| `messageType` | MessageType | None=0, Info=1, Warning=2, Error=3, Markdown=4 |
| `comments` | LanguageAndText[] | 多言語コメント配列 |
| `comments.Array.data[n].langcode` | string | 言語コード |
| `comments.Array.data[n].text` | string | コメント本文 [TextArea] |

### 設計上の注意点

- **ビルド時変換チェーン**: `AutoDresser` → `CostumeChanger`、`Prop` → `ItemToggler`。Prefab 上では変換前のコンポーネントが存在し、ビルド時に変換される。inspect で見えるのは変換前の型。
- **ParametersPerMenu の共有構造**: ItemToggler, CostumeChanger (各衣装), SmoothChanger (各フレーム), Prop, AutoDresser が全て同じ `ParametersPerMenu` 構造を持つ。propertyPath のネスト深度が異なるだけ。
- **パラメーター名**: メニュー名がそのままパラメーター名になる。`autoFixDuplicate=true` で重複時に自動リネームされる。
- **Int パラメーター圧縮**: CostumeChanger/AutoDresser で `isLocalOnly=true` の場合、Int パラメーターをビット分割して Bool で同期する。同期コスト削減のための最適化。
- **DirectBlendTree 最適化**: `ToolSettings.instance.useDirectBlendTree` で有効化（デフォルト有効）。複数の SmoothChanger を1つの DirectBlendTree にまとめてレイヤー数を削減する。
- **全フィールドが `internal`**: Runtime クラスのフィールドは全て `[SerializeField] internal` で宣言。`[assembly:InternalsVisibleTo]` で Editor アセンブリに公開。外部からの直接アクセスは不可。
- **MA MenuItem 参照**: `parentOverrideMA` フィールドは `#if LIL_MODULAR_AVATAR && LIL_VRCSDK3A` でコンパイル分岐。MA 未インストール環境では `UnityEngine.Object` 型として保持される。
- **マーカーコンポーネント**: MaterialOptimizer はフィールドなし。存在のみでビルド時にマテリアル最適化が有効化される。

### 同梱プレファブ

| プレファブ名 | 用途 |
|---|---|
| LightChanger.prefab | SmoothChanger によるライティング調整 |
| LocalGimmicks.prefab | ローカル専用ギミック集 |
| [General] Optimize.prefab | MaterialOptimizer 設定済み |
| [lilToon] Distance Fade.prefab | lilToon ディスタンスフェード設定 |
| [lilToon] Fix Lighitng.prefab | lilToon ライティング統一設定 |

## 実運用で学んだこと

(なし -- Phase 3-4 の検証で追記する)
