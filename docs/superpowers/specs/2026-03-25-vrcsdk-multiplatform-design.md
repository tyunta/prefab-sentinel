# Phase 4: VRCSDK マルチプラットフォームアップロード — Design Spec

## 背景

Phase 3 で `vrcsdk_upload` MCP ツールを実装したが、ビルドターゲットは Unity Editor の現在設定に依存しており、複数プラットフォーム（Windows / Android / iOS）への順次アップロードができない。VRChat はこの 3 プラットフォームをサポートしており、アバター/ワールドのクロスプラットフォーム対応には手動でのターゲット切替→ビルド→アップロードの繰り返しが必要だった。

## スコープ

既存の `vrcsdk_upload` MCP ツールに `platforms` パラメータを追加し、C# Handler 内でプラットフォーム順次切替→ビルド→アップロードを実行する。

### スコープ内

- `platforms` パラメータの追加（Python MCP + C# Bridge）
- プラットフォーム切替 (`EditorUserBuildSettings.SwitchActiveBuildTarget`)
- 順次ビルド+アップロード（途中失敗で停止）
- 元のビルドターゲットへの復元（`try/finally`）
- per-platform 結果の構造化レスポンス

### スコープ外

- サムネイル撮影
- バージョニング（description 内の `(vN)` 自動更新）
- 新規アセット作成（`blueprint_id` は引き続き必須）
- リトライ機構
- プログレスストリーミング（中間フィードバック）

## MCP ツール変更

### パラメータ追加

既存の `vrcsdk_upload` に `platforms` パラメータを追加:

```python
def vrcsdk_upload(
    target_type: str,
    asset_path: str,
    blueprint_id: str,
    platforms: list[str] | None = None,  # 追加。None → ["windows"]
    description: str = "",
    tags: str = "",
    release_status: str = "",
    confirm: bool = False,
    change_reason: str = "",
    timeout_sec: int = 600,
) -> dict[str, Any]:
```

`None` センチネルを使う（Python のミュータブルデフォルト引数の問題を回避）。関数冒頭で `if platforms is None: platforms = ["windows"]` とする。

### Python 側バリデーション

- `platforms` が空リスト → `VRCSDK_INVALID_PLATFORMS` エラー
- 無効なプラットフォーム名 → `VRCSDK_INVALID_PLATFORMS` エラー（有効値: `"windows"`, `"android"`, `"ios"`）
- 重複エントリ → `VRCSDK_INVALID_PLATFORMS` エラー
- バリデーション通過後、`send_action()` に `platforms=json.dumps(platforms)` で JSON 文字列として渡す（既存の `confirm` 等と同じく kwargs で Bridge に透過的に渡す）

### レスポンス変換（Python 側）

`send_action()` の戻り値を直接 return せず、MCP ツール内でポスト処理する:

```python
result = send_action(
    action="vrcsdk_upload",
    timeout_sec=timeout_sec,
    ...,
    platforms=json.dumps(platforms),
)
# C# が platform_results_json に JSON 文字列で格納したものをパースして差し替え
if "platform_results_json" in result.get("data", {}):
    result["data"]["platform_results"] = json.loads(result["data"].pop("platform_results_json"))
# dry-run 時は Python 側で platforms をエコーバック（C# EditorControlData に専用フィールドを増やさない）
if not confirm:
    result["data"]["platforms"] = platforms
return result
```

この方式により:
- `platform_results` は C# → JSON 文字列 → Python でパースして構造化
- dry-run の `platforms` エコーバックは Python 側で注入（`EditorControlData` に `platforms` フィールドを追加しない）
- `timeout_sec` のデフォルトは 600s（1 プラットフォーム向け）。複数プラットフォーム時は呼び出し側が `600 * len(platforms)` 程度を指定することを推奨

### レスポンス形式

注: レスポンスの `data.platforms`（dry-run 時）は Python 側で注入する。`data.platform_results`（confirm 時）は C# の `platform_results_json` を Python でパースして差し替える。詳細は上記「レスポンス変換」セクション参照。

#### dry-run (`confirm=False`)

```json
{
  "success": true,
  "severity": "info",
  "code": "VRCSDK_VALIDATED",
  "message": "Validation passed for avatar at Assets/Avatars/MyAvatar.prefab",
  "data": {
    "target_type": "avatar",
    "asset_path": "Assets/Avatars/MyAvatar.prefab",
    "blueprint_id": "avtr_xxx...",
    "phase": "validated",
    "platforms": ["windows", "android"],
    "elapsed_sec": 0,
    "executed": false
  },
  "diagnostics": []
}
```

dry-run ではプラットフォーム切替を行わない（切替だけで数分のインポート再処理が走るため）。

#### confirm 成功

