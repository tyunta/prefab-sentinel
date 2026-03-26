# prefab-sentinel editor_set_camera / editor_get_camera

## 基本情報

| 項目 | 値 |
|------|---|
| MCP ツール名 | `editor_set_camera`, `editor_get_camera`, `editor_frame` |
| 対象 | Unity SceneView カメラ（Game ビューではない） |
| version_tested | prefab-sentinel 0.5.72 |
| last_updated | 2026-03-26 |
| confidence | high |

## L1: 確定仕様（ソースコード精読済み）

### Euler 規約
- **yaw=0 はカメラが +Z 方向を向く**（MCP 側の規約）。
- 内部実装: MCP の yaw に +180° して Unity の `SceneView.rotation` に渡す。Unity 内部では yaw=0 が背面(−Z)。
  ```csharp
  float internalYaw = (request.yaw + 180f) % 360f;
  sceneView.rotation = Quaternion.Euler(newPitch, internalYaw, 0f);
  ```

### 統合 API（v0.5.72+）

旧 Mode A / Mode B は廃止され、単一の API に統合。

**Pivot orbit（基本）:**
- `pivot`: 注視点（SceneView.pivot）
- `yaw`: 水平角。0=正面(+Z方向を見る)
- `pitch`: 垂直角
- `distance`: SceneView.size（小さいほどズーム）

**Position モード（新規）:**
- `position` + `look_at`: カメラ位置と注視先を指定。pivot=look_at、rotation と distance を自動逆算
- `position` + `yaw`/`pitch`: カメラ位置と向きを指定。pivot を逆算

**制約:**
- `position` と `pivot` の同時指定はエラー
- `look_at` は `position` が必須

### レスポンス
`set_camera` は `previous`（変更前）と `current`（変更後）のカメラ状態を返す。`get_camera` で事前取得せずに元の状態に戻せる。

### distance / size パラメータ
- `sceneView.size` を直接設定する。ユークリッド距離ではない。
- SceneView がカメラ位置を `pivot + rotation * (0, 0, -cameraDistance)` で計算。cameraDistance は size と FoV から導出される（size が小さいほどカメラが近い）。
- 値が小さいほどズームイン。バストアップ: 0.25〜0.35、上半身: 0.35〜0.50、全身: 0.8〜1.5。

## L2: 実測で判明した挙動

### yaw の実測マッピング（Shiratsume アバター）
| yaw (MCP) | internal yaw (Unity) | カメラの位置 | 映る面 |
|-----------|---------------------|-------------|--------|
| 0         | 180                 | −Z 側       | **正面** |
| 160       | 340                 | +Z 側       | 背面 |
| 340       | 160                 | +Z→−Z 寄り  | 正面やや右 |
| 345       | 165                 | 同上        | 正面やや右（実用的） |

- Shiratsume アバターは **−Z 方向を向いている**（顔が −Z 側）。
  - yaw=0 (カメラが +Z を向く = −Z 側から覗く) で正面が映る。
  - yaw=340〜350 で「正面やや右」の自然なアングル。

### pivot の注意点
- `editor_frame` はオブジェクトの **Bounds 中心** に pivot を合わせるが、`SkinnedMeshRenderer` のバウンズ中心はメッシュのローカル原点付近（足元 y≈0）になることがある。
- `editor_frame` の後に `editor_set_camera` を呼ぶと、set_camera の pivot が frame の結果を上書きする。

### RepaintAllViews による自動リフレッシュ（v0.5.72+）
- `set_camera` 後に自動で `RepaintAllViews` が実行されるため、手動で `editor_refresh` を呼ぶ必要はなくなった
- バックグラウンドの Unity でも反映される（即時 + 1フレーム遅延の2段構え）

### Shiratsume 正面バストアップの実証済みパラメータ
```
pivot: {"x": -0.12, "y": 1.30, "z": 0}
yaw: 345
pitch: 8
distance: 0.28
```
- pivot の x=-0.12 はアバターの中心がやや左にずれているため（原点が体の中心でない、または Scene 上の配置による）。
- 手順: set_camera → refresh → screenshot の3ステップ。

## L3: 推測・未検証

- Game ビューのカメラ（Main Camera）は別系統で、`editor_set_camera` の対象外。
- `editor_screenshot` の `view: "game"` で Game ビューを撮れるが、Game ビューのカメラ制御は MCP 未対応の可能性。
- Mode A の `rotation` 内のフィールドは `[yaw, pitch, roll]` の順で渡す（ソースコード上 `camera_rotation[0]` が yaw として +180 される）。

## 実運用で学んだこと

### 2026-03-26: カメラポジショニング試行錯誤
- **失敗**: Mode A の `position` をカメラのワールド座標だと思って指定 → 実際は pivot を設定するので灰色画面。
- **失敗**: pivot を `(0, 1.4, 0)` に固定して yaw/distance だけ変更 → アバターが画面端に寄る。pivot がアバターの実際の位置と合っていない。
- **失敗**: `editor_frame` → `set_camera` で pivot を上書き → frame の中央合わせが無効化。
- **失敗**: set_camera 後に refresh なしで screenshot → Unity フォーカス時にカメラがジャンプ。
- **成功**: pivot を手動で微調整 + yaw=345 + distance=0.28 + refresh で安定した正面バストアップ撮影。
- **教訓**: before 撮影時に `editor_get_camera` の結果を記録しておくと after で同じアングルを再現しやすい。
