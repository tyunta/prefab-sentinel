# VRChat Ecosystem Knowledge Batch Population Design

## Goal

既存の knowledge-acquisition プロトコルと MA リファレンス実装を使い、14 ツールに Phase 1 (デスクリサーチ) + Phase 2 (ソースコード分析) を一括適用する。

## 対象ツール (14 件)

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
| 11 | 可愛いポーズツール + Posing System | jp.unisakistudio.kawaiiposing + jp.unisakistudio.posingsystem | 新規 | o | o |
| 12 | Floor Adjuster | net.narazaka.vrchat.floor-adjuster | 新規 | - | o |
| 13 | Copy Scale Adjuster | numeira.modular-avatar-copy-scale-adjuster | 新規 | - | o |
| 14 | Avatar Compressor | dev.limitex.avatar-compressor | 新規 | - | o |

備考: 可愛いポーズツール (kawaiiposing) と Posing System (posingsystem) は密結合のため、1 ファイル `kawaii-posing.md` にまとめて扱う。posingsystem のコンポーネントも同ファイル内に記載する。

## ツール種別と Phase 2 の適用方法

ツールの性質によって Phase 2 の抽出対象が異なる。

### タイプ A: コンポーネントベース (大半のツール)

MonoBehaviour / AvatarTagComponent 等を持つ一般的な NDMF プラグインやエディタ拡張。

