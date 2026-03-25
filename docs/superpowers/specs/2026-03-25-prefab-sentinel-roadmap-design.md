# Prefab Sentinel Roadmap: Bridge 強化 / Material 編集 / VRCSDK アップロード

## 背景

2026-03-22〜25 のアバター調整作業（髪色チューニング・グラデーション適用）と MCP 評価レポートから、以下の主要ギャップが判明した:

1. **Material (.mat) の書き込みパスが完全に欠落** — `set_property` / `patch_apply` いずれも .mat に対して動作せず、YAML 直接編集にフォールバックが必要だった
2. **カメラ制御が GET 不可・軸が非直感的** — yaw=0 が背面、pivot 指定不可、position 読み取り不可
3. **書き込み後の Editor Refresh が手動** — YAML 書き換え後に毎回 `editor_refresh()` を呼ぶ必要がある
4. **VRCSDK アップロード手段がない** — ビルド→アップロードを MCP 経由で実行できない

本設計は上記を 5 Phase に分解し、依存順に消化する全体ロードマップを定義する。

## Phase 構成と依存関係

```
Phase 1: Editor Bridge 基盤強化        ← 最初に着手
  ├─ カメラ GET/SET (6DoF + pivot)
  ├─ 書き込み後 auto-refresh
  └─ Bridge 接続ステータス改善

Phase 2: Material 編集パス             ← Phase 1 (auto-refresh) に依存
  ├─ editor_set_material_property (ランタイム)
  ├─ set_material_property (オフライン YAML)
  └─ get_unity_symbols .mat 対応

Phase 3: VRCSDK アップロード基盤       ← Phase 1 (Bridge) に依存
  ├─ Unity 側 VRCSDKUploadHandler
  ├─ vrcsdk_upload MCP ツール
  └─ リトライ + エラーハンドリング

Phase 4: VRCSDK アップロード拡張       ← Phase 3 に依存
  ├─ マルチプラットフォーム
  ├─ サムネイル撮影
  └─ バージョニング

Phase 5: DX 改善                      ← いつでも (独立)
  ├─ patch_apply ドキュメント
  ├─ エラーヒント
  └─ パラメータ命名一貫性
```

Phase 1 → 2 → 3 は直列。Phase 4 は 3 の後。Phase 5 は独立。

---

## Phase 1: Editor Bridge 基盤強化

### 1.1 カメラ GET/SET の再設計

#### 現状の問題

- `editor_camera` は SET のみ（GET なし）
- yaw=0 が背面（直感に反する）
- pivot 指定不可、position 直接指定不可

#### 新設計

`editor_camera` を廃止し、2 つのツールに分離する。

**`editor_get_camera()`**

パラメータなし。レスポンス:

```json
{
  "position": {"x": 0.0, "y": 1.0, "z": -5.0},
  "rotation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
  "euler": {"yaw": 0.0, "pitch": 0.0, "roll": 0.0},
  "pivot": {"x": 0.0, "y": 0.0, "z": 0.0},
  "size": 10.0,
  "orthographic": false
}
```

**`editor_set_camera(...)`**

2 つのモードを受け付ける。両方同時指定はエラー。

Mode A — 絶対座標:
| パラメータ | 型 | デフォルト | 説明 |
|---|---|---|---|
| `position` | `str` (JSON `{x,y,z}`) | 省略=変更なし | ワールド座標 |
| `rotation` | `str` (JSON `{yaw,pitch,roll}`) | 省略=変更なし | Euler 角 |
| `size` | `float` | 省略=変更なし | SceneView zoom level |
| `orthographic` | `bool` | 省略=変更なし | 正射影切替 |

Mode B — pivot 周回:
| パラメータ | 型 | デフォルト | 説明 |
|---|---|---|---|
| `pivot` | `str` (JSON `{x,y,z}`) | 省略=変更なし | 回転中心 |
| `yaw` | `float` | 省略=変更なし | 水平回転 (0=正面) |
| `pitch` | `float` | 省略=変更なし | 垂直回転 |
| `distance` | `float` | 省略=変更なし | pivot からの距離 |
| `orthographic` | `bool` | 省略=変更なし | 正射影切替 |

**軸修正:** yaw=0 を正面 (Unity +Z 方向を見る) に修正。内部で 180° オフセット。

