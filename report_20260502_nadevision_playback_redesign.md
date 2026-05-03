# Prefab Sentinel UX Report: NadeVision Playback Redesign

**Date:** 2026-05-02
**Version:** 0.5.152 (plugin/bridge)
**Environment:** WSL2 + Unity 2022 + VRChat Worlds SDK + UdonSharp 2023.12+ + VVMW (VizVid 2026-05 build)
**Task:** NadeVision (180° SBS Skybox 動画プレイヤー) の **椅子着座ベース個別ロード + 共有時計合流モデル** への再設計、23 タスク 8 phase 完走

セッション全体: 既存 EyeSphere/dome 系の撤去 → spec 書き直し → Plan モード → 実装 (Controller / Bridge / FadeController / StationListener / Chair.prefab / NadeVision.prefab 大改修) → ClientSim runtime smoke → multi-PC sync 検証 → HMD 検証 → cleanup

---

## Summary

3-state machine (Idle/Sat/Watching) と痩せた SyncBridge (URL + baseline のみ) で「全員強制ロード撤廃 + 個別ロード + Watching のみ Fade」モデルに再設計し、VVMW Core (synced=false) の autoplay 強制問題を listener + 1-frame 遅延 Pause で吸収、UdonSharp の **複数 synced field deserialization race** を `OnDeserialization` パターンで解決。最大の摩擦は **bridge protocol v1↔v2 mismatch** と、それに付随する **CS0104 'Assembly' ambiguous** で MCP 経由の recompile が一切通らなくなる症状。

---

## What Worked Well

### `editor_batch_*` 群 (Excellent, 前回と同評価)

`editor_batch_create` + `editor_batch_add_component` + `editor_batch_set_property` + `editor_batch_set_material_property` の組合せで、Chair.prefab + ViewingZone + global PlaybackUi (Canvas + 7 buttons + Slider + Toggle) の構築をほぼ 1 リクエスト/論理単位で完遂。

### `inspect_hierarchy` + `inspect_wiring` で構造把握 (Excellent)

「Find("Core") が誤指定 (VRC_UiShape canvas root が `/NadeVision/VVMW (On-Screen Controls)` だった)」のような階層名 typo は `inspect_hierarchy` で実 GameObject 名を確認すれば回避できる。文字列ベースの探索を **必ず inspect で実値確認してから** やる規律を memory として記録した (`feedback_editor_script_no_auto_search.md`)。

### `validate_refs` (Excellent, 必須)

Phase 7 の最終 cleanup 前後で必ず走らせ、broken refs 0 を確認。Editor 補助 .cs を 3 ファイル削除した直後にも走らせて配線が壊れていないことを保証。

### `editor_recompile` + DLL mtime 監視 (Excellent)

`editor_recompile` は protocol v1 のうちは安定。完了待ちは `Library/ScriptAssemblies/Assembly-CSharp.dll` の mtime 監視で `until` ループ + `run_in_background` パターン (memory `feedback_unity_compile_wait_dll_mtime.md` に記録)。固定 sleep より早く確実。

---

## What Hurt the Most

### bridge protocol v1↔v2 mismatch + CS0104 連鎖 (Critical)

セッション中盤で `editor_recompile` が `UNITY_BRIDGE_PROTOCOL_VERSION` で fail するようになり、`deploy_bridge` で 9 ファイル再展開したところ **deploy したばかりの bridge .cs に CS0104 'Assembly' ambiguous** が出て **Assembly-CSharp-Editor.dll が compile failure** に。結果:

1. MCP は protocol v2 を期待
2. しかし Unity 側 bridge は CS0104 で旧 (v1) のまま生き残る
3. recompile / set_property / 各種 editor MCP が全滅
4. user が手動で .cs を修正 → Unity focus → ようやく v2 bridge 起動

#### 該当ファイル
`Packages/idv.jlchntoz.vrcw-foundation/Runtime/...` 由来ではなく、**bridge 配布物自体** の `PrefabSentinel.UnityEditorControlBridge.cs:2702 / 2713`:

