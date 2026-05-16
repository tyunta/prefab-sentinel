---
tool: <tool-slug>
version_tested: "<x.y.z>"
last_updated: <YYYY-MM-DD>
confidence: low
---

# <tool name>

新規 `knowledge/*.md` のスケルトン。各セクションの下に「ここに書くこと / ここに書かないこと」をインラインで注記している。執筆を始める前に [STYLE_GUIDE.md](./STYLE_GUIDE.md) を再読する。frontmatter の 4 フィールド（`tool` / `version_tested` / `last_updated` / `confidence`）はすべて必須。

## 基本情報

<!-- ここに書く: 正規名 / パッケージ名 / 配布元 (VPM / npm / git URL) / 公式ドキュメント URL / プラットフォーム要件。 -->
<!-- ここに書かない: 単一 run の inspect 結果、PR 番号、特定プロジェクトでの使い方。 -->

## 主要 API・概念

<!-- ここに書く: 安定して参照される API 名 / コンポーネント名 / SerializedProperty パス / 設計上の概念（例: MA Merge Armature の locking mode）。 -->
<!-- ここに書かない: 内部実装専用型、unstable な experimental API、ローカルプロジェクト固有の派生型。 -->

## 使い分け

<!-- ここに書く: 機能 A と機能 B の選択基準。利用者が判断に迷う分岐点とその根拠。 -->
<!-- ここに書かない: 個人の好み、特定プロジェクトでのコンベンション、根拠なき "おすすめ"。 -->

## 落とし穴

<!-- ここに書く: 再現可能な失敗モード（条件 + 症状 + 回避策）。inspect で裏取り済みのものを優先。 -->
<!-- ここに書かない: 一度しか観察していない single-run の事象、原因不明の不具合（→ 別途調査して原因が判明してから書く）。 -->

## 関連 knowledge

<!-- ここに書く: 同じツール群の他ファイルへの相対リンク（`[modular-avatar](./modular-avatar.md)` 形式）。分割した場合は分割先全部を列挙する。 -->