**後方互換:** 旧 `editor_camera` は削除。外部ユーザー不在のため破壊的変更を許容。

#### 実装箇所

- Python: `mcp_server.py` — 旧 `editor_camera` 削除、新 `editor_get_camera` / `editor_set_camera` 追加
- Python: `editor_bridge.py` — `send_action` に `get_camera` / `set_camera` アクション追加
- Unity: `UnityEditorControlBridge.cs` — `HandleGetCamera` / `HandleSetCamera` 追加、旧 `HandleCamera` 削除
- Unity: `EditorBridgeWindow.cs` — `SupportedActions` に新アクション追加

### 1.2 書き込み後 auto-refresh

#### 対象ツール

`set_property`, `add_component`, `remove_component`, `patch_apply(confirm=true)`, `revert_overrides`

#### 実装方針

- orchestrator に `_maybe_auto_refresh()` ヘルパーを追加
- 各対象ツールの `confirm=True` 成功パス末尾で呼び出し
- Bridge 接続確認 → 接続あり: `send_action("refresh_asset_database")` → 接続なし: スキップ
- レスポンスに `auto_refresh: true | false | "skipped"` フィールドを追加

#### 判定ロジック

```python
def _maybe_auto_refresh(self) -> str:
    """Returns 'true', 'false', or 'skipped'."""
    if not self._bridge_available():
        return "skipped"
    try:
        send_action("refresh_asset_database")
        return "true"
    except BridgeError:
        return "false"
```

注: `send_action` および orchestrator は同期 API。async は使わない。

#### 対象外

Editor Bridge 経由の mutation (`editor_instantiate`, `editor_set_material`, `editor_delete`) は Unity メモリ上の直接操作のため、AssetDatabase.Refresh は不要。

### 1.3 Bridge 接続ステータス改善

#### `get_project_status` の拡張

レスポンスに以下を追加:

```json
{
  "bridge": {
    "connected": true,
    "mode": "editor",
    "watch_dir": "/path/to/watch",
    "last_response_ms": 45
  }
}
```

`connected` は環境変数の存在 + watch_dir の存在チェック。実際の疎通確認は行わない（コスト回避）。

#### エラーメッセージ統一

Bridge 必須ツール (`editor_*` 全般、auto-refresh) で接続失敗時:

```
"Editor Bridge not connected. Set UNITYTOOL_BRIDGE_MODE=editor and UNITYTOOL_BRIDGE_WATCH_DIR=<path>. See README 'Unity Bridge セットアップ' section."
```

---

## Phase 2: Material 編集パス

### 2.1 `editor_set_material_property` (ランタイム編集)

Editor 上でシェーダーパラメータを即座に変更する。スクショ→調整の反復ループ用。

#### MCP ツール

```
editor_set_material_property(
  hierarchy_path: str,       # Renderer を持つ GameObject
  material_index: int,       # マテリアルスロット番号
  property_name: str,        # シェーダープロパティ名 (例: "_MainGradationStrength")
  value: str,                # JSON: 数値, [r,g,b,a], "guid:..."
)
```

#### Unity 側

`UnityEditorControlBridge` に `HandleSetMaterialProperty` を追加。

型の自動判定:
- 整数 → `Material.SetInt()`
- 数値 (小数) → `Material.SetFloat()`
- 4 要素配列 → `Material.SetColor()` / `Material.SetVector()`（シェーダー定義から判定）
- 文字列 (GUID) → `AssetDatabase.LoadAssetAtPath<Texture>()` → `Material.SetTexture()`
- Undo 対応: `Undo.RecordObject(material, "Set Material Property")`

#### Bridge アクション

`set_material_property` アクションを `SUPPORTED_ACTIONS` に追加。

### 2.2 `set_material_property` (オフライン YAML 編集)

.mat ファイルの `m_SavedProperties` を直接編集する。Bridge 不要。

#### MCP ツール

```
set_material_property(
  asset_path: str,           # .mat ファイルパス
  property_name: str,        # "_MainGradationStrength"
  value: str,                # JSON: 数値, [r,g,b,a], {guid, fileID}
  confirm: bool = False,
  change_reason: str = ""
)
```

#### 実装方針

