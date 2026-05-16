# knowledge/ Style Guide

`knowledge/*.md` ファイルは、VRChat / Unity エコシステムツール（ModularAvatar、liltoon、VRCFury、AvatarOptimizer 等）のドメイン知識をセッション横断で再利用するための正本。本ファイルは編集規約・ファイル粒度・「何を書き、何を書かないか」の判断基準を定める。新規ファイルのスケルトンは [TEMPLATE.md](./TEMPLATE.md) を使う。

判断に迷ったら本ファイルを再読する。L1〜L3 セクション（概要 / 操作パターン / SerializedProperty）の構造は本ガイドではなく各ファイル側で自然に確立されたパターンに従う。

## Frontmatter convention

すべての `knowledge/*.md` ファイルは先頭に YAML frontmatter を持ち、以下の 4 つの必須フィールドを宣言する。値の型は文字列。

| フィールド | 値の規約 |
|------------|----------|
| `tool` | パッケージ名 / コンポーネント名のスラッグ。ファイル名（拡張子除く）と一致させる。例: `modular-avatar` / `liltoon` / `vrcfury` |
| `version_tested` | 動作確認したパッケージのバージョン。`"1.16.2"` のように引用符付き文字列で記述（YAML が float に誤解釈するのを防ぐ） |
| `last_updated` | 最終更新日。`2026-03-26` の ISO-8601 形式 |
| `confidence` | `low` / `medium` / `high` のいずれか。`high` は inspect 実測値で裏取り済み、`medium` は再現確認済み、`low` は推測または単発観察 |

