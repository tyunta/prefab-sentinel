# API共通仕様

MCP ツールが返す応答エンベロープの形状とエラーコードの正本。`README.md` はこのドキュメントへのポインタのみを持つ。

## レスポンスフォーマット

ツールの種類によって 2 つのレスポンス形式を使い分ける。

**参照系ツール**（`get_unity_symbols`, `find_unity_symbol`, `find_referencing_assets`）— ペイロード直接返却:

```json
{
  "asset_path": "Assets/Player.prefab",
  "symbols": [ ... ]
}
```

`find_referencing_assets` は直接ペイロード:

```json
{
  "matches": [ ... ],
  "target": "queried_asset_or_guid",
  "metadata": {
    "total_count": 3,
    "truncated": false,
    "scope": "Assets/...",
    "asset_path": "Assets/Player.prefab",
    "asset_missing": false
  }
}
```

32 文字 GUID が project meta index に無く、`scope` が指定されている場合も `find_referencing_assets` / `where_used` は usage-list 形状を維持する。この missing target では `metadata.asset_path`（または service data の `asset_path`）は `null`、`asset_missing` は `true` になる。`scope` 無しの missing GUID は従来通り `REF001`。

`scope` または path target の stat / permission failure は raw `OSError` / `PermissionError` を漏らさず、`where_used` service failure として typed response に変換する。scope path status と target asset path status は `REF404`、target `.meta` の path status / metadata read failure は `REF001` を返す。MCP wrapper の `find_referencing_assets` は成功時の直接ペイロード形状を維持し、service failure 時だけ typed code/message を含む `ToolError` へ変換する。

該当なしは空配列（`"matches": []`）で表現する。インフラエラー（ファイル不在等）は MCP `ToolError` で伝播。

**操作系・検証系・orchestrator 系ツール** — 標準エンベロープ:

```json
{
  "success": true,
  "severity": "info|warning|error|critical",
  "code": "TOOL_SPECIFIC_CODE",
  "message": "human readable",
  "data": {},
  "diagnostics": [
    {
      "severity": "info|warning|error|critical",
      "code": "DIAGNOSTIC_SPECIFIC_CODE",
      "message": "human readable",
      "data": {}
    }
  ]
}
```

### Editor Bridge 共通エンベロープと response file 契約

Editor Bridge の transport が受理する共通エンベロープは、次の **6 個の必須フィールド**である。`success` は JSON boolean（数値ではない）、`severity` は `info` / `warning` / `error` / `critical`、`code` と `message` は string、`data` は object、`diagnostics` は array でなければならない。`success=true` と `severity="error"` または `"critical"` の組合せは不正である。transport が補う `bridge_mode`、`action`、`request_id` 等はこの共通6フィールドの外側にある補助 metadata であり、共通契約を置換しない。

file-IPC request / response の `protocol_version` は全 route（editor-control / runtime / patch）で integer `2` 固定である。editor-control consumer は不一致を `EDITOR_BRIDGE_RESPONSE_SCHEMA`、runtime consumer は `RUN_PROTOCOL_ERROR`、patch consumer は `SER_BRIDGE_PROTOCOL_VERSION` として拒否する。0.9.32 以前の version 1 client / Bridge を受理する互換経路は持たない。patch payload の `plan_version=2` は操作計画 schema のversionであり、同じ数値でも file-IPC protocolとは別の正本である。

共通エンベロープは transport shape だけを保証する。各操作の parser が、その操作に固有の `code` と `data` の意味・必須フィールド・`diagnostics` entry の追加制約を検証する。したがって、shape が正しい `success=false` 応答（たとえば warning severity の soft-negative）は成功に変換せず、操作固有の failure として保持する。

Python 側は response file を symlink ではない regular file として開き、開いた file descriptor の種別を確認してから読む。読み取り上限は **16 MiB (`16 * 1024 * 1024` bytes、ちょうど 16 MiB は受理)** で、UTF-8 の標準 JSON だけを受理する。非 regular file、上限超過、open / read / close 失敗、UTF-8 decode 失敗、または host `float` 変換後に非有限となる JSON number は file-read failure である。editor-control file transport は `EDITOR_BRIDGE_RESPONSE_READ`、runtime file transport は `RUN_EDITOR_BRIDGE_RESPONSE` へ写像する。file を正常に読めても共通6フィールドを満たさない場合、editor-control transport は `EDITOR_BRIDGE_RESPONSE_SCHEMA`、runtime parser は `RUN_PROTOCOL_ERROR` を返す。

Unity 側の atomic response write と direct fallback が両方失敗した場合、Bridge は元の `{uuid}.request.json` を `{uuid}.publication-failed.json` へ rename する。Python transport はこの marker の内容を読まず、editor-control は `EDITOR_BRIDGE_RESPONSE_PUBLICATION_FAILED`、runtime は `RUN_EDITOR_BRIDGE_RESPONSE_PUBLICATION_FAILED` を timeout 前に返す。両 envelope は固定 message と `data.state_unknown=true` を持ち、request / response path、exception type / message、marker 内容を公開しない。marker 観測後は response / response tmp / marker を削除し、通常の request cleanup も完了する。marker rename 自体が失敗した場合、Unity 側は元 request を削除せず完全な例外を private log に残す。

| code | 発生境界 |
|---|---|
| `EDITOR_BRIDGE_RESPONSE_READ` | editor-control file transport が response file を受理できない。 |
| `RUN_EDITOR_BRIDGE_RESPONSE` | runtime file transport が response file を受理できない。 |
| `EDITOR_BRIDGE_RESPONSE_PUBLICATION_FAILED` | editor-control request は処理されたが、Unity が response を atomic/direct のどちらでも公開できなかった。 |
| `RUN_EDITOR_BRIDGE_RESPONSE_PUBLICATION_FAILED` | runtime request は処理されたが、Unity が response を atomic/direct のどちらでも公開できなかった。`data.executed=false` は trusted execution confirmation がないことを示し、`data.state_unknown=true` が副作用状態不明を明示する。 |
| `EDITOR_BRIDGE_RESPONSE_SCHEMA` | editor-control transport が read 済み payload の共通6フィールドまたは success/severity rule を満たさない。 |
| `RUN_PROTOCOL_ERROR` | runtime parser が共通 envelope、または `validate_runtime` action 固有 payload（`data.executed` や diagnostics entry など）を検証できない。`data.read_only=true`, `data.executed=false` を返す。 |
| `EDITOR_REFLECT_RESPONSE_SCHEMA` | `editor_reflect` が共通 envelope 後の成功 payload に string `data.reflect_result_json` を得られない、またはその string を JSON として parse した値が object でない。direct object の `data.reflect_result_json` は受理しない。valid な `success=false` bridge response はそのまま返す。 |
| `ASSET_DELETE_RESPONSE_SCHEMA` | `delete_assets` が共通 envelope 後の成功 payload に `DELETE_ASSETS_OK`、string-only `deleted_paths`、string-only `failed_paths` を得られない。valid な bridge failure は operation-owned failure に投影する。 |
| `WRITE_RESPONSE_SCHEMA` | write wrapper が共通 envelope、または object でない diagnostics entry を得られない。diagnostics object の `detail` / `evidence` は optional で、欠落時は空 string として扱い、存在する場合だけ string でなければならない。valid な `success=false` result は保持する。 |

`diagnostics[]` の wire 上 contract は単一の 4 キー dict `{severity, code, message, data}` に統一されている（issue #244 以降の標準形、issue #304 でレガシー経路も同 contract へ adapt 済み）。`mcp_tools_validation.py:127` 等の新規 emitter は `ToolResponse.to_dict()` の戻り値に対し直接この 4 キー dict を append する（例: `IGNORE_GUIDS_FILE_LOADED`）。レガシー orchestrator 経路で構築される `prefab_sentinel.contracts.Diagnostic` dataclass も、`ToolResponse.to_dict()` 内の `_diagnostic_to_wire` adapter を通じて同じ 4 キー dict へ正規化される: `Diagnostic.detail` → wire `code`、`Diagnostic.evidence` → wire `message`（空文字なら `code` フォールバック）、`Diagnostic.path` / `Diagnostic.location` は非空時のみ `data` 配下に格納される。`mcp_tools_session._build_session_diagnostic` ヘルパは session-level の ad-hoc 経路（`deploy_bridge` / `get_project_status`）が同 contract で wire に乗ることを保証し、`get_project_status` は live Bridge `get_editor_state` diagnostics も同じ 4 キー shape に正規化して status envelope へ引き継ぐ。

wire `severity` の決定規則（issue #4）: `Diagnostic` dataclass は任意の per-item `severity`（`str | None`、既定 `None`）を持つ。`_diagnostic_to_wire` は **diagnostic 自身の `severity` が設定されていればそれを優先**し、`None` のときはエンベロープの `severity` を継承する（`diag.severity or default_severity`）。これにより 1 つのエンベロープ内で個々の diagnostic が異なる severity を運べる（例: envelope が `error` でも一部 diagnostic は `warning`）。per-item `severity` は `Severity` 語彙に対して検証されない任意文字列であり、設定しない限り wire 出力は従来と byte-identical。

## Bridge deployment response (`deploy_bridge`)

`deploy_bridge` は標準エンベロープを返し、`data` には project-private path や raw exception を含めず、検証できた manifest と transaction state だけを載せる。成功時は source と配備先の byte manifest が一致し、`manifest_sha256` と `bridge_version` が配備先から独立再検証済みである。failure でも判明している state field は同じ意味を保つ。

| field | 契約 |
|---|---|
| `source_manifest_sha256` | package 側 Bridge source bundle の aggregate SHA-256。 |
| `manifest_sha256` | 最新の target 検証で complete と確認できた場合の aggregate SHA-256。ownership publication 直前の target 検証が失敗した場合、より前の成功値は現在値として扱わず空文字列にする。Issue #186 が checkout identity との照合に使う。 |
| `bridge_version` | 最新の target 検証で確認した Bridge version。ownership publication 直前の target 検証が失敗した場合、より前の成功値は現在値として扱わず空文字列にする。Issue #186 が reload 後の Bridge identity との照合に使う。 |
| `source_file_count` | source manifest に含まれる `.cs` / `.asmdef` 数。 |
| `managed_entry_count` / `unmanaged_entry_count` | 最終 target の regular-file 数 / preflight で検出した未所有 entry 数。 |
| `preserved_meta_count` / `stale_owned_count` | 維持した owned `.meta` 数 / 新 manifest から除外した旧 owned entry 数。 |
| `staging_prepared` / `staging_verified` | complete staging を作成したか / staging bytes を source manifest と再照合したか。 |
| `promotion_state` | `not_attempted` / `already_current` / `installed_fresh` / `promoted` / `rolled_back` / `rollback_failed` のいずれか。`already_current` は現在の project root / Bridge instance ID と一致する fresh 応答で観測した running Bridge version、記録済み manifest、target bytes がすべて source manifest に一致し、target move / refresh を行わず transaction staging だけを破棄した成功状態。running identity / version を未観測または不一致なら no-op にしない。 |
| `barrier_used` | existing target の promotion が Unity AssetDatabase refresh barrier を取得したか。fresh install は `false`。 |
| `rollback_attempted` / `rollback_restored` | rollback を試みたか / exact old manifest を target に復元できたか。 |
| `backup_retained` | exact old recovery backup が transaction 内に保持されていることを byte 検証できたか。directory の存在だけでは `true` にしない。 |
| `target_complete` | 最新の target 検証で exact old または exact new manifest の complete set を確認できたか。ownership record の load / atomic write がその後に失敗した場合は直前の成功を保持するが、ownership publication 直前の target 検証失敗では `false` に戻す。 |
| `ownership_published` | 検証済み target identity を ownership record へ atomic publication 済みか。 |
| `transaction_retained` | deterministic recovery のため transaction directory を保持したか。 |
| `deployed_files` | `target_complete=true` のときだけ返す、sort 済み相対 Bridge file 名。 |
| `recovery_required` | cleanup failure で transaction が残った場合だけ追加される `true`。通常応答では省略。 |

安定 result code は次の 19 個。未知の private action code や transport 詳細を新しい public code として透過しない。

| code | 発生境界 |
|---|---|
| `DEPLOY_OK` | byte-equal existing target の再利用、fresh install、または existing-target promotion の byte 検証、ownership 状態、必要な cleanup が完了した。 |
| `DEPLOY_NO_PROJECT` | `activate_project` 前に呼ばれた。 |
| `DEPLOY_SOURCE_NOT_FOUND` | package Bridge source bundle が存在しない、空、または許可された regular non-link `.cs` / `.asmdef` set を構成できない。記録済み target の preflight（Issue #212）と、promotion 後の ownership publication 直前（Issue #256）の manifest 読み取り・構築失敗も、実際の manifest 不一致へ変換せず、この固定 code と path-free message を保持する。後者は ownership record を更新せず transaction / backup を保持する。 |
| `DEPLOY_OUTSIDE_PROJECT` | target / transaction path が normalized project containment を満たさない。 |
| `DEPLOY_TARGET_UNMANAGED` | target に ownership record で所有を証明できない entry がある。 |
| `DEPLOY_OWNERSHIP_INVALID` | ownership record が欠損、malformed、重複、記録済み target / entry が欠落、または正常に構築した target manifest が記録と不一致。manifest 読み取り・構築失敗を不一致と見なさない。 |
| `DEPLOY_PARENT_CONFLICT` | 親 directory に未所有の Bridge-looking sibling があり、assembly conflict の恐れがある。削除は行わない。 |
| `DEPLOY_STAGING_FAILED` | transaction 作成、source copy、private manifest publication など staging 構築そのものに失敗した。cleanup failure には使わない。 |
| `DEPLOY_STAGING_MISMATCH` | staged bytes / manifest / Bridge version が source identity と一致しない。 |
| `DEPLOY_CROSS_FILESYSTEM` | complete-directory rename に必要な target と transaction の同一 filesystem 条件を満たさない。 |
| `DEPLOY_CLEANUP_FAILED` | preparation abort または complete target outcome 後の transaction cleanup に失敗した。`transaction_retained` / `recovery_required` は実際の residue、`backup_retained` は previous manifest と byte-identical な canonical backup が存在する場合だけ `true`。pre-promotion abort では Assets を変更しない。 |
| `DEPLOY_LOCK_REPLACED` | project-global deploy lock の file identity が acquisition 中に差し替わった。mutation は開始しない。 |
| `DEPLOY_BARRIER_UNAVAILABLE` | nonempty existing target に必要な private `promote_bridge_bundle` action を接続中 Bridge が持たない、または barrier を取得できない。mutation は開始しない。 |
| `DEPLOY_PROMOTION_FAILED` | private promotion が完了しない、transport outcome が曖昧、または deploy lock を取得できない。ambiguous outcome では recovery evidence を保持する。 |
| `DEPLOY_ROLLED_BACK` | promotion 失敗後、private action が exact old target を復元した。deploy 自体は失敗。 |
| `DEPLOY_ROLLBACK_FAILED` | exact old target を復元できない critical failure。検証できた complete backup と transaction を保持する。 |
| `DEPLOY_FINAL_MANIFEST_MISMATCH` | promotion 後に正常構築できた target manifest が期待 manifest と一致しない。ownership publication 直前の再検証で発生した場合も builder failure とは区別し、ownership record を更新せず transaction / backup を保持する。 |
| `DEPLOY_OWNERSHIP_WRITE_FAILED` | complete target は検証済みだが ownership record の atomic publication に失敗した。 |
| `DEPLOY_REFRESH_FAILED` | complete target outcome 後、refresh barrier 保持中の `AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport)` 呼び出しに失敗した。code 名は既存 contract を維持する。成功時も synchronous added / removed source inventory import が完了したことだけを表し、Unity compilation / reload success は主張しない。 |

Issue #256: promotion 後の最初の target 検証が成功しても、ownership publication 直前の検証が `DEPLOY_OUTSIDE_PROJECT` / `DEPLOY_SOURCE_NOT_FOUND` / `DEPLOY_FINAL_MANIFEST_MISMATCH` で失敗した場合、その後の public envelope は `manifest_sha256=""`、`bridge_version=""`、`target_complete=false` とし、失敗 code と ownership 前検証段階を固定 message で保持する。検証の retry、ownership publication、transaction cleanup は行わない。これに対し、最新の target 検証成功後に ownership record の load / atomic write が失敗した場合は、既知の manifest / version と `target_complete=true` を維持する。この分類は検証証拠を正しく表すものであり、間欠的な filesystem failure の原因を修正したという主張ではない。

## MCP protocol surface

stdio は二つの era を持つ。modern `2026-07-28` は `server/discover` / `tools/list` / `tools/call` と `notifications/cancelled`、legacy `2025-11-25` と `2025-06-18` はそれぞれ同じ `initialize` / `tools/list` / `tools/call` と `notifications/initialized` / `notifications/cancelled` を受理する。いずれも Tools-only で、Tool 名、入力 schema、tool result、domain envelope は共有する。どちらの legacy `initialize` も成功後に client が `notifications/initialized` を送ってから通常 operation に入る。

legacy `initialize` の product-owned result は次の値へ正規化する。`protocolVersion` は要求した `2025-11-25` または `2025-06-18` をそのまま echo する。SDK が選択 revision 用に serialize した dictionary を `call_next` の内部から受け取った後に、ここに示す product-owned field だけを置き換える。`serverInfo` は modern discovery と同じ package name / installed package version、`instructions` は modern discovery と byte-for-byte 同じ次の文字列である。次は `2025-11-25` request の例であり、`2025-06-18` request はその値だけを `2025-06-18` にする。

```json
{
  "protocolVersion": "2025-11-25",
  "capabilities": {"tools": {"listChanged": false}},
  "serverInfo": {
    "name": "prefab-sentinel",
    "version": "<installed package version, identical to modern discovery>"
  },
  "instructions": "activate_project selects the process-wide active Unity project. One process represents one logical client/project scope. Tool calls execute serially. Normal inspection, dry-run, and confirm entry points remain unchanged."
}
```

modern `server/discover` と HTTP は `2026-07-28` のみである。したがって discovery `supportedVersions` は常に `["2026-07-28"]` であり、legacy を必要とする client は別の stdio process を `initialize` で開始する。

## inspection progress / timeout metadata

長時間化しやすい read-only inspection / validation (`inspect_hierarchy`, `inspect_wiring`, `validate_all_wiring`, `validate_materials`) は、完了応答または timeout 応答の `data` に次の共通 metadata を載せる。

| field | 説明 |
|-------|------|
| `progress_summary[]` | 実行済み stage の ordered summary。各要素は少なくとも `name`, `completed` を持ち、信頼できる count がある場合だけ `count` を持つ。 |
| `partial_counts` | stage 名から count への dict。実測できた count だけを載せ、推測値は入れない。 |
| `current_or_slowest_step` | timeout / partial 応答で operator が見るべき現在または遅い stage。完了応答では省略される場合がある。 |
| `suggested_next_action` | scope narrowing や `inspect_material_asset(mode="summary")` など、次に取るべき具体的な縮小手順。 |

`INSPECTION_TIMEOUT` は incomplete scan を表す successful/failed の通常結果とは別の typed timeout envelope であり、`diagnostics_baseline` は incomplete current key を分類できないため載せない。

## wiring actionability metadata

`inspect_wiring` / `validate_all_wiring` は raw null-cause / duplicate-cause label を保持したまま、別軸として `actionability` を返す。語彙は `actionable`, `expected`, `optional`。unknown null / duplicate は常に `actionable` に残し、known pattern だけを `optional` または `expected` に分類する。summary には `actionability_counts` が入り、`script_filter` 使用時は matched component / field を中心に数え、out-of-scope diagnostics は明示 opt-in まで畳む。nested prefab の `source_only` / `placeholder` は `entry_kind` として表し、actionability count には混ぜない。

known initial patterns:

- uGUI `ScrollRect` の horizontal scrollbar 未配線 null は `optional`。
- uGUI `Button.m_TargetGraphic` と script の `background` field が同一 Image を指す duplicate は `expected` で、diagnostic severity は info に下げる。

## `inspect_material_asset` response

`inspect_material_asset(asset_path, mode="full", property_names=None)` は既定 `full` では従来の structured material tree / properties shape を返す。`mode="summary"` は shader identity、main texture、requested property values、kind 別 counts、`read_only=true` の narrow projection だけを返し、full tree / properties arrays は返さない。`property_names` は summary mode の selected properties を絞るための opt-in で、unknown property は selected result に合成しない。

未知の `mode` は `INSPECT_MATERIAL_ASSET_INVALID_MODE` を返し、`data.accepted_modes` に `full` / `summary` を載せる。fallback で full mode に戻さない。

## `validate_materials` response

`validate_materials(scope: str | None = None, include_details: bool = False, timeout_sec: float | None = None)` は read-only の静的 Material / shader / TMP / icon-font validator。`scope` は file または directory を受け付け、明示 scope が無い場合だけ activate 済み session scope を使う。明示 scope と session scope のどちらも無い場合は project root scan にフォールバックせず、`MATERIAL_VALIDATION_SCOPE_REQUIRED` を返す。完了応答は deterministic evidence order を維持しつつ `progress_summary` / `partial_counts` を返す。timeout 応答は `INSPECTION_TIMEOUT` で、`suggested_next_action` は scope narrowing または `inspect_material_asset(mode="summary")` を案内する。

