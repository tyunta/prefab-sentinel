# prefab-sentinel ワークフローパターン

## 基本情報

| 項目 | 値 |
|------|---|
| 対象 | prefab-sentinel MCP ツール群のワークフロー |
| version_tested | prefab-sentinel 0.5.162 |
| last_updated | 2026-05-07 |
| confidence | high |

## L1: 確定パターン

### Prefab 編集（open モード）
`validate_structure` → `patch_apply`(dry-run) → `patch_apply`(confirm) → `validate_refs` → `validate_runtime`

### Prefab 新規作成（create モード）
パッチ計画作成 → `patch_apply`(dry-run) → `patch_apply`(confirm) → `validate_structure` → `validate_refs`

### Editor リモート操作
`editor_select` → `editor_frame` で対象を表示 → `editor_screenshot` で視覚確認。スクショはトリアージの起点として使い、データソースにしない（必ず `inspect_wiring` / `validate_refs` で裏取りする）。

### 見た目の反復調整
`editor_set_material` でスロット差し替え → `editor_set_camera` でアングル調整 → `editor_screenshot` で確認 → 確定後に `revert_overrides` / `patch_apply` で永続化

### Variant の実態調査（Prefab とシーンの乖離）
Prefab ファイルが古い場合やシーン上にオーバーライドがある場合:
1. `get_unity_symbols(expand_nested=true)` で Variant チェーン全体の Nested Prefab 構成を把握
2. `editor_list_children` でシーン上の実際の子オブジェクト構成を確認
3. `editor_list_materials` でシーン上の実際のマテリアル割り当てを確認
4. `inspect_materials` / `inspect_hierarchy` はオフライン解析のため、シーン実態と異なる場合がある

### 壊れた参照の修復
`validate_refs` → `find_referencing_assets` → `ignore_asset_guids` で偽陽性を除外 → safe_fix / decision_required

### オーバーライドの削除
`revert_overrides` → dry-run → confirm のゲート付き。Variant の特定の Modification 行を YAML から除去

## L2: 検査ワークフロー

### フィールド配線検査
`inspect_wiring` → null参照・fileID不整合の特定。重複は same-component（WARNING）/ cross-component（INFO）に分類。Variant ファイルではベース Prefab のコンポーネントを自動解析

### 階層構造の確認
`inspect_hierarchy` → ツリー構造・コンポーネント配置の把握。Variant ファイルではベース Prefab の階層を表示し、オーバーライド付きノードに `[overridden: N]` マーカーを付与

### マテリアル構成の確認
`inspect_materials` → Renderer ごとのマテリアルスロット一覧。Variant チェーンを考慮し、各スロットが `overridden` / `inherited` かを表示

### 内部構造の検証
`validate_structure` → fileID重複・Transform整合性・参照欠損の検出

### ランタイムエラー調査
`validate_runtime` → ログ分類 → アセット特定 → 修正提案

### ランタイム階層確認
`editor_list_children` でシーン実行中の子オブジェクト一覧を取得。`inspect_hierarchy` はファイルベースだが、こちらは Prefab Instance 内のネスト構造も表示

### ランタイムマテリアル確認
`editor_list_materials` で Unity API から直接 Renderer のマテリアルスロット一覧を取得。`inspect_materials` がオフラインで Variant/FBX チェーンを解決できない場合の代替

### Console ログ取得
`editor_console` でリアルタイムログをテキスト取得。batchmode 後の Editor.log 読みではなく、Editor 起動中のバッファからの取得

### ユーザー駆動コンパイル環境での compile 観察 (issue #200)
ユーザーが Editor を直接操作している環境では `editor_recompile_and_wait` のような同期再コンパイル API を呼ばず、`editor_console(log_type_filter="error")` を直接読むこと。理由は:

- `editor_recompile_and_wait` は `RequestScriptCompilation` を起動するが、ユーザー側の Editor が既に compile 中だと衝突して二重 reload を招く
- ユーザーが `Cmd/Ctrl+R` で手動 reimport している最中の場合、bridge がさらに reload を要求するとセッション state が壊れる
- `editor_console(log_type_filter="error")` は read-only で副作用ゼロ。compile が回っているかどうか、エラーがあるかどうかをそのまま観察できる

専用の compile-status MCP API は **導入しない**。`editor_console(log_type_filter="error")` のフィルタで観察に十分対応できる範囲を、専用 API で囲ってしまうと監視粒度が固定化されてしまう。

### シーン一括構築（v0.5.110+）
`editor_batch_create` で構造物を一括生成 → `editor_add_component` でコンポーネント追加 → `editor_batch_set_property` で配線 → `editor_save_scene` で保存。batch 操作は Undo グループ化されるため、Ctrl+Z 1回で全て元に戻せる

