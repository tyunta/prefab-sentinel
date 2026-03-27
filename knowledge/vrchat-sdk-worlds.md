---
tool: vrchat-sdk-worlds
version_tested: "VRC SDK 3.7+"
last_updated: 2026-03-27
confidence: medium
---

# VRChat World SDK 3

## L1: 基本概要

VRChat World SDK 3 は UdonSharp / Udon Graph でインタラクティブなワールドを構築するための SDK。

## L2: Script GUID リファレンス

**ランタイム DLL**: `VRCSDK3.dll` (GUID: `661092b4961be7145bfbe56e1e62337b`)

| コンポーネント | 名前空間 | DLL/ソース |
|--------------|----------|-----------|
| VRCSceneDescriptor | `VRC.SDK3.Components` | VRCSDK3.dll |
| VRCPickup | `VRC.SDK3.Components` | VRCSDK3.dll |
| VRCStation | `VRC.SDK3.Components` | VRCSDK3.dll |
| VRCObjectSync | `VRC.SDK3.Components` | VRCSDK3.dll |
| VRCObjectPool | `VRC.SDK3.Components` | VRCSDK3.dll |
| VRCMirrorReflection | `VRC.SDK3.Components` | VRCSDK3.dll |
| UdonBehaviour | `VRC.Udon` | Runtime/Udon/ (GUID: `45115577ef41a5b4ca741ed302693907`) |

## L2: コアコンポーネント

### VRCSceneDescriptor（必須 — 1シーンに1つ）
- **名前空間**: `VRC.SDK3.Components`
- **役割**: ワールド定義。存在しないとビルド不可
- **SerializedField**:
  - `spawns`: Transform[] — スポーン地点配列
  - `spawnRadius`: float — スポーン分散半径
  - `spawnOrder`: enum — Default / Random / InOrder / Demo
  - `spawnOrientation`: enum
  - `ReferenceCamera`: Camera — プレイヤーカメラ設定
  - `RespawnHeightY`: float — この Y 以下でリスポーン
  - `ObjectBehaviourAtRespawnHeight`: enum — Destroy / Respawn
  - `ForbidUserPortals`: bool
  - `DynamicPrefabs`: bool
  - `DynamicMaterials`: bool
  - `interactThruLayers`: int (LayerMask)

### VRCObjectSync
- **名前空間**: `VRC.SDK3.Components`
- **役割**: GameObject の Transform をネットワーク同期
- **必須**: 同一 GO に Rigidbody
- **制御**: `SetKinematic()` / `SetGravity()` — Rigidbody は VRCObjectSync 経由で制御（直接変更不可）

### VRCPickup
- **名前空間**: `VRC.SDK3.Components`
- **役割**: オブジェクトを掴む/使う
- **必須**: Rigidbody + Collider。VRCObjectSync との併用が一般的
- **SerializedField**:
  - `InteractionText` / `UseText`: string — UI テキスト
  - `proximity`: float — 掴める距離
  - `AutoHold`: AutoHoldMode enum (No/Yes/Sometimes/AutoDetect)
  - `orientation`: PickupOrientation enum (Any/Grip/Gun)
  - `pickupable`: bool
  - `allowManipulationWhenEquipped`: bool
  - `ExactGun` / `ExactGrip`: Transform — ハンドポジション
  - `DisallowTheft`: bool
- **Udon イベント**: `OnPickup`, `OnDrop`, `OnPickupUseDown/Up`

### VRCStation
- **名前空間**: `VRC.SDK3.Components`
- **役割**: プレイヤーが座る/乗る
- **必須**: Collider (Is Trigger 推奨)
- **SerializedField**:
  - `stationEnterPlayerLocation` / `stationExitPlayerLocation`: Transform
  - `PlayerMobility`: Mobility enum (Immobile/Mobile)
  - `disableStationExit`: bool
  - `seated`: bool
  - `animatorController`: AnimatorController
  - `canUseStationFromStation`: bool
- **制約**: Entry と Exit は 2m 以内に配置

### VRCMirrorReflection
- **名前空間**: `VRC.SDK3.Components`
- **役割**: ワールド内ミラー
- **必須**: MeshRenderer
- **主要プロパティ**: `m_ReflectLayers` (LayerMask)
- **パフォーマンス**: デフォルト OFF 推奨。低品質/高品質の 2 段階切り替えが定石

