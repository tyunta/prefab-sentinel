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

`diagnostics[]` の wire 上 contract は単一の 4 キー dict `{severity, code, message, data}` に統一されている（issue #244 以降の標準形、issue #304 でレガシー経路も同 contract へ adapt 済み）。`mcp_tools_validation.py:127` 等の新規 emitter は `ToolResponse.to_dict()` の戻り値に対し直接この 4 キー dict を append する（例: `IGNORE_GUIDS_FILE_LOADED`）。レガシー orchestrator 経路で構築される `prefab_sentinel.contracts.Diagnostic` dataclass も、`ToolResponse.to_dict()` 内の `_diagnostic_to_wire` adapter を通じて同じ 4 キー dict へ正規化される: `Diagnostic.detail` → wire `code`、`Diagnostic.evidence` → wire `message`（空文字なら `code` フォールバック）、`Diagnostic.path` / `Diagnostic.location` は非空時のみ `data` 配下に格納される。`mcp_tools_session._build_session_diagnostic` ヘルパは session-level の ad-hoc 経路（`deploy_bridge` / `get_project_status`）が同 contract で wire に乗ることを保証し、`get_project_status` は live Bridge `get_editor_state` diagnostics も同じ 4 キー shape に正規化して status envelope へ引き継ぐ。

wire `severity` の決定規則（issue #4）: `Diagnostic` dataclass は任意の per-item `severity`（`str | None`、既定 `None`）を持つ。`_diagnostic_to_wire` は **diagnostic 自身の `severity` が設定されていればそれを優先**し、`None` のときはエンベロープの `severity` を継承する（`diag.severity or default_severity`）。これにより 1 つのエンベロープ内で個々の diagnostic が異なる severity を運べる（例: envelope が `error` でも一部 diagnostic は `warning`）。per-item `severity` は `Severity` 語彙に対して検証されない任意文字列であり、設定しない限り wire 出力は従来と byte-identical。

## `validate_materials` response

`validate_materials(scope: str | None = None, include_details: bool = False)` は read-only の静的 Material / shader / TMP / icon-font validator。`scope` は file または directory を受け付け、明示 scope が無い場合だけ activate 済み session scope を使う。明示 scope と session scope のどちらも無い場合は project root scan にフォールバックせず、`MATERIAL_VALIDATION_SCOPE_REQUIRED` を返す。

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

