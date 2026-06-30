# Configuration

`UNITYTOOL_*` 環境変数・`ignore_guids.txt` ファイル・`<scope>/config/` 規約・書き込み系ツールの監査ペアの正本。実行・bridge 連携の仕様は [docs/execution-reference.md](./docs/execution-reference.md)、運用ルールの正本は [AGENTS.md](./AGENTS.md)。本ファイルは設定項目を 1 箇所に集約して、新規・既存いずれのコントリビュータが「何を設定すれば動くか / 何を設定しないと止まるか」を一覧で確認できるようにする。

## 環境変数一覧

`UNITYTOOL_*` プレフィックスの環境変数は Unity Editor Bridge 連携・CI 連携・テストゲーティングに使用する。`種別` 列の値は **active**（実運用で active に読まれる）/ **test-only**（テストの opt-in ゲートにのみ使用）/ **planned**（AGENTS.md / README.md で仕様化済みだが現コードベースでは未参照）/ **legacy**（旧経路用で現コードでは未参照、docstring または ideas doc にのみ残る）。

| 変数名 | 既定値 | 用途 | 種別 | 関連 issue |
|--------|--------|------|------|-----------|
| `UNITYTOOL_BRIDGE_E2E_LIVE` | （未設定） | 値 `"1"` で `tests/test_mcp_server.py` の live Editor Bridge E2E テストを有効化する opt-in ゲート。未設定時は該当テストが skip される。 | test-only | #270 |
| `UNITYTOOL_BRIDGE_WATCH_DIR` | （未設定 = fail-fast） | 常駐 Editor Bridge との file-IPC watch ディレクトリ。Python 側が `{uuid}.request.json` を書き込み、Editor Bridge が `{uuid}.response.json` をアトミック書き出しで返す。未設定時は `BRIDGE_WATCH_DIR_MISSING` で fail-fast 停止する。 | active | #88, #89, #270 |
| `UNITYTOOL_CI_BRANCH` | （未設定。`GITHUB_REF_NAME` をフォールバック） | CI 上の現在ブランチ名。`<scope>/config/ignore_guids.txt` の auto-update（`suggest ignore-guids --out-ignore-guid-file`）の許可ブランチ判定で参照される想定。現コードベースでは `suggest ignore-guids --out-ignore-guid-file` 自体が未実装のため、参照箇所はまだ存在しない（AGENTS.md / README.md の仕様記述のみ）。 | planned | #237 |
| `UNITYTOOL_IGNORE_GUID_ALLOW_BRANCHES` | `main,release/*` | ignore-guid file の auto-update を許可するブランチパターンのカンマ区切り上書き。明示指定時のみ、許可ブランチ上でのみ ignore-guid file が更新される想定。現コードベースでは未参照（AGENTS.md / README.md の仕様記述のみ）。 | planned | #237 |
| `UNITYTOOL_PATCH_BRIDGE` | （未設定） | 非 JSON resource（`.prefab` / `.unity` / `.mat` / `.asset` / `.anim` / `.controller`）の patch 適用に使う外部 bridge コマンドを `shlex` 形式で指定する。未設定で非 JSON resource を扱おうとすると `SER_UNSUPPORTED_TARGET` で停止する。受理コマンドは allowlist（`python` / `uv` / `prefab-sentinel-unity-bridge` 等）に限定される。 | active | — |
| `UNITYTOOL_UNITY_COMMAND` | （未設定） | 旧 Unity batchmode 経路の Unity 実行コマンド。issue #270 で batchmode 経路が削除され、現行コードでは参照されない。導入当時は Editor headless 経路を MCP 統合と並行で運用していたが、常駐 Editor Bridge への一本化に伴い削除された経緯がある。 | legacy | #270 |
| `UNITYTOOL_UNITY_EXECUTE_METHOD` | （未設定） | 旧 Unity batchmode 経路の `-executeMethod` エンドポイント。issue #270 で削除済みで、現行コードでは参照されない。`tools/unity/PrefabSentinel.UnityPatchBridge.cs` の docstring コメントにのみ残る。 | legacy | #270 |
| `UNITYTOOL_UNITY_LOG_FILE` | （未設定） | `collect_unity_console` / `validate_runtime` が読む Unity Editor ログファイルパス。`runtime_root` 配下の絶対パスに正規化される（外指定は `RUN_CONFIG_ERROR`）。 | active | — |
| `UNITYTOOL_UNITY_PROJECT_PATH` | （未設定。`activate_project` の引数優先） | Unity プロジェクトルート（`Assets/` の親）。WSL 環境では Windows パス（`D:/...`）と WSL パス（`/mnt/d/...`）の両方を受け付ける（`prefab_sentinel/wsl_compat.py`）。 | active | — |
| `UNITYTOOL_UNITY_TIMEOUT_SEC` | `120` | Editor Bridge のレスポンスファイル出現を待つポーリング上限秒数。整数値で指定する（不正値・非正値はパスに応じて `BRIDGE_TIMEOUT_INVALID` / `EDITOR_BRIDGE_TIMEOUT_INVALID` / `RUN_CONFIG_ERROR` のエラーエンベロープで拒否され、silent fallback はしない）。既定値はパスごとに異なり、patch bridge が `120`、`prefab_sentinel.editor_bridge` が `30`、`runtime_validation` 経路が `300`。 | active | — |

