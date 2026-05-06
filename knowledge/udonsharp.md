---
tool: udonsharp
version_tested: "VRC SDK 3.7+ / UdonSharp 1.x"
last_updated: 2026-05-03
confidence: high
---

# UdonSharp Prefab 構築パターン

> Phase 3 実環境検証済み。Script GUID、inspect_wiring、validate_refs で実測確認。

## L1: 基本構造

### コンパイルパイプライン
C# (.cs) → UdonSharp Compiler → Udon Assembly → Udon VM bytecode

### アーティファクト構成
1. **C# スクリプト (.cs)** — UdonSharpBehaviour を継承したソースコード
2. **UdonSharpProgramAsset (.asset)** — **手動で作成が必要**（Create > U# Script で .cs と同時に作るか、既存 .cs に対して作成）
   - `m_Script`: `c333ccfdd0cbdbc4ca30cef2dd6e6b9b`（UdonSharpProgramAsset クラス、全プロジェクト共通）
   - `sourceCsScript`: 元の .cs の GUID
   - `serializedUdonProgramAsset`: コンパイル済みバイトコードへの参照
   - `behaviourSyncMode`: 同期モード値
   - `hasInteractEvent`: Interact() の有無
   - `serializationData`: フィールド定義（Odin Serializer 形式）
3. **SerializedUdonPrograms/ 内の .asset** — コンパイル済みバイトコード

### Script GUID リファレンス
| クラス | GUID | 名前空間 | 場所 |
|--------|------|----------|------|
| UdonSharpProgramAsset | `c333ccfdd0cbdbc4ca30cef2dd6e6b9b` | `UdonSharp` | Editor |
| UdonSharpBehaviour | `3c6e5249679282e459858775b10f38d0` | `UdonSharp` | Runtime |
| UdonBehaviour | `45115577ef41a5b4ca741ed302693907` | `VRC.Udon` | Runtime |
| PipelineManager | `4ecd63eff847044b68db9453ce219299` | `VRC.Core` | Runtime（VRCWorld に付随） |

### UdonSharpProgramAsset の SerializedField
| フィールド | 型 | 用途 |
|-----------|-----|------|
| `sourceCsScript` | MonoScript | 元の C# スクリプト参照 |
| `scriptVersion` | UdonSharpProgramVersion | ソーススクリプトバージョン |
| `compiledVersion` | UdonSharpProgramVersion | コンパイル済みバージョン |
| `behaviourSyncMode` | BehaviourSyncMode | ネットワーク同期モード |
| `hasInteractEvent` | bool | Interact() の有無 |
| `scriptID` | long | スクリプト固有 ID |
| `serializationData` | SerializationData | Odin Serializer でのフィールド定義 |

### UdonSharpBehaviour の隠しフィールド
| フィールド | 型 | 用途 |
|-----------|-----|------|
| `_udonSharpBackingUdonBehaviour` | UdonBehaviour | **必須**: ランタイム UdonBehaviour へのリンク |
| `serializationData` | SerializationData | 複雑データのシリアライズ |

### UdonBehaviour の SerializedField
| フィールド | 型 | 用途 |
|-----------|-----|------|
| `programSource` | AbstractUdonProgramSource | プログラムソース（Editor only） |
| `serializedProgramAsset` | AbstractSerializedUdonProgramAsset | コンパイル済みプログラム |
| `_syncMethod` | SyncType | ネットワーク同期モード |
| `publicVariables` | IUdonVariableTable | 公開変数テーブル（Odin） |

### 同期モードの対応
| UdonSharp 属性 | .asset の behaviourSyncMode 値 | 用途 |
|----------------|-------------------------------|------|
| `BehaviourSyncMode.NoVariableSync` | 2 | イベント送信のみ |
| `BehaviourSyncMode.Manual` | 4 | `RequestSerialization()` で明示的同期 |
| `BehaviourSyncMode.Continuous` | 6 | 毎フレーム自動同期（非推奨） |

### 同期可能な型
`bool`, `sbyte`, `byte`, `short`, `ushort`, `int`, `uint`, `long`, `ulong`, `float`, `double`, `Vector2`, `Vector3`, `Vector4`, `Quaternion`, `Color`, `Color32`, `char`, `string`, `VRCUrl` + これらの配列

