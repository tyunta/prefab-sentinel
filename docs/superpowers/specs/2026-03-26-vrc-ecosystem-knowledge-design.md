# VRChat エコシステムナレッジベース — Design Spec

## 背景

prefab-sentinel は汎用的な Unity アセット操作ツールキットであり、VRC SDK アップロード以外は VRChat エコシステム固有の知識を持たない。ModularAvatar、liltoon、VRCFury、AvatarOptimizer 等のコミュニティツールについて、コンポーネントの意味・使い分け・設定値の妥当性を判断する材料がない。

AI エージェントが自律的にアバター改変作業を行う場合にも、人間にアドバイスする場合にも、これらの判断材料が必要になる。

## 目的

VRChat エコシステムのツール群について、概念理解から SerializedProperty レベルの詳細まで段階的に蓄積できるナレッジベースを構築する。知識は通常作業の中で継続的に成長し、必要なときに自動的に参照される。

## アーキテクチャ

3 つの構成要素からなる。

```
knowledge/                          ← 1. ナレッジストア（読み書き両方向）
  modular-avatar.md                    ファイル名 = ツール名（ケバブケース）
  liltoon.md                           リポジトリルート /mnt/d/git/prefab-sentinel/knowledge/
  vrchat-sdk-constraints.md
  avatar-optimizer.md
  vrcfury.md
  (作業中に遭遇した新ツールも同じ形式で追加)

skills/
  knowledge-acquisition/            ← 2. 集中調査プロトコル（スキル）
    SKILL.md

CLAUDE.md                           ← 3. 行動トリガー（自動読み込み・書き戻しルール）
```

### 1. ナレッジストア (`knowledge/`)

ツールごとに 1 ファイル。共通スキーマで L1〜L3 を段階的に埋める。配置先はリポジトリルート直下の `knowledge/` ディレクトリ。

### 2. 集中調査プロトコル (`skills/knowledge-acquisition/`)

スキル `/prefab-sentinel:knowledge-acquisition` として、4 系統の知識獲得を体系化する。

### 3. 行動トリガー (`CLAUDE.md`)

通常作業中の自動読み込み・自動書き戻しの発火条件と手順を定義する。エージェントの行動を制御する実行ルールであり、単なる設定ではない。

## ナレッジファイルスキーマ

```markdown
---
tool: (ツール名)
version_tested: "(確認バージョン)"
last_updated: YYYY-MM-DD
confidence: low | medium | high
---

# (ツール名)

## 概要 (L1)
何をするツールか、どういうときに使うか、他ツールとの使い分け。

## コンポーネント一覧 (L1→L2)
| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| ... | ... | ... |

## 操作パターン (L2)
### (ユースケース名)
手順、注意点、よくある失敗。

## SerializedProperty リファレンス (L3)
### (コンポーネント名)
- Script GUID: `(値)`
- フィールド:
  | propertyPath | 型 | 説明 | 典型値 |
  |---|---|---|---|
  | ... | ... | ... | ... |

## 実運用で学んだこと
- (作業中の発見、成功/失敗パターンが自動追記される)
```

### フィールド定義

- **`confidence`**: 知識の信頼度。Web 検索のみ → `low`、ソース読解で裏付け → `medium`、実環境で inspect 検証 → `high`。
- **`version_tested`**: 確認したツールのバージョン。Unity Package Manager に表示されるバージョン文字列、またはツール固有のバージョン表記をそのまま使う（例: `"1.10.3"`, `"1.8.1-beta"`）。バージョンアップ時の再検証判断に使う。
- **「実運用で学んだこと」**: 自動書き戻しの受け皿。蓄積された知見が十分溜まったら上位セクションに整理・昇格する。

## 知識の 3 レベル

| レベル | 内容 | 獲得方法 | confidence 目安 |
|--------|------|----------|-----------------|
| L1: 概念と使い分け | 何をするツールか、いつ使うか | Web 検索、公式ドキュメント | low |
| L2: 操作パターン | どう設定するか、組み合わせ方 | ソースコード分析、利用パターン観察 | medium |
| L3: SerializedProperty | GUID、propertyPath、型、典型値 | 実環境 inspect、パッケージソース読解 | high |

## 知識獲得の 4 系統

| 系統 | ソース | 主な出力レベル |
|------|--------|----------------|
| 公式ドキュメント・コミュニティリソース調査 | Web 検索、公式 README/Wiki | L1, L2 |
| ソースコード分析 | GitHub リポジトリ、Issue、リリースノート | L2, L3 |
| インストール済みパッケージの構造分析 | Unity プロジェクトの Packages フォルダ、.meta ファイル | L2, L3 |
| 実プロジェクトでの利用パターン観察 | 実プロジェクト内アセットの inspect | L2, L3 |