- 既存の `material_asset_inspector.py` が `m_SavedProperties` のパースロジックを持つ。書き込み用のメソッドを同モジュールに追加し、パース部分を再利用する（`SerializedObjectService` に重複実装しない）
- `m_SavedProperties` 内の `m_Floats` / `m_Ints` / `m_Colors` / `m_TexEnvs` の 4 カテゴリから `first` == property_name のエントリを特定
- `second` の値を書き換え
- dry-run (confirm=False): 対象エントリの現在値と変更後値をレスポンスで返す
- confirm=True: YAML 書き換え → auto-refresh

#### patch_apply との関係

Material 用の `patch_apply` 対応（root ハンドル暗黙解決）は本 Phase のスコープ外。`set_material_property` で単一プロパティ編集をカバーし、複数プロパティ一括変更の需要が確認された時点で検討する。

### 2.3 `get_unity_symbols` の .mat 対応

#### SymbolTree の拡張

Material ファイル検出時、以下のシンボルツリーを構築:

```
Material (fileID: 2100000, class_id: 21)
  ├─ _MainTex: {guid: "abc...", fileID: 2800000}
  ├─ _Color: {r: 1, g: 0.8, b: 0.6, a: 1}
  ├─ _MainGradationStrength: 0.5
  └─ ...
```

- `SymbolKind.COMPONENT` → `Material` ノード (root)
- `SymbolKind.PROPERTY` → 各シェーダープロパティ
- `find_unity_symbol("Material/_MainGradationStrength")` でアドレス可能

#### 実装箇所

- `symbol_tree.py` の `SymbolTree.build()` に Material 分岐を追加
- `m_SavedProperties` の 4 カテゴリ (`m_Floats`, `m_Ints`, `m_Colors`, `m_TexEnvs`) をパース
- 既存の `material_asset_inspector.py` のパースロジックを再利用

---

## Phase 3: VRCSDK アップロード基盤

### 3.1 アーキテクチャ

```
MCP Tool                    Bridge Protocol              Unity Handler
─────────────              ────────────────             ──────────────
vrcsdk_upload() ──JSON──→ {action: "vrcsdk_upload"} ──→ VRCSDKUploadHandler
                                                          ├─ Login check
                                                          ├─ Load avatar/world
                                                          ├─ Build bundle
                                                          ├─ Upload to VRC
                                                          └─ Response JSON
```

### 3.2 MCP ツール

```
vrcsdk_upload(
  target_type: "avatar" | "world",
  asset_path: str,              # Prefab パスまたは Scene パス
  blueprint_id: str = "",       # 既存 VRC アセット ID。空 = 新規作成
  name: str = "",               # 表示名 (新規作成時は必須)
  description: str = "",        # 説明文
  tags: list[str] = [],         # VRC タグ
  release_status: str = "private",
  confirm: bool = False,        # dry-run ゲート
  change_reason: str = ""       # 監査ログ
)
```

#### dry-run (confirm=False)

- VRC SDK ログイン状態確認
- アセット存在・型チェック (avatar → VRCAvatarDescriptor、world → VRC_SceneDescriptor)
- blueprint_id 有効性確認 (空でなければ VRC API 照合)
- ビルドは行わない

#### confirm=True

- ビルド → アップロード → 情報更新の完全フロー
- タイムアウト: 600s (通常の 30s から拡張)
- レスポンスの `data.phase` で段階報告: `"building"` → `"uploading_bundle"` → `"updating_info"` → `"complete"`

### 3.3 Unity 側 Handler

`PrefabSentinel.VRCSDKUploadHandler.cs` を新規作成。

```csharp
class VRCSDKUploadHandler
{
    // Entry point
    static BridgeResponse Handle(BridgeRequest request)

    // Workflow
    async Task<UploadResult> UploadAvatar(string prefabPath, string blueprintId, ...)
    async Task<UploadResult> UploadWorld(string scenePath, string blueprintId, ...)

    // Shared
    async Task<string> BuildBundle(GameObject root)   // IVRCSdkBuilderApi.Build()
    async Task UploadBundle(string path, string id)   // VRCApi
    bool ValidateLogin()                               // APIUser.IsLoggedIn
}
```

CAU から借用するロジック:
- `IVRCSdkAvatarBuilderApi` / `IVRCSdkWorldBuilderApi` の `Build()` パターン
- VRC API のレコード作成・更新フロー
- バンドルサイズバリデーション
- リトライ機構 (最大 3 回、指数バックオフ)

