---
name: guide
description: >-
  Unity Prefab / Scene / Asset の壊れた参照・Variant override・配線ミスを構造化応答で診断し、
  dry-run → confirm 経路で安全に修復するための MCP ツールリファレンス。
  Editor Bridge のセットアップ手順もここに含む。
  トリガー: .prefab, .unity, .asset, .mat, Prefab, Variant, 配線, 参照, 壊れた参照,
  broken reference, GUID, fileID, override, パッチ, patch plan, validate refs,
  validate structure, where-used, inspect variant, inspect wiring,
  inspect hierarchy, wiring, null参照, null reference, MonoBehaviour,
  階層, hierarchy, Transform整合, 孤立, orphan, 重複fileID, duplicate fileID,
  Editor Bridge, Bridge セットアップ, bridge setup, deploy_bridge,
  UNITYTOOL_BRIDGE_WATCH_DIR, watch ディレクトリ
  のいずれかに該当する作業で使用する。
---

Unity プロジェクトで Prefab / Scene / Asset の検査・編集・参照修復を行う MCP ツールリファレンス。

MCP ツールとして呼び出す（CLI は v0.4.0 で廃止済み）。Plugin 導入時はホスト（Claude Code / Codex CLI）が MCP サーバーを自動起動するため、このスキルが使える時点でツールは接続済み。