## ignore_guids.txt 形式仕様

`<scope>/config/ignore_guids.txt` は `validate_refs` の missing-asset 判定から除外する GUID リストの正本（issue #237）。

- ファイル形式は UTF-8 テキスト。1 行 1 GUID。
- `#` 以降は行内コメントとして無視する。
- 空行は無視する。
- `validate_refs` MCP ツールの `ignore_asset_guids` 引数（caller-supplied list）とこのファイルは併用され、union-dedupe で orchestrator に転送される。
- ファイルが寄与した場合（1 件以上の GUID を取り込んだ場合）は `IGNORE_GUIDS_FILE_LOADED` info diagnostic を `diagnostics[]` に追加し、`data.path` に解決後の絶対パス・`data.count` に取り込み件数を返す。
- ファイルが存在しない場合は無視する（diagnostic は出さない）。読み取り不能の場合も同様に黙って無視する。
- malformed エントリ（GUID として無効な行）は既存の `REF001` envelope で表面化する。
- ファイルパスは `--scope` で指定した scope の `config/ignore_guids.txt` を自動解決する。固定パスは持たない。ファイル auto-load とは別に caller が GUID を直接指定したい場合は `validate_refs` の `ignore_asset_guids` 引数を使う。

## diagnostics_baseline.json 形式仕様

`config/diagnostics_baseline.json` は activate 済み project root 直下の `config/` に置く diagnostics 分類用 baseline。`ignore_guids.txt` と異なり scope ごとには解決しない。

```json
{
  "version": 1,
  "known_diagnostics": [
    "missing_asset_guid:0123456789abcdef0123456789abcdef"
  ]
}
```

- `version` は `1` のみ受理する。
- `known_diagnostics` は空でない文字列 key の配列。key は `validate_refs` / `inspect_wiring` / `validate_all_wiring` / `validate_structure` / `validate_materials` が返す stable diagnostic key をそのまま記録する。
- ファイルが存在しない場合、または project root が未設定の場合は validation は空 baseline として実行する。`update_diagnostics_baseline` は project root 未設定時に `DIAGNOSTICS_BASELINE_PROJECT_ROOT_REQUIRED` で停止する。
- JSON が壊れている、root が object でない、schema が違う、空文字列 key が含まれる場合は `DIAGNOSTICS_BASELINE_INVALID` の error envelope を返し、対象 tool は orchestrator を呼ばない。
- validation tool はこのファイルを read-only に扱い、自動作成・自動更新しない。baseline を更新する場合は明示的に `update_diagnostics_baseline` を呼ぶ。
- `update_diagnostics_baseline(source, target, mode="preview")` は `validate_refs` / `inspect_wiring` / `validate_all_wiring` / `validate_structure` / `validate_materials` のいずれかを再実行し、応答の `data.diagnostics_baseline` から次の `known_diagnostics` を計算する。既存 report JSON からの import/update は行わない。
- `mode="preview"` はファイルや `config/` を作らず、`would_create` / `added_count` / `pruned_count` / capped samples を返す。新規 diagnostic が 0 件でも success として no-op preview を返す。
- `mode="write"` は `confirm=True` と非空 `change_reason` が必須。条件を満たす場合だけ `config/` を必要に応じて作成し、`version: 1` と sorted / deduped `known_diagnostics` を indent 2、UTF-8、末尾 newline の JSON として書き込む。invalid baseline は上書きしない。
- `prune_resolved=False` が既定で、baseline にだけ残る resolved key は保持する。`prune_resolved=True` の場合だけ、new key 追加後に resolved key を削除する。