```csharp
foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
//      ~~~~~~~~ ambiguous between UnityEditor.Compilation.Assembly and System.Reflection.Assembly
```

`AppDomain.CurrentDomain.GetAssemblies()` の返り値型は `System.Reflection.Assembly[]` で確定しているので、bridge .cs 配布前に **`System.Reflection.Assembly` で修飾** すべき。

#### 提案
- **deploy_bridge は CI で UnityEditor + System.Reflection の両 using 環境でコンパイル検査** してから配布
- bridge 配布物に CI を組んで、サンプル U# プロジェクト (UnityEditor.Compilation を using してる) で compile が通ることを保証
- protocol mismatch 時の MCP 側エラーメッセージに **「bridge .cs の compile error が出ている可能性」** を示唆するヒントを足す (Unity console を MCP 経由で読める手段が欲しい)

### UnityEventFilter による Slider/Toggle の binding strip (High)

VRChat Worlds SDK の build pre-process である `UnityEventFilter` が `UnityAction<float>` (Slider.OnValueChanged) と `UnityAction<bool>` (Toggle.OnValueChanged) の persistent listener を **strip する** ため、Inspector で配線しても build 時に消える。VRChat 公式 [allowlist](https://creators.vrchat.com/worlds/udon/networking/network-components/) で Object/string 引数の SendCustomEvent 系のみ allowed。

#### 解決
Slider/Toggle の `onValueChanged` を直接配線せず、**string 引数の SendCustomEvent** で Controller の `OnVolumeChanged()` (引数なし) を呼ぶようにし、Controller 側で `volumeSlider.value` を読みに行く構成に変更。`UnityEventTools.AddStringPersistentListener` (Editor) で配線。

#### 提案
prefab-sentinel の knowledge に「VRChat allowlist と UnityEventFilter の strip 対象」を 1 ファイルにまとめると同種のハマりを減らせる。`udonsharp.md` の L2 networking 節か新規 `vrchat-event-binding.md` あたりに収納したい。

### VRC_UiShape の BoxCollider Z 強制 1.0 (Medium)

`VRC_UiShape` (World Space Canvas のレーザー hit 用) が **Awake で BoxCollider のサイズ Z を 1.0 (canvas-local unit) に強制リサイズ** する。Canvas localScale=1 で配置すると 1m 厚の collider が出来てしまい、UI 表面までレーザーが届かない。

#### 解決
Canvas localScale=0.01 にして sizeDelta を pixel 単位で指定する **VRChat WS Canvas 慣習** に合わせる。これで Z=0.01m となり、UI 表面の薄い hit zone が出来る。bug ではなく仕様 (memory `feedback_vrc_uishape_z_autoresize.md` に記録)。

#### 提案
prefab-sentinel knowledge に既存の `vrchat-sdk-worlds.md` があるので、World Space Canvas 構築節を 1 つ追加して、scale=0.01 + sizeDelta-in-pixels の hard rule を明文化したい。validate_refs / inspect_wiring で「Canvas localScale != 0.01 かつ VRC_UiShape あり」を warning する linter があれば早期発見できる。

### VVMW Core synced=false の autoplay 強制 (Medium)

`Core.synced=false` のとき `OnVideoReady` で **無条件に `activeHandler.Play()`** が走る (Core.cs:567-571)。listener で `Core.Pause()` を呼んでも `Play()` が後勝ち。1 frame 遅延 Pause で対処したが、autoplay リーク (~16ms の audio) は許容するしかない。

#### 提案
今回の知見を `knowledge/vvmw.md` に新規ファイルとして追加した。VRChat 動画プレイヤー周辺は依存先パッケージごとに挙動差異があるので、prefab-sentinel knowledge に **依存パッケージ別ファイル** を増やすパターンを推奨したい。

### UdonSharp 複数 synced field の deserialization race (Critical)

`[UdonSynced] _syncedPcUrl` (VRCUrl) と `[UdonSynced, FieldChangeCallback] _baselineServerTimeMs` (long) を **同じ SubmitUrl 内で両方更新** すると、remote 側で deserialize 中、`_baselineServerTimeMs` の callback が **`_syncedPcUrl` 未到着のタイミング** で fire し `pc=''` で abort する。

VRChat 公式 docs ([Networking Tips & Tricks](https://udonsharp.docs.vrchat.com/networking-tips-&-tricks/)) に明示の警告がある:

> "if you have multiple networked variables, other networked variables may not be updated yet when a [FieldChangeCallback] happens."

#### 解決
`OnDeserialization(DeserializationResult)` で全 synced field 整合後に処理するパターンに置換 + owner は `SubmitUrl` 末尾で明示呼び出し + `_lastSeenBaselineServerTimeMs` で重複発火抑制。実装は `Packages/idv.jlchntoz.vvmw/Runtime/VVMW/Core.cs:704` (VVMW Core 自体も同じパターン) を参考。

#### 提案
`knowledge/udonsharp.md` の `[FieldChangeCallback]` 節に、「複数 synced field の整合タイミング判定 → OnDeserialization で待ち合わせ」の対比表とサンプルコードを追記した。今後同じ罠を踏むユーザーが多いと思うので prefab-sentinel guide スキルから直接参照されると良い。

### `RenderSettings.skybox` 復元の Inspector 配線忘れ (Medium)

`Context.defaultSkyboxMaterial` を Inspector 配線忘れで `null` のまま放置すると、Stop 時に Skybox が空 (黒) になる。Inspector 配線必須項目を増やすほどヒューマンエラーが増える。

#### 解決
Inspector 配線を捨てて `Start()` で `_originalSkyboxMaterial = RenderSettings.skybox` を **自動捕捉** する方式に変更し、`context.defaultSkyboxMaterial` フィールド自体を Context から削除。**「未配線を吸収するフォールバックを足さない」** という AGENTS.md の原則に反するように見えるが、ここは「世界既定 skybox を保つ」という不変条件を `RenderSettings.skybox` から直接取れる場合の自然な書き方であり、仕様外のフォールバックではない (= "fail-fast" を曲げていない)。

#### 提案
prefab-sentinel knowledge に「Inspector 配線 vs runtime auto-capture の判断基準」のような節があると良い。基本 fail-fast、ただし RenderSettings 等の **Unity 側で必ず存在する API** から取得できるものは自動捕捉が自然、というメタ知見。

---

## Process Observations

### Sub-agent 委譲の効果

Chair.prefab 構築と NadeVision.prefab への新 UB 追加は subagent (general-purpose) に委譲して並列実行。subagent はメイン側が握っていない長大な MCP 結果を消化してくれるので、メインの context 圧迫が大幅に減った。一方で subagent が editor_set_property NotSupportedException で stuck したケースもあり、**MCP 操作で「set_property 系 + run_script 系」が混ざると subagent でも詰まる** という傾向は今回も同じ。

### Plan モードで spec を書き直した効果

セッション初期に「ドーム方式 → EyeSphere 方式 → Skybox 方式」「permissionMode → 着座ベース」「Suspended state → Sat に統合」という設計大幅変更を spec ドキュメントから書き直してから実装に入ったので、途中で「やっぱり違う」と立ち戻る回数が大幅に減った。Plan モードを「実装に入る前の design.md 書換 + 23 task 分解」まで使い切るのが効率的。

### Diagnostic Debug.Log 追加→削除のサイクル

「1 回目 Submit が反映されない」症状を切り分けるため、UrlInputPanel/SyncBridge/Controller の hot path に **string concat 込みの Debug.Log を 4 段** 一時追加 → user に console を貼ってもらう → ログから state=0 (Idle) が判明 → さらに OnDeserialization 経路で `pc=''` を発見 (= deserialization race) という展開で、ログがなければ絶対切り分けられなかった。Phase H 完了時に全削除。

#### 提案
prefab-sentinel に「diagnostic log 仮設パターン」のテンプレートが skills か knowledge にあると、毎回手書きで Debug.Log を埋め込まず `[ComponentName] MethodName: key=value` 形式で統一できる。

---

## What I Want From prefab-sentinel

### 1. Unity Console を MCP 経由で読みたい

今回ユーザーに console をスクショ/コピペしてもらう往復が何度も発生。`editor_console` は既にあるが、**実際の Console error / warning / log を Phase 別に絞ってフィルタ取得** したい。`editor_console --since-last-call --severity=warning,error` のような口があると診断速度が桁違いに上がる。

### 2. `recompile + wait until ready` の 1 コマンド

`editor_recompile` 後の DLL mtime 監視は手動で書く必要があり、毎回 `until` ループ + `run_in_background` をセットアップしている。`editor_recompile_and_wait` のような同期 API があれば、recompile 完了の待ち合わせを user が考える必要がなくなる。

### 3. UdonSharp `.asset` 再生成の自動化

C# ソース変更 → `.asset` の program data 再生成は `editor_recompile` 直後に走るが、**bridge protocol mismatch 等で recompile 自体が通らないと .asset が古いまま** にもなる。`.asset` の最終更新時刻を validate_refs/inspect_wiring の出力に含めて、「.cs より古い .asset がある」を warning する仕組みが欲しい。

### 4. VRChat allowlist linter

UnityEventFilter で strip される配線パターン (Slider/Toggle の persistent listener など) を **build 前に検出** したい。`validate_runtime` 系で「VRChat allowlist 違反の listener 配線」を warning するチェックがあると、build → strip → 動かない、の 3 段階を 1 段階に短縮できる。

### 5. 依存パッケージ別 knowledge の標準テンプレ

`knowledge/vvmw.md` を新規追加したが、VVMW 以外にも UdonChips / SCSS / Multi-Layer World Manager 等の VRChat エコシステムには重要な依存パッケージがあり、それぞれに「この package に固有の挙動・落とし穴」がある。prefab-sentinel に **knowledge_template_for_dependency_package.md** のようなひな形があれば、ユーザーが踏んだ罠を体系的に記録しやすい。

---

## Conclusion

bridge protocol v1↔v2 mismatch (+ CS0104) 以外は概ね smooth に進んだ。MCP 経由の prefab 編集は今回も Excellent で、特に batch ツール群と inspect 系の組合せは **手作業の Inspector ぽちぽちより速い**。

最大の win は `OnDeserialization` パターンの発見で、これは VRChat 動画系のあらゆる UB で再利用できる知見。`knowledge/udonsharp.md` に追記したので、次回 prefab-sentinel guide 経由で他のユーザーが同じ罠を回避できるようになった。

---

## Memory Files Saved (NadeVision project memory)

- `feedback_udonsharp_fieldchangecallback_owner.md` (上書き) — 複数 synced field deserialization race と OnDeserialization パターン
- `feedback_unity_compile_wait_dll_mtime.md` — recompile 完了は DLL mtime 監視
- `feedback_editor_script_no_auto_search.md` — Editor 補助でも明示パス指定 (Find 系の typo 防止)
- `feedback_vrc_uishape_z_autoresize.md` — VRC_UiShape の BoxCollider Z=1 強制
- `project_nadelens_hdr_capture_clip.md` — NadeLens hemi RT の HDR 化検討
- `project_vvmw_runtime_nre_preexisting.md` (継続使用) — VVMW Switch_Object null material が NRE で halt する既知問題

## Knowledge Files Updated (prefab-sentinel)

- `knowledge/udonsharp.md` — `[FieldChangeCallback]` 節に「複数 synced field deserialization race と OnDeserialization パターン」を追記
- `knowledge/vvmw.md` (新規) — VVMW Core API、event listener、synced=false autoplay 強制、NadeVision 統合パターン
