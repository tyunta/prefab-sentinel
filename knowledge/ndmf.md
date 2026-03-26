---
tool: ndmf
version_tested: "1.11.0"
last_updated: 2026-03-26
confidence: medium
---

# Non-Destructive Modular Framework (NDMF)

## 概要 (L1)

非破壊ビルドパイプラインフレームワーク。アバターをビルド時に段階的に処理するためのプラグインアーキテクチャを提供する。Modular Avatar、Avatar Optimizer 等の主要プラグインの共通基盤。

**解決する問題**: 複数の非破壊ツールが独立にビルドパイプラインに介入すると、実行順序の衝突やコンテキスト共有の不整合が発生する。NDMF はフェーズベースの実行順序管理、トポロジカルソートによる制約解決、Extension Context によるプラグイン間状態共有を統一的に提供する。

**主な機能**:
- フェーズベースのビルドパイプライン (Resolving → Generating → Transforming → Optimizing + 拡張フェーズ)
- プラグインとパスの宣言的定義 (Fluent API)
- Extension Context によるプラグイン間状態共有
- プラットフォームフィルタリング (VRChat / Generic / Resonite)
- エディタプレビューシステム (IRenderFilter)
- 仮想アニメーターコントローラ (AnimatorServicesContext)
- エラーレポーティングウィンドウ

**プラットフォーム**: VRChat アバター (VRChat SDK 3.x) がデフォルトターゲット。1.8.0 からマルチプラットフォーム対応が実験的に追加。

**最新バージョン**: 1.11.0 (2026-02-06)。VPM リポジトリ `https://vpm.nadena.dev/vpm.json` から配布。

**依存関係**: `com.unity.modules.animation` のみ。VRChat SDK は任意依存 (`NDMF_VRCSDK3_AVATARS` define で分岐)。

## コンポーネント一覧 (L1→L2)

### ユーザー向けコンポーネント

NDMF 自体はビルドフレームワークであり、ユーザーが直接操作するコンポーネントは少ない。以下はエディタ上でアバタールートや設定を管理するためのもの。

| コンポーネント名 | 用途 | 備考 |
|---|---|---|
| NDMFAvatarRoot | アバタールートのマーカー | `[NDMFExperimental]`。VRCAvatarDescriptor が無い環境でアバタールートを示す。ビルド時に自動付与される |
| NDMFViewpoint | ビューポイント位置の指定 | マーカーコンポーネント (フィールドなし)。IPortableAvatarConfigTag |
| PortableBlendshapeVisemes | ポータブルリップシンク設定 | プラットフォーム非依存のビゼーム設定。TargetRenderer + Shape リスト |
| PortableDynamicBone | ポータブル揺れもの設定 | プラットフォーム非依存の DynamicBone 定義。テンプレート名でパラメーター推測 |
| PortableDynamicBoneCollider | ポータブル揺れものコライダー | Sphere / Capsule / Plane 形状 |

### 開発者向けインフラコンポーネント

プラグイン開発者やフレームワーク内部で使用されるもの。ユーザーが直接触ることはない。

| コンポーネント名 | 用途 | 備考 |
|---|---|---|
| GeneratedAssets | ビルド生成アセットのコンテナ | ScriptableObject。サブアセットの管理用 |
| SubAssetContainer | サブアセット格納ルート | ScriptableObject。GeneratedAssets の子 |
| AlreadyProcessedTag | 二重処理防止マーカー | Play モード時にアバターが処理済みかを追跡 |
| SelfDestructComponent | 一時オブジェクトの自動破棄 | KeepAlive が null なら次フレームで自己破壊 |
| ProxyTagComponent | プレビューシステムのプロキシタグ | プレビュー用レンダラーのクローン管理 |

## パイプライン構造 (L2)

### フェーズ定義

NDMF 1.11.0 では 7 つのフェーズが定義順に実行される。公式 API として公開されているのは Resolving / Generating / Transforming / Optimizing の 4 フェーズ + FirstChance / PlatformInit / PlatformFinish の 3 フェーズ。

