# UnityTool 統合仕様書（MCP / Skills / 運用設計）

## 0. このドキュメントの目的
この文書は、Unity/VRChatプロジェクトに対して安全かつ再現可能にPrefab/Scene/Udon設定を編集するためのツール群の**全体構想・詳細仕様・相互関係**を定義する。

本仕様の主目的は以下。
- 手作業YAML編集で起きる参照破損（Broken PPtr）を防ぐ
- Prefab Variantのoverride不整合を検出・修復する
- Udon/ClientSimログに基づく原因特定と修正を自動化する
- 人間の判断が必要な変更と、機械的に実行可能な変更を明確分離する

---

## 1. やること / やる内容 / やらないこと

### やること
- Unity SerializedObjectレベルでの安全な編集基盤を提供する
- Prefab Base / Variant / Sceneインスタンスの実効値を追跡可能にする
- 参照解決（GUID + fileID）と整合性検証をAPI化する
- 実行時検証（UdonSharp compile / ClientSim smoke / ログ分類）をパイプライン化する
- Codex Skillsとして運用フローを標準化する

### やる内容
- MCPサーバ群を責務別に分割実装
- CLIオーケストレーターを中心にMCPとSkillsを連携
- 監査ログ（誰が・何を・なぜ・どう変更したか）を保存
- 失敗時のfail-fastと段階的ロールバック設計

### やらないこと
- YAML文字列の直接置換を標準手段にしない
- Unity内部参照を推測で補完しない
- 変更根拠なしの自動最適化をしない
- ユーザー判断が必要な仕様変更を勝手に適用しない

---

## 2. 背景課題（現行運用の痛点）

### 2.1 参照破損
- 症状: `Broken text PPtr ... fileID ... doesn't exist`
- 原因: `Variant`内overrideが、Base変更や型変更に追従できず不正参照を保持

### 2.2 Variant差分の不可視性
- 症状: Inspectorでは正しく見えるのに、実効値で崩れる
- 原因: `m_Modifications.propertyPath`の増殖・衝突・古いoverrideの残留

### 2.3 実行時停止
- 症状: Udon runtime exception、機能全停止
- 原因: `null`設定・必須コンポーネント欠落・未検証の配線

### 2.4 調査コストの高さ
- ログ・YAML・Scene・Prefabを横断する必要があり、復旧までの時間が長い

---

## 3. 全体アーキテクチャ

```text
[User/Codex]
    |
    v
[UnityTool CLI Orchestrator]
    |-------------------------------|
    v                               v
[MCP: serialized-object]        [Skills Layer]
[MCP: prefab-variant]           - variant-safe-edit
[MCP: reference-resolver]       - udon-log-triage
[MCP: runtime-validation]       - prefab-reference-repair
    |
    v
[Unity Editor Bridge / Headless Unity Process]
    |
    v
[Project Assets + Scene + Prefab + Udon + Logs]
```

### 3.1 コンポーネント責務
- UnityTool CLI Orchestrator
  - ユースケース単位で複数MCPを編成
  - 実行計画、依存順序、停止条件を管理
- MCP群
  - 単一責務・明確な入出力・再実行可能
- Skills群
  - 手順化された運用規約（レビュー/修正/再検証）

### 3.2 データフロー原則
- 読み取りは「構造化」優先、文字列処理は補助
- 書き込みは「意図（path+value）」で実行
- 全更新に`before/after`差分と検証結果を付帯

---

## 4. MCP仕様（詳細）

## 4.1 MCP-A: unity-serialized-object-mcp

### 目的
UnityのSerializedObject/SerializedProperty経由で安全に値を読む・書く。

### 主機能
- `get_object(path_or_guid, component_type, object_name?)`
- `get_property(object_handle, property_path)`
- `set_property(object_handle, property_path, value)`
- `insert_array_element(object_handle, property_path, index, value)`
- `remove_array_element(object_handle, property_path, index)`
- `apply_and_save(target_asset_or_scene)`
- `dry_run_patch(ops[])`

### 入力例
```json
{
  "target": "Assets/haiirokoubou/prefab/group_sound_proof_ver2.21 Variant.prefab",
  "ops": [
    {
      "op": "set",
      "component": "haiirokoubouNameSp.sound_proof_main",
      "path": "mic_obj_extra.Array.size",
      "value": 2
    }
  ]
}
```

### 出力例
```json
{
  "success": true,
  "applied": 1,
  "warnings": [],
  "diff": [
    {
      "path": "mic_obj_extra.Array.size",
      "before": 1,
      "after": 2
    }
  ]
}
```

### 検証規約
- 型一致必須
- `UnityEngine.Object`参照は存在確認必須
- 必須参照欠落は`error`で停止（fail-fast）

---

