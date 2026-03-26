# Knowledge Batch Population Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 14 VRChat ecosystem tools の knowledge ファイルを Phase 1+2 で一括作成する。

**Architecture:** サブエージェント並列実行 (4 バッチ × 3-4 並列)。各サブエージェントが 1 ツールの Phase 1 (Web 調査) + Phase 2 (ソース分析) を自律実行し、メインコンテキストがバッチ間でレビュー・コミットする。

**Tech Stack:** WebSearch, WebFetch, Glob, Grep, Read, Write (全て docs-only、コード変更なし)

---

## サブエージェント失敗時のプロトコル

サブエージェントが失敗した場合、以下の手順で対応する:

1. **部分的失敗**: 同一バッチ内の他エージェントの成功分はそのまま採用する。失敗したツールのみ再 dispatch する。
2. **Phase 1 失敗** (Web 検索がタイムアウト等): プロンプトを調整して再 dispatch。2 回失敗したらそのツールをスキップし、次バッチへ進む。スキップしたツールは全バッチ完了後に個別対応する。
3. **Phase 2 失敗** (パッケージパスが見つからない等): パスの存在を手動で確認し、正しいパスで再 dispatch。パッケージ自体が存在しない場合は Phase 1 のみ (confidence: low) で出力する。
4. **品質チェック不合格**: メインコンテキストが直接修正するか、修正指示付きで再 dispatch する。

---

## File Structure

### 削除
- `knowledge/vrchat-sdk-constraints.md` — 空 scaffold、参照なし

### 新規作成 (11 件)
| ファイル | ツール | タイプ |
|---------|--------|--------|
| `knowledge/vrchat-sdk-base.md` | VRChat SDK Base | C |
| `knowledge/vrchat-sdk-avatars.md` | VRChat SDK Avatars | C |
| `knowledge/ndmf.md` | NDMF | C |
| `knowledge/face-emo.md` | FaceEmo | A |
| `knowledge/lilycal-inventory.md` | lilycalInventory | A |
| `knowledge/kawaii-posing.md` | 可愛いポーズツール + Posing System | A |
| `knowledge/floor-adjuster.md` | Floor Adjuster | A |
| `knowledge/copy-scale-adjuster.md` | Copy Scale Adjuster | A |
| `knowledge/avatar-compressor.md` | Avatar Compressor | A |
| `knowledge/vrc-quest-tools.md` | VRCQuestTools | A |
| `knowledge/tex-trans-tool.md` | TexTransTool | A |

### 既存上書き (3 件)
| ファイル | ツール | タイプ |
|---------|--------|--------|
| `knowledge/liltoon.md` | liltoon | B |
| `knowledge/vrcfury.md` | VRCFury | A |
| `knowledge/avatar-optimizer.md` | AAO | A |

### リファレンス (読み取り専用)
- `knowledge/modular-avatar.md` — 品質基準テンプレート
- `skills/knowledge-acquisition/SKILL.md` — Phase 1-2 プロトコル

---

### Task 0: 準備 — scaffold 削除と空ファイル作成

**Files:**
- Delete: `knowledge/vrchat-sdk-constraints.md`

- [ ] **Step 1: 旧 scaffold 削除**

```bash
git rm knowledge/vrchat-sdk-constraints.md
```

- [ ] **Step 2: コミット**

```bash
SKIP_BUMP=1 git commit -m "docs: remove vrchat-sdk-constraints.md scaffold (split into base + avatars)"
```

---

### Task 1: Batch 1 — liltoon, VRCFury, AAO (並列 3 エージェント)

**Files:**
- Overwrite: `knowledge/liltoon.md`
- Overwrite: `knowledge/vrcfury.md`
- Overwrite: `knowledge/avatar-optimizer.md`

- [ ] **Step 1: 3 サブエージェントを並列 dispatch**

各エージェントに以下のテンプレートを適用して dispatch する。`mode: bypassPermissions` を使用し、WebSearch/WebFetch/Read/Glob/Grep/Write を許可する。

**Agent 1a: liltoon (タイプ B — シェーダー)**