| 実行順 | フェーズ | 用途 | 備考 |
|---|---|---|---|
| 1 | **FirstChance** | プラットフォーム初期化前の最優先処理 | EditorOnly 除去はまだ行われない |
| 2 | (InternalPrePlatformInit) | プラットフォーム設定の同期 | internal。SyncPlatformConfigPass が実行される |
| 3 | **PlatformInit** | プラットフォームバックエンド初期化 | EditorOnly 除去はまだ行われない |
| 4 | **Resolving** | 参照解決・初期処理 | MA: 文字列パス解決、アニメーターのクローン。NDMF 組込み: RemoveMissingScriptComponents, RemoveEditorOnlyPass |
| 5 | **Generating** | コンポーネント生成 | 後続プラグインが使うコンポーネントをここで生成する |
| 6 | **Transforming** | 本体変換 | MA の大半のパスがここで実行される |
| 7 | **Optimizing** | 最適化 | Avatar Optimizer 等の最適化パス |
| 8 | **PlatformFinish** | プラットフォーム固有クリーンアップ | パラメーター上限チェック等の検証 |

### 実行順序制御

同一フェーズ内のパス実行順序は以下の仕組みで決定される:

1. **Sequence**: `InPhase(phase)` で取得したシーケンス内のパスは宣言順に実行される
2. **BeforePlugin / BeforePass**: パス間の明示的な順序制約
3. **トポロジカルソート**: 制約を満たすように全パスをソートする。制約違反はエラー
4. **フェーズ間制約は不可**: 異なるフェーズのパス間に制約を設定するとエラーになる

### 組込みパス

| パス | フェーズ | 機能 |
|---|---|---|
| SyncPlatformConfigPass | InternalPrePlatformInit | ExtractCommonAvatarInfo でプラットフォーム設定を同期 |
| RemoveMissingScriptComponents | Resolving | Missing Script コンポーネントを除去 |
| RemoveEditorOnlyPass | Resolving | EditorOnly タグ付きオブジェクトを除去 |
| Generate portable components | PlatformFinish | プライマリプラットフォームからポータブルコンポーネントを生成 |

## Extension API (L2)

### IExtensionContext

プラグイン間で状態を共有するためのインターフェース。パスの実行前に自動的にアクティベート / ディアクティベートされる。

```csharp
public interface IExtensionContext
{
    void OnActivate(BuildContext context);
    void OnDeactivate(BuildContext context);
}
```

**ライフサイクル**:
- Sequence に `WithRequiredExtension(typeof(T))` を指定すると、そのパス実行前に `OnActivate` が呼ばれる
- 後続パスが当該 Extension と互換でない場合、`OnDeactivate` が呼ばれる
- `WithCompatibleExtension` を指定すると、既にアクティブな Extension を非活性化しない

**属性によるコンテキスト管理**:
- `[DependsOnContext(typeof(T))]`: Extension が別の Extension に依存することを宣言。アクティベート時に依存先も自動アクティベートされる
- `[CompatibleWithContext(typeof(T))]`: Pass がアクティブな Extension を非活性化しないことを宣言

### 主要な組込み Extension Context

| Extension Context | 用途 | 依存先 |
|---|---|---|
| VirtualControllerContext | アニメーターコントローラの仮想化。アクティブ中はアニメーターがクローンされ、VirtualAnimatorController として操作可能 | なし |
| AnimatorServicesContext | アニメーター操作の統合サービス。AnimationIndex + ObjectPathRemapper + VirtualControllerContext を束ねる | VirtualControllerContext |

### BuildContext API

パスの `Execute(BuildContext context)` に渡されるコンテキストオブジェクト。

