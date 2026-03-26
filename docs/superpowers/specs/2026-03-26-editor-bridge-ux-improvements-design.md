# Editor Bridge UX 改善設計

**日付:** 2026-03-26
**由来:** prefab-sentinel 実用レビュー (`report_20260326_prefab_sentinel_review.md`) + ユーザーフィードバック
**アプローチ:** レイヤー別ボトムアップ（C# → Python → ドキュメント）

---

## 概要

Editor Bridge のカメラ API 設計ミスマッチ、エラーメッセージ不親切さ、ドキュメント散在を一括改善する。コード変更 4 件 + ドキュメント整備 3 件。

---

## 1. editor_set_camera API 統合

### 問題

- Mode A (`position`/`rotation`/`size`) と Mode B (`pivot`/`yaw`/`pitch`/`distance`) が実質同じ処理なのに別名
- Mode A の `position` が実は `sceneView.pivot` を設定（名前と動作の乖離）
- 本当のカメラワールド座標指定がない

### 設計

旧 Mode A/B を廃止し、単一パラメータセット + position モードに統合する。

**パラメータ（全てオプション、指定したものだけ更新）:**

| パラメータ | 型 | 説明 |
|-----------|-----|------|
| `pivot` | `{x,y,z}` | 注視点（SceneView.pivot） |
| `yaw` | float | 水平角。0=正面(+Z方向を見る) |
| `pitch` | float | 垂直角 |
| `distance` | float | SceneView.size（小さいほどズーム） |
| `orthographic` | bool | 正射投影切替 |
| `position` | `{x,y,z}` | カメラのワールド座標 **（新規）** |
| `look_at` | `{x,y,z}` | 注視先 **（新規）** |

**モード解決ルール:**

1. `position` + `look_at` → pivot=look_at、rotation と distance を逆算
2. `position` + `yaw`/`pitch`（look_at なし）→ pivot を `position + rotation * distance` で逆算
3. `pivot` + `yaw`/`pitch`/`distance`（position なし）→ 旧 Mode B と同等
4. `position` + `pivot` 同時指定 → エラー（`EDITOR_CTRL_CAMERA_CONFLICT`）

**position モードの逆算ロジック（C# 側）:**

```
// position + look_at
Vector3 direction = (look_at - position).normalized;
float euclidean = Vector3.Distance(position, look_at);
// SceneView の内部関係: cameraDistance = size / sin(FoV/2)
// 逆算: size = euclidean * sin(FoV/2)
// FoV は SceneView.cameraSettings.fieldOfView（デフォルト 60°）から取得
float fov = sceneView.cameraSettings.fieldOfView;
float size = euclidean * Mathf.Sin(fov * 0.5f * Mathf.Deg2Rad);
Quaternion rotation = Quaternion.LookRotation(direction);
pivot = look_at;

// position + yaw/pitch (look_at なし)
Quaternion rotation = Quaternion.Euler(pitch, internalYaw, 0);
// pivot = position + rotation * Vector3.forward * cameraDistance
// cameraDistance は現在の sceneView.size から導出
pivot = position + rotation * new Vector3(0, 0, cameraDistance);
```

**レスポンス変更:**

```json
{
  "previous": {
    "pivot": {"x": 0, "y": 1.3, "z": 0},
    "yaw": 345, "pitch": 8,
    "distance": 0.28,
    "orthographic": false
  },
  "current": {
    "pivot": {"x": -0.12, "y": 1.3, "z": 0},
    "yaw": 345, "pitch": 8,
    "distance": 0.28,
    "orthographic": false
  }
}
```

`previous` は変更適用前の get_camera 結果。呼び出し元が元の状態に戻せる。

### 破壊的変更

- `position` の意味が変わる: 旧=sceneView.pivot、新=カメラワールド座標
- `rotation` 配列パラメータ廃止 → `yaw`/`pitch` 個別指定に統一
- `size` → `distance` にリネーム
- 旧パラメータの互換は設けない（外部ユーザーなし）

---

## 2. Repaint 強化 + recompile の refresh 自動化

### 問題

- `set_camera` 後に Unity がバックグラウンドだとカメラ位置が反映されず、フォーカス復帰時にジャンプする
- `editor_recompile` が AssetDatabase.Refresh を呼ばないため、C# ファイルコピー後に recompile だけでは反映されない

### 設計: Repaint 強化

`set_camera` ハンドラの末尾で、現在の単発 `Repaint()` を以下に置き換える:

```csharp
sceneView.Repaint();
SceneView.RepaintAll();
InternalEditorUtility.RepaintAllViews();
EditorApplication.delayCall += () => {
    sceneView.Repaint();
    SceneView.RepaintAll();
};
```

即時 Repaint + 1フレーム遅延 Repaint の2段構え。`RepaintAllViews()` で全ビューを巻き込む。

**適用対象ハンドラ:**

| ハンドラ | 理由 |
|---------|------|
| `set_camera` | カメラジャンプ防止 |
| `frame_selected` | frame 直後のスクショが古い画面になる防止 |
| `set_blend_shape` | BlendShape 変更の即時反映 |
| `set_material_property` | マテリアル変更の即時反映 |

### 設計: recompile の refresh 自動化

`recompile_scripts` ハンドラの先頭で `AssetDatabase.Refresh` を呼ぶ:

```csharp
// recompile_scripts handler
AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
EditorApplication.delayCall += () => {
    CompilationPipeline.RequestScriptCompilation();
};
```

`Refresh` は同期処理なので、完了後に `delayCall` でコンパイル要求が走る順序保証あり。Bridge C# コピー → `editor_recompile` 一発で反映される。

### Python 側

`editor_set_camera` の使い方ガイダンスから `editor_refresh` の手動呼び出し案内を削除（C# 側で自動化されるため）。