```
## タスク
liltoon の knowledge ファイルを Phase 1 + Phase 2 で作成する。

## リファレンス
品質基準として /mnt/d/git/prefab-sentinel/knowledge/modular-avatar.md を読み、
同等の構造・深さを目指すこと。

## プロトコル
/mnt/d/git/prefab-sentinel/skills/knowledge-acquisition/SKILL.md の Phase 1-2 手順に従う。

## ツール種別
タイプ B (シェーダーパッケージ) — MonoBehaviour を持たない。
Phase 2 では .shader / .hlsl / .lilblock ファイルからシェーダープロパティ名と型を抽出する。
.meta からシェーダーアセット GUID を抽出する。
注意: SKILL.md の Phase 2 は [SerializeField] 抽出を指示するが、liltoon はシェーダーパッケージのため該当しない。
上記のシェーダープロパティ抽出手順を SKILL.md の Phase 2 に代えて実行すること。
L3 セクションは「シェーダープロパティ リファレンス」とし、
プロパティ名・型・デフォルト値・用途のテーブルを作成する。
主要プロパティカテゴリ (Base Color, Outline, Emission, Normal Map, Shadow 等) ごとに整理する。

## 対象パッケージ
- 優先: /mnt/d/VRChatProject/Shiratsume_Android_20260107/Packages/jp.lilxyzw.liltoon
- 差分比較: /mnt/d/git/UnityTool_sample/avatar/Packages/jp.lilxyzw.liltoon

## 出力
- ファイル: /mnt/d/git/prefab-sentinel/knowledge/liltoon.md に Write する
- コミットはしない

## 必須セクション
- frontmatter (tool: liltoon, version_tested, last_updated: 2026-03-26, confidence: medium)
- 概要 (L1): liltoon が解決する問題、対応レンダリングパイプライン、VRChat との関係
- シェーダーバリアント一覧 (L1→L2): lil/lilToon, lil/lilToonOutline, Fake Shadow 等
- 操作パターン (L2): 基本セットアップ、アウトライン設定、エミッション設定等 3+件
- シェーダープロパティ リファレンス (L3): プロパティ名・型・デフォルト値テーブル
  - 検証方法を L3 ヘッダーに明記
- 実運用で学んだこと (空セクション)
```

**Agent 1b: VRCFury (タイプ A — コンポーネント)**

```
## タスク
VRCFury の knowledge ファイルを Phase 1 + Phase 2 で作成する。

## リファレンス
品質基準として /mnt/d/git/prefab-sentinel/knowledge/modular-avatar.md を読み、
同等の構造・深さを目指すこと。

## プロトコル
/mnt/d/git/prefab-sentinel/skills/knowledge-acquisition/SKILL.md の Phase 1-2 手順に従う。

## ツール種別
タイプ A (コンポーネントベース) — NDMF プラグイン。
Phase 2 では Runtime/*.cs (または該当する C# ソースディレクトリ) から
[SerializeField] / public フィールドを抽出し、.meta から Script GUID を抽出する。
VRCFury は独自のコンポーネント構造を持つため、ディレクトリ構成を先に探索すること。

## 対象パッケージ
- 優先: /mnt/d/VRChatProject/Shiratsume_Android_20260107/Packages/com.vrcfury.vrcfury
- 差分比較: /mnt/d/git/UnityTool_sample/avatar/Packages/com.vrcfury.vrcfury

## 出力
- ファイル: /mnt/d/git/prefab-sentinel/knowledge/vrcfury.md に Write する
- コミットはしない

## 必須セクション
- frontmatter (tool: vrcfury, version_tested, last_updated: 2026-03-26, confidence: medium)
- 概要 (L1)
- コンポーネント一覧 (L1→L2) — カテゴリ別テーブル
- 操作パターン (L2) — 3+件
- SerializedProperty リファレンス (L3) — Script GUID + propertyPath
  - 検証方法を L3 ヘッダーに明記
- 実運用で学んだこと (空セクション)
```

**Agent 1c: AAO: Avatar Optimizer (タイプ A — コンポーネント)**

```
## タスク
AAO: Avatar Optimizer の knowledge ファイルを Phase 1 + Phase 2 で作成する。

## リファレンス
品質基準として /mnt/d/git/prefab-sentinel/knowledge/modular-avatar.md を読み、
同等の構造・深さを目指すこと。

## プロトコル
/mnt/d/git/prefab-sentinel/skills/knowledge-acquisition/SKILL.md の Phase 1-2 手順に従う。

## ツール種別
タイプ A (コンポーネントベース) — NDMF プラグイン。
Phase 2 では Runtime/*.cs から [SerializeField] / public フィールドを抽出し、
.meta から Script GUID を抽出する。

## 対象パッケージ
- 優先: /mnt/d/VRChatProject/Shiratsume_Android_20260107/Packages/com.anatawa12.avatar-optimizer
- 差分比較: /mnt/d/git/UnityTool_sample/avatar/Packages/com.anatawa12.avatar-optimizer

## 出力
- ファイル: /mnt/d/git/prefab-sentinel/knowledge/avatar-optimizer.md に Write する
- コミットはしない

## 必須セクション
- frontmatter (tool: avatar-optimizer, version_tested, last_updated: 2026-03-26, confidence: medium)
- 概要 (L1)
- コンポーネント一覧 (L1→L2) — カテゴリ別テーブル
- 操作パターン (L2) — 3+件
- SerializedProperty リファレンス (L3) — Script GUID + propertyPath
  - 検証方法を L3 ヘッダーに明記
- 実運用で学んだこと (空セクション)
```

