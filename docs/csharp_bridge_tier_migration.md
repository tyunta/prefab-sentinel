# C# Bridge 振る舞い契約 — Tier 分類と per-concern 移行表

issue #357（H-1, keystone）の成果物。`tools/unity/*.cs`（C# Bridge）の振る舞いを文字列 grep で
回帰 pin している source-text テスト群を全件棚卸しし、各アサーションを移行 Tier に分類して
xUnit への移行先を確定させる。後続の H-2…H-(N+1)（per-concern 移行）と #358（cleanup）/
#359（docs）はすべて本表から派生する。

本 issue の scope は表の作成のみ。実装コード（純ロジック抽出・xUnit テスト追加・source-text
テスト削除）は H-2 以降で行う。

## 1. 背景と前提

C# Bridge は `UnityEditor` / `UnityEngine` 依存のため CI で実行できない。xUnit harness
（`tests/csharp/PrefabSentinel.Tests.csproj`、#290 で bootstrap）が compile に含むのは
Unity-free な `PrefabSentinel.Screenshot.ViewAllowlistClassifier.cs` 1 ファイルのみ。
結果、Python テストが `.cs` を文字列 grep して振る舞いを回帰 pin している。
`source_text_invariant` マーカー付きテストは 22 ファイル / 約 8,920 行（テストコードの約 17.8%）。

文字列結合テストは振る舞い不変のリファクタでも壊れ、Bridge が直近 90 日で最ホットな
編集領域であるため保守税が複利で効く。この装置を behavioral test へ移行する前提として、
現状の source-text アサーションを全件分類するのが本表の目的。

## 2. Tier 定義

#290 Phase 3 spec の T1/T2/T3 を踏襲し、レビュー指摘（2026-05-16）を受けて **T2 を T2a / T2b に
細分**した。T2 は「Unity-free な核を seam で切り出せる」という一括りだったが、実際には
コストが桁違いの 2 グループが同居していたため。

| Tier | 定義 | 移行先 / 扱い |
|------|------|--------------|
| **T1** | `System.IO` / `string` / `bool` / JSON のみ依存の純ロジック。Unity 型に一切触れない。 | `internal static class *Logic / *Predicate` へ抽出し、`<Compile Include>` で xUnit harness に取り込んで直接テスト。`ViewAllowlistClassifier` と同じパターン。**無条件の勝ち。** |
| **T2a** | Unity 型に触れるが、ハンドラが Unity の値（`bool`/`int`/`string`/enum）を読んで純関数に**引数で渡す**だけで核を切り出せる。production に新しいインターフェースを追加しない。 | 純関数 / 値オブジェクトを抽出。実質 T1 と同コスト・同じく**無条件の勝ち（clean-win）**。 |
| **T2b** | Unity オブジェクトを運ぶメソッドを持つ**インターフェースを production コードに新設**しないと核を切り出せない。production の構造を変える設計判断を伴う。 | seam（インターフェース）を導入。**opportunistic** — standalone の移行 PR にはしない。次にその concern の `.cs` を実機能・実バグ修正で触る PR の一部として seam を入れる（根拠・方式は §6.1）。 |
| **T3** | 定義の唯一性・命名規約・asmdef 曖昧性排除・partial 構成・定数ドリフトなど、実行では検証できない構造不変条件。 | source-text テストに恒久的に残す。#358 cleanup で「構造不変条件のみ」に純化される。 |

> **T2a/T2b の線引き**: 「ハンドラが Unity static を読んで `Classify(bool, bool)` に値を渡す」は
> T2a — seam とは名ばかりで実質は引数渡し。「`interface IUploadWorkflow { Task<…> RunAsync(…); }`
> を production に新設し `HandleAsync` をその shell に作り変える」は T2b — production 1 実装 +
> テスト用 fake 1 実装で 2 つ目の本物のユースケースがなく、リポジトリ最ホットのファイルに
> テスト都合の間接層を持ち込む（test-induced design damage のリスク）。後者は §6.1 の通り
> opportunistic に回す（standalone 移行 PR にしない）。

### 2.1 移行の参照実装

唯一の完了済み移行は `ScreenshotViewAllowlistClassifier`（`tools/unity/PrefabSentinel.Screenshot.ViewAllowlistClassifier.cs`）。
Unity 参照ゼロの `public static class` を `<Compile Include>` link で xUnit harness に取り込み、
screenshot ハンドラがその `IsAccepted` / `IsSceneView` に委譲する。T1 / T2a 抽出はこの形を踏襲する。

### 2.2 T3 テストの comment 除去（#358 への前提）

旧 #222 の 2026-05-13 観測: source-text テストが `assertIn(literal, body)` で C# メソッド
ボディを grep しているため、コメント行に literal が含まれると誤 green になる潜在リスクがある。
T3 として残す全構造テストは、grep 前に `re.sub(r"//.*$", source, flags=re.M)` 相当の
コメント除去を施す。#358 cleanup はこれを純化の必須前提として実施する
（一部の T3 テストは既にコメント除去を実装済み — 例 `TestScreenshotRoutingUsesClassifier`）。

## 3. 棚卸しの網羅範囲

| ファイル | テストクラス数 | 対象 .cs |
|---|---|---|
| `tests/test_editor_control_bridge_source.py` | 62 | `PrefabSentinel.UnityEditorControlBridge*.cs` |
| `tests/test_unity_patch_bridge_source.py` | 5 | `PrefabSentinel.UnityPatchBridge*.cs` |
| `tests/test_unity_integration_tests_source.py` | 1 | `PrefabSentinel.UnityIntegrationTests.cs` |
| `tests/test_vrcsdk_upload_handler_source.py` | 11 | `PrefabSentinel.VRCSDKUploadHandler.cs` |
| `tests/test_bridge_constants_drift.py` | 2 | （Python: `scripts/check_bridge_constants`） |
| `tests/test_bridge_constants_sync.py` | 2 | （Python: `bridge_constants` モジュール） |
| `tests/test_unity_patch_bridge_constants.py` | 2 | （Python: `tools/unity/unity_patch_bridge.py`） |

計 85 テストクラス。`*constants*` 3 ファイル（6 クラス）と integration source テスト 1 件は
**Python ツールの Python ユニットテストであり C# 移行対象外** — §4.14 参照。

## 4. per-concern 移行表