## material_validation_rules.json 形式仕様

`config/material_validation_rules.json` は project root 直下の `config/` に置く、`validate_materials` 用の任意の宣言的ルールファイル。ファイルが存在しない場合、`validate_materials` は generic static checks のみを実行し、shader 名・folder・shared material の project-specific policy は一切仮定しない。ファイルが存在して JSON/schema が invalid な場合は `MATERIAL_RULES_INVALID` の error envelope で停止し、validation scan は開始しない。

```json
{
  "version": 1,
  "shader_name_policies": [
    {
      "id": "ui-overlay-shader",
      "scope": "Assets/UI",
      "hierarchy_prefix": "Canvas/Overlay",
      "expected_shader": "UI/Overlay/AlwaysOnTop"
    }
  ],
  "shared_material_groups": [
    {
      "id": "nameplate-icons",
      "scope": "Assets/UI",
      "hierarchy_prefix": "Canvas/Overlay/Icons",
      "expected_material": "Assets/UI/Materials/Icon.mat"
    }
  ],
  "folder_policies": [
    {
      "id": "no-material-assets-in-fonts",
      "folder": "Assets/Fonts",
      "disallowed_extensions": [".mat"],
      "disallowed_asset_kinds": ["Material"]
    }
  ]
}
```

- `version` は `1` のみ受理する。
- `shader_name_policies[]` は `id`、適用対象 `scope`、任意の renderer hierarchy prefix `hierarchy_prefix`、期待 shader 名 `expected_shader` を持つ。`.mat` asset、renderer slot 経由で解決した material、TMP material preset 経由で解決した material evidence に適用される。
- `shared_material_groups[]` は `id`、適用対象 `scope`、任意の renderer hierarchy prefix `hierarchy_prefix` を持つ。`expected_material` は任意で、指定時は一致しない slot だけを `MATERIAL_SHARED_GROUP_MISMATCH` にする。未指定時に複数 material candidate が見つかった場合は `MATERIAL_SHARED_GROUP_DRIFT` を返すが、正解 material は宣言しない。
- `folder_policies[]` は `id`、project-relative folder `folder`、任意の `disallowed_extensions[]`、任意の `disallowed_asset_kinds[]` を持つ。unknown kind は policy violation として扱わない。
- 各配列は省略可。省略された rule family は空として扱う。
- このファイルは read-only に扱う。tool 実行中に自動作成・自動更新しない。

## scope config 規約

`<scope>/config/*.txt` 系の設定ファイルはすべて `--scope` 起点で相対解決する。固定パスは持たない。