| メンバー | 型 | 用途 |
|---|---|---|
| `AvatarRootObject` | GameObject | ビルド対象アバターのルート |
| `AvatarRootTransform` | Transform | ルートの Transform |
| `Extension<T>()` | T | アクティブな Extension Context の取得 |
| `GetState<T>()` | T | パス間で共有する任意の状態オブジェクト |
| `AssetSaver` | IAssetSaver | 生成アセットの永続化 |
| `ObjectRegistry` | ObjectRegistry | オブジェクト追跡 (移動・置換の原本記録) |
| `ErrorReport` | ErrorReport | エラーレポートへの書き込み |
| `PlatformProvider` | INDMFPlatformProvider | 現在のビルドプラットフォーム |
| `Successful` | bool | エラー未発生かどうか |

### プラットフォームフィルタリング

パスやプラグインの実行をプラットフォームで制限する仕組み。

**プラットフォーム識別子** (`WellKnownPlatforms`):
- `nadena.dev.ndmf.generic`: 基本レンダリング前提
- `nadena.dev.ndmf.vrchat.avatar3`: VRChat Avatar 3.0
- `nadena.dev.ndmf.resonite`: Resonite

**宣言方法** (優先度: Pass属性 > Sequence > Plugin属性):

1. **Plugin 属性**: `[RunsOnPlatforms("nadena.dev.ndmf.vrchat.avatar3")]` または `[RunsOnAllPlatforms]`
2. **Sequence メソッド**: `sequence.OnPlatforms(new[] { WellKnownPlatforms.VRChatAvatar30 }, s => { ... })`
3. **Pass 属性**: `[RunsOnPlatforms(...)]` または `[RunsOnAllPlatforms]`

**デフォルト動作**: 属性もメソッド指定もない場合、VRChat Avatar 3.0 のみで実行される (後方互換)。

### INDMFPlatformProvider

プラットフォーム固有の処理を抽象化するインターフェース。VRChat SDK がインストールされていれば VRChatPlatform が自動登録される。

主要メソッド:
- `ExtractCommonAvatarInfo`: プラットフォーム固有コンポーネントから共通アバター情報を抽出
- `GeneratePortableComponents`: 非ターゲットプラットフォーム向けにポータブルコンポーネントを生成
- `AvatarRootComponentType`: アバタールートを特定するコンポーネント型

## 操作パターン (L2)

### NDMF プラグインの基本定義

最小構成のプラグイン定義。`[assembly: ExportsPlugin]` で登録し、`Plugin<T>` を継承して `Configure()` でパスを宣言する。

```csharp
[assembly: ExportsPlugin(typeof(MyPlugin))]

class MyPlugin : Plugin<MyPlugin>
{
    public override string QualifiedName => "com.example.my-plugin";
    public override string DisplayName => "My Plugin";

    protected override void Configure()
    {
        InPhase(BuildPhase.Transforming)
            .Run(MyPass.Instance);
    }
}

class MyPass : Pass<MyPass>
{
    protected override void Execute(BuildContext context)
    {
        // context.AvatarRootObject でアバターを操作
    }
}
```

### Extension Context を使ったアニメーター操作パターン

AnimatorServicesContext を使うと、アニメーターコントローラの仮想化とオブジェクトパスの自動追跡が得られる。

```csharp
protected override void Configure()
{
    var seq = InPhase(BuildPhase.Transforming);
    seq.WithRequiredExtension(typeof(AnimatorServicesContext), s =>
    {
        s.Run(MyAnimatorPass.Instance);
    });
}

// Pass 内:
var asc = context.Extension<AnimatorServicesContext>();
var controllers = asc.ControllerContext.Controllers; // IDictionary<object, VirtualAnimatorController>
var pathRemapper = asc.ObjectPathRemapper;
```

### パス実行順序の制約宣言パターン

他プラグインとの実行順序を明示的に制約する。

```csharp
protected override void Configure()
{
    InPhase(BuildPhase.Transforming)
        .Run(EarlyPass.Instance)
            .BeforePlugin("nadena.dev.modular-avatar") // MA の前に実行
        .Then.Run(LatePass.Instance); // EarlyPass の後に実行
}
```