Tier 列の `(+T3)` 表記は「クラス内に移行するアサーションと、移行後も source-text に
残す T3 残骸（DTO フィールド宣言・forbidden-token grep・定数ドリフト等）が混在する」ことを示す。
`T2a→T1` は「seam なしで値を渡すだけ、`Math` 置換等で実質 T1 に落ちる」行。
#358 cleanup はこの残骸を「T3 恒久」ラベルの構造不変条件のみに純化する。

### 4.1 concern 横断・partial-layout・concern 固有 T3（全 T3、移行 issue なし）

| concern | source-text テスト箇所 | アサーション種別 | Tier | 移行先 xUnit テスト案 |
|---|---|---|---|---|
| cross-cutting | `test_editor_control_bridge_source.py::TestBridgePartialLayout` | partial-layout | T3 | T3 恒久。partial ファイルの存在・`partial class` 宣言・削除済 partial 不在は CLR から観測不能。 |
| cross-cutting | `…::TestBridgePartialSizing` | partial-layout | T3 | T3 恒久。行数上限・concern コメント・legacy allowlist 整合はテキスト事実。 |
| cross-cutting | `…::TestOperationalRulesPartialInventory` | partial-layout | T3 | T3 恒久。CLAUDE.md inventory ↔ disk のドリフト検査。C# テストですらない。 |
| cross-cutting | `test_unity_patch_bridge_source.py::TestPatchBridgePartialLayout` | partial-layout | T3 | T3 恒久。disk 上 9 ファイル集合 ↔ documented inventory の同一性。 |
| cross-cutting | `…::TestPatchBridgePartialDeclaresPartialClass` | partial-layout | T3 | T3 恒久。`partial` キーワード presence は compiler-input 事実。 |
| cross-cutting | `…::TestPatchBridgeOperationalRulesInventory` | partial-layout | T3 | T3 恒久。disk ↔ CLAUDE.md 同期。 |
| MenuScriptWatch | `…::MenuScriptWatchSplitSourceInvariantTests` | partial-layout | T3 | T3 恒久。#262 partial split の宣言唯一性・inventory drift。 |
| Hierarchy | `…::TestGetHierarchyPathDedup` | structural-invariant | T3 | T3 恒久。`GetHierarchyPath` 定義唯一性（CS0111 ガード）。本体は `Transform.parent` 走査で Unity 結合。 |
| Components | `…::TestAsmdefAssemblyDisambiguation` | structural-invariant | T3 | T3 恒久。完全修飾 `System.Reflection.Assembly` 使用の compile-disambiguation 不変条件。 |
| Helpers | `…::TestResolveComponentTypeDedup` | structural-invariant | T3 | T3 恒久。`ResolveComponentType` 定義唯一性（CS0111 ガード）。 |
| UiElement | `…::TestEditorAsmdefUiReferences` | structural-invariant | T3 | T3 恒久。asmdef の `Unity.TextMeshPro` / `UnityEngine.UI` 参照（ビルド構成）。 |
| SaveInstantiate | `…::TestBatchCreateParentWarning` | string-literal-grep | T3 | T3 恒久。`parent` 未解決時の "Parent not found" 警告。Unity-heavy なループ内のリテラルで純核なし。 |
| SaveInstantiate | `…::TestBatchObjectSpecComponents` | structural-invariant, string-literal-grep | T3 | T3 恒久。`BatchObjectSpec.components` フィールド宣言 + `ResolveComponentType`（`AppDomain` 走査）依存の警告。 |
| SaveInstantiate | `…::TestSafeSaveAsPrefabSource` | string-literal-grep, structural-invariant | T3 | T3 恒久。`PrefabUtility.SaveAsPrefabAsset` / `Undo.AddComponent` 主体。#228 の `PROTECT_REQUIRED` 1 回・配置順 pin は control-flow-shape 不変条件。 |
| UdonSharpAddComponent | `…::TestAddUdonSharpComponentHandler` | string-literal-grep | T3 | T3 恒久。`UdonSharpUndo.AddComponent` / proxy-to-backing reflection で本体は Unity 結合。純核なし。 |
| UdonSharpFieldWrite | `…::TestSetUdonSharpFieldHandler` | string-literal-grep | T3 | T3 恒久。`SerializedObject` / `UdonSharpEditorUtility` 主体。VRCUrl 検出も Unity 型の reflection metadata。 |
| UdonSharpListenerWiring | `…::TestWirePersistentListenerHandler` | string-literal-grep | T3 | T3 恒久。`UnityEventTools` / `UnityEventBase` 走査。抽出可能な純核なし。 |

### 4.2 ConsoleCapture

| concern | source-text テスト箇所 | アサーション種別 | Tier | 移行先 xUnit テスト案 |
|---|---|---|---|---|
| ConsoleCapture | `…::TestConsoleLogBufferRetrievalAppliesPhaseFilter` | string-literal-grep | **T1** (+T3) | `MatchesPhaseFilter` / `MatchesTypeFilter` / `MatchesClassificationFilter`（既に純 — `UnityEditorControlBridge.cs:1287-1302`）を `internal static class ConsoleLogEntryPredicate` へ抽出。xUnit で catch-all / strict-equality を直接検証。`GetEntries` が predicate を呼ぶ点は T3 委譲 grep として残す。 |
| ConsoleCapture | `…::TestOnLogMessagePhasePriority` | string-literal-grep | **T2a** | `internal static class ConsoleLogPhaseClassifier` に `string Classify(bool isBuildingPlayer, bool isPlayingOrWillChangePlaymode)` を抽出（value-in/value-out 純関数、新インターフェースなし）。`OnLogMessage` が 2 つの Unity static flag を読んで引数で渡す。xUnit で build>play>edit の 3-way 真理値表を検証。 |
| ConsoleCapture | `…::TestHandleCaptureConsoleLogsContract` | string-literal-grep | **T2a** (+T3) | `internal static class ConsoleCaptureRequestValidator` に `Validate(order, cursor, maxEntries, highestSeqId, capacity) -> (ok, code, cursorAfter)` を抽出。buffer 依存は `long`/`int` 値として注入（インターフェース不要）。xUnit で ordering whitelist / cursor prefix・parse・range / `EDITOR_CTRL_INVALID_*` コードを検証。DTO フィールド presence サブテストは T3 残骸。 |
| ConsoleCapture | `…::TestHandleCaptureConsoleLogsBoundCheck` | string-literal-grep | **T2a** | 上記 `ConsoleCaptureRequestValidator` に統合。`[1, capacity]` 境界と `EDITOR_CTRL_MAX_ENTRIES_OUT_OF_RANGE` を `test_default_parameter_boundaries.py` 流の ±1 境界テストで pin。 |
| ConsoleCapture | `…::TestHandleCaptureConsoleLogsValidatesPhaseFilter` | string-literal-grep | **T2a** | 上記 validator に統合。`IsSupportedPhaseFilter`（`:1287` 既に純）の検証 + 未対応 selector で `EDITOR_CTRL_INVALID_PHASE_FILTER` を返すことを xUnit で検証。 |
| ConsoleCapture | `…::TestConsoleLogEntryDeclaresPhaseField` | string-literal-grep | T3 | T3 恒久。`ConsoleLogEntry` の `public string phase` フィールド宣言不変条件。 |
| ConsoleCapture | `…::TestConsoleLogBufferCapacityVisibility` | constant-drift | T3 | T3 恒久。`ConsoleLogBuffer.DefaultCapacity` の `public const` 可視性（C# ↔ Python mirror のドリフト）。 |