## L2: C# 制約

### 使用不可
- `try`/`catch`/`finally` — 例外処理なし
- `async`/`await` — 非同期パターンなし
- デリゲート、C# イベント、ラムダ
- LINQ (`System.Linq`)
- ジェネリッククラス (`Class<T>`)、ジェネリック非 static メソッド
- UdonSharpBehaviour 間の継承
- インターフェース
- `List<T>`, `Dictionary<K,V>` — **配列 `[]` のみ使用可能**
- プロパティ（getter/setter）
- メソッドオーバーロード
- カスタム enum（Unity 定義の enum は可）
- 名前空間定義

### 使用可能
- 基本構文: if/else, for/foreach/while, switch
- 配列（1次元、ジャグ配列）
- static メソッド
- 属性: `[SerializeField]`, `[HideInInspector]`, `[NonSerialized]`, `[Header]`, `[Tooltip]`, `[TextArea]`
- Unity ライフサイクル: `Start`, `Update`, `LateUpdate`, `FixedUpdate`, `OnEnable`, `OnDisable`

### 注意事項
- フィールド初期値はコンパイル時のみ。シーン依存の初期化は `Start()` で行う
- 数値キャストはオーバーフローチェック付き
- 構造体のメソッドは元を変更しない（`Vector3.Normalize()` は新しい値を返す）
- `GetComponent<UdonBehaviour>()` はキャスト必要: `(UdonBehaviour)GetComponent(typeof(UdonBehaviour))`

## L2: イベントシステム

### ローカルイベント
- `SendCustomEvent("MethodName")` — 同一 UdonBehaviour のメソッド呼び出し
- `SendCustomEventDelayedSeconds("MethodName", delay, EventTiming)` — 遅延呼び出し
- `SendCustomEventDelayedFrames("MethodName", frames, EventTiming)` — フレーム遅延

### ネットワークイベント
- `SendCustomNetworkEvent(NetworkEventTarget.All, "MethodName")` — 全クライアント実行
- `SendCustomNetworkEvent(NetworkEventTarget.Owner, "MethodName")` — オーナーのみ
- `[NetworkCallable]` 属性（新 API）— public メソッド、戻り値なし、最大8引数

### 主要 VRChat イベント
| カテゴリ | イベント |
|---------|---------|
| Interaction | `Interact()` |
| Player | `OnPlayerJoined/Left(VRCPlayerApi)`, `OnPlayerRespawn` |
| Network | `OnDeserialization()`, `OnPreSerialization()`, `OnPostSerialization(SerializationResult)` |
| Ownership | `OnOwnershipRequest(VRCPlayerApi, VRCPlayerApi)`, `OnOwnershipTransferred` |
| Pickup | `OnPickup()`, `OnDrop()`, `OnPickupUseDown/Up()` |
| Station | `OnStationEntered/Exited(VRCPlayerApi)` |
| Collision | `OnPlayerTriggerEnter/Stay/Exit(VRCPlayerApi)` |

### エリア判定パターン

`OnPlayerTriggerEnter/Exit` はトリガーコライダーと **同一 GameObject** 上の UdonBehaviour でのみ発火する。任意の Collider でエリア判定したい場合は `Collider.ClosestPoint` による位置ベース判定を使う。

```csharp
// 任意の Collider（別 GO でも可）でプレイヤーの内外判定
var pos = Networking.LocalPlayer.GetPosition();
bool inside = Vector3.Distance(pos, areaCollider.ClosestPoint(pos)) < 0.01f;
```

| 方式 | 制約 | 用途 |
|------|------|------|
| `OnPlayerTriggerEnter/Exit` | 同一 GO 必須、isTrigger 必須 | コライダーと UdonBehaviour が同居できる場合 |
| `ClosestPoint` ポーリング | 任意 GO、任意 Collider 形状 | エリアコライダーを別オブジェクトに分離したい場合 |

- `ClosestPoint` は isTrigger の有無に関わらず動作するが、プレイヤーが通過できるよう isTrigger=true が必要
- ポーリング間隔は用途に応じて調整（60秒クリアなら1秒間隔で十分）
- ボタン押下時に即判定して初期状態を正しくセットする

