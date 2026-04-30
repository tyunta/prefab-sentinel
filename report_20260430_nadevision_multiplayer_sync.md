# Prefab Sentinel UX Report: NadeVision Multiplayer Sync

**Date:** 2026-04-30
**Version:** 0.5.152 (plugin/bridge)
**Environment:** WSL2 + Unity 2022 (auto-detected) + VRChat Worlds SDK + UdonSharp + VVMW
**Task:** VRChat ワールドの動画プレイヤーパッケージにマルチユーザ同期 (URL/再生時刻/再生状態の sync) を追加

セッション全体: spec → plan → 実装 (A1-D3 全 14 タスク) → ClientSim 配線完了

---

## Summary

UdonSharp ベースの新規 SyncBridge コンポーネントを設計・実装し、World Space Canvas (URL 入力 UI) を新規構築、既存の PlaybackUi に Play/Pause + Resync ボタンを追加、全 Bridge 参照を配線するマルチコンポーネント連携を **ほぼ全て MCP 経由** で完遂した。最大の摩擦点は `editor_run_script` の bridge state stuck 問題と、UdonSharp の Program Asset 検出キャッシュ問題。

---

## What Worked Well

### batch ツール群 (Excellent)

`editor_batch_create` + `editor_batch_add_component` + `editor_batch_set_property` の 3 点セットで scene UI 構築の大半 (~30+ オブジェクト/コンポーネント) を効率的に完遂。

| Tool | Usage | Assessment |
|------|-------|------------|
| `editor_batch_create` | UrlInputPanel 配下に Empty x4 (PcUrlField/SubmitButton/DeniedHint/PermissionLabel) 一括作成 | 1 リクエストで複数 GO 作成、Undo グループ化で安全 |
| `editor_batch_add_component` | 1 リクエストで 6 コンポーネント (RectTransform/Image/VRCUrlInputField/Button/Text x2) 追加 | 完全修飾名 (`UnityEngine.UI.Text` 等) なら確実 |
| `editor_batch_set_property` | text 内容・参照配線・サイズ設定を 1 回で 8〜10 プロパティ更新 | object_reference の `:ComponentType` サフィックスが必須 (型曖昧性回避) |

### `:ComponentType` サフィックスでの型解決 (Excellent)

stripped prefab instance や同型複数コンポーネントが混在する scene でも `/Path/To/GO:ComponentTypeName` で正確に解決。

例:
- `/NadeVision/VVMW (On-Screen Controls):Core` (VVMW Variant prefab 内の Core を取得)
- `/NadeVision/SyncBridge:NadeVisionSyncBridge` (UdonSharp proxy MonoBehaviour 取得)
- `/NadeVision/SyncBridge:UdonBehaviour` (バッキング UdonBehaviour 取得)

サフィックスなしだと "GameObject has no Core component" のような誤判定が発生 (stripped instance の影響と推測)。

### Editor メニュー helper パターン (Workaround だが Good)

`editor_run_script` が compile cycle で詰まった際、`Assets/Editor/_NadeVisionWireMultiplayerSync.cs` に `[MenuItem]` 付き静的クラスを **永続ファイルとして** 配置し `editor_execute_menu_item` で実行することで、複雑なロジック (UnityEvent 永続リスナー追加・SetLayerRecursive・proxy 重複削除) を MCP から実行できた。

`editor_run_script` の代替として有効。

### 検査ツール (Good)

- `inspect_wiring` (udon_only=true): UdonSharp コンポーネントの `is_udon_sharp: true` フラグで配線状態を一覧化
- `editor_console (error filter, since_seconds)`: 直近のコンパイルエラー・ランタイムエラーをノイズなく取得
- `get_unity_symbols(expand_nested=true)`: VVMW Variant prefab の入れ子構造を可視化、`[Unresolved: <guid>]` で broken ref も検出

---

## What Didn't Work Well

### 1. `editor_run_script` の bridge state stuck (CRITICAL)