## エラーコード規約

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
| `RUN001` | Udon runtime exception |
| `RUN002` | ClientSim startup failure |
| `CHANGE_REASON_REQUIRED` | `confirm=True` で呼ばれた書き込み系ツールが `change_reason` を欠いた場合。`editor_run_script` は `confirm=False` や空文字の `change_reason` も同コードで拒否する（監査トレイル強制）。 |
| `ASSET_DELETE_DRY_RUN` | `delete_asset` / `delete_assets` の dry-run 計画成功。`data.targets[]` に asset / `.meta` / reference impact、`data.pre_delete_broken_references` に削除前 baseline、`data.related_candidates[]` に deterministic UdonSharp generated program 候補、`data.decision_required[]` に ambiguous UdonSharp generated program 候補の判断材料を返す。 |
| `ASSET_DELETE_APPLIED` | Unity Editor Bridge の `delete_assets` action が AssetDatabase delete を完了した成功応答。`data.broken_reference_delta` を必ず返し、delta 増加時も AssetDatabase 成功なら failure に変換しない。 |
| `ASSET_DELETE_NOT_FOUND` | 削除対象の `Assets/...` asset が存在しないため dry-run / apply を拒否した場合。 |
| `ASSET_DELETE_META_UNREADABLE` | 削除対象 asset の `.meta` が読み取れない、または UTF-8 として decode できないため dry-run / apply を拒否した場合。 |
| `ASSET_DELETE_EXTERNAL_PACKAGE_UNSUPPORTED` | `Packages/`、`Library/`、project 外、または package-cache 相当の path が削除対象に指定された場合。 |
| `ASSET_DELETE_UNSUPPORTED` | Unity Editor Bridge / AssetDatabase delete action が未設定・未対応・利用不能なため、confirmed apply を拒否した場合。raw filesystem delete への fallback は行わない。 |
| `ASSET_DELETE_FAILED` | AssetDatabase delete action が `failed_paths` を返した場合。 |
| `ASSET_DELETE_DECISION_REQUIRED` | UdonSharp generated program asset 候補が複数または曖昧で、自動削除候補として扱えない場合の diagnostic code。`delete_asset` / `delete_assets` dry-run では `data.decision_required[]` に `detail` と候補 `asset_paths[]` を返す。 |
| `SER003` | `set_properties` が dry-run 段階でチェーン上に解決できない property path を検出した場合（issue #109）。`severity="error"`、`data.suggestions` に近似候補（最大 5 件）、`diagnostics[].detail` に `property_not_found` を載せる。issue #41 で `set_properties` は `symbol_path` を直接 component に解決するため、component 不在は `SYMBOL_NOT_FOUND` で表面化する（`SER003` の `component_not_found` 経路は廃止）。 |
| `SER_APPLY_REJECTED` | `patch_apply` の Prefab 経路で `SerializedObject.ApplyModifiedPropertiesWithoutUndo()` 直前のバリデーション（`TryApplyOp`）が op を拒否した場合（issue #298）。`severity="error"`。`diagnostics` 配列には各失敗 op の `BridgeDiagnostic` に加え、`property_path` / `component_type` / `attempted_value` を `evidence` に埋めた summary 行が末尾に追加される。`AudioSource.m_Priority` 等の既知トラップを応答だけで診断できることが目的。issue #37 以降、`set` op の `file_id` ターゲットがアセット内のどの component にも解決しない場合も、この経路で `apply_error` diagnostic（未解決 fileID を `evidence` に明示、`location` は `ops[N].file_id`）として fail-fast で表面化する。Editor 例外路は `UNITY_BRIDGE_APPLY_EXCEPTION` のまま（未捕捉例外と rejection を別コードで区別）。 |
| `BRIDGE_LEGACY_SCHEMA_REJECTED` | `unity_patch_bridge` がレガシー形状（トップレベル `target` キー）のリクエストを受け取った場合。v2 スキーマ（`{plan_version, resources, ops}`）のみを受け入れる。互換レイヤは存在しない。 |
| `EDITOR_CTRL_RUN_SCRIPT_OK` / `..._COMPILE` / `..._RUNTIME` / `..._BAD_ID` | `editor_run_script` の成功 / コンパイル失敗 / 実行例外 / 不正 temp id。 |
| `EDITOR_CTRL_RUN_SCRIPT_RECOVERY` | 同一スニペットが 2 回連続で `..._COMPILE` 拒否された場合に発火する `severity="warning"` 応答（issue #116）。Bridge は temp ディレクトリを掃除し、`AssetDatabase.Refresh` で再コンパイルを要求した上で、診断ペイロード（`diagnostic_compiling` / `diagnostic_temp_files` / `diagnostic_last_domain_reload`）を返す。次回呼び出しはクリーンな状態で再試行できる。 |
| `EDITOR_CTRL_ADD_COMPONENT_REUSED` / `..._RELINKED` | `editor_add_component` が UdonSharp 派生型に対して呼ばれ、既存ペアが見つかった（reuse）または孤立 proxy に新規 UdonBehaviour を再リンクした（relinked）場合の `severity="info"` 成功応答（issue #103）。 |
| `EDITOR_CTRL_CAMERA_CONFLICT` | `editor_set_camera` が `position` と `pivot` を同時指定、または `look_at` を `position` 抜きで指定した場合（issue #112）。 |
| `EDITOR_CTRL_CAMERA_PROJECTION_TRANSITION` | `editor_set_camera(position=...)` が SceneView projection transition 中と判定された場合（SceneView と backing Camera の orthographic state mismatch、または perspective `fieldOfView` が non-finite / non-positive）。`severity="error"`。Bridge は private SceneView internals を reflection せず、自動 wait / retry もせず、caller に settle 後の再試行を要求する。 |
| `EDITOR_CTRL_INVALID_CLASSIFICATION_FILTER` | `editor_console` の `classification_filter` が `all` / `non_fatal` / `fatal` 以外の場合（issue #117）。 |
| `EDITOR_CTRL_INVALID_PHASE_FILTER` | `editor_console` の `phase_filter` が `all` / `edit` / `play` / `build` 以外の場合（issue #239）。`severity="error"`、メッセージで受理可能な値を列挙。Bridge 境界で buffer に触れる前に拒否される。 |
| `EDITOR_CTRL_EDITOR_STATE_OK` | `get_editor_state` action の成功時応答コード（issue #239 / issue #40）。`get_project_status` MCP ツールと offline symbol-reference ツールから内部的に発火し、`data.editor_state` に play/build/compile bool フラグ、`has_unsaved_changes`、active scene / Prefab Stage、`active_stage_kind`、dirty state、project root identity、bridge/plugin/session/instance identity を返す。live Bridge response には `operator_context` が同梱され、caller は reached Unity project root と expected ProjectSession root を比較できる。通常は `severity="info"`、ただし scene / stage / dirty-category enumeration が一部失敗して `EDITOR_STATE_ENUMERATION_LIMITED` diagnostics を含む場合は successful warning response になり、`get_project_status` もその diagnostic と warning severity を引き継ぐ。 |
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
| `DIAGNOSTICS_BASELINE_INVALID` | project root の `config/diagnostics_baseline.json` が invalid JSON または schema 不一致だった場合。`validate_refs` / `inspect_wiring` MCP wrapper は orchestrator を呼ぶ前に `success=false`, `severity="error"` で停止し、`data.path` と `data.read_only=true` を返す。 |
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
| `EDITOR_CTRL_CREATE_UI_NO_NAME` / `..._BAD_TYPE` / `..._PARENT_NOT_FOUND` / `..._TMP_FONT_MISSING` / `..._OK` | `editor_create_ui_element` の応答コード（issue #195）。`..._BAD_TYPE` は `data.suggestions` に `Image` / `TextMeshProUGUI` / `Button` / `Slider` / `Toggle` の正規許容セットを含める。`..._TMP_FONT_MISSING` は warning（`success=false`）で、GameObject は作成されるが TextMeshPro の font は未代入。 |
| `INSPECT_WIRING_INVALID_CURSOR` / `INSPECT_WIRING_PAGE_SIZE_OUT_OF_RANGE` | `inspect_wiring` の pagination ガード（issue #197）。前者は `cursor` が `pos:<offset>` 形式でない、もしくは `[0, total]` の範囲外の場合に `severity="error"` を返す。後者は `page_size` が `[1, 500]` の外の場合に `severity="error"` を返す。 |
| `INSPECT_WIRING_EMPTY_FILTER_RESULT` | `inspect_wiring` の `script_filter` が non-empty にもかかわらずマッチするコンポーネントが 1 件もなかった場合（issue #227）。`severity="warning"`、メッセージに供給フィルタと正規化後のサフィックスを含める。caller が「filter のスペルミス」と「対象に MonoBehaviour がそもそも無い」を区別できるようにするため `INSPECT_WIRING_NO_MONOBEHAVIOURS` とは別コードに分離している。 |
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