### コンポーネント間通信パターン
- 型付き参照で直接メソッド呼び出し: `controller._PressA()`
- `SendCustomEvent` でアンダースコアプレフィックスのメソッド呼び出し（慣習: `_MethodName()`）

## L2: ネットワーク同期パターン

### Manual Sync ワークフロー
1. `[UdonSynced]` でフィールドをマーク
2. `Networking.SetOwner(localPlayer, gameObject)` でオーナー取得
3. フィールド値を変更
4. `RequestSerialization()` を呼ぶ
5. リモート側で `OnDeserialization()` が発火

### `[FieldChangeCallback]`
同期変数の変更時にプロパティエミュレーション（フィールド＋メソッドペア）を発火。標準 C# プロパティ構文 `{ get; set; }` ではなく UdonSharp 独自の命名規約で動作する。`Update()` でのポーリング不要。

#### 単一 vs. 複数 synced field 比較表 (issue #122)

`[FieldChangeCallback]` は「個別 field の値変化」を捕まえる API なので、複数 synced field の整合した状態が要件のときは `OnDeserialization` 待ち合わせが必須。判断基準を一覧化したもの:

| 観点 | 単一 synced field のみ | 複数 synced field の整合が必要 |
|------|----------------------|-------------------------------|
| 採用するフック | `[FieldChangeCallback]` | `OnDeserialization(DeserializationResult)` |
| Fire タイミング | 当該 field が deserialize された瞬間 | 全 synced field の deserialize が終わったあと |
| 他 field の値 | 未到着の可能性あり（読むと初期値） | 全部最新値が入っている |
| Owner 側の処理 | 自分は callback 来ない → 明示呼び出しを足す | 同上 → SubmitUrl 直後に明示呼び出し |
| Idempotency | 連続 fire しうるので idempotent 必須 | 同 baseline で再 fire しないよう track 必要 |
| 典型的な失敗 | 該当なし | callback 内で他 synced field を読み、未到着の値で誤動作 |
| 代表ユースケース | 単一 enum / int の状態同期 | URL + baseline timestamp / 複合状態の同期再生 |

#### ⚠ 落とし穴: 複数 synced field の deserialization race (2026-05-02 検証済み)

**症状**: `[UdonSynced] _syncedPcUrl` (VRCUrl) と `[UdonSynced, FieldChangeCallback(...)] _baselineServerTimeMs` (long) を **同じ SubmitUrl 内で両方更新** → owner で `RequestSerialization` → remote で deserialize 中、`_baselineServerTimeMs` の callback が **`_syncedPcUrl` 未到着のタイミング** で fire し、callback 内で `pcUrl.Get()` を読むと `''` が返る。NadeVision で「audience 側 Sat PC が `branch=BackgroundLoad` に正しく入るが `pc=''` で abort する」症状の根本原因。

**Why**: `[FieldChangeCallback]` は「特定 field の値変化」を捕まえる API で、「synced 群全体の整合した状態」を保証しない。U# / Udon の deserialize は field 単位で順次進むので、callback は全 field 整合前の中間状態で fire し得る。VRChat 公式 docs ([Networking Tips & Tricks](https://udonsharp.docs.vrchat.com/networking-tips-&-tricks/)) に明示:

> "if you have multiple networked variables, other networked variables may not be updated yet when a [FieldChangeCallback] happens."

**正しいパターン**: `OnDeserialization` (全 synced field deserialize 完了後に fire) で待ち合わせる + owner 用に明示呼び出しを足す:

```csharp
using VRC.Udon.Common;

[UdonSynced] public VRCUrl _syncedPcUrl;
[UdonSynced] public long _baselineServerTimeMs;
private long _lastSeenBaselineServerTimeMs;

public override void OnDeserialization(DeserializationResult result)
{
    if (_baselineServerTimeMs == _lastSeenBaselineServerTimeMs) return;
    _lastSeenBaselineServerTimeMs = _baselineServerTimeMs;
    OnSyncedSubmitInternal();  // ← _syncedPcUrl は確実に最新
}

public void SubmitUrl(VRCUrl pcUrl, ...)
{
    if (!Networking.IsOwner(gameObject)) Networking.SetOwner(...);
    _syncedPcUrl = pcUrl;
    _baselineServerTimeMs = Networking.GetServerTimeInMilliseconds();
    _lastSeenBaselineServerTimeMs = _baselineServerTimeMs;  // owner は OnDeserialization 来ないので track
    RequestSerialization();
    OnSyncedSubmitInternal();  // owner 自身の明示発火
}
```