- [ ] **Step 2: 全エージェント完了を待つ**

- [ ] **Step 3: 品質チェック (3 ファイル)**

各ファイルについて以下を確認:
- [ ] frontmatter: tool, version_tested, last_updated, confidence が正しい
- [ ] 全必須セクション存在 (概要, 一覧, 操作パターン, L3, 実運用)
- [ ] プレースホルダー「(未調査)」が残っていない
- [ ] liltoon: シェーダープロパティが抽出されている
- [ ] VRCFury/AAO: Script GUID が 1 件以上抽出されている
- [ ] confidence: medium
- [ ] 検証方法が L3 ヘッダーに明記

問題があれば修正する。

- [ ] **Step 4: コミット**

```bash
git add knowledge/liltoon.md knowledge/vrcfury.md knowledge/avatar-optimizer.md
SKIP_BUMP=1 git commit -m "docs: populate knowledge Phase 1+2 for liltoon, VRCFury, AAO (batch 1/4)"
```

---

### Task 2: Batch 2 — VRChat SDK Base, VRChat SDK Avatars, NDMF (並列 3 エージェント)

**Files:**
- Create: `knowledge/vrchat-sdk-base.md`
- Create: `knowledge/vrchat-sdk-avatars.md`
- Create: `knowledge/ndmf.md`

- [ ] **Step 1: 3 サブエージェントを並列 dispatch**

**Agent 2a: VRChat SDK Base (タイプ C — プラットフォーム)**

```
## タスク
VRChat SDK Base (com.vrchat.base) の knowledge ファイルを Phase 1 + Phase 2 で作成する。

## リファレンス
品質基準として /mnt/d/git/prefab-sentinel/knowledge/modular-avatar.md を読み、
同等の構造・深さを目指すこと。

## プロトコル
/mnt/d/git/prefab-sentinel/skills/knowledge-acquisition/SKILL.md の Phase 1-2 手順に従う。

## ツール種別
タイプ C (プラットフォーム) — VRChat の基盤 SDK。
Phase 2 では Runtime/*.cs から [SerializeField] / public フィールドと Script GUID を抽出する。
加えて、以下の構造的知識を追加セクションで記載する:
- レイヤー構成 (VRChat 標準レイヤー)
- ネットワーク制約 (同期上限、帯域制限)
- ビルドパイプライン (IVRCSDKBuildRequestedCallback 等)
- コンポーネントの一般的な制約

## 対象パッケージ
- 優先: /mnt/d/VRChatProject/Shiratsume_Android_20260107/Packages/com.vrchat.base
- 差分比較: /mnt/d/git/UnityTool_sample/avatar/Packages/com.vrchat.base

## 出力
- ファイル: /mnt/d/git/prefab-sentinel/knowledge/vrchat-sdk-base.md に Write する
- コミットはしない

## 必須セクション
- frontmatter (tool: vrchat-sdk-base, version_tested, last_updated: 2026-03-26, confidence: medium)
- 概要 (L1)
- コンポーネント一覧 (L1→L2)
- プラットフォーム制約 (L2) — レイヤー、ネットワーク、ビルド
- 操作パターン (L2) — 3+件
- SerializedProperty リファレンス (L3) — Script GUID + propertyPath
  - 検証方法を L3 ヘッダーに明記
- 実運用で学んだこと (空セクション)
```

**Agent 2b: VRChat SDK Avatars (タイプ C — プラットフォーム)**

