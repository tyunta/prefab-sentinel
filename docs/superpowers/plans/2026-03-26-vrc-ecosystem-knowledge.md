# VRChat エコシステムナレッジベース Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** VRChat エコシステムツールのドメイン知識を段階的に蓄積・参照できるナレッジベースの仕組みを構築する。

**Architecture:** `knowledge/` ディレクトリにツールごとのナレッジファイル、`skills/knowledge-acquisition/` に集中調査プロトコル、`CLAUDE.md` に自動読み込み・書き戻しルールを配置する 3 構成要素。コード変更なし。

**Tech Stack:** Markdown, CLAUDE.md rules, prefab-sentinel skills

**Spec:** `docs/superpowers/specs/2026-03-26-vrc-ecosystem-knowledge-design.md`

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `knowledge/modular-avatar.md` | ModularAvatar のナレッジファイル |
| Create | `knowledge/liltoon.md` | liltoon のナレッジファイル |
| Create | `knowledge/vrchat-sdk-constraints.md` | VRChat SDK 制約のナレッジファイル |
| Create | `knowledge/avatar-optimizer.md` | AvatarOptimizer のナレッジファイル |
| Create | `knowledge/vrcfury.md` | VRCFury のナレッジファイル |
| Create | `skills/knowledge-acquisition/SKILL.md` | 集中調査プロトコルスキル |
| Modify | `CLAUDE.md:74` | 自動読み込み・書き戻しルール追記（`ignore-guid 運用` の前） |
| Modify | `README.md` | ナレッジベースの説明追加 |

---

## Task 1: ナレッジファイルのスキーマテンプレート作成（ModularAvatar）

**Files:**
- Create: `knowledge/modular-avatar.md`

最初の 1 ファイルでスキーマを確立する。以降のファイルはこれをテンプレートとして使う。

- [ ] **Step 1: `knowledge/` ディレクトリ作成と ModularAvatar ナレッジファイル作成**

```markdown
---
tool: modular-avatar
version_tested: "(未調査)"
last_updated: 2026-03-26
confidence: low
---

# Modular Avatar

## 概要 (L1)
(未調査)

## コンポーネント一覧 (L1→L2)
| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| (未調査) | | |

## 操作パターン (L2)
(未調査)

## SerializedProperty リファレンス (L3)
(未調査)

## 実運用で学んだこと
(なし)
```

- [ ] **Step 2: frontmatter が正しいか確認**

ファイルを Read して以下を確認:
- `tool:` フィールドが存在する
- `version_tested:` フィールドが存在する
- `last_updated:` フィールドが存在する
- `confidence:` フィールドが `low` である
- 5 つのセクション見出し（概要、コンポーネント一覧、操作パターン、SerializedProperty リファレンス、実運用で学んだこと）が全て存在する

- [ ] **Step 3: コミット**

```bash
git add knowledge/modular-avatar.md
git commit -m "feat: add knowledge base scaffold with modular-avatar template"
```

---

## Task 2: 残り 4 つのナレッジファイル作成

**Files:**
- Create: `knowledge/liltoon.md`
- Create: `knowledge/vrchat-sdk-constraints.md`
- Create: `knowledge/avatar-optimizer.md`
- Create: `knowledge/vrcfury.md`

Task 1 のテンプレートと同じスキーマで 4 ファイルを作成する。

- [ ] **Step 1: liltoon ナレッジファイル作成**

```markdown
---
tool: liltoon
version_tested: "(未調査)"
last_updated: 2026-03-26
confidence: low
---

# liltoon

## 概要 (L1)
(未調査)

## コンポーネント一覧 (L1→L2)
| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| (未調査) | | |

## 操作パターン (L2)
(未調査)

## SerializedProperty リファレンス (L3)
(未調査)

## 実運用で学んだこと
(なし)
```

注: liltoon はシェーダーなので「コンポーネント一覧」は「シェーダーバリアント / プロパティグループ一覧」に読み替えてよい。ただしスキーマの見出しはそのまま維持する（知識が充実する段階で見出しを変更する）。

- [ ] **Step 2: vrchat-sdk-constraints ナレッジファイル作成**