### プラットフォーム条件付きパスパターン

特定プラットフォームでのみ実行するパスと、全プラットフォームで実行するパスを混在させる。

```csharp
protected override void Configure()
{
    var seq = InPhase(BuildPhase.Transforming);

    // VRChat 専用パス (デフォルト動作)
    seq.Run(VRChatOnlyPass.Instance);

    // 全プラットフォーム対応パス
    seq.OnAllPlatforms(s =>
    {
        s.Run(UniversalPass.Instance);
    });

    // 複数プラットフォーム対応パス
    seq.OnPlatforms(new[] { WellKnownPlatforms.VRChatAvatar30, WellKnownPlatforms.Resonite }, s =>
    {
        s.Run(MultiPlatformPass.Instance);
    });
}
```

## SerializedProperty リファレンス (L3)

ソースバージョン: 1.7.9 (UnityTool_sample) + 1.11.0 (Shiratsume) 差分検証済み
検証方法: .cs ソースコード読み + .meta ファイルの GUID 抽出 (inspect 実測なし → confidence: medium)

### Script GUID テーブル

#### ユーザー向けコンポーネント

| コンポーネント | GUID | 備考 |
|---|---|---|
| NDMFAvatarRoot | `52fa21b17bc14dc294959f976e3e184f` | `[NDMFExperimental]` マーカー (フィールドなし) |
| NDMFViewpoint | `0ab1015720c14a6f8fc82e619577ae94` | マーカー (フィールドなし) |
| PortableBlendshapeVisemes | `af3bdc53cbb9418e970a35a186cc6a8b` | |
| PortableDynamicBone | `88fc3a5ff09b438f81fadf9e76c432d1` | |
| PortableDynamicBoneCollider | `718f4be58b24469b954a517d5adafdbf` | |

#### インフラコンポーネント

| コンポーネント | GUID | 備考 |
|---|---|---|
| GeneratedAssets | `1a2be196c9955284c888ab3769068aec` | ScriptableObject |
| SubAssetContainer | `4ee4713ac71e4f8a911a54de59b4829d` | ScriptableObject |
| SelfDestructComponent | `85007fe4bf2143538d606445019b01a2` | グローバル名前空間 |
| ProxyTagComponent | `5fcc33a40bec42a4a7c117b74e5b061f` | internal |
| AlreadyProcessedTag | `bc59d628745a4afaad8f152997c67105` | |
| NonPersistentConfig | `51368ef264ae4fc8a5343490ac690215` | ScriptableSingleton |
| ApplyOnPlayGlobalActivator | `c432a5ed00a04259997c23712bcf2186` | |

### 共通型

**OverrideProperty\<T\>** (Serializable class — PortableDynamicBone で使用):
- `m_override`: bool — プライマリプラットフォームの値を上書きするか
- `m_value`: T — 上書き値

### コンポーネント別フィールド

#### PortableBlendshapeVisemes
| propertyPath | 型 | 説明 |
|---|---|---|
| `m_targetRenderer` | SkinnedMeshRenderer | ビゼーム対象レンダラー |
| `m_shapes` | List\<Shape\> | ビゼームマッピングリスト |
| `m_shapes.Array.data[n].VisemeName` | string | ビゼームキー名 |
| `m_shapes.Array.data[n].Blendshape` | string | ブレンドシェイプ名 |