```
## タスク
VRChat SDK Avatars (com.vrchat.avatars) の knowledge ファイルを Phase 1 + Phase 2 で作成する。

## リファレンス
品質基準として /mnt/d/git/prefab-sentinel/knowledge/modular-avatar.md を読み、
同等の構造・深さを目指すこと。

## プロトコル
/mnt/d/git/prefab-sentinel/skills/knowledge-acquisition/SKILL.md の Phase 1-2 手順に従う。

## ツール種別
タイプ C (プラットフォーム) — VRChat アバター SDK。
Phase 2 では Runtime/*.cs から [SerializeField] / public フィールドと Script GUID を抽出する。
特に VRCAvatarDescriptor は巨大なコンポーネントなので、フィールドをカテゴリ別に整理する。
加えて、以下の構造的知識を追加セクションで記載する:
- パフォーマンスランク (Excellent/Good/Medium/Poor/VeryPoor) の基準
- Expressions (Menu + Parameters) の制約 (256 bits, メニュー深さ)
- PhysBone 制約 (コンポーネント数、トランスフォーム数)
- Quest/Android 固有の制約

## 対象パッケージ
- 優先: /mnt/d/VRChatProject/Shiratsume_Android_20260107/Packages/com.vrchat.avatars
- 差分比較: /mnt/d/git/UnityTool_sample/avatar/Packages/com.vrchat.avatars

## 出力
- ファイル: /mnt/d/git/prefab-sentinel/knowledge/vrchat-sdk-avatars.md に Write する
- コミットはしない

## 必須セクション
- frontmatter (tool: vrchat-sdk-avatars, version_tested, last_updated: 2026-03-26, confidence: medium)
- 概要 (L1)
- コンポーネント一覧 (L1→L2) — VRCAvatarDescriptor + 周辺コンポーネント
- パフォーマンスランク基準 (L2)
- プラットフォーム制約 (L2) — Expressions, PhysBone, Quest
- 操作パターン (L2) — 3+件
- SerializedProperty リファレンス (L3) — Script GUID + propertyPath
  - 検証方法を L3 ヘッダーに明記
- 実運用で学んだこと (空セクション)
```

**Agent 2c: NDMF (タイプ C — フレームワーク)**

```
## タスク
NDMF (Non-Destructive Modular Framework) の knowledge ファイルを Phase 1 + Phase 2 で作成する。

## リファレンス
品質基準として /mnt/d/git/prefab-sentinel/knowledge/modular-avatar.md を読み、
同等の構造・深さを目指すこと。

## プロトコル
/mnt/d/git/prefab-sentinel/skills/knowledge-acquisition/SKILL.md の Phase 1-2 手順に従う。

## ツール種別
タイプ C (フレームワーク) — ビルドパイプラインフレームワーク。
Phase 2 では Runtime/*.cs から [SerializeField] / public フィールドと Script GUID を抽出する。
加えて、以下の構造的知識を追加セクションで記載する:
- パイプラインフェーズ (Resolving, Generating, Transforming, Optimizing)
- Plugin / Pass の定義 API
- Extension Context API
- プラットフォームフィルタリング ([RunsOnPlatforms])

重要: MA ナレッジ (knowledge/modular-avatar.md) の概要セクションに NDMF の
3 フェーズパイプラインの記述がある。NDMF ナレッジでは NDMF 自体の API と
Extension Point のみを記載し、MA 固有の Pass 一覧等は記載しない。
MA ナレッジとの重複を避けること。

## 対象パッケージ
- 優先: /mnt/d/VRChatProject/Shiratsume_Android_20260107/Packages/nadena.dev.ndmf
- 差分比較: /mnt/d/git/UnityTool_sample/avatar/Packages/nadena.dev.ndmf

## 出力
- ファイル: /mnt/d/git/prefab-sentinel/knowledge/ndmf.md に Write する
- コミットはしない

## 必須セクション
- frontmatter (tool: ndmf, version_tested, last_updated: 2026-03-26, confidence: medium)
- 概要 (L1)
- コンポーネント一覧 (L1→L2) — ユーザー向け / 開発者向けに分類
- パイプライン構造 (L2) — フェーズ定義、実行順序
- Extension API (L2) — Extension Context、プラットフォームフィルタ
- 操作パターン (L2) — NDMF プラグイン開発者視点で 3+件
- SerializedProperty リファレンス (L3) — Script GUID + propertyPath
  - 検証方法を L3 ヘッダーに明記
- 実運用で学んだこと (空セクション)
```

- [ ] **Step 2: 全エージェント完了を待つ**

- [ ] **Step 3: 品質チェック (3 ファイル)**

各ファイルについて以下を確認:
- [ ] frontmatter 正しい
- [ ] 全必須セクション存在
- [ ] プレースホルダー残存なし
- [ ] Script GUID 1 件以上
- [ ] confidence: medium
- [ ] 検証方法明記
- [ ] NDMF が MA ナレッジと重複していない

- [ ] **Step 4: コミット**