**症状:** セッション中盤以降、`editor_run_script` を呼ぶと **永続的に** `EDITOR_CTRL_RUN_SCRIPT_COMPILE` "Script compilation is still in progress after AssetDatabase.Refresh; a domain reload is pending. Retry after Unity finishes compiling." を返す。

実際には Unity 側のコンパイルは完了している (`Library/ScriptAssemblies/BeeDriver.json` 不在を確認、`UdonSharp Compile finished` ログあり)。

**再現性:** 高い。`editor_run_script` を 2-3 回呼んだ後、長時間 (15-90 秒) 待機しても解消せず、`editor_recompile` / `editor_refresh` でも復旧しない。

**影響:** セッション中盤で完全に使用不可になる。今回の Phase C (Editor 経由でしか出来ない複雑ロジック: UnityEvent persistent listener 追加・proxy 削除等) が手動完成手順に転落しかけた。

**推定原因:** `editor_run_script` が temp .cs を書く → compile trigger → poll → 早期に "still compiling" 判定 → temp 削除 → 次の呼び出しで再発、のループに陥っている可能性。bridge 内の "compile complete" 判定が overly conservative。

**回避策:**
1. **Editor メニュー helper を永続化** — `Assets/Editor/_*.cs` に置いて `editor_execute_menu_item` で実行
2. 個別 MCP (`editor_create_empty`, `editor_add_component`, `editor_batch_set_property`) に分解

**提案:**
- `editor_run_script` の compile-pending 判定を緩和 (現状 1 回の poll で諦める印象)
- 一定回数失敗したら自動的に AssetDatabase の状態を強制リセット
- **公式の推奨パターン** として「Editor メニュー helper + execute_menu_item」を knowledge に追加

### 2. UdonSharp Program Asset 検出キャッシュの問題 (High)

**症状:** `editor_create_udon_program_asset` で .asset を生成 → `editor_add_component` で UdonSharp class を attach → `UdonSharpProgramAsset not found for {className}. The component was added as a regular MonoBehaviour, not UdonBehaviour. Run editor_create_udon_program_asset first, then retry.` が出続ける。

`Refresh All UdonSharp Assets` メニュー実行後でも改善せず。`Found 84 assets. Last: 84, This: 84 — Completed 0 refresh cycles` と出るので UdonSharp 内部の cache が更新されていない。

**回避策:** UdonSharp の `AddUdonSharpComponent` 経由を諦め、手動 wiring パターンに切替:
1. `editor_add_component` で `VRC.Udon.UdonBehaviour` を追加
2. `editor_set_property programSource = .asset` で UdonBehaviour に program asset を割り当て
3. `editor_add_component` で UdonSharp class (proxy MonoBehaviour として追加される、警告は無視)
4. `editor_set_property _udonSharpBackingUdonBehaviour = /Path:UdonBehaviour` で proxy → UB を link
5. `editor_set_property` で各 public field を配線

**副作用:** この手順を踏むと proxy MonoBehaviour と UdonBehaviour の対が duplicate しやすい (1 回目で warning 経由で MonoBehaviour、2 回目で proper UdonSharp が認識される等)。今回 `/NadeVision/SyncBridge` に NadeVisionSyncBridge proxy + UdonBehaviour が **2 セット** 残った。

**提案:**
- `editor_add_component` の UdonSharp 検出ロジックを補強 — `script_map` に最新の `.asset` 情報を反映するためのキャッシュ無効化フックを追加
- 既存 UdonBehaviour + programSource 配線済みの GO に対して同じ UdonSharp class を re-add した場合、duplicate を作らずに既存と link する
- UdonSharp proxy の重複検出と一括除去ヘルパー (`editor_dedupe_udonsharp_proxies` のような新ツール)

### 3. `editor_set_property` の `component_type: "GameObject"` 拒否 (Medium)

**症状:** GameObject の m_IsActive を設定するため `component_type: "GameObject", property_name: "m_IsActive"` を指定すると `System.NotSupportedException: The invoked member is not supported in a dynamic module` が `ResolveComponentType` で発生。