`validate_refs` と `inspect_wiring` は、project root に `config/diagnostics_baseline.json` が存在する場合、current diagnostics を stable key で `new` / `known` / `resolved` に分類する。baseline file が存在しない場合も、classification が要求される呼び出しでは `status="absent"` または `status="not_loaded_no_project_root"` として空 baseline を返す。

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

## `inspect_wiring` filtered diagnostics

`script_filter` が non-empty の場合、`inspect_wiring` は component list だけでなく diagnostics も filtered component と out-of-scope component に分ける。

- top-level `success` / `severity` は filtered diagnostics だけから決まる。filtered component が clean なら、out-of-scope warning があっても `success=true`, `severity="info"`。
- top-level `diagnostics[]` は backward compatibility のため従来どおり全体 diagnostics を保持する。
- `data.diagnostic_counts.filtered` / `data.diagnostic_counts.out_of_scope` は severity 別件数を返す。
- `data.filtered_diagnostics[]` は filtered component に属する diagnostics の wire rows。
- `data.out_of_scope_diagnostics[]` は `include_out_of_scope_diagnostics=true` のときだけ返る。既定では counts のみ返す。
- `summary_only=true` でも `data.diagnostic_counts` は返り、`components` / `filtered_diagnostics` / `out_of_scope_diagnostics` の detail arrays は抑制される。
- `include_out_of_scope_diagnostics` は `script_filter` が空のとき detail flag としては無視され、out-of-scope partition は作られない。

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

