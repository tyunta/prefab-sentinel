---
tool: copy-scale-adjuster
version_tested: "1.0.2"
last_updated: 2026-03-26
confidence: medium
---

# Copy Scale Adjuster

## 概要 (L1)

アバター本体に付与された MA Scale Adjuster (`ModularAvatarScaleAdjuster`) の値を、衣装側の対応ボーンに一括コピーする Editor 専用ユーティリティ。Modular Avatar の Setup Outfit ワークフローを補完する。

**解決する問題**: MA Merge Armature で衣装を統合する際、アバター側にスケール調整用の MA Scale Adjuster が設定されていても、衣装側のボーンにはコピーされない。スケール不一致により衣装がアバターの体型に合わない問題が起きる。本ツールはアバター側の Scale Adjuster を衣装側にパス一致でコピーし、手動での逐一コピーを不要にする。

**位置づけ**: MA 本体で正式サポートされるまでの暫定ツール（README 原文: 「MA側で正式実装されるまでの繋ぎに使ってください」）。

**依存関係**: Modular Avatar >= 1.9.16 (`vpmDependencies`)。MA の `ModularAvatarScaleAdjuster` と `ModularAvatarMergeArmature` を直接参照する。

**アーキテクチャ**: Editor-only パッケージ。Runtime コンポーネント (MonoBehaviour) を持たない。全コードが `#if UNITY_EDITOR` で囲まれ、asmdef の `includePlatforms` も `["Editor"]` のみ。ビルド後のアバターには一切残らない。

**作者**: Rinna Koharu (Rerigferl)。MIT No Attribution ライセンス。

**最新バージョン**: 1.0.2 (2024-09-26)。VPM リポジトリ `https://rerigferl.github.io/vpm/` から配布。

## コンポーネント一覧 (L1→L2)

### Editor ユーティリティ (ランタイムコンポーネントなし)

本パッケージは MonoBehaviour を含まず、Editor メニューコマンドとして動作する。

| クラス名 | 種別 | 用途 |
|---|---|---|
| `CopyScaleAdjuster` | static Editor class | MA Scale Adjuster のコピーロジック本体。メニュー登録・検索・コピー実行を担う |
| `RelativePathBuilder` | ref struct (Editor utility) | ボーンの相対パスを prefix/suffix 除去付きで構築するヘルパー。stackalloc + ArrayPool による低アロケーション実装 |

### メニューエントリ

| メニューパス | トリガー | 動作 |
|---|---|---|
| `GameObject/Modular Avatar/Setup Outfit with Copy Scale Adjuster` | 右クリック > Modular Avatar | MA Setup Outfit を実行した後に Scale Adjuster コピーを連続実行 |
| `GameObject/Modular Avatar/Copy Scale Adjuster` | 右クリック > Modular Avatar (MA Merge Armature 付きオブジェクト選択時) | 既存の MA Merge Armature に対して Scale Adjuster コピーのみ実行 |
| `CONTEXT/ModularAvatarMergeArmature/Copy Scale Adjuster` | Inspector の MA Merge Armature コンテキストメニュー | 同上 |

### バリデーション条件

メニューが有効になる条件: 選択オブジェクトまたはコンテキスト対象に `ModularAvatarMergeArmature` があり、かつその `mergeTargetObject` 配下に `ModularAvatarScaleAdjuster` が 1 つ以上存在すること。

## 操作パターン (L2)

### 衣装追加時の Scale Adjuster コピー

1. 衣装プレファブをアバター直下に配置する
2. 衣装オブジェクトを右クリック → `Modular Avatar` → `Setup Outfit with Copy Scale Adjuster`
3. MA の Setup Outfit が実行され、Merge Armature が自動配置される
4. 続けてアバター側の Scale Adjuster が衣装側の対応ボーンにコピーされる
5. Undo は一括グループ化されており、1 回の Ctrl+Z で全操作を取り消せる

