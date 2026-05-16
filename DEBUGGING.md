# Debugging

何かが落ちた・期待外の応答が返った時に、Bridge エンベロープ / Unity Console / broken reference / `editor_run_script` のどこを見ればいいかを 1 箇所にまとめる。エンベロープ形状の正本は [docs/api-reference.md「レスポンスフォーマット」](./docs/api-reference.md#レスポンスフォーマット)、エラーコードのカタログは [docs/api-reference.md「エラーコード規約」](./docs/api-reference.md#エラーコード規約)（本ファイルでは再掲しない）。

## Bridge ログの読み方

操作系・検証系・orchestrator 系 MCP ツールは標準エンベロープを返す。本節は「最初に見るべきフィールド」を読み解きの順で示す（フィールド意味の正本は [docs/api-reference.md「レスポンスフォーマット」](./docs/api-reference.md#レスポンスフォーマット)）。

応答エンベロープの 6 フィールド:

- `success` — `bool`。`True` は意図した操作が完了したこと、`False` は契約違反 / 入力不正 / 実行時失敗。`severity` と独立に判定する（`severity="warning"` でも `success=True` はあり得る）。
- `severity` — `info` / `warning` / `error` / `critical` のいずれか。最大ランクは同梱 `diagnostics` の最大重要度（情報レベルが floor）として算出される（issue #244）。
- `code` — ツール固有の不変識別子（例: `SER001` / `REF001` / `EDITOR_CTRL_RUN_SCRIPT_OK`）。code は安定した contract で、message は可変。CI / 自動化は code で分岐する。
- `message` — 人間向けの 1 行説明。code が `EDITOR_RUN_SCRIPT_TRANSPORT_TIMEOUT` のような複合状態を表す場合、message には供給値や境界値が埋め込まれる。
- `data` — ツール固有の構造化ペイロード（例: `data.suggestions` / `data.editor_state` / `data.next_cursor`）。code 分岐後に取り出す。
- `diagnostics` — 配列。新規 emitter（`mcp_tools_validation.py:127` 等）は `{severity, code, message, data}` の 4 キー dict を直接 append する（例: `IGNORE_GUIDS_FILE_LOADED`）。一部のレガシー orchestrator 経路は `prefab_sentinel.contracts.Diagnostic` dataclass を `asdict()` 経由で emit するため、wire 上の field 名が異なるエントリも残存する（例: `STALE_GUID_INDEX_HINT` は本節 5 で参照しているとおり `code` ではなく `detail` キーで識別する。新規 contract は 4 キー dict 側 — [docs/api-reference.md「レスポンスフォーマット」末尾の注記](./docs/api-reference.md#レスポンスフォーマット) を参照）。`severity` はトップレベル `severity` の floor を引き上げる入力で、各エントリ自身も独立に warning / info 情報を運ぶ。

severity 境界の使い分け（`critical` vs `error`）と全エラーコードの意味は [docs/api-reference.md「エラーコード規約」](./docs/api-reference.md#エラーコード規約) を参照。本ファイルは catalog を持たない。

参照系ツール（`get_unity_symbols` / `find_unity_symbol` / `find_referencing_assets`）はエンベロープを返さず、`{matches: [...], target, metadata}` 等のペイロードを直接返す。空配列は「該当なし」を意味し、エラーではない（インフラエラーのみ MCP `ToolError` で伝播する）。

## Unity Console capture

Unity Editor で発生したログを Bridge 経由で取得する `editor_console` の使い方。`phase_filter` と `classification_filter` を組み合わせて、ノイズを早めに落とす。

- `phase_filter` の受理値は 4 つ: `all`（既定）/ `edit` / `play` / `build`。Bridge 境界で buffer に触れる前に拒否され、範囲外は `EDITOR_CTRL_INVALID_PHASE_FILTER`（issue #239）。
- `classification_filter` の受理値は 3 つ: `all`（既定）/ `non_fatal`（non-fatal pattern table にマッチしたもののみ）/ `fatal`（マッチしないもののみ）。範囲外は `EDITOR_CTRL_INVALID_CLASSIFICATION_FILTER`（issue #117）。
- `since_seconds` の既定値は `60.0` 秒（直近 60 秒。`0.0` で時間フィルタなし）。`order` の既定値は `"newest_first"`（issue #113、breaking）。
- `cursor` は不透明な継続トークン（`""` で先頭ページ、以降は前回応答の `data.next_cursor` をそのまま渡す）。`seq:<long>` 形式に合致しない値 / 取り込み済み範囲外は `EDITOR_CTRL_INVALID_CURSOR`。`order` を切り替える場合は `cursor` をリセットする。
- `max_entries` の許容範囲は `[1, ConsoleLogBuffer.DefaultCapacity]`（既定 1000、両端含む）。範囲外は Python 側が `MAX_ENTRIES_OUT_OF_RANGE`、C# 側が `EDITOR_CTRL_MAX_ENTRIES_OUT_OF_RANGE`（issue #131）。
- 末尾まで到達すると `data.next_cursor` は空文字。

調査時の典型フロー:

1. `editor_console(phase_filter="play", classification_filter="fatal", since_seconds=120)` で Play 中の fatal だけを直近 2 分から拾う。
2. 各エントリの stack trace と message を確認し、`classify_errors` または `validate_runtime` のレスポンスにマッチさせる。
3. ノイズが多い場合は `classification_filter="non_fatal"` でテーブル登録済みパターン（例: `udonsharp_obs_nre`）にマッチしたエントリだけを取り出し、`data.entries[].message` と `data.entries[].stack_trace` で件数とコンテキストを確認する。non-fatal pattern のラベル別集計は `editor_console` 応答には含まれない（`warnings.nonfatal_patterns` は save / instantiate 系応答でのみ populate される、`tools/unity/PrefabSentinel.UnityEditorControlBridge.SaveInstantiate.cs`）。

## broken reference 調査手順

`validate_refs` で broken PPtr / missing fileID を検出してから、`inspect_wiring` で配線元を特定し、patch plan を組み立てるまでの順序を固定する。スクショや目視ではなく必ず構造化応答を起点にする。

1. **scope を宣言する** — `validate_refs(scope="...")`。`<scope>/config/ignore_guids.txt` があれば auto-load され、寄与した場合は `IGNORE_GUIDS_FILE_LOADED` info diagnostic が返る（issue #237）。
2. **broken の分類を確認する** — `data.unique_missing_asset_guids`（重複排除済みの sorted 全集合）と `data.broken_occurrences`（発生回数）をセットで読む。Unity 組み込み GUID（`0000...e000...` / `f000...`）は既定で除外される。
3. **どこから参照されているか逆引きする** — `find_referencing_assets(asset_or_guid=<missing_guid>, scope=...)` を呼ぶ。`top_missing_breakdown=true` を `validate_refs` 側に渡すと per-source-file 占有率の内訳も得られる（issue #198）。
4. **配線文脈を確認する** — `inspect_wiring(asset=<source>, ...)` で MonoBehaviour フィールドの null reference 件数と配線状態を確認する。ページングが必要なら `cursor`（`pos:<offset>` 形式）と `page_size`（受理範囲 `[1, 500]`、既定 50）を使う。`summary_only=true` で per-component スライスと per-reference diagnostic 一覧を抑制し 4 件のカウントだけ返す軽量モードもある（issue #227）。
5. **STALE 警告に従う** — missing-asset 失敗パスで `diagnostics[].detail == "STALE_GUID_INDEX_HINT"`（issue #229）が出ていれば、cached resolver が古い可能性がある。`validate_refs(refresh_guid_index=true, ...)` で GUID index を 1 回 invalidate して再実行する。fresh meta-file scan で resolve できれば cached resolver の問題、それでも missing なら本物の broken 参照。
6. **snapshot で before/after を分離する** — ビルド前に `validate_refs(snapshot_save="<name>", scope=...)`、ビルド後に `validate_refs(snapshot_diff="<name>", scope=...)` を回すと、PR で resolve された broken と新規 introduce された broken を `data.steps[0].result.data.snapshot_diff` の `new_broken` / `resolved` / `unchanged_count` で分離できる（issue #199）。
7. **patch plan を組む** — 候補が一意なら `prefab-reference-repair` skill 経由で自動適用、複数候補なら `decision_required` でユーザー判断待ちにする。
8. **書き込みは dry-run → confirm の二段** — `patch_apply` は `confirm=True` + 非空 `change_reason` + `out_report` が必須。1 件でも missing-asset GUID が残れば `REF001` で fail-fast し、部分適用は発生しない。

ignore-guid を一時的に追加して走らせる場合は、`validate_refs(ignore_asset_guids=["<guid>"], ...)` で caller-supplied list として渡す（`<scope>/config/ignore_guids.txt` と union-dedupe される）。集計は `data.ignored_missing_asset_occurrences` / `data.top_ignored_missing_asset_guids` で確認できる。

## editor_run_script の落とし穴

`editor_run_script` は Unity Editor 内で C# スニペットを 1 ステップでコンパイル・実行する MCP ツール（issue #74）。timeout が 2 系統存在し、`compile_timeout_ms` / transport budget / Bridge 側 deadline がそれぞれ独立に効くため、応答 code を取り違えやすい。

- **`confirm` と `change_reason` は両方必須** — `confirm=True` かつ非空の `change_reason` を欠く呼び出しは Bridge に到達する前に `CHANGE_REASON_REQUIRED` で拒否される。dry-run モードは存在しない。
- **`compile_timeout_ms` の許容範囲は `[1, 120000]`（両端含む、120 秒上限）** — 範囲外を渡すと Python の入口で `COMPILE_TIMEOUT_OUT_OF_RANGE` を返し Bridge には送らない（issue #127）。`0` / 負値 / 上限超過は CLAMP しない（受信側で値を勝手に補正しない設計）。
- **temp ファイルのライフサイクル** — Bridge は `Assets/Editor/_PrefabSentinelTemp/<temp_id>.cs` にソースを書き出し、`AssetDatabase.Refresh()` 後に `PrefabSentinelTempScript.Run()`（`public static void`、固定のクラス / メソッド名）を呼び出す。成功・失敗を問わず応答前に `.cs` / `.cs.meta` を削除し、Editor 起動時にも前回クラッシュの残骸を掃除する。
- **2 回連続 `..._COMPILE` 拒否の自動回復** — 同一スニペット（`temp_id` またはコード本文の安定ハッシュ）が 2 回連続で `EDITOR_CTRL_RUN_SCRIPT_COMPILE` 拒否となった場合、Bridge は temp ディレクトリを再掃除して `AssetDatabase.Refresh` を要求し、`EDITOR_CTRL_RUN_SCRIPT_RECOVERY`（severity=warning）を返す（issue #116）。次回呼び出しはクリーンな状態で再試行できる。Bridge を再起動しなくても復帰できる経路。
- **3 つのエラー code を応答 code だけで判別する** — `EDITOR_RUN_SCRIPT_TRANSPORT_TIMEOUT`（transport poll 側、Wrapper が bridge の汎用 `EDITOR_BRIDGE_TIMEOUT` をリライト、issue #226）と `EDITOR_RUN_SCRIPT_COMPILE_TIMEOUT`（compile-pending 段階で Bridge 側 deadline = `compile_timeout_ms` + `RunScriptEntryTypeTimeoutMs(=4 s)` 経過、issue #234）と `EDITOR_CTRL_RUN_SCRIPT_COMPILE`（compile / staging / entry-point failure）の 3 つは別 code で運用判断する。message 文字列ではなく code で分岐する。
- **transport budget は floor 30 秒** — transport timeout は `max(RUN_SCRIPT_TRANSPORT_TIMEOUT_FLOOR_SEC=30, ceil(compile_timeout_ms / 1000) + RUN_SCRIPT_TRANSPORT_DISPATCH_MARGIN_SEC=5)` で算出され、bridge 側 deadline より transport が先に諦めることはない。`data.compile_timeout_ms` / `data.transport_timeout_sec` / `data.compile_timeout_max_ms` で response からプログラム的に確認できる。
- **`EDITOR_CTRL_RUN_SCRIPT_BAD_ID` は不正 temp id 専用** — submit / poll 分離経路（issue #233）で request identifier の形状違反は `REQUEST_ID_INVALID`、async runner が当該 request id を保持していない場合は `EDITOR_CTRL_RUN_SCRIPT_UNKNOWN_REQUEST`、submit から bridge が deadline 内に ACK しない場合は `EDITOR_RUN_SCRIPT_SUBMIT_TIMEOUT` で別 code を返す。
- **診断ペイロードはすべての失敗応答に添付される** — `..._COMPILE` / `..._RECOVERY` / `..._COMPILE_TIMEOUT` 応答すべてに `diagnostic_compiling` / `diagnostic_temp_files` / `diagnostic_last_domain_reload` が添付される。compile 状態と temp file 残骸の有無を確認する起点に使う。応答の `data` ペイロードと合わせて Bridge の状態を再構成できる。

呼び出し例（最短）:

```json
{
  "code": "using UnityEngine; public static class PrefabSentinelTempScript { public static void Run() { Debug.Log(\"hello\"); } }",
  "confirm": true,
  "change_reason": "debug: smoke check from DEBUGGING.md",
  "compile_timeout_ms": 15000
}
```

成功時は `EDITOR_CTRL_RUN_SCRIPT_OK` が返る。失敗時は本節の code 群で原因を切り分け、`diagnostic_temp_files` で残骸が残っていないことを確認してから再試行する。
