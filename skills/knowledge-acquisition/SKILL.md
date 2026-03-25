---
name: knowledge-acquisition
description: >-
  VRChat エコシステムツールのナレッジを体系的に調査・検証して knowledge/ に蓄積する。
  新ツールの初回調査や、既存ナレッジの大幅補充時に使う。
  トリガー: 新しい VRChat コミュニティツールに初めて遭遇した、
  既存ナレッジの confidence が low で作業に支障がある、
  ユーザーから「このツールについて調べて」と依頼された場合。
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