```json
{
  "success": true,
  "severity": "info",
  "code": "VRCSDK_UPLOAD_OK",
  "message": "Uploaded avatar to 2 platforms in 92.3s",
  "data": {
    "target_type": "avatar",
    "asset_path": "Assets/Avatars/MyAvatar.prefab",
    "blueprint_id": "avtr_xxx...",
    "phase": "complete",
    "elapsed_sec": 92.3,
    "executed": true,
    "platform_results": [
      {"platform": "windows", "success": true, "elapsed_sec": 45.1},
      {"platform": "android", "success": true, "elapsed_sec": 47.2}
    ],
    "original_target_restored": true
  },
  "diagnostics": []
}
```

#### confirm 失敗（途中停止）

```json
{
  "success": false,
  "severity": "error",
  "code": "VRCSDK_BUILD_FAILED",
  "message": "Build failed for platform 'android'",
  "data": {
    "target_type": "avatar",
    "asset_path": "Assets/Avatars/MyAvatar.prefab",
    "blueprint_id": "avtr_xxx...",
    "phase": "failed",
    "elapsed_sec": 55.0,
    "executed": true,
    "platform_results": [
      {"platform": "windows", "success": true, "elapsed_sec": 45.1},
      {"platform": "android", "success": false, "elapsed_sec": 9.9, "error": "Shader compilation error..."},
      {"platform": "ios", "skipped": true}
    ],
    "original_target_restored": true
  },
  "diagnostics": []
}
```

### `platform_results` フィールド仕様

各エントリは以下のいずれか:

- 成功: `{"platform": "...", "success": true, "elapsed_sec": N}`
- 失敗: `{"platform": "...", "success": false, "elapsed_sec": N, "error": "..."}`
- スキップ: `{"platform": "...", "skipped": true}`

`platform_results` は `confirm=True` 時のみ含まれる。dry-run では含まれない。

### `platform_results_json` の C# 側生成

C# の `EditorControlData` は `JsonUtility` の制約でネストしたオブジェクト配列を直接格納できない。`platform_results_json` フィールドに手動で JSON 文字列を構築して格納する:

```csharp
// C# 側で手動 JSON 構築
var sb = new System.Text.StringBuilder("[");
for (int i = 0; i < results.Count; i++)
{
    if (i > 0) sb.Append(",");
    var r = results[i];
    if (r.skipped)
        sb.Append($"{{\"platform\":\"{r.platform}\",\"skipped\":true}}");
    else if (r.success)
        sb.Append($"{{\"platform\":\"{r.platform}\",\"success\":true,\"elapsed_sec\":{r.elapsed_sec:F1}}}");
    else
        sb.Append($"{{\"platform\":\"{r.platform}\",\"success\":false,\"elapsed_sec\":{r.elapsed_sec:F1},\"error\":\"{EscapeJson(r.error)}\"}}");
}
sb.Append("]");
data.platform_results_json = sb.ToString();
```

Python 側での変換は「レスポンス変換（Python 側）」セクション参照。

## C# 側変更

### EditorControlRequest

```csharp
public string platforms = string.Empty;  // JSON array string: "[\"windows\",\"android\"]"
```

### EditorControlData

```csharp
public string platform_results_json = string.Empty;  // JSON string of per-platform results
public bool original_target_restored = false;
```

### プラットフォームマッピング

`VRCSDKUploadHandler` に静的メソッドを追加:

```csharp
private static BuildTarget ToBuildTarget(string platform) => platform switch
{
    "windows" => BuildTarget.StandaloneWindows64,
    "android" => BuildTarget.Android,
    "ios"     => BuildTarget.iOS,
    _         => throw new ArgumentException($"Unknown platform: {platform}")
};

private static BuildTargetGroup ToBuildTargetGroup(string platform) => platform switch
{
    "windows" => BuildTargetGroup.Standalone,
    "android" => BuildTargetGroup.Android,
    "ios"     => BuildTargetGroup.iOS,
    _         => throw new ArgumentException($"Unknown platform: {platform}")
};
```

### Handle() メソッドの変更

```
1. 既存バリデーション（target_type, asset_path, blueprint_id, login）
2. platforms パース: 空/未指定 → ["windows"]
3. confirm=false → 既存の dry-run レスポンス（platforms エコーバックは Python 側で注入）
4. confirm=true:
   a. 元のビルドターゲットを保存: var originalTarget = EditorUserBuildSettings.activeBuildTarget
   b. platform_results リストを初期化
   c. bool failed = false;
      try {
        foreach (platform in platforms) {
          per-platform Stopwatch 開始
          // SwitchActiveBuildTarget は bool を返す（例外ではない）
          bool switched = EditorUserBuildSettings.SwitchActiveBuildTarget(
              ToBuildTargetGroup(platform), ToBuildTarget(platform));
          if (!switched) {
            現在の platform を失敗として記録 (error: "Platform switch failed")
            failed = true; break;
          }
          BuildAndUpload(request)  // 既存メソッド再利用（例外は catch で捕捉）
          platform_results に成功を記録
        }
      } catch (Exception ex) {
        現在の platform を失敗として記録 (error: ex.Message)
        failed = true;
      } finally {
        // 失敗した platform の後にある platforms を skipped として記録
        残りの未処理 platforms を skipped として記録
        // 元のビルドターゲットに復元
        original_target_restored = EditorUserBuildSettings.SwitchActiveBuildTarget(
            EditorUserBuildSettings.GetBuildTargetGroup(originalTarget), originalTarget);
      }
   d. platform_results_json を構築して data に格納
   e. EditorControlData に original_target_restored を設定
   f. 成功/失敗に応じたレスポンスを返す
      - 失敗時は BuildError(code, message, data) オーバーロードを使い、
        populated な EditorControlData（platform_results_json, original_target_restored 含む）を渡す
```

