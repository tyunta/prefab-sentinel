# MCP ツール表面規約

`prefab-sentinel-mcp` が公開する MCP ツールの **住所表現・引数命名・監査ペア要否** の規約の正本。[docs/tools.md](./tools.md) が「どのツールがあるか」のカタログであるのに対し、本書は「ツールをどう名付け・どう住所し・いつ監査するか」の規約を定める。

本書は規約（あるべき形）の正本であり、新規ツールの追加・既存ツールの変更は本書に従う。本書ではスキーム名をハイフン（`asset-path`）、実際の引数名をアンダースコア（`asset_path`）で表記する。

_規約確定: 2026-05-18_

## 1. 住所スキーム

MCP ツールが対象を指す住所は 5 種類。大きく **project 側（asset ファイルを指す）** と **scene 側（asset 内 / live editor のオブジェクトを指す）** に分かれる。引数名はこの区別を必ず表に出す（§2.1）。

| スキーム | 指すもの | 側 |
|----------|----------|----|
| asset-path | project の asset ファイル | project |
| asset-guid | asset ファイル（GUID 表現） | project |
| symbol-path | asset 内のオブジェクト（offline symbol tree 経由） | scene |
| hierarchy-path | live editor の scene / Prefab Stage オブジェクト | scene |
| patch-selector | patch op 内のコンポーネント指定 | scene |

scene 側スキーム（symbol-path / hierarchy-path / patch-selector）では、同名の兄弟要素を `#N`（0 始まりの出現順）で一意化する。曖昧な住所は解決せず error で停止する（fail-fast）。

### 1.1 asset-path

`.prefab` / `.unity` / `.asset` / `.mat` / `.cs` / `.anim` 等、project 内の asset ファイルを相対パスで指す（例: `Assets/Prefabs/Mic.prefab`）。offline ツールと editor_* ツールの双方が使う。

### 1.2 asset-guid

asset ファイルを 32 文字 hex の GUID で指す。asset-path の代替表現であり、同一の asset を指す 2 つの方法。path と GUID の両方を受けるツールは「どちらか一方を指定」とする。

### 1.3 symbol-path

offline symbol tree（YAML 直読みで構築する人間可読ツリー）上のオブジェクトを指す。GameObject パス + 末尾コンポーネントの形（例: `CharacterBody/MeshRenderer`、`Body/Head/MonoBehaviour(PlayerScript)`）。`get_unity_symbols` / `find_unity_symbol` / offline の `set_property` / `set_properties` 等が使う。権威は last-saved disk YAML — live editor の未保存編集は反映されない（Editor Bridge 接続中かつ未保存変更がある場合、`get_unity_symbols` / `find_unity_symbol` はペイロードに freshness マーカーを付与する — issue #40）。

### 1.4 hierarchy-path

live editor の scene または active Prefab Stage 上の GameObject を `/` 区切りで指す（例: `/Canvas/Panel/Button`）。editor_* ツールが使う。Prefab Stage が開いていれば stage root を先に解決する。権威は live editor。

live geometry (`editor_get_transform` / `editor_get_bounds` / `editor_measure_distance`) と target screenshot (`editor_screenshot(target=...)`) は同じ hierarchy-path authority を使う。geometry は read-only で、missing / ambiguous path は typed envelope で fail-fast する。routine geometry inspection は dedicated geometry tool を使い、`editor_run_script` snippets を第一選択にしない。

### 1.5 patch-selector

patch v2 スキーマの op 内でコンポーネントを指す文字列フォーマット `TypeName@/hierarchy/path`（例: `MeshRenderer@/Body/Head`）。引数ではなく op フィールドの値なので §2.1 の引数命名規約の対象外。

## 2. 命名規約

### 2.1 住所引数

住所を受ける引数は **`[<role>_]<kind>_path`** または **`[<role>_]asset_guid`** で名付ける。

- `<kind>` ∈ `{hierarchy, asset, symbol}`。**引数名だけで scene 側か project 側かが判別できる**こと（必須要件）。
- 操作対象そのもの（主対象）は role を付けない: `hierarchy_path` / `asset_path` / `symbol_path`。
- 操作対象以外の参照（副参照）は役割を表す `<role>_` を前置する。`<role>` はその引数が操作の中で果たす役割を表す語: `parent_hierarchy_path` / `target_hierarchy_path` / `prefab_asset_path` / `material_asset_path` / `output_asset_path`。
- GUID 表現は `[<role>_]asset_guid`（例: `material_asset_guid`）。
- `path` / `scene_path` / `script_path` のような kind を欠く名、`target_path` のような kind を表さない曖昧な名は使わない。
- patch-selector（§1.5）は引数でなく op フィールド値なので本規約の対象外。