### 4.3 Properties（property 書き込み）

| concern | source-text テスト箇所 | アサーション種別 | Tier | 移行先 xUnit テスト案 |
|---|---|---|---|---|
| Helpers/Properties | `…::TestApplyPropertyValueTypes` | string-literal-grep | **T2a** | `ApplyPropertyValue`（`Helpers.cs:98`）の文字列パース部を Unity-free 純関数 `PropertyValueParser` へ分離。`SerializedPropertyType` は内部 enum にミラーし、戻り値は生 `(kind, float[] components, errorCode)` — UnityEngine 型（`Color`/`Vector4`）を返さないので harness 取り込み可。ハンドラが戻り値から `new Color(...)` 等を構築し `SerializedProperty` へ書く。**`IPropertySink` インターフェースは新設しない**（値オブジェクト戻り値で足りる — 当初案の T2b 化を回避）。Color alpha デフォルト・Vector arity 拒否を xUnit で検証。 |
| Properties | `…::TestHandleEditorSetPropertyQuaternion` | string-literal-grep, constant-drift | **T2a→T1** | `QuaternionInputValidator.Validate(string) -> (ok, code, qx,qy,qz,qw)` を抽出。`Mathf.Sqrt`/`Mathf.Abs` を `System.Math` に置換すれば完全 T1。xUnit で 4 成分要求・`1e-4` ノルム境界・エラーコードを pin。 |
| Properties | `…::TestSetPropertyGameObject` | string-literal-grep | **T2a→T1** (+T3) | `internal static class GameObjectPropertyAllowlist`（`string[]` セット + `IsAllowed`）を抽出（`ViewAllowlistClassifier` パターン）。4 つの allowlist 名と reject ケースを xUnit で pin。`new SerializedObject(go)` grep は T3 残骸。 |
| Properties | `…::TestSetPropertySuggestions` | string-literal-grep | **T1** (+T3) | `SuggestSimilar` + `LevenshteinDistance`（`UnityEditorControlBridge.cs:1649`、既に Unity-free）を `internal static class SuggestionRanker` へ移し `<Compile Include>` link。ランキング・`0.4` distance-ratio 閾値・`maxResults` truncation・空入力ガードを xUnit で pin。"Did you mean" サフィックス分岐と iterator 走査は T3 残骸。 |

### 4.4 UiElement

| concern | source-text テスト箇所 | アサーション種別 | Tier | 移行先 xUnit テスト案 |
|---|---|---|---|---|
| UiElement | `…::TestCreateUiElementSource` | structural-invariant, string-literal-grep | **T2a→T1** (+T3) | `internal static class UiElementTypeAllowlist`（`{Image, TextMeshProUGUI, Button, Slider, Toggle}` セット + `IsAllowed`）を抽出。5 トークンと `BAD_TYPE` 拒否を xUnit で pin。`SupportedActions` membership・dispatcher case・envelope コード・font-path 定数は T3 残骸。 |
| UiElement | `…::TmpFontMissingMessageBranching` | string-literal-grep | **T1** | `internal static class UiFontMissingMessage` に `string ForCallerFontPath(string callerFontPath, string canonicalDefaultPath)` を抽出。`string.IsNullOrEmpty(props.font)` の 3 項分岐は純 string/bool。両 arm（空→canonical default 名・非空→caller path 補間）を xUnit で直接検証。envelope code/severity/payload-key は T3 残骸。 |

### 4.5 Menu + MenuScriptWatch

| concern | source-text テスト箇所 | アサーション種別 | Tier | 移行先 xUnit テスト案 |
|---|---|---|---|---|
| MenuScriptWatch | `…::TestHasEditorScriptChangedSinceScopeExpanded` | string-literal-grep, constant-drift | **T2a** | `internal static class EditorScriptPathClassifier` に純 `bool IsEditorSourcePathDirty(string relativePath)`（or `HasEditorSegment(string[])` / `IsInRunScriptTempArea(string[])`）を抽出。`HasEditorScriptChangedSince` は `Directory.GetFiles` 走査 + mtime 比較のみ保持し per-path 分類を委譲（値渡しのみ、インターフェース不要）。nested-Editor 受理・temp-area 除外・substring 偽陰性を xUnit で検証。`MenuExecuteAssetsRoot`/`…EditorSegment` の値 pin は T3 残骸。 |
| MenuScriptWatch | `…::MenuHasEditorScriptChangedSinceSegmentExclusionTests` | string-literal-grep, structural-invariant, constant-drift | **T2a** (+T3) | 上記 `EditorScriptPathClassifier` に統合。`_PrefabSentinelTemp` を whole-segment `string.Equals` で除外 vs substring `IndexOf`（実バグクラス）を xUnit で検証。`IndexOf` 不在 grep と定数値 pin は T3 残骸。 |
| Menu+MenuScriptWatch | `…::TestMenuExecuteBarrierSource` | string-literal-grep, constant-drift, structural-invariant | **T1** (+T3) | `HasEditorScriptChangedSince`（`MenuScriptWatch.cs:44`）の Unity-free 核（dir 走査・path-segment split・whole-segment マッチ・mtime 比較、Unity 接点は `Debug.LogWarning` のみ）を `EditorScriptChangeDetector` として抽出。temp ディレクトリツリーを作って whole-segment マッチ・temp 除外・I/O 失敗→`return true` 保守的契約を xUnit で検証。menu-barrier grep（`isCompiling` / `assume_compiled`）・resumer coverage・`AsyncActions` membership は T3 残骸。 |