## 4.2 MCP-B: prefab-variant-mcp

### 目的
Base/Variant/Sceneインスタンスを横断して実効値とoverrideを可視化する。

### 主機能
- `resolve_prefab_chain(variant_path)`
- `list_overrides(variant_path)`
- `compute_effective_values(variant_path, component_filter?)`
- `detect_stale_overrides(variant_path)`
- `migrate_override_paths(variant_path, mapping_rules)`
- `remove_orphan_overrides(variant_path)`

### 重点検査
- 存在しない`propertyPath`
- 型変更後に残った古いoverride
- `Array.size`と`Array.data[i]`の整合
- 重複override・後勝ち衝突

### 失敗時挙動
- 自動修復不可の場合は`decision_required`として返却
- 自動修復対象は`safe_fix`として提案と根拠を返却

---

## 4.3 MCP-C: reference-resolver-mcp

### 目的
GUID/fileID参照を人間可読の実体へ逆引きし、壊れた参照を早期検出する。

### 主機能
- `resolve_reference(guid, file_id)`
- `resolve_object_to_reference(asset_path, hierarchy_path, component_type)`
- `scan_broken_references(scope)`
- `where_used(asset_or_guid)`
- `validate_pointer_set(pointer_list)`

### 出力カテゴリ
- `resolved`
- `missing_asset`
- `missing_local_id`
- `type_mismatch`

### ユースケース
- Broken PPtr検出時に「どこが」「何を」参照しているか即提示

---

## 4.4 MCP-D: runtime-validation-mcp

### 目的
編集後の破綻を実行系で検証し、ログを構造化して原因候補を返す。

### 主機能
- `compile_udonsharp(project_root)`
- `run_clientsim(scene_path, profile)`
- `collect_unity_console(since_timestamp)`
- `classify_errors(log_lines)`
- `assert_no_critical_errors(classification_report)`

### ログ分類ルール（初期）
- `BROKEN_PPTR`
- `UDON_NULLREF`
- `VARIANT_OVERRIDE_MISMATCH`
- `DUPLICATE_EVENTSYSTEM`（低優先）
- `MISSING_COMPONENT`

### 受け入れ判定
- `critical` = 0
- `error` = 0
- `warning` は許容可否をポリシーで指定

---

## 5. Skills仕様（運用手順）

## 5.1 skill: variant-safe-edit
### 目的
Variant編集で破損を出さないための標準手順。

### 手順
1. `prefab-variant-mcp.list_overrides`
2. `reference-resolver-mcp.scan_broken_references`
3. `unity-serialized-object-mcp.dry_run_patch`
4. `unity-serialized-object-mcp.apply_and_save`
5. `runtime-validation-mcp.compile_udonsharp`
6. `runtime-validation-mcp.run_clientsim`

### 停止条件
- critical error検出
- 必須参照欠落

---

## 5.2 skill: udon-log-triage
### 目的
Udonログを根拠に修正候補を最短で絞る。

### 手順
1. 例外箇所（ファイル/行）抽出
2. 参照アドレス（heap pointer）と変数名対応
3. 直近の設定変更差分と照合
4. 修正候補を`safe_fix` / `decision_required`で分類

---

## 5.3 skill: prefab-reference-repair
### 目的
壊れた参照の機械的復旧。

### 手順
1. `scan_broken_references`
2. 同型・同名候補を検索
3. 一意候補のみ自動適用
4. 複数候補は判断待ちキューへ

---

## 6. 相互関係（責務境界）

- serialized-object-mcp
  - 何を書き換えるか（操作実行）
- prefab-variant-mcp
  - どこが上書きされているか（差分可視化）
- reference-resolver-mcp
  - 参照が有効か（実体照合）
- runtime-validation-mcp
  - 実行時に壊れていないか（結果検証）
- Skills
  - どの順で使うか（運用プロトコル）

この分離により、障害の切り分けを「編集」「差分」「参照」「実行」の4面で独立して行える。

---

## 7. API共通仕様

### 7.1 レスポンス共通フォーマット
```json
{
  "success": true,
  "severity": "info|warning|error|critical",
  "code": "TOOL_SPECIFIC_CODE",
  "message": "human readable",
  "data": {},
  "diagnostics": [
    {
      "path": "Assets/...",
      "location": "propertyPath or line",
      "detail": "...",
      "evidence": "..."
    }
  ]
}
```

### 7.2 エラーコード規約
- `SER001`: Serialized path not found
- `SER002`: Type mismatch
- `PVR001`: Stale override detected
- `REF001`: Missing asset guid
- `REF002`: Missing local fileID
- `RUN001`: Udon runtime exception
- `RUN002`: ClientSim startup failure

---

## 8. 実行シーケンス（代表）