```markdown
---
tool: vrchat-sdk-constraints
version_tested: "(未調査)"
last_updated: 2026-03-26
confidence: low
---

# VRChat SDK Constraints

## 概要 (L1)
(未調査)

## コンポーネント一覧 (L1→L2)
| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| (未調査) | | |

## 操作パターン (L2)
(未調査)

## SerializedProperty リファレンス (L3)
(未調査)

## 実運用で学んだこと
(なし)
```

注: SDK Constraints はパフォーマンスランク・コンポーネント制限等。「コンポーネント一覧」は「制約カテゴリ一覧」として使う。

- [ ] **Step 3: avatar-optimizer ナレッジファイル作成**

```markdown
---
tool: avatar-optimizer
version_tested: "(未調査)"
last_updated: 2026-03-26
confidence: low
---

# Avatar Optimizer (AAO)

## 概要 (L1)
(未調査)

## コンポーネント一覧 (L1→L2)
| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| (未調査) | | |

## 操作パターン (L2)
(未調査)

## SerializedProperty リファレンス (L3)
(未調査)

## 実運用で学んだこと
(なし)
```

- [ ] **Step 4: vrcfury ナレッジファイル作成**

```markdown
---
tool: vrcfury
version_tested: "(未調査)"
last_updated: 2026-03-26
confidence: low
---

# VRCFury

## 概要 (L1)
(未調査)

## コンポーネント一覧 (L1→L2)
| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| (未調査) | | |

## 操作パターン (L2)
(未調査)

## SerializedProperty リファレンス (L3)
(未調査)

## 実運用で学んだこと
(なし)
```

- [ ] **Step 5: 全 5 ファイルの frontmatter 確認**

```bash
cd /mnt/d/git/prefab-sentinel && for f in knowledge/*.md; do echo "=== $f ==="; head -6 "$f"; echo; done
```

Expected: 5 ファイル全てに `tool:`, `version_tested:`, `last_updated:`, `confidence:` が存在する。

- [ ] **Step 6: コミット**

```bash
git add knowledge/liltoon.md knowledge/vrchat-sdk-constraints.md knowledge/avatar-optimizer.md knowledge/vrcfury.md
git commit -m "feat: add knowledge scaffolds for liltoon, SDK constraints, AAO, VRCFury"
```

---

## Task 3: 集中調査プロトコルスキル作成

**Files:**
- Create: `skills/knowledge-acquisition/SKILL.md`

- [ ] **Step 1: スキルディレクトリとファイル作成**

```markdown
---
name: knowledge-acquisition
description: >
  VRChat エコシステムツールのナレッジを体系的に調査・検証して knowledge/ に蓄積する。
  新ツールの初回調査や、既存ナレッジの大幅補充時に使う。
triggers:
  - 新しい VRChat コミュニティツールに初めて遭遇した
  - 既存ナレッジの confidence が low で、作業に支障がある
  - ユーザーから「このツールについて調べて」と依頼された
---

# Knowledge Acquisition Protocol

VRChat エコシステムツールのナレッジを体系的に調査し `knowledge/` に蓄積する。

## ワークフロー

4 フェーズで段階的に知識の精度を上げる。途中で止めてよい（Phase 1 だけでも confidence: low のナレッジとして価値がある）。

### Phase 1: デスクリサーチ (L1 → L2)

1. Web 検索で公式ドキュメント・README・Wiki を収集する
2. GitHub リポジトリで README、主要 Issue、リリースノートを読む
3. ナレッジファイルの「概要」「コンポーネント一覧」「操作パターン」を埋める
4. confidence を `low` に設定する

### Phase 2: ソースコード分析 (L2 → L3)

1. GitHub ソースで主要クラスの構造を読む
2. Unity Packages 内の実コード（PF-TEST プロジェクトの `Packages/` フォルダ）で `[SerializeField]` を列挙する
3. Script GUID を `.meta` ファイルから特定する
4. ナレッジファイルの「SerializedProperty リファレンス」を埋める
5. confidence を `medium` に設定する

### Phase 3: 実環境検証 (L3 確定)

**前提:** PF-TEST プロジェクトに対象ツールがインストール済みであること。

1. prefab-sentinel の inspect ツールで実測する:
   - `inspect_wiring`: フィールドの配線状態
   - `get_unity_symbols`: コンポーネント階層
   - `inspect_hierarchy`: Transform 構造
   - `inspect_materials`: マテリアル・シェーダー情報
2. Phase 2 で得た情報と実測値を突合する
3. 差異があれば実測値を正とし、ナレッジを修正する
4. confidence を `high` に設定する

### Phase 4: 実プロジェクトでの利用パターン観察 (L2 補強)

**前提:** 実プロジェクトに対象ツールを使用したアセットが存在すること。

1. 実プロジェクト内のアセットを inspect して利用パターンを収集する
2. コンポーネントの組み合わせ、設定値の傾向を抽象化する
3. 固有名詞（アバター名、衣装名、クリエイター名、販売サイト商品名）は記載しない
4. 「操作パターン」「実運用で学んだこと」セクションを充実させる

## 使用する MCP ツール

| ツール | 用途 | フェーズ |
|--------|------|----------|
| `inspect_wiring` | コンポーネントのフィールド配線を実測 | Phase 3 |
| `get_unity_symbols` | コンポーネント階層の構造取得 | Phase 3 |
| `inspect_hierarchy` | Transform 構造の取得 | Phase 3 |
| `inspect_materials` | マテリアル・シェーダー情報の取得 | Phase 3 |
| Web 検索 / WebFetch | 公式ドキュメント取得 | Phase 1 |

## 実行ルール

- Phase 1-2 は Unity 環境が無くても実行可能。Phase 3-4 は PF-TEST 環境が必要。
- 各フェーズ完了後にナレッジファイルを更新してコミットする。
- 既に confidence: high かつ version_tested が現在のバージョンと一致する項目は再調査をスキップする。
- インストール済みバージョンが version_tested と異なる場合、L3 の confidence は暗黙的に low として扱い、再検証を優先する。

## ガードレール

- confidence: high を付けるには inspect による実測検証が必須。
- Phase 4 で固有名詞（アバター名、衣装名、クリエイター名、販売サイト商品名）を記載しない。
- 公式ドキュメントの転記はしない。判断材料と実践知識のみ記録する。
```