`editor_run_script` は Unity Editor 内で C# スニペットを 1 ステップでコンパイル・実行する MCP ツール（Issue #74）。

- 入力: `code: str`, `confirm: bool`, `change_reason: str`, `compile_timeout_ms: int = 15000`
- `confirm=True` **かつ** 非空の `change_reason` が常に必須。どちらかを欠く呼び出しは Bridge に到達する前に `CHANGE_REASON_REQUIRED` で拒否される。dry-run モードは未サポート。
- Bridge 側では `Assets/Editor/_PrefabSentinelTemp/<temp_id>.cs` にソースを書き出し、`AssetDatabase.Refresh()` でコンパイル後 `PrefabSentinelTempScript.Run()`（`public static void`、固定のクラス/メソッド名）を呼び出す。成功・失敗を問わず temp の `.cs` / `.cs.meta` は応答前に削除する。Editor 起動時にも前回クラッシュの残骸を掃除する。
- 既定のコンパイル待ち budget は 15000 ms（issue #116）。コールド起動でも大きめのスニペットが 1 度で確定するように調整した値。
- `compile_timeout_ms` の許容範囲は **`[1, 120000]` ミリ秒（両端含む、120 秒上限）**（issue #127）。範囲外を渡すと Bridge へは送信せず Python の入口で `COMPILE_TIMEOUT_OUT_OF_RANGE`（`severity="error"`）を返す。clamp はしない。上限はワーストケースで Editor Bridge の poll を 1 リクエストあたり 120 秒に制限するためのセキュリティガード。下限は 0 / 負値（busy loop / 即時エラー）を排除する。
- スタック検出: 同一スニペット（`temp_id`、もしくは省略時はコード本文の安定ハッシュ）が連続して `..._COMPILE` 拒否となった場合、2 回目で Bridge が temp ディレクトリを再掃除して `AssetDatabase.Refresh` を要求し、`EDITOR_CTRL_RUN_SCRIPT_RECOVERY`（severity=warning）を返す。次回呼び出しでは Bridge を再起動せずに復帰できる。
- すべての `..._COMPILE` / `..._RECOVERY` 応答に診断 (`diagnostic_compiling`, `diagnostic_temp_files`, `diagnostic_last_domain_reload`) が添付される。
- エラーコード: `EDITOR_CTRL_RUN_SCRIPT_OK` / `..._COMPILE` / `..._RUNTIME` / `..._BAD_ID` / `..._RECOVERY`。
- 応答 `data` は `stdout`（テキスト出力）、`return_value`（JSON-safe primitive または null）、`outputs`（snippet が `Output.Add(key, value)` で明示した primitive / primitive-array map）、`exception`（型名・短い message・redacted stack）、`path_hints`（WSL `/mnt/<drive>/...` に対する Windows path / `Assets/...` / `Application.dataPath` guidance）を分離して返す。snippet source は自動変換しない。

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