```bash
git add knowledge/vrchat-sdk-base.md knowledge/vrchat-sdk-avatars.md knowledge/ndmf.md
SKIP_BUMP=1 git commit -m "docs: populate knowledge Phase 1+2 for VRChat SDK Base/Avatars, NDMF (batch 2/4)"
```

---

### Task 3: Batch 3 — FaceEmo, lilycalInventory, VRCQuestTools, TexTransTool (並列 4 エージェント)

**Files:**
- Create: `knowledge/face-emo.md`
- Create: `knowledge/lilycal-inventory.md`
- Create: `knowledge/vrc-quest-tools.md`
- Create: `knowledge/tex-trans-tool.md`

- [ ] **Step 1: 4 サブエージェントを並列 dispatch**

各エージェントに以下のプロンプトで dispatch する。`mode: bypassPermissions` を使用。

**Agent 3a: FaceEmo (タイプ A — コンポーネント)**

```
## タスク
FaceEmo の knowledge ファイルを Phase 1 + Phase 2 で作成する。

## リファレンス
品質基準として /mnt/d/git/prefab-sentinel/knowledge/modular-avatar.md を読み、
同等の構造・深さを目指すこと。

## プロトコル
/mnt/d/git/prefab-sentinel/skills/knowledge-acquisition/SKILL.md の Phase 1-2 手順に従う。

## ツール種別
タイプ A (コンポーネントベース) — NDMF プラグイン。表情メニュー自動生成ツール。
Phase 2 では Runtime/*.cs (存在しない場合は他の C# ソースディレクトリを探索) から
[SerializeField] / public フィールドを抽出し、.meta から Script GUID を抽出する。

## 対象パッケージ
- 優先: /mnt/d/VRChatProject/Shiratsume_Android_20260107/Packages/jp.suzuryg.face-emo
- 差分比較: /mnt/d/git/UnityTool_sample/avatar/Packages/jp.suzuryg.face-emo

## 出力
- ファイル: /mnt/d/git/prefab-sentinel/knowledge/face-emo.md に Write する
- コミットはしない

## 必須セクション
- frontmatter (tool: face-emo, version_tested, last_updated: 2026-03-26, confidence: medium)
- 概要 (L1)
- コンポーネント一覧 (L1→L2) — カテゴリ別テーブル
- 操作パターン (L2) — 3+件
- SerializedProperty リファレンス (L3) — Script GUID + propertyPath
  - 検証方法を L3 ヘッダーに明記
- 実運用で学んだこと (空セクション)
```

**Agent 3b: lilycalInventory (タイプ A — コンポーネント)**

```
## タスク
lilycalInventory の knowledge ファイルを Phase 1 + Phase 2 で作成する。

## リファレンス
品質基準として /mnt/d/git/prefab-sentinel/knowledge/modular-avatar.md を読み、
同等の構造・深さを目指すこと。

## プロトコル
/mnt/d/git/prefab-sentinel/skills/knowledge-acquisition/SKILL.md の Phase 1-2 手順に従う。

## ツール種別
タイプ A (コンポーネントベース) — NDMF プラグイン。アバターインベントリ管理ツール。
Phase 2 では Runtime/*.cs (存在しない場合は他の C# ソースディレクトリを探索) から
[SerializeField] / public フィールドを抽出し、.meta から Script GUID を抽出する。

## 対象パッケージ
- Shiratsume のみ: /mnt/d/VRChatProject/Shiratsume_Android_20260107/Packages/jp.lilxyzw.lilycalinventory
- PF-TEST にはインストールされていない

## 出力
- ファイル: /mnt/d/git/prefab-sentinel/knowledge/lilycal-inventory.md に Write する
- コミットはしない

## 必須セクション
- frontmatter (tool: lilycal-inventory, version_tested, last_updated: 2026-03-26, confidence: medium)
- 概要 (L1)
- コンポーネント一覧 (L1→L2) — カテゴリ別テーブル
- 操作パターン (L2) — 3+件
- SerializedProperty リファレンス (L3) — Script GUID + propertyPath
  - 検証方法を L3 ヘッダーに明記
- 実運用で学んだこと (空セクション)
```

**Agent 3c: VRCQuestTools (タイプ A — コンポーネント)**

