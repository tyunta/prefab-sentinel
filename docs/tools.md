# MCP ツール一覧

`prefab-sentinel-mcp` が公開する全 MCP ツール（現在 89 件 / 16 カテゴリ）の正本カタログ。各カテゴリは `prefab_sentinel/mcp_tools_*.py` の 1 モジュールに対応する。エンベロープ仕様は [api-reference.md「レスポンスフォーマット」](./api-reference.md#レスポンスフォーマット)、エラーコードの正本は [api-reference.md「エラーコード規約」](./api-reference.md#エラーコード規約) を参照。

## 用途索引

カテゴリ別の「いつ使うか」ガイド。最初に触れる場合はここからスキルへ繋ぐ。

- **components** — open / create モードでコンポーネントを増減・コピーする (`/prefab-sentinel:variant-safe-edit`)
- **patch** — マテリアル・アセット・削除 dry-run / 確定削除・パッチ計画 (`patch_apply`) を扱う
- **set_property** — シンボルパス + コンポーネント型でフィールド値を狙い撃ち編集する
- **validation** — broken reference / 配線 / 構造 / 命名整合 / ランタイムを read-only で診断する (`/prefab-sentinel:prefab-reference-repair` の起点)
- **symbols** — 人間可読パスで Unity オブジェクトをアドレッシングする
- **session** — `activate_project` でスコープを宣言し、`deploy_bridge` で Bridge C# を同期する
- **editor_view** — Editor Bridge 経由で Scene/Hierarchy/Console を read する（スクショ・カメラ・ログ）
- **editor_geometry** — Transform / Bounds / Distance を dedicated read-only API で取得する
- **editor_write** — Editor Bridge 経由で Hierarchy / Component / BlendShape / Menu を write する
- **editor_ops** — `editor_set_property` / `editor_safe_save_prefab` 等の compound 編集を行う
- **editor_advanced** — VRC SDK アップロード / 任意リフレクション呼び出し
- **editor_animation** — AnimationClip の inspect / create / apply プリミティブ
- **editor_batch** — 複数オブジェクト・プロパティ・マテリアルを 1 Undo グループで一括処理する
- **editor_exec** — Editor 内で C# スニペットを 1 ステップでコンパイル・実行する (`editor_run_script`)
- **editor_prefab_stage** — Prefab Stage を open / close して in-place 編集する
- **editor_udonsharp** — UdonSharp 派生コンポーネントの追加・フィールド書き込み・listener 配線

## 全ツール一覧（カテゴリ別）

各カテゴリ表のカラム: ツール / 区分 / 簡潔説明 / 関連 issue / 種別（read-only / write）。`write` はプロジェクト / Editor / アセット状態に副作用を持つことを示す表示で、`confirm=True` + 非空 `change_reason` を引数として要求する audit-pair 対象はこの中の部分集合（`patch` / `components` / `set_property` の各カテゴリ全体と、`vrcsdk_upload` / `editor_run_script` / `editor_run_script_submit` / `editor_create_animation_clip` / `editor_execute_menu_item` / `editor_safe_save_prefab` / `editor_create_udon_program_asset` / `editor_set_material_property` / `editor_add_udonsharp_component` / `editor_set_udonsharp_field` / `editor_wire_persistent_listener` / `editor_create_scene` / `editor_save_scene` / `editor_close_prefab`）に限られる。正本一覧は [CONFIGURATION.md](../CONFIGURATION.md#confirm--change_reason-必須対象一覧)。それ以外の `write` ツール（例: `editor_select` / `editor_rename` / `editor_set_blend_shape` / `editor_set_property` / `editor_batch_*` / `editor_batch_set_blend_shape` / `editor_apply_animation_clip` 等）は audit-pair なしで呼び出す（`confirm` / `change_reason` を引数に渡すと `TypeError` で拒否される）。issue #49 で `editor_execute_menu_item` / `editor_safe_save_prefab` / `editor_create_udon_program_asset` / `editor_create_scene` / `editor_save_scene` が audit-pair 側へ、`editor_batch_set_blend_shape` / `editor_apply_animation_clip` が非 audit 側へ再分類された（逆不可逆性原理の適用）。issue #114 で `delete_asset` / `delete_assets` は dry-run 既定、確定適用時は AssetDatabase 経由 + audit-pair 必須の patch ツールとして追加された。

### components

`prefab_sentinel/mcp_tools_components.py` + `prefab_sentinel/mcp_tools_components_copy.py`。GameObject へのコンポーネント追加・削除・フィールドコピー。

| ツール | 区分 | 簡潔説明 | 関連 issue | 種別 |
|--------|------|----------|-----------|------|
| `add_component` | components | シンボルパスで指定した GameObject にコンポーネントを追加（dry-run/confirm ゲート付き） | — | write |
| `remove_component` | components | シンボルパスで指定したコンポーネントを削除（dry-run/confirm ゲート付き） | — | write |
| `copy_component_fields` | components | 同一型コンポーネント間でシリアライズフィールド値をコピー（cross-asset / same-asset） | — | write |

### patch

`prefab_sentinel/mcp_tools_patch.py`。マテリアル / アセットの直接編集と、パッチ計画（`patch_apply`）の検証・適用。

| ツール | 区分 | 簡潔説明 | 関連 issue | 種別 |
|--------|------|----------|-----------|------|
| `set_material_property` | patch | `.mat` ファイルのプロパティをオフライン YAML 編集 | — | write |
| `copy_asset` | patch | アセットファイルをコピーし `m_Name` と `.meta` を自動同期 | — | write |
| `rename_asset` | patch | アセットファイルをリネームし `m_Name` と `.meta` を追従 | — | write |
| `delete_asset` | patch | 1 件の project asset 削除を dry-run で影響確認し、`confirm` + `change_reason` で Unity AssetDatabase 経由に適用 | #114 | write |
| `delete_assets` | patch | 複数 project asset 削除を一括 dry-run / AssetDatabase 確定適用し、削除後 broken-reference delta を返す | #114 | write |
| `patch_apply` | patch | パッチ計画（v2 スキーマ）の dry-run / confirm 適用。`change_reason` + `out_report` 必須 | — | write |
| `revert_overrides` | patch | Variant から指定 propertyPath の override を YAML レベルで削除 | — | write |

### set_property

`prefab_sentinel/mcp_tools_set_property.py`。シンボルパス + コンポーネント型名による狙い撃ちのフィールド編集。

| ツール | 区分 | 簡潔説明 | 関連 issue | 種別 |
|--------|------|----------|-----------|------|
| `set_property` | set_property | シンボルパスでコンポーネントのフィールド値を設定。patch op は `TypeName@/hierarchy/path` selector を発行 | #37 | write |
| `set_properties` | set_property | コンポーネントを指す `symbol_path`（component 引数なし）で複数プロパティを一括設定。`change_reason` + `out_report` 必須 | #41, #109 | write |

### validation

`prefab_sentinel/mcp_tools_validation.py`。broken reference / Variant override / 配線 / ランタイムを横断する read-only 診断群。

| ツール | 区分 | 簡潔説明 | 関連 issue | 種別 |
|--------|------|----------|-----------|------|
| `find_referencing_assets` | validation | GUID / パスの参照元アセット検索 | — | read-only |
| `validate_refs` | validation | 壊れた GUID / fileID 参照のスキャン。`ignore_asset_guids` / snapshot diff / `refresh_guid_index` / diagnostics baseline 分類対応 | #100, #198, #199, #229, #237 | read-only |
| `inspect_wiring` | validation | MonoBehaviour フィールド配線の分析。pagination / `script_filter` / `summary_only` / `include_out_of_scope_diagnostics` / diagnostics baseline 分類対応 | #100, #197, #227 | read-only |
| `inspect_variant` | validation | Prefab Variant の override チェーン分析 | — | read-only |
| `diff_unity_symbols` | validation | Variant と Base の差分のみ返す | — | read-only |
| `list_serialized_fields` | validation | C# スクリプトのシリアライズ対象フィールド一覧 | — | read-only |
| `validate_field_rename` | validation | フィールドリネームの影響分析（派生クラス経由含む） | — | read-only |
| `check_field_coverage` | validation | C# フィールドと YAML propertyPath の不一致検出 | — | read-only |
| `inspect_materials` | validation | レンダラーごとのマテリアルスロット表示（override / inherited マーカー） | — | read-only |
| `inspect_material_asset` | validation | `.mat` ファイルのシェーダー・プロパティ・テクスチャ参照を構造化データで返す | — | read-only |
| `validate_structure` | validation | YAML 内部構造の検証（fileID 重複・Transform 整合） | — | read-only |
| `inspect_hierarchy` | validation | GameObject 階層ツリー表示。`expand_monobehaviour` でスクリプトクラス名展開 | #196, #238 | read-only |
| `validate_all_wiring` | validation | スコープ内の全 `.prefab` / `.unity` の null 参照を一括スキャン | — | read-only |
| `validate_runtime` | validation | `compile_only` / `editor_console_only` / `clientsim` profile で runtime 検証。既定は Play Mode に入らない `compile_only`、ClientSim は明示 profile + audit pair 必須 | #92 | read-only |

### symbols

`prefab_sentinel/mcp_tools_symbols.py`。シンボルツリーと人間可読パスでオブジェクトを引く。

| ツール | 区分 | 簡潔説明 | 関連 issue | 種別 |
|--------|------|----------|-----------|------|
| `get_unity_symbols` | symbols | アセットのシンボルツリー取得（`depth` / `detail` / `expand_nested`） | — | read-only |
| `find_unity_symbol` | symbols | 人間可読パスでオブジェクト検索（`include_fields` / `show_origin`） | — | read-only |

### session

`prefab_sentinel/mcp_tools_session.py`。プロジェクトスコープと Bridge C# の同期。

| ツール | 区分 | 簡潔説明 | 関連 issue | 種別 |
|--------|------|----------|-----------|------|
| `activate_project` | session | プロジェクトスコープ設定 + キャッシュ warm。`project_root` 明示指定可 | #244 | read-only |
| `deploy_bridge` | session | Unity プロジェクトの Bridge C# / `.asmdef` ファイルを自動更新 | — | write |
| `get_project_status` | session | セッション状態の表示（キャッシュ件数・スコープ・watcher・editor state） | #239 | read-only |

### editor_view

`prefab_sentinel/mcp_tools_editor_view.py`。Editor Bridge 経由の read 系（Scene/Game ビュー・カメラ・Console）。

> editor_* ツールの `hierarchy_path` セグメントは同名兄弟を `name#N`（0 始まりの出現順、`m_Children` 順）で一意化できる。`#N` を欠く曖昧な住所は first-sibling を勝手に選ばず `EDITOR_CTRL_HIERARCHY_PATH_AMBIGUOUS` で停止する（issue #38）。

| ツール | 区分 | 簡潔説明 | 関連 issue | 種別 |
|--------|------|----------|-----------|------|
| `editor_screenshot` | editor_view | Scene / Game ビューのスクリーンショット取得。`width` / `height` は `0`（現在 view サイズ）または `[1, 4096]` の範囲だけ受理する。renderer target capture は `angle`（`SCREENSHOT_ANGLE_PRESETS`: front/back/left/right/top/three_quarter; UI-only current_camera）を保持し、`target_mode=world_space_ui` / `projection` / `padding_ratio` で World Space UI の RectTransform bounds を SceneView に orthographic framing できる（UI angle: front/back/current_camera） | #249, #259, #84, #95 | read-only |
| `editor_force_scene_view_refresh` | editor_view | 全 SkinnedMeshRenderer の `forceMatrixRecalculationPerRender` を立てて player-loop を 1 tick 進める | #242, #268 | write |
| `editor_select` | editor_view | Hierarchy 内の GameObject を選択（Prefab Stage 対応） | — | write |
| `editor_frame` | editor_view | 選択オブジェクトを Scene ビューでフレーミング | — | write |
| `editor_get_camera` | editor_view | Scene ビューのカメラ状態取得（position / rotation / pivot / size / orthographic） | — | read-only |
| `editor_set_camera` | editor_view | Scene ビューのカメラ設定。pivot orbit / position / reset_to_defaults の 3 モード相互排他 | #112 | write |
| `editor_list_children` | editor_view | GameObject の子オブジェクト一覧（`active` / `tag` 付き） | — | read-only |
| `editor_list_materials` | editor_view | ランタイムレンダラーのマテリアルスロット一覧 | — | read-only |
| `editor_list_roots` | editor_view | 現在の Scene / Prefab Stage のルートオブジェクト一覧 | — | read-only |
| `editor_get_material_property` | editor_view | ランタイムのシェーダープロパティ値を読み取り | — | read-only |
| `editor_console` | editor_view | Unity Console ログを bridge-owned callback buffer から構造化取得。`since_sequence` / `since_request_id` / `phase_filter` / `classification_filter` / pagination 対応 | #94, #113, #117, #131, #239 | read-only |
| `editor_refresh` | editor_view | `AssetDatabase.Refresh()` をトリガーし、refresh で誘発したコンパイルを観測（compile-aware）。コンパイル無し→refresh-OK、成功→compile-success、失敗→実コンパイラ診断付き compile-failure | #70 | write |
| `editor_recompile` | editor_view | スクリプト再コンパイルを発行し `CompilationPipeline.compilationFinished` で完了を観測（同期 / ブロッキング） | #54, #118, #134, #203, #213, #235 | write |
| `editor_run_tests` | editor_view | Editor Bridge 経由で Unity 統合テストを実行 | — | read-only |

### editor_geometry

`prefab_sentinel/mcp_tools_editor_geometry.py`。Editor Bridge 経由の read-only live geometry API。ad-hoc `editor_run_script` ではなく専用 action を使う。

| ツール | 区分 | 簡潔説明 | 関連 issue | 種別 |
|--------|------|----------|-----------|------|
| `editor_get_transform` | editor_geometry | hierarchy path の local/world position、quaternion/euler rotation、scale、parent、active flags を返す | #98 | read-only |
| `editor_get_bounds` | editor_geometry | renderer / collider / RectTransform / auto / combined の world-space AABB を child-including または target-only で返す | #98 | read-only |
| `editor_measure_distance` | editor_geometry | 2 hierarchy path 間の pivot / bounds-center / nearest-AABB distance を測る | #98 | read-only |

### editor_write

`prefab_sentinel/mcp_tools_editor_write.py`。Editor Bridge 経由の write 系（Hierarchy / Component / BlendShape / Menu）。

| ツール | 区分 | 簡潔説明 | 関連 issue | 種別 |
|--------|------|----------|-----------|------|
| `editor_instantiate` | editor_write | Prefab を現在の Scene にインスタンス化 | — | write |
| `editor_set_material` | editor_write | ランタイムでレンダラーのマテリアルスロットを差し替え（Undo 対応） | — | write |
| `editor_set_material_property` | editor_write | ランタイムでシェーダープロパティを設定（型はシェーダー定義から自動判定、Undo 対応、audit pair 必須） | — | write |
| `editor_find_renderers_by_material` | editor_write | 指定マテリアルを使うレンダラーを Scene / Prefab Stage から逆引きする | — | read-only |
| `editor_rename` | editor_write | GameObject をリネーム（Undo 対応） | — | write |
| `editor_add_component` | editor_write | ランタイムで GameObject にコンポーネントを追加（UdonSharp 自動 backing 対応） | #103 | write |
| `editor_remove_component` | editor_write | ランタイムで GameObject からコンポーネントを削除（Undo 対応、同型複数時は index 必須） | — | write |
| `editor_create_udon_program_asset` | editor_write | UdonSharp Program asset（`.asset`）を新規生成。`confirm` + `change_reason` 必須 | #49 | write |
| `editor_delete` | editor_write | Hierarchy から GameObject を削除（Undo 対応） | — | write |
| `editor_get_blend_shapes` | editor_write | SkinnedMeshRenderer の BlendShape 名とウェイト一覧を取得（pagination 対応） | #241 | read-only |
| `editor_set_blend_shape` | editor_write | BlendShape ウェイトを名前で設定（Undo 対応） | — | write |
| `editor_list_menu_items` | editor_write | リフレクション経由で `[MenuItem]` エントリを一覧表示 | — | read-only |
| `editor_execute_menu_item` | editor_write | メニューアイテムをパスで実行（deny-list + implicit recompile barrier 付き）。`confirm` + `change_reason` 必須 | #49, #225, #248 | write |

### editor_ops

`prefab_sentinel/mcp_tools_editor_ops.py`。SerializedObject API 経由の compound 編集と Prefab 保存。

| ツール | 区分 | 簡潔説明 | 関連 issue | 種別 |
|--------|------|----------|-----------|------|
| `editor_set_property` | editor_ops | SerializedObject API 経由でコンポーネントのプロパティを設定（enum 名/display/index、LayerMask raw/symbolic、ObjectReference shorthand、UdonSharp / Quaternion 対応） | #101, #111 | write |
| `editor_set_properties` | editor_ops | 単一コンポーネントの複数プロパティを 1 リクエストで一括設定（Undo グループ）。各 entry は `property_name` キー + `value_present` マーカーを持つ | #41, #52 | write |
| `editor_safe_save_prefab` | editor_ops | シーン上の GameObject を Prefab / Variant として保存。`protect_components` / raw-save mode 対応 | #193, #228 | write |
| `editor_set_parent` | editor_ops | 既存 GameObject の親子関係を変更（Undo 対応） | — | write |

### editor_advanced

`prefab_sentinel/mcp_tools_editor_advanced.py`。VRC SDK アップロードと任意 reflection 呼び出し。

| ツール | 区分 | 簡潔説明 | 関連 issue | 種別 |
|--------|------|----------|-----------|------|
| `vrcsdk_upload` | editor_advanced | VRC SDK 経由でアバター / ワールドをビルド + アップロード（マルチプラットフォーム対応） | — | write |
| `editor_reflect` | editor_advanced | Editor Bridge 内で任意の reflection 呼び出しを行う dispatch ツール | — | write |

### editor_animation

`prefab_sentinel/mcp_tools_editor_animation.py`。AnimationClip の inspect / create / apply プリミティブ。

| ツール | 区分 | 簡潔説明 | 関連 issue | 種別 |
|--------|------|----------|-----------|------|
| `editor_inspect_animation_clip` | editor_animation | `.anim` clip の curves / events を構造化データで取得（read-only） | #243 | read-only |
| `editor_create_animation_clip` | editor_animation | 新規 AnimationClip を作成し AssetDatabase に書き出す | #243 | write |
| `editor_apply_animation_clip` | editor_animation | clip を AnimationMode で 1 Undo group にまとめてプレビュー適用 | #243 | write |

### editor_batch

`prefab_sentinel/mcp_tools_editor_batch.py`。複数オブジェクト・プロパティ・マテリアルを 1 Undo グループで一括処理する。

| ツール | 区分 | 簡潔説明 | 関連 issue | 種別 |
|--------|------|----------|-----------|------|
| `editor_create_empty` | editor_batch | 空の GameObject を名前 / 親 / 位置指定で作成 | — | write |
| `editor_create_primitive` | editor_batch | プリミティブ（Cube / Sphere 等）を 1 回で作成（位置 / スケール / 回転指定） | — | write |
| `editor_create_ui_element` | editor_batch | uGUI 要素を作成。`Image` / `TextMeshProUGUI` / `Button` / `Slider` / `Toggle` のみ受理 | #195 | write |
| `editor_batch_create` | editor_batch | 複数オブジェクトを 1 リクエストで一括生成（Undo グループ） | — | write |
| `editor_batch_set_property` | editor_batch | 複数プロパティを 1 リクエストで一括設定（Undo グループ） | — | write |
| `editor_batch_set_material_property` | editor_batch | 同一マテリアルの複数シェーダープロパティを 1 リクエストで一括設定（Undo グループ） | — | write |
| `editor_batch_add_component` | editor_batch | 複数オブジェクトにコンポーネントを一括追加（初期値対応） | — | write |
| `editor_batch_set_blend_shape` | editor_batch | 同一 SkinnedMeshRenderer の複数 blend shape を 1 リクエストで一括設定（Undo グループ） | #240 | write |
| `editor_open_scene` | editor_batch | シーンを開く（single / additive） | — | write |
| `editor_save_scene` | editor_batch | シーンを保存 | — | write |
| `editor_create_scene` | editor_batch | 新規空シーンを作成して保存 | — | write |

### editor_exec

`prefab_sentinel/mcp_tools_editor_exec.py`。Editor 内で C# スニペットをコンパイル・実行する。

| ツール | 区分 | 簡潔説明 | 関連 issue | 種別 |
|--------|------|----------|-----------|------|
| `editor_run_script` | editor_exec | C# スニペットを 1 ステップでコンパイル + 実行。stdout / primitive return / structured outputs / exception summary / WSL path hints を分離して返す。`confirm` + `change_reason` 必須、`compile_timeout_ms` ∈ `[1, 120000]` | #74, #93, #103, #116, #127, #226, #234 | write |
| `editor_run_script_submit` | editor_exec | 長時間スクリプト用の非同期 submit。bridge に identifier と acceptance timestamp を返す | #233 | write |
| `editor_run_script_poll` | editor_exec | submit が返した 32 文字 lower-case hex identifier で poll し結果を取り出す | #233 | read-only |

### editor_prefab_stage

`prefab_sentinel/mcp_tools_editor_prefab_stage.py`。Prefab Stage の open / close。

| ツール | 区分 | 簡潔説明 | 関連 issue | 種別 |
|--------|------|----------|-----------|------|
| `editor_open_prefab` | editor_prefab_stage | 指定 prefab を Prefab Stage で開く。以降の hierarchy-bound 書き込みは stage root を先に解決 | #236, #268 | write |
| `editor_close_prefab` | editor_prefab_stage | 現在の Prefab Stage を閉じる。`save=True` は writer audit ペア（`confirm` + `change_reason`）が必須 | #236 | write |

### editor_udonsharp

`prefab_sentinel/mcp_tools_editor_udonsharp.py`。UdonSharp 派生コンポーネントの追加・フィールド書き込み・persistent listener 配線。

| ツール | 区分 | 簡潔説明 | 関連 issue | 種別 |
|--------|------|----------|-----------|------|
| `editor_add_udonsharp_component` | editor_udonsharp | `UdonSharpBehaviour` 派生コンポーネントを upsert（追加 or 既存再利用）し初期フィールドを 1 トランザクションで適用。audit pair 必須 | #119 | write |
| `editor_set_udonsharp_field` | editor_udonsharp | `UdonSharpBehaviour` の指定フィールドを書き込み、`CopyProxyToUdon` で backing と同期。`values_json` で string/int/float/bool/VRCUrl/ObjectReference whole-array writes 対応。audit pair 必須 | #102, #119 | write |
| `editor_wire_persistent_listener` | editor_udonsharp | `UnityEventTools.AddStringPersistentListener` の高水準ラッパー（string モード、ノーオプ可）。audit pair 必須 | #119 | write |