### VRCObjectPool
- **名前空間**: `VRC.SDK3.Components`
- **役割**: ネットワーク同期オブジェクトプール
- **API**: `TryToSpawn()` — オーナーのみ
- **用途**: 弾丸、ドロップアイテム、動的生成系ギミック

### その他のコンポーネント
- `VRCAvatarPedestal` — アバター試着台
- `VRCPortalMarker` — ポータル
- `VRCSpatialAudioSource` — 空間オーディオ
- `VRCUiShape` — UI インタラクション
- `VRCPlayerObject` — プレイヤー永続化
- `VRCEnablePersistence` — 永続化有効化
- `VRCVisualDamage` — ビジュアルダメージ

## L2: レイヤー構成

- **自動セットアップ**: Builder タブ > "Setup Layers for VRChat" + "Set Collision Matrix"
- **アップロード時上書き**: Layer 0-21 と Collision Matrix は VRChat デフォルトで上書き
- **カスタム可能**: Layer 22-31 のみ永続
- **実用ポイント**:
  - 環境メッシュ: Default (0) or Environment (11)
  - Pickup: Pickup (13) / PickupNoEnvironment (14)
  - すり抜け: Walkthrough (17)
  - カスタム: 22-31

## L2: ネットワーキング

詳細な同期パターン（オーナーシップ、帯域制限、Late Joiner、`[FieldChangeCallback]`）は `udonsharp-prefab-patterns.md` L2: ネットワーク同期パターン を参照。

ここではワールド固有のポイントのみ記載:
- VRC_ObjectSync は Transform 同期専用。変数同期は UdonBehaviour の `[UdonSynced]` を使う
- `OnPlayerJoined` でプレイヤー参加検知し、ワールド初期化ロジックを実行

## L2: 最適化ガイドライン

| カテゴリ | 推奨 |
|---------|------|
| Draw Calls | マテリアル削減、GPU Instancing 有効化、テクスチャアトラス |
| ポリゴン | Quest: ~250K 三角形目安、PC: オクルージョンカリングで節約 |
| ライティング | リアルタイムライト最小限（Quest は全廃推奨）、ベイク + ライトプローブ |
| オクルージョンカリング | 必ずベイク |
| ミラー | デフォルト OFF、レイヤーマスク最小化 |

## L2: Quest/Android 制限

| 制限 | 詳細 |
|------|------|
| シェーダー | ワールドは制限なし（ただしモバイル向け軽量推奨） |
| Post Processing | 完全無効 |
| Cloth | 完全無効 |
| リアルタイムライト | 動作するが極めて高コスト、実質使用不可 |
| ポリゴン | ~250K 三角形 |
| テクスチャ | ASTC 圧縮推奨、解像度小さめ |

## L2: ビルド・アップロード

### 手動手順
1. VRChat SDK > Show Control Panel
2. Authentication で VRChat ログイン
3. Builder で Layers / Collision Matrix 自動セットアップ
4. ワールド名、キャパシティ設定
5. Build & Test（ローカル）or Build and Upload（公開）

### Prefab Sentinel 連携
- `vrcsdk_upload` ツール: 既存 blueprint_id の更新のみ（新規作成は不可）
- `platforms: ["windows", "android"]` で複数プラットフォーム対応
- **制約**: VRCSDKUploadHandler が Avatar SDK を参照しており、World 専用プロジェクトではコンパイルエラー（要修正）

## L2: ClientSim（ローカルテスト）

- **パッケージ**: `com.vrchat.clientsim`（World テンプレートに同梱）
- **起動**: Play Mode で自動起動。Escape でゲーム内設定
- **自動テスト**: `ClientSimTestBase` 継承、Domain Reloading 無効が必須
- **Prefab Sentinel 連携**: `editor_run_tests` で ClientSim テスト実行、`editor_console` でエラー取得

## L2: SDK 3.10.0 重要変更 — Dynamics in Worlds

> 注: この機能は SDK 3.10.0+ が必要。version_tested (3.7+) では未検証。ドキュメントベースの情報。

- PhysBone / Contact / VRC Constraint がワールドでも使用可能に
- `Object.Instantiate()` した PhysBone が正常シミュレーション
- Contact Receiver で `OnContactEnter(ContactEnterInfo)` イベント発火
- VRCPlayerObject 上の PhysBone / Contact / Constraint も動作

## 実運用で学んだこと

(Phase 3-4 の検証で追記する)
