# MCP ツール表面規約

`prefab-sentinel-mcp` が公開する **MCP protocol / result 境界、住所表現、引数命名、監査ペア要否** の規約の正本。[docs/tools.md](./tools.md) が「どのツールがあるか」のカタログであるのに対し、本書は「どの request / result contract でツールを公開し、どう名付け・住所し・監査するか」を定める。

本書は規約（あるべき形）の正本であり、新規ツールの追加・既存ツールの変更は本書に従う。本書ではスキーム名をハイフン（`asset-path`）、実際の引数名をアンダースコア（`asset_path`）で表記する。

_規約確定: 2026-05-18_

## MCP protocol / result 境界

公開 MCP contract は Tools capability のみを宣言する。stdio は modern `2026-07-28` と、二つの revision を持つ legacy era（`2025-11-25` / `2025-06-18`）を受理する。

| stdio revision | request allowlist | notification allowlist |
|---|---|---|
| `2026-07-28` | `server/discover` / `tools/list` / `tools/call` | `notifications/cancelled` |
| `2025-11-25` | `initialize` / `tools/list` / `tools/call` | `notifications/initialized` / `notifications/cancelled` |
| `2025-06-18` | `initialize` / `tools/list` / `tools/call` | `notifications/initialized` / `notifications/cancelled` |

stdio の `notifications/cancelled` は全 revision で SDK handler へ転送し、`notifications/initialized` は各 legacy lifecycle だけで転送する。resource / prompt / `ping` は全 revision に公開しない。HTTP は `2026-07-28` のみで、client-to-server notification を持たないため、HTTP gate は request ID のない notification を `-32600` で拒否する。