## 8.1 マイク本数追加（安全ルート）
1. Variantの`mic_obj`/`mic_obj_extra`実効値取得
2. 追加マイクの候補参照を解決
3. 配列size/dataをdry-run
4. 適用
5. Broken PPtrスキャン
6. ClientSim smoke

### 成功条件
- 参照切れゼロ
- Udon criticalゼロ
- 指定マイク数が実効値で一致

---

## 8.2 部屋削減（space配列縮退）
1. `space.Array.*`の実効値取得
2. 利用中グループ影響分析
3. `space.Array.size`縮退案を生成
4. world_audioグループ再計算の安全性確認
5. 適用→検証

---

## 9. データモデル

### 9.1 Core Entities
- `AssetRef { guid, path, type }`
- `ObjectRef { guid, fileID, componentType, hierarchyPath }`
- `OverrideEntry { target, propertyPath, value, objectReference }`
- `PatchOp { op, component, path, value }`
- `ValidationIssue { severity, category, location, evidence, fixHint }`

### 9.2 不変条件
- ObjectRefは`guid+fileID`で一意解決可能
- `Array.size`と`Array.data[i]`の整合維持
- 型不一致は適用不可
- 必須参照欠落時は`error`停止

---

## 10. 品質要件（NFR）

### 10.1 安全性
- 破壊的変更はdry-run必須
- apply前後で参照整合チェック

### 10.2 再現性
- 同一入力に対して同一差分を生成
- ログに実行IDと入力ハッシュを記録

### 10.3 監査性
- 変更理由、対象、結果、検証証跡を保存

### 10.4 性能
- 1Prefab編集の目標: < 2秒（キャッシュ有）
- 全参照スキャンの目標: < 30秒（中規模プロジェクト）

---

## 11. セキュリティ/アクセス制御

- 書き込み権限は明示モード時のみ
- デフォルトはread-only inspection
- 重要操作は`--confirm`または署名付き実行計画を要求
- 外部プロセス実行（Unity batchmode）は許可リスト制

---

## 12. 実装ロードマップ

### Phase 1（最短価値）
- reference-resolver-mcp
- prefab-variant-mcp（read系中心）
- variant-safe-edit skill（検査のみ）

### Phase 2（編集安定化）
- unity-serialized-object-mcp（write対応）
- dry-run + rollback

### Phase 3（実行検証統合）
- runtime-validation-mcp
- エラー分類器

### Phase 4（運用高度化）
- 自動修復提案
- KPIダッシュボード

---

## 13. 受け入れ基準（Definition of Done）

- Broken PPtr再発率: 0件（指定テストセット）
- Variant override整合性: 100%
- Udon runtime critical: 0件（スモークシーン）
- 変更ごとに証跡（before/after + validation report）生成

---

## 14. テスト戦略

### 14.1 ユニット
- propertyPath解決
- array操作境界値
- 参照逆引き

### 14.2 統合
- Base/Variant/Sceneの三層編集
- 参照修復から実行検証までのE2E

### 14.3 回帰
- 既知不具合（Broken PPtr, Udon nullref）再現ケースを固定

---

## 15. 既知リスクと対策

- Unityバージョン差でSerializedProperty挙動が変わる
  - 対策: バージョン互換テーブル
- Udon/SDK更新でログ形式が変わる
  - 対策: ログ分類ルールをバージョン管理
- 大規模Sceneでスキャン時間増大
  - 対策: 差分スコープ限定とキャッシュ

---

## 16. 運用ポリシー

- 変更前に必ずscope宣言（対象Prefab/Scene）
- 変更後に必ずruntime検証
- decision_requiredはユーザー合意後のみ適用
- READMEを単一の運用・仕様の正本とする

---

## 17. CLI想定コマンド

```bash
unitytool inspect variant --path "Assets/... Variant.prefab"
unitytool inspect where-used --asset-or-guid "Assets/SomeAsset.prefab" --scope "Assets"
unitytool validate refs --scope "Assets/haiirokoubou"
unitytool suggest ignore-guids --scope "Assets/haiirokoubou"
unitytool patch apply --plan patch.json --dry-run
unitytool validate runtime --scene "Assets/Scenes/VRCDefaultWorldScene.unity"
unitytool report export --format md --out reports/latest.md
```

### 17.1 Phase 1 Scaffold 実行方法（現行実装）

Phase 1では read-only 検査系の CLI 骨格のみ提供する。  
ローカル実行は `uv run`、可搬実行は `uvx --from .` を使用する。