> §4.5 の 3 クラスはすべて `HasEditorScriptChangedSince` の同一純ロジック核に収束する。
> 1 つの `EditorScriptPathClassifier` / `EditorScriptChangeDetector` 抽出で 3 クラスの
> behavioral アサーションを同時に retire できる。

### 4.6 RunScriptCompile — validators / deadline

| concern | source-text テスト箇所 | アサーション種別 | Tier | 移行先 xUnit テスト案 |
|---|---|---|---|---|
| RunScriptCompile | `…::TestCompileTimeoutRequestField` | string-literal-grep | **T2a** (+T3) | `RunScriptDeadline.Resolve(int requestTimeout, int bridgeDefault, long callTimeMs, int entryTypeTimeoutMs) -> (compilePollMs, deadlineMs)` を純関数として抽出。default-vs-override 選択と deadline 加算を xUnit で pin。DTO フィールド grep は T3 残骸。 |
| RunScriptCompile | `…::TestRecompileAndWaitTimeoutBoundCheck` | constant-drift, string-literal-grep | **T2a** (+T3) | `RecompileTimeoutValidator.Validate(float timeoutSec) -> (ok, code, resolvedBudgetSec)` を抽出（`RecompileAndWaitTimeoutMaxSec`=1800 を公開）。`<0` 拒否・`0`→default・1800 上限の ±1 境界を xUnit で pin。1800 のクロス言語ドリフトは `check_bridge_constants` 系テストに残す。 |
| RunScriptCompile | `…::TestRunScriptCompilePendingResponseDeadlinePath` | string-literal-grep | **T2a** (+T3) | `RunScriptCompilePendingCodeSelector.SelectCode(int priorStuckCount, int stuckThreshold) -> string` を抽出。`RunScriptStuckThreshold ± 1` の recovery/timeout 境界を xUnit で pin。`assertNotIn EDITOR_CTRL_RUN_SCRIPT_COMPILE`（generic コード不在）は T3 残骸。 |

### 4.7 RunScriptCompile — recompile 解決ガード / 診断 redaction

| concern | source-text テスト箇所 | アサーション種別 | Tier | 移行先 xUnit テスト案 |
|---|---|---|---|---|
| RunScriptCompile | `…::TestRecompileAndWaitOutcomeSync` | string-literal-grep | **T2a** (+T3) | 二重解決ガードを `internal sealed class RecompileResolutionGuard` に抽出（`bool TryClaim()` — 初回 true・以後 false。Unity 非依存の小クラス、インターフェースではない）。`FAILED` vs `NOOP` の outcome 選択を `RecompileOutcomeClassifier`（値オブジェクトの compile-result snapshot 入力）へ。single-claim セマンティクスとコード選択を xUnit で検証。lambda 配線は T3 残骸。 |
| RunScriptCompile | `…::TestRecompileAndWaitDeadlineWatchdog` | string-literal-grep | **T2a** (+T3) | 上記 `RecompileResolutionGuard.TryClaim()` を再利用 + 純 `bool DeadlineElapsed(long nowTicks, long deadlineTicks)` predicate を追加。watchdog が guard を claim し timeout outcome のみ発火することを xUnit で検証。「他コードを参照しない」negative アサートは T3 残骸。 |
| RunScriptCompile | `…::RecompileScheduleFailedSanitization` | string-literal-grep | **T2a** (+T3) | `internal static class ScheduleFailureEnvelope` に `string RedactedMessage()`（固定定数）を抽出。redacted message が固定リテラルで `ex.Message` を含まないことを xUnit で検証。catch arm がヘルパを呼ぶ配線は T3 残骸。 |
| RunScriptCompile | `…::RecompileForceReimportDiagnosticRedaction` | string-literal-grep | **T2a** (+T3) | `internal static class ReimportDiagnostic` に `string Evidence(Exception ex)`（`ex.GetType().Name` のみ、`.Message` 不使用。`Exception` は System 型）を抽出。evidence が例外メッセージ本文を含まないことを xUnit で検証。catch arm 配線は T3 残骸。 |

### 4.8 RunScriptCompile — T3 恒久（移行対象外）

| concern | source-text テスト箇所 | アサーション種別 | Tier | 判断理由 |
|---|---|---|---|---|
| RunScriptCompile | `…::TestRunScriptShortPoll` | string-literal-grep | T3 | per-frame poller は Unity-frame コールバック。完了条件核を Unity-free に切れない。 |
| RunScriptCompile | `…::TestForceReimportSupport` | string-literal-grep | T3 | `AssetDatabase.ImportAsset` / `CompilationPipeline` の orchestration。純核なし。 |
| RunScriptCompile | `…::TestRecompileAndWaitDispatch` | structural-invariant, string-literal-grep | T3 | HashSet membership カタログ + `CompilationPipeline` イベント配線 + negative grep。 |
| RunScriptCompile | `…::TestRunScriptNoSleep` | structural-invariant | T3 | `Thread.Sleep` 不在の negative regression grep。 |
| RunScriptCompile | `…::TestRecompileAndWaitDomainReloadResume` | string-literal-grep, structural-invariant | T3 | domain-reload survival。`-1` reload-count 閾値は live reload counter ライフサイクルと不可分。 |
| RunScriptCompile | `…::TestRecompileAsmFinishedDelegateType` | string-literal-grep | T3 | Unity API の型シグネチャ pin（CS0426 ガード）。compile-time 正当性。 |
| RunScriptCompile | `…::TestRecompileScheduleFailedCode` | string-literal-grep | T3 | live Editor 内でしか throw しない catch arm のコード分類。 |
| RunScriptCompile | `…::TestRunScriptPollFrameRuntimeCatchesNoLeakInEnvelope` | string-literal-grep | T3 | leak-token 不在（negative-space 不変条件）。全例外型での不在は実行で証明不能。 |
| RunScriptCompile | `…::TestRunScriptPollFrameRuntimeCatchesRouteToConsole` | string-literal-grep | T3 | `Debug.LogWarning` は Unity-only sink。detail mirror は Unity-free harness で観測不能。 |
| RunScriptCompile | `…::TestHandleRunScriptStageRefreshCatchesNoLeak` | string-literal-grep | T3 | 同上（negative-space + Unity sink）。 |
| RunScriptCompile | `…::TestHandleRunIntegrationTestsCatchNoLeakInEnvelope` | string-literal-grep | T3 | 同上。ハンドラ本体は `UnityIntegrationTests.RunTestSuite()` で Unity 結合。 |
| RunScriptCompile | `…::TestCleanupRunScriptTempFilesRefreshCatchLogFormat` | string-literal-grep | T3 | `{ex}` vs `{ex.Message}` の差は Unity Console でのみ観測可能。 |
| RunScriptCompile | `…::TestBuildRecompileReloadWaitPollDrainsImportQueue` | string-literal-grep | T3 | poll closure 全体が Unity 呼び出しの orchestration。純核なし。 |