```
## タスク
VRCQuestTools の knowledge ファイルを Phase 1 + Phase 2 で作成する。

## リファレンス
品質基準として /mnt/d/git/prefab-sentinel/knowledge/modular-avatar.md を読み、
同等の構造・深さを目指すこと。

## プロトコル
/mnt/d/git/prefab-sentinel/skills/knowledge-acquisition/SKILL.md の Phase 1-2 手順に従う。

## ツール種別
タイプ A (コンポーネントベース) — Quest/Android 対応変換ツール。
Phase 2 では Runtime/*.cs (存在しない場合は他の C# ソースディレクトリを探索) から
[SerializeField] / public フィールドを抽出し、.meta から Script GUID を抽出する。

## 対象パッケージ
- Shiratsume のみ: /mnt/d/VRChatProject/Shiratsume_Android_20260107/Packages/com.github.kurotu.vrc-quest-tools
- PF-TEST にはインストールされていない

## 出力
- ファイル: /mnt/d/git/prefab-sentinel/knowledge/vrc-quest-tools.md に Write する
- コミットはしない

## 必須セクション
- frontmatter (tool: vrc-quest-tools, version_tested, last_updated: 2026-03-26, confidence: medium)
- 概要 (L1)
- コンポーネント一覧 (L1→L2) — カテゴリ別テーブル
- 操作パターン (L2) — 3+件
- SerializedProperty リファレンス (L3) — Script GUID + propertyPath
  - 検証方法を L3 ヘッダーに明記
- 実運用で学んだこと (空セクション)
```

**Agent 3d: TexTransTool (タイプ A — コンポーネント)**

```
## タスク
TexTransTool の knowledge ファイルを Phase 1 + Phase 2 で作成する。

## リファレンス
品質基準として /mnt/d/git/prefab-sentinel/knowledge/modular-avatar.md を読み、
同等の構造・深さを目指すこと。

## プロトコル
/mnt/d/git/prefab-sentinel/skills/knowledge-acquisition/SKILL.md の Phase 1-2 手順に従う。

## ツール種別
タイプ A (コンポーネントベース) — NDMF プラグイン。テクスチャ変換・合成ツール。
Phase 2 では Runtime/*.cs (存在しない場合は他の C# ソースディレクトリを探索) から
[SerializeField] / public フィールドを抽出し、.meta から Script GUID を抽出する。

## 対象パッケージ
- Shiratsume のみ: /mnt/d/VRChatProject/Shiratsume_Android_20260107/Packages/net.rs64.tex-trans-tool
- PF-TEST にはインストールされていない

## 出力
- ファイル: /mnt/d/git/prefab-sentinel/knowledge/tex-trans-tool.md に Write する
- コミットはしない

## 必須セクション
- frontmatter (tool: tex-trans-tool, version_tested, last_updated: 2026-03-26, confidence: medium)
- 概要 (L1)
- コンポーネント一覧 (L1→L2) — カテゴリ別テーブル
- 操作パターン (L2) — 3+件
- SerializedProperty リファレンス (L3) — Script GUID + propertyPath
  - 検証方法を L3 ヘッダーに明記
- 実運用で学んだこと (空セクション)
```

- [ ] **Step 2: 全エージェント完了を待つ**

- [ ] **Step 3: 品質チェック (4 ファイル)**

標準チェックリスト:
- [ ] frontmatter 正しい
- [ ] 全必須セクション存在
- [ ] プレースホルダー残存なし
- [ ] Script GUID 1 件以上
- [ ] confidence: medium
- [ ] 検証方法明記

- [ ] **Step 4: コミット**

```bash
git add knowledge/face-emo.md knowledge/lilycal-inventory.md knowledge/vrc-quest-tools.md knowledge/tex-trans-tool.md
SKIP_BUMP=1 git commit -m "docs: populate knowledge Phase 1+2 for FaceEmo, lilycalInventory, VRCQuestTools, TexTransTool (batch 3/4)"
```

---

### Task 4: Batch 4 — 可愛いポーズツール, Floor Adjuster, Copy Scale Adjuster, Avatar Compressor (並列 4 エージェント)

**Files:**
- Create: `knowledge/kawaii-posing.md`
- Create: `knowledge/floor-adjuster.md`
- Create: `knowledge/copy-scale-adjuster.md`
- Create: `knowledge/avatar-compressor.md`

- [ ] **Step 1: 4 サブエージェントを並列 dispatch**

各エージェントに以下のプロンプトで dispatch する。`mode: bypassPermissions` を使用。

**Agent 4a: 可愛いポーズツール + Posing System (タイプ A — コンポーネント)**

