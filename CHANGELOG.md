# Changelog

`prefab-sentinel` の人手で精選した変更履歴。フォーマットは [Keep a Changelog 1.1.0](https://keepachangelog.com/ja/1.1.0/)、バージョン採番は [Semantic Versioning](https://semver.org/lang/ja/) に従う。`## [Unreleased]` を最上段に置き、リリース時にバージョンと日付を入れた節へ畳む。patch バンプ単位の網羅は対象外で、未掲載項目は `git log` を直接参照する。

## [Unreleased]

## [0.7.0] - 2026-05-19

### Changed

- アセットのリフレッシュがトリガーしたスクリプトコンパイルの成否を検出して報告するようになった。従来は結果を問わず即座に完了扱いとなり、コンパイルエラーが見落とされうる経路があった。

### Fixed

- ライブ Unity Editor 経由のプロパティ書き込み・パッチ適用が機能していなかった不具合を修正。リクエストのルーティング識別子が欠落しており、Editor 接続中の prefab / シーン編集の中核経路が軒並み失敗していた。
- Scene view カメラの配置に関する複数の不具合を修正。指定位置にカメラが着地しない（投影モード・距離計算の誤り）、操作直後の応答がカメラ状態を反映しないなど。スクリーンショットと framing の位置精度が改善する。
- Unity Editor 上でのスクリプト実行が完了を検知できず常にタイムアウトしていた不具合を修正。コンパイル不能なスニペットも、長い待機を挟まず即座に診断付きで失敗を返す。

## [0.6.0] - 2026-05-18

### Added

- `guide` スキルに「VRChat エコシステムナレッジ」節を追加。`knowledge/` 同梱の ModularAvatar / liltoon / VRCFury / AvatarOptimizer 等のドメインナレッジを、作業前に `knowledge/` の Glob で特定して読むよう案内する。プラグイン利用者の AI エージェントがエコシステムナレッジへ辿り着く導線を明示した（従来は本リポジトリの CLAUDE.md 規約に依存しており、エンドユーザーには届いていなかった）。

### Changed

- プロパティ書き込みを単一の `WritePropertyValue` レイヤーへ統一。`editor_set_property` / `editor_add_component` の初期プロパティ適用 / `editor_set_udonsharp_field` が同一の型別適用とエラー分類を共有する。これに伴い Quaternion 書き込みがコンポーネント生成と UdonSharp フィールド書き込みの経路でも受け付けられるようになった（従来は `editor_set_property` のみ）。
- `editor_add_component` の `properties_json` 初期プロパティ適用が、失敗時に診断を返すよう変更。プロパティ名の不一致・オブジェクト参照の解決失敗・値のパース失敗のいずれも、従来は無音で握りつぶされ成功レスポンスが返っていた。今後はそれぞれ診断エントリ（`properties_json[<name>]` を location に持つ）として応答に乗り、失敗が 1 件でもあれば `severity` を `warning` に上げる（コンポーネント追加自体は成功するため `success` は `true` のまま）。
- README の「VRChat エコシステムナレッジ」節を実態に合わせて修正。「通常作業中に自動で読み書きする」は本リポジトリの CLAUDE.md 規約による挙動でありプラグイン利用者には適用されないため、`guide` スキル経由でナレッジが供給される旨に書き換えた。あわせて「やること」にエコシステムナレッジ同梱を追記。

### Fixed

- セッションの bridge バージョン検査が `BRIDGE_NOT_FOUND` 診断を統一 4 キー形 `{severity, code, message, data}` で返すよう修正。従来は `data` キーを欠き、`activate_project` 応答の診断リスト経由で MCP クライアントが観測するワイヤ形がセッション診断間で不整合だった。

## [0.5.197] - 2026-05-16

初回公開リリース。MCP サーバー (`prefab-sentinel-mcp`) を唯一の外部インターフェースとし、以下を提供する。

### Added

- MCP サーバー (`prefab-sentinel-mcp`) と中核ツール（`activate_project` / `validate_refs` / `inspect_wiring` / `patch_apply` 等）。
- UdonSharp 操作向けの専用 MCP ツール群（`editor_add_udonsharp_component` / `editor_set_udonsharp_field` / `editor_wire_persistent_listener`）— backing UdonBehaviour の自動配線と CopyProxyToUdon 同期を 1 トランザクションで扱う。
- Editor Bridge 上の `editor_recompile_and_wait`（`CompilationPipeline.compilationFinished` 観測 + domain reload 跨ぎ）と、`editor_execute_menu_item` の implicit recompile barrier。
- `editor_console` の pagination / `phase_filter` / `classification_filter` / opaque cursor。
- `editor_safe_save_prefab` の `protect_components` / raw-save mode / orphan modification 報告。
- AnimationClip プリミティブ 3 種（`editor_inspect_animation_clip` / `editor_create_animation_clip` / `editor_apply_animation_clip`）と Prefab Stage open / close ツール。
- `validate_refs` の `snapshot_save` / `snapshot_diff` による build-before/after 分離、`refresh_guid_index` + `STALE_GUID_INDEX_HINT` 警告、`<scope>/config/ignore_guids.txt` の auto-load。
- 四半期 mutation testing の正本テンプレート (`docs/quarterly_mutmut_report_template.md`) と集計スクリプト (`scripts/mutmut_score_report.py`)。

### Changed

- 全 MCP ツールのエンベロープを `success / severity / code / message / data / diagnostics` に統一し、`severity` は同梱 `diagnostics` の最大重要度をフロアとして決定する。
- 書き込み系ツール（`set_property` / `add_component` / `remove_component` / `copy_component_fields` / `set_component_fields` / `set_material_property` / `copy_asset` / `rename_asset` / `revert_overrides` / `patch_apply`）は `confirm=True` 時に `change_reason` を必須化（`patch_apply` / `set_component_fields` はさらに `out_report` も必須）。
- `editor_set_property` の Quaternion サポート（xyzw 4 要素必須、ノルム `1.0 ± 1e-4` 外は `EDITOR_CTRL_SET_PROP_QUATERNION_NOT_NORMALIZED`）。
- Before-value 解決の戻り値型を `str | UnresolvedReason` とし、`UnresolvedReason` StrEnum で失敗理由を機械可読化。
- `editor_screenshot` の `view` allowlist 化と `crop_roi` preset / pixel-quadruple サポート（path-traversal 経路を遮断）。

### Removed

- Unity batchmode 経路を削除し、Editor 連携を常駐 Editor Bridge の file-IPC に一本化（`UNITYTOOL_UNITY_COMMAND` / `UNITYTOOL_UNITY_EXECUTE_METHOD` は参照されなくなった）。
- CLI（v0.4.0 で廃止済み。MCP サーバー経由のみがサポート対象）。