- Phase 2: Runtime/*.cs の `[SerializeField]` / `public` フィールド抽出 + .meta の Script GUID 抽出
- L3 セクション: Script GUID テーブル + propertyPath テーブル
- 該当: VRCFury, AAO, VRChat SDK Base, VRChat SDK Avatars, NDMF, FaceEmo, lilycalInventory, VRCQuestTools, TexTransTool, 可愛いポーズツール, Floor Adjuster, Copy Scale Adjuster, Avatar Compressor

### タイプ B: シェーダーパッケージ (liltoon)

MonoBehaviour を持たないシェーダー専用パッケージ。

- Phase 2: .shader / .hlsl / .lilblock ファイルからシェーダープロパティ名と型を抽出 (例: `_MainTex`, `_MainColor`, `_MainColorPower`)。.meta からシェーダーアセット GUID を抽出
- L3 セクション: 「シェーダープロパティ リファレンス」に名前変更。プロパティ名・型・デフォルト値・用途のテーブル
- 該当: liltoon のみ

### タイプ C: プラットフォーム / フレームワーク (SDK Base, SDK Avatars, NDMF)

コンポーネントに加えてプラットフォーム制約・ビルドパイプライン・パフォーマンスランク等の構造的知識がある。

- Phase 2: タイプ A の抽出に加え、制約・ランク・パイプライン情報を別セクションで記載
- L3 セクション: 通常の propertyPath + 「プラットフォーム制約」「パフォーマンスランク」等の追加セクション
- 該当: VRChat SDK Base (ネットワーク制約、レイヤー構成等)、VRChat SDK Avatars (VRCAvatarDescriptor + パフォーマンスランク)、NDMF (パイプラインフェーズ、Extension API)
- **NDMF と MA の境界**: NDMF ナレッジは NDMF 自体の API・Extension Point・フェーズ定義のみを記載する。MA のビルドパイプライン記述 (MA ナレッジの概要セクション) との重複は避け、NDMF 側から MA ナレッジを参照する形にする

## 実行アプローチ: 並列サブエージェント一括実行

### バッチ構成

| バッチ | ツール | 同時実行数 |
|--------|--------|-----------|
| Batch 1 | liltoon, VRCFury, AAO | 3 |
| Batch 2 | VRChat SDK Base, VRChat SDK Avatars, NDMF | 3 |
| Batch 3 | FaceEmo, lilycalInventory, VRCQuestTools, TexTransTool | 4 |
| Batch 4 | 可愛いポーズツール, Floor Adjuster, Copy Scale Adjuster, Avatar Compressor | 4 |

### プロジェクトパス選択ルール

Phase 2 ソース分析で使うプロジェクトパスの優先順:
1. **両方に存在** → Shiratsume を優先 (バージョンが新しい傾向)。PF-TEST との差分があれば記載
2. **Shiratsume のみ** → Shiratsume のみ使用
3. **PF-TEST のみ** → PF-TEST のみ使用

パス:
- PF-TEST: `/mnt/d/git/UnityTool_sample/avatar/Packages/<pkg>`
- Shiratsume: `/mnt/d/VRChatProject/Shiratsume_Android_20260107/Packages/<pkg>`

### サブエージェント dispatch テンプレート

各サブエージェントに以下のプロンプト構造で dispatch する:

```
## タスク
{TOOL_NAME} の knowledge ファイルを Phase 1 + Phase 2 で作成する。

## リファレンス
品質基準として knowledge/modular-avatar.md を読み、同等の構造・深さを目指すこと。

## プロトコル
skills/knowledge-acquisition/SKILL.md の Phase 1-2 手順に従う。

## ツール種別
{TYPE_A | TYPE_B | TYPE_C} — {種別に応じた Phase 2 の具体的指示}

## 対象パッケージ
- 優先: {SHIRATSUME_PATH}
- 差分比較: {PF_TEST_PATH} (存在する場合)

## 出力
- ファイル: knowledge/{FILE_NAME}.md に Write する
- コミットはしない

## 必須セクション
- frontmatter (tool, version_tested, last_updated, confidence)
- 概要 (L1)
- コンポーネント一覧 (L1→L2) — カテゴリ別テーブル
- 操作パターン (L2) — 大規模 3+件、小規模 1-2 件
- SerializedProperty リファレンス (L3) — Script GUID + propertyPath
  - 検証方法を L3 ヘッダーに明記
  - {TYPE_B の場合: シェーダープロパティ リファレンスに読み替え}
  - {TYPE_C の場合: プラットフォーム制約等の追加セクション}
- 実運用で学んだこと (空セクション)
```

### サブエージェントの責務

1. Phase 1: Web 検索で概要・コンポーネント一覧・操作パターンを収集
2. Phase 2: ツール種別に応じたソース分析
   - タイプ A: Runtime/*.cs の [SerializeField] + .meta の Script GUID
   - タイプ B: .shader/.hlsl からプロパティ名・型 + .meta のシェーダー GUID
   - タイプ C: タイプ A + 制約・パイプライン情報
3. 両プロジェクトにある場合はバージョン差分を記載
4. ナレッジファイルを Write する (コミットはしない)

### バッチ間ゲート — 品質チェックリスト

各バッチ完了後、メインコンテキストが以下をチェック:

- [ ] frontmatter: tool, version_tested, last_updated, confidence が全て存在し正しく設定されている
- [ ] 全必須セクションが存在する (概要, コンポーネント一覧, 操作パターン, L3 リファレンス, 実運用で学んだこと)
- [ ] scaffold のプレースホルダー「(未調査)」が残っていない
- [ ] Script GUID が 1 件以上抽出されている (タイプ A/C) またはシェーダープロパティが抽出されている (タイプ B)
- [ ] confidence が `medium` (Phase 2 完了) に設定されている
- [ ] 検証方法が L3 ヘッダーに明記されている
- [ ] NDMF ファイルが MA ナレッジと重複していない (Batch 2 のみ)

問題があれば修正し、パスしたらコミット → 次バッチ投入。

## ファイル構成変更

### 分割・削除
- `knowledge/vrchat-sdk-constraints.md` → 削除

確認: このファイル名は CLAUDE.md の自動読み込みルール、memory ファイル、他のナレッジファイルのいずれからも参照されていない。削除しても既存の動作に影響なし。

### 新規作成 (12 件)
- vrchat-sdk-base.md, vrchat-sdk-avatars.md, ndmf.md
- face-emo.md, lilycal-inventory.md, kawaii-posing.md
- floor-adjuster.md, copy-scale-adjuster.md, avatar-compressor.md
- vrc-quest-tools.md, tex-trans-tool.md

### 既存上書き (3 件)
- liltoon.md, vrcfury.md, avatar-optimizer.md

## 品質基準

### 必須セクション (MA 同等)
- frontmatter: tool, version_tested, last_updated, confidence
- 概要 (L1): 解決する問題、依存関係、プラットフォーム、バージョン
- コンポーネント一覧 (L1→L2): カテゴリ別テーブル
- 操作パターン (L2): 典型的な使用パターン (大規模 3+件、小規模 1-2 件)
- SerializedProperty リファレンス (L3): Script GUID テーブル + propertyPath (タイプ A/C)、シェーダープロパティテーブル (タイプ B)
- 検証方法の明記 (L3 ヘッダーに記載)
- 実運用で学んだこと (Phase 3-4 用の空セクション)

### ツール種別による構造の柔軟性
- タイプ B (liltoon): 「SerializedProperty リファレンス」→「シェーダープロパティ リファレンス」に読み替え
- タイプ C (SDK, NDMF): 通常セクションに加え「プラットフォーム制約」「パフォーマンスランク」「パイプラインフェーズ」等の追加セクション可
- セクション名のマイナーな変更はドメインの性質に合わせて許容

### confidence 設定
- Phase 1 のみ → `low`
- Phase 2 完了 → `medium`
- frontmatter は全セクション中の最低値

### version_tested
- 両プロジェクトにある → Shiratsume バージョンを採用 (新しい方)、差分があれば MA 同様に記載
- Shiratsume のみ → Shiratsume バージョン
- PF-TEST のみ → PF-TEST バージョン

## コミット戦略

- バッチごとに 1 コミット (計 4 コミット + scaffold/削除の準備コミット 1)
- `SKIP_BUMP=1` 環境変数を設定してコミット (docs-only のバージョン消費を抑制)
- コミットメッセージ: `docs: populate knowledge Phase 1+2 for <tool-list> (batch N/4)`