この節は受理 surface の規約であり、full conformance の合格宣言ではない。現行 protocol error の優先順位と stdio transport 例外は [api-reference.md](./api-reference.md#エラーコード規約)、process-wide application state の statelessness 逸脱は [ARCHITECTURE.md](../ARCHITECTURE.md#mcpserver--protocol-boundary) を正本とする。

modern `2026-07-28` の各 request は `_meta["io.modelcontextprotocol/protocolVersion"]="2026-07-28"` と `_meta["io.modelcontextprotocol/clientCapabilities"]` を必須とする。`_meta["io.modelcontextprotocol/clientInfo"]` は optional だが、client は送信することが推奨される。legacy `2025-11-25` と `2025-06-18` の stdio request は modern request `_meta` を使わず、それぞれの `initialize.params` に lifecycle metadata（`protocolVersion`、capabilities、client information）を入れ、`notifications/initialized` の後に `tools/list` / `tools/call` を送る。Tool 名、引数 schema、result と domain envelope の contract は両 era で共有する。modern の namespaced `_meta` は request ごとの metadata であり、legacy initialize lifecycle の代替ではない。

`tools/call` の失敗は次の 3 境界を混同しない。

- ツールが正常に実行され、Prefab Sentinel の domain envelope が `success=false` を返す場合は、MCP `CallToolResult.isError=false` のまま `structuredContent` に envelope を保持する。domain failure は MCP execution failure ではない。
- SDK の引数 schema 検証や tool handler の実行自体が失敗した場合は `CallToolResult.isError=true` とする。
- protocol version / request metadata / request-method allowlist の違反は tool result に包まず、top-level JSON-RPC error とする。numeric code と HTTP status の正本は [api-reference.md「エラーコード規約」](./api-reference.md#エラーコード規約)。

## 1. 住所スキーム

MCP ツールが対象を指す住所は 5 種類。大きく **project 側（asset ファイルを指す）** と **scene 側（asset 内 / live editor のオブジェクトを指す）** に分かれる。引数名はこの区別を必ず表に出す（§2.1）。

| スキーム | 指すもの | 側 |
|----------|----------|----|
| asset-path | project の asset ファイル | project |
| asset-guid | asset ファイル（GUID 表現） | project |
| symbol-path | asset 内のオブジェクト（offline symbol tree 経由） | scene |
| hierarchy-path | live editor の scene / Prefab Stage オブジェクト | scene |
| patch-selector | patch op 内のコンポーネント指定 | scene |

scene 側スキーム（symbol-path / hierarchy-path / patch-selector）では、同名の兄弟要素を `#N`（0 始まりの出現順）で一意化する。曖昧な住所は解決せず error で停止する（fail-fast）。

### 1.1 asset-path

`.prefab` / `.unity` / `.asset` / `.mat` / `.cs` / `.anim` 等、project 内の asset ファイルを相対パスで指す（例: `Assets/Prefabs/Mic.prefab`）。offline ツールと editor_* ツールの双方が使う。

`validate_refs(scope=...)` の `scope` は検査対象 file / directory selector であり、GUID / fileID の解決 universe ではない。`Assets/...` 配下の file scope を指定しても、project 内 sibling / parent asset の GUID は active project root で解決する。応答では `scan_scope` と `guid_resolution_root` を分けて返す。

offline `copy_asset` の source は inspect-side と同じ project-relative `Assets/...` normalization を使う。絶対パスや project 外 source は `ASSET_COPY_SOURCE_INVALID_PATH`、project 内だが存在しない source は `ASSET_COPY_SOURCE_NOT_FOUND` とし、`normalized_candidate_path` / `resolution_root` / `reason` を diagnostic evidence として返す。

offline `rename_asset` の source は inspect-side と同じ project-relative normalization を使う。project root 外 source は `ASSET_RENAME_INVALID_PATH` とし、`normalized_candidate_path` / `resolution_root` / `reason` を diagnostic evidence として返す。`new_name` は同一ディレクトリ内の bare filename のみを受け入れる。`../x.mat`、path separator、absolute path、Windows drive-rooted path、または project root 外へ解決される name は `ASSET_RENAME_INVALID_NAME` で拒否する。cross-directory move は `editor_move_asset` の責務であり、offline rename は source asset / `.meta` を変更しない。

`editor_create_generated_asset` の `asset_path` は case-sensitive `.renderTexture` generated asset の destination を表し、`editor_move_asset` の `source_asset_path` / `destination_asset_path` は AssetDatabase.MoveAsset に渡す project-relative asset path を表す。どちらも `.meta` path を対象にせず、Editor Bridge 側で AssetDatabase state を確認する。

### 1.2 asset-guid

asset ファイルを 32 文字 hex の GUID で指す。asset-path の代替表現であり、同一の asset を指す 2 つの方法。path と GUID の両方を受けるツールは「どちらか一方を指定」とする。

### 1.3 symbol-path

offline symbol tree（YAML 直読みで構築する人間可読ツリー）上のオブジェクトを指す。GameObject パス + 末尾コンポーネントの形（例: `CharacterBody/MeshRenderer`、`Body/Head/MonoBehaviour(PlayerScript)`）。`get_unity_symbols` / `find_unity_symbol` / offline の `set_property` / `set_properties` 等が使う。権威は last-saved disk YAML — live editor の未保存編集は反映されない（Editor Bridge 接続中かつ未保存変更がある場合、`get_unity_symbols` / `find_unity_symbol` はペイロードに freshness マーカーを付与する — issue #40）。

`get_unity_symbols(expand_nested=true)` の display path は人間が読む identity であり、nested / duplicate / placeholder entry では lookup key と一致しない場合がある。expanded nested payload が `lookup.asset_path`, `lookup.symbol_path`, `lookup.expand_nested=true` を持つ場合、`find_unity_symbol` はその lookup object の値を canonical key として受け取る。display-only path を lookup に使って miss しても、空 `matches` は正常な no-match payload であり、discovery payload 側が正しい lookup identity を提示する。

### 1.4 hierarchy-path

live editor の scene または active Prefab Stage 上の GameObject を `/` 区切りで指す（例: `/Canvas/Panel/Button`）。editor_* ツールが使う。Prefab Stage が開いていれば stage root を先に解決する。権威は live editor。

live geometry (`editor_get_transform` / `editor_get_bounds` / `editor_measure_distance`) と target screenshot (`editor_screenshot(target=...)`) は同じ hierarchy-path authority を使う。geometry は read-only で、missing / ambiguous path は typed envelope で fail-fast する。routine geometry inspection は dedicated geometry tool を使い、`editor_run_script` snippets を第一選択にしない。

`editor_frame` と `editor_screenshot(target=...)` の renderer framing は `bounds_policy` を共有する。既定の `all_visible_renderers` は対象 GameObject 配下の active enabled child Renderers をすべて集約する。`focus_core` は明示 opt-in の policy で、core-focused framing に戻したい場合だけ指定する。policy 名を省略して implicit filtering に戻す経路は持たない。

### 1.4.1 live/saved status provenance

offline asset-path / symbol-path inspection は saved disk YAML を読む。`get_project_status` は single status surface として live Editor state を補助的に返し、Bridge 由来 fields は `state_source="live_editor"` を持つ。dirty scene / prefab / material / asset identities と blocker records は live API が提供できた範囲だけの evidence であり、saved-disk inspection の代替 authority ではない。

Bridge / live Editor blocker vocabulary は `watch_dir`, `bridge_connection`, `compile_or_build`, `playmode_transition`, `prefab_stage_for_scene_bound_operation`, `dirty_or_save_blocker` に固定する。tool error がこの vocabulary に分類できる場合は `blocker_class` と `suggested_next_action` を返し、evidence-free failure では blocker を合成しない。

### 1.5 patch-selector

patch v2 スキーマの op 内でコンポーネントを指す文字列フォーマット `TypeName@/hierarchy/path`（例: `MeshRenderer@/Body/Head`）。引数ではなく op フィールドの値なので §2.1 の引数命名規約の対象外。

## 2. 命名規約

### 2.1 住所引数

住所を受ける引数は **`[<role>_]<kind>_path`** または **`[<role>_]asset_guid`** で名付ける。

- `<kind>` ∈ `{hierarchy, asset, symbol}`。**引数名だけで scene 側か project 側かが判別できる**こと（必須要件）。
- 操作対象そのもの（主対象）は role を付けない: `hierarchy_path` / `asset_path` / `symbol_path`。
- 操作対象以外の参照（副参照）は役割を表す `<role>_` を前置する。`<role>` はその引数が操作の中で果たす役割を表す語: `parent_hierarchy_path` / `target_hierarchy_path` / `prefab_asset_path` / `material_asset_path` / `output_asset_path`。
- GUID 表現は `[<role>_]asset_guid`（例: `material_asset_guid`）。
- `path` / `scene_path` / `script_path` のような kind を欠く名、`target_path` のような kind を表さない曖昧な名は使わない。
- patch-selector（§1.5）は引数でなく op フィールド値なので本規約の対象外。

### 2.2 property 引数

コンポーネントの SerializedProperty パスを指す引数は **`property_name`** で統一する。`field_name` / `path` 等の別語を使わない。リスト要素の dict キーも同様（例: `fields[].property_name`）。

### 2.3 sync / async

同期版と非同期版が対になるツールでは、**bare 名（接尾辞なし）を同期（ブロッキング — 呼べば結果が返る）** とする。

- 非ブロッキングの fire-and-return 版は `_async` 接尾辞を付ける。
- 完了に長時間かかり poll が要るジョブは `_submit` / `_poll` の 2 ツールに分ける。
- bare 名が片方で同期・片方で非同期、という既定値の不一致を作らない。

### 2.4 value 引数

「値」を受け取る引数は、**「未指定」と「空値」を別の表現にする**。`value: str = ""` のように既定値が「未指定」のセンチネルを兼ねる設計を禁止する（空文字列を書けなくなる）。`value: str | None = None` のように、値が与えられたか否かを内容と独立に表せる型にする。

0.9.x の narrow exception として、`editor_set_udonsharp_field.values_json` だけは非 nullable の `str = ""` を維持する。omitted または空文字列を「配列値なし」として扱い、explicit JSON `null` は SDK 2.x の公開 input schema で schema-invalid としてサポートしない。この例外を他の value 引数へ一般化してはならない。

### 2.5 単複

ツール名の単複は操作の arity に一致させる。

- 1 件を扱う → 単数（`editor_set_blend_shape`）
- 複数を返す / 扱う → 複数（`editor_get_blend_shapes`）
- 複数を 1 Undo グループで一括処理 → `batch_` 接頭辞（`editor_batch_set_blend_shape`）

## 3. 監査ペア原理

### 3.1 原理

書き込み系ツールは、その効果が次の **(a)(b)(c) のいずれかに該当するとき**、`confirm=True` + 非空 `change_reason` の監査ペアを要求する。

- **(a) arbitrary code 実行** — 呼び出し側が事前に挙動を確定できないコードを走らせる（任意 C# スニペット、任意の登録済メニューアイテム等）
- **(b) 外部公開** — プロジェクト外のサービスへ送信・公開する
- **(c) 非 Undo の asset 改変** — Unity の Undo で戻せない形で asset ファイルをディスク上で変更する（生成・上書き・削除。`.prefab` / `.asset` / `.unity` / `.anim` の保存、offline YAML 書き込み等）

いずれにも該当しない write（Undo 可能な scene 変更、live editor 状態の変更）と read は監査ペアを要求しない。**batch であること自体は監査理由にしない** — Undo 可能な操作の batch は 1 Undo グループで戻るため。

### 3.2 適用例

原理 (a)(b)(c) を各ツールに適用した分類。個別ツールの監査要否の確定一覧は [CONFIGURATION.md](../CONFIGURATION.md#confirm--change_reason-必須対象一覧) を参照。

| 区分 | 例 | 根拠 |
|------|----|------|
| 監査側 | `editor_run_script` / `editor_run_script_submit` / `editor_execute_menu_item` | (a) |
| 監査側 | `vrcsdk_upload` | (b) |
| 監査側 | `delete_asset` / `delete_assets` / `editor_create_generated_asset` / `editor_move_asset` / `editor_create_animation_clip` / `editor_safe_save_prefab` / `editor_create_udon_program_asset` / `editor_create_scene` / `editor_save_scene` / `editor_close_prefab`(save=True) | (c) |
| 監査側 | offline write（`patch_apply` / `set_property` / `add_component` 等） | (c) disk YAML 書き込み |
| 非監査側 | `editor_set_property` / `editor_set_blend_shape` / `editor_batch_set_property` / `editor_batch_set_blend_shape` / `editor_apply_animation_clip` 等 | Undo 可能な scene / live 変更 |
| 条件付き監査 | `validate_runtime(profile="clientsim")` | ClientSim は Play Mode / dirty scene state に触れうるため明示 profile + audit pair を要求する。`compile_only` / `editor_console_only` は read-only profile。 |

`delete_asset` / `delete_assets` は dry-run が既定で、確定適用時だけ `confirm=True` + 非空 `change_reason` を要求する。適用経路は Unity `AssetDatabase` を持つ Editor Bridge action に限定し、Bridge / AssetDatabase が使えない場合は typed error を返して filesystem delete へ迂回しない。削除後に broken reference が増えた場合も、tool は可否判断をせず `broken_reference_delta` として報告する。

`editor_create_generated_asset` / `editor_move_asset` は dry-run が AssetDatabase state を読むため Bridge に到達するが、audit/report 引数は検証しない。確定適用時だけ `confirm=True` + 非空 `change_reason` + `out_report` を要求し、final response と同一 JSON を report に書く。`copy_asset` / `rename_asset` は offline file/YAML 操作のまま、`delete_assets` は削除専用の AssetDatabase-backed patch tool のままで、issue #116 の create/move 操作へ責務を統合しない。
