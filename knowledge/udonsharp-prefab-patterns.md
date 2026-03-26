# UdonSharp Prefab 構築パターン

version_tested: VRC SDK 3.7+ / UdonSharp 1.x
last_updated: 2026-03-27
confidence: high (実プロジェクトで検証済み)

## L1: 基本構造

### UdonSharp コンポーネントのシリアライズ構造
UdonSharp スクリプトは Unity 上では以下の2層構造でシリアライズされる:

1. **UdonSharpProgramAsset (.asset)** — **手動で作成が必要**。Unity で Create > U# Script で .cs と同時に作るか、既存の .cs に対して Udon C# Program Asset を手動作成して紐付ける
   - `serializedUdonProgramAsset`: コンパイル済み Udon Assembly への参照
   - `sourceCsScript`: 元の C# スクリプトの GUID
   - `behaviourSyncMode`: 同期モード (2=NoVariableSync, 4=Manual)
   - `hasInteractEvent`: Interact() の有無
   - `scriptID`: スクリプト識別子

2. **Prefab/Scene 内の MonoBehaviour** — UdonBehaviour として実行される
   - `m_Script` は UdonSharpRuntime の GUID を参照
   - フィールド値は Udon VM 形式でシリアライズ（通常の MonoBehaviour と異なる）

### 同期モードの対応
| UdonSharp 属性 | behaviourSyncMode 値 | 用途 |
|----------------|---------------------|------|
| `BehaviourSyncMode.NoVariableSync` | 2 | イベント送信のみ、変数同期なし |
| `BehaviourSyncMode.Manual` | 4 | `RequestSerialization()` で明示的同期 |
| `BehaviourSyncMode.Continuous` | 6 | 毎フレーム自動同期（非推奨） |

## L2: Prefab Sentinel での UdonSharp 操作

### できること
- `inspect_hierarchy`: UdonSharp コンポーネント付き Prefab の階層表示
- `inspect_wiring`: UdonSharp フィールドの参照検証（null チェック、重複参照検出）
- `find_unity_symbol`: UdonSharp コンポーネントのフィールド値閲覧
- YAML 直接編集: Prefab の GameObject/Transform 階層構築
- `editor_refresh` / `editor_recompile`: スクリプト変更後のコンパイルトリガー
- `editor_console`: UdonSharp コンパイルエラーの確認

### できないこと（制約）
- `add_component` (open-mode): 既存 Prefab への UdonBehaviour 追加は不可。UdonBehaviour の特殊なシリアライズ（Udon VM データ、プログラムアセット参照）を生成できない
- UdonSharp プログラムアセットの作成: .asset は自動生成されない。Unity Editor で手動作成（Create > U# Script or Udon C# Program Asset）が必要。MCP からは作成手段がない
- `set_property` での UdonSharp フィールド変更: Udon VM のシリアライズ形式が通常の SerializedProperty と異なるため動作未検証

### 推奨ワークフロー
1. C# スクリプト (.cs) を作成
2. `editor_refresh` / `editor_recompile` で Unity に認識させる
3. **Unity で手動**: Udon C# Program Asset (.asset) を作成し .cs を紐付け
4. YAML 直接編集で Prefab の階層（GameObject + Transform）を構築
5. `inspect_hierarchy` で階層を検証
6. **Unity Inspector で手動**: UdonSharp コンポーネントを追加・フィールド設定
7. `inspect_wiring` で参照の整合性を検証
8. `validate_refs` で壊れた参照がないことを確認

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
