---
tool: udonsharp
version_tested: "VRC SDK 3.7+ / UdonSharp 1.x"
last_updated: 2026-03-27
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

### 帯域制限
- 合計 ~11 KB/秒
- Manual: 最大 ~280KB/回
- Continuous: 最大 ~200 bytes/回

### Late Joiner
- 同期変数は Late Joiner に自動送信される
- `OnDeserialization` が参加時に発火
- 手動で `RequestSerialization()` を呼ぶ必要なし

## L3: Prefab Sentinel での UdonSharp 操作

### できること
| 操作 | ツール | バージョン |
|------|--------|-----------|
| GameObject 作成 | `editor_execute_menu_item` (Create Empty / Create Empty Child) | v0.5.82+ |
| リネーム | `editor_rename` | v0.5.84+ |
| コンポーネント追加 | `editor_add_component` (UdonSharp 含む、backing 自動生成) | v0.5.84+ |
| プログラムアセット作成 | `editor_create_udon_program_asset` (リフレクション経由) | v0.5.84+ |
| UdonSharp フィールド配線 | `editor_set_property` (`object_reference` でヒエラルキーパス指定) | v0.5.85+ |
| Prefab 化 | `editor_save_as_prefab` (`PrefabUtility.SaveAsPrefabAsset`) | v0.5.85+ |
| 階層検証 | `inspect_hierarchy` | v0.5.82+ |
| フィールド参照検証 | `inspect_wiring` | v0.5.82+ |
| フィールド値閲覧 | `find_unity_symbol` (include_properties) | v0.5.82+ |
| スクリプト認識 | `editor_refresh` / `editor_recompile` | v0.5.82+ |
| コンパイルエラー確認 | `editor_console` | v0.5.82+ |

### ブリッジ実装詳細

#### create_udon_program_asset (UnityEditorControlBridge.cs:1909-1951)
1. `AssetDatabase.LoadAssetAtPath<MonoScript>(scriptPath)` で .cs を読み込み
2. リフレクションで `UdonSharp.UdonSharpProgramAsset` 型を解決
3. `ScriptableObject.CreateInstance(assetType)` で生成
4. `sourceCsScript` フィールドをリフレクションで設定
5. `AssetDatabase.CreateAsset()` で .cs 横に .asset として保存
6. UdonSharp コンパイラが残り（`serializedUdonProgramAsset`, `serializationData`）を自動補完

#### TrySetupUdonSharpBacking (UnityPatchBridge.cs:3080-3186)
1. コンポーネント型が `UdonSharpBehaviour` 継承か検出
2. 同一 GO に backing `UdonBehaviour` を追加
3. `_udonSharpBackingUdonBehaviour` プロパティでリンク
4. `GetAllUdonSharpPrograms()` で全プログラムアセットをスキャン
5. `sourceCsScript.GetClass()` でスクリプトクラスをマッチ
6. backing UdonBehaviour の `programSource` にアセットを設定

### できないこと（制約）
- `editor_add_component` で Unity 標準コンポーネントに `UnityEngine.` 完全修飾名が必要な場合がある（`BoxCollider` → `UnityEngine.BoxCollider`）
- 非 open-mode での `add_component`（YAML patch 版）は既存 Prefab へのコンポーネント追加に制限あり

### 推奨ワークフロー (v0.5.85+ — 全工程 MCP 完結)
1. C# スクリプト (.cs) を作成（Write ツール）
2. `editor_refresh` / `editor_recompile` で Unity に認識させる
3. `editor_create_udon_program_asset` でプログラムアセット作成
4. `editor_execute_menu_item` (`Create Empty` / `Create Empty Child`) + `editor_rename` で GameObject 階層を構築
5. `editor_add_component` で UdonSharp コンポーネント追加（backing UdonBehaviour 自動生成）
6. `editor_set_property` で UdonSharp フィールド配線（`object_reference` にヒエラルキーパス指定）
7. `editor_save_as_prefab` で正規 Prefab 化（`PrefabUtility.SaveAsPrefabAsset`）
8. `inspect_hierarchy` + `inspect_wiring` + `validate_refs` で検証

## 実運用で学んだこと

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

### v0.5.85 フルワークフロー検証 (2026-03-27)
- `editor_set_property` で UdonSharp フィールド (`targetObject`) への ObjectReference 配線に成功
- `editor_save_as_prefab` で `PrefabUtility.SaveAsPrefabAsset` による正規 Prefab 化に成功
- `editor_add_component` で `UnityEngine.BoxCollider` は完全修飾名が必要（`BoxCollider` だけだと `TYPE_NOT_FOUND`）
- `editor_execute_menu_item` で `GameObject/Create Empty Child` を使えば選択中の GO の子として作成可能
- 全工程（スクリプト作成 → プログラムアセット → 階層構築 → コンポーネント追加 → 配線 → Prefab 化 → 検証）が MCP のみで完結