**影響:** `SetActive(false)` を MCP から実行できない。今回 DeniedHint の初期非アクティブ化を Editor メニュー helper に逃がした。

**提案:**
- `ResolveComponentType` が `Component` 派生型のみを期待しているなら、`GameObject` を特例として `m_IsActive` / `m_Layer` / `m_Name` / `m_TagString` の設定をサポートする
- 専用ツール `editor_set_gameobject_property` を追加

### 4. UI Text の `m_FontSize` プロパティ名違反 (Low)

**症状:** `editor_batch_set_property` で `component_type: "Text", property_name: "m_FontSize"` を指定すると `Property not found: m_FontSize on Text`。

**真の名前:** Unity UI の Text コンポーネントは `m_FontData.m_FontSize` (nested struct)。

**影響:** 1 回の trial-and-error で気付き、今回はテキストサイズを Inspector 後調整で済ませた。

**提案:**
- `editor_set_property` のエラーメッセージに「did you mean `m_FontData.m_FontSize`?」のような近い候補を出す
- workflow-patterns.md の "実運用で学んだこと" に Unity UI の頻出プロパティパス (Text, Image, etc.) を追記

### 5. Editor スクリプト編集の recompile pickup 不安定 (Medium)

**症状:** `Assets/Editor/_*.cs` を `Edit` ツールで編集後、`editor_refresh` + `editor_recompile` を呼んでも、次の `editor_execute_menu_item` 実行時に **古いコンパイル結果** で動作することがある。

具体例: 今回 helper の Wire メソッドにロジックを追加 → recompile → 実行すると stack trace が `_NadeVisionWireMultiplayerSync.cs:82` を指す (古いバージョンの行番号)。

**回避策:** ファイル先頭にコメントを追加するなど、ファイルサイズが大きく変わる編集を行うと正しく recompile される。`touch` だけでは不十分なケースあり。

**推定原因:** Unity の AssetDatabase が timestamp ベースで変更検知している場合、Edit ツールが timestamp を更新しない可能性。

**提案:**
- `editor_recompile` 実行時に対象アセットを明示的に Reimport するオプション
- workflow-patterns.md に「Edit 後の recompile pickup を確実にするには substantive content change か file replace が望ましい」を追記

---

## Workflow Insights

### 大規模な scene 構築のフロー (今回成立したパターン)

```
1. C# script (.cs) 作成 → Write
2. .cs.meta も作成 (GUID は uuid4 hex)
3. editor_create_udon_program_asset で .asset 生成
4. editor_recompile + Monitor で compile 待機
5. UdonSharp コンポーネント配置:
   a. editor_create_empty で GameObject 作成
   b. editor_add_component で VRC.Udon.UdonBehaviour 追加
   c. editor_set_property programSource = .asset
   d. editor_add_component で UdonSharp class proxy 追加 (警告は無視)
   e. editor_set_property _udonSharpBackingUdonBehaviour = /Path:UdonBehaviour
6. UI 構築:
   a. editor_batch_create で子 GameObject 一括作成
   b. editor_batch_add_component で UI コンポーネント一括追加
   c. editor_batch_set_property で text/参照/レイアウト一括設定
7. UnityEvent (Button.onClick 等) 配線:
   - editor_run_script が使えれば一発、駄目なら Editor メニュー helper
8. layer 設定・SetActive 等の GameObject 操作:
   - 同様に Editor メニュー helper 経由
9. editor_save_scene + inspect_wiring で検証
```

### Editor メニュー helper パターン (新規・推奨)

`editor_run_script` の代替として:

```csharp
// Assets/Editor/_MyHelper.cs
using UnityEditor;
public static class MyHelper {
    [MenuItem("Tools/MyTool/DoStuff")]
    public static void DoStuff() {
        // 任意の Editor C# ロジック
    }
}
```