```bash
# プロジェクトルートで
uv run unitytool inspect variant --path "Assets/... Variant.prefab"
uv run unitytool inspect where-used --asset-or-guid "Assets/SomeAsset.prefab" --scope "Assets" --max-usages 200
uv run unitytool validate refs --scope "Assets/haiirokoubou"
uv run unitytool validate refs --scope "Assets/haiirokoubou" --details --max-diagnostics 200
uv run unitytool validate refs --scope "Assets/haiirokoubou" --exclude "**/Generated/**"
uv run unitytool validate refs --scope "Assets/haiirokoubou" --ignore-guid-file "config/ignore_guids.txt"
uv run unitytool validate runtime --scene "sample/avatar/Assets/Marycia.unity" --log-file "sample/world/Logs/ClientSim.log"
uv run unitytool validate bridge-smoke --plan "config/prefab_patch_plan.json" --unity-command "C:/Program Files/Unity/Hub/Editor/<version>/Editor/Unity.exe" --unity-project-path "D:/git/UnityTool/sample/avatar" --unity-execute-method "PrefabSentinel.UnityPatchBridge.ApplyFromJson"
uv run unitytool patch hash --plan "config/patch_plan.example.json"
set UNITYTOOL_PLAN_SIGNING_KEY="replace-with-signing-key"
uv run unitytool patch sign --plan "config/patch_plan.example.json"
uv run unitytool patch attest --plan "config/patch_plan.example.json" --out "reports/patch_attestation.json"
uv run unitytool patch verify --plan "config/patch_plan.example.json" --sha256 "<sha256>"
uv run unitytool patch verify --plan "config/patch_plan.example.json" --attestation-file "reports/patch_attestation.json"
uv run unitytool patch verify --plan "config/patch_plan.example.json" --signature "<signature>"
uv run unitytool patch apply --plan "config/patch_plan.example.json" --dry-run
uv run unitytool patch apply --plan "config/patch_plan.example.json" --dry-run --attestation-file "reports/patch_attestation.json"
uv run unitytool patch apply --plan "config/patch_plan.example.json" --dry-run --plan-sha256 "<sha256>"
uv run unitytool patch apply --plan "config/patch_plan.example.json" --dry-run --plan-signature "<signature>"
uv run unitytool patch apply --plan "config/patch_plan.example.json" --dry-run --out-report "reports/patch_result.json"
set UNITYTOOL_PATCH_BRIDGE="python tools/unity_patch_bridge.py"
set UNITYTOOL_UNITY_COMMAND="C:/Program Files/Unity/Hub/Editor/<version>/Editor/Unity.exe"
set UNITYTOOL_UNITY_PROJECT_PATH="D:/git/UnityTool/sample/avatar"
set UNITYTOOL_UNITY_EXECUTE_METHOD="PrefabSentinel.UnityPatchBridge.ApplyFromJson"
python scripts/unity_bridge_smoke.py --plan "config/prefab_patch_plan.json" --unity-command "C:/Program Files/Unity/Hub/Editor/<version>/Editor/Unity.exe" --unity-project-path "D:/git/UnityTool/sample/avatar" --unity-execute-method "PrefabSentinel.UnityPatchBridge.ApplyFromJson" --out "reports/unity_bridge_smoke.json"
uv run unitytool patch apply --plan "config/prefab_patch_plan.json" --confirm
uv run unitytool patch apply --plan "config/prefab_patch_plan.json" --confirm --scope "Assets" --runtime-scene "Assets/Smoke.unity"
uv run unitytool suggest ignore-guids --scope "Assets/haiirokoubou" --min-occurrences 100 --max-items 20
uv run unitytool suggest ignore-guids --scope "Assets/haiirokoubou" --ignore-guid "7e5debf235ac2d54397a268de3328672"
uv run unitytool suggest ignore-guids --scope "Assets/haiirokoubou" --min-occurrences 100 --out-ignore-guid-file "config/ignore_guids.txt" --out-ignore-guid-mode append
python scripts/benchmark_refs.py --scope "sample/avatar/Assets" --warmup-runs 1 --runs 3 --out "sample/avatar/config/benchmark_refs.json" --out-csv "sample/avatar/config/benchmark_refs.csv" --csv-append --include-generated-date
python scripts/benchmark_history_to_csv.py --inputs "sample/avatar/config/bench_*.json" --scope-contains "avatar" --severity error --generated-date-prefix "2026-02" --min-p90 2.0 --latest-per-scope --top-slowest 20 --split-by-severity --sort-by avg_sec --sort-order desc --include-date-column --out "sample/avatar/config/benchmark_trend.csv" --out-md "sample/avatar/config/benchmark_trend.md"
python scripts/benchmark_samples.py --targets all --runs 1 --warmup-runs 0 --history-generated-date-prefix "2026-02" --history-min-p90 2.0 --history-latest-per-scope --history-split-by-severity --history-write-md --run-regression --regression-baseline-auto-latest 3 --regression-baseline-pinning-file "sample/avatar/config/baseline_pinning.json" --regression-alerts-only --regression-fail-on-regression --regression-out-csv-append --regression-out-md
python scripts/benchmark_regression_report.py --baseline-inputs "sample/avatar/config/bench_20260211*.json" --latest-inputs "sample/avatar/config/bench_20260212*.json" --baseline-pinning-file "sample/avatar/config/baseline_pinning.json" --avg-ratio-threshold 1.1 --p90-ratio-threshold 1.1 --alerts-only --fail-on-regression --out-json "sample/avatar/config/benchmark_regression.json" --out-csv "sample/avatar/config/benchmark_regression.csv" --out-csv-append --out-md "sample/avatar/config/benchmark_regression.md"
python scripts/bridge_smoke_samples.py --targets all --avatar-plan "sample/avatar/config/prefab_patch_plan.json" --world-plan "sample/world/config/prefab_patch_plan.json" --unity-command "C:/Program Files/Unity/Hub/Editor/<version>/Editor/Unity.exe" --out-dir "reports/bridge_smoke" --summary-md "reports/bridge_smoke/summary.md"
python scripts/bridge_smoke_samples.py --targets all --avatar-plan "sample/avatar/config/prefab_patch_plan.json" --world-plan "sample/world/config/prefab_patch_plan.json" --unity-command "C:/Program Files/Unity/Hub/Editor/<version>/Editor/Unity.exe" --unity-timeout-sec 600 --avatar-unity-timeout-sec 900 --world-unity-timeout-sec 450 --max-retries 2 --retry-delay-sec 5 --out-dir "reports/bridge_smoke" --summary-md "reports/bridge_smoke/summary.md"
python scripts/smoke_summary_to_csv.py --inputs "reports/bridge_smoke/**/summary.json" --duration-percentile 90 --out "reports/bridge_smoke_history.csv" --out-md "reports/bridge_smoke_history.md" --out-timeout-profile "reports/bridge_timeout_profile.json" --timeout-multiplier 1.5 --timeout-slack-sec 60 --timeout-min-sec 300 --timeout-round-sec 30

# uvx 経由でローカルパッケージから実行（インストール不要）
uvx --from . unitytool inspect variant --path "Assets/... Variant.prefab"
uvx --from . unitytool inspect where-used --asset-or-guid "Assets/SomeAsset.prefab" --scope "Assets"
uvx --from . unitytool validate refs --scope "Assets/haiirokoubou"
uvx --from . unitytool validate runtime --scene "sample/avatar/Assets/Marycia.unity"
uvx --from . unitytool validate bridge-smoke --plan "config/prefab_patch_plan.json"
uvx --from . unitytool patch apply --plan "config/patch_plan.example.json" --dry-run
uvx --from . unitytool suggest ignore-guids --scope "Assets/haiirokoubou"
```