**判断基準**:
- 「単一 field の値変化のみで処理が完結する」→ `[FieldChangeCallback]` 使ってよい (owner 自分自身の処理は明示呼び出しを足す)
- 「複数 synced field の整合した状態」が必要 → `OnDeserialization` で待ち合わせ
- ハンドラは **idempotent** に書く (重複呼出で副作用が出ないように)

参考実装: `Packages/idv.jlchntoz.vvmw/Runtime/VVMW/Core.cs:704` (`OnDeserialization(DeserializationResult result)` で全 sync 整合後の処理を集約)。

### 帯域制限
- 合計 ~11 KB/秒
- Manual: 最大 ~280KB/回
- Continuous: 最大 ~200 bytes/回

### Late Joiner
- 同期変数は Late Joiner に自動送信される
- `OnDeserialization` が参加時に発火
- 手動で `RequestSerialization()` を呼ぶ必要なし

## L3: Prefab Sentinel での UdonSharp 操作

### 推奨ワークフロー
1. C# スクリプト (.cs) を作成
2. `editor_recompile` で Unity に認識させる
3. `editor_create_udon_program_asset` でプログラムアセット (.asset) 作成
4. GameObject 階層を構築（`Create Empty Child` + `editor_rename`）
5. `editor_add_component` で UdonSharp コンポーネント追加（backing UdonBehaviour 自動生成）
6. `editor_set_property` でフィールド配線（`object_reference` にヒエラルキーパス指定）
7. `editor_safe_save_prefab` で Prefab 化
8. `inspect_wiring` + `validate_refs` で検証

### フォールバック: 手動 wiring パターン (2026-04-30)

ステップ 5 で `UdonSharpProgramAsset not found` が出て MonoBehaviour として attach されてしまう症状が発生したら、UdonSharp 内部キャッシュ未更新の可能性。以下の 5 ステップで明示的に構築:

1. `editor_add_component` で `VRC.Udon.UdonBehaviour` を追加
2. `editor_set_property programSource = .asset` (asset path 指定) で UdonBehaviour に program asset を割り当て
3. `editor_add_component` で UdonSharp class (proxy MonoBehaviour として追加される、警告は無視してよい)
4. `editor_set_property _udonSharpBackingUdonBehaviour = /Path:UdonBehaviour` で proxy → UB を link
5. `editor_set_property` で各 public field を配線

注意: ステップ 3 で同じ UdonSharp class を re-add すると proxy MonoBehaviour と UdonBehaviour が duplicate しやすい。最終 inspect で 2 セット残ったら Inspector 手動削除 or Editor menu helper で clean up する。

## 実運用で学んだこと

### `OnBeforeSerialize` ArgumentNullException は非致命扱い (2026-05-03)

- 症状: `editor_safe_save_prefab` 実行中に `ArgumentNullException` が console に出る。stack trace の先頭に `UdonSharpBehaviour.OnBeforeSerialize`（または `UdonSharp.UdonSharpBehaviour.OnBeforeSerialize` を含む派生型）が現れる。
- 原因: UdonSharp 1.x が proxy MonoBehaviour を Unity の serialization callback に晒しており、未リンクのフィールドや stripped instance に対して NRE を投げる。SaveAsPrefabAsset 自体は成功するため、結果アセットには影響しない。
- 分類: PrefabSentinel の Editor Bridge は label `udonsharp_obs_nre` でこのパターンを **non-fatal** として登録（issue #117、`tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` の `NonFatalExceptionClassifier`）。
- 観測:
  - `editor_safe_save_prefab` / `editor_instantiate_to_scene` は `success=true` のまま `data.warnings.udonsharp_obs_nre_count` と `data.warnings.nonfatal_patterns` に件数とラベルを返す。
  - `editor_console` の `classification_filter="non_fatal"` でこのエントリだけを抽出可能。`fatal` で除外可能。
