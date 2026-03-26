# VRChat Ecosystem Knowledge Batch Population Design

## Goal

既存の knowledge-acquisition プロトコルと MA リファレンス実装を使い、15 ツールに Phase 1 (デスクリサーチ) + Phase 2 (ソースコード分析) を一括適用する。

## 対象ツール (15 件)

| # | ツール | パッケージ名 | 既存ファイル | PF-TEST | Shiratsume |
|---|--------|-------------|-------------|---------|------------|
| 1 | liltoon | jp.lilxyzw.liltoon | liltoon.md (scaffold) | o | o |
| 2 | VRCFury | com.vrcfury.vrcfury | vrcfury.md (scaffold) | o | o |
| 3 | AAO: Avatar Optimizer | com.anatawa12.avatar-optimizer | avatar-optimizer.md (scaffold) | o | o |
| 4 | VRChat SDK Base | com.vrchat.base | vrchat-sdk-constraints.md → 分割 | o | o |
| 5 | VRChat SDK Avatars | com.vrchat.avatars | 新規 | o | o |
| 6 | NDMF | nadena.dev.ndmf | 新規 | o | o |
| 7 | FaceEmo | jp.suzuryg.face-emo | 新規 | o | o |
| 8 | lilycalInventory | jp.lilxyzw.lilycalinventory | 新規 | - | o |
| 9 | VRCQuestTools | com.github.kurotu.vrc-quest-tools | 新規 | - | o |
| 10 | TexTransTool | net.rs64.tex-trans-tool | 新規 | - | o |
| 11 | 可愛いポーズツール | jp.unisakistudio.kawaiiposing | 新規 | o | o |
| 12 | Floor Adjuster | net.narazaka.vrchat.floor-adjuster | 新規 | - | o |
| 13 | Copy Scale Adjuster | numeira.modular-avatar-copy-scale-adjuster | 新規 | - | o |
| 14 | Avatar Compressor | dev.limitex.avatar-compressor | 新規 | - | o |
| 15 | Posing System | jp.unisakistudio.posingsystem | - (可愛いポーズツールの依存) | o | o |

備考: 可愛いポーズツール (kawaiiposing) は posingsystem に依存しているため、posingsystem もスコープに含む可能性がある。サブエージェントが判断し、必要なら同ファイルに記載する。

## 実行アプローチ: 並列サブエージェント一括実行

### バッチ構成

| バッチ | ツール | 同時実行数 |
|--------|--------|-----------|
| Batch 1 | liltoon, VRCFury, AAO | 3 |
| Batch 2 | VRChat SDK Base, VRChat SDK Avatars, NDMF | 3 |
| Batch 3 | FaceEmo, lilycalInventory, VRCQuestTools, TexTransTool | 4 |
| Batch 4 | 可愛いポーズツール, Floor Adjuster, Copy Scale Adjuster, Avatar Compressor | 4 |

### サブエージェントへの入力

各サブエージェントに以下を渡す:
1. **リファレンス**: `knowledge/modular-avatar.md` (品質基準)
2. **プロトコル**: `skills/knowledge-acquisition/SKILL.md` (Phase 1-2 手順)
3. **対象パッケージパス**: PF-TEST (`/mnt/d/git/UnityTool_sample/avatar/Packages/<pkg>`) および/または Shiratsume (`/mnt/d/VRChatProject/Shiratsume_Android_20260107/Packages/<pkg>`)
4. **出力先**: `knowledge/<tool-name>.md`

### サブエージェントの責務

1. Phase 1: Web 検索で概要・コンポーネント一覧・操作パターンを収集
2. Phase 2: Runtime/*.cs から [SerializeField] と public フィールドを抽出、.meta から Script GUID を抽出
3. 両プロジェクトにある場合はバージョン差分を記載
4. ナレッジファイルを MA と同等の品質で書き出す (ファイル書き込みまで)
5. コミットはしない (メインコンテキストがバッチ単位でコミット)

### バッチ間ゲート

各バッチ完了後:
1. メインコンテキストが全ファイルをざっとレビュー
2. 品質問題があれば修正
3. バッチ単位で `SKIP_BUMP=1 git commit`
4. 次バッチ投入

## ファイル構成変更

### 分割
- `knowledge/vrchat-sdk-constraints.md` → 削除
- `knowledge/vrchat-sdk-base.md` ← 新規 (Base SDK の知識 + 制約)
- `knowledge/vrchat-sdk-avatars.md` ← 新規 (Avatars SDK の知識 + 制約)

### 新規作成 (11 件)
- ndmf.md, face-emo.md, lilycal-inventory.md, kawaii-posing.md
- floor-adjuster.md, copy-scale-adjuster.md, avatar-compressor.md
- vrc-quest-tools.md, tex-trans-tool.md
- vrchat-sdk-base.md, vrchat-sdk-avatars.md

### 既存上書き (3 件)
- liltoon.md, vrcfury.md, avatar-optimizer.md

## 品質基準

### 必須セクション (MA 同等)
- frontmatter: tool, version_tested, last_updated, confidence
- 概要 (L1): 解決する問題、依存関係、プラットフォーム、バージョン
- コンポーネント一覧 (L1→L2): カテゴリ別テーブル
- 操作パターン (L2): 典型的な使用パターン (大規模 3+件、小規模 1-2 件)
- SerializedProperty リファレンス (L3): Script GUID テーブル + propertyPath
- 検証方法の明記 (L3 ヘッダーに記載)
- 実運用で学んだこと (Phase 3-4 用の空セクション)

### confidence 設定
- Phase 1 のみ → `low`
- Phase 2 完了 → `medium`
- frontmatter は全セクション中の最低値

### version_tested
- 両プロジェクトにある → Shiratsume バージョンを採用 (新しい方)、差分があれば MA 同様に記載
- Shiratsume のみ → Shiratsume バージョン
- PF-TEST のみ → PF-TEST バージョン

## コミット戦略

- バッチごとに 1 コミット (計 4 コミット + scaffold 作成 1 コミット)
- `SKIP_BUMP=1` で docs-only のバージョン消費を抑制
- コミットメッセージ: `docs: populate knowledge Phase 1+2 for <tool-list> (batch N/4)`