`report export` は JSON レポートを Markdown / JSON に変換して保存する。
`--ignore-guid-file` は UTF-8 テキスト（1行1GUID、`#` 以降コメント可）を受け付ける。
`suggest ignore-guids` は `--out-ignore-guid-file` で候補GUIDを1行1件で保存できる（`replace`/`append`）。
`report export --format md` では、`scan_broken_references` データが含まれる場合に Noise Reduction サマリーを先頭に出力する。
`report export --format md` は `--md-max-usages N` / `--md-omit-usages` で `usages` 配列を軽量化できる。
`report export --format md` は `--md-max-steps N` / `--md-omit-steps` で `steps` 配列を軽量化できる。
`scripts/benchmark_refs.py` で `validate refs` の実行時間を同条件で複数回計測できる。
`scripts/benchmark_refs.py` は `p50` / `p90` をJSON・CSVの双方に出力する。
`scripts/benchmark_refs.py` は `--out-csv` で比較しやすいCSV行も出力できる。
`scripts/benchmark_refs.py` は `--warmup-runs` で初回ウォームアップ分を計測統計から除外できる。
`scripts/benchmark_refs.py` は `--include-generated-date` で生成UTC時刻（`generated_at_utc`）をJSON・CSVへ出力できる。
`scripts/benchmark_history_to_csv.py` で複数JSONの結果を1本の比較CSVへ統合できる。
`scripts/benchmark_history_to_csv.py` は `--scope-contains` / `--severity` で抽出条件を指定できる。
`scripts/benchmark_history_to_csv.py` は `--generated-date-prefix` で `generated_at_utc` の日付プレフィックス抽出ができる。
`scripts/benchmark_history_to_csv.py` は `--min-p90 X` で `p90_sec >= X` の行だけに絞り込める。
`scripts/benchmark_history_to_csv.py` は `--latest-per-scope` で scopeごとに最新の1件だけを残せる。
`scripts/benchmark_history_to_csv.py` は `--top-slowest N` で `avg_sec` が遅い上位N件だけに絞り込める。
`scripts/benchmark_history_to_csv.py` は `--split-by-severity` で `benchmark_trend_error.csv` などseverity別CSVを追加出力できる。
`scripts/benchmark_history_to_csv.py` は `--out-md` でトレンドのMarkdownサマリを追加出力できる。
`scripts/benchmark_history_to_csv.py` は `--sort-by {source|scope|avg_sec}` / `--sort-order {asc|desc}` で並び順を指定できる。
`scripts/benchmark_history_to_csv.py` は `--include-date-column` で `generated_at_utc` から `generated_date_utc` 列を追加できる。
`scripts/benchmark_history_to_csv.py` は scopeの区切り文字（`/` と `\`）を内部で正規化して比較できる。
`scripts/benchmark_history_to_csv.py` は benchmark summary 形式でないJSONを自動的に除外する（`bench_*.json` に他用途JSONが混ざってもノイズ化しない）。
`scripts/benchmark_samples.py` で `sample/avatar` / `sample/world` のベンチ実行と履歴CSV更新をまとめて実行できる（`--history-generated-date-prefix` / `--history-min-p90` / `--history-latest-per-scope` / `--history-split-by-severity` / `--history-write-md` を転送可能）。
`scripts/benchmark_samples.py` は `--run-regression` と `--regression-baseline-inputs`（または `--regression-baseline-auto-latest N`）で `benchmark_regression_report.py` まで連続実行できる。
`scripts/benchmark_samples.py` は `--regression-baseline-pinning-file` で scope別baseline固定ファイルを regression report に渡せる。
`scripts/benchmark_samples.py` は `--regression-out-md` で回帰レポートのMarkdownサマリ（`benchmark_regression.md`）も生成できる。
`scripts/benchmark_regression_report.py` で baseline/latest のJSON群を scope単位で比較し、`avg_ratio` / `p90_ratio` と閾値で `regressed|improved|stable` を判定できる。
`scripts/benchmark_regression_report.py` は `--baseline-pinning-file` で scopeごとのbaseline JSONを固定できる。
`scripts/benchmark_regression_report.py` は `--alerts-only` / `--fail-on-regression` でCI向け短文ログと非0終了コードを使える。
`scripts/benchmark_regression_report.py` は `--out-csv-append` で比較履歴を同一CSVに追記できる。
`scripts/benchmark_regression_report.py` は `--out-md` で比較サマリのMarkdown（回帰一覧 + scope表）を出力できる。
`scripts/bridge_smoke_samples.py` は `unity_bridge_smoke.py` を avatar/world 複数ケースで連続実行し、`reports/bridge_smoke/<target>/response.json` と `unity.log`、集計 `summary.json`（任意 `summary.md`）を決定的なパスで出力できる。`--max-retries` / `--retry-delay-sec` でターゲットごとの一時失敗を再試行でき、`--avatar-unity-timeout-sec` / `--world-unity-timeout-sec` で target 別 timeout を調整できる。`summary` の各ケースには `attempts` と `duration_sec` を含み、timeout tuning の根拠にできる。
`scripts/smoke_summary_to_csv.py` は `bridge_smoke_samples.py` の `summary.json` 群を集約して、target別の duration/attempts/failure 傾向を CSV と Markdown decision table として出力できる。`--out-timeout-profile` を指定すると、観測値ベースの timeout 推奨値（`recommended_cli_arg` 付き）を JSON で出力できる。
`.github/workflows/ci.yml` は `python -m unittest discover -s tests -v` と `bridge-smoke-contract`（`bridge_smoke_samples.py` の expected-failure 実行 + `smoke_summary_to_csv.py` による timeout decision table/timeout profile 生成 + artifact保存）を自動実行する。
`.github/workflows/unity-smoke.yml` は `workflow_dispatch` 専用で self-hosted Windows Unity ランナー上の実Unity smoke（`bridge_smoke_samples.py` 非期待失敗モード）を実行し、`unity-smoke-summary` / `unity-smoke-avatar` / `unity-smoke-world` の分割artifactで保存する。`unity-smoke-summary` には `summary.json`/`summary.md` に加えて `history.csv`/`history.md`/`timeout_profile.json`（`smoke_summary_to_csv.py` 生成）を含む。`targets`（`all|avatar|world`）と入力パスの preflight 検証を備え、`unity_timeout_sec` + `avatar/world` 個別 timeout 入力で batchmode timeout を調整できる。さらに `run_window_start_utc_hour` / `run_window_end_utc_hour` を指定すると、UTC実行ウィンドウ外では smoke 実行をスキップできる。
`patch hash` は plan JSON を検証したうえで SHA-256 digest を出力する（`--format json` 対応）。
`patch sign` は plan JSON を検証したうえで HMAC-SHA256 署名を出力する（`--key-env` / `--key-file` / `--format json` 対応）。
`patch attest` は plan の sha256 と任意の署名を attestation JSON として出力できる（`--unsigned` / `--out`）。
`patch verify` は SHA-256 / HMAC-SHA256 の一致検証を行い、検証失敗時は非0終了コードを返す（`--format json`/`text`）。
`patch verify` は `--attestation-file` から期待値を読み取って照合できる。
`patch apply` は plan JSON のスキーマ検証と `dry_run_patch` プレビューを実装済み（`set` / `insert_array_element` / `remove_array_element`）。
`patch apply` は `--out-report` 指定時に結果 envelope を JSON ファイルに保存する。
`patch apply` は非dry-run時に `--confirm` を要求し、JSONターゲット（`.json`）は内蔵バックエンドで実編集する。
`patch apply` は `--attestation-file` から期待値（sha256/signature）を読み取って適用前照合できる（CLI引数の `--plan-sha256` / `--plan-signature` が優先）。
`patch apply` は `--plan-sha256` 指定時に plan ファイル内容の SHA-256 を照合し、不一致なら適用前に停止する。
`patch apply` は `--plan-signature` 指定時に HMAC-SHA256 署名を照合し、不一致なら適用前に停止する（既定キー環境変数: `UNITYTOOL_PLAN_SIGNING_KEY`）。
`patch apply` は `--scope` 指定時に `scan_broken_references` を事前実行し、`error`/`critical` で fail-fast 停止する。
`patch apply` は `.prefab` ターゲットで `list_overrides` を事前実行し、`error`/`critical` で fail-fast 停止する。
`patch apply` は `--runtime-scene` 指定時に `compile_udonsharp` / `run_clientsim` / ログ分類 / `assert_no_critical_errors` を後段実行する。
`patch apply` は Unityターゲット（`.prefab` / `.unity` / `.asset` など）に対して `UNITYTOOL_PATCH_BRIDGE` 経由の外部bridgeを使って適用できる。
`patch apply` は Unity bridge 未設定時に Unityターゲットを `SER_UNSUPPORTED_TARGET` で停止する（Unity YAMLの直接編集は行わない）。
`UNITYTOOL_PATCH_BRIDGE` は JSON入力(stdin) / JSON出力(stdout) のbridgeコマンドを指定する（`protocol_version: 1`）。
`tools/unity_patch_bridge.py` は `UNITYTOOL_UNITY_COMMAND` を使って Unity batchmode コマンドを実行し、JSONリクエスト/レスポンスファイルを介して結果を返す（`set` / `insert_array_element` の `value` を Unity 側で扱える型情報へ正規化）。
`tools/unity_patch_bridge.py` は `UNITYTOOL_UNITY_PROJECT_PATH` / `UNITYTOOL_UNITY_EXECUTE_METHOD` / `UNITYTOOL_UNITY_TIMEOUT_SEC` / `UNITYTOOL_UNITY_LOG_FILE` で実行設定を制御できる。
`tools/unity_patch_bridge.py` は Unity応答の `success/severity/code/message/data/diagnostics` を厳密検証し、欠落・型不一致時は `BRIDGE_UNITY_RESPONSE_SCHEMA` で fail-fast 停止する。
`scripts/unity_bridge_smoke.py` は patch plan から `tools/unity_patch_bridge.py` を end-to-end 実行し、Unity実行環境の上書き・期待成功/失敗判定・レスポンス保存（`--out`）をまとめて検証できる。bridge応答は `success/severity/code/message/data/diagnostics` を厳密検証し、欠落・型不一致時は fail-fast で停止する。
`tools/unity/PrefabSentinel.UnityPatchBridge.cs` は Unity 側 `-executeMethod` 実装として `.prefab` ターゲットの `set` / `insert_array_element` / `remove_array_element` を SerializedObject 経由で適用する（`component` は一意一致必須、component曖昧時は候補パス付きで fail-fast）。
`component` セレクタは `TypeName@Hierarchy/Path` 形式を受け付け、同型コンポーネントが複数ある場合にGameObject階層で明示的に絞り込める。
`set` の値デコードは `int/float/bool/string/null` に加えて `enum`、`Color`、`Vector2/3/4`、`Vector2Int/3Int`、`Rect/RectInt`、`Bounds/BoundsInt`、`Quaternion`、`ObjectReference`（`value_kind=json` の `{guid,file_id}`）を扱う。
配列操作パスの診断は `.Array.data` 形式を厳密検証し、`.Array.size`/index付き誤指定時はヒント付きで停止する。
`validate runtime` は log分類ベースのscaffoldを実装済みで、`--scene` 存在確認、`BROKEN_PPTR` / `UDON_NULLREF` などの分類、`assert_no_critical_errors` 判定までを返す。
`validate runtime` の compile/ClientSim 実行は現時点では `RUN_COMPILE_SKIPPED` / `RUN_CLIENTSIM_SKIPPED` として明示的に未配線を返す。
`validate bridge-smoke` は patch plan を bridge request へ変換して `tools/unity_patch_bridge.py` を実行し、`--expect-failure` 判定と `--out` 保存までをCLI本体から実行できる。
`report export --format md` は `VALIDATE_RUNTIME_RESULT` payload を入力した場合、Runtime Validation 要約（分類件数・severity内訳・カテゴリ表）を追加出力する。

```bash
uv run unitytool report export --input reports/input.json --format md --out reports/latest.md
uv run unitytool report export --input reports/input.json --format md --out reports/latest.md --md-max-usages 100
uv run unitytool report export --input reports/input.json --format md --out reports/latest.md --md-omit-usages
uv run unitytool report export --input reports/input.json --format md --out reports/latest.md --md-max-steps 20
uv run unitytool report export --input reports/input.json --format md --out reports/latest.md --md-omit-steps
uvx --from . unitytool report export --input reports/input.json --format json --out reports/latest.json
```

現行Phase 1では read-only 解析を実装済み。  
`inspect variant` は Prefab chain / overrides / stale候補（重複override・Array.size不整合）を返し、  
`inspect where-used` は GUID/asset の参照元を scope 指定で検索し、`max_usages` 超過分を `truncated_usages` に集計する。  
`validate refs` は `missing_asset` / `missing_local_id` を検出する。  
`validate refs` は既定でサマリーのみ返し、診断一覧は `--details` 指定時のみ返す（重い出力を抑制）。  
Unity組み込みGUID（例: `0000000000000000e000000000000000` / `f000...`）は欠落判定から除外する。  
GUIDインデックスは scope が属する Unity プロジェクトルート（最寄り `Assets` 親）で構築し、`Library` / `Logs` / `Temp` / `obj` など既定除外ディレクトリは走査しない。  
`validate refs` の結果には `scan_project_root`（GUIDインデックスに使った Unity プロジェクトルート）を含む。  
外部 `*.prefab` 参照の fileID 検証は誤検知回避のため既定でスキップし、件数を `skipped_external_prefab_fileid_checks` に集計する。  
`validate refs` の `categories` はユニーク問題件数（例: missing GUID単位）を返し、発生回数は `categories_occurrences` / `broken_occurrences` で確認する。  
ノイズ判定に使えるよう、`top_missing_asset_guids` に missing GUID上位を返す。  
`suggest ignore-guids` は `top_missing_asset_guids` から閾値ベースで無視候補GUIDを提案する（適用は手動判断前提）。  
`--ignore-guid` / `--ignore-guid-file` で missing GUID を一時的に無視でき、集計は `ignored_missing_asset_occurrences` / `top_ignored_missing_asset_guids` で確認できる。  
候補採用を継続運用する場合は `--out-ignore-guid-file` で ignore リストへ追記して再利用できる。  
`where_used` も同じ既定除外を適用し、`Library` など非本番スコープを走査しない。  
書き込み操作（apply / repair / runtime検証）は引き続き次フェーズ対象。

---

## 18. 代表レポート出力フォーマット

```md
# UnityTool Validation Report
- RunId: 20260211-235959-abc123
- Scope: Assets/haiirokoubou
- Result: FAILED

## Findings
1. REF002 Missing local fileID
   - Location: group_sound_proof_ver2.21 Variant.prefab / mic_obj_extra.Array.data[0]
   - Evidence: fileID 6858960407220450596 not found
   - Suggested Fix: map to existing VRCPickup fileID 87704510201466299

2. RUN001 Udon runtime exception
   - Location: sound_proof_main.cs:200
   - Evidence: world_audio[i] null
   - Suggested Fix: ignore invalid entries or set world_audio size=0
```

---

## 19. 将来拡張

実装中の具体アイデアと実行順は `docs/IDEAS_AND_ROADMAP.md` で管理する。

- Sceneテンプレート比較による自動配線提案
- 複数ワールド横断の共通設定ガバナンス
- 変更影響グラフ（誰に聞こえるか、どの部屋に属するか）可視化

---

## 20. まとめ
本構想は、
- **参照整合**（壊れない）
- **差分可視化**（何が効いているか分かる）
- **実行検証**（壊れていないと確認できる）
を分離しつつ連携させることで、Unity/VRChatギミック修正の失敗率と復旧コストを大幅に下げることを目的とする。

このREADMEをUnityToolの正本仕様として運用し、MCP・Skills実装時は本書の責務境界と不変条件に従う。
