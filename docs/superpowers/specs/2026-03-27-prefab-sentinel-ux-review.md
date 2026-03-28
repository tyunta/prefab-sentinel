# Prefab Sentinel MCP ツール UX レビューレポート

**日付:** 2026-03-27
**作業内容:** VRChat ゲームワールド「忘れられた神殿」のシーン構築（ゼロからの新規構築）
**使用者:** Claude Opus 4.6 (AI エージェント)
**Bridge C# ファイル:** v0.5.102 / v0.5.110 のソースを Unity プロジェクトにコピー（ただし BridgeVersion 定数は `"0.5.82"` のまま未更新）
**Plugin バージョン:** 0.5.96 → 0.5.102 → 0.5.110

---

## 作業概要

| 項目 | 数値 |
|------|------|
| 作成した UdonSharp スクリプト | 8本 |
| 作成した UdonSharpProgramAsset | 8個 |
| シーン内オブジェクト | 約80個（32ルート + 子オブジェクト群） |
| 配線したプロパティ | 約60フィールド |
| コンポーネント追加 | 約40回 |
| エリア数 | 6（ロビー + 4部屋 + 祭壇）+ 通路5本 |

---

## 良い点

### 1. editor_recompile + editor_console の即時フィードバックループ

スクリプトを書いて `editor_recompile` → `editor_console(error)` で即座にコンパイルエラーを確認できた。UdonSharp 固有のエラー（`CompareTag` が Udon 非公開）も即座に検出・修正できた。

**具体例:** LightBeam.cs の `hit.collider.CompareTag("Mirror")` が Udon 非対応 → `editor_console` で検出 → mirrors 配列参照方式に設計変更 → 再コンパイル → エラー 0 件確認、の流れが数分で完了。

### 2. editor_create_udon_program_asset の自動化

以前は手動で ScriptableObject を作成する必要があった UdonSharpProgramAsset を、8個一気に作成できた。これは大きな省力化。

### 3. editor_add_component の型解決

`"DoorController"`, `"VRC.SDK3.Components.VRCPickup"` など、シンプル名でも完全修飾名でもコンポーネントを追加できる柔軟性は高い。型解決の失敗もなかった。

### 4. editor_set_property の object_reference 構文

`"/Lobby/PlateA:PressurePlate"` のようにパス:型名で参照先を指定できるのは直感的。シーン内オブジェクトの参照もアセットパスの参照も同じ構文で扱える統一性が良い。

### 5. editor_list_roots / editor_list_children による状態確認

操作の前後でシーン状態を確認でき、安心して作業を進められた。

### 6. editor_delete による不要オブジェクトの除去

HW シーンからコピーした ForgottenTemple シーンから不要オブジェクト（Floor, HelloButton, ToggleTarget）を即座に削除できた。クリーンなシーン準備がスムーズに行えた。

### 7. バッチ操作の Undo グループ化

`editor_batch_create` と `editor_batch_set_property` は `Undo.CollapseUndoOperations` で操作をまとめる。22オブジェクトを一括作成しても Ctrl+Z 1回で全て元に戻せる。バッチ操作でありながら安全性が確保されている。

### 8. 新バッチツール（v0.5.110 で追加）の劇的効果

| ツール | 効果 |
|--------|------|
| `editor_batch_create` | 1回で5〜22オブジェクト作成。ロビー全体が1コマンド |
| `editor_batch_set_property` | 1回で11〜29プロパティ配線。全部屋の配線を3回で完了 |
| `editor_create_primitive` | 名前+親+位置+スケールを1回で指定（batch_create 内で使用） |
| `editor_create_empty` | 同上（空オブジェクト版） |
| `editor_save_scene` | シーン保存の自動化（手動 Ctrl+S 不要） |

※ `editor_open_scene` は v0.5.110 で追加されたが、今回のワークフローではユーザーに手動でシーンを開いてもらったため未検証。

**数値比較:**
- バッチツール導入前の見積り: 300〜500 MCP 呼び出し（推定）
- バッチツール導入後の実績: 約65回（実測、下記の定量的評価セクション参照）
- **約78〜87% の呼び出し削減**

---

## 悪い点・問題点

### 1. Bridge バージョン不一致が繰り返し発生

**深刻度: 高**

Plugin を更新するたびに Unity 側の Bridge C# ファイルを手動コピーする必要があった。しかも：
- v0.5.102 の Bridge を Unity にコピー → `System.ReflectionTypeLoadException` のコンパイルエラー → 手動修正が必要
- v0.5.110 でバッチツール追加 → 再度コピー → 同じコンパイルエラーが再発

**問題の根本:** Bridge の `ResolveComponentType` メソッド（line 2356 付近）で `System.ReflectionTypeLoadException` を使っているが、Unity のデフォルトアセンブリ設定では完全修飾（`System.Reflection.ReflectionTypeLoadException`）が必要。なお `HandleListMenuItems`（line 2765 付近）では既に正しい完全修飾名が使われており、コードベース内で不整合がある。

