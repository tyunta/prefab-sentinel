# Editor Bridge バッチ操作・シーン構築支援 設計レポート

**日付:** 2026-03-27
**背景:** ForgottenTemple ゲームワールドのシーン構築作業で、MCP ツールによるオブジェクト大量生成の非効率が顕在化

---

## 問題

現在の Editor Bridge ツールセットでは、シーン内に1つの GameObject を配置するのに **4〜6回の MCP 往復** が必要。

```
1. editor_execute_menu_item("GameObject/3D Object/Cube")  — 生成
2. editor_rename("/Cube", "Wall_N")                        — リネーム
3. editor_set_parent("/Wall_N", "/Lobby")                  — 階層移動
4. editor_set_property(Scale)                              — スケール設定
5. editor_set_property(Position)                           — 位置設定
6. editor_add_component("BoxCollider")                     — コンポーネント追加
```

ロビー1部屋（床+壁4+扉+圧力板2 = 8オブジェクト）で **約40〜48回**、
全6エリア＋接続通路＋オーディオ配線で **推定300〜500回** の MCP 呼び出しが発生する。

---

## 不足している機能

### カテゴリ1: オブジェクト生成の簡素化

#### `editor_create_empty`
- **概要:** 名前・親・位置を指定して空の GameObject を1回で作成
- **現状:** `GameObject/Create Empty` → rename → set_parent → set_property(position) の4回
- **パラメータ案:**

```
name: string         — オブジェクト名
parent_path: string  — 親の hierarchy path（空 = scene root）
position: string     — ローカル座標 "x,y,z"
```

#### `editor_create_primitive`
- **概要:** プリミティブ形状（Cube, Sphere, Cylinder, Quad, Capsule, Plane）を名前・親・位置・スケール付きで1回で作成
- **現状:** メニュー → rename → set_parent → set_property(scale) → set_property(position) の5回
- **パラメータ案:**

```
primitive_type: string  — "Cube" | "Sphere" | "Cylinder" | "Quad" | "Capsule" | "Plane"
name: string            — オブジェクト名
parent_path: string     — 親の hierarchy path
position: string        — ローカル座標 "x,y,z"
scale: string           — ローカルスケール "x,y,z"
rotation: string        — オイラー角 "x,y,z"（省略可）
```

### カテゴリ2: バッチ操作

#### `editor_batch_create`
- **概要:** 複数オブジェクトの生成を1リクエストで実行
- **現状:** 1オブジェクトずつ個別に生成
- **パラメータ案:**

```json
{
  "objects": [
    {
      "type": "Cube",
      "name": "Floor",
      "parent": "/Lobby",
      "position": "0,0,0",
      "scale": "10,0.1,10"
    },
    {
      "type": "Cube",
      "name": "Wall_N",
      "parent": "/Lobby",
      "position": "0,1.5,5",
      "scale": "10,3,0.2"
    }
  ]
}
```
- **効果:** ロビー全体（8オブジェクト）を1回の MCP 呼び出しで構築可能

#### `editor_batch_set_property`
- **概要:** 複数のプロパティ設定を1リクエストで実行
- **現状:** 1プロパティずつ `editor_set_property` を個別呼び出し
- **パラメータ案:**

```json
{
  "operations": [
    {
      "hierarchy_path": "/Lobby/PlateA",
      "component_type": "PressurePlate",
      "property_name": "roomNumber",
      "value": "0"
    },
    {
      "hierarchy_path": "/Lobby/PlateA",
      "component_type": "PressurePlate",
      "property_name": "pairedPlate",
      "object_reference": "/Lobby/PlateB:PressurePlate"
    }
  ]
}
```
- **効果:** PressurePlate 1つの全配線（5フィールド）を1回で完了

### カテゴリ3: コンポーネント追加の強化

#### `editor_add_component` の初期値サポート
- **概要:** コンポーネント追加時に初期プロパティ値も同時に設定
- **現状:** add_component → set_property x N の複数回
- **パラメータ案（既存 API 拡張）:**

```
hierarchy_path: "/Lobby/PlateA"
component_type: "BoxCollider"
properties: {
  "m_IsTrigger": "true",
  "m_Size": "1,0.05,1"
}
```
- **効果:** BoxCollider(isTrigger=true) + サイズ設定を1回で

### カテゴリ4: シーン操作

#### `editor_open_scene`
- **概要:** 指定したアセットパスのシーンを開く
- **現状:** ユーザーに Unity Editor で手動ダブルクリックを依頼
- **パラメータ案:**

```
scene_path: string  — "Assets/ForgottenTemple/Scenes/ForgottenTemple.unity"
mode: string        — "single" | "additive"（省略時 = single）
```

#### `editor_save_scene`
- **概要:** 現在のシーンを保存
- **現状:** ユーザーに Ctrl+S を依頼するか、diagnostics の警告に依存
- **パラメータ案:**

```
path: string  — 省略時は現在のシーンを上書き保存
```

---

## 優先度

| 優先度 | 機能 | 理由 |
|--------|------|------|
| **P0** | `editor_create_primitive` | 最も呼び出し回数を削減（5回→1回） |
| **P0** | `editor_create_empty` | コンテナ/空オブジェクト作成の基本操作 |
| **P1** | `editor_batch_set_property` | 配線作業の効率化（N回→1回） |
| **P1** | `editor_open_scene` | 自動化のボトルネック解消 |
| **P1** | `editor_save_scene` | シーン保存の自動化 |
| **P2** | `editor_batch_create` | P0 の上位互換、より複雑な実装 |
| **P2** | `editor_add_component` 初期値 | 既存 API の拡張、互換性考慮が必要 |

---

## 効果試算

ForgottenTemple シーン構築（6エリア + 通路 + 配線）の場合：

| 方式 | 推定 MCP 呼び出し回数 |
|------|----------------------|
| 現状（個別操作のみ） | 300〜500 回 |
| P0 実装後（create_primitive/empty） | 100〜150 回 |
| P0 + P1 実装後（+ batch_set_property） | 50〜80 回 |
| 全機能実装後 | 20〜30 回 |

---

## 暫定対策

上記機能の実装前に大量のシーン構築を行う場合、**一時的な Editor スクリプト**（`[MenuItem]` 付き C#）を作成し `editor_execute_menu_item` で実行するアプローチが有効。Unity の API で全内部管理（fileID、コンポーネント参照）を自動処理できる。

---

## 実装上の注意

- バッチ操作は Undo グループでまとめる（`Undo.SetCurrentGroupName` → 1回の Undo で全部戻せるように）
- `editor_batch_create` のレスポンスには作成した全オブジェクトの hierarchy path を返す（後続の配線で必要）
- `editor_open_scene` は未保存変更がある場合の処理（保存確認ダイアログ or 自動保存）を決める必要がある
- Bridge プロトコルバージョンの互換性管理（今回 `editor_set_parent` でバージョン不一致が発生した）