```
## タスク
可愛いポーズツール (KawaiiPosing) + Posing System の knowledge ファイルを Phase 1 + Phase 2 で作成する。

## リファレンス
品質基準として /mnt/d/git/prefab-sentinel/knowledge/modular-avatar.md を読み、
同等の構造・深さを目指すこと。

## プロトコル
/mnt/d/git/prefab-sentinel/skills/knowledge-acquisition/SKILL.md の Phase 1-2 手順に従う。

## ツール種別
タイプ A (コンポーネントベース) — ポーズ制御ツール。
Phase 2 では Runtime/*.cs (存在しない場合は他の C# ソースディレクトリを探索) から
[SerializeField] / public フィールドを抽出し、.meta から Script GUID を抽出する。

## 対象パッケージ
kawaiiposing と posingsystem は密結合のため、1 ファイルにまとめて記載する。
両パッケージの Runtime C# を分析し、コンポーネント一覧ではパッケージの出自を備考列で区別する。

- kawaiiposing (優先: Shiratsume): /mnt/d/VRChatProject/Shiratsume_Android_20260107/Packages/jp.unisakistudio.kawaiiposing
- kawaiiposing (差分比較: PF-TEST): /mnt/d/git/UnityTool_sample/avatar/Packages/jp.unisakistudio.kawaiiposing
- posingsystem (優先: Shiratsume): /mnt/d/VRChatProject/Shiratsume_Android_20260107/Packages/jp.unisakistudio.posingsystem
- posingsystem (差分比較: PF-TEST): /mnt/d/git/UnityTool_sample/avatar/Packages/jp.unisakistudio.posingsystem

## 出力
- ファイル: /mnt/d/git/prefab-sentinel/knowledge/kawaii-posing.md に Write する
- コミットはしない

## 必須セクション
- frontmatter (tool: kawaii-posing, version_tested, last_updated: 2026-03-26, confidence: medium)
- 概要 (L1): 両パッケージの関係を説明
- コンポーネント一覧 (L1→L2) — カテゴリ別テーブル (パッケージ出自を備考列で区別)
- 操作パターン (L2) — 3+件
- SerializedProperty リファレンス (L3) — Script GUID + propertyPath
  - 検証方法を L3 ヘッダーに明記
- 実運用で学んだこと (空セクション)
```

**Agent 4b: Floor Adjuster (タイプ A — コンポーネント)**

```
## タスク
Floor Adjuster の knowledge ファイルを Phase 1 + Phase 2 で作成する。

## リファレンス
品質基準として /mnt/d/git/prefab-sentinel/knowledge/modular-avatar.md を読み、
同等の構造・深さを目指すこと。

## プロトコル
/mnt/d/git/prefab-sentinel/skills/knowledge-acquisition/SKILL.md の Phase 1-2 手順に従う。

## ツール種別
タイプ A (コンポーネントベース) — NDMF プラグイン。アバターの床位置調整ツール。
Phase 2 では Runtime/*.cs (存在しない場合は他の C# ソースディレクトリを探索) から
[SerializeField] / public フィールドを抽出し、.meta から Script GUID を抽出する。

## 対象パッケージ
- Shiratsume のみ: /mnt/d/VRChatProject/Shiratsume_Android_20260107/Packages/net.narazaka.vrchat.floor-adjuster
- PF-TEST にはインストールされていない

## 出力
- ファイル: /mnt/d/git/prefab-sentinel/knowledge/floor-adjuster.md に Write する
- コミットはしない

## 必須セクション
- frontmatter (tool: floor-adjuster, version_tested, last_updated: 2026-03-26, confidence: medium)
- 概要 (L1)
- コンポーネント一覧 (L1→L2) — カテゴリ別テーブル
- 操作パターン (L2) — 1-2 件 (小規模ツール)
- SerializedProperty リファレンス (L3) — Script GUID + propertyPath
  - 検証方法を L3 ヘッダーに明記
- 実運用で学んだこと (空セクション)
```

**Agent 4c: Copy Scale Adjuster (タイプ A — コンポーネント)**