**MCP 経由実行:**
1. `Write` でファイル作成 → `editor_refresh` → `editor_recompile`
2. Monitor で compile 待機
3. `editor_execute_menu_item("Tools/MyTool/DoStuff")` で実行
4. `editor_console` でログ確認

利点: scope が永続化され、`editor_run_script` の bridge state stuck の影響を受けない。複雑なロジックを再利用可能。

欠点: temp ファイル想定の `editor_run_script` と違い、自動 cleanup なし。完了後に手動 (or `Bash rm`) 削除が必要。

### Stripped Prefab Instance 内のコンポーネント解決

prefab instance の内部 GameObject は scene YAML 上で `stripped` MonoBehaviour として現れる。`get_unity_symbols(expand_nested=true)` で nested prefab を展開すると本来の構造が見える。

`editor_set_property` で stripped 内のコンポーネントを参照する際は、scene 上の **実際のパス** (`/Root/InstanceName:ComponentType`) で指定する。Prefab の inspect_hierarchy が示す `/Root/Core` のような nominal path とは別である点に注意。

---

## Final State

### Package commits (Tyunta/NadeVision)
14 commits 全て成功:
- A1-A4: NadeVisionSyncBridge.cs (4 commits) — UdonSynced fields, ApplyLocally, owner ops, drift loop
- B1-B4: ResolutionSelectPanel 改修 / UrlInputPanel.cs / PlaybackControlButtons.cs / Program Asset 生成
- D2-D3: README + ハンドオフ + テスト計画
- + plan / spec / fix commits

### Scene state (parent project)
- `/NadeVision/SyncBridge`: UdonBehaviour + NadeVisionSyncBridge proxy + targetCore 配線済 (1 セット duplicate あり、要手動削除)
- `/NadeVision/UrlInputPanel`: 新規 World Space Canvas, VRCUrlInputField + Submit + DeniedHint + PermissionLabel + UdonBehaviour + UrlInputPanel proxy + 全フィールド配線済
- `/NadeVision/PlaybackUi`: PlaybackControlButtons proxy + UdonBehaviour + Btn_PlayPause + Btn_Resync 追加
- `/NadeVision/PlaybackUi/ResolutionSelectPanel`: bridge 配線済 (seek-after-load 有効)
- 全 UI Layer = NADE_VISION (29)
- 全 Button onClick = SendCustomEvent 永続リスナー配線済
- DeniedHint 初期非アクティブ

### 既知の小さな残課題
- `/NadeVision/SyncBridge` に NadeVisionSyncBridge proxy + UdonBehaviour が duplicate 残存 → ユーザに Inspector 手動削除を依頼
- scene の `/NadeVision` は prefab instance ではないため、変更は scene-only。NadeVision.prefab に反映するには手動操作

---

## Suggestions to prefab-sentinel

1. **`editor_run_script` の compile-pending 判定を緩和** — 現状 false-positive が高頻度発生。実際の compile 状態を `EditorApplication.isCompiling` 等で再判定するロジック追加。

2. **Editor メニュー helper パターンを workflow-patterns.md に追加** — 今回の knowledge update PR に含める。

3. **UdonSharp proxy duplicate 問題の根治** — `add_component` を同じ class で 2 度呼んだときの挙動を明示的に: error or idempotent。

4. **`m_IsActive` / `m_Layer` 設定の専用ツール** — GameObject レベルのプロパティは現状 `editor_run_script` 必須で UX が悪い。

5. **Edit 後の recompile pickup の確実性向上** — `editor_recompile` に `force_reimport=True` フラグなど。

6. **エラーメッセージの "近い候補" 提案** — `Property not found: m_FontSize on Text` → `did you mean 'm_FontData.m_FontSize'?` のような help。

---

**Plugin/Bridge:** 0.5.152
**Total session duration:** ~3 時間 (spec/plan 含む)
**MCP tool calls:** ~80 回 (うち 5 回 editor_run_script で stuck、その他は成功)