**コピーのマッチングロジック**:
- アバター側の `mergeTargetObject` 配下にある全 `ModularAvatarScaleAdjuster` を収集
- 各コンポーネントのボーンパスから MA Merge Armature の `prefix` / `suffix` を除去して相対パスを算出
- 衣装側で同じ相対パスの Transform を `Transform.Find` で検索
- 一致するボーンが見つかった場合、既存の Scale Adjuster があれば値を上書き、なければ新規追加
- ボーン名が一致しないと（prefix/suffix 除去後の名前不一致）コピーされない

### 既存衣装への再コピー

1. MA Merge Armature が付いた衣装オブジェクトを選択
2. 右クリック → `Modular Avatar` → `Copy Scale Adjuster`、または Inspector の MA Merge Armature コンポーネントのコンテキストメニューから `Copy Scale Adjuster`
3. アバター側の現在の Scale Adjuster 値が衣装側に同期される

**ユースケース**: アバター側のスケール調整値を変更した後、衣装側にも反映したい場合。

## SerializedProperty リファレンス (L3)

検証方法: Editor/CopyScaleAdjuster.cs + Editor/RelativePathBuilder.cs のソースコード読み + .meta ファイルの GUID 抽出。PF-TEST 未インストールのため inspect 実測なし。

### 本パッケージ固有のランタイムコンポーネント

**なし。** 本パッケージは Editor-only であり、MonoBehaviour を定義しない。Script GUID は Editor スクリプトのものしか存在しない。

### Editor Script GUID テーブル (参考)

| クラス名 | GUID | 備考 |
|---|---|---|
| `CopyScaleAdjuster` | `ab128fb950ac04b498478cd01f9eabbc` | Editor-only。Prefab/Scene に埋め込まれない |
| `RelativePathBuilder` | `4e1528774950b4e4fa5d9dd3f8cd6591` | Editor-only。ref struct のためコンポーネントではない |

### 操作対象の MA コンポーネント

本ツールは以下の MA コンポーネントを読み書きする。フィールド詳細は `knowledge/modular-avatar.md` を参照。

| コンポーネント | GUID | 本ツールでの用途 |
|---|---|---|
| `ModularAvatarScaleAdjuster` | `09a660aa9d4e47d992adcac5a05dd808` | コピー元/コピー先。`EditorUtility.CopySerializedIfDifferent` で全フィールドを丸ごとコピー |
| `ModularAvatarMergeArmature` | `2df373bf91cf30b4bbd495e11cb1a2ec` | `mergeTargetObject`, `prefix`, `suffix` を読み取り、コピー先のボーンパスを算出 |

### MA Scale Adjuster のフィールド (コピー対象)

`EditorUtility.CopySerializedIfDifferent` はコンポーネントの全 SerializedProperty を一括コピーするため、個別フィールドの選択はない。参考として MA Scale Adjuster のフィールドを再掲する。

| propertyPath | 型 | 説明 |
|---|---|---|
| `m_Scale` | Vector3 | スケール値 (デフォルト: 1,1,1) |
| `legacyScaleProxy` | Transform | [FormerlySerializedAs("scaleProxy")] |

### 設計上の注意点

- **Editor-only のため Prefab/Scene への痕跡なし**: 本ツールは MA Scale Adjuster コンポーネントを追加・上書きするが、ツール自体のコンポーネントは Prefab/Scene に記録されない。prefab-sentinel の `find_referencing_assets` で本パッケージの GUID を検索しても結果は返らない。
- **CopySerializedIfDifferent の挙動**: コンポーネントの全 SerializedProperty をコピーする Unity API。差分がある場合のみ書き込まれ、Undo 対象になる。`legacyScaleProxy` (Transform 参照) もコピーされるが、これはアバター側のボーンを参照しているため衣装側では無効な参照になりうる（通常 null のため実害なし）。
- **MA バージョン分岐**: asmdef の `versionDefines` で `MODULAR_AVATAR_VERSION_1_10` を定義。MA 1.10 以降は `SetupOutfit.SetupOutfitUI` を直接呼び出し、それ以前はリフレクションで `EasySetupOutfit.SetupOutfit` を呼ぶ。
- **ボーン名不一致時のサイレントスキップ**: prefix/suffix 除去後のパスが衣装側に見つからない場合、警告なくスキップする。部分的にしかコピーされないケースに注意。

## 実運用で学んだこと

(なし -- 実運用検証で追記する)