### 4.9 Screenshot

| concern | source-text テスト箇所 | アサーション種別 | Tier | 移行先 xUnit テスト案 |
|---|---|---|---|---|
| Screenshot | `…::EditorControlBridgeScreenshotCameraStateRestoreTests` | string-literal-grep, partial-layout | **T2b** | **seam（opportunistic）**: `ICameraStatePort`（`CameraState Capture()` / `void Restore(CameraState)`）+ Unity-free `readonly struct CameraState`（pivot, rotation, size, orthographic）。preset framing 後に `Restore` が `Capture` の戻り値と同一値で呼ばれること（round-trip identity）を fake port で xUnit 検証。partial 存在チェックは T3 残骸。**`ICameraStatePort` は production への interface 新設 — §6.1 の通り opportunistic（standalone 移行しない）。** |
| Screenshot | `…::ScreenshotViewAllowlistSourceTests` | string-literal-grep, partial-layout | T3 | T3 恒久。振る舞い（`IsAccepted`）は既に `ViewAllowlistClassifier` へ移行済。残るのは委譲配線 + reject-precedes-filesystem の source-ordering regression net。 |
| Screenshot | `…::TestScreenshotRoutingUsesClassifier` | string-literal-grep, structural-invariant | T3 | T3 恒久。**完了済み移行のモデル**。振る舞いは `ScreenshotViewKindTests.cs`（xUnit）でカバー済。残るのは委譲 site pin と並行実装不在の不変条件。 |

### 4.10 PrefabStage

| concern | source-text テスト箇所 | アサーション種別 | Tier | 移行先 xUnit テスト案 |
|---|---|---|---|---|
| PrefabStage | `…::PrefabStagePersistFixSourceInvariantTests` | string-literal-grep, structural-invariant | **T1 + T2b** | **(a) T1（無条件・clean-win）**: パス正規化（`StartsWith("/")`→`Substring(1)`）を `internal static class StageHierarchyPathLogic.NormalizeStagePath(string)` へ抽出、`ViewAllowlistClassifier` パターンで xUnit 検証。**(b) T2b（opportunistic）**: `IPrefabStagePersistencePort`（`bool IsActive`, `bool SaveAsPrefabAsset(out bool didSave)`, `void ClearDirtiness()`）を導入し close ハンドラを Unity-free `ClosePrefabPlanner.Execute(request, port)` へ。`save_on_close` false→未呼び出し / true→1 回・`saved` は `out didSave` 反映 / `IsActive=false`→`EDITOR_CTRL_PREFAB_STAGE_CLOSE_FAILED` / 例外→コード+`ex.Message` を fake port で xUnit 検証。**(b) は production への interface 新設 — §6.1 の通り opportunistic（standalone 移行しない）。(a) のみ先行 clean-win として実施可。** |

### 4.11 Helpers / core — dispatch・DTO

| concern | source-text テスト箇所 | アサーション種別 | Tier | 移行先 xUnit テスト案 |
|---|---|---|---|---|
| core | `test_editor_control_bridge_source.py::TestUdonSharpActionWiring` | string-literal-grep | **T2a** | アクションレジストリを Unity-free `internal static class ActionRegistry` へ抽出（`IReadOnlySet<string> Supported` / `Async` + `bool TryClassify(string action, out ActionDescriptor)`。データのみ、Unity 型なし）。membership と async 分類を xUnit で直接検証。`RunFromPaths` がレジストリを消費。 |
| core | `…::TestUdonSharpRequestFields` | string-literal-grep | **T2a** | `EditorControlRequest` は Unity 型を持たない値オブジェクト。`<Compile Include>` で xUnit harness に直接取り込み、フィールド presence / JSON round-trip を検証（seam すら不要）。`JsonUtility` パリティが必要な部分のみ source-text に残す。 |
| Helpers/core | `…::EditorControlBridgeRequestSchemaTests` | string-literal-grep | T3 | T3 恒久。Python wrapper が送る全フィールドを DTO が宣言する完全性不変条件（クロスファイル宣言）。 |
| Helpers/core | `…::EditorControlBridgeDispatcherRoutingTests` | string-literal-grep | T3 | T3 恒久。網羅ルーティング / C#↔Python アクション集合の対称性不変条件。 |
| Helpers | `…::HelpersResolveObjectReferenceSourceTests` | string-literal-grep, structural-invariant | T3 | T3 恒久。`ResolveGameObjectInActiveStage` への委譲 net + `GameObject.Find` 直呼び不在の forbidden-pattern grep。 |
| ConsoleCapture/Helpers | `…::TestHandleGetEditorStateReadsFourFlags` | string-literal-grep | T3 | T3 恒久。Unity static 4 flag を DTO へ読むだけ。seam は同じ 4 bool を再配線するのみ。 |
| core | `…::TestEditorControlDataDeclaresNoExceptionTextField` | structural-invariant | T3 | T3 恒久。`EditorControlData` に `exception` フィールドが**ない**こと（field-absence 不変条件）。 |
| cross-cutting | `…::TestBestEffortCatchWarnings` | string-literal-grep | T3 | T3 恒久。全 catch site が typed + warn-template というコーディング規約。bare catch 不在は source 検査でのみ強制可能。 |

### 4.12 UnityPatchBridge

| concern | source-text テスト箇所 | アサーション種別 | Tier | 移行先 xUnit テスト案 |
|---|---|---|---|---|
| Prefab | `test_unity_patch_bridge_source.py::TestPrefabApplyRejectionEnvelopeSource` | string-literal-grep | **T2a** | `internal static class PrefabApplyRejectionEnvelope` に値オブジェクト `PrefabApplyFailure { PropertyPath, ComponentType, AttemptedValue }`（全フィールド `string`）を入力、`SER_APPLY_REJECTED` コードと 3 つの diagnostic キーを持つ payload を返す純関数を抽出。Unity の prefab-apply 呼び出し site が値オブジェクトを埋めて呼ぶ（インターフェース不要）。 |
| core | `…::TestPatchBridgeCoreConstantsPresent` | structural-invariant, constant-drift | T3 | T3 恒久。`ProtocolVersion`・`s_currentHandles` の宣言 site 唯一性（`[ThreadStatic]` 配置含む）。 |