**影響:** 毎回の Plugin 更新で手動修正が必要。ユーザーが技術的に対応できない場合、Bridge 更新が事実上不可能。

### 2. 配列プロパティの設定が未サポート

**深刻度: 高**

`FallRespawn.respawnPoints`（`Transform[]` 型）の配列サイズ設定と要素設定ができなかった。

```
editor_set_property: respawnPoints.Array.size = 6
→ "Unsupported property type: ArraySize"
```

**影響:** 配列型 SerializeField を持つコンポーネントの配線が MCP 経由で完結しない。手動 Inspector 操作が残る。

### 3. editor_execute_menu_item の制限

`File/New Scene` が危険パスとして拒否される。シーンの新規作成ができないため、既存シーンをコピーして不要オブジェクトを削除するワークアラウンドが必要だった。

### 4. LightBeam の mirrors 配列も未配線のまま

配列プロパティ未サポートの影響で、LightBeam の `mirrors` フィールド（`MirrorRotator[]`）も MCP 経由で配線できなかった。

### 5. プロトコルバージョン不一致のエラーメッセージが不親切

`UNITY_BRIDGE_PROTOCOL_VERSION` エラーが出ても、具体的にどのバージョンが必要か、どう更新すればいいかの情報がない。

---

## 改善提案

### P0（必須）

#### 1. Bridge 自動デプロイ機構
Plugin インストール/更新時に Unity プロジェクトの Bridge C# ファイルを自動更新する仕組み。少なくとも `activate_project` 時にバージョンチェックして警告を出す。

#### 2. Bridge の ReflectionTypeLoadException 修正
`ResolveComponentType` メソッド（line 2356 付近）の `System.ReflectionTypeLoadException` → `System.Reflection.ReflectionTypeLoadException` に修正。`HandleListMenuItems` は既に正しい形なので修正不要。加えて `BridgeVersion` 定数（line 20）がハードコード `"0.5.82"` のまま更新されていない問題も修正すべき。

#### 3. 配列プロパティの読み書きサポート
`ArraySize` プロパティタイプのサポート追加。具体的には：
- 配列サイズの設定: `respawnPoints.Array.size = 6`
- 配列要素の設定: `respawnPoints.Array.data[0]` への object_reference 設定
- `editor_batch_set_property` でも配列操作を可能に

### P1（重要）

#### 4. editor_batch_add_component
`editor_add_component` のバッチ版。複数オブジェクトに一括でコンポーネントを追加。現状は1オブジェクトずつ呼ぶ必要がある。

#### 5. プロトコルバージョン不一致時の詳細エラー
```
現在: "Bridge protocol version mismatch."
改善案: "Bridge protocol v1, required v2. Update Bridge: copy tools/unity/*.cs to Assets/Editor/"
```

#### 6. editor_execute_menu_item の安全な代替パス
`File/New Scene` の代わりに `editor_create_scene(path)` のような専用ツールを提供。

### P2（あれば嬉しい）

#### 7. editor_batch_add_component
複数オブジェクトへのコンポーネント一括追加。現状は1オブジェクトずつ `editor_add_component` を呼ぶ必要がある。なお、個別の `editor_add_component` は既に `properties` パラメータで初期値設定をサポートしている（例: `BoxCollider` + `m_IsTrigger=true` を1回で設定可能）。

#### 8. シーン構築テンプレート
よくある構成（床+壁4+天井の部屋、通路）をテンプレートとして1コマンドで生成。

#### 9. 配線検証の自動化
`inspect_wiring` を全ルートオブジェクトに対して自動実行し、null 参照を一覧するツール。

---

## 定量的な評価

### MCP 呼び出し効率（全作業通じて）

| フェーズ | 呼び出し回数 | 備考 |
|----------|-------------|------|
| スクリプト作成 + コンパイル | ~12 | recompile, console, create_udon_program_asset x8 |
| シーン構造物作成 | ~10 | batch_create x7 + 個別操作数回 |
| コンポーネント追加 | ~30 | add_component（バッチ版があれば1/3に） |
| 配線 | ~8 | batch_set_property x5 + 個別数回 |
| シーン管理 | ~5 | list_roots, save_scene, open_scene |
| **合計** | **~65** | バッチツールなしなら推定 400+ |

### 時間効率

- バッチツール導入前（v0.5.102）: ロビー1部屋の壁2枚に約20分 → 全体完了は推定数時間
- バッチツール導入後（v0.5.110）: 全6エリア + 通路 + 配線が約30分で完了

---

## 結論

Prefab Sentinel の Editor Bridge は **Unity シーンのプログラマティックな構築・配線において非常に有効** であることが実証された。特に v0.5.110 で追加されたバッチ操作ツールは劇的な効率改善をもたらした。

最大の課題は **Bridge のデプロイ・更新フロー** と **配列プロパティの未サポート** の2点。これらが解決されれば、AI エージェントによるシーン構築ワークフローは実用レベルに達する。
