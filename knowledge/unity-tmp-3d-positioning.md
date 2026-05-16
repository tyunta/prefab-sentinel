---
tool: unity-tmp-3d-positioning
version_tested: "Unity 2022.3.22f1 / TextMeshPro 3.0.x"
last_updated: 2026-05-10
confidence: high
---

# TextMeshPro 3D World-Space の sizing と positioning 罠

VRChat World で world-space に置く **3D TMP**（頭上ラベル・パネル内テキスト等）を扱うとき、fontSize の世界スケールと RectTransform の position 解釈で時間を溶かしやすい。踏みやすい罠と対応をカタログ化する。

## 1. fontSize 1 ≒ 10cm (default font の場合)

### 観測

`TextMeshPro` (3D, `MeshRenderer` ベース、Canvas ではない) を使うと:

- `fontSize = 36` (default 推奨値) → ~3.6m の文字高 (very 大きい)
- `fontSize = 0.5` → ~5cm の文字高 (人間が読みやすいサイズ、頭上アバター表示にちょうど)
- `fontSize = 0.06` → ~0.6cm (ほぼ見えない)

経験則: **fontSize 1 ≒ 10cm**、fontSize 10 ≒ 1m。

### 注意

これは **font asset の sampling point size と ascender/descender 比に依存する近似値**。
公式 Unity docs に「fontSize × N = M meters」のような universal な式はない。
ただし default の `LiberationSans SDF.asset` (TMP 標準) を使う限り **十分実用的な近似値**。

別 font に切り替えたら必ず実測確認:

1. テスト文字を出して `transform.position` で位置を確認
2. font asset の inspector で `Sampling Point Size` を確認
3. 比例計算で換算

### 実装値の例

| ラベル | fontSize | 想定文字高 |
|---|---|---|
| ワールド固定の小ラベル (短いテキスト) | 0.50 | ~5cm |
| パネル内の複数行テキスト | 0.30 | ~3cm |
| アバター頭上に追従するラベル | 0.50 | ~5cm |

VRChat の HMD 視野で 1〜2m 距離から読める範囲。

## 2. RectTransform の `m_LocalPosition` は 嘘 — `m_AnchoredPosition` を見る

### 観測

`TextMeshPro` (UI でない 3D 版でも) は内部に `RectTransform` を持つ。`prefab YAML` を `cat` 等で見ると:

```yaml
RectTransform:
  m_LocalPosition: {x: 0, y: 0, z: 0}   # ← 常に 0
  m_AnchoredPosition: {x: 0, y: 0.36}   # ← 実効位置はここ
  m_SizeDelta: {x: 1.2, y: 0.1}
  m_Pivot: {x: 0.5, y: 0.5}
```

YAML を素読みしてデバッグするとき、`m_LocalPosition` が `(0,0,0)` だからといって
「位置が反映されていない」と判断してはいけない。**`RectTransform` は anchor + AnchoredPosition で position を表現する**。

実効位置 = `parent_position + (anchor 計算) + AnchoredPosition`。
anchor が default (`(0.5, 0.5)`) で parent 中心に置くなら、`AnchoredPosition` がほぼ local 座標と一致する。

### Builder script で代入する場合

```csharp
GameObject label = new GameObject("Label");
label.transform.SetParent(root.transform, false);
label.transform.localPosition = new Vector3(0f, 0.36f, 0f);  // これでも RectTransform が AnchoredPosition に変換してくれる

var labelTmp = label.AddComponent<TextMeshPro>();
// ... fontSize, alignment etc.

// RectTransform の sizeDelta を整える (text bounding box)
var labelRT = label.GetComponent<RectTransform>();
if (labelRT != null) {
    labelRT.pivot = new Vector2(0.5f, 0.5f);
    labelRT.sizeDelta = new Vector2(1.2f, 0.10f);
}
```

`Transform.localPosition = ...` で書いた値は **RectTransform 経由で `m_AnchoredPosition` に格納される**ので、
保存後の YAML を見ると `m_LocalPosition: 0` / `m_AnchoredPosition: <設定値>` という見た目になる。これが正常。

### 検査コマンド

prefab-sentinel で確認:
```
inspect_hierarchy(asset_path="Assets/.../MyPrefab.prefab", expand_monobehaviour=true)
```
これで RectTransform の serialized fields が dump され、`m_AnchoredPosition` が実際の意図した値に
なっているか確認できる。`m_LocalPosition` は無視してよい。

## 3. 縦に並んだ 3D TMP を「中央揃え」したいときは alignment = `Center`

### 観測

複数行のテキストをパネル下部に詰めるため、最初は:
- `pivot = (0.5, 0)` (下端 origin)
- `alignment = TextAlignmentOptions.BottomLeft`
- `localPosition.x = 0`

としたら、文字列が **テキストボックス左寄せ**になり、テキストの左端が `localPosition.x = 0`、
**意図した中心軸から大きく左にずれた** (見た目「左上にテキストがある」状態)。

### 修正

```csharp
factorTmp.alignment = TextAlignmentOptions.Center;
```

これで bounding box 内で左右中央揃えになり、`localPosition.x = 0` でちゃんと親の中心軸に揃う。
`pivot` は `(0.5, 0)` のまま (下端 origin で y 方向の伸び方を制御する) でよい。

### 一般則

- **3D TMP は bounding box 中で alignment が効く。pivot は box 自体の anchoring**
- 親の中心軸に揃えたい → `alignment = Center` (左右) + `pivot.x = 0.5` (左右)
- 上下方向の origin (アンダー伸ばし or オーバー伸ばし) → `pivot.y` で制御

## 4. lineSpacing は **負の値** で詰める

```csharp
factorTmp.lineSpacing = -10f;  // 詰める
```

正の値で広げるが、3D 上で 4 行縦に並べる時は default (0) でも空きすぎることがある。
`-5 ～ -15` あたりで詰めるとちょうどよい (font サイズに依存)。

## 5. enableWordWrapping = false で固定幅レイアウト

```csharp
factorTmp.enableWordWrapping = false;
```

`sizeDelta` で sleeve box を広めに取っても、ASCII bar (`█░`) の長文が改行で崩れることがある。
3D 表示で固定幅プログレスバー風レイアウトをするなら off にしておく。

## 6. Builder で生成した直後に値が反映されない問題 → recompile 待ち

頻発するケース:
- Editor/Builder.cs を Edit して fontSize や localPosition の値を変える
- 直後に `editor_execute_menu_item Tools/.../Build Prefabs` を打つ
- **Unity の自動 recompile が完了する前に menu execute が走り、古い builder が動く**
- 生成 prefab の値が新値にならない

→ **対応**: Edit と menu execute の間に必ず `editor_recompile_and_wait` を入れる。

## チェックリスト (TMP 3D で position / size がおかしい時)

- [ ] fontSize は 0.3〜0.6 程度に設定したか (default の 36 は world で巨大)
- [ ] alignment は中心揃えか (`TextAlignmentOptions.Center`)
- [ ] localPosition と sizeDelta は何の値になっているか YAML で確認
- [ ] `m_LocalPosition` ではなく `m_AnchoredPosition` を見たか
- [ ] Edit → Build の間に recompile_and_wait を挟んだか
- [ ] enableWordWrapping を切ったか (固定幅レイアウト時)

## Sources

- Unity Discussions (font size in WorldSpace): https://discussions.unity.com/t/font-size-in-worldspace/874227
- Unity TextMeshPro Manual (Font Size, Rich Text Size): https://docs.unity3d.com/Packages/com.unity.textmeshpro@4.0/manual/RichTextSize.html