CAU から借用しないもの:
- EditorWindow UI
- ProgressAsset / レジューム
- サムネイル / バージョニング / Git タグ
- マルチプラットフォーム順次切替

注: VRC SDK API は `async Task` だが、Bridge の `Handle()` エントリは同期レスポンスを返す。Unity 側では `EditorApplication.delayCall` + コールバックパターン、または `.GetAwaiter().GetResult()` で同期化する。具体的なパターンは実装時に VRC SDK バージョンに合わせて決定する。

### 3.4 エラーハンドリング

| 状態 | severity | 対応 |
|---|---|---|
| VRC SDK 未ログイン | `error` | `"VRC SDK not logged in. Log in via VRChat SDK control panel"` |
| アセット不在 / 型不一致 | `error` | dry-run で検出 |
| ビルド失敗 | `error` | SDK エラーを `diagnostics` に転記 |
| アップロード失敗 | `error` | リトライ 3 回。全失敗なら error |
| タイムアウト (600s 超) | `critical` | 操作中断 |

### 3.5 VRC SDK 依存の分離

`VRCSDKUploadHandler.cs` は VRC SDK の型 (`VRCSdkControlPanel`, `VRCApi`, etc.) に直接依存する。SDK が Unity プロジェクトに導入されていない場合:

- `#if VRC_SDK_VRCSDK3` でコンパイル制御
- SDK 未導入プロジェクトでは Handler 自体がコンパイルから除外される
- MCP ツール側で SDK 不在時のエラーメッセージ: `"VRC SDK not found in project. Install VRChat SDK 3.x"`

---

## Phase 4: VRCSDK アップロード拡張 (概要)

Phase 3 の `vrcsdk_upload` に以下を段階追加:

- **マルチプラットフォーム:** `platforms: ["windows", "android"]` パラメータ。`EditorUserBuildSettings.SwitchActiveBuildTarget` → ビルド → アップロードを順次実行
- **サムネイル撮影:** `thumbnail_mode: "edit" | "play"` パラメータ。Preview Scene + RenderTexture で撮影、`VRCApi.UpdateAvatarImage()` でアップロード
- **バージョニング:** `version_increment: true` で description 内の `(vN)` → `(vN+1)` 自動更新

---

## Phase 5: DX 改善 (概要)

- `patch_apply` スキーマの README ドキュメント (annotated examples for material, prefab, scene)
- エラーメッセージへの "Did you mean...?" ヒント
- MCP パラメータ命名一貫性 (既存 spec: `2026-03-25-mcp-param-naming-design.md`)

---

## やらないこと

- `patch_apply` の Material root ハンドル暗黙解決 — `set_material_property` で十分カバー。需要確認後に検討
- BatchMode 対応のアップロード — VRC SDK API が Editor 前提のため非対応
- ContinuousAvatarUploader の直接統合 — UI 密結合のため、ロジックのみ参考にして新規 Handler を書く
- Phase 4 の先行実装 — YAGNI。Phase 3 完了後に着手

## テスト方針

### Phase 1

- Unit: カメラ GET/SET のパラメータ変換 (yaw オフセット、Mode A/B 排他)
- Integration: Bridge 経由のカメラ往復 (set → get → 値一致)
- Unit: `_maybe_auto_refresh()` の Bridge 接続/未接続パス

### Phase 2

- Unit: Material YAML パース (`m_SavedProperties` の 4 カテゴリ: `m_Floats`, `m_Ints`, `m_Colors`, `m_TexEnvs`)
- Unit: SymbolTree .mat 対応 (Material ノード構築、find_unity_symbol)
- Integration: `editor_set_material_property` → 既存 `editor_get_material_property` で値読み戻し → 一致確認 (注: `editor_get_material_property` は既に実装済みの読み取り専用ツール)
- Integration: `set_material_property(confirm=True)` → YAML 変更確認

### Phase 3

- Unit: dry-run バリデーション (型チェック、blueprint_id 照合)
- Integration: アバター/ワールドのビルド→アップロードフロー (VRC API モック)
- エラーパス: SDK 未ログイン、アセット不在、ビルド失敗、タイムアウト