#### PortableDynamicBone
| propertyPath | 型 | 説明 |
|---|---|---|
| `m_root` | Transform | 揺れもののルートボーン |
| `m_templateName.m_override` | bool | テンプレート名の上書き |
| `m_templateName.m_value` | string | テンプレート名 (hair, long_hair, tail, ear, breast, generic) |
| `m_baseRadius.m_override` | bool | 基本半径の上書き |
| `m_baseRadius.m_value` | float | 基本半径 |
| `m_ignoreTransforms.m_override` | bool | 無視 Transform の上書き |
| `m_ignoreTransforms.m_value` | List\<Transform\> | 無視する Transform リスト |
| `m_isGrabbable.m_override` | bool | 掴み可否の上書き |
| `m_isGrabbable.m_value` | bool | 掴み可否 |
| `m_ignoreSelf.m_override` | bool | 自己衝突無視の上書き |
| `m_ignoreSelf.m_value` | bool | 自己衝突無視 |
| `m_colliders.m_override` | bool | コライダーリストの上書き |
| `m_colliders.m_value` | List\<PortableDynamicBoneCollider\> | コライダーリスト |
| `m_ignoreMultiChild.m_override` | bool | マルチ子ボーン無視の上書き |
| `m_ignoreMultiChild.m_value` | bool | マルチ子ボーン無視 |
| `m_radiusCurve.m_override` | bool | 半径カーブの上書き |
| `m_radiusCurve.m_value` | AnimationCurve | 半径カーブ |

#### PortableDynamicBoneCollider
| propertyPath | 型 | 説明 |
|---|---|---|
| `m_root` | Transform | コライダーのルート |
| `m_colliderType` | PortableDynamicColliderType | Sphere=0, Capsule=1, Plane=2 |
| `m_radius` | float | コライダー半径 |
| `m_height` | float | コライダー高さ |
| `m_positionOffset` | Vector3 | 位置オフセット |
| `m_rotationOffset` | Quaternion | 回転オフセット |
| `m_insideBounds` | bool | 内側判定 |

#### NonPersistentConfig (ScriptableSingleton)
| propertyPath | 型 | 説明 |
|---|---|---|
| `applyOnPlay` | bool | Play モードで自動適用 (デフォルト: true) |
| `applyOnBuild` | bool | ビルド時に自動適用 (デフォルト: true) |

### 設計上の注意点

- **INDMFEditorOnly**: VRChat SDK がある場合は `IEditorOnly` を継承し、ない場合は空インターフェース。NDMF のランタイムコンポーネントはすべてこれを実装し、ビルド後にアバターに残らない。
- **IPortableAvatarConfigTag**: NDMFViewpoint, PortableBlendshapeVisemes が実装。ポータブルなアバター設定を示すマーカー。
- **OverrideProperty\<T\>**: プライマリプラットフォームの値を継承しつつ、必要に応じて上書きするパターン。`WeakSet()` は Override=false のときのみ値を設定する。
- **NDMF コンポーネントの名前空間**:
  - `nadena.dev.ndmf.runtime.components`: NDMFAvatarRoot, NDMFViewpoint, PortableBlendshapeVisemes
  - `nadena.dev.ndmf.multiplatform.components`: PortableDynamicBone, PortableDynamicBoneCollider, OverrideProperty
  - グローバル名前空間: SelfDestructComponent (レガシー)

### 1.7.9 → 1.11.0 変更サマリー

- **新コンポーネント 8 種**: NDMFAvatarRoot, NDMFViewpoint, OverrideProperty, PortableDynamicBone, PortableDynamicBoneCollider, PortableBlendshapeVisemes, IOverrideProperty, IPortableAvatarConfigTag
- **新フェーズ 3 種**: FirstChance, PlatformInit, PlatformFinish (旧 4 フェーズに加えて拡張)
- **新 API**: WellKnownPlatforms, RunsOnPlatforms/RunsOnAllPlatforms 属性, CompatibleWithContext 属性, INDMFPlatformProvider, Sequence.OnPlatforms/OnAllPlatforms
- **構造変更**: BuildPhase が sealed class に変更 (enum ではない)。内部 InternalPrePlatformInit フェーズの追加。PluginInfo がデフォルトプラットフォームを VRChat に設定する後方互換処理を内包
- **削除コンポーネント**: なし

## 実運用で学んだこと

(なし — Phase 3-4 の検証で追記する)
