---
tool: floor-adjuster
version_tested: "1.1.2"
last_updated: 2026-03-26
confidence: medium
---

# Floor Adjuster

## 概要 (L1)

非破壊にアバターの上下位置（床位置）を調整するツール。NDMF プラグインとして動作し、ビルド時にアバターの高さオフセットを適用する。最終アバターにはランタイムコンポーネントが残らない（IEditorOnly 実装）。

**解決する問題**: VRChat アバターの足が地面にめり込む、または浮いている場合の位置補正。手動でボーンやスケールを調整する煩雑さを解消する。

**NDMF との関係**: NDMF >= 1.1.1 に依存。`BuildPhase.Transforming` フェーズで Modular Avatar の後に実行される（`AfterPlugin("nadena.dev.modular-avatar")`）。

**2 つの方式**:
- **by skeleton（新方式, 1.1.0+）**: Humanoid Avatar の `m_Avatar.m_Human.data.m_Scale` を書き換えることで初期位置を変更する。GameObject の Transform.position.y で床高さを指定する。
- **by scale（旧方式）**: Armature のスケールを変更して Hips ボーンを移動し、Hips にスケールの逆数を掛けて見た目の大きさを保つ。副作用として前後位置もズレるため ViewPoint を補正する。

**推奨**: 新方式（by skeleton）。旧方式からの変換ボタンが Inspector に用意されている。

**配布元**: VPM リポジトリ `https://vpm.narazaka.net/` / GitHub `https://github.com/Narazaka/FloorAdjuster`。ライセンス: Zlib。

## コンポーネント一覧 (L1->L2)

### 床位置調整

| コンポーネント名 | 用途 | 典型的な使用場面 |
|---|---|---|
| FloorAdjuster (by scale) | Armature スケール変更による床位置調整（旧方式） | Armature オブジェクトに配置。Height で高さ、Hips で対象ボーンを指定 |
| SkeletalFloorAdjuster (by skeleton) | Humanoid Avatar データ書き換えによる床位置調整（新方式） | アバター直下に配置。Transform の Y 位置で床高さを指定。フィールドなし |

**配置ルール**:
- FloorAdjuster: アバターの Armature オブジェクトに配置する。Armature 以外に付けると Inspector に警告が出る。
- SkeletalFloorAdjuster: アバタールート直下の子 GameObject に配置する。右クリックメニュー「Setup FloorAdjuster」で自動セットアップされる。
- 同一アバターに複数の FloorAdjuster / SkeletalFloorAdjuster が存在すると警告が出る。Inspector に「他の Floor Adjuster を削除する」ボタンが表示される。
- ビルド時は FloorAdjuster を先に検索し、見つからなければ SkeletalFloorAdjuster を検索する（両方存在する場合は FloorAdjuster が優先）。

**同梱プレファブ**: `FloorAdjuster.prefab` — SkeletalFloorAdjuster コンポーネント付きの GameObject。アバター直下にドラッグして Y 位置を調整するだけで使える。

## 操作パターン (L2)

### 新方式（by skeleton）セットアップ

1. アバターを右クリック → 「Setup FloorAdjuster」を実行
2. アバター直下に「FloorAdjuster」オブジェクトが作成される（SkeletalFloorAdjuster コンポーネント付き）
3. Scene ビューまたは Inspector で FloorAdjuster オブジェクトの Y 位置を上下に移動して床高さを設定
4. ビルド時に Humanoid Avatar の m_Scale が調整され、ViewPosition も自動補正される

**代替**: 同梱の `FloorAdjuster.prefab` をアバター直下にドラッグ&ドロップしても同等。

### 旧方式から新方式への変換

1. 旧方式（FloorAdjuster by scale）の Inspector で「新しい方式(by skeleton)に変換する」ボタンを押す
2. 自動的に SkeletalFloorAdjuster が作成され、旧コンポーネントが削除される

## SerializedProperty リファレンス (L3)

ソースバージョン: 1.1.2 (Shiratsume)
検証方法: .cs ソースコードの `[SerializeField]` / `public` フィールド読み取り + .meta ファイルの GUID 抽出（inspect 実測なし -> confidence: medium）

### Script GUID テーブル

| コンポーネント | GUID | 備考 |
|---|---|---|
| FloorAdjuster | `73f7270170c01b34c98d42afb9e73aac` | 旧方式 (by scale) |
| SkeletalFloorAdjuster | `0bd99fd6c0640c6418b069efe05cbc70` | 新方式 (by skeleton)、マーカーコンポーネント (フィールドなし) |

### コンポーネント別フィールド

#### FloorAdjuster (by scale)
| propertyPath | 型 | 説明 |
|---|---|---|
| `Height` | float | 床高さオフセット。正の値でアバターが上がる（床が下がる） |
| `Hips` | Transform | Hips ボーンの参照。未設定時は Editor が子の "Hips" を自動検索して補完する |

#### SkeletalFloorAdjuster (by skeleton)
シリアライズフィールドなし。Transform の position.y をビルド時に読み取って床高さとする。

### 設計上の注意点

- 両コンポーネントとも `IEditorOnly` を実装しており、ビルド後のアバターには残らない。
- SkeletalFloorAdjuster は Transform.position のみで動作するため、propertyPath でアクセスするシリアライズフィールドが存在しない。位置情報は標準の Transform プロパティ（`m_LocalPosition`）に格納される。
- FloorAdjuster の `Hips` フィールドは Editor 側で未設定時に `transform.Find("Hips")` で自動補完されるが、これはシリアライズされた値ではなく Inspector 表示時の動的補完。prefab-sentinel の `validate_refs` では null として検出される可能性がある。
- NDMF ビルドパス内で `FloorAdjuster` が `SkeletalFloorAdjuster` より優先される（先に `GetComponentInChildren` で検索される）。両方存在する場合、FloorAdjuster のみ処理される。
- FloorAdjuster (by scale) はアバターの Armature の `localScale` と Hips の `localScale` を変更するため、他のスケール操作ツール（MA Scale Adjuster 等）と併用する場合は競合に注意。

## 実運用で学んだこと

(なし -- Phase 3-4 の検証で追記する)