成功・失敗コード:

| コード | success | severity | 意味 |
|--------|---------|----------|------|
| `MATERIAL_VALIDATION_OK` | `true` | `info` | scope 内の supported Unity text targets を読み終え、validation findings が無い。supported target が 0 件の既存 scope もこのコードで返る。 |
| `MATERIAL_VALIDATION_FINDINGS` | `false` | `warning` | generic risk または declarative rule finding が 1 件以上ある。schema/read error は無い。 |
| `MATERIAL_VALIDATION_SCOPE_REQUIRED` | `false` | `error` | MCP wrapper で明示 scope も activate 済み session scope も無い。orchestrator は呼ばれない。 |
| `MATERIAL_VALIDATION_SCOPE_NOT_FOUND` | `false` | `error` | resolved scope が存在しない、project root 外、または usable scope として扱えない。 |
| `MATERIAL_RULES_INVALID` | `false` | `error` | project root の `config/material_validation_rules.json` が invalid JSON または schema 不一致。validation scan は実行しない。 |
| `MATERIAL_VALIDATION_READ_ERROR` | `false` | `error` | in-scope supported Unity text asset の読み取りで validation の信頼性を保てない failure があった。 |

`data` の基本形:

| field | 説明 |
|-------|------|
| `summary` | scanned file / material / renderer slot / TMP evidence / folder entry 件数。 |
| `rule_config` | `status` (`absent` / `loaded` / `invalid`), `path`, loaded rule family counts。absent config は error ではなく、generic checks のみを意味する。 |
| `read_only` | 常に `true`。asset repair や patch application は行わない。 |
| `details` | `include_details=true` のときだけ返る。material asset evidence、renderer slot evidence、TMP/font evidence、folder evidence を含む。TMP の ZTest / atlas など静的に読めない optional fields は合成せず省略する。 |

diagnostic code:

| コード | severity | 説明 |
|--------|----------|------|
| `MATERIAL_SHADER_MISSING` | `warning` | `.mat` の shader reference が serialized evidence 上で欠落している。 |
| `MATERIAL_SHADER_UNRESOLVED` | `warning` | `.mat` の shader GUID が project meta index で解決できない。 |
| `MATERIAL_SLOT_UNRESOLVED` | `warning` | renderer material slot の material GUID が project meta index で解決できない。 |
| `MATERIAL_SHADER_POLICY_MISMATCH` | `warning` | loaded `shader_name_policies` の expected shader と evidence source の shader 名が一致しない。 |
| `MATERIAL_SHARED_GROUP_MISMATCH` | `warning` | loaded `shared_material_groups.expected_material` と selected renderer slot material が一致しない。 |
| `MATERIAL_SHARED_GROUP_DRIFT` | `warning` | loaded `shared_material_groups` が expected material 無しで複数 candidate material を検出した。winner は宣言しない。 |
| `MATERIAL_FOLDER_POLICY_VIOLATION` | `warning` | loaded `folder_policies` の disallowed extension または classifiable asset kind に該当した。unknown kind は silent。 |
| `MATERIAL_VALIDATION_READ_ERROR` | `error` | supported Unity text asset を UTF-8 text として読めない等の per-file read failure。 |

## `get_project_status` live/saved status metadata

`get_project_status()` は session / cache / scope の saved state に加え、Bridge が接続済みの場合だけ live `get_editor_state` を呼んで `data.editor_state` と status top-level summary fields を更新する。live 由来の fields は `state_source="live_editor"` を伴う。offline symbol / serialized YAML inspection の authority は saved disk であり、未保存 Unity state はこの status surface で確認する。

live snapshot は `unity_version=Application.unityVersion` と `required_packages[]` も返す。required set は `com.vrchat.base`, `com.vrchat.worlds`, logical capability `udonsharp` の固定順で、各 entry は `{name, ready, version}`。Base / Worlds は Unity 2022.3 の `PackageInfo.GetAllRegisteredPackages()` で現在ロード済みの package を取得し、`PackageInfo.name` の ordinal 完全一致で照合する（同versionに package-name 直接検索 API は存在しない）。SDK 3.4.0 以降の UdonSharp は Worlds package に統合されているため、`udonsharp` は Worlds package の登録と loaded `UdonSharp.Editor` assembly の両方を要求し、version は Worlds package version を記録する。`UdonSharp` という assembly は統合版の出力名ではなく、実際の editor capability assembly を示さない。local acceptance は pre-deploy の clean Editor-state gate と post-reload の environment gate を分け、後者で `unity_version` が `2022.3.*`、かつ三件すべての `ready=true` と non-empty version であることを fixture mutation 前に要求する。post-reload gate は assembly reload 中の一時的な `data.bridge.connection_state="unavailable"` だけを1秒間隔・最大120秒で待ち、それ以外の状態や readiness failure は即時評価する。自動 install や旧独立 package-name fallback は行わない。

