# deploy_bridge 旧ファイル清掃 + 除外機構 設計

**日付:** 2026-03-28
**由来:** `report_20260328_shiratsume_material_tuning.md` Bridge 更新セクションの問題

---

## 概要

`deploy_bridge` ツールに 2 つの改善を加える:

1. **旧ファイル検出・削除** — デプロイ前に `Assets/Editor/` 直下の旧 Bridge ファイルを検出・削除し、CS0101 重複定義エラーを防止
2. **VRCSDKUploadHandler のデフォルト除外** — SDK バージョン非互換のコンパイルエラーを防止

---

## 1: 旧ファイル検出・削除

### トリガー

デプロイ先 (`target_path`) が `Assets/Editor/PrefabSentinel/` の場合、親ディレクトリ (`Assets/Editor/`) 直下に `PrefabSentinel.*.cs` が残存していると CS0101 が発生する。

### 動作

1. デプロイ前に `target_path.parent` を走査（直下のみ、再帰しない）
2. `PrefabSentinel.*.cs` にマッチするファイルを収集
3. ターゲットディレクトリ内のファイルは除外（`target_path` 配下は対象外）
4. 該当ファイルを削除
5. レスポンスに結果を格納

### レスポンス

```python
"data": {
    "removed_old_files": ["Assets/Editor/PrefabSentinel.EditorBridge.cs", ...],
    "copied_files": [...],
    ...
}
```

```python
"diagnostics": [
    {
        "severity": "warning",
        "message": "Removed 3 old Bridge files from Assets/Editor/ to prevent CS0101 duplicate definitions",
    },
]
```

### スキャン範囲の制約

`target_path.parent` の直下のみ。再帰走査しない理由:
- 旧バージョンのデプロイ先は `Assets/Editor/` 直下だった
- 再帰するとユーザーが意図的に配置したファイルを誤削除するリスクがある

### 前提条件

`target_path.parent` が存在しない場合（初回デプロイ）はスキップする。

---

## 2: VRCSDKUploadHandler のデフォルト除外

### 問題

`PrefabSentinel.VRCSDKUploadHandler.cs` は VRC SDK の特定の API (`GetBuildTargetGroup`, `IVRCSdkWorldBuilderApi` 等) に依存する。SDK バージョンが合わないプロジェクトではコンパイルエラーになる。

### パラメータ

```python
def deploy_bridge(
    target_dir: str = "",
    include_upload_handler: bool = False,
) -> dict[str, Any]:
```

- `False` (デフォルト): `PrefabSentinel.VRCSDKUploadHandler.cs` をコピーしない
- `True`: 通常通りコピーする

### 除外ロジック

```python
_OPTIONAL_FILES = {"PrefabSentinel.VRCSDKUploadHandler.cs"}

for cs_file in sorted(plugin_tools.glob("*.cs")):
    if cs_file.name in _OPTIONAL_FILES and not include_upload_handler:
        skipped_files.append(cs_file.name)
        continue
    # ... copy
```

### レスポンス

```python
"data": {
    "skipped_files": ["PrefabSentinel.VRCSDKUploadHandler.cs"],
    ...
}
```

```python
"diagnostics": [
    {
        "severity": "info",
        "message": "Skipped VRCSDKUploadHandler.cs (optional, set include_upload_handler=true to deploy)",
    },
]
```

---

## 変更ファイル

| ファイル | 変更 |
|---------|------|
| `prefab_sentinel/mcp_server.py` | `deploy_bridge` に旧ファイル清掃 + `include_upload_handler` パラメータ追加 |
| `tests/test_mcp_server.py` | deploy_bridge テスト追加 |

## スコープ外

- ロールバック機構
- SDK バージョン判定ロジック
- 汎用的な exclude パラメータ（YAGNI — 問題ファイルは 1 件のみ）
- ツール数の変更（既存ツールの改修のみ）