### 2.2 property 引数

コンポーネントの SerializedProperty パスを指す引数は **`property_name`** で統一する。`field_name` / `path` 等の別語を使わない。リスト要素の dict キーも同様（例: `fields[].property_name`）。

### 2.3 sync / async

同期版と非同期版が対になるツールでは、**bare 名（接尾辞なし）を同期（ブロッキング — 呼べば結果が返る）** とする。

- 非ブロッキングの fire-and-return 版は `_async` 接尾辞を付ける。
- 完了に長時間かかり poll が要るジョブは `_submit` / `_poll` の 2 ツールに分ける。
- bare 名が片方で同期・片方で非同期、という既定値の不一致を作らない。

### 2.4 value 引数

「値」を受け取る引数は、**「未指定」と「空値」を別の表現にする**。`value: str = ""` のように既定値が「未指定」のセンチネルを兼ねる設計を禁止する（空文字列を書けなくなる）。`value: str | None = None` のように、値が与えられたか否かを内容と独立に表せる型にする。

### 2.5 単複

ツール名の単複は操作の arity に一致させる。

- 1 件を扱う → 単数（`editor_set_blend_shape`）
- 複数を返す / 扱う → 複数（`editor_get_blend_shapes`）
- 複数を 1 Undo グループで一括処理 → `batch_` 接頭辞（`editor_batch_set_blend_shape`）

## 3. 監査ペア原理

### 3.1 原理

書き込み系ツールは、その効果が次の **(a)(b)(c) のいずれかに該当するとき**、`confirm=True` + 非空 `change_reason` の監査ペアを要求する。

- **(a) arbitrary code 実行** — 呼び出し側が事前に挙動を確定できないコードを走らせる（任意 C# スニペット、任意の登録済メニューアイテム等）
- **(b) 外部公開** — プロジェクト外のサービスへ送信・公開する
- **(c) 非 Undo の asset 改変** — Unity の Undo で戻せない形で asset ファイルをディスク上で変更する（生成・上書き・削除。`.prefab` / `.asset` / `.unity` / `.anim` の保存、offline YAML 書き込み等）

いずれにも該当しない write（Undo 可能な scene 変更、live editor 状態の変更）と read は監査ペアを要求しない。**batch であること自体は監査理由にしない** — Undo 可能な操作の batch は 1 Undo グループで戻るため。

### 3.2 適用例

原理 (a)(b)(c) を各ツールに適用した分類。個別ツールの監査要否の確定一覧は [CONFIGURATION.md](../CONFIGURATION.md#confirm--change_reason-必須対象一覧) を参照。

| 区分 | 例 | 根拠 |
|------|----|------|
| 監査側 | `editor_run_script` / `editor_run_script_submit` / `editor_execute_menu_item` | (a) |
| 監査側 | `vrcsdk_upload` | (b) |
| 監査側 | `delete_asset` / `delete_assets` / `editor_create_animation_clip` / `editor_safe_save_prefab` / `editor_create_udon_program_asset` / `editor_create_scene` / `editor_save_scene` / `editor_close_prefab`(save=True) | (c) |
| 監査側 | offline write（`patch_apply` / `set_property` / `add_component` 等） | (c) disk YAML 書き込み |
| 非監査側 | `editor_set_property` / `editor_set_blend_shape` / `editor_batch_set_property` / `editor_batch_set_blend_shape` / `editor_apply_animation_clip` 等 | Undo 可能な scene / live 変更 |
| 条件付き監査 | `validate_runtime(profile="clientsim")` | ClientSim は Play Mode / dirty scene state に触れうるため明示 profile + audit pair を要求する。`compile_only` / `editor_console_only` は read-only profile。 |

`delete_asset` / `delete_assets` は dry-run が既定で、確定適用時だけ `confirm=True` + 非空 `change_reason` を要求する。適用経路は Unity `AssetDatabase` を持つ Editor Bridge action に限定し、Bridge / AssetDatabase が使えない場合は typed error を返して filesystem delete へ迂回しない。削除後に broken reference が増えた場合も、tool は可否判断をせず `broken_reference_delta` として報告する。