- 推奨フロー: 保存自体は止めない。ノイズが多い場合のみ proxy を一旦 unparent して保存し戻す。
- **`OnBeforeSerialize` を override しないこと** (issue #200): UdonSharp 1.x の `UdonSharpBehaviour` 基底クラスは `ISerializationCallbackReceiver.OnBeforeSerialize` を **明示的インターフェース実装** (explicit interface implementation) として持つため、サブクラスから `public override void OnBeforeSerialize()` を書こうとしても U# コンパイラが拒否する。空実装でノイズを抑える対処は不可能。代わりに console 側で `classification_filter="non_fatal"` などで除外する。

### DualButtonSwitcher パターン (2026-03-27)
- 2ボタン + 3状態（None/A/B）のグローバル切り替えシステム
- Controller (ManualSync) + Button (NoVariableSync) の分離設計
- ボタンは `Interact()` → `controller._PressA()/_PressB()` でイベント送信
- Controller が `[UdonSynced]` 状態を管理、`OnDeserialization` で全プレイヤーに反映
- ボタン自体の表示/非表示も Controller の `ApplyState` 内で制御
- stateNoneObjects はトグルオフ時と初期状態のみ表示される点に注意

### VRCSDKUploadHandler の World SDK 制約 (2026-03-27)
- `VRCSDKUploadHandler.cs` が `VRC.SDK3.Avatars` 名前空間を参照しており、World 専用プロジェクトではコンパイルエラーになる
- World プロジェクトでは VRCSDKUploadHandler を配置せず、Unity の VRC SDK パネルから手動アップロードが必要

### UdonSharp プログラムアセット作成 (2026-03-27)
- .asset は .cs を書いただけでは自動生成されない
- Unity で手動作成（Create > U# Script）か、ブリッジの `create_udon_program_asset` アクションで作成
- 作成後、UdonSharp コンパイラが `serializedUdonProgramAsset` と `serializationData` を自動補完

### Phase 3 実測検証結果 (2026-03-27)
- `inspect_wiring`: DualButtonController prefab で null 参照 0件、フィールド名が C# ソースと一致
- `validate_refs`: 528 参照スキャン、破損 0件
- Script GUID 実測確認:
  - UdonBehaviour `45115577ef41a5b4ca741ed302693907` — VRCWorld と DualButtonController 両方で確認
  - VRCSDK3.dll `661092b4961be7145bfbe56e1e62337b` — VRCWorld の SceneDescriptor で確認
- backing UdonBehaviour が Controller に正しく追加されている（`guid:45115577` のコンポーネントとして確認）
- `4ecd63eff847044b68db9453ce219299` — PipelineManager（VRCWorld に付随）

### DoneruWorldDisplay ワールドギミック構築 (2026-04-08)

- 3 コンポーネント構成（Poller → EventStore → Display）の投げ銭表示ギミック
- 全 12 GameObject、16 コンポーネント、16 フィールド配線を Editor Bridge のみで完遂
- **ProgramAsset タイミング問題を実体験**: .cs 作成 → `editor_add_component` → ProgramAsset 作成の順だと、コンポーネントが通常 MonoBehaviour としてアタッチされる。正しい順序:
  1. `.cs` 作成
  2. `editor_recompile`
  3. `editor_create_udon_program_asset`（全 .cs に対して）
  4. `editor_add_component`（ここで初めて UdonBehaviour になる）
- **配列フィールドの設定パターン**: `editor_set_component_fields` では配列要素を個別設定できない。`editor_set_property` で `historyTexts.Array.size` → `historyTexts.Array.data[N]` の順に設定する
- **object_reference の `::ComponentType` 記法**: `editor_set_component_fields` では UdonSharp コンポーネントに対して `::DoneruEventStore` サフィックスが解決できない。パスのみ（`/Path/To/Object`）で解決する
- **`editor_batch_add_component`**: 同種コンポーネント（Text x5）を一括追加可能。個別呼び出しの 5 倍速い
- **`inspect_wiring` の `is_udon_sharp` フラグ**: コンポーネント再作成後に `true` を確認し、UdonBehaviour として正しく認識されていることを検証できた
- **Prefab Variant 問題**: 同一パスに再度 `editor_safe_save_prefab` すると Variant として保存される。初回保存で生成された Prefab がベースになるため
- **Legacy Text → TMPro 移行**: フィールド型を `Text` → `TextMeshProUGUI` に変更後、Prefab 上の旧コンポーネント削除 → TMPro 追加 → DoneruDisplay 再作成 → 再配線が必要。コンポーネント型が変わるとフィールド参照が切れるため、コンポーネントごと再作成になる
- **UI レイアウト**: RectTransform の `m_SizeDelta`, `m_AnchoredPosition`, `m_LocalScale` を `editor_batch_set_property` で一括設定可能。World Space Canvas は `m_LocalScale` = 0.001 が実用的（Canvas 座標 800x500 → 世界座標 0.8m x 0.5m）

### NadeVision マルチユーザ同期 構築 (2026-04-30)

NadeVision 動画プレイヤーパッケージにマルチユーザ同期機能を追加。3 つの新規 UdonSharp + 1 既存改修 + World Space Canvas 新規構築 + 全 Bridge 配線を MCP 経由で完遂。

- **UdonSharp Program Asset 検出キャッシュの問題**: `editor_create_udon_program_asset` で .asset を生成しても、UdonSharp 内部の `GetAllUdonSharpPrograms()` キャッシュが更新されず `editor_add_component` が `UdonSharpProgramAsset not found` で MonoBehaviour として attach する症状が発生。`Refresh All UdonSharp Assets` メニューでも `Last: 84, This: 84 — Completed 0 refresh cycles` と出て更新されない。
- **回避策: 手動 wiring パターン** — UdonSharp の `AddUdonSharpComponent` を諦め、以下の 5 ステップで明示構築:
  1. `editor_add_component` で `VRC.Udon.UdonBehaviour` を追加
  2. `editor_set_property programSource = .asset` で UdonBehaviour に program asset を割り当て
  3. `editor_add_component` で UdonSharp class (proxy MonoBehaviour として追加される、警告は無視)
  4. `editor_set_property _udonSharpBackingUdonBehaviour = /Path:UdonBehaviour` で proxy → UB を link
  5. `editor_set_property` で各 public field を配線
- **副作用: proxy duplicate**: 上記パターンで UdonSharp class を re-add すると proxy MonoBehaviour と UdonBehaviour が duplicate しがち。最終 inspect で 2 セット残る場合あり。Inspector で手動削除 or Editor menu helper の cleanup ロジックで対処。
- **stripped prefab instance 内コンポーネント解決**: VVMW Variant prefab のような nested prefab に含まれる UdonSharp class (例: `Core` script) を `:ComponentType` サフィックスで参照する場合、scene 上の **実際のパス** (`/NadeVision/VVMW (On-Screen Controls):Core`) を使う。Prefab inspect_hierarchy が示す `/NadeVision/Core` のような nominal path とは別。
- **Core_Timing API 非公開フィールド**: VVMW `Core` の `defaultUrl` / `autoPlayDelay` は `[SerializeField]` private で外部 UdonSharp から読み取り不可。Bridge 設計時は public な `Core.Url` / `Core.AltUrl` を bootstrap 時に snapshot する方式に切替が必要。同様に `Core.Time` setter は `private set` のため、seek には `Core.Progress = target / Duration` を使う (Progress setter が内部で Time = Duration*value を呼ぶ)。
- **UdonSharp の nested type 制約 (再発)**: enum 等を class 内に nest すると `Nested type declarations are not currently supported by U#` で compile error。class 外に出して top-level で定義する。

### v0.5.85 フルワークフロー検証 (2026-03-27)
- `editor_set_property` で UdonSharp フィールド (`targetObject`) への ObjectReference 配線に成功
- `editor_safe_save_prefab` で `PrefabUtility.SaveAsPrefabAsset` による正規 Prefab 化に成功
- `editor_add_component` で `UnityEngine.BoxCollider` は完全修飾名が必要（`BoxCollider` だけだと `TYPE_NOT_FOUND`）
- `editor_execute_menu_item` で `GameObject/Create Empty Child` を使えば選択中の GO の子として作成可能
- 全工程（スクリプト作成 → プログラムアセット → 階層構築 → コンポーネント追加 → 配線 → Prefab 化 → 検証）が MCP のみで完結