- [ ] **Step 2: 既存スキルとの構造一貫性を確認**

既存スキル（`skills/guide/SKILL.md`）と同様に:
- frontmatter に `name`, `description` がある
- セクション構造がワークフロー → ツール → ルール → ガードレールの順

- [ ] **Step 3: コミット**

```bash
git add skills/knowledge-acquisition/SKILL.md
git commit -m "feat: add knowledge-acquisition skill for systematic VRC tool research"
```

---

## Task 4: CLAUDE.md に自動読み込み・書き戻しルール追記

**Files:**
- Modify: `CLAUDE.md` (`## ignore-guid 運用` 見出しの直前に挿入)

- [ ] **Step 1: CLAUDE.md に新セクション追記**

`## ignore-guid 運用` 見出しを検索し、その直前に以下のテキストを挿入する（コードフェンスは含まない。以下の内容がそのまま CLAUDE.md に入る）:

```
## VRChat エコシステムナレッジの自動適用
- 作業中に VRChat エコシステムツール（ModularAvatar、liltoon、VRCFury、AvatarOptimizer 等）に関する判断が必要になったら、対応する `knowledge/*.md` を Read してから判断する。
- ファイル特定: コンポーネント型名・シェーダー名・パッケージ名からファイルを特定する。不明な場合は `knowledge/` を Glob して候補を探す。
- 読み込み判断: 判断に迷ったときのみ読む。同セッション内で既読なら再読み込み不要。
- confidence が `low` のナレッジを判断材料にする場合、その旨をユーザーに伝える。
- ナレッジファイルが存在しないツールに遭遇したら、その旨を報告し集中調査の要否を確認する。

### 自動書き戻し
- 以下に該当したら `knowledge/*.md` に追記する: ソースコードから未知の情報を発見した / 作業が成功して再現可能なパターンが確立した / 作業が失敗して原因と回避策が判明した / ユーザーから教わった知識。
- 追記前に同じ事実が L1〜L3 セクションに存在するか確認し、存在すれば追記せず必要なら既存記述を更新する。矛盾する発見は既存記述を修正する。
- 新規の発見は「実運用で学んだこと」セクションに追記する。
- 書き戻し時に `version_tested` と `last_updated` を更新する。
- inspect 実測値を書き戻した場合、該当項目の confidence を `high` に昇格する。

### memory との棲み分け
| 種類 | 保存先 | 例 |
|------|--------|---|
| ユーザーの好み・フィードバック | `memory/` | 「コミットは /commit を使う」 |
| プロジェクト固有の状況 | `memory/` | 「Phase 2.3 未実装」 |
| ツールのドメイン知識 | `knowledge/` | 「MA Merge Armature の mergeTarget は Transform 参照」 |
| 作業で得た技術的知見 | `knowledge/` | 「liltoon の _MainColorPower は 0.5 以下だと暗すぎる」 |

```

- [ ] **Step 2: 挿入位置の確認**

CLAUDE.md を Read して:
- 新セクションが「Editor リモート操作の行動規約」の直後にある
- 「ignore-guid 運用」がその直後に続いている
- 既存セクションの内容が壊れていない

- [ ] **Step 3: 既存ルールとの矛盾チェック**

グローバル `~/.claude/CLAUDE.md` の「prefab-sentinel の自動適用」セクションのトリガーキーワードは MCP ツール選択用。今回追加するルールはナレッジ読み込み用。両方が同時に発火しても矛盾しないことを確認する。

- [ ] **Step 4: コミット**

```bash
git add CLAUDE.md
git commit -m "docs: add VRChat ecosystem knowledge auto-read/write rules to CLAUDE.md"
```

---

## Task 5: README にナレッジベースの説明追加

**Files:**
- Modify: `README.md`

- [ ] **Step 1: README の構成確認**

README.md を Read して、「やること / やる内容 / やらないこと」セクション（L169〜）の構成を確認する。

- [ ] **Step 2: 「やる内容」セクションにナレッジベースの説明を追記**

`### やる内容` 見出しを検索し、その箇条書きの末尾に以下を追記:

```markdown
- **VRChat エコシステムナレッジ**: `knowledge/` ディレクトリに ModularAvatar、liltoon、VRCFury、AvatarOptimizer 等のドメイン知識を蓄積する。知識は 3 レベル（L1: 概念、L2: 操作パターン、L3: SerializedProperty）で段階的に充実し、通常作業中に自動で読み書きされる。
```

- [ ] **Step 3: 「やらないこと」セクションとの整合確認**

`### やらないこと` セクションを Read し、既存項目と矛盾がないことを確認する。ナレッジベースの「やらないこと」（公式ドキュメントのミラー禁止、動的検索 API 不要等）は設計上の判断であり spec に記録済みのため、README の「やらないこと」には追記しない。

- [ ] **Step 4: コミット**

```bash
git add README.md
git commit -m "docs: add knowledge base description to README"
```

---

## Task 6: 全体検証

- [ ] **Step 1: ファイル構造の確認**

```bash
cd /mnt/d/git/prefab-sentinel && find knowledge/ skills/knowledge-acquisition/ -type f | sort
```

Expected:
```
knowledge/avatar-optimizer.md
knowledge/liltoon.md
knowledge/modular-avatar.md
knowledge/vrcfury.md
knowledge/vrchat-sdk-constraints.md
skills/knowledge-acquisition/SKILL.md
```

- [ ] **Step 2: 全ナレッジファイルの frontmatter 検証**

```bash
cd /mnt/d/git/prefab-sentinel && for f in knowledge/*.md; do echo "=== $f ==="; grep -c "^tool:" "$f"; grep -c "^version_tested:" "$f"; grep -c "^last_updated:" "$f"; grep -c "^confidence:" "$f"; echo; done
```

Expected: 各ファイルで全て `1` が出力される。

- [ ] **Step 3: SKILL.md の frontmatter 検証**

```bash
grep -c "^name:" skills/knowledge-acquisition/SKILL.md && grep -c "^description:" skills/knowledge-acquisition/SKILL.md
```

Expected: 両方 `1`

- [ ] **Step 4: CLAUDE.md の新セクション存在確認**

```bash
grep -c "VRChat エコシステムナレッジの自動適用" CLAUDE.md
```

Expected: `1`

- [ ] **Step 5: 既存テストが壊れていないことを確認**

```bash
cd /mnt/d/git/prefab-sentinel && uv run --extra test python -m unittest discover -s tests 2>&1 | grep -E "^(Ran|FAILED|OK)"
```

Expected: `OK`（コード変更なしなので全 PASS のはず。テスト数は変動しうるため `OK` のみ確認）

- [ ] **Step 6: lint 確認**

```bash
cd /mnt/d/git/prefab-sentinel && uv run ruff check prefab_sentinel/ tests/
```

Expected: エラーなし（コード変更なし）