## 自動読み込みルール

作業中に以下の判断が必要になったら、対応する `knowledge/*.md` を Read してから判断する:

- どのコンポーネントを使うべきか（例: MA Merge Armature vs VRCFury Armature Link）
- コンポーネントのプロパティをどう設定すべきか
- シェーダーパラメータの意味や適正値
- パフォーマンスランクへの影響

### ファイル特定ルール

コンポーネント型名・シェーダー名・パッケージ名から `knowledge/` 内のファイルを特定する。対応表はナレッジファイルの「コンポーネント一覧」セクションに記載される。例:

- `ModularAvatarMergeArmature` → `knowledge/modular-avatar.md`
- `lilToon` シェーダー → `knowledge/liltoon.md`
- `VRCFuryArmatureLink` → `knowledge/vrcfury.md`

対応するファイルが不明な場合は `knowledge/` ディレクトリを Glob して候補を探す。

### 読み込みの判断基準

- エージェントが対象ツールについて**判断に迷ったとき**に読む。既に知っていること（前回の同セッション内で読んだ等）の再読み込みは不要。
- 読み込んだナレッジの `confidence` が `low` の場合、その旨をユーザーに伝えてから判断に使う。
- ナレッジファイルが存在しないツールに遭遇した場合、その旨を報告し、集中調査の要否を確認する。

## 自動書き戻しルール

以下のいずれかに該当したら、該当する `knowledge/*.md` に追記する:

- ソースコードを読んで、ナレッジに無い情報を発見した（GUID、propertyPath、フィールドの意味等）
- 作業が成功して、再現可能なパターンが確立した
- 作業が失敗して、原因と回避策が判明した
- ユーザーから教わった知識

書き戻しルール:

- 追記前に、同じ事実が L1〜L3 の構造化セクションに既に存在するか確認する。存在する場合は追記せず、必要なら既存記述を更新する。
- 既存の記述と矛盾する発見をした場合は、既存記述を修正して正しい情報に置き換える（追記ではなく修正）。
- 新規の発見は「実運用で学んだこと」セクションに追記する（既存の構造化セクションは壊さない）。
- 書き戻し時に `version_tested` と `last_updated` を更新する。
- confidence の昇格: inspect で実測した情報を書き戻した場合、該当項目の confidence を `high` に上げる。
- 「実運用で学んだこと」の項目が安定し再利用されるようになったら、上位の構造化セクション（L1〜L3）に昇格整理する。

## memory との棲み分け

| 種類 | 保存先 | 例 |
|------|--------|---|
| ユーザーの好み・フィードバック | `memory/` | 「コミットは /commit を使う」 |
| プロジェクト固有の状況 | `memory/` | 「Phase 2.3 未実装」 |
| ツールのドメイン知識 | `knowledge/` | 「MA Merge Armature の mergeTarget は Transform 参照」 |
| 作業で得た技術的知見 | `knowledge/` | 「liltoon の _MainColorPower は 0.5 以下だと暗すぎる」 |

## 集中調査プロトコル

スキル `/prefab-sentinel:knowledge-acquisition` として定義する。新ツールの初回調査や既存ナレッジの大幅補充時に使う。

### 4 フェーズ

```
Phase 1: デスクリサーチ (L1 → L2)
  ├─ Web 検索で公式ドキュメント・README・Wiki を収集
  ├─ GitHub リポジトリで README、主要 Issue、リリースノートを読む
  └─ 出力: 概要、コンポーネント一覧、基本的な操作パターン (confidence: low)

Phase 2: ソースコード分析 (L2 → L3)
  ├─ GitHub ソースで主要クラスの構造を読む
  ├─ Unity Packages 内の実コードで SerializedField を列挙する
  ├─ Script GUID を特定する（.meta ファイルから）
  └─ 出力: propertyPath、型、GUID リファレンス (confidence: medium)

Phase 3: 実環境検証 (L3 確定)
  ├─ PF-TEST プロジェクトで prefab-sentinel の inspect ツールを使って実測する
  │   ├─ inspect_wiring: フィールドの配線状態
  │   ├─ get_unity_symbols: コンポーネント階層
  │   └─ inspect_hierarchy: Transform 構造
  ├─ Phase 2 で得た情報と実測値を突合する
  └─ 出力: 検証済み L3 データ (confidence: high)

Phase 4: 実プロジェクトでの利用パターン観察 (L2 補強)
  ├─ 実プロジェクト内のアセットを inspect して利用パターンを収集する
  ├─ コンポーネントの組み合わせ、設定値の傾向を抽象化する
  ├─ 固有名詞（アバター名、衣装名、クリエイター名、販売サイト商品名）は記載しない
  └─ 出力: 「操作パターン」「実運用で学んだこと」の充実 (confidence: medium→high)
```