### 4.13 VRCSDKUploadHandler

| concern | source-text テスト箇所 | アサーション種別 | Tier | 移行先 xUnit テスト案 |
|---|---|---|---|---|
| VRCSDKUploadHandler | `test_vrcsdk_upload_handler_source.py::TestHandleAsyncFullProtection` | structural-invariant | **T2b** | **seam（opportunistic）**: `interface IUploadWorkflow { Task<EditorControlResponse> RunAsync(EditorControlRequest); }` + 薄い `HandleAsync` shell（`try { resp = await workflow.RunAsync(req); } catch { resp = BuildError(...); } finally {} WriteResponse(...)`）。各段で throw する fake workflow を注入し、常にエラー envelope が `WriteResponse` に渡ることを xUnit で検証。 |
| VRCSDKUploadHandler | `…::TestLoginPollingInsideTryCatch` | structural-invariant | **T2b** | 上記 `IUploadWorkflow` seam に統合。login polling を seam の `RunAsync` 内に置き、polling 中に throw する fake で shell の outer catch が処理することを検証。 |
| VRCSDKUploadHandler | `…::TestLoginPollingInHandleAsync` | string-literal-grep | **T2b** | **seam（opportunistic）**: `interface ILoginGate { bool IsLoggedIn { get; } void OpenSdkPanel(); }` + Unity-free `LoginPollLogic.WaitForLoginAsync(ILoginGate, attempts, delay)`。fake gate（never-logs-in / logs-in-on-3）で timeout→`VRCSDK_NOT_LOGGED_IN` を xUnit 検証。 |
| VRCSDKUploadHandler | `…::TestResolveBuilderAsyncRetry` | string-literal-grep | **T2b** | **seam（opportunistic）**: `interface IBuilderProbe { object TryGetBuilder(); void OpenSdkPanel(); }` + Unity-free `RetryLogic.PollAsync(IBuilderProbe, attempts, delay)`。retry 回数と null-probe の最終失敗を xUnit 検証。 |
| VRCSDKUploadHandler | `…::TestCS0117GetBuildTargetGroup` | string-literal-grep | T3 | T3 恒久。CS0117 regression（非存在 API 不使用）。compile-correctness 事実。 |
| VRCSDKUploadHandler | `…::TestCS1501BuildErrorVisibility` | structural-invariant | T3 | T3 恒久。3-arg `BuildError` の `internal` 可視性（CS1501 regression）。 |
| VRCSDKUploadHandler | `…::TestCS0246BuildAndUploadWorldReflection` | string-literal-grep | T3 | T3 恒久。VRC SDK 型を直接 CLR 参照せず reflection 文字列経由（CS0246 regression）。 |
| VRCSDKUploadHandler | `…::TestVRCApiTypeConstant` | structural-invariant | T3 | T3 恒久。assembly-qualified 文字列の単一定義（DRY 不変条件）。 |
| VRCSDKUploadHandler | `…::TestShowSdkPanelMenuItemConstant` | structural-invariant | T3 | T3 恒久。menu path リテラルの単一定義（DRY 不変条件）。 |
| VRCSDKUploadHandler | `…::TestPreprocessorGuardVersionSpecific` | structural-invariant | T3 | T3 恒久。`#if` ガードの配置。compile-conditional ファイル包含。 |
| VRCSDKUploadHandler | `…::TestAssemblyLookupPattern` | string-literal-grep | T3 | T3 恒久。assembly 非依存 reflection の実装技法 pin。packaging 事実。 |

> §4.13 の 4 つの T2b は 3 つの seam に収束する: `IUploadWorkflow`（例外安全 shell —
> `TestHandleAsyncFullProtection` + `TestLoginPollingInsideTryCatch`）、`ILoginGate`/`LoginPollLogic`、
> `IBuilderProbe`/`RetryLogic`。いずれも production に interface を新設するため H-12 全体が
> opportunistic（§6.1）。CS-error-code regression テスト（CS0117/CS1501/CS0246）は、Editor-host
> build（UTF EditMode テスト）が存在すれば compile error として捕捉される性質のため、移行では
> なく将来的に**削除可能**（本表では T3 恒久として残す判断）。

### 4.14 Python ツールテスト（C# 移行対象外）

以下は `.cs` を grep しておらず、Python ツール自身の Python ユニットテスト。C# xUnit harness の
移行対象ではなく、現状の Python テストとして残置する（#358 cleanup の対象外）。

| ファイル :: クラス | 実体 |
|---|---|
| `test_unity_integration_tests_source.py::TestNonFatalClassificationCallsSafeSave` | テストフィクスチャ文字列の grep（別テストファイルを検査）。Editor-host harness 出現後は冗長化し**削除**可能。 |
| `test_bridge_constants_drift.py::BridgeConstantsConsoleCapacityDriftTests` / `BridgeConstantsDriftTests` | `scripts.check_bridge_constants` の Python ユニットテスト。 |
| `test_bridge_constants_sync.py::BridgeConstantsSyncTests` / `RuntimeValidationConfigSurfaceTests` | live ドリフト検査 + Python モジュール export surface 検査。 |
| `test_unity_patch_bridge_constants.py::TestPatchBridgeWireCodeRegression` | `tools/unity/unity_patch_bridge.py`（Python bridge）の振る舞いテスト。 |
| `test_unity_patch_bridge_constants.py::TestPatchBridgeBareLiteralCoverage` | Python source の bare リテラル不在 grep（DRY 規律）。 |

## 5. Tier 集計

クラスを「移行価値のある主 Tier」で 1 件計上（多くが mixed — Tier 行は移行後も T3 残骸を残す）。