### スクリプト開発 → シーン配線
C# ファイルを Write → `editor_recompile` → `editor_console(error)` でコンパイル確認 → `editor_create_udon_program_asset` でアセット作成 → シーン構築 → 配線。UdonSharp 非対応構文（`CompareTag` 等）は `editor_console` で即検出可能

### C# Editor Script + Bridge パターン（複雑な Prefab 組み立て）
`add_component` が open モードで使えない制約の回避策として確立。Animator Controller（多数のステート・遷移）や MA コンポーネント（ネストされたシリアライズフィールド）など、patch_apply では扱いにくいアセットの生成に最適:
1. `[MenuItem]` 属性付きの C# エディタスクリプトを Write
2. `editor_recompile` でコンパイル
3. `editor_console(log_type_filter="error", since_seconds=15)` でコンパイルエラーチェック
4. `editor_execute_menu_item("Tools/...")` でジェネレータ実行
5. `editor_console(since_seconds=10)` で成功ログ確認
6. `inspect_hierarchy` + `validate_refs` + `validate_structure` で検証

スクリプトを冪等に設計すること（既存コンポーネント削除 → 再生成）で、仕様変更時の再実行が安全になる。実績: AnimatorController（6ステート・30+遷移）+ MA コンポーネント群 + AudioSource の組み立てを3回反復実行、全て成功

### シーンの新規作成
`editor_open_scene` で既存シーンを開くか、既存 .unity ファイルをコピーして `editor_refresh` → `editor_delete` で不要オブジェクト除去。`File/New Scene` は `editor_execute_menu_item` の危険パスで拒否される

### Editor menu helper パターン（run-script の代替）
`editor_run_script` が `EDITOR_CTRL_RUN_SCRIPT_COMPILE` で詰まったとき、temp script に頼らず `[MenuItem]` 属性付きの永続ヘルパースクリプトを `Assets/Editor/_*.cs` に配置し、`editor_execute_menu_item("Tools/...")` で実行する経路。bridge state に左右されないため、コンパイル待ちの永続スタックを跨いで安定して動作する。冪等な MenuItem として書くことで `editor_execute_menu_item` から繰り返し呼べる。

外部エディタで `Assets/Editor/*.cs` を編集した直後に上記ヘルパーを呼ぶ場合は、`editor_recompile(force_reimport=True)` で `ImportAssetOptions.ForceUpdate | ForceSynchronousImport` を強制し、ファイル更新が AssetDatabase に確実に反映されてから再コンパイルさせる。

## Unity UI propertyPath リファレンス

Inspector の表示名と SerializedProperty の `propertyPath` が食い違う Unity UI のフィールド早見表。`editor_set_property` / `editor_batch_set_property` で `Property not found` が返ったときの最初の参照点。

| コンポーネント | Inspector 表示 | 実際の propertyPath |
|----------------|----------------|---------------------|
| `Text`         | Font Size      | `Text.m_FontData.m_FontSize` |
| `Text`         | Alignment      | `Text.m_FontData.m_Alignment` |
| `Text`         | Line Spacing   | `Text.m_FontData.m_LineSpacing` |
| `Text`         | Best Fit       | `Text.m_FontData.m_BestFit` |
| `RectTransform`| Anchored Pos X | `RectTransform.m_AnchoredPosition.x` |
| `RectTransform`| Width / Height | `RectTransform.m_SizeDelta.x` / `RectTransform.m_SizeDelta.y` |
| `Image`        | Source Image   | `Image.m_Sprite` |
| `TextMeshProUGUI` | Horizontal Alignment | `TextMeshProUGUI.m_HorizontalAlignment` |
| `TextMeshProUGUI` | Vertical Alignment   | `TextMeshProUGUI.m_VerticalAlignment` |

`Text` 系は `m_FontData` ネスト構造越しに到達する点が特徴。Unity UI の Inspector が独自カスタムエディタを当てているため、表示名と直接シリアライズパスが一致しない。

## 判断ルール

- `safe_fix`: 一意で決定的な修正のみ自動適用可
- `decision_required`: ユーザー合意まで保留
- `error` / `critical` が出たら停止し、修正または判断待ちへ回す
- Unity 環境がない場合は dry-run / 検査までで停止
- `patch_apply` の confirm モードでは `change_reason` を必須とする（監査ログ）

## 実運用で学んだこと

