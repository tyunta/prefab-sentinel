# VRChat World SDK 3

version_tested: VRC SDK 3.7+ / ClientSim
last_updated: 2026-03-27
confidence: low (Phase 1 デスクリサーチのみ)

## L1: 基本概要

VRChat World SDK 3 は UdonSharp / Udon Graph でインタラクティブなワールドを構築するための SDK。

## L2: コアコンポーネント

### VRC_SceneDescriptor（必須 — 1シーンに1つ）
- **名前空間**: `VRC.SDKBase`
- **役割**: ワールド定義。存在しないとビルド不可
- **主要プロパティ**:
  - `spawns`: Transform[] — スポーン地点配列
  - `spawnOrder`: Default / Random / InOrder / Demo
  - `RespawnHeightY`: float — この Y 以下でリスポーン
  - `ObjectBehaviourAtRespawn`: Destroy / Respawn
  - `ReferenceCamera`: Camera — プレイヤーカメラ設定（クリッピング、Post Processing）

### VRC_ObjectSync
- **役割**: GameObject の Transform をネットワーク同期
- **必須**: 同一 GO に Rigidbody
- **主要プロパティ**:
  - `AllowCollisionOwnershipTransfer`: 衝突時オーナーシップ自動移譲
  - `SetKinematic()` / `SetGravity()`: Rigidbody は VRCObjectSync 経由で制御（直接変更不可）

### VRC_Pickup
- **役割**: オブジェクトを掴む/使う
- **必須**: Rigidbody + Collider。VRCObjectSync との併用が一般的
- **主要プロパティ**:
  - `InteractionText` / `UseText`: UI 表示テキスト
  - `proximity`: 掴める距離
  - `AutoHold`: true=グラブでアタッチ / false=離すとドロップ
  - `orientation`: ExactGun / ExactGrip / Any / None
- **Udon イベント**: `OnPickup`, `OnDrop`, `OnPickupUseDown/Up`

### VRC_Station
- **役割**: プレイヤーが座る/乗る
- **必須**: Collider (Is Trigger 推奨)
- **主要プロパティ**:
  - `stationEnterPlayerLocation` / `stationExitPlayerLocation`: Transform
  - `PlayerMobility`: Mobile / Immobilize / ImmobilizeForVehicle
  - `disableStationExit`: 自発的退出を禁止
  - `animatorController`: 着席アニメーション
- **制約**: Entry と Exit は 2m 以内に配置

### VRC_MirrorReflection
- **役割**: ワールド内ミラー
- **必須**: MeshRenderer
- **主要プロパティ**:
  - `m_ReflectLayers`: LayerMask — 反射するレイヤー（パフォーマンスの核）
- **パフォーマンス**: デフォルト OFF 推奨。低品質/高品質の 2 段階切り替えが定石

### VRCObjectPool
- **役割**: ネットワーク同期オブジェクトプール
- **API**: `TryToSpawn()` — オーナーのみ。プールから 1 つアクティブ化して返す
- **用途**: 弾丸、ドロップアイテム、動的生成系ギミック

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

### オーナーシップ
- 同期変数はオーナーのみ変更可能
- `Networking.SetOwner(player, obj)` で移譲
- `OnOwnershipRequest` で承認/拒否
- オーナー退出時は VRChat が自動割り当て
- 帯域上限: ~11 KB/秒

### Late Joiner
- 同期変数は Late Joiner に自動送信
- `OnDeserialization` が参加時に発火
- `OnPlayerJoined` でプレイヤー参加検知

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

- PhysBone / Contact / VRC Constraint がワールドでも使用可能に
- `Object.Instantiate()` した PhysBone が正常シミュレーション
- Contact Receiver で `OnContactEnter(ContactEnterInfo)` イベント発火
- VRCPlayerObject 上の PhysBone / Contact / Constraint も動作

## 実運用で学んだこと

(Phase 3-4 の検証で追記する)