| Tier | テストクラス数 | 備考 |
|------|---------------|------|
| **T1**（純抽出 — Unity 型に一切触れない） | 4 | `ConsoleLogEntryPredicate`（`TestConsoleLogBufferRetrievalAppliesPhaseFilter`）/ `SuggestionRanker`（`TestSetPropertySuggestions`）/ `UiFontMissingMessage`（`TmpFontMissingMessageBranching`）/ `EditorScriptChangeDetector`（`TestMenuExecuteBarrierSource`）。 |
| **T2a**（値 seam — 無条件の勝ち） | 20 | Unity の値を引数で渡すだけ。新インターフェースを production に追加しない。実質 T1 と同コスト。`QuaternionInputValidator`・`GameObjectPropertyAllowlist`・`UiElementTypeAllowlist` は `Math` 置換等で完全 T1 に落ちる。 |
| **T2b**（interface seam — opportunistic） | 6 | `ICameraStatePort`（`EditorControlBridgeScreenshotCameraStateRestoreTests`）/ `IPrefabStagePersistencePort`（`PrefabStagePersistFixSourceInvariantTests`）/ `IUploadWorkflow`（`TestHandleAsyncFullProtection`, `TestLoginPollingInsideTryCatch`）/ `ILoginGate`（`TestLoginPollingInHandleAsync`）/ `IBuilderProbe`（`TestResolveBuilderAsyncRetry`）。production に interface を新設する設計判断を伴う → §6.1 の通り opportunistic（standalone 移行 PR にしない）。 |
| **T3**（source-text 恒久） | 55 | 内訳: 純構造/layout 不変条件、concern 固有の Unity 結合ハンドラ本体 grep、forbidden-token negative grep、定数ドリフト、Python ツールテスト 7 件。 |
| 計 | 85 | — |

T1/T2 を持つ concern: ConsoleCapture, Properties, UiElement, MenuScriptWatch, RunScriptCompile,
Screenshot, PrefabStage, core(dispatch/DTO), Prefab(patch bridge), VRCSDKUploadHandler。
T3 のみの concern（移行 issue なし）: Hierarchy, Components, SaveInstantiate, UdonSharpAddComponent,
UdonSharpFieldWrite, UdonSharpListenerWiring, Menu(barrier 部), UnityIntegrationTests,
ManagedReference / Asset / Scene / Resolve / Mutation / Diagnostics / Payloads
（patch bridge — すべて partial-layout / constant T3 のみ）。

## 6. H-2…H-(N+1) 起票リスト（N = 11）

各 issue は #290 の per-migration PR 規約を踏襲する: 既存 source-text テストの **behavioral 部分
のみ削除**、**delegation invariant（"bridge X calls Predicate.Y"）は narrow T3 として残置**、
Tier3 Justification 表を更新。粒度は 1 issue = 1 PR = 半日〜1 日（`feedback_issue_granularity`）。
順序は #290 の難易度昇順の目安を尊重し、最易の MenuScriptWatch reference 実装を先頭付近に置く。

**種別** 列: `clean-win` = T1/T2a のみ、予定された移行 PR として無条件で着手してよい（8 issue）。
`opportunistic` = T2b の interface seam のみ。standalone 移行 PR にせず、次にその concern の `.cs`
を実作業で触る PR に相乗りさせる（H-9 / H-12、根拠 §6.1）。`mixed` = T1 部は clean-win、T2b 部は
opportunistic（H-10）。

| issue | GitHub | 種別 | 対象 concern | scope（抽出するクラス / seam） | 移行する source-text テストクラス |
|---|---|---|---|---|---|
| **H-2** | #364 | clean-win | MenuScriptWatch | `EditorScriptPathClassifier`（T1 核）/ `EditorScriptChangeDetector`。#290 が「最易 reference 実装」と指定。 | `TestHasEditorScriptChangedSinceScopeExpanded`, `MenuHasEditorScriptChangedSinceSegmentExclusionTests`, `TestMenuExecuteBarrierSource`（detector 部） |
| **H-3** | #365 | clean-win | ConsoleCapture | `ConsoleLogEntryPredicate`（T1）, `ConsoleLogPhaseClassifier`（T2a）, `ConsoleCaptureRequestValidator`（T2a） | `TestConsoleLogBufferRetrievalAppliesPhaseFilter`, `TestOnLogMessagePhasePriority`, `TestHandleCaptureConsoleLogsContract`, `TestHandleCaptureConsoleLogsBoundCheck`, `TestHandleCaptureConsoleLogsValidatesPhaseFilter` |
| **H-4** | #366 | clean-win | Properties | `SuggestionRanker`（T1）, `QuaternionInputValidator`（T1）, `GameObjectPropertyAllowlist`（T1）, `PropertyValueParser`（T2a、値オブジェクト戻り — `IPropertySink` は新設しない） | `TestSetPropertySuggestions`, `TestHandleEditorSetPropertyQuaternion`, `TestSetPropertyGameObject`, `TestApplyPropertyValueTypes` |
| **H-5** | #367 | clean-win | UiElement | `UiElementTypeAllowlist`（T1）, `UiFontMissingMessage`（T1） | `TestCreateUiElementSource`, `TmpFontMissingMessageBranching` |
| **H-6** | #368 | clean-win | RunScriptCompile（validators） | `RunScriptDeadline`, `RecompileTimeoutValidator`, `RunScriptCompilePendingCodeSelector`（いずれも T2a 純関数） | `TestCompileTimeoutRequestField`, `TestRecompileAndWaitTimeoutBoundCheck`, `TestRunScriptCompilePendingResponseDeadlinePath` |
| **H-7** | #369 | clean-win | RunScriptCompile（recompile 解決 / redaction） | `RecompileResolutionGuard`, `RecompileOutcomeClassifier`, `ScheduleFailureEnvelope`, `ReimportDiagnostic`（いずれも T2a — 純関数 / 非インターフェースの小クラス） | `TestRecompileAndWaitOutcomeSync`, `TestRecompileAndWaitDeadlineWatchdog`, `RecompileScheduleFailedSanitization`, `RecompileForceReimportDiagnosticRedaction` |
| **H-8** | #370 | clean-win | core（dispatch / DTO） | `ActionRegistry`（T2a）, `EditorControlRequest` の `<Compile Include>` harness 取り込み | `TestUdonSharpActionWiring`, `TestUdonSharpRequestFields` |
| **H-9** | #371 | **opportunistic** | Screenshot | `CameraState`（値オブジェクト）+ `ICameraStatePort`（**T2b** interface seam） | `EditorControlBridgeScreenshotCameraStateRestoreTests` |
| **H-10** | #372 | **mixed** | PrefabStage | `StageHierarchyPathLogic`（T1、clean-win）, `ClosePrefabPlanner` + `IPrefabStagePersistencePort`（**T2b** interface seam、opportunistic） | `PrefabStagePersistFixSourceInvariantTests` |
| **H-11** | #373 | clean-win | UnityPatchBridge（Prefab） | `PrefabApplyRejectionEnvelope` + 値オブジェクト `PrefabApplyFailure`（T2a 純関数） | `TestPrefabApplyRejectionEnvelopeSource` |
| **H-12** | #374 | **opportunistic** | VRCSDKUploadHandler | `IUploadWorkflow`, `ILoginGate`, `IBuilderProbe`（いずれも **T2b** interface seam） | `TestHandleAsyncFullProtection`, `TestLoginPollingInsideTryCatch`, `TestLoginPollingInHandleAsync`, `TestResolveBuilderAsyncRetry` |