- `editor_batch_create` は1回で22オブジェクトまで問題なく動作確認済み（Undo グループ化あり）
- `editor_batch_set_property` は1回で29プロパティまで動作確認済み
- 配列型プロパティ（`Array.size`, `Array.data[N]`）は `editor_set_property` で設定可能（v0.5.149 で確認）。`editor_set_component_fields` では不可。手順: まず `Array.size` を設定、次に `Array.data[0]`, `Array.data[1]`, ... を個別に設定。DoneruWorldDisplay で Text[] を5要素配線して実証済み
- `editor_set_parent` 実行後はオブジェクトのパスが変わる（例: `/Wall_N` → `/Lobby/Wall_N`）。並列呼び出しでパスを参照する場合は移動後のパスを使うこと
- Bridge C# ファイルを Unity プロジェクトにコピーした後、`ResolveComponentType` の `System.ReflectionTypeLoadException` が Unity のデフォルトアセンブリでコンパイルエラーになる。`System.Reflection.ReflectionTypeLoadException` に手動修正が必要（v0.5.110 時点）
- `BridgeVersion` 定数（C# 側）が実際の Plugin バージョンと同期しておらず `"0.5.82"` のままハードコードされている
- Bridge ファイルを手動更新する場合、旧ファイルの位置（`Assets/Editor/` 直下）と新ファイルの位置（`Assets/Editor/PrefabSentinel/`）が異なると CS0101 重複定義エラーになる。旧ファイルを完全に削除してからコピーすること
- `VRCSDKUploadHandler.cs` はプロジェクトの VRC SDK バージョンや対象（Avatar/World）によってコンパイルエラーを起こす。不要なら配置しない
- シーン構築では batch_create → add_component → batch_set_property の3ステップが最も効率的。推定400+ → 実測65回に削減（約80%減）
- WSL 環境では `UNITYTOOL_BRIDGE_WATCH_DIR` に WSL パス（`/mnt/d/...`）を使うこと。Windows パス（`D:/...`）は watch_dir の存在チェックで失敗する（wslpath 自動変換が効かないケースがある）
- `.claude/settings.json` の `env` セクションで設定した環境変数は、MCP サーバーに伝播しない場合がある。確実に伝播させるには、シェルプロファイル（`.bashrc` 等）に記載して Claude Code 起動前に export すること
- `inspect_hierarchy` は FBX ベースの Prefab / Variant を解析できない（`unreadable_file` 診断）。FBX 由来の階層確認には `editor_list_children` を使うこと
- `inspect_wiring` の null 参照レポートには、MA コンポーネントの設計上 null が正常な optional フィールド（`menuSource_otherObjectChildren`, `menuToAppend`, `installTargetMenu`）が含まれる。これらは偽陽性として無視してよい
- `editor_execute_menu_item` + `editor_console` の組み合わせは、C# スクリプト実行のフィードバックループとして非常に効果的。1サイクル（recompile → execute → console確認）が数秒で完了する
- `deploy_bridge` 後の `activate_project` で `bridge.connected: false` と表示されても、env 変数が正しく設定されていれば `editor_*` 呼び出し時に接続が確立される。`activate_project` の bridge 状態はキャッシュされた情報であり、リアルタイムの接続状態を反映しない
- TMPro コンポーネントは `TMPro.TextMeshProUGUI`（完全修飾名）で `editor_add_component` に渡す。`properties` で `fontSize`, `fontStyle`, `text` を初期値設定可能
- TMPro のテキスト配置は `m_HorizontalAlignment`（Left=1, Center=2, Right=4）と `m_VerticalAlignment`（Top=256, Middle=512, Bottom=1024）で設定する
- `editor_batch_set_property` で RectTransform の `m_SizeDelta.x/y`, `m_AnchoredPosition.x/y`, `m_LocalScale.x/y/z` を一括設定可能。1 リクエストで 20 プロパティ設定を確認済み（DoneruWorldDisplay の HistoryText 5 要素 x 4 フィールド）
- World Space Canvas のセットアップ: `m_SizeDelta` で Canvas 座標系のサイズ（例: 800x500）を設定し、`m_LocalScale` で世界座標サイズに変換（例: 0.001 = 0.8m x 0.5m）。子要素は `m_AnchoredPosition` + `m_SizeDelta` で配置
- String プロパティに空文字列 `""` は設定不可（API 仕様）。クリアにはスペース `" "` で代用する
- **`editor_run_script` の bridge state stuck (2026-04-30)**: 数回呼び出した後、`EDITOR_CTRL_RUN_SCRIPT_COMPILE` "Script compilation is still in progress" を **永続的に** 返す症状あり。実際には Unity 側のコンパイルは settled。`editor_recompile` / `editor_refresh` でも復旧せず。**回避策: Editor menu helper パターン** — `Assets/Editor/_*.cs` に `[MenuItem]` 付き静的クラスを永続ファイルとして配置し `editor_execute_menu_item` で実行する。temp script の自動 cleanup は失われるが、bridge state に左右されず複雑なロジック (UnityEvent 永続リスナー追加・SetActive・Layer 設定等) を実行できる。今回 NadeVision マルチユーザ同期の wiring helper として実用化
- **`component_type: "GameObject"` は受け付けない (2026-04-30)**: `editor_set_property` / `editor_batch_set_property` で `component_type` に `"GameObject"` を渡すと `System.NotSupportedException: The invoked member is not supported in a dynamic module` が `ResolveComponentType` で発生する。GameObject の `m_IsActive` / `m_Layer` 等は MCP から直接設定不可。Editor menu helper で `gameObject.SetActive(false)` / `gameObject.layer = N` を実行する
- **`:ComponentType` サフィックスの推奨 (再確認)**: stripped prefab instance を含む scene では type 曖昧性回避のため `:` サフィックス必須。サフィックスなしだと `GameObject 'X' has no Y component` で誤判定が起きる。例: `/NadeVision/VVMW (On-Screen Controls):Core` (nested prefab 内の Core を解決)
- **UI Text の font size プロパティパス (2026-04-30)**: Unity UI の `Text` コンポーネントの font size は `m_FontSize` ではなく `m_FontData.m_FontSize` (nested struct)。`Property not found: m_FontSize on Text` エラーが出たらこちらを試す。同様に `m_Alignment` は `m_FontData.m_Alignment` の場合あり
- **Edit 後の recompile pickup の不安定さ (2026-04-30)**: `Edit` ツールで `Assets/Editor/*.cs` を更新後、`editor_refresh` + `editor_recompile` を呼んでも次の `editor_execute_menu_item` で **古いコンパイル結果** で動作するケースあり (stack trace の行番号が古い版のまま)。`touch` だけでは不十分。回避策: 先頭にコメントを追加するなど substantive content change を加える、または完全 rewrite で timestamp を確実に更新する
- `editor_set_component_fields` の `object_reference` で `::ComponentType` サフィックスは UdonSharp コンポーネントに対して機能しない。パスのみ（`/Path/To/Object`）を使う
- **`inspect_wiring` のページネーション契約 (2026-05-07, issue #197)**: Nested package prefab を多数含む対象（NadeVision.prefab + VVMW package など）を呼ぶと MCP token cap を超える（実測 65,859 chars）。`cursor` は `pos:<offset>` 形式の不透明 continuation token、`page_size` は `[1, 500]` の inclusive bounds（既定 50）。`data.component_count` は常に総件数で、`data.components` は当ページのスライス。`data.next_cursor` が空文字のとき exhausted。`null_reference_count` 等の diagnostic counts はページ非依存（全ページで同じ値）。`Phase1Orchestrator.validate_all_wiring`（aggregator）は `page_size=500` で呼ぶので、aggregate scan は 1 ページに収まる前提
- **`editor_recompile_and_wait` の三分岐 (2026-05-07, issue #203)**: ソース無変更で呼ぶと固定タイムアウトしていた問題を解消。`CompilationPipeline.compilationFinished` イベント駆動で 3 つの結果を返す: 全アセンブリ `assemblyCompilationNotRequired` → `EDITOR_CTRL_RECOMPILE_AND_WAIT_NOOP`（同期 success、SessionState 永続化なし）/ `assemblyCompilationFinished` で `CompilerMessageType.Error` のメッセージ 1 件以上 → `EDITOR_CTRL_RECOMPILE_FAILED`（`data.errors` にメッセージ列）/ 1 件以上のアセンブリが実コンパイル → domain reload 後の `AssemblyReloadCount` 増加で `EDITOR_CTRL_RECOMPILE_AND_WAIT_OK`。mtime polling は完全廃止 — Unity が `assemblyCompilationNotRequired` を返すケースで mtime が進まないため絶対に発火しない仕様だった
- **`editor_create_ui_element` の使い分け (2026-05-07, issue #195)**: `editor_create_primitive` は `GameObject.CreatePrimitive(PrimitiveType.X)` のラッパなので Cube/Sphere/Cylinder/Capsule/Plane/Quad の 6 値のみ。uGUI 要素（Image/TextMeshProUGUI/Button/Slider/Toggle）は `editor_create_ui_element` を使う。`rect={"anchorMin": [...], "anchorMax": [...], "sizeDelta": [...]}` で RectTransform を第一級指定、`properties={"color": [r,g,b,a], "font": "<asset path>"}` で graphic を設定。TMP の `font` を省略すると `Assets/TextMesh Pro/Resources/Fonts & Materials/LiberationSans SDF.asset` を自動代入（fontSize 体感サイズの再現に必要、§3 trap 回避）