### 検証方針

Repaint 強化が不十分な場合（バックグラウンドで依然として反映されない場合）は、次バッチで Win32 `SetForegroundWindow` のオプション対応を検討する。

---

## 3. editor_frame の bounds 情報返却

### 問題

`SkinnedMeshRenderer` の Bounds 中心がメッシュ原点（足元 y≈0）になることがあり、AI エージェントが「なぜ足元を向いているのか」を判断できない。

### 設計

`frame_selected` ハンドラで、フレーミング後に選択オブジェクトの Bounds 情報とカメラ状態をレスポンスに含める:

```json
{
  "success": true,
  "bounds_center": { "x": 0.0, "y": 0.05, "z": 0.0 },
  "bounds_extents": { "x": 0.3, "y": 0.8, "z": 0.2 },
  "camera": {
    "pivot": {"x": 0, "y": 0.05, "z": 0},
    "yaw": 0, "pitch": 0,
    "distance": 1.2,
    "orthographic": false
  }
}
```

**C# 実装:**

```csharp
Renderer renderer = selectedGo.GetComponentInChildren<Renderer>();
if (renderer != null) {
    response.bounds_center = renderer.bounds.center;  // ワールド座標 AABB
    response.bounds_extents = renderer.bounds.extents;
}
// フレーミング後のカメラ状態も返す
response.camera = GetCameraState(sceneView);
```

AI 側は全軸の bounds 情報から:
- y: 足元/中心の判断
- x,z: 左右・前後の偏り
- extents: サイズ感

を判断し、`editor_set_camera` で pivot を補正できる。`camera` フィールドにより get_camera の追加呼び出しも不要。

---

## 4. SupportedActions エラーメッセージ改善

### 問題

EditorBridge.cs のディスパッチで、どの Bridge の SupportedActions にもマッチしないアクションが PatchBridge にフォールスルーし、「プロトコルバージョン不一致」という見当違いのエラーになる。

### 設計

PatchBridge へのフォールスルーを廃止し、明示的なルーティング + 未知アクションエラーに変更:

```csharp
if (RuntimeValidationBridge.SupportedActions.Contains(action))
    return RuntimeValidationBridge.RunFromPaths(req, res);
if (EditorControlBridge.SupportedActions.Contains(action))
    return EditorControlBridge.RunFromPaths(req, res);
if (PatchBridge.SupportedActions.Contains(action))
    return PatchBridge.RunFromPaths(req, res);

// どこにもマッチしない
WriteErrorResponse(res,
    $"Unknown action '{action}'. " +
    $"EditorControlBridge supports: [{string.Join(", ", EditorControlBridge.SupportedActions)}]. " +
    "Bridge C# scripts may need updating.");
```

Python 側の変更は不要（エラーレスポンスの message フィールドが改善されるだけ）。

---

## 5. ドキュメント整備

### 方針

guide スキルを **リファレンス専用** に絞り、実践知識を knowledge に分離する。

**guide に残す（リファレンス）:**
- ツール一覧・パラメータ仕様
- パッチスキーマ書式
- Bridge セットアップ手順
- エラーコード一覧

**knowledge に移す（実践知識）:**

| ファイル | 内容 |
|---------|------|
| `prefab-sentinel-editor-camera.md`（既存） | カメラ操作パターン（API 変更に合わせて更新） |
| `prefab-sentinel-material-operations.md`（新規） | マテリアル操作パターン（liltoon カラー変更、テクスチャ差し替え、float 調整） |
| `prefab-sentinel-patch-patterns.md`（新規） | パッチ計画の実例集 |
| `prefab-sentinel-wiring-triage.md`（新規） | 配線検査の読み方・対処法 |
| `prefab-sentinel-variant-patterns.md`（新規） | Variant 操作の実践パターン |

guide 内の既存の実践的記述（worked examples、tips、推奨ワークフロー）を特定し、該当する knowledge ファイルに移動。guide 側にはリンクのみ残す。

### Bridge セットアップ手順（guide に追加）

```
## Bridge セットアップ

### 1. C# スクリプトの転送
tools/unity/ 配下の以下を Unity プロジェクトの Assets/Editor/ にコピー:
- PrefabSentinel.EditorBridge.cs
- PrefabSentinel.EditorControlBridge.cs
- PrefabSentinel.UnityPatchBridge.cs
- PrefabSentinel.UnityRuntimeValidationBridge.cs

### 2. 環境変数
- UNITYTOOL_BRIDGE_MODE=editor
- UNITYTOOL_BRIDGE_WATCH_DIR=<Unity プロジェクト内の監視ディレクトリ>
- UNITYTOOL_UNITY_TIMEOUT_SEC=30（任意）

### 3. 更新手順
C# を上書きコピー → editor_recompile（refresh + recompile が自動実行される）
```

### README 整理

README の Bridge 詳細を guide へのリンクに置き換え、重複を排除。

---

## 実装順序

レイヤー別ボトムアップ:

| ステップ | 層 | 内容 |
|---------|-----|------|
| 1 | C# | set_camera モード統合 + position 逆算、frame bounds 返却、Repaint 強化、recompile refresh 自動化、SupportedActions エラー改善 |
| 2 | Python | MCP パラメータ変更、previous/current レスポンス、bounds フィールド追加 |
| 3 | ドキュメント | guide リファレンス化、knowledge 分離、Bridge セットアップ追加、README 整理 |

---

## スコープ外

- Win32 フォーカス強制（Repaint 強化で不十分な場合に次バッチで検討）
- open-mode での add_component 対応
- UdonSharp backing 自動作成
- Game ビューカメラ制御
- 旧パラメータの後方互換