**N = 11**（H-2…H-12 = #364–#374、本表 merge 後に起票済み）。H-6/H-7 は RunScriptCompile を 2 分割した — 単一 concern に 7 つの
独立抽出が集中し 1 PR の粒度を超えるため（validators 群 / 解決ガード・redaction 群）。

### 6.1 T2b は opportunistic — standalone 移行 PR にしない

H-9 / H-10(port 部) / H-12 は production コードに interface を新設する。当初は「着手前に
earned か / 回帰実績 / 代替手段を justify する decision_required ゲート」を置いたが、
2026-05-16 の検討で次の 2 点が判明し、**opportunistic 方式**へ切り替えた。

**(1) 「回帰実績」基準は不 falsifiable だった。** ゲートは「source-text テストが振る舞い
不変リファクタを壊した実績」を判定材料にしたが、5 並列の AI レビューと「同一コミット
内でのテスト追従更新」がコストを上流で吸収するため、`git log` 上の破損カウントはレビューが
機能する限り恒久的に ≈0 を返す。実測（T2b 3 concern の全履歴 — Screenshot / PrefabStage /
VRCSDKUploadHandler — を走査、grep 起因の破損 0 件）もこれを裏付けた。通り得ない基準は
ゲートにならない。注意力コストは実在するが `git log` には映らない（破損ではなくレビュー
工数として支払われる）。

**(2) T2b は注意力コストの正しいレバーではない。** source-text 装置 85 クラスのうち 55 は
消せない T3 恒久（partial 構成・定義唯一性など）。毎 PR の「このリファクタは装置と整合するか」
の注意力負荷は、この消せない T3 質量に支配される。T2b が装置から外すのは 6 クラス（≈7%）で、
レビュー負荷をほぼ動かさない。一方コストは production 最ホット領域への interface 5 本の新設。
H トラック中で損益が最も悪い。注意力コストへの本命の対処は **#358 cleanup**（§2.2 のコメント
除去＋構造不変条件のみへの純化で、残る装置を refactor-robust にする）であり、T2b ではない。

**方式**: H-9 / H-10(port 部) / H-12 を予定された移行 PR として持たない。**次にその concern の
`.cs` を実機能・実バグ修正で触る PR の一部として seam を導入する。** Bridge は最ホット領域の
ため、この trigger は必ず発火する。trigger が「破損実績」（来ない）でなく「次の実作業」（必ず
来る・具体的）なので、これは無期限保留ではなく**条件付き前倒し**。設計コンテキストが既に頭に
載った状態で入れるためコストが潰れ、「抽象化は 2 つ目の具体的ユースケースが確認できてから」
（2 つ目＝当該ハンドラを実作業で触ること）も満たす。behavioral 品質メリット（特に PrefabStage
close-handler と VRCSDK の async-void 例外安全）はその実装時に回収される。

**例外的に standalone で前倒し実装してよい場合**: (a) それらのハンドラ群の再編を近い将来
計画している、(b) AI レビューでその特定クラスが体感的に痛い。いずれも該当しなければ
opportunistic を既定とする。

clean-win の 8 issue（H-2…H-8, H-11）は T1/T2a のみで上記と無関係 — 予定された移行 PR として
先行着手してよい。

H トラックの後続: **#358 cleanup** は当初「全 H-2…H-12 完了」に依存していたが、T2b 3 本が
opportunistic 化したため、依存を **clean-win 8 本（H-2…H-8, H-11）+ H-10 の T1 部**の完了に
変更する。#358 は注意力コスト削減の本命（残る T3 装置を refactor-robust 化）であり、clean-win
完了後に繰り上げて着手する。その後 **#359 docs**（Tier3 定義を純化後の実態へ同期）。
opportunistic な T2b は trigger 発火時に各々が完了し、完了分は #358 / #359 へ追って反映する。

## 7. 本表が前提とする判断

- **N=11 の根拠**: T1/T2 アサーションを持つ concern は 10、うち RunScriptCompile のみ抽出数
  過多で 2 分割。T3 のみの concern（Hierarchy / Components / SaveInstantiate / UnityIntegrationTests /
  patch bridge の layout-only concern 群）は移行 issue を持たない。
- **T2 を T2a / T2b に細分（2026-05-16 レビュー反映）**: 当初の T2 一括分類は「ほぼタダの
  値 seam」と「production に interface を新設する設計判断」を同列に並べていた。後者は
  リポジトリ最ホットのファイルにテスト都合の間接層を持ち込む（test-induced design damage）
  リスクがあるため、T2b として分離。clean-win（T1/T2a）20 は予定された移行 PR で先行、
  T2b 6 は §6.1 の通り opportunistic（次の実作業に相乗り）。
- **T2b を opportunistic にした根拠（2026-05-16）**: 当初 T2b に置いた decision_required
  ゲートは判定材料が「source-text テストの破損実績」だったが、これは 5 並列 AI レビューが
  コストを上流で吸収するため恒久的に ≈0 を返す不 falsifiable な基準だった（実測でも T2b
  3 concern の grep 破損 0 件）。かつ T2b が装置から外せるのは 85 クラス中 6（≈7%）で、毎 PR
  の注意力コスト（消せない T3 恒久 55 クラスに支配される）をほぼ削らない。interface 5 本
  新設のコストに見合わないため、standalone 移行をやめ実作業相乗りに切り替えた。注意力
  コスト削減の本命は #358（残る装置の refactor-robust 化）と位置づける。
- **共通 seam への収束**: §4.5（`EditorScriptPathClassifier` が 3 クラスを cover）と §4.13
  （`IUploadWorkflow` が 2 クラスを cover）のように、1 抽出が複数 source-text クラスの
  behavioral アサーションを同時に retire するケースを issue grouping に織り込んだ。
- **Screenshot は部分完了済み**: view-acceptance（`ViewAllowlistClassifier` / `ScreenshotViewKindTests.cs`）は
  移行済。H-9 は camera-state restore のみを扱う。
- **CS-error-code テストは T3 据え置き**: CS0117/CS1501/CS0246 regression は Editor-host build
  （UTF EditMode テスト）があれば compile error で捕捉できる性質だが、本リポジトリは GameCI /
  batchmode を明示的に不採用（#290）のため、当面は T3 source-text として残す。
