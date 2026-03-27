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

`target_path.parent` 直下に `PrefabSentinel.*.cs` が残存していると、`target_path` 内の同名ファイルと CS0101 重複定義エラーが発生する。典型例は旧デプロイ先 `Assets/Editor/` 直下のファイルと新デプロイ先 `Assets/Editor/PrefabSentinel/` の共存。

清掃は `target_path.parent` に対して常に実行する（特定パス名に依存しない）。`target_path.parent` に `PrefabSentinel.*.cs` が見つからなければ no-op。

### 動作

1. デプロイ前に `target_path.parent` を走査（直下のみ、再帰しない）
2. `PrefabSentinel.*.cs` にマッチするファイルを収集
3. ターゲットディレクトリ内のファイルは除外（`target_path` 配下は対象外）
4. 該当ファイルと対応する `.meta` ファイル（存在すれば）を削除
5. レスポンスに結果を格納

### `.meta` ファイルの扱い

Unity は `.cs` ファイルごとに `.meta` ファイルを生成する。`.cs` だけ削除すると孤立した `.meta` が残り、domain reload のたびに警告が出る。旧 `.cs` を削除する際は `{filename}.meta` も存在すれば一緒に削除する。

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

`PrefabSentinel.VRCSDKUploadHandler.cs` は `#if VRC_SDK_VRCSDK3` で囲まれているが、ガード内で SDK の新しい API (`GetBuildTargetGroup`, `IVRCSdkWorldBuilderApi` 等) を使用する。SDK が存在するがバージョンが古いプロジェクトではプリプロセッサガードを通過した上でコンパイルエラーになる。C# の `#if` はパッケージの存在のみをチェックし、API バージョンを区別できないため、ファイル単位の除外が最も確実。

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
_UPLOAD_HANDLER = "PrefabSentinel.VRCSDKUploadHandler.cs"

for cs_file in sorted(plugin_tools.glob("*.cs")):
    if cs_file.name == _UPLOAD_HANDLER and not include_upload_handler:
        skipped_files.append(cs_file.name)
        continue
    # ... copy
```

### ターゲット内の既存ファイル清掃

`include_upload_handler=False` の場合、ターゲットディレクトリに過去のデプロイで残った `VRCSDKUploadHandler.cs` (+ `.meta`) が存在すれば削除する。スキップしただけでは既存のコピーが残りコンパイルエラーが継続するため。

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