### プラットフォーム切替の注意点

- `EditorUserBuildSettings.SwitchActiveBuildTarget()` は `bool` を返す同期 API。`false` 返却時は切替失敗（例外は投げない）。
- 切替時にアセットの再インポートが走り、通常数分かかる。Unity Editor をブロックする。
- `timeout_sec` は呼び出し側がプラットフォーム数に応じて調整する必要がある（デフォルト 600s は 1 プラットフォーム向け。複数プラットフォーム時は `600 * len(platforms)` 程度を推奨）。

## エラーハンドリング

| 状態 | コード | 発生箇所 |
|------|--------|----------|
| `platforms` が空/無効値/重複 | `VRCSDK_INVALID_PLATFORMS` | Python 側バリデーション |
| プラットフォーム切替失敗 | `VRCSDK_PLATFORM_SWITCH_FAILED` | C# `SwitchActiveBuildTarget` |
| ビルド失敗 | `VRCSDK_BUILD_FAILED` | C# 既存コード |
| アップロード失敗 | `VRCSDK_UPLOAD_FAILED` | C# 既存コード |
| 復元失敗 | `original_target_restored: false` | C# finally ブロック |

- 復元失敗はエラーコードを返さず、`original_target_restored: false` フラグで通知する。ビルド/アップロード自体の成否は `success` フィールドで判定する。
- プラットフォーム切替失敗は `platform_results` に `{"platform": "...", "success": false, "elapsed_sec": N, "error": "Platform switch failed"}` として記録される（ビルド失敗と同じ形式、`elapsed_sec` は切替にかかった時間）。
- 失敗・エラー時のレスポンスは `BuildError(code, message, data)` オーバーロード（Phase 5.3 で追加済み）を使い、`platform_results_json` と `original_target_restored` を含む `EditorControlData` を渡す。

## 影響範囲

| ファイル | 変更内容 |
|----------|----------|
| `prefab_sentinel/mcp_server.py` | `platforms` パラメータ追加、バリデーション、レスポンス内 `platform_results_json` → `platform_results` 変換 |
| `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` | `EditorControlRequest.platforms` フィールド追加、`EditorControlData.platform_results_json` / `original_target_restored` フィールド追加 |
| `tools/unity/PrefabSentinel.VRCSDKUploadHandler.cs` | プラットフォームループ、`ToBuildTarget` / `ToBuildTargetGroup` マッピング、ビルドターゲット保存/復元、per-platform 結果収集 |
| `tests/test_mcp_server.py` | `platforms` バリデーションテスト、レスポンス変換テスト |
| `README.md` | マルチプラットフォームの記述追加 |

## やらないこと

- サムネイル撮影
- バージョニング
- 新規アセット作成
- リトライ機構
- プログレスストリーミング
- `timeout_sec` の自動スケール（呼び出し側の責務。複数プラットフォーム時は `600 * len(platforms)` を推奨）
- プラットフォーム切替時の再インポート最適化

## テスト

### Python（自動）

| テスト | 内容 |
|--------|------|
| `platforms` バリデーション | 空リスト→エラー、無効値→エラー、重複→エラー |
| デフォルト `platforms` | パラメータ未指定時に `["windows"]` が Bridge に渡される |
| `platform_results_json` 変換 | JSON 文字列 → `platform_results` リストに変換される |
| 既存テスト | `vrcsdk_upload` の既存テストが引き続き pass（後方互換） |

### C#（手動、Editor Bridge 必要）

| テスト | 手順 |
|--------|------|
| 単一プラットフォーム | `platforms: ["windows"]` → 既存と同じ挙動 |
| 複数プラットフォーム | `platforms: ["windows", "android"]` → 順次ビルド+アップロード |
| 途中失敗 | 意図的にビルドエラーを起こし、残りが skipped になることを確認 |
| ターゲット復元 | アップロード後、元のビルドターゲットに戻ることを確認 |

## 検証基準

1. 全ユニットテスト pass（既存 + 新規）
2. `platforms` 未指定で既存の `vrcsdk_upload` と同じ挙動（後方互換）
3. C# コンパイルチェック pass
4. `platform_results` がレスポンスの `data` に正しく含まれる