### 実行ルール

- Phase 1-2 は Unity 環境が無くても実行可能。Phase 3-4 は PF-TEST 環境が必要。
- 各フェーズ完了後にナレッジファイルを更新してコミットする。
- 途中で止めてよい（Phase 1 だけでも `confidence: low` のナレッジとして価値がある）。
- 既に `confidence: high` かつ `version_tested` が現在のインストール済みバージョンと一致する項目は再調査をスキップする。
- インストール済みバージョンが `version_tested` と異なる場合、L3（SerializedProperty）セクションの confidence は暗黙的に `low` として扱い、再検証を優先する。L1〜L2 は概念レベルなのでそのまま参照してよい。

### SKILL.md 構造

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

## ワークフロー
(4 フェーズの手順 — 上記「4 フェーズ」セクションの内容)

## 使用する MCP ツール
- inspect_wiring: コンポーネントのフィールド配線を実測 (Phase 3)
- get_unity_symbols: コンポーネント階層の構造取得 (Phase 3)
- inspect_hierarchy: Transform 構造の取得 (Phase 3)
- inspect_materials: マテリアル・シェーダー情報の取得 (Phase 3)
- Web 検索 / WebFetch: 公式ドキュメント取得 (Phase 1)

## ガードレール
- confidence: high を付けるには inspect による実測検証が必須。
- Phase 4 で固有名詞（アバター名、衣装名、クリエイター名、販売サイト商品名）を記載しない。
- 公式ドキュメントの転記はしない。判断材料と実践知識のみ記録する。
```

## 初期着手順

| 順 | ツール | 初期フェーズ | 理由 |
|---|---|---|---|
| 1 | ModularAvatar | Phase 1-2 | アバター改変の基盤。最も頻繁に判断を迫られる |
| 2 | liltoon | Phase 1-2 | マテリアル操作で既に prefab-sentinel が扱う領域 |
| 3 | VRChat SDK Constraints | Phase 1 | パフォーマンスランク等の制約は Web 情報で十分埋まる |
| 4 | AvatarOptimizer | Phase 1-2 | MA と組み合わせて使うことが多い |
| 5 | VRCFury | Phase 1-2 | MA との使い分け判断に必要 |

Phase 3-4 は PF-TEST 環境が整ったあとに全ツール横断で実施する。対象ツールの制限は設けない。作業中に未知のツールに遭遇したら同じプロトコルで新規ナレッジファイルを作る。

## 影響範囲

| ファイル | 変更内容 |
|----------|----------|
| `knowledge/*.md` (新規、5+ ファイル) | ナレッジファイル群 |
| `skills/knowledge-acquisition/SKILL.md` (新規) | 集中調査プロトコルスキル |
| `CLAUDE.md` | 新セクション「VRChat エコシステムナレッジの自動適用」を「prefab-sentinel の自動適用」セクションの直後に追記 |
| `README.md` | ナレッジベースの説明追加 |

### CLAUDE.md 配置詳細

プロジェクトの `CLAUDE.md`（`/mnt/d/git/prefab-sentinel/CLAUDE.md`）に新セクションを追加する。配置場所は既存の「Editor リモート操作の行動規約」セクションの直後。

グローバルの `~/.claude/CLAUDE.md` にある「prefab-sentinel の自動適用」セクションのトリガーキーワードとは独立して動作する。prefab-sentinel の自動適用は MCP ツール選択のトリガーであり、ナレッジの自動読み込みは判断材料の取得トリガーである。両方が同時に発火しても矛盾しない。

## やらないこと

- ナレッジの動的検索 API（静的ファイル + Read で十分）
- ナレッジファイルの自動生成ツール（プロトコルに従って AI が書く）
- 公式ドキュメントのミラー（判断材料と実践知識のみ。API リファレンスの転記はしない）
- 対象ツールの制限（初期着手は 5 つだが、プロトコルはどんなツールにも適用可能）

## テスト・検証基準

- ナレッジファイルがスキーマに従っている（frontmatter 必須フィールド、セクション構造）
- CLAUDE.md の自動読み込みルールが既存ルールと矛盾しない
- 集中調査プロトコルが Phase 1 単独で完結できる（環境依存なし）
- 既存テスト（1206 件）に影響がない（コード変更なし）