```
## タスク
Copy Scale Adjuster の knowledge ファイルを Phase 1 + Phase 2 で作成する。

## リファレンス
品質基準として /mnt/d/git/prefab-sentinel/knowledge/modular-avatar.md を読み、
同等の構造・深さを目指すこと。

## プロトコル
/mnt/d/git/prefab-sentinel/skills/knowledge-acquisition/SKILL.md の Phase 1-2 手順に従う。

## ツール種別
タイプ A (コンポーネントベース) — MA 連携ツール。Modular Avatar の Copy Scale を調整する。
Phase 2 では Runtime/*.cs (存在しない場合は他の C# ソースディレクトリを探索) から
[SerializeField] / public フィールドを抽出し、.meta から Script GUID を抽出する。

## 対象パッケージ
- Shiratsume のみ: /mnt/d/VRChatProject/Shiratsume_Android_20260107/Packages/numeira.modular-avatar-copy-scale-adjuster
- PF-TEST にはインストールされていない

## 出力
- ファイル: /mnt/d/git/prefab-sentinel/knowledge/copy-scale-adjuster.md に Write する
- コミットはしない

## 必須セクション
- frontmatter (tool: copy-scale-adjuster, version_tested, last_updated: 2026-03-26, confidence: medium)
- 概要 (L1)
- コンポーネント一覧 (L1→L2) — カテゴリ別テーブル
- 操作パターン (L2) — 1-2 件 (小規模ツール)
- SerializedProperty リファレンス (L3) — Script GUID + propertyPath
  - 検証方法を L3 ヘッダーに明記
- 実運用で学んだこと (空セクション)
```

**Agent 4d: Avatar Compressor (タイプ A — コンポーネント)**

```
## タスク
Avatar Compressor の knowledge ファイルを Phase 1 + Phase 2 で作成する。

## リファレンス
品質基準として /mnt/d/git/prefab-sentinel/knowledge/modular-avatar.md を読み、
同等の構造・深さを目指すこと。

## プロトコル
/mnt/d/git/prefab-sentinel/skills/knowledge-acquisition/SKILL.md の Phase 1-2 手順に従う。

## ツール種別
タイプ A (コンポーネントベース) — アバターデータ圧縮ツール。テクスチャ・メッシュの最適化。
Phase 2 では Runtime/*.cs (存在しない場合は他の C# ソースディレクトリを探索) から
[SerializeField] / public フィールドを抽出し、.meta から Script GUID を抽出する。

## 対象パッケージ
- Shiratsume のみ: /mnt/d/VRChatProject/Shiratsume_Android_20260107/Packages/dev.limitex.avatar-compressor
- PF-TEST にはインストールされていない

## 出力
- ファイル: /mnt/d/git/prefab-sentinel/knowledge/avatar-compressor.md に Write する
- コミットはしない

## 必須セクション
- frontmatter (tool: avatar-compressor, version_tested, last_updated: 2026-03-26, confidence: medium)
- 概要 (L1)
- コンポーネント一覧 (L1→L2) — カテゴリ別テーブル
- 操作パターン (L2) — 1-2 件 (小規模ツール)
- SerializedProperty リファレンス (L3) — Script GUID + propertyPath
  - 検証方法を L3 ヘッダーに明記
- 実運用で学んだこと (空セクション)
```

- [ ] **Step 2: 全エージェント完了を待つ**

- [ ] **Step 3: 品質チェック (4 ファイル)**

標準チェックリスト:
- [ ] frontmatter 正しい
- [ ] 全必須セクション存在
- [ ] プレースホルダー残存なし
- [ ] Script GUID 1 件以上
- [ ] confidence: medium
- [ ] 検証方法明記
- [ ] kawaii-posing.md: 両パッケージのコンポーネントが含まれている

- [ ] **Step 4: コミット**

```bash
git add knowledge/kawaii-posing.md knowledge/floor-adjuster.md knowledge/copy-scale-adjuster.md knowledge/avatar-compressor.md
SKIP_BUMP=1 git commit -m "docs: populate knowledge Phase 1+2 for KawaiiPosing, FloorAdjuster, CopyScaleAdjuster, AvatarCompressor (batch 4/4)"
```

---

### Task 5: 最終検証

**Files:**
- Read: `knowledge/*.md` (全 15 ファイル)

- [ ] **Step 1: 全ファイル一覧確認**

```bash
ls -la knowledge/*.md | wc -l
# Expected: 15 (1 existing MA + 3 overwritten + 11 new)
```

確認: 15 ファイルが存在し、`vrchat-sdk-constraints.md` が存在しないこと。

- [ ] **Step 2: frontmatter 一括検証**

全ファイルの frontmatter を確認:
```bash
for f in knowledge/*.md; do echo "=== $f ==="; head -6 "$f"; echo; done
```

全ファイルで:
- `confidence: medium` (MA のみ既に medium)
- `version_tested` が "(未調査)" でない
- `last_updated: 2026-03-26`

- [ ] **Step 3: プレースホルダー残存チェック**

```bash
grep -l "(未調査)" knowledge/*.md
# Expected: no output (全て解消済み)
```

- [ ] **Step 4: push**

全バッチのコミットが完了していることを確認し、push する。