`confidence: low` のナレッジを判断材料にする場合は、判断時にその旨をユーザーに伝える運用（[CLAUDE.md `## VRChat エコシステムナレッジの自動適用`](../CLAUDE.md#vrchat-エコシステムナレッジの自動適用) 参照）。inspect 実測値で裏取りができたら `confidence: high` に昇格し、同時に `version_tested` / `last_updated` を更新する。

frontmatter のフィールド順は表の通り（`tool` → `version_tested` → `last_updated` → `confidence`）を推奨する。追加のフィールドを足したい場合は本ガイドに追記してから既存ファイルを更新する（個別ファイル単独で項目を追加しない）。

## What counts as "pure knowledge"

`knowledge/` に書くのは「セッションをまたいで再利用できる、ツールのドメイン知識」のみ。以下の判断基準に従う。

書く:

- ツールの API・コンポーネント・SerializedProperty パスの安定した事実
- 再現可能な失敗モードと回避策（条件と症状を 1 セットで記述）
- 設計トレードオフの判断基準（A と B の使い分け）
- inspect で裏取り済みのフィールド型・初期値
- SDK / パッケージ由来の安定 GUID（DLL アセンブリ GUID、SDK スクリプトの GUID 等。配布物ごとに固定で、プロジェクトをまたいで YAML 上の型識別に使える）

書かない:

- 単一の作業 / 単一の incident に固有の状況（再利用価値がないため knowledge には残さない）
- 「今回は〜してみた」「〜の PR で対応した」のような work-log 表現
- プロジェクト固有アセットの GUID / fileID（特定プロジェクトの prefab・スクリプト・シーンの識別子。再利用価値がない）。SDK / パッケージ由来の安定 GUID は逆に「書く」側
- ユーザーの好み・プロジェクト固有の事情（→ `memory/` に置く、[CLAUDE.md `### memory との棲み分け`](../CLAUDE.md#memory-との棲み分け) 参照）

迷ったら「半年後の自分 / 他のエージェントが、同じツールを別プロジェクトで触るときに役に立つか」で判定する。No なら `knowledge/` ではない。

## Work-log fingerprints (remove on sight)

以下のパターンが本文中に紛れていたら work-log 汚染と見なし、書き換えるか削除する。grep ベースで定期的に洗い出す:

- 裸の日付: `2026-03-26 に確認した` / `先週` / `今朝` 等。`version_tested` / `last_updated` の frontmatter で十分。
- インライン PR / issue 参照: `PR #224 で対応` / `issue #129 暫定` 等。仕様の根拠としての issue リンクは可（例: `issue #243 仕様参照`）だが、「いつ・誰が対応したか」は work-log。
- バージョンスタンプの観察ノート: `v0.5.110 時点では〜だった` 等。`version_tested` で代替する。
- 日記表現: `今回は` / `してみた` / `わかった` / `気づいた` 等の一人称・work-diary 語彙。impersonal な declarative に書き換える。

検出 grep の例:

```bash
grep -nE '今回|してみた|わかった|気づいた|先週|PR #[0-9]+ で|v0\.[0-9]+\.[0-9]+ 時点' knowledge/*.md
```

ヒットしたら個別判断: 仕様根拠の issue リンクは残す、それ以外は declarative に書き換える。

## File granularity

1 ファイル 1 ツール / 1 トピックを基本とし、目安は 200〜600 行。500 行を超えるファイルは概ね分割候補。

- ツール本体（ModularAvatar / liltoon 等）と、そのツールに紐づく特定パターン（`modular-avatar-merge-armature-pitfalls` 等）はファイルを分けてよい。L1（概要）はツール本体に、L2/L3（操作 / SerializedProperty）はパターン側に集約する。
- 200 行未満のファイルでも、内容が完結して再利用される単位ならば分割しない。「同じセッション内で必ず一緒に読む」関連ファイルがある場合は、本文末尾の「関連 knowledge」セクションで相互リンクを張る。
- 600 行を超える場合の分割は、§（H2）境界で行う。L1 / L2 / L3 のレベル間境界での分割は避ける（読み手が「ツールの概要」を理解した直後に「操作パターン」を続けて読むことが多い）。
- 分割した場合は各ファイル末尾の `## 関連 knowledge` セクションで相互リンクを張り、L1 ファイル側に「`A/B/C/D` に分割した」と一行で記録する（読み手が起点ファイルから他の分割先に到達できることを保証する）。

## Disposition checklist (keep / edit / merge / split / delete)

既存の `knowledge/*.md` 群を本ガイドに照らして 1 ファイルずつ評価する手順。各ファイルに 1 つの disposition を割り当てる。

判定フロー（上から順に評価し、最初に該当した disposition を採用する）:

1. 内容が「ツールのドメイン知識」ではない（work-log / 単発観察 / project-specific noise）→ **Delete**
2. 内容は正しく `knowledge/` に属するが、別ファイルに同じ事実が重複しており、こちらの粒度が下位 / 冗長 → **Merge**（target を明示）
3. 1 ファイル内に 2 つ以上の独立トピックが同居しており、`File granularity` の 1 ツール 1 トピック規則に反する → **Split**（split 後の各ファイル名を明示）
4. 残るが本ガイドの規約違反（frontmatter 欠落 / work-log 汚染 / 古いバージョン情報 / `## 関連 knowledge` 欠落など）がある → **Edit**
5. それ以外 → **Keep**

判定の根拠は 1 行で書き留める。例:

| ファイル | 判定 | 根拠 |
|----------|------|------|
| `xxx.md` | Edit | frontmatter 4 フィールド中 `confidence` 欠落、本文 work-log 汚染 3 件 |
| `yyy.md` | Merge → `zzz.md` | 内容が `zzz.md` の §2 と完全重複、粒度はこちらが下位 |
| `aaa.md` | Split → `aaa-component.md` + `aaa-pipeline.md` | 1 ファイル内にコンポーネント仕様と CI パイプライン手順が同居 |

triage の結果は単一の markdown table（38 行）として記録し、issue #285 のコメントに掲示する。Edit / Merge / Split / Delete のいずれかに該当したファイルは、本ガイドが正本となった後にフォローアップ issue を 1 つ作成して個別対応する（具体的な issue 命名規約は #285 の運用に従う）。