- 走査対象（`--scope`）は実行時に明示する。`activate_project` の `project_root` 引数と独立して、検査ごとに scope を切り替えられる。
- `<scope>/config/ignore_guids.txt` は `validate_refs` / `find_referencing_assets` の各 entry point から auto-load される。
- project-level `config/diagnostics_baseline.json` は scope config ではない。diagnostics baseline 分類や `update_diagnostics_baseline` で使う場合も、常に activate 済み project root から解決する。
- ファイルが存在しない場合は黙って無視する（fail にも warning にもしない）。明示要求のない absent file は単に「適用なし」を意味する。
- scope の区切り文字は `/` と `\` のどちらでも受け付け、内部で `/` に正規化される。WSL 環境のパス変換は `prefab_sentinel/wsl_compat.py` が担う。
- ベンチマーク・smoke batch・regression report 等の出力先パスは scope 配下の `reports/` / `benchmark_*.json` を慣例として使うが、本ファイルは scope 規約の正本としては扱わない（個別スクリプトの引数仕様は [docs/execution-reference.md](./docs/execution-reference.md) を参照）。
- `<scope>/config/` 配下に新規の設定ファイルを追加する場合は、auto-load 規約とサンプルパスを本節に追記し、対応 issue 番号を残す。

## confirm / change_reason 必須対象一覧

| ツール名 | `change_reason` 必須 | `out_report` 必須 |
|----------|----------------------|--------------------|
| `set_property` | ✅ | — |
| `add_component` | ✅ | — |
| `remove_component` | ✅ | — |
| `copy_component_fields` | ✅ | — |
| `set_properties` | ✅ | ✅ |
| `update_diagnostics_baseline(mode="write")` | ✅ | — |
| `set_material_property` | ✅ | — |
| `editor_set_material_property` | ✅ | — |
| `editor_serialized_property_write` | ✅ | — |
| `copy_asset` | ✅ | — |
| `rename_asset` | ✅ | — |
| `delete_asset` | ✅ | — |
| `delete_assets` | ✅ | — |
| `editor_create_generated_asset` | ✅ | ✅ `out_report` |
| `editor_move_asset` | ✅ | ✅ `out_report` |
| `revert_overrides` | ✅ | — |
| `patch_apply` | ✅ | ✅ |
| `vrcsdk_upload` | ✅ | — |
| `editor_run_script` | ✅ | — |
| `editor_run_script_submit` | ✅ | — |
| `editor_create_animation_clip` | ✅ | — |
| `editor_execute_menu_item` | ✅ | — |
| `editor_safe_save_prefab` | ✅ | — |
| `editor_create_udon_program_asset` | ✅ | — |
| `editor_add_udonsharp_component` | ✅ | — |
| `editor_set_udonsharp_field` | ✅ | — |
| `editor_wire_persistent_listener` | ✅ | — |
| `editor_create_scene` | ✅ | — |
| `editor_save_scene` | ✅ | — |
| `editor_close_prefab` | ✅ | — |

issue #49 で `editor_execute_menu_item` / `editor_safe_save_prefab` / `editor_create_udon_program_asset` / `editor_create_scene` / `editor_save_scene` が監査ペア対象へ追加された（逆不可逆性原理: arbitrary code 実行・非 Undo の asset 改変）。`editor_batch_set_blend_shape` / `editor_apply_animation_clip` は Undo 可能な scene 変更のため監査ペア対象外（`confirm` / `change_reason` を渡すと `TypeError`）。

`validate_runtime(profile="clientsim")` も ClientSim が Play Mode と scene dirty state に触れうるため `confirm=True` + 非空 `change_reason` を要求する。既定 profile は `compile_only` で、ClientSim は明示 profile なしには実行されない。

`editor_serialized_property_read` / `editor_serialized_property_list` は read-only なので audit pair 対象外。`editor_serialized_property_write` は dry-run 既定だが、`confirm=True` の確定書き込みでは Undo / dirty / Prefab override state に触れるため `change_reason` を必須にする。

`editor_create_generated_asset` / `editor_move_asset` は issue #116 の AssetDatabase-backed project asset 操作で、`confirm=False` dry-run は Bridge に到達して AssetDatabase state を読むが `project_root` / `out_report` / `change_reason` を検証しない。`confirm=True` では Python 境界で `project_root` → `out_report` → `change_reason` の順に検証し、`OUT_REPORT_REQUIRED` などの監査/report error は Bridge 呼び出し前に返す。成功・失敗どちらでも、最終 MCP response と同一 JSON を `out_report` に排他作成で書く。
