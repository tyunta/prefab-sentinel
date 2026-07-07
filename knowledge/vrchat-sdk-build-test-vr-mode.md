---
tool: vrchat-sdk-build-test-vr-mode
version_tested: "VRChat SDK 3.7+"
last_updated: 2026-05-24
confidence: high
---

# VRChat SDK Build & Test: VR / Desktop Mode 起動

VRChat SDK Control Panel の `Build & Test` 機能は VRChat client を新しい instance として起動するが、起動モード (VR / Desktop) の制御は **複数要因の組合せ** で決まる。デフォルトは「VR runtime が起動状態なら VR、無ければ Desktop」。

## 基本情報

- 対象 UI: `VRChat SDK / Show Control Panel` → **Builder タブ** → Local Testing セクション
- 関連 launch option: `--no-vr` (Desktop mode 強制)
- 関連 runtime: SteamVR (OpenVR) / Oculus Link / その他 OpenXR runtime
- 公式 docs: <https://creators.vrchat.com/worlds/udon/using-build-test/>, <https://docs.vrchat.com/docs/launch-options>

## 主要 API・概念

### Builder タブ Local Testing セクション

| Setting | 効果 | Default |
|---------|------|---------|
| `Force Non-VR` (checkbox) | ON で `--no-vr` flag を起動引数に付与し VR runtime を無視 | OFF |
| `Number of Clients` (int) | 2 以上で複数 VRChat instance を同時起動 | 1 |
| `Build & Test` (button) | 上記設定を反映して VRChat client を起動 | - |

**重要**: Local Testing 系設定は **Builder タブ** 内にある (Settings タブには無い)。Settings タブは Authentication / VRChat Client path / 環境変数のみを扱う。

### VR mode 起動条件

VRChat は起動時に以下の順で VR runtime を検出する:

1. `--no-vr` flag が引数にある → 強制 Desktop
2. (flag 無し)
   - OpenVR (SteamVR) が runnning → VR mode で起動
   - Oculus runtime が ready → VR mode で起動
   - どちらも無効 → Desktop mode で fallback

### VR mode 強制 option は **存在しない**

launch options 公式 docs に `--vrmode openvr` / `--force-vr` / `--vr` 等は **未記載**。VR 起動は default 挙動 (runtime detection)。VR で起動したい場合は:

1. `Force Non-VR` を OFF
2. SteamVR (or Oculus) を **事前に Ready 状態** にする (HMD 装着 + Steam Library で Status=Ready 等)
3. その状態で `Build & Test` 押下

## 使い分け

### Force Non-VR を ON にするケース

- 初回ビルド / Build & Test の動作確認 (公式 docs 推奨: "For the first test, you should turn on 'Force Non-VR'")
- HMD 装着が面倒な Desktop only テスト
- AI による automated testing で HMD を使えない場合

### Force Non-VR を OFF にするケース

- VR 固有挙動 (controller input, IK pose, HMD tracking, per-eye rendering, etc.) のテスト
- IPD / Stereo SBS 系コンテンツのテスト
- 本番想定の動作確認

### Number of Clients との関係

`Number of Clients = N` (N ≥ 2) で N 個の VRChat instance を同時起動。Network 同期コンテンツのテストに有用だが、各 instance の VR/Desktop モードは同一設定が適用される (個別指定不可)。

Multi-instance テストで「片方 VR、片方 Desktop」を再現するには:

1. 先に手動で 1 instance を VR mode (SteamVR ready + 通常起動) で起動
2. SteamVR を後で停止 (HMD を外す等で 2 つ目の起動時に検出されないように)
3. SDK Build & Test を `Force Non-VR=ON` で押下 → 2 instance 目が Desktop で起動

ただしこの flow は VRChat SDK が公式サポートする workflow ではない。

## 落とし穴

### Builder タブが折り畳まれていて Local Testing 設定が見えない

VRChat SDK Control Panel の Builder タブは長い縦スクロール構造を持ち、Validations / Build / Local Testing の各セクションがある。Validations セクションが警告で満たされていると展開されすぎて Local Testing が画面外になることがある。

回避: Validations の警告を解消 → Builder タブ全体を view し直す → Local Testing セクションを探す。

### Setting タブを誤って探す

公式 docs と一部 community guide で「Settings tab で〜」と書かれていることがあるが、これは旧 SDK / Avatar SDK 系の話。**Worlds SDK の現行版 (3.7+) では Builder タブ内**。Avatar SDK は別構造を持つ可能性がある。

検出 grep:

```bash
grep -rn "Force Non-VR\|Number of Clients" /path/to/VRChatSDK
```

公式 docs 文言を確認すると Builder タブ記載が支配的。

### Wrapper UI 経由の挙動

VRChat SDK の Build & Test を wrap する外部ツールは内部で同 API を呼ぶため、SDK 側の Builder タブ設定がそのまま反映される。Wrapper UI に独自の VR/Desktop toggle が無い場合、VRChat SDK の Builder タブ設定が effective。

### SteamVR が "Ready" 表示でも detection されないケース

- Steam ライブラリで SteamVR が起動済みでも、HMD が **Activity Mode** に入っていないと detect されない場合あり
- HMD を装着して "VR ready" 状態 (画面が SteamVR home などを表示) になってから Build & Test 押下が確実
- Oculus Link 経由の場合、Oculus Desktop app の status が "Connected" になってから

### `--no-vr` 以外の launch option による副作用

`--profile=N` (N ≥ 1) で profile 分離はできるが VR/Desktop には影響しない。`--watch-worlds` は local testing 用 flag で常時 on (SDK が自動付与)。

## 検出パターン

実際の起動引数を確認したい場合、VRChat の output log に記載される:

```
%APPDATA%\..\LocalLow\VRChat\VRChat\output_log.txt (もしくは output_log_<timestamp>.txt)
```

または起動済 process の command line を取得:

```powershell
# PowerShell (Windows)
Get-WmiObject Win32_Process -Filter "Name='VRChat.exe'" | Select-Object CommandLine
```

`--no-vr` が含まれているか目視確認すれば Force Non-VR 設定の effective を判定可能。

VR mode で起動成功した場合、output_log.txt に `OpenVR initialized` / `Oculus initialized` / `XR Subsystem` 等の log line が出る。これを grep すれば VR 起動の客観的確認になる。

## 関連 knowledge

- [vrchat-sdk-worlds.md](./vrchat-sdk-worlds.md) — VRChat World SDK 全般
- [vrchat-sdk-base.md](./vrchat-sdk-base.md) — SDK base components