`data.bridge` は transport observation の公開投影で、`connected`, `connection_state`（`connected` / `not_configured` / `unavailable` / `misconfigured`）, `code`, `blocker_class`, `suggested_next_action` の安定フィールドだけを返す。`UNITYTOOL_BRIDGE_WATCH_DIR` の実値、IPC request/response path、生の `OSError` message、stack trace は `bridge_status()` / `ProjectSession.status()` / `get_project_status()` のいずれにも含めず、probe failure の実pathと例外は private Python log にだけ記録する。Bridge由来の `editor_state` も既知state fieldsへ投影し、path fields / dirty identity arrays / `open_scenes[].path` は空文字または正規化可能な `Assets/...` だけを許可する。`\`、空segment、`.` / `..` segmentを含む値はproject-relative identityとして扱わない。Bridge diagnosticsは既知status codeのstable messageとallowlisted locationだけへ変換し、unknown codeは `BRIDGE_DIAGNOSTIC`、unknown failure codeは `EDITOR_BRIDGE_ERROR` へ畳む。raw `code` / `message` / `detail` / `path` / `evidence` はprivate logへ残し、raw blocker metadataは公開投影にもprivate logにも複製せず破棄する。失敗envelopeのraw message/dataもpublic diagnosticへ複製しない。session identity の `project_root` / `expected_project_root`、live `actual_project_root`、project-root mismatch、正規な `Assets/...` identity は redaction 対象外として維持する。

issue #179 の watch identity が fresh な private status により mismatch と判定された場合、`get_project_status()` は live `get_editor_state` を送らず、`success=true`, `severity="warning"`, `code="SESSION_STATUS"` を返す。`data.bridge` は `connected=false`, `connection_state="misconfigured"`, `code="EDITOR_BRIDGE_WATCH_DIR_MISMATCH"`, `blocker_class="watch_dir"`, `suggested_next_action="Use the same watch directory for Codex and the Unity Editor Bridge."` である。`data.blockers[]` は `{blocker_class="watch_dir", state_source="bridge_transport", message="Configured watch directory differs from the active Unity Editor Bridge watch directory.", suggested_next_action="Use the same watch directory for Codex and the Unity Editor Bridge."}` という1件だけになる。

issue #194 以降、各 registered MCP session は最後の fresh status（match / mismatch）を一つだけ追跡する。その観測後 5000 ms 以内（境界を含む）の status file 欠落は Unity reload 中の transition として `EDITOR_BRIDGE_STATUS_TRANSIENT` / `connection_state="unavailable"` へ投影し、`SESSION_STATUS` warning のまま blocker を返さず live request も送らない。初回欠落、5000 ms 超の欠落、stale、schema 不正、読取不能は `EDITOR_BRIDGE_STATUS_UNAVAILABLE` と `bridge_connection` blocker 1件へ投影し、live request を送らない。persistent outage の private ERROR は同じ outage につき1回だけ記録し、fresh status で再armする。`activate_project` が成功した場合だけ session tracker を reset する。private status path `Library/PrefabSentinel/bridge-status-v1.json`、marker IDs、watch paths、timestamps、private status content、raw exceptions are not public。

`data.blockers[]` は shared blocker vocabulary を使う:

- `watch_dir`
- `bridge_connection`
- `compile_or_build`
- `playmode_transition`
- `prefab_stage_for_scene_bound_operation`
- `dirty_or_save_blocker`

各 blocker は `blocker_class`, `state_source`, `message`, `suggested_next_action` を持つ。watch-dir missing / unavailable / mismatch の分類でも、`evidence` に configured/reported path やprobe例外を載せない。watch-dir mismatch の private identity 検証は issue #179 で実装済みであり、classifier は上記の stable public projectionだけを維持する。dirty identity fields (`dirty_scene_paths`, `dirty_prefab_paths`, `dirty_material_paths`, `dirty_asset_paths`) は live Editor API が列挙できた `Assets/...` identityだけを返す。`dirty_asset_paths` は `AssetDatabase.IsNativeAsset` が true の Unity serialized asset に限定し、`.shader` など importer が生成した loaded object の dirty flag を未保存ファイルとして扱わない。Bridge が connected と報告された後に `get_editor_state` が失敗した場合も status envelope は `success=true` のまま warning となり、diagnostic `data` に `blocker_class` と `suggested_next_action` を載せる。

## SerializedProperty editor payload (issue #112)

`editor_serialized_property_read` / `editor_serialized_property_list` / `editor_serialized_property_write` は live Editor Bridge の標準エンベロープを返す。Bridge からの raw carrier は `data.serialized_property_json` で、Python MCP wrapper は可能な場合に decode して `data.serialized_property` に同じ payload を格納する。decode できない carrier は envelope の `data.serialized_property_json` として残し、インフラ例外に丸めない。

`serialized_property` payload は raw `SerializedProperty.propertyPath` を正本にし、少なくとも `property_path` / `display_name` / `property_type` / `value_kind` / typed value fields（`bool_value` / `int_value` / `long_value` / `float_value` / `string_value` / `enum_name` / `enum_index`）/ `state` を運ぶ。ObjectReference では null / asset path / GUID / hierarchy path / type evidence、配列では `array_size` と子要素 summary、list では任意の `root_property_path` を起点にした `items` / `next_cursor` / `truncated`、write dry-run では `current` / `proposed` / `would_change` / dirty target / UdonSharp sync plan を含める。confirmed changed writes は Undo label、ApplyModifiedProperties 後の dirty state、Prefab override recording、outermost root / prefab asset path、UdonSharp sync result を evidence として返す。no-op writes は Undo / dirty / override / sync を実行しない evidence を返す。

成功コード:

| コード | 説明 |
|--------|------|
| `EDITOR_CTRL_SERIALIZED_PROPERTY_READ_OK` | read が対象 property を解決して payload を返した。 |
| `EDITOR_CTRL_SERIALIZED_PROPERTY_LIST_OK` | list が traversal 結果を返した。 |
| `EDITOR_CTRL_SERIALIZED_PROPERTY_DRY_RUN_OK` | unconfirmed write が target と値を検証し、副作用なしの dry-run payload を返した。 |
| `EDITOR_CTRL_SERIALIZED_PROPERTY_NO_CHANGE` | requested value が現在値と同一で、Undo / dirty / override / sync を作らなかった。 |
| `EDITOR_CTRL_SERIALIZED_PROPERTY_WRITE_OK` | confirmed changed write が SerializedObject 経由で適用され、dirty / override / sync evidence を返した。 |

## Editor asset operations (issue #116)

`editor_create_generated_asset` / `editor_move_asset` は live Editor Bridge の標準エンベロープを Python 境界で検証・射影して返す。成功時 `diagnostics[]` は `{severity, code, message, data}` の 4 キー wire shape に正規化され、Bridge root/data の余剰キーは成功 payload からは公開しない。Bridge の成功 shape が壊れている場合は `UNITY_BRIDGE_INVALID_RESPONSE` と `PARTIAL_SIDE_EFFECT_REQUIRES_REVIEW` diagnostic を返す。確定適用後の report write failure は rollback せず、`OUT_REPORT_WRITE_FAILED` に元 operation result/error を含める。

`editor_create_generated_asset` の入力は `asset_type`, `asset_path`, `parameters`, `confirm`, 任意の `project_root`, `out_report`, `change_reason`。`asset_type` は現状 `render_texture` のみを受理し、Bridge success payload は `unity_type`, `asset_path`, `guid`, `would_create`, `created`, `dry_run`, `saved`, `refreshed`, `dirty_before`, `dirty_after`, `name`, `applied_parameters` を返す。`applied_parameters` は snake_case `width`, `height`, `depth`, `format`, `read_write`, `filter_mode`, `wrap_mode`, `mip_map` を持つ。

`editor_move_asset` の入力は `source_asset_path`, `destination_asset_path`, `confirm`, 任意の `project_root`, `out_report`, `change_reason`。Bridge success payload は `source_asset_path`, `destination_asset_path`, `unity_type`, `before_guid`, `after_guid`, `guid_preserved`, `would_move`, `moved`, `dry_run`, `saved`, `refreshed`, `dirty_before`, `dirty_after`, `old_name`, `new_name`, `name_changed` を返す。

どちらの tool も `confirm=False` dry-run では audit/report 引数を検証せず Bridge に AssetDatabase state を問い合わせる。`confirm=True` では Python 境界で `project_root` → `out_report` → `change_reason` の順に検証し、`out_report` へ最終 response と同一 JSON を排他作成する。

## Inspector profile responses

The three read-only Inspector profile tools use the standard envelope and require an Editor Bridge for serialized values.

- `inspect_serialized_surface(asset_path, symbol_path=None, include_override_origin=False)` returns `INSPECTOR_SERIALIZED_SURFACE_OK`. `data.surface` contains the last-saved target identity (including Unity's numeric `target.local_file_id` when available), ordered raw properties, current effective values, optional override origins, one-hop ObjectReference identities, and bounded source/custom-editor candidate evidence. Array `element_type` preserves Unity's raw `SerializedProperty.arrayElementType`; profile validation treats Unity's `PPtr<T>` form as compatible with declarative `ObjectReference`, while other element types remain exact matches.
- `inspect_with_profile(asset_path, view_name, symbol_path=None, include_override_origin=False)` returns one requested semantic view as `INSPECTOR_PROFILE_VIEW_OK`, or one of the distinct authoring/blocker states below. It never expands all views implicitly and never emits a patch plan.
- `validate_inspector_profile(profile_path, asset_path, symbol_path=None)` returns `INSPECTOR_PROFILE_VALIDATION_RESULT` only after whole-profile validation against a current surface. Mechanical success does not prove semantic truth and does not promote the profile.

A writer-enabled profile path is mechanically addressable only when the same Editor inspection that produced the surface also returns one exact target `local_file_id`, and the existing serialized-value writer accepts every declared operation over the complete view address set with `dry_run=true` and `confirm=false`. The Bridge obtains that identity with `AssetDatabase.TryGetGUIDAndLocalFileIdentifier`; validation does not reparse YAML or re-resolve a symbol path. It then uses the real resource grammar: a Prefab component carries that exact `file_id`, while a ScriptableObject root uses the open-asset `$asset` handle (the raw-surface address contract already guarantees that `.asset` inspection targets the root main asset). Scene writable declarations fail closed because the current scene writer has no exact local-fileID-to-component-handle grammar; semantic scene views remain readable. Writable `fields` entries require `expected_type`; writable `zipped_arrays` columns require `element_type`. Validation translates `set`, `set_element`, `append_row`, and `remove_row` to their actual scalar, element, insertion, or removal addresses and runs the real orchestrator dry-run; a missing ID, unsupported resource address, or rejected probe disables that writable declaration.

Authoring-state `data` includes `recommended_profile_path`, composite target identity and canonical address, `surface_summary`, an `inspect_serialized_surface` retry descriptor shaped as `surface_ref={"tool":"inspect_serialized_surface","args":{...}}`, `source_candidates_status`, `source_candidates_reasons` when degraded, at most two unique source candidates and one active custom-editor candidate, an empty degraded PropertyDrawer candidate set, the required authoring skill, and the next action. A `runtime_script` candidate is emitted only when Unity provides a non-empty project-relative script path; absence remains explicit in the target identity and degradation reasons instead of a null-path candidate. When no current surface exists, `surface_summary.available=false` and no serialized values are fabricated.

Unity's built-in `UnityEditor.GenericInspector` fallback is not a custom-editor candidate. A missing public `MonoScript` path degrades candidate discovery with `The target has no public MonoScript source.`; failure to select an active editor adds `Unity could not select an active editor for the target.` in that deterministic order.

ScriptableObject `.asset` inspection rejects a canonical loaded target while `EditorUtility.IsDirty(target)` is true. The Bridge returns `EDITOR_CTRL_INSPECTOR_SURFACE_DIRTY`, and the MCP boundary reports `INSPECTOR_SURFACE_UNAVAILABLE` with that Bridge diagnostic instead of presenting unsaved in-memory values as the last-saved surface. Finite `Float` properties preserve Unity's `numericType`: `Double` uses `doubleValue`, other floating-point values use `floatValue`, and both are emitted with invariant round-trip formatting. JSON has no non-finite numeric literals, so NaN and positive/negative infinity are represented as `null` rather than producing an invalid surface payload.

### Scene inspection lifecycle

Scene inspection snapshots the complete loaded Scene-manager state before resolving the target: loaded order, handle, normalized project-relative path, dirty bit, and active Scene identity. The state machine is fail-closed:

- exactly one clean loaded match is `Borrowed`; inspection neither opens nor closes it;
- exactly one dirty loaded match is `Dirty` and stops before target resolution or surface construction;
- more than one loaded match is `Ambiguous` and stops before choosing an instance;
- no loaded match is `Owned`; the Bridge opens only that Scene additively, closes only that owned Scene in cleanup, and, if required, restores the original valid loaded active Scene with `SceneManager.SetActiveScene`.

Completion compares the after-snapshot with startup Scene order/handle/path, active identity, and dirty state, then checks borrowed-target presence, owned-target cleanup, and active restoration. A failed condition suppresses the normal serialized surface and returns the ordered `failed_postconditions` tokens (`startup_scene_order`, `active_scene`, `startup_scene_dirty_state`, `borrowed_scene_missing`, `owned_close_not_attempted`, `owned_close_failed`, `owned_scene_present`, `active_scene_restore_failed`). Production does not call `RestoreSceneManagerSetup`, save a Scene, retry, or substitute another reference to satisfy these postconditions.

Raw Bridge failures map one-to-one to public terminal errors:

| raw Bridge code | public code | condition |
|-----------------|-------------|-----------|
| `EDITOR_CTRL_INSPECTOR_SCENE_DIRTY` | `INSPECTOR_SCENE_DIRTY` | The unique loaded target is dirty; ownership is `None`, and no open, close, or surface work occurs. |
| `EDITOR_CTRL_INSPECTOR_SCENE_AMBIGUOUS` | `INSPECTOR_SCENE_AMBIGUOUS` | Multiple loaded Scene instances have the requested path; ownership remains `None`. |
| `EDITOR_CTRL_INSPECTOR_SCENE_RESTORE_FAILED` | `INSPECTOR_SCENE_RESTORE_FAILED` | Cleanup, active restoration, or a complete after-state postcondition failed. |

The Python boundary accepts a Scene lifecycle response only when it is a known `success=false` raw code with exactly one matching error diagnostic and a closed evidence schema. It rebuilds the public response with empty top-level `data` and one sanitized diagnostic shaped as `{severity, code, message, data}`. Diagnostic `data` contains only `path`, `location`, `detail`, and `evidence`; `evidence` contains only `asset_path`, `ownership`, `matched_handles`, `cleanup_attempted`, `close_result`, `active_restore_attempted`, `active_restore_result`, `before`, `after`, and `failed_postconditions`. Paths must remain empty or project-relative `Assets/...`; ownership, result, snapshot, and postcondition values are allowlisted. Raw Bridge messages/data, exception markers, absolute paths, and unknown nested fields are not republished.

`inspect_serialized_surface` returns this sanitized terminal response before generic Bridge fallback. `inspect_with_profile` preserves it before authoring/profile-required fallback, and `validate_inspector_profile` returns it unchanged before any draft mutation. Integration warning attribution is limited to the controlled operation window around the single `editor_inspect_serialized_surface` dispatch; warnings produced by fixture setup, case isolation, or suite cleanup are not attributed to the inspection.

`recommended_profile_path` and project-local `profile_path` values are relative to the activated project. A selected bundled profile uses the stable `profiles/<filename>` identifier. Profile discovery and validation failures return stable diagnostics without absolute host or package paths.

When the Bridge is unavailable, full runtime identity and surface validation cannot be completed. Offline screening therefore preserves `INSPECTOR_SURFACE_UNAVAILABLE` when a profile matches the known composite identity, or when a profile without a fixed script GUID/fileID has the same short managed type as the offline script filename. Unknown assembly is not treated as a mismatch at this screening stage. This relaxed check is used only to decide whether `INSPECTOR_PROFILE_REQUIRED` would be premature: normal profile selection is unchanged, unrelated short types still allow authoring, and matching invalid or same-priority ambiguous profiles remain fail-closed surface blockers until the Bridge returns.

For a `fields` view, each rendered field preserves optional declarative `group` and `enum_map` metadata. The raw effective `value` remains unchanged. When `enum_map` is present, `enum_label` is the mapped label for an integer value or an Enum payload's integer `index`; it is `null` when no mapping exists or no integer key can be derived. Profiles do not execute any mapping logic beyond this literal key lookup.

After required request fields are validated, all three operations require an activated Unity project before address validation, profile access, or Editor Bridge dispatch. An inactive session performs no Bridge call and returns `PROJECT_NOT_ACTIVATED` with the operation-specific fixed message:

- `inspect_serialized_surface`: `Activate a Unity project before inspecting a serialized surface.`
- `inspect_with_profile`: `Activate a Unity project before inspecting with a profile.`
- `validate_inspector_profile`: `Activate a Unity project before validating an inspector profile.`

| code | success | severity | meaning |
|------|---------|----------|---------|
| `INSPECTOR_SERIALIZED_SURFACE_OK` | `true` | `info` | The Editor Bridge returned the last-saved raw surface. |
| `INSPECTOR_PROFILE_VIEW_OK` | `true` | `info` or `warning` | Exactly one requested view rendered; warning indicates a current zipped-array mismatch. |
| `INSPECTOR_PROFILE_VALIDATION_RESULT` | `true` | `info` or `warning` | The explicit profile is mechanically valid; warning retains current length mismatches and disables affected writes. |
| `PROJECT_NOT_ACTIVATED` | `false` | `error` | No Unity project is active; the operation stops before address, profile, or Editor Bridge work. |
| `INSPECTOR_SURFACE_ADDRESS_INVALID` | `false` | `error` | The component/ScriptableObject address shape is invalid; `data.field` identifies the offending field. |
| `INSPECTOR_SURFACE_TARGET_NOT_FOUND` | `false` | `error` | The last-saved asset/object/component cannot be resolved; `data.address` preserves the request. |
| `INSPECTOR_SURFACE_UNAVAILABLE` | `false` | `warning` | The current Editor-authoritative surface is unavailable. No YAML or live-state fallback is used. |
| `INSPECTOR_SCENE_DIRTY` | `false` | `error` | A unique loaded Scene target is dirty; no last-saved surface is returned. |
| `INSPECTOR_SCENE_AMBIGUOUS` | `false` | `error` | More than one loaded Scene has the requested path; no instance is selected. |
| `INSPECTOR_SCENE_RESTORE_FAILED` | `false` | `error` | Scene cleanup or exact Scene-manager state restoration failed; no normal surface is returned. |
| `INSPECTOR_PROFILE_REQUIRED` | `false` | `info` | No profile identity matches; a complete authoring payload is returned. |
| `INSPECTOR_PROFILE_INCOMPLETE` | `false` | `info` | The matching valid profile lacks the requested view; available views and unrelated length warnings are retained. |
| `INSPECTOR_PROFILE_INVALID` | `false` | `warning` | Discovery conflict, unsafe file, schema error, or whole-profile mechanical failure blocks rendering. |
| `INSPECTOR_VIEW_NAME_REQUIRED` | `false` | `error` | A present but empty `view_name` is rejected before target/profile/Bridge work. |

`INSPECTOR_ZIPPED_ARRAY_LENGTH_MISMATCH` is a warning entry carrying each current length. It does not invalidate the declarative profile by itself, but the affected view is non-writable. `INSPECTOR_PROFILE_PATH_UNSAFE` is the mechanical diagnostic used when an explicit profile path escapes its allowed root or is not a regular non-symlink JSON file.

## Audited runtime validation response (Issue #167)

### Runtime profile contract

| key | authoritative value |
|---|---|
| profile | required; no default |
| editor_console_only | read-only |
| compile_only | conditional/write |
| clientsim | conditional/write |
| console_authority | unity_log or editor_bridge (compile_only only) |
| generated_asset_policy | deny / create / replace |
| audit owner | api-reference.md |

`validate_runtime` は唯一の runtime action である。`profile` は必須で、空白または未指定は副作用前に `RUN_PROFILE_REQUIRED` (`success=false`, `severity="error"`, `data.field="profile"`, `data.executed=false`) を返す。許容 profile は `compile_only`、`editor_console_only`、`clientsim`。`editor_console_only` は read-only で、compile / generated asset / ClientSim を実行しない。`compile_only` と `clientsim` は write-class であり、いずれも `confirm=true`、非空 `change_reason`、`out_report` を要求する。`out_report` は project root 内かつ `Assets/` 外で、既存親 directory と atomic publication を preflight できなければ `OUT_REPORT_INVALID` または `OUT_REPORT_WRITE_FAILED` で Unity 副作用前に停止する。

`compile_only` の `console_authority` は `unity_log`（既定）または `editor_bridge` を明示選択する。`unity_log` は `log_file`（未指定時は `<runtime_root>/Logs/Editor.log`）だけを読み、`editor_bridge` は Bridge-owned callback buffer の `capture_console_logs` だけを読む。選択した authority が失敗または unavailable でも他方を自動探索・retry・fallback しない。不正値は Unity 副作用前に `RUN_CONSOLE_AUTHORITY_UNSUPPORTED` (`success=false`, `severity="error"`, `data.field="console_authority"`) で停止する。`editor_console_only` と `clientsim` の既存 policy は変更しない。

collection step は `data.console_authority` と `data.evidence_available` を返す。存在する空 log / 空 Bridge buffer は `evidence_available=true`, `line_count=0` であり、観測済み zero errors として分類・assert できる。`RUN_LOG_MISSING` は `evidence_available=false` であり、`compile_only` は classify/assert を実行せず、`RUN_ASSERT_OK` を生成せず、`VALIDATE_RUNTIME_RESULT.success=false` とする。compile 実行結果は Console state から独立した report `compile` section にそのまま保持する。

write-class profile の既定は `generated_asset_policy="deny"`、`allow_dirty_program_assets_before_compile=false`、`allow_dirty_scenes_before_compile=false`。policy は正確に `deny | create | replace` であり、`create` は missing canonical generated asset の新規作成だけ、`replace` は mismatched old generated asset の削除と canonical asset の作成を許可する。無効値は `GENERATED_ASSET_POLICY_INVALID` (`success=false`, `severity="error"`)。dirty override は明示 authority であり、同一既存 dirty identity の compile 起因変更は `attribution_unknown` として報告する。`allow_warnings` は console warning の classification だけを制御し、compile / generated / dirty side-effect warning を suppress または `info` へ downgrade しない。

成功・preflight rejection・compile failure を含む、report path reservation 後の全 terminal response は `data` と `out_report` に同一の `runtime_validation_report.v1` payload を持つ。top-level exact section は `schema_version`、`profile`、`audit`、`preflight`、`compile`、`clientsim`、terminal `result`。Bridge payload は evidence に使う前に strict schema で検証する。`preflight` は authorization / planned generated paths / dirty identity snapshots を、`compile` は `before` / `after` / `delta` / `generated_assets` を、`clientsim` は `executed` と実行時だけ `before` / `runtime` / `after` / `side_effect_report` を持つ。identity/path の delta arrays は newly-dirty / no-longer-dirty、planned / created / deleted generated assets、Scene before / after / newly-dirty を区別する。未実行 lane は省略せず、`compile_only` は `clientsim.executed=false` を返す。

reporting utility の Markdown 要約はこの v1 の `result.steps` と `preflight` / `compile` / `clientsim` を読み、実行状態・観測された結果・生成件数・ClientSim 差分の完全性を表示する。read-only `editor_console_only` の現行 flat pipeline も対象とする。要約の追加は raw Data JSON を変更せず、監査拒否を実行済みとして表示しない（Issue #242）。

Markdown の診断節も公開 wire diagnostic の `code` / `severity` / `message` / `data.path` / `data.location` を表示する。内部 `Diagnostic` の旧フィールド名を入力契約とせず、wire payload を保持したまま要約する（Issue #248）。

compound `clientsim` は report path、compile preflight、ClientSim readiness/state/lease preflight、initial Scene authorization をすべて compile 前に確定する。次に force compile を1回実行し、成功した場合だけ ClientSim を開始する。compile failure は partial dirty/generated evidence を保持したまま ClientSim / Play Mode を開始しない。compile と ClientSim の partial failure evidence は独立 section に残る。どの終端でも tool は save、dirty clear、revert、generated-asset cleanup を行わず、caller が保存または破棄を判断する。

### Runtime payload typed schema

The outer Bridge response is protocol_version: int, success: bool, severity: string, code: string, message: string, data: RuntimeData, and diagnostics: RuntimeDiagnostic[]. RuntimeDiagnostic is path: string, location: string, detail: string, and evidence: string.

RuntimeData is project_root: string, scene_path: string, profile: string, timeout_sec: int, udon_program_count: int, clientsim_ready: bool, read_only: bool, executed: bool, side_effect_report: ClientSimSideEffectReport | null, compile: CompileReport, and clientsim: ClientSimReport. data.side_effect_report equals clientsim.side_effect_report; udon_program_count equals compile.program_count. compile_only requires clientsim.executed=false. A clientsim execution requires an executed successful compile.

The report preflight object is completed: bool and diagnostics: RuntimeDiagnostic[]. The report audit object is confirm: bool, change_reason: string, generated_asset_policy: string, allow_dirty_program_assets_before_compile: bool, and allow_dirty_scenes_before_compile: bool. The terminal result object is result.success: bool, result.severity: string, result.code: string, result.message: string, result.diagnostics: RuntimeDiagnostic[], result.steps: object[], result.fail_fast_triggered: bool, result.field: string | absent, and result.console_evidence: ConsoleEvidence | absent. ConsoleEvidence is authority: string, available: bool, collection_code: string, and line_count: int; it is present after compile-only Console collection is attempted.

ClientSimReport is executed: bool, initial_scene_snapshot: SceneIdentity[], before: SceneSideEffectSnapshot | null, runtime: SceneSideEffectSnapshot | null, after: SceneSideEffectSnapshot | null, and side_effect_report: ClientSimSideEffectReport | null. When executed is false, before, runtime, after, and side_effect_report are null. When executed is true, side_effect_report is an object.

SceneSideEffectSnapshot is Roots: string[], Hierarchy: string[], Components: string[], AssetChangeCandidates: string[], Dirty: bool, and DirtyCount: int. ClientSimSideEffectReport is diff_complete: bool, diff_warnings: string[], scene_path: string, roots_before: string[], roots_runtime: string[], roots_after: string[], hierarchy_before: string[], hierarchy_runtime: string[], hierarchy_after: string[], components_before: string[], components_runtime: string[], components_after: string[], added_gameobjects: string[], removed_gameobjects: string[], added_components: string[], removed_components: string[], residual_added_gameobjects: string[], residual_removed_gameobjects: string[], residual_added_components: string[], residual_removed_components: string[], dirty_before: bool, dirty_runtime: bool, dirty_after: bool, dirty_count_before: int, dirty_count_runtime: int, dirty_count_after: int, and asset_change_candidates: string[].

### Compile audit typed schema

The runtime_validation_report.v1 compile section is CompileReport:

| field | type |
|---|---|
| executed: bool | compile phase ran |
| success: bool | compile phase succeeded |
| severity: string | info, warning, or error |
| code: string | compile terminal code |
| program_count: int | compile target count |
| before: Snapshot | before evidence |
| after: Snapshot | after evidence, including partial failure evidence |
| delta: Delta | before/after difference |
| generated_assets: GeneratedAssetReport | planned and actual generated paths |
| diagnostics: RuntimeDiagnostic[] | path, location, detail, evidence strings |

Snapshot is inventory_stable: bool, prefab_repair_paths: string[], related_assets: AssetIdentity[], loaded_scenes: SceneIdentity[], generated_asset_plan: GeneratedAssetPlan, and project_dirty_paths: string[]. AssetIdentity is guid: string, local_file_id: long, path: string, type: string, dirty: bool, and attribution_unknown: string[]. SceneIdentity is path: string, handle: int, dirty: bool, and attribution_unknown: string[]. GeneratedAssetPlan is the pure preflight model with planned_created_paths: string[] and planned_deleted_paths: string[].

Delta has exactly eleven string arrays: newly_dirty_paths: string[], no_longer_dirty_paths: string[], newly_dirty_scene_paths: string[], no_longer_dirty_scene_paths: string[], planned_created_paths: string[], planned_deleted_paths: string[], actual_created_paths: string[], actual_deleted_paths: string[], unrelated_dirty_paths_before: string[], unrelated_dirty_paths_after: string[], and attribution_unknown: string[]. GeneratedAssetReport has exactly planned_created_paths: string[], planned_deleted_paths: string[], actual_created_paths: string[], and actual_deleted_paths: string[]; all four arrays equal their namesake Delta arrays. An executed RUN_COMPILE_FAILED report has non-empty compiler diagnostics. Normal UdonSharp compiler diagnostics identify the ProgramAsset path, source C# path, and emitted error evidence.

The clientsim section is explicit even when unexecuted: executed: bool, initial_scene_snapshot: SceneIdentity[], before, runtime, after, and side_effect_report. Compound validate_runtime(profile="clientsim") completes all ClientSim preflight before its one force compile; it enters Play Mode only when compile succeeds. Neither success nor failure saves, clears dirty state, reverts, or cleans generated assets.

### Runtime/report error codes

| code | success | severity | condition |
|---|---|---|---|
| RUN_PROFILE_REQUIRED | false | error | profile absent or blank |
| RUN_CONSOLE_AUTHORITY_UNSUPPORTED | false | error | compile-only `console_authority` is not `unity_log` or `editor_bridge` |
| RUN_PROTOCOL_ERROR | false | error | request/response schema or strict operation payload invalid |
| RUN002 | false | error | ClientSim startup failure |
| VALIDATE_RUNTIME_PROFILE_UNSUPPORTED | false | error | unsupported profile |
| CHANGE_REASON_REQUIRED | false | error | write audit tuple incomplete |
| GENERATED_ASSET_POLICY_INVALID | false | error | policy is not deny, create, or replace |
| OUT_REPORT_INVALID | false | error | report path is under Assets |
| OUT_REPORT_REQUIRED | false | error | write-class request omitted out_report |
| OUT_REPORT_OUTSIDE_PROJECT | false | error | report path resolves outside project root |
| OUT_REPORT_WRITE_FAILED | false | error | report probe, reservation, or terminal publication failed |
| VALIDATE_RUNTIME_RESULT | true or false | info, warning, error, or critical | terminal orchestrator pipeline result; severity is at least compile severity, and unavailable compile-only Console evidence forces false |
| RUN_LOG_COLLECTED | true | info | selected Unity log exists and was observed; zero lines remain available evidence |
| RUN_LOG_MISSING | true (collection step) | warning | selected Unity log is unavailable; compile-only terminal fails before classify/assert |
| RUN_LOG_DECODE_WARN | true (collection step) | warning | selected Unity log is undecodable and unavailable; compile-only terminal fails before classify/assert |
| RUN_EDITOR_CONSOLE_COLLECTED | true | info | explicitly selected Bridge Console buffer was observed; zero entries remain available evidence |
| RUN_EDITOR_CONSOLE_ERROR | false | error | explicitly selected Bridge Console capture failed; no alternative authority is attempted |
| RUN_COMPILE_OK | true | info or warning | force compile succeeded; side-effect delta raises warning |
| RUN_COMPILE_FAILED | false | error | force compile failed with partial delta retained |
| UDON_GENERATED_ASSET_OUTCOME_MISMATCH | false | error | actual generated asset changes differ from the authorized plan |
| UDON_COMPILE_PREFLIGHT_INDETERMINATE | false | error | side-effect-free inventory was not stable |
| UDON_COMPILE_PREFAB_REPAIR_REQUIRED | false | error | user-owned Prefab repair is required |
| UDON_COMPILE_DIRTY_PRECONDITION | false | error | related program asset dirty without authority |
| UDON_COMPILE_DIRTY_SCENE_PRECONDITION | false | error | loaded Scene dirty without authority |
| UDON_GENERATED_ASSET_REPLACEMENT_REQUIRED | false | error | replacement is not allowed |
| UDON_GENERATED_ASSET_CREATION_REQUIRED | false | error | creation is not allowed |
| CLIENTSIM_CONFIRM_REQUIRED | false | error | validate_runtime(profile="clientsim") request reached the Bridge without the required audited write contract |
| CLIENTSIM_ALREADY_RUNNING | false | error | cleanup lease exists |
| CLIENTSIM_EDITOR_NOT_READY | false | error | Unity is not stable Edit Mode |
| CLIENTSIM_ACTIVE_SCENE_REQUIRED | false | error | requested Scene is not sole loaded active Scene |
| CLIENTSIM_DIRTY_SCENE | false | error | initial dirty-scene authorization failed |
| CLIENTSIM_START_SCENE_UNRESTORABLE | false | error | start-scene lease failed |
| CLIENTSIM_PREFLIGHT_TIMEOUT | false | error | preflight deadline failed |
| RUN_CLIENTSIM_SKIPPED | true | warning | public ClientSim API absent |
| RUN_CLIENTSIM_DISABLED | true | warning | project setting disabled |
| RUN_CLIENTSIM_OK | true | info | ClientSim reached network-ready state |
| CLIENTSIM_ENTER_PLAY_MODE_FAILED | false | error | Unity rejected Play Mode entry |
| CLIENTSIM_ENTER_PLAY_MODE_TIMEOUT | false | error | Unity did not enter Play Mode in time |
| CLIENTSIM_UNEXPECTED_PLAY_MODE_EXIT | false | error | Play Mode exited before terminal result |
| CLIENTSIM_READY_CHECK_FAILED | false | error | public readiness inspection failed |
| CLIENTSIM_READY_TIMEOUT | false | error | readiness did not complete in time |
| CLIENTSIM_EXIT_PLAY_MODE_FAILED | false | error | Unity rejected exit request |
| CLIENTSIM_EXIT_PLAY_MODE_TIMEOUT | false | error | Unity did not exit in time |
| CLIENTSIM_RESTORE_FAILED | false | error | previous start Scene was not restored and lease remains |
| CLIENTSIM_STATE_INVALID | false | error | persisted lifecycle state invalid |
| CLIENTSIM_STATE_CORRUPT | false | error | persisted lifecycle state corrupt |

CLIENTSIM_SIDE_EFFECT_DIFF_UNAVAILABLE and CLIENTSIM_SIDE_EFFECT_DIFF_DETECTED are warning diagnostics for executed evidence; neither permits a clean inference.

## ClientSim lifecycle response

`validate_runtime(profile="clientsim")` is an explicit, audited Play Mode operation. Before entering Play Mode, the requested scene must already be the only loaded scene and the active scene; otherwise `CLIENTSIM_ACTIVE_SCENE_REQUIRED` is returned without changing Editor state. The Bridge fixes the absolute operation deadline before snapshot/preflight work, snapshots the current in-memory scene, rejects a dirty scene unless `allow_dirty_scenes_before_compile=true`, verifies the public ClientSim settings/readiness API, leases the previous `EditorSceneManager.playModeStartScene`, temporarily sets that property to `null`, and enters Play Mode without opening or saving another scene. If preflight consumes the deadline, `CLIENTSIM_PREFLIGHT_TIMEOUT` is returned before a lease or Play Mode change. Readiness excludes persistent Resources prefab assets from `Resources.FindObjectsOfTypeAll` and requires exactly one non-persistent component in a valid loaded scene before invoking public `IsNetworkReady`.

The operation and an independent restoration lease are persisted in `SessionState` across domain reloads. A terminal outcome always retains ownership through Play Mode exit, restores the previous start-scene setting by GUID, captures the post-exit scene, and publishes the response with a strict atomic temp-file move. A failed restoration retains both retry evidence and the lease and publishes no response until restoration succeeds. Persisted state is cleared from the producer's successful publication result, not from a racy post-publication existence check; this path never uses the synchronous direct-write fallback. Python's file-IPC polling deadline is the requested ClientSim operation timeout plus a fixed 30-second exit-cleanup grace and 5-second dispatch margin; the operation timeout sent to Unity is not extended.

`data.executed` is a required boolean on every Bridge `validate_runtime(profile="clientsim")` response. A pre-Play rejection has `executed=false` and does not require a side-effect report; `executed=true` requires a structurally complete `data.side_effect_report`. The report distinguishes `before`, `runtime`, and `after` root/hierarchy/component snapshots. `added_*` / `removed_*` describe runtime-vs-before changes; `residual_added_*` / `residual_removed_*` describe after-vs-before changes that remain after cleanup. Differences preserve duplicate multiplicity, so an added same-name sibling or repeated same-type component remains observable. Dirty-asset candidates are collected only from already loaded persistent dirty objects; observation never loads every project asset. `asset_change_candidates` is the symmetric before/after multiset difference, so both newly dirty and newly clean or unloaded assets remain observable.

Python cleanup classification requires exact booleans for `diff_complete` and dirty flags, non-boolean integers for dirty counts, and string arrays for `diff_warnings`, every `residual_*` field, and `asset_change_candidates`. A missing or malformed executed report produces `CLIENTSIM_SIDE_EFFECT_DIFF_UNAVAILABLE` instead of being treated as clean. `diff_complete=false` also produces that diagnostic. When only the runtime snapshot is unavailable, valid before/after residual, dirty, and asset differences still additionally produce `CLIENTSIM_SIDE_EFFECT_DIFF_DETECTED`; when the before or after snapshot is unavailable, those derived cleanup differences are not trusted. Expected runtime-only ClientSim objects remain evidence without a cleanup warning. A successful smoke check returns `RUN_CLIENTSIM_OK`; package/API absence or disabled settings returns a pre-Play skip response.

## Open Prefab transaction response (#156)

Exactly one `kind="prefab"`, `mode="open"` resource with `confirm=true` returns `data.transaction`. `status` is `not_started`, `committed`, `rolled_back`, or `rollback_failed`. `report_written=true` means the JSON at `out_report` equals the complete terminal response. Across public patch responses, `data.target`, every `data.targets[]` item, every `data.resources[].path`, and nested `steps[*].result.data.target` values are project-relative POSIX paths. Open Prefab terminal `out_report`, successful `rollback_result.data.target`, and the same path-bearing fields inside `original_result` follow that boundary; contained resolved paths remain internal to reservation, persistence, restoration, and Bridge execution. A successful rollback restores the exact disk preimage and then synchronizes the Editor AssetDatabase; `rollback_result.data.auto_refresh` is exactly `"true"`. Refresh failure or an unavailable Bridge makes the terminal `status="rollback_failed"`, `severity="critical"`, `code="PATCH_ROLLBACK_FAILED"`, with `rollback_result.data.boundary="rollback_sync"` and `state_unknown=true`. If raw applied step data cannot be projected during rollback, the exact preimage restoration and refresh remain authoritative, `original_result` becomes the sanitized `data.boundary="projection"` failure, and the `rolled_back` terminal is still persisted instead of leaking a projection exception. `rollback_result` retains restoration evidence, and `diagnostics_baseline` reports exact `new` / `known` / `resolved` stable-key partitions. `created_results` contains only selected `instantiate_prefab` result handles: post-save `symbol_path`, root GameObject/Transform file IDs, source asset path/GUID, and actual component/property override pairs. The host accepts a successful Bridge response only when `applied == op_count` and those selected handles exactly match unique, non-empty `created_results` identities; it deliberately does not enumerate every created descendant or component.

## エラーコード規約

### MCP protocol-boundary JSON-RPC errors

次の表は、Prefab Sentinel product middleware / HTTP gate が自ら返す JSON-RPC error を網羅する。これらは MCP wire contract 自体の rejection であり、`tools/call` の `CallToolResult` や Prefab Sentinel の `success / severity / code / ...` domain envelope には包まない。HTTP status は Streamable HTTP の場合だけ適用し、stdio には HTTP status がない。JSON-RPC / MCP 全体の error inventory ではなく、現在の product-owned emission surface である。

| JSON-RPC code | 名前 | 条件 | HTTP status |
|---:|---|---|---:|
| `-32700` | `Parse error` | request body が JSON として parse できない | `400` |
| `-32600` | `Invalid Request` | JSON-RPC request object が不正。HTTP では request ID を持たない notification（`notifications/cancelled` を含む）も現行 gate がこの code で拒否する | `400` |
| `-32602` | `Invalid params` | HTTP の true legacy `initialize` body のように、modern namespaced request `_meta` が欠落・不正 | `400` |
| `-32022` | `UnsupportedProtocolVersion` | stdio の well-formed unsupported `initialize` / unsupported era、または legacy version を運ぶ modern-envelope HTTP request。`data` に requested / supported version evidence を返す | stdio: なし、HTTP: `400` |
| `-32020` | `HeaderMismatch` | HTTP の必須 header が欠落・malformed、または header value が対応する request body value と一致しない | `400` |
| `-32601` | `Method not found` | 上記 validation を通過した後、対応する era の Tools-only request method 以外。valid modern HTTP `initialize` も removed method としてこの code を返す | `404` |

stdio product-owned `-32022` は `data.supported=["2026-07-28", "2025-11-25", "2025-06-18"]`（modern-first）と `data.requested` を返す。HTTP の modern-envelope rejection は `data.supported=["2026-07-28"]` を返す。HTTP の request classification は、true legacy body の malformed metadata `-32602` → modern-envelope unsupported version `-32022` → header/body mismatch `-32020` → valid modern removed method（`initialize` を含む）`-32601` の優先順位である。どちらの HTTP rejection も legacy HTTP session を作らない。

mixed-era request の rejection は SDK-owned である。test は JSON-RPC code と machine-readable structure / semantics を pin するが、SDK の explanatory prose は `mcp>=2,<3` の依存範囲で product contract にしない。

現行 101 tool は client capability を必要としないため、product が `-32021` (`MissingRequiredClientCapability`) を返す経路はない。pinned conformance alpha.11 の `server-stateless` は `test_missing_capability` という structural diagnostic tool を要求するが、その tool を公開 surface または hidden dispatch に追加しない。この非適用 probe を含む scenario は厳格 CI gate から除外し、upstream が non-applicable structural probe の skip をサポートした時点で再検討する。これは full conformance の主張ではなく、process-wide state の既知逸脱は [ARCHITECTURE.md](../ARCHITECTURE.md#mcpserver--protocol-boundary) に残る。

この表の numeric code を下記の domain-code inventory に文字列として追加しない。tool が正常に実行されて domain envelope の `success=false` を返す場合は MCP execution error ではなく、`CallToolResult.isError=false` の structured result として保持する。SDK の tool 引数検証・handler 実行失敗だけが `CallToolResult.isError=true` になる。result 境界の正本は [tool-conventions.md](./tool-conventions.md#mcp-protocol--result-境界)。

### Prefab Sentinel domain-code inventory

| コード | 説明 |
|--------|------|
| `SER001` | Serialized path not found — `propertyPath` の構文不正（空文字列、空セグメント `a..b`、閉じ括弧欠落 `a.Array.data[0` 等）または対象のプロパティが存在しない。 |
| `SER002` | Type mismatch — `propertyPath` の添字が不正（負のインデックス `Array.data[-1]`、非整数インデックス `Array.data[abc]`、`Array.size[0]` のような禁止された組み合わせ）または型の不一致。Python 的な負インデックス意味論は採用しない。 |
| `PVR001` | Stale override — empty propertyPath (single category) or mixed categories |
| `PVR002` | Stale override — duplicate propertyPath (later entries shadow earlier) |
| `PVR003` | Stale override — array size/index mismatch |
| `REF001` | Missing asset guid / unreadable target metadata — `patch_apply` / `revert_overrides` / `validate_refs` は、参照されたアセットの GUID が 1 件でもプロジェクト内に見つからない場合、**fail-fast** で全体を中断し `success=False`, `severity="error"`, `code="REF001"` を返す。`where_used` / `find_referencing_assets` では target `.meta` の status failure または metadata read/decode failure も `REF001` の typed failure として返す。部分適用や書き込みは一切行わない。 |
| `REF002` | Missing local fileID |
| `REF404` | Reference lookup path unavailable — `where_used` / `find_referencing_assets` の `scope` path status failure または target asset path status failure。`severity="error"`、message は `scope path status` または `target asset path status` を含み、raw filesystem exception は公開境界へ出さない。 |
| `MATERIAL_VALIDATION_OK` | `validate_materials` が supported Unity text targets を read-only scan し、generic risk / loaded rule finding / schema-read error が無かった場合。`severity="info"`。 |
| `MATERIAL_VALIDATION_FINDINGS` | `validate_materials` が generic material shader risk、renderer slot risk、または loaded declarative rule finding を検出した場合。`success=false`, `severity="warning"`。 |
| `MATERIAL_VALIDATION_SCOPE_REQUIRED` | `validate_materials` MCP wrapper が明示 scope も activate 済み session scope も受け取れなかった場合。project root fallback は行わず、orchestrator を呼ばない。`severity="error"`。 |
| `MATERIAL_VALIDATION_SCOPE_NOT_FOUND` | `validate_materials` の resolved scope が存在しない、project root 外、または usable scope として扱えない場合。`severity="error"`。 |
| `MATERIAL_RULES_INVALID` | project root の `config/material_validation_rules.json` が unreadable / invalid JSON / schema 不一致だった場合。`validate_materials` は validation scan を開始せず `severity="error"` で停止する。 |
| `MATERIAL_VALIDATION_READ_ERROR` | `validate_materials` が in-scope supported Unity text asset の read/decode failure を検出し、validation の信頼性を保てない場合。`severity="error"`。 |
| `INSPECTION_TIMEOUT` | `inspect_hierarchy` / `inspect_wiring` / `validate_all_wiring` / `validate_materials` が timeout 後に reliable partial data を返す場合。`data.progress_summary`, `data.partial_counts`, `data.current_or_slowest_step`, `data.suggested_next_action` を含み、incomplete scan では `diagnostics_baseline` を載せない。 |
| `INSPECT_MATERIAL_ASSET_INVALID_MODE` | `inspect_material_asset` が `full` / `summary` 以外の mode を受け取った場合。`data.accepted_modes` に受理値を載せ、material parse は開始しない。 |
| `RUN001` | Udon runtime exception |
| `RUN002` | ClientSim startup failure |
| `RUN_CLIENTSIM_OK` / `RUN_CLIENTSIM_SKIPPED` / `RUN_CLIENTSIM_DISABLED` | ClientSim reached public network-ready state, or was skipped before Play because the package/API is unavailable or ClientSim is disabled. |
| `CLIENTSIM_CONFIRM_REQUIRED` | `profile="clientsim"`, `confirm=true`, or non-empty `change_reason` is missing; Play Mode is not entered. |
| `CLIENTSIM_ALREADY_RUNNING` | Another persisted ClientSim operation or restoration lease owns cleanup. |
| `CLIENTSIM_EDITOR_NOT_READY` | Unity is already playing or changing Play Mode. |
| `CLIENTSIM_ACTIVE_SCENE_REQUIRED` | The requested scene is not the sole loaded active scene. |
| `CLIENTSIM_DIRTY_SCENE` | A loaded Scene is dirty while `allow_dirty_scenes_before_compile=false`. |
| `CLIENTSIM_START_SCENE_UNRESTORABLE` / `CLIENTSIM_RESTORE_FAILED` | The previous Play Mode start scene cannot be leased or restored safely. |
| `CLIENTSIM_PREFLIGHT_TIMEOUT` | Snapshot/settings preflight consumed the operation deadline; no restoration lease is acquired and Play Mode is not entered. |
| `CLIENTSIM_ENTER_PLAY_MODE_FAILED` / `CLIENTSIM_ENTER_PLAY_MODE_TIMEOUT` | Unity rejected Play Mode entry or did not enter before the operation deadline. |
| `CLIENTSIM_READY_CHECK_FAILED` / `CLIENTSIM_READY_TIMEOUT` | Public ClientSim readiness inspection failed or did not become ready before the operation deadline. |
| `CLIENTSIM_EXIT_PLAY_MODE_FAILED` / `CLIENTSIM_EXIT_PLAY_MODE_TIMEOUT` | Play Mode exit failed or exceeded the cleanup deadline; restoration ownership remains persisted until stable Edit Mode. |
| `CLIENTSIM_UNEXPECTED_PLAY_MODE_EXIT` / `CLIENTSIM_STATE_INVALID` / `CLIENTSIM_STATE_CORRUPT` | The persisted lifecycle and actual Editor state diverged; the Bridge exits/restores before responding. |
| `CLIENTSIM_SIDE_EFFECT_DIFF_UNAVAILABLE` | Cleanup evidence is missing, malformed, or incomplete. This warning never classifies an executed run as clean. |
| `CLIENTSIM_SIDE_EFFECT_DIFF_DETECTED` | Trusted before/after evidence contains residual hierarchy/component changes, dirty-state changes, or symmetric dirty-asset candidate changes. It may accompany `CLIENTSIM_SIDE_EFFECT_DIFF_UNAVAILABLE` when only the runtime snapshot is missing. |
| `CHANGE_REASON_REQUIRED` | `confirm=True` で呼ばれた書き込み系ツールが `change_reason` を欠いた場合。`editor_run_script` は `confirm=False` や空文字の `change_reason` も同コードで拒否する（監査トレイル強制）。 |
| `OUT_REPORT_REQUIRED` / `OUT_REPORT_OUTSIDE_PROJECT` / `OUT_REPORT_WRITE_FAILED` | `set_properties` confirmed report preflight uses `OUT_REPORT_REQUIRED`, `OUT_REPORT_OUTSIDE_PROJECT`, and `OUT_REPORT_WRITE_FAILED`: missing report は required、project 外は outside-project、missing/non-directory parent、existing/unwritable reservation は write-failed として、いずれも writer dispatch 前に拒否する。Exactly-one open Prefab transaction も同じ path partition を共有し、さらに transaction 固有の one-shot reservation child の launch/timeout/exit/status/create/release failure を mutation 前に `OUT_REPORT_WRITE_FAILED` で拒否する。descriptor は child のみが所有して終了時に解放され、parent は close/retry しない。post-exit cleanup failure で残った空予約は外部除去まで同一パスを占有する。transaction の terminal persistence failure は rollback 後も同 code を `data.transaction.report_result` に保持し、`report_written=false` にする。 |
| `PATCH_APPLY_RESULT` | SerializedValue apply boundary の stable terminal code。`set_property` / `set_properties` の asset read・symbol-tree parse preflight 例外は `data.boundary="preflight"` を返し、resolved host path や raw exception を公開せず writer dispatch 前に停止する。dedicated writer exception は `data.boundary="apply"` と confirm 状態に応じた `data.state_unknown` を返す。両 writer の dedicated writer が例外を送出せず `success=false` を返し、`data.read_only=false` の場合も、公開応答は `data.state_unknown=true` を追加する。caller は後続 write の前に serialized state を再検査する。Open Prefab transaction では通常 terminal として使用し、preflight failure は `status="not_started"`、post-mutation failure + restoration success は exact message `patch.apply validation failed; transaction rolled back.` と `status="rolled_back"` を返す。 |
| `PATCH_WRITER_BOUNDARY_FAILED` | `set_material_property` / `copy_asset` / `rename_asset` / `delete_asset` / `delete_assets` / `revert_overrides` の public writer boundary が unexpected exception または response projection failure を構造化した terminal。orchestrator / scope acquisition と unconfirmed dispatch は `mutation_state="not_started"`, `cache_state="unchanged"`、confirmed dispatch は `mutation_state="unknown"`, `cache_state="invalidated"` を返す。cache invalidation 自体が失敗した場合だけ `cache_state="unknown"` とし、その例外も private log に限定する。raw exception、host path、stack trace は private Python log にだけ残し、confirmed unknown は `PATCH_WRITER_REINSPECTION_REQUIRED` diagnostic を伴う。 |
| `PATCH_WRITER_REINSPECTION_REQUIRED` | confirmed writer の状態が不明、または mutation 成功後の AssetDatabase refresh が失敗したため、次の write 前に read/inspection が必要な warning diagnostic。refresh failure は元の `success=true` と operation code/message を維持し、top-level severity を `warning`、`mutation_state="applied"`, `cache_state="invalidated"`, `auto_refresh="false"` とする。cache invalidation failure時は success/applied を維持したまま `cache_state="unknown"` とする。 |
| `PATCH_ROLLBACK_FAILED` | Post-mutation failure 後の exact preimage restoration も失敗した critical terminal。exact message `patch.apply validation failed and automatic rollback failed.` と元 failure / rollback result / report result を保持する。 |
| `ASSET_COPY_DRY_RUN` / `ASSET_COPY_APPLIED` | offline `copy_asset` の preview / copy 成功。source が `.controller` の場合は document 順序に依存せず、一意な non-stripped class 91 / `AnimatorController` の root `m_Name` だけを改名する。既存 `data.m_name_before` / `m_name_after` に加え、両応答に `data.m_name_target={class_id, file_id, type_name, property_path}` を返す。class ID / fileID は文字列、`type_name="AnimatorController"`、`property_path="m_Name"`。変更する名前は UTF-8 の JSON double-quoted string（YAML scalar として有効）で書き、`#` などを名前の一部として保持する。`m_name_before` は source scalar の原表現（quote / escape を含む）、`m_name_after` は要求された名前。source scalar が要求名の plain 表現または writer の canonical quoted 表現に一致する場合は元 bytes を保持し `m_name_unchanged=true` とする。他の有効な quoted 表現も受理し、出力時に canonical 表現へ正規化できるが、一般的な YAML decode や semantic no-op 判定を行う契約ではない。選択外 document の名前・遷移・内部参照は変更しない（Issue #228: 内部 State の名前の誤選択と raw scalar 書込みを防ぐため）。他の asset 型のコピー契約は変更しない。 |
| `ASSET_COPY_MAIN_OBJECT_INVALID` | `.controller` の main object または root 名を安全に特定できない場合、dry-run / confirmed copy を staging 前に `severity="error"` で拒否する。`data.field="source_path"`、`matching_document_count` は non-stripped class 91 の件数。`reason` は件数が 1 以外なら `main_object_not_unique`、型 root 不一致または fileID 重複なら `main_object_identity_invalid`、root `m_Name` の欠落・重複・空値・複数行など信頼できる単一行 scalar がない場合は `root_name_invalid`。別 field の quoted / flow 値内にある `m_Name` 文字列は root property として選ばない。別 field の有効な multiline scalar は元 bytes のまま保持し、書換対象の root `m_Name` だけを単一行に限定する（文字列内容を field と誤認しないため）。最初の document / 内部 State 名への fallback は行わない。 |
| `ASSET_RENAME_DRY_RUN` / `ASSET_RENAME_APPLIED` | offline `rename_asset` の preview / rename 成功。`.controller` は上記 copy と同じ main-object 選択、`data.m_name_target`、opaque before / requested after、scalar encoding / no-op 契約を使う（Issue #239: rename にも残っていた first-State-name 選択を解消するため）。preview は source / `.meta` を変更しない。confirmed rename は元の `.meta` を内容・GUID ごと移動し、GUID を新規生成しない。名前の no-op でもファイルと `.meta` の移動は実行する。既存の移動失敗 rollback と、他の asset 型の rename 契約は維持する。 |
| `ASSET_RENAME_MAIN_OBJECT_INVALID` | `.controller` の rename target を安全に特定できず、staging / asset move / metadata move 前に `severity="error"` で停止した場合。`data.field="asset_path"`。`reason` と `matching_document_count` は `ASSET_COPY_MAIN_OBJECT_INVALID` と同じ選択規則・値であり、source / `.meta` は変更しない。 |
| `ASSET_RENAME_INVALID_PATH` | offline `rename_asset` が project root 外へ解決される source asset path を受け取った場合。`data.input_path` / `data.normalized_candidate_path` / `data.resolution_root` / `data.reason` と `rename_source_resolution` diagnostic を返し、raw `ValueError` は公開境界へ出さない。 |
| `ASSET_RENAME_INVALID_NAME` | offline `rename_asset` が `new_name` に bare filename 以外（path separator、parent segment、absolute / drive-rooted name、project root 外 destination）を受け取った場合。cross-directory move は `rename_asset` の責務外とし、`data.input_name` と `data.reason` を返して source asset を変更しない。 |
| `ASSET_OP_WRITE_FAILED` | offline `copy_asset` / `rename_asset` の confirmed write / rename / rollback が失敗した場合。cleanup unlink / rollback failure は元の write failure を置き換えず `diagnostics[]` に追加する。 |
| `ASSET_DELETE_DRY_RUN` | `delete_asset` / `delete_assets` の dry-run 計画成功。`data.targets[]` に asset / `.meta` / reference impact、`data.pre_delete_broken_references` に削除前 baseline、`data.related_candidates[]` に deterministic UdonSharp generated program 候補、`data.decision_required[]` に ambiguous UdonSharp generated program 候補の判断材料を返す。 |
| `ASSET_DELETE_APPLIED` | Unity Editor Bridge の `delete_assets` action が AssetDatabase delete を完了した成功応答。`data.broken_reference_delta` を必ず返し、delta 増加時も AssetDatabase 成功なら failure に変換しない。 |
| `ASSET_DELETE_NOT_FOUND` | 削除対象の `Assets/...` asset が存在しないため dry-run / apply を拒否した場合。 |
| `ASSET_DELETE_META_UNREADABLE` | 削除対象 asset の `.meta` が読み取れない、または UTF-8 として decode できないため dry-run / apply を拒否した場合。 |
| `ASSET_DELETE_EXTERNAL_PACKAGE_UNSUPPORTED` | `Packages/`、`Library/`、project 外、または package-cache 相当の path が削除対象に指定された場合。 |
| `ASSET_DELETE_UNSUPPORTED` | Unity Editor Bridge / AssetDatabase delete action が未設定・未対応・利用不能なため、confirmed apply を拒否した場合。raw filesystem delete への fallback は行わない。 |
| `ASSET_DELETE_FAILED` | AssetDatabase delete action が `failed_paths` を返した場合。 |
| `ASSET_DELETE_DECISION_REQUIRED` | UdonSharp generated program asset 候補が複数または曖昧で、自動削除候補として扱えない場合の diagnostic code。`delete_asset` / `delete_assets` dry-run では `data.decision_required[]` に `detail` と候補 `asset_paths[]` を返す。 |
| `INVALID_CONFIRM_VALUE` | `editor_create_generated_asset` / `editor_move_asset` の `confirm` が JSON bool でない場合。Bridge 呼び出しと report write は行わない。 |
| `PROJECT_ROOT_INVALID` / `OUT_REPORT_REQUIRED` / `OUT_REPORT_INVALID` / `OUT_REPORT_PARENT_NOT_FOUND` / `OUT_REPORT_EXISTS` / `CHANGE_REASON_REQUIRED` / `CHANGE_REASON_TOO_LONG` | #116 editor asset tools の confirmed call audit/report validation failure。検証順は project_root → out_report → change_reason。 |
| `UNSUPPORTED_GENERATED_ASSET_TYPE` | generated asset creation が `render_texture` 以外の `asset_type` を受け取った場合。`data.unity_type` は返さない。 |
| `GENERATED_ASSET_INVALID_PATH` / `GENERATED_ASSET_PATH_IS_META_FILE` | create destination が `Assets/...` project path、case-sensitive `.renderTexture` extension、非空 stem、または non-`.meta` path の lexical rule に反する場合。 |
| `GENERATED_ASSET_INVALID_PARAMETER` | RenderTexture `parameters` が object でない、required `width` / `height` を欠く、unknown key を持つ、型・範囲・allowlist に反する場合。 |
| `GENERATED_ASSET_DESTINATION_EXISTS` / `GENERATED_ASSET_DESTINATION_META_EXISTS` / `GENERATED_ASSET_PARENT_NOT_FOUND` / `GENERATED_ASSET_PARENT_NOT_FOLDER` | Bridge create dry-run / confirm が AssetDatabase destination または parent folder state で拒否した場合。 |
| `GENERATED_ASSET_CREATE_FAILED` / `GENERATED_ASSET_SAVE_OR_REFRESH_FAILED` / `GENERATED_ASSET_POSTCHECK_FAILED` / `GENERATED_ASSET_DIRTY_POSTCHECK_FAILED` | Confirm create 開始後の constructor/CreateAsset、SaveAssets/Refresh、または final AssetDatabase postcheck failure。残存または不明状態は `PARTIAL_SIDE_EFFECT_REQUIRES_REVIEW` warning diagnostic を伴う。 |
| `ASSET_SOURCE_INVALID_PATH` / `ASSET_DESTINATION_INVALID_PATH` / `ASSET_SOURCE_IS_META_FILE` / `ASSET_DESTINATION_IS_META_FILE` / `ASSET_EXTENSION_MISMATCH` / `ASSET_MOVE_SAME_PATH` / `ASSET_MOVE_CASE_ONLY_RENAME_UNSUPPORTED` | move source/destination の lexical rule failure。Python 境界で拒否され Bridge は呼ばない。 |
| `ASSET_SOURCE_NOT_FOUND` / `ASSET_SOURCE_LOAD_FAILED` / `ASSET_SOURCE_IS_FOLDER` / `ASSET_DESTINATION_EXISTS` / `ASSET_DESTINATION_META_EXISTS` / `ASSET_DESTINATION_PARENT_NOT_FOUND` / `ASSET_DESTINATION_PARENT_NOT_FOLDER` | Bridge move dry-run / confirm が AssetDatabase source/destination/parent state で拒否した場合。source `.meta` だけが存在する場合は `ASSET_SOURCE_NOT_FOUND` と `data.meta_exists` で evidence を返す。 |
| `ASSET_MOVE_FAILED` / `ASSET_MOVE_SAVE_OR_REFRESH_FAILED` / `ASSET_MOVE_POSTCHECK_FAILED` / `ASSET_MOVE_DIRTY_POSTCHECK_FAILED` | Confirm move 開始後の AssetDatabase.MoveAsset error string、SaveAssets/Refresh exception、GUID/name/load/dirty final postcheck failure。残存または不明状態は `PARTIAL_SIDE_EFFECT_REQUIRES_REVIEW` warning diagnostic を伴う。 |
| `UNITY_BRIDGE_INVALID_RESPONSE` | #116 Python projector が Bridge root/envelope/diagnostics/success data の malformed response を検出した場合。`data.state_unknown=true` と `PARTIAL_SIDE_EFFECT_REQUIRES_REVIEW` diagnostic を返す。 |
| `OUT_REPORT_WRITE_FAILED` | `set_properties` または #116 confirmed operation が report destination の preflight/reservation に失敗した場合、または operation 完了後に exclusive final-response write を確定できない場合。preflight failure は mutation 前に停止する。`set_properties` の dedicated writer が例外を送出した場合は raw exception を公開せず、`PATCH_APPLY_RESULT`、`data.boundary="apply"`、`data.state_unknown=true` の terminal failure を予約済み report に確定する。その failure の確定自体に失敗した場合は `OUT_REPORT_WRITE_FAILED` の `data.operation_error` に保持する。その他の post-operation failure も元 operation result/error を `data.operation_result` または `data.operation_error` に保持し、raw filesystem path/exception を公開しない。 |
| `SER003` | `set_properties` が dry-run 段階でチェーン上に解決できない property path を検出した場合（issue #109）。`severity="error"`、`data.suggestions` に近似候補（最大 5 件）、`diagnostics[].detail` に `property_not_found` を載せる。issue #41 で `set_properties` は `symbol_path` を直接 component に解決するため、component 不在は `SYMBOL_NOT_FOUND` で表面化する（`SER003` の `component_not_found` 経路は廃止）。 |
| `SER_APPLY_REJECTED` | dedicated serialized-value 経路の `set` / `insert_array_element` / `remove_array_element` が `SerializedObject.ApplyModifiedPropertiesWithoutUndo()` 直前のバリデーション（`TryApplyOp`）で拒否された場合（issue #298）。`severity="error"`。`diagnostics` 配列には各失敗 op の `BridgeDiagnostic` に加え、`property_path` / `component_type` / `attempted_value` を `evidence` に埋めた summary 行が末尾に追加される。`AudioSource.m_Priority` 等の既知トラップを応答だけで診断できることが目的。issue #37 以降、serialized-value op にある `file_id` がアセット内のどの component にも解決しない場合も、この経路で `apply_error` diagnostic（未解決 fileID を `evidence` に明示、`location` は `ops[N].file_id`）として fail-fast で表面化する。Editor 例外路は `UNITY_BRIDGE_APPLY_EXCEPTION` のまま（未捕捉例外と rejection を別コードで区別）。 |
| `BRIDGE_LEGACY_SCHEMA_REJECTED` | `unity_patch_bridge` がレガシー形状（トップレベル `target` キー）のリクエストを受け取った場合。v2 スキーマ（`{plan_version, resources, ops}`）のみを受け入れる。互換レイヤは存在しない。 |
| `EDITOR_CTRL_RUN_SCRIPT_OK` / `..._COMPILE` / `..._RUNTIME` / `..._BAD_ID` | `editor_run_script` の成功 / コンパイル失敗 / 実行例外 / 不正 temp id。 |
| `EDITOR_CTRL_RUN_SCRIPT_RECOVERY` | 同一スニペットが 2 回連続で `..._COMPILE` 拒否された場合に発火する `severity="warning"` 応答（issue #116）。Bridge は temp ディレクトリを掃除し、`AssetDatabase.Refresh` で再コンパイルを要求した上で、診断ペイロード（`diagnostic_compiling` / `diagnostic_temp_files` / `diagnostic_last_domain_reload`）を返す。次回呼び出しはクリーンな状態で再試行できる。 |
| `EDITOR_CTRL_RUN_SCRIPT_COMPLETION_INVALID` | `editor_run_script_poll` が completion artifact を読み取ったものの、JSON root が object でない、decoded top-level `data` が欠落・重複している、`data` value が object でない、または JSON syntax / trailing content が不正なため実行結果を解釈できない場合（issue #244）。この narrow structural guard を Unity DTO materialization より先に行い、complete response schema 全体の検証とはしない。`success=false`, `severity="error"`, `data.status="failed"`, `data.executed=false`, `data.state_unknown=true`, `data.read_only=false` と caller の validated `data.request_id` を返す。raw artifact は `message` / `stdout` / `diagnostics` へ公開しない。 |
| `EDITOR_CTRL_RUN_SCRIPT_COMPLETION_READ_FAILED` | `editor_run_script_poll` が存在確認済みの completion artifact を読み取れない場合（issue #257）。`success=false`, `severity="error"`, `message="RunScript completion could not be read; execution outcome is unknown."`, `data.status="failed"`, `data.executed=false`, `data.state_unknown=true`, `data.read_only=false` と caller の validated `data.request_id` を返す。artifact body、path、例外詳細は公開 `message` / `data` / `diagnostics` へ含めず、Bridge の private Unity log にだけ残す。このコードは存在確認後の読取失敗だけを分類し、既存の入力検証・completion待機の挙動を変更しない。 |
| `EDITOR_CTRL_INSPECTOR_SCENE_DIRTY` / `INSPECTOR_SCENE_DIRTY` | loaded unique Scene の unsaved state を raw Bridge / sanitized public terminal として分離する。target resolution、surface build、open/close は行わない。詳細契約は「Inspector profile responses / Scene inspection lifecycle」を正本とする。 |
| `EDITOR_CTRL_INSPECTOR_SCENE_AMBIGUOUS` / `INSPECTOR_SCENE_AMBIGUOUS` | 同じ path の loaded Scene が複数あるため instance 選択を拒否する raw/public terminal。詳細契約は同節を正本とする。 |
| `EDITOR_CTRL_INSPECTOR_SCENE_RESTORE_FAILED` / `INSPECTOR_SCENE_RESTORE_FAILED` | owned cleanup、active restoration、または complete after-state postcondition の失敗を示す raw/public terminal。詳細契約は同節を正本とする。 |
| `EDITOR_CTRL_ADD_COMPONENT_REUSED` / `..._RELINKED` | `editor_add_component` が UdonSharp 派生型に対して呼ばれ、既存ペアが見つかった（reuse）または孤立 proxy に新規 UdonBehaviour を再リンクした（relinked）場合の `severity="info"` 成功応答（issue #103）。 |
| `EDITOR_CTRL_UDON_ADD_COMPONENT_FAILED` | `editor_add_component` / `editor_add_udonsharp_component` が UdonSharp の setup-aware add または孤立 proxy の再リンクを完了できなかった場合の `severity="error"`。`editor_add_component` は UdonSharp 派生型を通常の `Undo.AddComponent` へフォールバックせず、ProgramAsset readiness、`UdonSharpUndo.AddComponent`、`RunBehaviourSetupWithUndo`、再取得した backing UdonBehaviour の postcondition のいずれかが失敗した時点で停止する。 |
| `EDITOR_CTRL_CAMERA_CONFLICT` | `editor_set_camera` が `position` と `pivot` を同時指定、または `look_at` を `position` 抜きで指定した場合（issue #112）。 |
| `EDITOR_CTRL_CAMERA_PROJECTION_TRANSITION` | `editor_set_camera(position=...)` が SceneView projection transition 中と判定された場合（SceneView と backing Camera の orthographic state mismatch、または perspective `fieldOfView` が non-finite / non-positive）。`severity="error"`。Bridge は private SceneView internals を reflection せず、自動 wait / retry もせず、caller に settle 後の再試行を要求する。 |
| `EDITOR_CTRL_INVALID_CLASSIFICATION_FILTER` | `editor_console` の `classification_filter` が `all` / `non_fatal` / `fatal` 以外の場合（issue #117）。 |
| `EDITOR_CTRL_INVALID_PHASE_FILTER` | `editor_console` の `phase_filter` が `all` / `edit` / `play` / `build` 以外の場合（issue #239）。`severity="error"`、メッセージで受理可能な値を列挙。Bridge 境界で buffer に触れる前に拒否される。 |
| `EDITOR_CTRL_EDITOR_STATE_OK` | `get_editor_state` action の成功時応答コード（issue #239 / issue #40 / issue #155）。`get_project_status` MCP ツールと offline symbol-reference ツールから内部的に発火し、`data.editor_state` に play/build/compile bool フラグ、`has_unsaved_changes`、active scene / Prefab Stage、`active_stage_kind`, `state_source="live_editor"`, dirty scene/prefab/material/asset identity arrays、project root identity、bridge/plugin/session/instance identity を返す。live Bridge response には `operator_context` が同梱され、caller は reached Unity project root と expected ProjectSession root を比較できる。通常は `severity="info"`、ただし scene / stage / dirty-category enumeration が一部失敗して `EDITOR_STATE_ENUMERATION_LIMITED` diagnostics を含む場合は successful warning response になり、`get_project_status` もその diagnostic と warning severity を引き継ぐ。 |
| `EDITOR_CTRL_INSPECTOR_SURFACE_DIRTY` | `.asset` の canonical ScriptableObject が未保存変更を持つため、last-saved raw surface として読み取れない場合。`severity="error"`。Bridge は live 値を返さず、MCP は `INSPECTOR_SURFACE_UNAVAILABLE` と本コードの diagnostic を返す。 |
| `EDITOR_BRIDGE_WATCH_DIR_MISSING` / `EDITOR_BRIDGE_WATCH_DIR_NOT_FOUND` / `EDITOR_BRIDGE_TIMEOUT` | Bridge transport setup / response timeout failure。`data.blocker_class` は `watch_dir` または `bridge_connection`、`data.suggested_next_action` は watch-dir setup または Unity Bridge watcher 確認を案内する。status surface は code/class/action だけへ投影し、configured/reported watch pathやraw probe exceptionを公開しない。`EDITOR_BRIDGE_TIMEOUT_INVALID` は caller input error なので blocker metadata を付けない。 |
| `EDITOR_BRIDGE_WATCH_DIR_MISMATCH` | fresh private watch-identity status が host と Unity Editor Bridge の directory identity 不一致を証明した。`get_project_status` は `SESSION_STATUS` warning、`connection_state="misconfigured"`、`watch_dir` blocker へ投影し、live request を送らない。marker / path / timestamp / private status / exception は公開しない。 |
| `EDITOR_BRIDGE_STATUS_TRANSIENT` | 最後の fresh private status 観測から 5000 ms 以内（境界を含む）に status file が欠落した。Unity reload 中の一時状態として `SESSION_STATUS` warning、`connection_state="unavailable"`、blocker 0件へ投影し、live request と private ERROR の両方を抑止する。 |
| `EDITOR_BRIDGE_STATUS_UNAVAILABLE` | 初回または freshness 境界超過後の欠落、stale、不正、読取不能な private status。固定 `bridge_connection` blocker と watcher 確認 action へ投影し、live request を送らない。同じ outage の private ERROR は1回だけ記録する。 |
| `EDITOR_BRIDGE_RESPONSE_READ` | editor-control file transport が response file を regular UTF-8 JSON として 16 MiB 以下で読めない場合。public envelope は固定文へ redaction し、Python の private log には response path と exception traceback を残す。 |
| `RUN_EDITOR_BRIDGE_RESPONSE` | runtime file transport が response file を regular UTF-8 JSON として 16 MiB 以下で読めない場合。public envelope と Python log の両方を固定文へ redaction し、caught filesystem / decode / close exception の message・path・traceback は記録しない。 |
| `EDITOR_BRIDGE_RESPONSE_PUBLICATION_FAILED` | editor-control request 処理後、atomic response write と direct fallback がともに失敗し、tagged marker を Python transport が観測した場合。fixed message と `data.action`, `data.state_unknown=true` だけを返す。 |
| `RUN_EDITOR_BRIDGE_RESPONSE_PUBLICATION_FAILED` | runtime request 処理後に同じ tagged marker を観測した場合。fixed message と `data.action`, `read_only=false`, `executed=false`, `state_unknown=true` を返す。 |
| `EDITOR_BRIDGE_RESPONSE_SCHEMA` | editor-control transport が read 後の payload の共通6フィールド / success-severity rule を満たせず、operation payload の検証前に拒否する場合。 |
| `RUN_PROTOCOL_ERROR` | runtime parser が共通 envelope、または runtime operation-owned payload contract を検証できない場合。 |
| `EDITOR_REFLECT_RESPONSE_SCHEMA` / `ASSET_DELETE_RESPONSE_SCHEMA` / `WRITE_RESPONSE_SCHEMA` | editor-control の共通 envelope を通過した後、reflection / delete / write の operation-owned payload contract を検証できない場合。各 operation は valid `success=false` bridge result を schema error や success に変換しない。 |
| `EDITOR_BRIDGE_ERROR` | watch-loop / pre-dispatch の最終例外境界で request processing が失敗した場合（issue #164）。public `message` は exact `"Editor Bridge request processing failed."` で、exception type / message / stack trace / filesystem path を含めない。元の exception 全体と request filename は Unity Console の private diagnostic にのみ記録する。 |
| `EDITOR_BRIDGE_PROJECT_ROOT_MISMATCH` | Session-aware Editor Bridge call が expected ProjectSession root と `operator_context.project_root` の不一致、または actual root の欠落を検出した場合の typed failure / diagnostic。通常の bridge operations では `success=false`, `severity="error"` で返し、`data.action`, `data.request_id`, `data.expected_project_root`, `data.actual_project_root`（存在する場合）、bridge session/instance identity を含める。`get_project_status` では status envelope 自体を成功扱いに保ち、`project_root_consistent=false` と warning diagnostic で wrong-editor 接続を診断可能にする。 |
| `EDITOR_CTRL_HIERARCHY_PATH_AMBIGUOUS` | Editor Bridge が `hierarchy_path` セグメントを解決した際、同名兄弟に一致し `#N` 一意化トークンを伴わない場合（issue #38, #59）。`severity="error"`。first-sibling を勝手に選ばず解決を停止する。issue #59 以降、全ての hierarchy-bound ハンドラが ambiguity-aware な `TryResolveGameObjectInActiveStage` 経由で解決するため、曖昧パスはこの dedicated envelope で一律に拒否される（`hierarchy_path` を取らない `list_roots` / `find_renderers_by_material` は解決を行わないため対象外）。真の miss は各ハンドラ既存の `*_NOT_FOUND` を返す。 |
| `EDITOR_CTRL_UDON_ADD_NO_PROGRAM_ASSET` | `editor_add_udonsharp_component` が対象型の UdonSharpProgramAsset を見つけられなかった場合（issue #46）。`severity="error"`。メッセージは `editor_create_udon_program_asset` で生成し再コンパイルする次手順を明示する。raw な `NullReferenceException` 文字列を漏らさない。 |
| `EDITOR_CTRL_UDON_ADD_PROGRAM_NOT_COMPILED` | `editor_add_udonsharp_component` の対象型の UdonSharpProgramAsset は存在するが未コンパイルの場合（issue #46）。`severity="error"`。メッセージは `editor_recompile` で再コンパイルする次手順を明示する。 |
| `EDITOR_CTRL_HANDLER_EXCEPTION` | Bridge dispatch の action switch 内で handler が内部捕捉しなかった例外を送出した場合（issue #51）。`severity="error"`。envelope は dispatch された action 名を構造化フィールド `data.action` として運び（メッセージ文字列だけに埋めない）、例外は型名のみに redact する（メッセージにスタックトレースを載せない）。`EDITOR_BRIDGE_ERROR` は真の watch-loop / pre-dispatch 失敗専用に残す。 |
| `EDITOR_CTRL_SERIALIZED_PROPERTY_NO_PATH` / `..._NO_COMPONENT_TYPE` / `..._NO_PROPERTY_PATH` | `editor_serialized_property_*` が必須の `hierarchy_path` / `component_type` / `property_path` を欠いた場合。list は root 未指定を許す。 |
| `EDITOR_CTRL_SERIALIZED_PROPERTY_OBJECT_NOT_FOUND` | live scene / active Prefab Stage で `hierarchy_path` が解決できない場合。direct prefab asset load/edit へフォールバックしない。 |
| `EDITOR_CTRL_SERIALIZED_PROPERTY_COMPONENT_NOT_FOUND` / `EDITOR_CTRL_SERIALIZED_PROPERTY_COMPONENT_AMBIGUOUS` | 対象 GameObject に指定 component が無い、または同型 component が複数あり `component_index` で一意化されていない場合。曖昧時は candidate evidence を返し first component を選ばない。 |
| `EDITOR_CTRL_SERIALIZED_PROPERTY_NOT_FOUND` | raw `propertyPath` または `editor_serialized_property_list(root_property_path=...)` の root が対象 component の SerializedObject に存在しない場合。`data.suggestions` に同じ component から採取した raw property path 候補を返し、list は component root へ fallback しない。 |
| `EDITOR_CTRL_SERIALIZED_PROPERTY_LIST_LIMIT_INVALID` / `EDITOR_CTRL_SERIALIZED_PROPERTY_CURSOR_INVALID` | list の `depth` / `cap` / `cursor` が境界外または不正形式の場合。`cap` は 1..200。 |
| `EDITOR_CTRL_SERIALIZED_PROPERTY_VALUE_REQUIRED` / `..._VALUE_CONFLICT` | write が値 intent を 1 つも持たない、または複数の値 intent を同時指定した場合。false / 0 / empty string は存在マーカーとして有効な値であり欠落扱いしない。 |
| `EDITOR_CTRL_SERIALIZED_PROPERTY_CHANGE_REASON_REQUIRED` | `editor_serialized_property_write(confirm=True)` が非空 `change_reason` を欠いた場合。 |
| `EDITOR_CTRL_SERIALIZED_PROPERTY_ARRAY_SIZE_INVALID` | `array_size` が負、または配列でない property に指定された場合。 |
| `EDITOR_CTRL_SERIALIZED_PROPERTY_TYPE_MISMATCH` / `EDITOR_CTRL_SERIALIZED_PROPERTY_UNSIGNED_RANGE` / `EDITOR_CTRL_SERIALIZED_PROPERTY_UNSUPPORTED_WRITE` | 指定値が property type に合わない、unsigned integer 系の範囲を外れる、または対象 property kind が writer 非対応の場合。 |
| `EDITOR_CTRL_SERIALIZED_PROPERTY_ENUM_VALUE_NOT_FOUND` | enum 名 / index が対象 enum の候補に一致しない場合。 |
| `EDITOR_CTRL_SERIALIZED_PROPERTY_OBJECT_REF_NOT_FOUND` / `EDITOR_CTRL_SERIALIZED_PROPERTY_OBJECT_REF_TYPE_MISMATCH` / `EDITOR_CTRL_SERIALIZED_PROPERTY_OBJECT_REF_AMBIGUOUS` | object reference の asset / hierarchy path が解決できない、解決した Unity object が対象 field 型へ assign できない、または hierarchy path が複数の assignable component に一致する場合。空 asset/hierarchy path は missing reference として拒否し、null reference に丸めない。曖昧時は candidate evidence を返し first component を選ばない。 |
| `EDITOR_CTRL_SERIALIZED_PROPERTY_UDON_SYNC_WARNING` | confirmed changed write は完了したが、UdonSharp proxy-to-backing sync の best-effort step が失敗または未確認だった場合の warning diagnostic。 |
| `IGNORE_GUIDS_FILE_LOADED` | `validate_refs` MCP ツールの `<scope>/config/ignore_guids.txt` auto-load が寄与した場合に `diagnostics` に付与される info diagnostic（issue #237）。`data.path` に解決後の絶対パス、`data.count` に取り込まれた件数を含める。ファイルが存在しない・読み取り不能の場合は発火しない。 |
| `DIAGNOSTICS_BASELINE_INVALID` | project root の `config/diagnostics_baseline.json` が invalid JSON または schema 不一致だった場合。baseline 対応 validation と `update_diagnostics_baseline` は source/orchestrator を呼ぶ前に `success=false`, `severity="error"` で停止し、`data.path` と `data.read_only=true` を返す。 |
| `DIAGNOSTICS_BASELINE_UPDATE_PREVIEW` | `update_diagnostics_baseline(mode="preview")` が source validation の `data.diagnostics_baseline` から次の baseline を計算した場合。`success=true`, `severity="info"`。`data.written=false` で、file system へは書き込まない。 |
| `DIAGNOSTICS_BASELINE_UPDATE_WRITTEN` | `update_diagnostics_baseline(mode="write", confirm=True, change_reason=...)` が project root の `config/diagnostics_baseline.json` を schema v1 JSON として書いた場合。`success=true`, `severity="info"`。 |
| `DIAGNOSTICS_BASELINE_WRITE_FAILED` | `update_diagnostics_baseline(mode="write")` が audit 通過後に baseline file の作成・一時 file 書き込み・置換に失敗した場合。`success=false`, `severity="error"` で停止し、`data.path` と `data.read_only=false` を返す。 |
| `DIAGNOSTICS_BASELINE_PROJECT_ROOT_REQUIRED` | `update_diagnostics_baseline` が activate 済み project root なしで呼ばれた場合。source validation は実行せず、file write もしない。 |
| `DIAGNOSTICS_BASELINE_SOURCE_INVALID` | `update_diagnostics_baseline.source` が `validate_refs` / `inspect_wiring` / `validate_all_wiring` / `validate_structure` / `validate_materials` 以外だった場合。source validation は実行せず、file write もしない。 |
| `DIAGNOSTICS_BASELINE_MODE_INVALID` | `update_diagnostics_baseline.mode` が `preview` / `write` 以外だった場合。source validation は実行せず、file write もしない。 |
| `DIAGNOSTICS_BASELINE_SOURCE_FAILED` | `update_diagnostics_baseline` が実行した source validation が `success=false` を返した場合。`data.source_response` に source envelope を含め、baseline file は書かない。 |
| `DIAGNOSTICS_BASELINE_SOURCE_MISSING_CLASSIFICATION` | `update_diagnostics_baseline` が実行した source validation が `success=true` だが `data.diagnostics_baseline` を持たない、または update 計算に必要な `new[]` / `resolved[]` key record が malformed の場合。baseline file は書かない。 |
| `EDITOR_CTRL_INVALID_ORDER` | `editor_console` の `order` が `newest_first` / `oldest_first` 以外の場合（issue #113）。`severity="error"`、メッセージで受理可能な値を列挙。 |
| `EDITOR_CTRL_INVALID_CURSOR` | `editor_console` の `cursor` が現在の取り込み済み範囲外、もしくは Bridge のフォーマット (`seq:<long>`) に合致しない場合（issue #113）。`severity="error"`、メッセージで原因を明示。 |
| `EDITOR_CTRL_SET_PROP_QUATERNION_NOT_NORMALIZED` | `editor_set_property` で `SerializedPropertyType.Quaternion` に与えた xyzw 4 要素のノルムが `1.0 ± 1e-4` の許容範囲外だった場合（issue #111）。`severity="error"`、メッセージに供給値とノルムを明示。Bridge 側では自動 normalize しない。Component 数が 4 でない（例えば 3 要素の euler を渡した）場合は既存の `EDITOR_CTRL_SET_PROP_TYPE_MISMATCH` で 4 要素必須を案内。 |
| `EDITOR_CTRL_SET_PROP_NULL_INPUT` | `editor_set_property` の Quaternion 入力が `null` だった場合（issue #118）。未設定を空文字として補完せず、caller contract violation として `severity="error"` の typed failure にする。明示的な空文字は従来通り `EDITOR_CTRL_SET_PROP_TYPE_MISMATCH`。 |
| `COMPILE_TIMEOUT_OUT_OF_RANGE` | `editor_run_script` の `compile_timeout_ms` が許容範囲 `[1, 120000]`（ミリ秒、両端含む）の外だった場合（issue #127）。`severity="error"`、Bridge へは送信せず Python の入口で拒否。メッセージに供給値・両端境界値を含める（CLAMP しない）。 |
| `MAX_ENTRIES_OUT_OF_RANGE` / `EDITOR_CTRL_MAX_ENTRIES_OUT_OF_RANGE` | `editor_console` の `max_entries` が許容範囲 `[1, ConsoleLogBuffer.DefaultCapacity]`（既定 1000、両端含む）の外だった場合（issue #131）。`severity="error"`。Python 側 (`MAX_ENTRIES_OUT_OF_RANGE`) は Bridge に送る前に拒否し、C# Bridge 側 (`EDITOR_CTRL_MAX_ENTRIES_OUT_OF_RANGE`) は buffer を見る前に拒否する。上限の根拠は「Bridge は ring buffer に保持している件数以上は返せない」という不変条件で、C# `ConsoleLogBuffer.DefaultCapacity` と Python `bridge_constants.CONSOLE_LOG_BUFFER_MAX_ENTRIES` は `scripts/check_bridge_constants.py` の drift detector で同期する。 |
| `EDITOR_CTRL_RECOMPILE_TIMEOUT` | 同期 recompile ツール `editor_recompile`（issue #54 改名前は `editor_recompile_and_wait`。bridge action 名は不変）が `timeout_sec`（既定 60 秒）以内に `CompilationPipeline.compilationFinished` イベント、もしくは事後の `AssemblyReloadCount` 増加を観測できなかった、純粋な deadline 経過の場合（issue #118 / issue #203 / issue #204）。`severity="error"`。Bridge 内の async runner は `compiledAny=true` の場合のみ SessionState ミラーで domain reload を跨いで継続する（NOOP / FAILED は同期で返るので永続化エントリは作らない）。Editor 側が `RequestScriptCompilation` を拒否した schedule-failure 経路では本コードは返らず、`EDITOR_CTRL_RECOMPILE_SCHEDULE_FAILED` を返す。 |
| `EDITOR_CTRL_RECOMPILE_SCHEDULE_FAILED` | 同期 recompile ツール `editor_recompile` が `CompilationPipeline.RequestScriptCompilation()` を呼び出した時点で Editor が例外を投げた場合（issue #204 / issue #213）。deadline 経過ではなく Editor 側の即時拒否を表すため、`EDITOR_CTRL_RECOMPILE_TIMEOUT` とは別コードに分離している。`severity="error"`。pipeline event 購読は応答返却前に解除され、async runner エントリも撤去される。 |
| `EDITOR_CTRL_RECOMPILE_AND_WAIT_NOOP` | 同期 recompile ツール `editor_recompile` が `CompilationPipeline.compilationFinished` 時点で 1 件も `assemblyCompilationFinished` を観測していない（= 全アセンブリが not-required と扱われた）場合（issue #203 / issue #213）。`severity="info"`、`success=true`。Domain reload は発生しないので SessionState mirror は使わず同期で応答。 |
| `EDITOR_CTRL_RECOMPILE_FAILED` | 同期 recompile ツール `editor_recompile` が `assemblyCompilationFinished` で `CompilerMessageType.Error` のメッセージを 1 件以上観測した場合（issue #203）。`severity="error"`、`data.errors` にメッセージ列を返す。 |
| `EDITOR_CTRL_REFRESH_COMPILE_SUCCESS` | コンパイル待機を要求した `editor_refresh`（`wait_for_compile=true`）が、refresh で誘発したコンパイルの成功 + domain reload を観測した場合（issue #70）。`severity="info"`、`success=true`。 |
| `EDITOR_CTRL_REFRESH_COMPILE_FAILED` | コンパイル待機を要求した `editor_refresh` が、refresh で誘発したコンパイルで `CompilerMessageType.Error` を 1 件以上観測した場合（issue #70）。`severity="error"`、`data.errors` に実コンパイラ診断列を返す。 |
| `EDITOR_CTRL_REFRESH_COMPILE_TIMEOUT` | コンパイル待機を要求した `editor_refresh` が deadline 以内に誘発コンパイルの完了を観測できなかった、純粋な deadline 経過の場合（issue #70）。`severity="error"`。 |
| `EDITOR_CTRL_REFRESH_SCHEDULE_FAILED` | コンパイル待機を要求した `editor_refresh` の `AssetDatabase.Refresh()` 呼び出しを Editor が例外で拒否した schedule-failure 経路（issue #70）。`severity="error"`。例外本文は Unity Console にのみ出力され、MCP 応答には乗らない。 |
| `EDITOR_COMPILE_DEFERRED_BACKGROUND` | `editor_recompile` / `editor_refresh` / `editor_run_script` / `editor_run_script_poll` / `editor_execute_menu_item` の compile/reload wait が deadline に達し、Bridge が Unity Editor の focus を background / non-focused と明示観測した場合（issue #72）。`success=false`, `severity="warning"`。`data.operation`, `data.editor_focused=false`, `data.deferred_reason="editor_background_compile_reload"`, `data.elapsed_sec`, `data.budget_sec`, `data.diagnostic_compiling`, `data.job_retained`, `data.cleanup_performed` を返す。focused / focus unknown の deadline は従来の `EDITOR_CTRL_RECOMPILE_TIMEOUT` / `EDITOR_CTRL_REFRESH_COMPILE_TIMEOUT` / `EDITOR_RUN_SCRIPT_COMPILE_TIMEOUT` / `EDITOR_RUN_SCRIPT_SUBMIT_TIMEOUT` に残す。transport / file-IPC timeout (`EDITOR_BRIDGE_TIMEOUT` → `EDITOR_RUN_SCRIPT_TRANSPORT_TIMEOUT`) はこのコードへ reclassify しない。 |
| `EDITOR_CTRL_CREATE_UI_NO_NAME` / `..._BAD_TYPE` / `..._PARENT_NOT_FOUND` / `..._TMP_FONT_MISSING` / `..._OK` | `editor_create_ui_element` の応答コード（issue #195）。`..._BAD_TYPE` は `data.suggestions` に `Image` / `TextMeshProUGUI` / `Button` / `Slider` / `Toggle` の正規許容セットを含める。`..._TMP_FONT_MISSING` は warning（`success=false`）で、GameObject は作成されるが TextMeshPro の font は未代入。 |
| `INSPECT_WIRING_INVALID_CURSOR` / `INSPECT_WIRING_PAGE_SIZE_OUT_OF_RANGE` | `inspect_wiring` の pagination ガード（issue #197）。前者は `cursor` が `pos:<offset>` 形式でない、もしくは `[0, total]` の範囲外の場合に `severity="error"` を返す。後者は `page_size` が `[1, 500]` の外の場合に `severity="error"` を返す。 |
| `INSPECT_WIRING_EMPTY_FILTER_RESULT` | `inspect_wiring` の `script_filter` が non-empty にもかかわらずマッチするコンポーネントが 1 件もなかった場合（issue #227）。`severity="warning"`、メッセージに供給フィルタと正規化後のサフィックスを含める。caller が「filter のスペルミス」と「対象に MonoBehaviour がそもそも無い」を区別できるようにするため `INSPECT_WIRING_NO_MONOBEHAVIOURS` とは別コードに分離している。 |
| `VALIDATE_STRUCTURE_INVALID_TARGET_PATH` / `INSPECT_WIRING_INVALID_TARGET_PATH` / `INSPECT_HIERARCHY_INVALID_TARGET_PATH` / `INSPECT_MATERIALS_INVALID_TARGET_PATH` / `INSPECT_MATERIAL_ASSET_INVALID_TARGET_PATH` / `INSPECT_TRANSFORM_INVALID_TARGET_PATH` / `INSPECT_UNITY_EVENT_INVALID_TARGET_PATH` | Shared read-only target-file guard rejected the caller path because it was absolute, contained a `..` segment, or resolved outside `project_root`. `severity="error"` and `data.read_only=true`; the file is not read. |
| `EDITOR_RUN_SCRIPT_TRANSPORT_TIMEOUT` | `editor_run_script` が transport poll で timeout を観測した場合（issue #226）。`severity="error"`。Wrapper は bridge から返された汎用 `EDITOR_BRIDGE_TIMEOUT` 応答をこのコードに書き換え、メッセージに供給 `compile_timeout_ms` と派生した `transport_timeout_sec`、retry 推奨上限値の 3 つを含める。`data.compile_timeout_ms` / `data.transport_timeout_sec` / `data.compile_timeout_max_ms` でプログラム的にも参照できる。Transport budget は `max(RUN_SCRIPT_TRANSPORT_TIMEOUT_FLOOR_SEC=30, ceil(compile_timeout_ms / 1000) + RUN_SCRIPT_TRANSPORT_DISPATCH_MARGIN_SEC=5)` で算出され、bridge 側の deadline より transport が先に諦めることはない。 |
| `EDITOR_RUN_SCRIPT_COMPILE_TIMEOUT` | `editor_run_script` の compile-pending 段階で bridge 側の deadline (`compile_timeout_ms` + `RunScriptEntryTypeTimeoutMs(=4 s)`) が経過した場合（issue #234）。`severity="error"`、bridge → wrapper を素通しする（wrapper は transport-timeout rewrite を発火させない）。caller は `EDITOR_CTRL_RUN_SCRIPT_COMPILE`（compile / staging / entry-point failure）と `EDITOR_RUN_SCRIPT_TRANSPORT_TIMEOUT`（transport poll timeout）と本コードの 3 通りを応答コードだけで判別できる。応答 `data` には既存の compile-pending 診断 (`diagnostic_compiling` / `diagnostic_temp_files` / `diagnostic_last_domain_reload`) が unchanged で乗る。 |
| `EDITOR_CTRL_SAFE_SAVE_PREFAB_PROTECT_REQUIRED` | `editor_safe_save_prefab` の request payload に `protect_components` フィールド自体が含まれない場合（issue #193 / issue #228）。`severity="error"`。issue #228 でトリガが「リスト未指定」のみに narrow され、明示的な空リスト `[]` は raw-save mode へ向かう（rejection ではなく success path）。 |
| `STALE_GUID_INDEX_HINT`（`Diagnostic.detail`） | `validate_refs` の missing-asset 失敗パスで、cached resolver が missing と報告した GUID のうち少なくとも 1 件が fresh meta-file scan で resolve できた場合（issue #229）。トップレベル code ではなく warning severity の `Diagnostic` として diagnostics 配列に追加される。`evidence` に stale-resolved 件数と `refresh_guid_index=True` を retry 推奨として含める。`refresh_guid_index=True` がすでにセット済みの場合・missing asset がそもそも報告されなかった場合・fresh scan も resolve できなかった場合は発火しない。 |
| `CROP_ROI_INVALID` / `EDITOR_CTRL_CROP_ROI_INVALID` / `EDITOR_CTRL_CROP_ROI_OUT_OF_BOUNDS` / `EDITOR_CTRL_CROP_ROI_NO_TARGET` | `editor_screenshot` の `crop_roi` 検証（issue #249）。`severity="error"`。Wrapper 側は allowlist preset でも pixel quadruple でもない値を `CROP_ROI_INVALID` で pre-bridge reject。Bridge 側は同一形状違反を `_INVALID`、ピクセル範囲外を `_OUT_OF_BOUNDS`、対象 RenderTexture / Camera が存在しない場合を `_NO_TARGET` で返す。 |
| `SCREENSHOT_VIEW_INVALID` | `editor_screenshot` の `view` セレクタが `SCREENSHOT_VIEW_ALLOWLIST`（`scene` / `game`）外の場合（issue #259）。`severity="error"`、Wrapper 側で pre-bridge reject。 |
| `SCREENSHOT_DIMENSIONS_OUT_OF_RANGE` / `EDITOR_CTRL_SCREENSHOT_DIMENSIONS_OUT_OF_RANGE` | `editor_screenshot` の `width` / `height` が `0`（現在の view サイズを使う）または `[1, 4096]` ピクセルの範囲外だった場合。`severity="error"`。Wrapper 側は refresh / capture の前に拒否し、Bridge 側は output path 合成・`RenderTexture` / `Texture2D` allocation の前に拒否する。 |
| `SCREENSHOT_FIT_MODE_INVALID` | `editor_screenshot` の `fit_mode` が `max_axis` / `both_axes` 以外の場合。`severity="error"`。Wrapper 側は refresh / capture の前に拒否し、Bridge 側も bypass caller を target framing / file rendering 前に拒否する。 |
| `SCREENSHOT_ANGLE_INVALID` / `EDITOR_CTRL_SCREENSHOT_ANGLE_INVALID` | `editor_screenshot` の `angle` が `SCREENSHOT_ANGLE_PRESETS`（renderer: `front` / `three_quarter` / `back` / `right` / `left` / `top`; World Space UI: `front` / `back` / `current_camera`）外の場合（issue #84 / #95）。`severity="error"`。Wrapper 側は `target` 非空のときのみ allowlist gate を発火（`target` 空のとき `angle` は意味を持たない）。Bridge 側は defense-in-depth ミラーで、Wrapper を経由しない経路（integration test 等）に対しても同じ拒否を行う。 |
| `SCREENSHOT_TARGET_INVALID_VIEW` | `editor_screenshot` の object-capture モードが `view!='scene'` と組み合わされた場合（issue #84）。`severity="error"`、Wrapper 側で pre-bridge reject。object-capture は SceneView の framing 経路でしか実行できない。 |
| `SCREENSHOT_TARGET_CROP_CONFLICT` | `editor_screenshot` の `target` 指定と face-feature `crop_roi`（`eye_left` / `eye_right` / `mouth` / `auto_face`）の同時指定（issue #84）。`severity="error"`、Wrapper 側で pre-bridge reject。両方が SceneView の再フレーミングを駆動するため拒否。`target` と pixel-rectangle `crop_roi` は許容（ピクセル切り出しは framing の後段で独立）。 |
| `EDITOR_CTRL_SCREENSHOT_TARGET_NOT_FOUND` | `editor_screenshot` の object-capture モードで `target` の hierarchy path がアクティブな Scene / Prefab Stage に存在しなかった場合（issue #84）。`severity="error"`、Bridge 側で発火。 |
| `EDITOR_CTRL_SCREENSHOT_TARGET_NO_RENDERERS` | `editor_screenshot` の object-capture モードで resolved subtree に active enabled `Renderer` contributor が 1 件もなかった場合（issue #84 / #85）。`severity="error"`、Bridge 側で発火。AABB を導けないため framing を実行しない。 |
| `EDITOR_CTRL_SCREENSHOT_VIEW_INVALID` | `editor_screenshot` の Bridge 側 view-allowlist mirror（issue #259）。`severity="error"`。Wrapper 側 `SCREENSHOT_VIEW_INVALID` とペアで Bridge も同じ allowlist を強制し、出力 path 合成前に拒否する。 |
| `SCREENSHOT_TARGET_MODE_INVALID` / `SCREENSHOT_PROJECTION_INVALID` / `SCREENSHOT_PADDING_RATIO_INVALID` | `editor_screenshot` の World Space UI / target framing selector 検証（issue #95）。Wrapper 側で pre-bridge reject。Bridge 側も `target_mode` (`auto` / `renderer` / `world_space_ui`), `projection` (`auto` / `perspective` / `orthographic`), `padding_ratio` `[0.0, 1.0]` を mirror 検証する。 |
| `EDITOR_CTRL_SCREENSHOT_UI_UNSUPPORTED` | `editor_screenshot(target_mode=world_space_ui)` が Screen Space UI、World Space Canvas 外の RectTransform、または RectTransform contributor 不在を検出した場合。Screen Space Overlay / Camera UI の framing は Non-Goal。 |
| `EDITOR_CTRL_TRANSFORM_TARGET_NOT_FOUND` / `EDITOR_CTRL_BOUNDS_SOURCE_INVALID` / `EDITOR_CTRL_DISTANCE_MODE_INVALID` / `EDITOR_CTRL_EMPTY_BOUNDS_ONLY` | live geometry API (`editor_get_transform`, `editor_get_bounds`, `editor_measure_distance`) の typed geometry diagnostics（issue #98）。bounds source は `auto` / `renderer` / `collider` / `rect_transform` / `combined`、distance mode は `pivot` / `bounds_center` / `bounds_nearest`。 |
| `EDITOR_CTRL_SET_PROP_ENUM_PARSE_FAILED` / `..._AMBIGUOUS` / `..._VALUE_NOT_FOUND` / `..._INDEX_OUT_OF_RANGE` | `editor_set_property` enum value handling（issue #101）。name / display name は exact と case-insensitive を受理し、bare index / `index:N` / `value:N` を区別する。失敗メッセージは候補名・display 名を含む。 |
| `EDITOR_CTRL_SET_PROP_LAYERMASK_PARSE_FAILED` / `EDITOR_CTRL_SET_PROP_LAYERMASK_UNKNOWN_LAYER` | `editor_set_property` LayerMask handling（issue #101）。decimal / `0x` hex / `Nothing` / `Everything` / single layer name / JSON string-array layer list を受理する。comma-separated list は Non-Goal。 |
| `EDITOR_CTRL_UDON_SET_FIELD_INPUT_CONFLICT` / `..._VALUES_JSON_PARSE` / `..._NON_ARRAY_VALUES` / `..._ARRAY_LENGTH_MISMATCH` / `..._UNSUPPORTED_ARRAY_TYPE` / `..._ARRAY_ELEMENT_PARSE` / `..._ARRAY_SYNC_FAILED` | `editor_set_udonsharp_field(values_json=...)` whole-array writes（issue #102）。`value`, `object_reference`, `values_json` は相互排他。supported element types は string / int / float / bool / VRCUrl / Unity `ObjectReference` 派生型。`expected_length` が非負のとき配列長を厳密検証する。ObjectReference 配列要素は hierarchy path / asset path / `:ComponentType` suffix を文字列で解決し、型不一致は `data.field_name` / `data.element_index` / `data.expected_type` 付き parse error を返す。 |
| `EDITOR_CTRL_BATCH_BLEND_SHAPE_PARSE` | `editor_batch_set_blend_shape` の `shapes_json` を JSON として parse できなかった場合（issue #240）。`severity="error"`、Bridge 側で発火。 |
| `EDITOR_CTRL_PREFAB_STAGE_NOT_FOUND` / `..._OPEN_FAILED` / `..._CLOSE_FAILED` | Prefab Stage open/close 系の Bridge 失敗（issue #236）。`severity="error"`。`_NOT_FOUND` は対象 Prefab パスが解決できない、`_OPEN_FAILED` / `_CLOSE_FAILED` は `PrefabStageUtility` 呼び出しで例外。 |
| `REQUEST_ID_INVALID` | `editor_run_script_poll` 等の poll 系 MCP ツールが受け取った request identifier の形状違反（issue #233）。`severity="error"`、Python 入口で pre-bridge reject。期待形状は `prefab_sentinel.mcp_tools_editor_exec._build_request_id_invalid_envelope` を参照。 |
| `EDITOR_CTRL_RUN_SCRIPT_UNKNOWN_REQUEST` / `EDITOR_RUN_SCRIPT_SUBMIT_TIMEOUT` | `editor_run_script_submit` / `editor_run_script_poll` の bridge 側応答（issue #233）。`severity="error"`。前者は async runner が当該 request id を保持していない場合、後者は submit から bridge が deadline 内に ACK を返さなかった場合に発火。 |
| `EDITOR_CTRL_ANIMATION_CLIP_NOT_FOUND` / `..._TARGET_NOT_FOUND` / `..._WRITE_FAILED` / `..._APPLY_FAILED` | AnimationClip 検査・編集系の Bridge 失敗（issue #243）。`severity="error"`。`_NOT_FOUND` は clip asset 不在、`_TARGET_NOT_FOUND` は curve の binding 先 GameObject 不在、`_WRITE_FAILED` / `_APPLY_FAILED` は AssetDatabase 書き込み / Animator 反映の失敗。 |
| `EDITOR_CTRL_FORCE_REFRESH_FAILED` | `force_scene_view_refresh` が player-loop tick 内で例外を観測した場合（issue #242）。`severity="error"`、Bridge 側で発火。 |
| `INSPECT_HIERARCHY_RESULT` / `INSPECT_HIERARCHY_NO_GAMEOBJECTS` | `inspect_hierarchy` の標準成功 / non-GameObject warning 応答。`expand_prefab_instances=true` の場合は `data.roots[]` が effective hierarchy node を持ち、各 node の `origin` に source default / nested instance / override-bearing host / effective node の metadata を含む。既定 `expand_prefab_instances=false` は従来の非展開 hierarchy contract を維持する。 |
| `EFFECTIVE_HIERARCHY_SOURCE_UNRESOLVED` / `EFFECTIVE_HIERARCHY_CYCLE` / `EFFECTIVE_HIERARCHY_DEPTH_LIMIT` / `EFFECTIVE_HIERARCHY_TRANSFORM_CHILD_CYCLE` | expanded effective hierarchy の warning diagnostic。missing/unreadable nested Prefab source、PrefabInstance cycle、configured depth limit、または malformed Transform child back-edge で該当 branch だけを停止し、resolved sibling branches は成功応答内に残す。 |
| `INSPECT_TRANSFORM_VALUES` | `inspect_transform_effective_values` の成功応答。`data.values` に `local_position` / `local_rotation` / `local_scale` / `world_position` / `world_rotation` / `world_scale` を持ち、それぞれ default / override / effective / origin metadata を返す。world values は effective parent chain が完全に解決できる場合だけ `computed=true` で返る。 |
| `INSPECT_TRANSFORM_SYMBOL_NOT_FOUND` / `INSPECT_TRANSFORM_FILE_NOT_FOUND` / `INSPECT_TRANSFORM_READ_ERROR` | Transform effective inspector の typed failure。symbol miss、asset 不在、UTF-8 decode / read failure を分離し、いずれも `data.read_only=true` を返し value table は返さない。 |
| `INSPECT_TRANSFORM_SYMBOL_AMBIGUOUS` | Transform effective inspector の typed failure。同じ offline `symbol_path` が複数の effective node に一致する場合に返し、last-write-wins で別 branch の Transform 値を返さない。 |
| `INSPECT_TRANSFORM_NUMERIC_PARSE_ERROR` | Transform effective inspector の typed failure。Transform override の numeric value が parse できない場合に `severity="error"` で返し、推測値や default 値への silent fallback は行わない。 |
| `INSPECT_TRANSFORM_WORLD_UNRESOLVED` | Transform effective inspector の warning diagnostic。local default/override/effective values は残し、uncomputed world values は `computed=false` + diagnostic code で返して推測値を出さない。 |
| `INSPECT_UNITY_EVENT_LISTENERS` | `inspect_unity_event_listeners` の成功応答。Button.onClick / Slider.onValueChanged / Toggle.onValueChanged に限り、persistent listener entry として target object path、target component type、method、persistent listener mode、argument、call state、source/default vs host override/effective origin を返す。diagnostics は同じ inspector 応答に同梱し、別 validator API はない。 |
| `INSPECT_UNITY_EVENT_UNSUPPORTED_SURFACE` / `INSPECT_UNITY_EVENT_OBJECT_NOT_FOUND` / `INSPECT_UNITY_EVENT_OBJECT_AMBIGUOUS` / `INSPECT_UNITY_EVENT_COMPONENT_NOT_FOUND` / `INSPECT_UNITY_EVENT_FIELD_NOT_FOUND` / `INSPECT_UNITY_EVENT_FILE_NOT_FOUND` / `INSPECT_UNITY_EVENT_READ_ERROR` | UnityEvent listener inspector の typed failure。unsupported selector、offline object path miss/ambiguity、supported component miss、serialized UnityEvent field miss、asset 不在、read/decode failure を分離する。unsupported selector の message は supported set `Button.onClick`, `Slider.onValueChanged`, `Toggle.onValueChanged` を列挙する。 |
| `INSPECT_UNITY_EVENT_NUMERIC_PARSE_ERROR` | UnityEvent listener inspector の typed failure。persistent listener の mode / call state / numeric argument / array size が parse できない場合に `severity="error"` で返し、silent fallback は行わない。 |
| `INSPECT_UNITY_EVENT_LISTENER_BOUNDS_ERROR` | UnityEvent listener inspector の typed failure。serialized `Array.size` または sparse indexed listener override が source / serialized member evidence を超える listener list materialization を要求する場合に返し、read-only inspection で arbitrary list growth を行わない。 |
| `INSPECT_UNITY_EVENT_TARGET_UNRESOLVED` | UnityEvent listener inspector の warning diagnostic。listener target component が選択中の effective prefab instance 内で解決できない場合に発火し、listener entry は空 target metadata を返す。 |
| `INSPECT_UNITY_EVENT_UDONSHARP_PROXY_TARGET` | UnityEvent listener inspector の warning diagnostic。listener target が UdonSharp proxy で、backing UdonBehaviour が同じ saved YAML 構造上に存在し、listener method が `SendCustomEvent` ではない場合だけ発火する。proxy identity だけでは warning にしない。 |
| `INSPECT_HIERARCHY_RECT_PARENT_UNRESOLVED` | `inspect_hierarchy` で stretched anchor を持つ RectTransform の親 rect chain が未解決の場合（issue #238）。`severity="warning"`、`Diagnostic.detail` に当該識別子。 |

## diagnostics baseline metadata

`validate_refs` / `inspect_wiring` / `validate_all_wiring` / `validate_structure` / `validate_materials` は、project root に `config/diagnostics_baseline.json` が存在する場合、current diagnostics を stable key で `new` / `known` / `resolved` に分類する。baseline file が存在しない場合も、classification が要求される呼び出しでは `status="absent"` または `status="not_loaded_no_project_root"` として空 baseline を返す。

`data.diagnostics_baseline` の形:

| field | 説明 |
|-------|------|
| `status` | `loaded` / `absent` / `not_loaded_no_project_root` / `invalid`。invalid は error envelope 側で返る。 |
| `path` | baseline file path。project root が無い場合は `null`。 |
| `new_count` / `known_count` / `resolved_count` | current diagnostics と baseline-only diagnostics の分類件数。 |
| `new[]` / `known[]` / `resolved[]` | stable key, severity, message, data を持つ diagnostic key records。 |

`validate_refs` の current key 例:

- `missing_asset_guid:<guid>`
- `missing_local_id_external:<target_path>:<file_id>`
- `missing_local_id_local:<source_path>:<file_id>`

`inspect_wiring` の current key 例:

- `inspect_wiring:null_reference:<source_prefab-or-target>:<component_file_id>:<field_name>`
- `inspect_wiring:internal_broken_ref:<source_prefab-or-target>:<component_file_id>:<field_name>`
- `inspect_wiring:duplicate_reference:<source_prefab-or-target>:<component_file_id>:<field_name>`

`validate_all_wiring` は aggregate summary key を作らず、各 file の `inspect_wiring` field-level key を集約して分類する。

`validate_structure` の current key 例:

- `validate_structure:duplicate_file_id:<target_path>:fileID:<file_id>:<evidence>`
- `validate_structure:missing_component:<target_path>:fileID:<gameobject_file_id>:<evidence>`

`validate_materials` の current key 例:

- `validate_materials:<diagnostic_code>:<asset_or_scope_path>:<stable_location>`

## `update_diagnostics_baseline` response

`update_diagnostics_baseline(source, target, mode="preview", prune_resolved=False, confirm=False, change_reason="", details=False, include_details=False)` は supported source validation を再実行し、その応答の `data.diagnostics_baseline.new[]` と `resolved[]` から project root の `config/diagnostics_baseline.json` に入る次の key set を計算する。

Supported source:

- `validate_refs`: `target` を `scope` として渡し、`details` をそのまま渡す。
- `inspect_wiring`: `target` を `asset_path` / `target_path` として渡す。
- `validate_all_wiring`: `target` を scan scope または file target として渡す。
- `validate_structure`: `target` を `asset_path` / `target_path` として渡す。
- `validate_materials`: `target` を `scope` として渡し、`include_details` をそのまま渡す。

成功時の `data` は以下を含む。

| field | 説明 |
|-------|------|
| `path` | project root の `config/diagnostics_baseline.json`。 |
| `mode` | `preview` または `write`。 |
| `baseline_status` | update 前 baseline の loader status。 |
| `written` | preview は `false`。write 成功時だけ `true`。 |
| `would_create` | update 前に baseline file が無く、write すれば新規作成になる場合 `true`。 |
| `known_count_before` / `known_count_after` | update 前後の key 数。 |
| `added_count` / `pruned_count` | `new[]` から追加された key 数と、`prune_resolved=True` で削除された key 数。 |
| `added_sample` / `pruned_sample` | 既定 20 件で capped された key sample。 |
| `known_diagnostics` | sorted / deduped な update 後 key set。 |

`mode="preview"` は `config/` を作らず、file write もしない。`mode="write"` は `confirm=True` と非空 `change_reason` を要求し、条件を満たした場合だけ schema v1 JSON を indent 2、UTF-8、末尾 newline で書く。`prune_resolved=False` が既定で、resolved key は明示指定がない限り保持される。

## `inspect_wiring` filtered diagnostics

`script_filter` が non-empty の場合、`inspect_wiring` は component list だけでなく diagnostics も filtered component と out-of-scope component に分ける。

- top-level `success` / `severity` は filtered diagnostics だけから決まる。filtered component が clean なら、out-of-scope warning があっても `success=true`, `severity="info"`。
- top-level `diagnostics[]` は filtered diagnostics のみ返す。対象外の detail rows は混入させない。
- `data.diagnostic_counts.filtered` / `data.diagnostic_counts.out_of_scope` は severity 別件数を返す。
- `data.filtered_diagnostics[]` は filtered component に属する diagnostics の wire rows。
- `data.out_of_scope_diagnostics[]` は `include_out_of_scope_diagnostics=true` のときだけ返る。既定では counts のみ返す。
- detail rows と severity 別件数は同じ診断単位の分類を使う。明示された `severity` を維持し、未指定の場合も集合の最大値で全行を上書きしない（Issue #241）。
- `summary_only=true` でも `data.diagnostic_counts` は返り、`components` / `filtered_diagnostics` / `out_of_scope_diagnostics` の detail arrays は抑制される。
- `include_out_of_scope_diagnostics` は `script_filter` が空のとき detail flag としては無視され、out-of-scope partition は作られない。
- 0-match でも同じ partition 規則を適用し、`INSPECT_WIRING_EMPTY_FILTER_RESULT` / `severity="warning"` / `component_count=0` と実際の out-of-scope 件数を返す。対象外 detail は明示 opt-in かつ `summary_only=false` の場合だけ返す（Issue #115 の early-return 回帰修正）。

### severity 境界: `critical` と `error` の使い分け

- `critical`: 後続処理の継続が不可能な停止級エラー。実行時に検出された致命的な状態（例: `UDON_NULLREF` マッチ、ClientSim が起動不能）。呼び出し元は即座に停止し、ユーザー判断を仰ぐ。
- `error`: 契約違反や入力の不備だが、呼び出し元の文脈では実行自体は続行しうる（例: `SER001`/`SER002`/`REF001`/`BRIDGE_LEGACY_SCHEMA_REJECTED`/`CHANGE_REASON_REQUIRED`）。該当操作は拒否されるが、後続の無関係な操作は継続可能。
- `warning` / `info`: 情報系。診断のみ、動作への影響なし。

## Runtime Validation レスポンス (`classify_errors`)

`validate_runtime` / `RuntimeValidationService.classify_errors` の `data` ペイロードは下記 2 キーで件数を返す（旧 `matched_issue_count` / `categories` は削除済み・互換なし）。

| キー | 型 | 説明 |
|---|---|---|
| `count_total` | `int` | マッチしたログ行の総数 |
| `count_by_category` | `dict[str, int]` | カテゴリ別のヒット件数（例: `{"UDON_NULLREF": 3}`） |

`UDON_NULLREF` がマッチした場合、`severity="critical"` を返す。それ以外は最大ランク（`info < warning < error < critical`）の severity を返す。

## `editor_run_script` (MCP ツール / Editor Bridge アクション)

`editor_run_script` は Unity Editor 内で完全な C# compilation unit を 1 ステップでコンパイル・実行する MCP ツール（Issue #74 / #201）。非同期の `editor_run_script_submit` も同じ `code` 契約を使う。

- 入力: `code: str`, `confirm: bool`, `change_reason: str`, `compile_timeout_ms: int = 15000`
- `code` は method body や statements ではなく、global namespace に正確な `public static class PrefabSentinelTempScript` を置き、parameterless な `public static void Run()` または `public static T Run()` を定義する完全な compilation unit。`T` は `string`、`bool`、数値 primitive（`byte` / `sbyte` / `short` / `ushort` / `int` / `uint` / `long` / `ulong` / `float` / `double` / `decimal`）、または 1 次元の `string[]` / `bool[]` / `byte[]` / `short[]` / `int[]` / `long[]` / `float[]` / `double[]` を受理し、null return value も受理する。method body の自動 wrap は行わない。
- 最小の直接実行可能な入力:

  ```csharp
  public static class PrefabSentinelTempScript { public static void Run() { } }
  ```

- `confirm=True` **かつ** 非空の `change_reason` が常に必須。どちらかを欠く呼び出しは Bridge に到達する前に `CHANGE_REASON_REQUIRED` で拒否される。dry-run モードは未サポート。
- Bridge 側では `Assets/Editor/_PrefabSentinelTemp/<temp_id>.cs` にソースを書き出し、`AssetDatabase.Refresh()` でコンパイル後、固定名の public static `PrefabSentinelTempScript.Run()` を呼び出す。成功・失敗を問わず temp の `.cs` / `.cs.meta` は応答前に削除する。Editor 起動時にも前回クラッシュの残骸を掃除する。
- 既定のコンパイル待ち budget は 15000 ms（issue #116）。コールド起動でも大きめのスニペットが 1 度で確定するように調整した値。
- `compile_timeout_ms` の許容範囲は **`[1, 120000]` ミリ秒（両端含む、120 秒上限）**（issue #127）。範囲外を渡すと Bridge へは送信せず Python の入口で `COMPILE_TIMEOUT_OUT_OF_RANGE`（`severity="error"`）を返す。clamp はしない。上限はワーストケースで Editor Bridge の poll を 1 リクエストあたり 120 秒に制限するためのセキュリティガード。下限は 0 / 負値（busy loop / 即時エラー）を排除する。
- スタック検出: 同一コード（`temp_id`、もしくは省略時はコード本文の安定ハッシュ）が連続して `..._COMPILE` 拒否となった場合、2 回目で Bridge が temp ディレクトリを再掃除して `AssetDatabase.Refresh` を要求し、`EDITOR_CTRL_RUN_SCRIPT_RECOVERY`（severity=warning）を返す。次回呼び出しでは Bridge を再起動せずに復帰できる。
- すべての `..._COMPILE` / `..._RECOVERY` 応答に診断 (`diagnostic_compiling`, `diagnostic_temp_files`, `diagnostic_last_domain_reload`) が添付される。
- `EDITOR_COMPILE_DEFERRED_BACKGROUND` は compile/reload wait 中に Unity Editor が background / non-focused と明示観測された場合の retryable warning。同期 `editor_run_script` は temp staging を掃除して返すため、Unity を foreground に戻して同じ snippet を再実行する。非同期 `editor_run_script_submit` は background deadline では job を保持し、`editor_run_script_poll(cleanup_on_timeout=True)` が `job_retained=true`, `cleanup_performed=false` を返した場合は同じ `request_id` を foreground 後に再 poll する。
- `editor_run_script_poll` の `data.status` は `pending` / `completed` / `failed` のいずれか。completion artifact は Unity DTO へ materialize する前に、root object と exactly one decoded top-level object-valued `data`、完全な JSON syntax / input consumption だけを構造検査する。この条件を満たさない completion は新しい status を追加せず `EDITOR_CTRL_RUN_SCRIPT_COMPLETION_INVALID` / `status="failed"` とする（issue #244）。存在確認済み artifact の read failure も `EDITOR_CTRL_RUN_SCRIPT_COMPLETION_READ_FAILED` / `status="failed"` とし、どちらも実行・副作用の不確実性を `state_unknown=true`、write-class operation を `read_only=false` で表す。raw artifact、path、例外詳細は公開しない（issue #257）。
- エラーコード: `EDITOR_CTRL_RUN_SCRIPT_OK` / `..._COMPILE` / `..._RUNTIME` / `..._BAD_ID` / `..._RECOVERY` / `..._COMPLETION_INVALID` / `..._COMPLETION_READ_FAILED`。
- 応答 `data` は `stdout`（テキスト出力）、`return_value`（上記の JSON-safe primitive / primitive array または null）、`outputs`（コードが `Output.Add(key, value)` で明示した primitive / primitive-array map）、`exception`（型名・短い message・redacted stack）、`path_hints`（WSL `/mnt/<drive>/...` に対する Windows path / `Assets/...` / `Application.dataPath` guidance）を分離して返す。入力 source は自動変換しない。

Issue #72 の live Unity 確認は TAKT 後に `deploy_bridge` で Bridge C# を配置し、Unity compile error 0 件、foreground true-timeout、background deferred timeout、async submit/poll retention を同じ Unity プロジェクトで確認する。Source tests は Bridge の field / builder / callsite wiring を固定するが、Unity version ごとの focus/reload runtime 差はこの live 確認で補完する。

## Editor camera modes (`editor_set_camera`)

`editor_set_camera` は SceneView を Unity 公開 API `SceneView.LookAt(point, rotation, size, ortho, instant: true)` 経由で同期的に駆動する。3 つのモードは相互排他（issue #112）。

| モード | 入力 | 効果 |
|--------|------|------|
| Pivot orbit | `pivot` (+ `yaw` / `pitch` / `size`) | pivot を中心に yaw/pitch/size で周回（`size` は SceneView 半幅、issue #81）。pivot 省略時は現在値を維持。 |
| Position | `position` (+ `look_at` または `yaw`/`pitch`) | カメラ世界座標を直接指定。`look_at` で注視点モード、`yaw`/`pitch` でオイラーモード。`position` と `pivot` の同時指定は `EDITOR_CTRL_CAMERA_CONFLICT`。public projection state が不安定な場合は `EDITOR_CTRL_CAMERA_PROJECTION_TRANSITION`。 |
| Reset | `reset_to_defaults=True` | pivot=`(0,0,0)`, rotation=`Euler(30, -45, 0)`, size=10、perspective に戻す。他のパラメータは無視。 |

**Yaw=0 の参照軸は +Z**。`yaw=0, pitch=0` のときカメラは +Z 方向を見る。Unity 内部の Euler とは反転しているため、Bridge 側で `internalYaw = (yaw + 180) mod 360` を適用してから `Quaternion.Euler` に渡す。

応答の `data.camera_position` は `LookAt(instant=true)` 完了後の世界座標スナップショット。前回値は `data.previous_camera_*` として返る。

## Variant 判定ルール

YAML が Prefab Variant かどうかは「**`m_SourcePrefab` 参照が存在し**、かつ **自身に GameObject ブロックを持たない**」を同時に満たすことを要件とする。`m_SourcePrefab` 参照のみを根拠にすると、ネストされた `PrefabInstance` を含む通常の base prefab を誤って Variant 扱いする（issue #114）。

判定は `prefab_sentinel.unity_assets.is_variant_prefab(text)` に集約しており、`orchestrator_variant._resolve_variant_base` および `inspect_hierarchy` がこのヘルパー経由で判定する。

## 非致命例外の分類 (`editor_safe_save_prefab` / `editor_console`)

Bridge は内部に「non-fatal exception pattern table」を持ち、ログ分類に利用する（issue #117）。現行登録パターン:

| label | 条件 |
|-------|------|
| `udonsharp_obs_nre` | `LogType.Exception` で message に `ArgumentNullException`、stack trace に `OnBeforeSerialize` を含むエントリ |

挙動:

- `editor_safe_save_prefab` / `editor_instantiate_to_scene` は操作中に発生したログを当該テーブルで分類し、件数とラベル一覧を `data.warnings.udonsharp_obs_nre_count` / `data.warnings.nonfatal_patterns` に積む。`SaveAsPrefabAsset` が成功している限り、ノイズが出ても応答は `success=true`。
- `editor_console` は `classification_filter` パラメータを受け取り、`all`（既定）/ `non_fatal`（テーブルにマッチしたものだけ）/ `fatal`（テーブルにマッチしないものだけ）を返す。値が不正なら `EDITOR_CTRL_INVALID_CLASSIFICATION_FILTER`。

### `editor_console` の既定値とページング (issue #113, breaking)

issue #113 で `editor_console` の既定値とページング契約を**破壊的に**置き換えた。後方互換は提供しない。

| パラメータ | 旧既定値 | 新既定値 | 備考 |
|----------|----------|----------|------|
| `since_seconds` | `0.0`（時間フィルタなし） | `60.0` 秒（直近 60 秒） | 対話的デバッグの典型ユースケースに合わせた。`0.0` を渡せば従来どおり時間フィルタなし。 |
| `order` | （存在せず、常に oldest-first） | `"newest_first"` | 受理可能な値: `newest_first` / `oldest_first`。範囲外は `EDITOR_CTRL_INVALID_ORDER`。 |
| `cursor` | （存在せず） | `""`（空＝先頭ページ） | 不透明な継続トークン。`""` 以外は前回応答の `next_cursor` をそのまま渡す。Bridge のフォーマット (`seq:<long>`) に合致しない場合や取り込み済み範囲外は `EDITOR_CTRL_INVALID_CURSOR`。 |

ページング動作:

- 各エントリには Bridge 側で取り込み時刻に単調増加する `sequence_id`（`long`）が割り当てられる。`order` が指す方向で buffer を歩き、`cursor` 位置を **排他的に** 越えたエントリだけを取り出す。
- 1 ページに `max_entries` 件まで詰めた状態でフィルタ条件を満たすエントリがまだ存在すれば、応答 `data.next_cursor` に不透明トークン（`seq:<long>`、Bridge 私物のフォーマット）を返す。次の呼び出しで同じトークンを `cursor` に渡せば続きから取得できる。
- 末尾まで到達すると `next_cursor` は空文字列。
- `order` を切り替える場合は `cursor` をリセットすること（前回トークンを別方向で再利用しても同じページが返る保証はない）。

## `editor_set_property` の Quaternion サポート (issue #111)

`editor_set_property` は `SerializedPropertyType.Quaternion`（例: `m_LocalRotation`）に対して xyzw 4 要素のリテラル文字列のみを受け付ける。Euler 入力は対象外（既存の euler hint 専用 SerializedProperty 経由で設定する）。

- 入力: カンマ区切りの 4 要素 `"x,y,z,w"`（順序は xyzw 固定）。
- `null` 入力は `EDITOR_CTRL_SET_PROP_NULL_INPUT`。未設定を空文字へ丸めない。
- 4 要素以外（例えば 3 要素の euler）は `EDITOR_CTRL_SET_PROP_TYPE_MISMATCH`。メッセージに「4 要素必須」を明示。
- ノルムが `1.0 ± 1e-4` の許容範囲外なら `EDITOR_CTRL_SET_PROP_QUATERNION_NOT_NORMALIZED`（`severity="error"`）。Bridge 側で自動 normalize はしない。許容幅は Unity の Transform.localRotation が float32 でやり取りされる際の丸め誤差を吸収する目的。
- 同一トランザクションで euler hint を同期する副作用は持たない（要件は呼び出し側に委ねる）。

## live editor geometry と UI screenshot

- `editor_get_transform(hierarchy_path)` は live Transform の local/world position、quaternion/euler rotation、local/lossy scale、parent path、active flags を返す。
- `editor_get_bounds(hierarchy_path, source="auto", include_children=True)` は renderer / collider / RectTransform contributors を world-space AABB に集約し、center / extents / size / min / max / contributor metadata を返す。`source="combined"` は対象 source の contributors 全体を集約する。contributors が無い場合は `EDITOR_CTRL_BOUNDS_UNAVAILABLE`。
- `editor_measure_distance(hierarchy_path, target_path, mode="pivot", bounds_source="auto")` は pivot 距離、bounds center 距離、または nearest-AABB 距離を返す。`bounds_nearest` では overlap 距離は 0。
- `editor_screenshot(target_mode="world_space_ui")` は active RectTransform contributors を World Space Canvas 上で集約し、orthographic SceneView framing を既定にする。`target_mode="auto"` でも target が active RectTransform contributors を持ち World Space Canvas 配下なら同じ UI branch に routing する。RectTransform contributor が無い auto target は renderer capture にフォールバックし、renderer capture を強制する場合は `target_mode="renderer"` を使う。応答は `bounds_source`, `bounds_center`, `bounds_extents`, `ui_normal`, `camera_position`, `camera_look_at`, `camera_orthographic`, `camera_size` を含む。
- `editor_screenshot(target=..., fit_mode="max_axis")` は既定の renderer target capture sizing。`fit_mode="both_axes"` は target bounds と `angle` preset から両軸が収まる aspect を求め、`width=0,height=0` では現在 SceneView 由来の default long edge を維持して short edge を再計算する。`width` と `height` を両方指定した場合はその aspect を render / solver の aspect とし、片側だけの指定は supplied side + default other side の既存 sizing contract に留める。
- renderer target capture と `editor_frame` は `bounds_policy` を受ける。既定 `all_visible_renderers` は対象 GameObject 配下の active enabled child Renderers 全体を AABB に集約し、単一 Renderer では `contributor_count=1`, `excluded_count=0` を返す。明示 `focus_core` は既存の core-focused framing に opt-in し、除外された renderer details を返す。renderer bounds response は `bounds_policy`, `bounds_center`, `bounds_extents`, `contributor_count`, `excluded_count`, included contributor evidence, excluded renderer evidence を持つ。

## Before-value 解決の `UnresolvedReason` StrEnum (issue #124, breaking)

`prefab_sentinel.services.serialized_object.before_cache.resolve_before_value` の戻り値型を **breaking** に置き換えた。後方互換は提供しない。

- 旧契約: 解決失敗時はラベル付きの sentinel 文字列（`"(unresolved)"` / `"(unresolved: file unreadable)"` / `"(unresolved: not a variant)"` / `"(unresolved: type not found in chain)"` / `"(unresolved: ambiguous component type)"` / `"(unresolved: not found in chain)"`）を返していた。`patch_preview.soft_warnings_for_preview` は `before_val.startswith("(unresolved")` の string-prefix sniff で検出していた。
- 新契約: 戻り型は `str | UnresolvedReason`（`UnresolvedReason` は `enum.StrEnum`）。解決成功時は plain `str`、解決失敗時は下表のいずれかの enum メンバを返す。`soft_warnings_for_preview` は `isinstance(before_val, UnresolvedReason)` で検出し、診断 evidence に enum の `.value` を載せる。

| Member | 発生条件 |
|--------|--------|
| `UnresolvedReason.NO_VARIANT_RESOLVER` | サービスに `PrefabVariantService` が bind されていない |
| `UnresolvedReason.FILE_UNREADABLE` | 対象 YAML が `OSError` で読めない（解決アタック中に削除された場合等） |
| `UnresolvedReason.NOT_A_VARIANT` | 対象が Variant ではなく base prefab |
| `UnresolvedReason.EMPTY_CHAIN` | チェーンは解決したが値マップが空 |
| `UnresolvedReason.TYPE_NOT_FOUND` | チェーンの class map に当該 component 型名が存在しない |
| `UnresolvedReason.AMBIGUOUS_TYPE` | 当該 component 型名がチェーンに 2 件以上存在する |
| `UnresolvedReason.PATH_NOT_FOUND` | property path が解決済み chain values に存在しない |

外部呼び出し側で旧 sentinel 文字列に依存している箇所は `isinstance(..., UnresolvedReason)` ベースに書き換える必要がある。`StrEnum` を継承しているため `str(value)` および `f"{value}"` で取り出した値は enum の `.value` 文字列（例: `"type_not_found"`）になる。

## テスト環境変数の取り扱い

ユニットテストは Editor Bridge のディスパッチ環境変数（`UNITYTOOL_BRIDGE_WATCH_DIR`）が **ホストシェルから漏れていない状態** を前提に動作する（issue #88, #89, #270）。

- `tests/test_unity_patch_bridge.py::_invoke_bridge` はテスト中に上記変数を pop し、各テストが決定的な状態から開始するようにする。
- `tests/test_services.py::RuntimeValidationServiceTests` および `SerializedObjectServiceTests` は `setUp` で同変数を pop し、`addCleanup` で復元する。
- 開発者シェルが `UNITYTOOL_BRIDGE_WATCH_DIR` を export した状態でも、`scripts/run_unit_tests.py` は green を維持する。

## `editor_run_tests` acceptance profile (issue #186)

`editor_run_tests` remains the existing public MCP tool; this profile extension adds no public tool. Its public input schema is:

| Field | Default | Contract |
| --- | --- | --- |
| `profile`（default: `default`） | `default` | `default` preserves the historical suite; `bridge_acceptance` selects only the bounded #186 fixture matrix. |
| `live_probes`（default: `false`） | `false` | `bridge_acceptance` requires explicit `true`; `default` requires `false`. |
| `run_id`（default: empty string） | `""` | `bridge_acceptance` requires exactly 32 lowercase hexadecimal characters; `default` requires empty. |
| `timeout_sec`（default: `300`） | `300` | Maximum Bridge wait in seconds. |

Unknown profiles and invalid combinations are rejected before fixture mutation with `EDITOR_CTRL_TEST_PROFILE_INVALID`, `EDITOR_CTRL_TEST_LIVE_PROBES_REQUIRED`, `EDITOR_CTRL_TEST_LIVE_PROBES_INVALID`, or `EDITOR_CTRL_TEST_RUN_ID_INVALID`. The public fields map once to the private Bridge DTO fields `test_profile`, `run_live_probes`, and `run_id`; an environment variable never authorizes the acceptance profile.

The `bridge_acceptance` response `data` is structured rather than parsed from the human-readable `message`: the echoed `run_id`, `acceptance_total` / `acceptance_passed` / `acceptance_failed`, exactly six `acceptance_cases` entries (`name`, `passed`, stable `code`), plus `fixture_owned` and `lease_phase`. The public acceptance report copies only these allowlisted fields; case messages and raw diagnostics remain private. `ACCEPTANCE_OK` requires the echoed ID to equal the controller-generated ID, all six cases to pass, `fixture_owned=true`, and `lease_phase="smoke_complete"`.

### Local acceptance terminal result codes

The CLI's terminal `result.code` is stable. `ACCEPTANCE_OK` is the only successful terminal code; every other entry exits nonzero and still attempts atomic report publication.

| Code | Meaning |
| --- | --- |
| `ACCEPTANCE_OK` | All required phases and cleanup postconditions succeeded. |
| `ACCEPTANCE_OPT_IN_REQUIRED` | `--confirm-live` was absent; no live side effect started. |
| `ACCEPTANCE_CONFIG_ERROR` | Required path/configuration/package/source identity input was invalid or unavailable. |
| `ACCEPTANCE_SOURCE_DIRTY` | The managed Python/Bridge/version surface differs from checkout HEAD. |
| `ACCEPTANCE_EDITOR_UNAVAILABLE` | The configured running Editor/Bridge cannot be reached or identified safely. |
| `ACCEPTANCE_EDITOR_STATE_BLOCKED` | Editor state is dirty, unsaved, playing, compiling, building, stage-dirty, or project-mismatched. |
| `ACCEPTANCE_DEPLOY_FAILED` | #193 safe deployment failed or its response manifest disagreed with source identity. |
| `ACCEPTANCE_COMPILE_FAILED` | Independent DLL/log observation found compiler errors or an invalid compile result. |
| `ACCEPTANCE_COMPILE_TIMEOUT` | Independent changed-deploy compile did not complete by its fixed deadline. |
| `ACCEPTANCE_COMPILE_LOG_CONTINUITY_LOST` | Editor log replacement, truncation, identity loss, decode failure, or the bounded event-line limit prevented continuous evidence. |
| `ACCEPTANCE_SMOKE_FAILED` | A bounded acceptance case, secondary Bridge check, or Console probe failed. |
| `ACCEPTANCE_SMOKE_TIMEOUT` | Smoke transport timed out; same-run lease recovery is attempted before publication. |
| `ACCEPTANCE_CLEANUP_FAILED` | Lease-owned restore/deletion/postcheck failed; this overrides an earlier smoke success. |
| `ACCEPTANCE_POSTCONDITION_FAILED` | Final Scene setup, dirty state, lease, or Console postcondition differed from preflight. |
| `ACCEPTANCE_REPORT_WRITE_FAILED` | Terminal report reservation/finalization failed; sanitized operation evidence remains authoritative on stdout/private log. |

`--recover-run-id <32-lowercase-hex>` is the explicit public CLI recovery mode, not an MCP tool. It still requires `--confirm-live` and the normal required command arguments. It reserves/publishes a report, activates the requested project, calls private same-run `acceptance_status` then `cleanup_integration_tests`, verifies project state and final Console zero, and stops. It returns `ACCEPTANCE_OK` only after those postchecks; failed status/cleanup maps to `ACCEPTANCE_CLEANUP_FAILED`, and unsafe final state/Console maps to `ACCEPTANCE_POSTCONDITION_FAILED`. It never runs source identity, deploy, compile, smoke, a new run, or retry; `source`, `deploy`, `compile`, and `smoke` therefore remain exact neutral placeholders.

## Unity acceptance cleanup lease (issue #186)

Python の `AcceptancePhaseResult` は構築時に caller-owned nested data / diagnostics を snapshot 化し、`to_dict()` でも独立した nested payload を返す。元入力や返却辞書の後続変更で保存済み phase を変えない境界であり、公開 `.data` 辞書を一般的な immutable mapping に置き換える契約ではない（Issue #243）。source-dirty の早期失敗では、未実行 evidence sections が neutral のまま公開されることを独立した literal 回帰で固定する（Issue #245）。

`run_integration_tests(test_profile="bridge_acceptance")` creates the private lease at `Library/PrefabSentinel/acceptance-lease-v1.json` before its first fixture mutation. The private Bridge actions `acceptance_status` and `cleanup_integration_tests` accept and echo the same 32-character lowercase-hex `run_id`; they expose `phase`, `cleanup_required`, and production field `cleanup_performed`, never the lease path or serialized document. Once the run request begins, the controller calls both actions unconditionally and then observes final project state and Console, including status failure/interruption paths. Successful cleanup additionally reports `scene_setup_restored`, `deleted_fixture_count`, `deleted_request_artifact_count`, and `lease_removed` summaries without returning any artifact path. Normal success requires `cleanup_performed=true`, `scene_setup_restored=true`, `lease_removed=true`, exactly one deleted fixture root, and exactly five deleted request artifacts. Recovery applies the same ownership/restoration booleans and accepts only zero through one fixture-root deletion and zero through five request-artifact deletions, covering a reserved-only or already-cleaned lease without admitting impossible counts.

| Code | Meaning |
| --- | --- |
| `ACCEPTANCE_STATUS_OK` / `ACCEPTANCE_CLEANUP_OK` | Same-run status or cleanup completed; repeated cleanup after a deleted lease is a successful no-op. |
| `EDITOR_CTRL_ACCEPTANCE_RUN_ID_INVALID` | `run_id` is not exactly 32 lowercase hexadecimal characters. |
| `EDITOR_CTRL_ACCEPTANCE_RUN_MISMATCH` | A private status or cleanup request names a run other than the active lease owner. |
| `EDITOR_CTRL_ACCEPTANCE_LEASE_INVALID` | The private lease cannot be read or fails schema, ownership-path, Scene-snapshot, or transition validation. |
| `EDITOR_CTRL_ACCEPTANCE_LEASE_UNRESOLVED` | A new acceptance run was refused before fixture mutation because a valid active lease remains. Use the explicit cleanup action, then stop; it does not start another run. |
| `EDITOR_CTRL_ACCEPTANCE_PHASE_INVALID` | The requested owner transition skips the fixed `reserved → fixture_created → smoke_complete → cleanup_started → cleaned` order. |
| `EDITOR_CTRL_ACCEPTANCE_CLEANUP_FAILED` | Restore, Scene postcondition, fixture deletion, lease publication, or final lease removal did not complete. The lease remains for same-run recovery. |