書き込みの実適用・実行検証・`editor_*` ツールを使うには Editor Bridge のセットアップが必要 → [Unity Bridge セットアップ](#unity-bridge-セットアップ)。read-only 検査・dry-run だけならセットアップ不要。

## 30 秒で動く 3 つの例

最短経路。`UNITYTOOL_BRIDGE_WATCH_DIR` 等の Editor Bridge 環境変数は read-only 経路では不要（broken-reference 検出と Variant 差分は YAML 直読みで完結する）。`activate_project` はセッションで最初に 1 回呼び、`scope`（必須）と Unity プロジェクトルート（`project_root`、任意）を宣言する。

**(1) 壊れた参照を検出する** — `validate_refs` で broken GUID / fileID を構造化応答で取り出す。

```text
activate_project(scope="Assets/<feature>", project_root="/path/to/unity/project")
validate_refs(scope="Assets/<feature>")
```

返ってきた envelope の `data.unique_missing_asset_guids` が空配列なら OK、要素があれば未解決 GUID の集合（重複排除済み）。`data.broken_occurrences` に発生回数。

**(2) Variant の override を可視化する** — `inspect_variant` で base からの差分のみを取り出す。

```text
inspect_variant(asset_path="Assets/<feature>/Variants/Foo.prefab", show_origin=True)
```

各 override に `origin_path` / `origin_depth` が付き、どの prefab（Base / Mid / Leaf）が値を確定したかを追える。

**(3) 編集を dry-run でプレビューする** — `set_property` の `confirm=False` で差分だけ取り出す（書き込まない）。

```text
set_property(
  asset_path="Assets/<feature>/Foo.prefab",
  symbol_path="CharacterBody/MeshRenderer",
  property_path="m_StaticBatchInfo.firstSubMesh",
  value=2,
  confirm=False,
)
```

`symbol_path` は GameObject パス + 末尾コンポーネント名でコンポーネント自体を指す（`asset_path` と `symbol_path` は常に別引数）。`severity` と `data.diff` を読んで意図通りなら `confirm=True` + 非空 `change_reason` で再呼び出し。`change_reason` を欠くと `CHANGE_REASON_REQUIRED` で拒否される。

## 最初に困ったらこの 3 ツール

どこから手をつけるか迷ったらここから。各ツールは read-only で副作用なし。

- **`validate_refs`** — broken GUID / fileID を全件検出する起点。`scope` 引数でスコープを宣言する。`<scope>/config/ignore_guids.txt` があれば auto-load される。
- **`inspect_wiring`** — MonoBehaviour フィールドの配線（null / fileID 不整合 / 重複参照）を分析する。`script_filter` で特定の script class に絞れる。
- **`inspect_hierarchy`** — GameObject の階層ツリーをコンポーネント注釈付きで読む。`expand_monobehaviour=true` でスクリプトクラス名を展開する。

書き込み系（`set_property` / `patch_apply` / `add_component` 等）はこの 3 つで現状把握してから着手する。

## MCP ツールのカテゴリ

全 MCP ツールの正本カタログは [docs/tools.md](../../docs/tools.md)、応答エンベロープ（`success / severity / code / message / data / diagnostics`）とエラーコードの正本は [docs/api-reference.md](../../docs/api-reference.md)。各ツールの引数・戻り値は MCP ツールスキーマに記述されている。主なカテゴリ:

- **検査（read-only・Unity 不要）** — `validate_refs` / `validate_structure` / `inspect_wiring` / `inspect_variant` / `inspect_hierarchy` / `inspect_materials` / `find_referencing_assets` / `get_unity_symbols` / `find_unity_symbol` / `diff_unity_symbols` / `list_serialized_fields` / `validate_field_rename` / `check_field_coverage`
- **編集（write・dry-run/confirm ゲート）** — `set_property` / `add_component` / `remove_component` / `patch_apply` / `revert_overrides` / `set_material_property` / `copy_asset` / `rename_asset`
- **実行検証** — `validate_runtime`（UdonSharp compile + ClientSim、Editor Bridge 常駐が必須）
- **Editor 操作（Editor Bridge 常駐が必須）** — `editor_*` 系。Scene / Hierarchy / Component / Material / BlendShape / Animation / Console / Prefab Stage の検査・編集（`editor_screenshot` / `editor_open_prefab` / `editor_close_prefab` / `editor_safe_save_prefab` / `editor_execute_menu_item` / `editor_recompile_and_wait` 等）。完全な一覧は docs/tools.md の `editor_*` カテゴリ群を参照
- **セッション** — `activate_project`（スコープ宣言 + キャッシュ warm）/ `get_project_status` / `deploy_bridge`（Bridge C# / `.asmdef` を Unity プロジェクトへ同期）

書き込み系ツールは `confirm=True` + 非空 `change_reason` の監査ペアを必須とする（`patch_apply` / `set_component_fields` は `out_report` も必須）。

## パッチ計画 JSON

複数操作をまとめて適用するときは `patch_apply` に plan JSON を渡す。`plan_version: 2` が唯一受け入れられる形状（旧 `{"target": ..., "ops": [...]}` 形状は `ValueError` で即時拒否される）。

- `resources[]` — 編集対象。`kind`（`prefab` / `scene` / `asset` / `material` / `json`）と `mode`（`open` = 既存編集 / `create` = 新規作成）。
- `ops[]` — 操作列。`open` は既存プロパティの値変更・配列操作のみ。`create` は階層・コンポーネントのゼロ作成（`result` ハンドルで作成物に名前を付け後続 op から参照、ルートは自動で `"root"` ハンドル）。
- `postconditions[]` — 適用後の検証条件。

最小例（open mode のプロパティ編集）:

```json
{
  "plan_version": 2,
  "resources": [
    {"id": "res1", "kind": "prefab", "path": "Assets/.../Target.prefab", "mode": "open"}
  ],
  "ops": [
    {"resource": "res1", "op": "set", "component": "SkinnedMeshRenderer", "path": "m_Enabled", "value": true}
  ],
  "postconditions": []
}
```

完全なスキーマ（op 種別一覧、create / scene モード、配列操作、複数リソース、annotated examples）は [docs/execution-reference.md](../../docs/execution-reference.md) の「`patch_apply` 入力スキーマ」節を参照。実例集は `knowledge/prefab-sentinel-patch-patterns.md`。

### パッチ計画の要点

- `open` モードの `component` は **型名セレクタ**で指定する（例: `SkinnedMeshRenderer`、`UnityEngine.MeshFilter`）。同型が複数あるときは `TypeName@/hierarchy/path` で曖昧性を解消する。YAML の数値 fileID はセレクタに使えない（C# ブリッジは型名で検索する）。
- `add_component` は **create モード専用**。既存 Prefab へコンポーネントを追加する bridge 操作は無く、open モードで使うと `create-mode operation` エラーになる。既存 Prefab への追加は YAML 直接編集で行う。
- 配列操作（`insert_array_element` / `remove_array_element`）の `path` は `m_Array.Array.data` のように `.Array.data` で終わる必要がある（Unity SerializedProperty の規約）。
- `ObjectReference` の `value` — open モードは `{"guid": "...", "fileID": ...}` 形式または null、create モードは `{"handle": "..."}` でハンドル参照。create モードでハンドル文字列を裸で渡すとランタイムエラーになる。
- Unity 組み込みリソースの GUID: `0000000000000000e000000000000000`（`unity default resources` — メッシュ Sphere / Cube 等）、`0000000000000000f000000000000000`（`unity_builtin_extra` — マテリアル・シェーダ）。fileID⇔名前の解決は `prefab_sentinel/builtin_assets.py`。
- UdonSharp コンポーネントは 2 つの MonoBehaviour（`UdonSharpBehaviour` + backing `UdonBehaviour`、後者は `m_Script` が固定 GUID `45115577ef41a5b4ca741ed302693907`）で構成され、片方だけでは "Selected U# behaviour is not setup" になる。create モードの `add_component` で `UdonSharpBehaviour` 派生型を指定すると backing が自動生成・自動配線される。

## ワークフロー・実践パターン

詳細は knowledge ファイルを参照:

- `knowledge/prefab-sentinel-workflow-patterns.md` — ワークフロー選択、判断ルール
- `knowledge/prefab-sentinel-variant-patterns.md` — Variant 操作、VRChat 着せ替え
- `knowledge/prefab-sentinel-editor-camera.md` — カメラ操作パターン
- `knowledge/prefab-sentinel-material-operations.md` — マテリアル操作パターン
- `knowledge/prefab-sentinel-wiring-triage.md` — 配線検査の読み方
- `knowledge/prefab-sentinel-patch-patterns.md` — パッチ計画の実例集

## VRChat エコシステムナレッジ

`knowledge/` には ModularAvatar / liltoon / VRCFury / AvatarOptimizer など主要な VRChat エコシステムツールのドメインナレッジが同梱されている（L1 概念 / L2 操作パターン / L3 SerializedProperty の 3 レベル構成）。

これらのツールが関わる Prefab / Scene / Material を検査・編集するときは、**判断の前に対応する `knowledge/*.md` を読む**。SerializedProperty 名やコンポーネントの不変条件はツールごとに異なり、読まずに編集するとそのツールの作法を外した変更になる。

対象ファイルは `knowledge/` を Glob し、コンポーネント型名・シェーダー名・パッケージ名から特定する。VRChat SDK / UdonSharp 自体のナレッジも `knowledge/` に含まれる。

個別のファイル名はここに列挙しない — `knowledge/` ディレクトリの内容そのものが単一の正本で、新しいツールのナレッジは knowledge-acquisition スキルが随時追加する。このスキルに一覧を持たせると drift するため、必ず Glob で実体を見る。

## 関連スキル（ワークフロー自動化）

本 guide は MCP ツールリファレンスと Editor Bridge セットアップ手順。以下のスキルは複数ツールを組み合わせたゲート付きワークフローを提供する。

| スキル | 用途 | トリガー |
|--------|------|---------|
| `/prefab-sentinel:variant-safe-edit` | Prefab/Scene/Asset の安全な編集。preflight → dry-run → confirm → validate のフルパイプライン | パッチ適用、Prefab 編集全般 |
| `/prefab-sentinel:prefab-reference-repair` | 壊れた参照の検出・修復。ignore-guid ポリシーと decision_required ゲート付き | `validate_refs` で壊れた GUID/fileID が検出されたとき |
| `/prefab-sentinel:udon-log-triage` | ランタイム例外・Udon/ClientSim ログエラーの分類→アセット特定→修正提案 | ランタイム例外やログベースのリグレッション発生時 |

## Unity Bridge セットアップ

read-only 検査（`validate_*` / `inspect_*` / `find_*`、および patch の dry-run）は Unity も Bridge も不要 — このスキル冒頭の例だけで動く。**書き込みの実適用（`confirm=True`）・`validate_runtime`・`editor_*` ツールを使うときだけ** Editor Bridge をセットアップする。

### セットアップ runbook

ユーザーが「Bridge セットアップして」と依頼したら、AI 側のツール呼び出しとユーザーの Unity 側手動操作を分けて、次を順に進める。

1. **プロジェクト確認 + Bridge C# 配置（AI）** — ユーザーに Unity プロジェクトルートを確認し、`activate_project` でスコープを宣言してから `deploy_bridge` を呼ぶ。

   ```text
   activate_project(scope="Assets/<feature>", project_root="/path/to/unity/project")
   deploy_bridge()
   ```

   - `deploy_bridge` は `activate_project` が前提（プロジェクト未活性なら `DEPLOY_NO_PROJECT` で停止）。
   - `target_dir` 省略時の既定は `<project_root>/Assets/Editor/PrefabSentinel/`。明示する場合もプロジェクト内に限る（外側は `DEPLOY_OUTSIDE_PROJECT`）。
   - Bridge C#（partial 分割された全ファイル）と `.asmdef` をコピーし、配置先の親ディレクトリに残った旧 `PrefabSentinel.*.cs` を除去して CS0101 重複定義エラーを防ぐ。手動 `cp` は partial 分割ファイル群を取りこぼすため使わない。

2. **Unity でコンパイル完了を待つ（ユーザー手動）** — ユーザーに Unity Editor へ切り替えてもらい、配置された C# のコンパイル完了を待つ。**この時点では Editor Bridge がまだ起動していないため、`editor_recompile_and_wait` / `editor_console` は使えない**（chicken-and-egg）。ユーザーが Unity Console を目視し、コンパイルエラーが無いことを確認する。エラーがある場合: `CS0101`（重複定義）なら配置先や親ディレクトリに旧 Bridge ファイルが残存、`CS0246` 等の型未解決なら VRChat SDK / UdonSharp の未導入・バージョン不整合を疑う。

3. **Editor Bridge ウィンドウを開く（ユーザー手動）** — Unity Editor の `PrefabSentinel > Editor Bridge` メニューからウィンドウを開き、watch ディレクトリ（例: `<project_root>/Temp/PrefabSentinelBridge`）を入力して有効化する。

4. **`UNITYTOOL_BRIDGE_WATCH_DIR` を設定（AI）** — 手順 3 でユーザーが入力した watch ディレクトリのパスを確認し、同じ値を環境変数 `UNITYTOOL_BRIDGE_WATCH_DIR` に設定する。この env は **MCP サーバープロセスが読む**。MCP サーバーはホスト（Claude Code / Codex CLI）が起動するため、ホストが MCP サーバーを spawn する際に渡る場所 — OS のユーザー環境変数、またはホストの MCP server 設定の `env` ブロック — に置く。設定後は反映のためホスト / MCP サーバーセッションを再起動する。`UNITYTOOL_*` 環境変数の正本と Windows / WSL のパス変換は [CONFIGURATION.md](../../CONFIGURATION.md) を参照。

   依頼の仕方: ユーザーは「watch dir を `<path>` にして」と頼めばよい。AI はそのパスを確認し、env を設定し、反映には再起動が要る旨を伝える。

5. **疎通確認（AI）** — `get_project_status` を呼び、`data.editor_state` が non-null（`is_playing` 等 4 つの bool フラグが返る）なら Bridge は接続済み。`null` なら未接続 — 手順 2〜4 を見直す。最後に `validate_runtime` で実行検証経路まで通ることを確認する。

### アーキテクチャ

Python → `{uuid}.request.json` をアトミック書き込み → Editor Bridge がポーリング検出 → `UnityPatchBridge` / `UnityRuntimeValidationBridge` / `UnityEditorControlBridge` が処理 → `{uuid}.response.json` をアトミック書き込み → Python がポーリング読み取り。Editor を閉じずに、ロード済みアセットキャッシュを跨いだ操作ができる。
