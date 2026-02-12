# AGENTS.md

## 0. 目的と正本
- 本ファイルは `UnityTool` リポジトリにおける運用ルールの正本。
- 仕様の正本は `README.md`、運用ルールの正本は `AGENTS.md` とする。
- 仕様に関わる変更を行う場合は、実装変更と同時に `README.md` の該当箇所を更新する。

## 1. やること / やる内容 / やらないこと
### やること
- Unity SerializedObject レベルで安全に編集できる基盤を維持する。
- Base / Variant / Scene インスタンスの実効値と override を追跡可能にする。
- GUID + fileID の参照解決と整合性検証を標準APIとして扱う。
- UdonSharp compile / ClientSim smoke / ログ分類までを検証パイプラインに含める。
- 人間判断が必要な変更と機械的に実行可能な変更を分離する。

### やる内容
- MCP を責務別（編集・差分・参照・実行検証）に分割する。
- CLI Orchestrator で依存順序・停止条件・実行計画を管理する。
- 変更理由・対象・差分・検証結果を監査ログとして残す。
- fail-fast と段階的ロールバックを実装方針とする。

### やらないこと
- YAML文字列の直接置換を標準手段にしない。
- Unity内部参照を推測で補完しない。
- 根拠のない自動最適化や挙動合わせを行わない。
- ユーザー合意が必要な仕様変更を自動適用しない。

## 2. 設計原則
- Easy より Simple: 目的達成に必要な最小実装を優先する。
- 根拠優先: 前提・不変条件・判断理由を明文化する。
- 検証可能性: 設定値と結果の対応を説明できる実装のみ採用する。
- 必須参照の欠落は補完せず `error` で停止する（fail-fast）。

## 3. 責務境界（MCP / Skills）
- `serialized-object-mcp`: 何を書き換えるか（操作実行）。
- `prefab-variant-mcp`: どこが上書きされているか（差分可視化）。
- `reference-resolver-mcp`: 参照が有効か（実体照合）。
- `runtime-validation-mcp`: 実行時に壊れていないか（結果検証）。
- Skills: どの順で使うか（運用プロトコル）。

## 4. 変更時の必須フロー
1. 変更対象の scope（Prefab / Scene / Assets）を宣言する。
2. `list_overrides` と `scan_broken_references` で事前診断する。
3. 変更は `dry_run_patch` で差分確認後に `apply_and_save` する。
4. 適用後に `compile_udonsharp` と `run_clientsim` で実行検証する。
5. `critical` / `error` が 1 件でもあれば停止し、修正または判断待ちへ回す。

## 5. 意思決定ルール
- 自動修復可能で根拠があるもののみ `safe_fix` として提案・適用する。
- 複数候補や仕様判断が必要なものは `decision_required` として保留する。
- `decision_required` はユーザー合意後にのみ適用する。

## 6. 品質ゲート（Definition of Done）
- Broken PPtr 再発率: 0件（指定テストセット）。
- Variant override 整合性: 100%。
- Udon runtime critical: 0件（スモークシーン）。
- 各変更に `before/after` 差分と validation report を必ず添付する。

## 7. API / エラー規約
- すべての応答は `success / severity / code / message / data / diagnostics` を含む。
- 主要コード: `SER001`, `SER002`, `PVR001`, `REF001`, `REF002`, `RUN001`, `RUN002`。
- `severity` は `info | warning | error | critical` を使用する。

## 8. セキュリティと実行制御
- 既定は read-only inspection とし、書き込みは明示モード時のみ許可する。
- 重要操作は `--confirm` または署名付き実行計画を要求する。
- 外部プロセス実行（Unity batchmode）は許可リスト制とする。

## 9. テスト方針
- Unit: propertyPath解決、配列境界、参照逆引き。
- Integration: Base / Variant / Scene 三層編集のE2E。
- Regression: Broken PPtr / Udon nullref の既知再現ケース固定。

## 10. ドキュメント運用
- README は「やること / やる内容 / やらないこと」を維持する。
- 運用ルール変更時は本ファイルに追記し、理由を簡潔に残す。
- 仕様との齟齬が出た場合は README を優先して同期する。