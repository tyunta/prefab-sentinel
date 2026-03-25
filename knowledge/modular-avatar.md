---
tool: modular-avatar
version_tested: "1.16.2"
last_updated: 2026-03-26
confidence: low
---

# Modular Avatar

## 概要 (L1)

非破壊アバター改変ツール。NDMF (Non-Destructive Modular Framework) プラグインとして動作し、ビルド時にコンポーネントを処理してアバターを組み立てる。最終アバターにはランタイムコンポーネントが残らない。

**解決する問題**: 衣装・ギミック・メニューの追加/削除を手動で行う煩雑さ。ドラッグ&ドロップでプレファブを配置するだけで、ビルド時にアーマチュア統合・アニメーター統合・メニュー組み立てが自動実行される。

**NDMF との関係**: MA は NDMF >= 1.8.0 に依存。NDMF はビルドパイプラインを Resolving → Transforming → Optimizing の 3 フェーズで実行し、MA はこのフェーズ内で約 30 の Pass を逐次実行する。

**プラットフォーム**: 主対象は VRChat アバター (VRChat SDK 3.x)。一部 Pass は `[RunsOnPlatforms]` で VRChat 専用。1.13.0 から非 VRC プラットフォームの実験的サポートあり。

**最新バージョン**: 1.16.2 (2025-02-11)。VPM リポジトリ `https://vpm.nadena.dev/vpm.json` から配布。

## コンポーネント一覧 (L1→L2)

### アーマチュア・ボーン操作

| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| MA Merge Armature | 衣装のボーン階層をアバターのアーマチュアに統合する | 衣装プレファブのルートに配置。プレフィックス/サフィックスでボーン名マッチング。一致するボーンは参照を書き換え、一致しないボーンは子として追加 |
| MA Bone Proxy | オブジェクトを既存ボーンの子に配置する | ヘアピン、コライダー等の単一ボーンターゲットアクセサリ。Merge Armature より軽量 |
| MA Scale Adjuster | アバターのスケール調整 | スケール変更時のボーン位置補正 |
| MA World Fixed Object | ワールド座標に固定 | アバターサイズに依存しない位置固定オブジェクト |

**Merge Armature のロックモード**:
- Not locked: 編集時に独立
- Unidirectional (Base→Target): アバターに追従、手動調整を保持（デフォルト）
- Bidirectional: 双方向同期

### アニメーター・パラメーター

| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| MA Merge Animator | アニメーターコントローラを統合 | FX レイヤーを複数サブアニメーターに分割して管理。レイヤー優先度と Write Defaults 解析あり |
| MA Merge Motion (Blend Tree) | ブレンドツリーを Direct BlendTree 構造にマージ | パフォーマンス最適化。複数レイヤーを 1 つの Direct BlendTree に統合 |
| MA Parameters | パラメーター設定と名前空間管理 | パラメーター衝突回避。SHA256 ハッシュで一意名を生成。プレファブ間の名前分離 |
| MA Sync Parameter Sequence | パラメーター同期順序の制御 | ネットワーク同期の順序保証 |

### リアクティブオブジェクト

条件（メニューアイテムのパラメーター、親 GameObject の active 状態）に基づいてアニメーションを自動生成するシステム。

| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| MA Object Toggle | GameObject の active/inactive を条件で切替 | 衣装パーツのオン/オフ。activeSelf プロキシパラメーターを生成 |
| MA Shape Changer | ブレンドシェイプの値を条件で変更・削除 | 衣装着用時の体メッシュ貫通防止シェイプ駆動 |
| MA Material Swap | マテリアルを条件で差替 | カラーバリエーション切替 |
| MA Material Setter | マテリアルプロパティを条件で変更 | 色やパラメーターの条件付き変更 |
| MA Mesh Cutter | メッシュ頂点を条件で非表示 | NaNimation 技術でボーンウェイト操作。頂点フィルタ: ByAxis, ByMask, ByShape, ByBone |

**NaNimation**: メッシュ複製なしで条件付き頂点非表示を実現する技術。NaN ボーンを追加し、非表示にしたい頂点のボーンウェイトをそこへ割り当て、ボーンスケールのアニメーションで表示/非表示を制御する。

### メニュー

| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| MA Menu Item | VRChat Expression メニュー項目を階層内に分散配置 | プレファブ内にメニュー項目を同梱。ビルド時に MenuInstallHook が収集・組み立て |
| MA Menu Installer | メニューのインストール先を指定 | レガシー API。Menu Item の方が推奨 |
| MA Menu Install Target | メニューの挿入位置を指定 | 既存メニュー構造への差し込み |
| MA Menu Group | メニュー項目のグループ化 | サブメニュー構造の構築 |

### メッシュ・描画設定

| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| MA Mesh Settings | プローブアンカー・バウンズの設定 | 描画の一貫性確保。衣装プレファブに付与 |
| MA Blendshape Sync | ブレンドシェイプ状態の同期 | 衣装と本体メッシュのシェイプキー連動 |
| MA Remove Vertex Color | 頂点カラーの除去 | シェーダー互換性問題の回避 |
| MA Visible Head Accessory | 一人称で頭アクセサリを表示 | VRChat のヘッドボーン非表示を回避 |

### その他

| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| MA Replace Object | ビルド時にオブジェクトを差し替え | 条件付きオブジェクト置換 |
| MA Convert Constraints | Unity Constraints を VRChat 互換に変換 | VRChat の制約システムへの移行 |
| MA PhysBone Blocker | PhysBone の影響を遮断 | 衣装のボーンが PhysBone に巻き込まれるのを防止 |
| MA Platform Filter | プラットフォーム別のコンポーネント有効/無効 | Quest/PC で異なる構成 |
| MA MMD Layer Control | MMD ワールド互換レイヤー制御 | MMD ワールドでの互換性確保 |
| MA Move Independently | 独立した移動 | 特定オブジェクトの独立トランスフォーム制御 |
| MA VRChat Settings | VRChat 固有設定の上書き | アバター全体の VRChat パラメーター |
| MA Rename VRChat Collision Tags | PhysBone/Contact コリジョンタグのリネーム | プレファブ間のタグ衝突回避 |
| MA World Scale Object | ワールドスケール制約の適用 | ワールドスケール基準のオブジェクト |

## 操作パターン (L2)

### 衣装追加の基本パターン

1. 衣装プレファブをアバター直下にドラッグ&ドロップ
2. 衣装ルートに **MA Merge Armature** を配置、mergeTarget にアバターの Armature を指定
3. プレフィックス/サフィックスが自動補完される（衣装ボーン名から推測）
4. 必要に応じて **MA Mesh Settings** でバウンズ・アンカーを統一
5. ビルド時にボーン統合が自動実行される

### 衣装トグルの基本パターン

1. 衣装オブジェクトに **MA Object Toggle** を配置
2. 親に **MA Menu Item** (Toggle) を配置
3. ビルド時に自動的に: パラメーター生成 → アニメーション生成 → メニュー組み立て
4. 手動でアニメーターやパラメーターを作る必要がない

### 体メッシュ貫通防止パターン

1. 衣装プレファブに **MA Shape Changer** を配置
2. ターゲットにアバターの Body メッシュを指定
3. 衣装で隠れる部分のシュリンクシェイプキーを駆動
4. 衣装トグルの条件と連動させると、衣装オフ時は元に戻る

### アクセサリ追加パターン（単一ボーン）

1. アクセサリオブジェクトに **MA Bone Proxy** を配置
2. Target にアバターの対象ボーン（Head, Hand 等）を指定
3. Attachment Mode: 汎用プレファブなら "As child at root"、アバター専用なら "As child keep world pose"

### メニュー構築パターン

1. 各機能プレファブ内に **MA Menu Item** を配置
2. サブメニューが必要なら **MA Menu Group** で囲む
3. 配置先を指定するなら **MA Menu Install Target** を使用
4. ビルド時に MenuInstallHook が全 Menu Item を収集してメニューツリーを構築

### パラメーター衝突回避パターン

1. プレファブルートに **MA Parameters** を配置
2. "内部パラメーター" に指定したパラメーターは自動的に SHA256 ハッシュでリネーム
3. 同じプレファブを複数配置しても衝突しない

## SerializedProperty リファレンス (L3)

(未調査 — Phase 2 のソースコード分析で埋める)

## 実運用で学んだこと

(なし — Phase 3-4 の検証で追記する)
