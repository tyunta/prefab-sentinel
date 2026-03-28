# Forgotten Temple — VRChat 2人協力ゲームワールド 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** VRChat上で2人が協力して古代神殿を探索するゲームワールドを構築する（ロビー＋4部屋＋祭壇の6エリア構成）

**Architecture:** UdonSharpBehaviour ベースのギミックスクリプト群を Manual Sync で同期。GameManager が全体進行を管理し、各部屋のギミック（DoorController, PressurePlate, TorchLight, MirrorRotator, LightBeam, AltarFinish）が独立した責務を持つ。シーン内のオブジェクト配線は Prefab Sentinel MCP ツールで行う。

**Tech Stack:** Unity 2022.3 LTS / VRC SDK 3.7+ / UdonSharp 1.x / ClientSim

**Project Path:** `D:\VRChatProject\PS-WORLD-TEST`

**Design Spec:** `docs/superpowers/specs/2026-03-27-forgotten-temple-game-world-design.md`

---

## ファイル構成

### 新規作成

| パス | 責務 |
|------|------|
| `Assets/ForgottenTemple/Scenes/ForgottenTemple.unity` | メインシーン |
| `Assets/ForgottenTemple/Scripts/GameManager.cs` | 全体進行管理（部屋番号、クリア状態、タイマー） |
| `Assets/ForgottenTemple/Scripts/GameManager.asset` | UdonSharpProgramAsset |
| `Assets/ForgottenTemple/Scripts/DoorController.cs` | 扉の開閉アニメーション制御 |
| `Assets/ForgottenTemple/Scripts/DoorController.asset` | UdonSharpProgramAsset |
| `Assets/ForgottenTemple/Scripts/PressurePlate.cs` | 圧力板のプレイヤー検知 |
| `Assets/ForgottenTemple/Scripts/PressurePlate.asset` | UdonSharpProgramAsset |
| `Assets/ForgottenTemple/Scripts/TorchLight.cs` | たいまつの Pickup + ライト制御 |
| `Assets/ForgottenTemple/Scripts/TorchLight.asset` | UdonSharpProgramAsset |
| `Assets/ForgottenTemple/Scripts/MirrorRotator.cs` | 鏡の90度回転 + 同期 |
| `Assets/ForgottenTemple/Scripts/MirrorRotator.asset` | UdonSharpProgramAsset |
| `Assets/ForgottenTemple/Scripts/LightBeam.cs` | LineRenderer 光線 + レイキャスト反射 |
| `Assets/ForgottenTemple/Scripts/LightBeam.asset` | UdonSharpProgramAsset |
| `Assets/ForgottenTemple/Scripts/AltarFinish.cs` | 祭壇クリア演出 |
| `Assets/ForgottenTemple/Scripts/AltarFinish.asset` | UdonSharpProgramAsset |
| `Assets/ForgottenTemple/Scripts/FallRespawn.cs` | 落下リスポーン |
| `Assets/ForgottenTemple/Scripts/FallRespawn.asset` | UdonSharpProgramAsset |
| `Assets/ForgottenTemple/Materials/` | マテリアル（石壁、床、光源等） |
| `Assets/ForgottenTemple/Audio/` | オーディオクリップ（BGM、SE） |

### 変更なし
- 既存の `Assets/Scripts/HelloWorldButton.cs` 等はそのまま残す

---

## 前提知識

### UdonSharp の制約（全タスク共通）
- `List<T>`, `Dictionary`, LINQ, try/catch, async/await, デリゲート、ラムダ **使用不可**
- 配列 `[]` のみ使用可能
- UdonSharpBehaviour 間の継承不可
- `[UdonSynced]` で同期、`RequestSerialization()` で送信、`OnDeserialization()` で受信
- `SendCustomNetworkEvent(NetworkEventTarget.All, "MethodName")` で全員にイベント送信
- `Networking.SetOwner(localPlayer, gameObject)` でオーナー取得が同期書き込みの前提

### Prefab Sentinel MCP ツールの利用
- スクリプト作成後は `editor_recompile` → `editor_create_udon_program_asset` でアセット作成
- コンポーネント追加は `editor_add_component`、プロパティ設定は `editor_set_property`
- 配線検証は `inspect_wiring` + `validate_refs`

### テスト方法
- UdonSharp は Unity 外でのユニットテスト不可
- ClientSim でプレイモードに入り、手動で動作確認
- `editor_console` でランタイムログを確認
- `editor_run_tests` でプロジェクトテスト実行

---

## Task 1: プロジェクト構造 + シーン作成

**Files:**
- Create: `Assets/ForgottenTemple/Scripts/` (directory)
- Create: `Assets/ForgottenTemple/Scenes/` (directory)
- Create: `Assets/ForgottenTemple/Materials/` (directory)
- Create: `Assets/ForgottenTemple/Audio/` (directory)
- Create: `Assets/ForgottenTemple/Scenes/ForgottenTemple.unity` (via Unity Editor)

- [ ] **Step 1: フォルダ構造を作成**

```bash
mkdir -p "D:/VRChatProject/PS-WORLD-TEST/Assets/ForgottenTemple/Scripts"
mkdir -p "D:/VRChatProject/PS-WORLD-TEST/Assets/ForgottenTemple/Scenes"
mkdir -p "D:/VRChatProject/PS-WORLD-TEST/Assets/ForgottenTemple/Materials"
mkdir -p "D:/VRChatProject/PS-WORLD-TEST/Assets/ForgottenTemple/Audio"
```

- [ ] **Step 2: Unity にフォルダを認識させる**

MCP: `editor_refresh`

- [ ] **Step 3: 新しいシーンを作成**

MCP: `editor_execute_menu_item` で `File/New Scene` を実行し、ForgottenTemple シーンとして保存。
既存の HW シーンは変更しない。

```
editor_execute_menu_item: "File/New Scene"
```

シーンが作成できない場合は、HW.unity をコピーして不要なオブジェクトを削除する代替アプローチを取る。

- [ ] **Step 4: VRCWorld を配置**

MCP: `editor_list_roots` で現在のルートオブジェクトを確認。
VRCWorld がなければ VRC SDK メニューから作成:

```
editor_execute_menu_item: "VRChat SDK/Utilities/Create VRCWorld"
```

- [ ] **Step 5: 基本オブジェクトを確認**

MCP: `editor_list_roots` で以下が存在することを確認:
- VRCWorld（VRCSceneDescriptor 付き）
- Main Camera
- Directional Light

- [ ] **Step 6: コミット**

```bash
cd "D:/VRChatProject/PS-WORLD-TEST"
git add Assets/ForgottenTemple/
git commit -m "feat: create ForgottenTemple project structure and scene"
```

---

## Task 2: GameManager スクリプト

**Files:**
- Create: `Assets/ForgottenTemple/Scripts/GameManager.cs`
- Create: `Assets/ForgottenTemple/Scripts/GameManager.asset` (via MCP)

**概要:** 全体進行を管理。現在の部屋番号、各部屋のクリア状態、経過時間を同期する。

- [ ] **Step 1: GameManager.cs を作成**

```csharp
using UdonSharp;
using UnityEngine;
using UnityEngine.UI;
using VRC.SDKBase;

[UdonBehaviourSyncMode(BehaviourSyncMode.Manual)]
public class GameManager : UdonSharpBehaviour
{
    [Header("Room Doors")]
    [SerializeField] private DoorController lobbyDoor;
    [SerializeField] private DoorController room1Door;
    [SerializeField] private DoorController room2Door;
    [SerializeField] private DoorController room3Door;
    [SerializeField] private DoorController room4Door;

    [Header("UI")]
    [SerializeField] private Text timerText;

    [UdonSynced] private int currentRoom;
    [UdonSynced] private bool gameStarted;
    [UdonSynced] private float startTime;

    private bool localGameStarted;

    public void Start()
    {
        currentRoom = 0;
        gameStarted = false;
    }

    public void Update()
    {
        if (!gameStarted) return;

        if (timerText != null)
        {
            float elapsed = Time.time - startTime;
            int minutes = (int)(elapsed / 60f);
            int seconds = (int)(elapsed % 60f);
            timerText.text = $"{minutes:00}:{seconds:00}";
        }
    }

    public void _StartGame()
    {
        if (gameStarted) return;

        Networking.SetOwner(Networking.LocalPlayer, gameObject);
        gameStarted = true;
        startTime = Time.time;
        currentRoom = 1;
        RequestSerialization();

        _ApplyState();
    }

    public void _OnRoomCleared(int roomNumber)
    {
        // ロビー(room 0)クリア時はゲーム開始
        if (!gameStarted && roomNumber == 0)
        {
            _StartGame();
            return;
        }

        if (currentRoom != roomNumber) return;

        Networking.SetOwner(Networking.LocalPlayer, gameObject);
        currentRoom = roomNumber + 1;
        RequestSerialization();

        _ApplyState();
    }

    public void _ApplyState()
    {
        Debug.Log($"[GameManager] Room={currentRoom}, Started={gameStarted}");
    }

    public override void OnDeserialization()
    {
        _ApplyState();

        if (gameStarted && !localGameStarted)
        {
            localGameStarted = true;
            startTime = Time.time;
        }
    }

    public int _GetCurrentRoom()
    {
        return currentRoom;
    }

    public bool _IsGameStarted()
    {
        return gameStarted;
    }

    public float _GetElapsedTime()
    {
        if (!gameStarted) return 0f;
        return Time.time - startTime;
    }
}
```

Write to: `D:/VRChatProject/PS-WORLD-TEST/Assets/ForgottenTemple/Scripts/GameManager.cs`

- [ ] **Step 2: Unity にコンパイルさせる**

MCP: `editor_recompile`

Expected: コンパイル成功（DoorController がまだ無いのでエラーの可能性あり）。
DoorController への参照でエラーが出る場合、一時的に型を `UdonSharpBehaviour` に変更して先に進む。

- [ ] **Step 3: UdonSharpProgramAsset を作成**

MCP: `editor_create_udon_program_asset` with:
- `script_path`: `Assets/ForgottenTemple/Scripts/GameManager.cs`
- `output_path`: `Assets/ForgottenTemple/Scripts/GameManager.asset`

- [ ] **Step 4: コミット**

```bash
cd "D:/VRChatProject/PS-WORLD-TEST"
git add Assets/ForgottenTemple/Scripts/GameManager.cs Assets/ForgottenTemple/Scripts/GameManager.asset
git commit -m "feat: add GameManager script for game progression"
```

---

## Task 3: DoorController スクリプト

**Files:**
- Create: `Assets/ForgottenTemple/Scripts/DoorController.cs`
- Create: `Assets/ForgottenTemple/Scripts/DoorController.asset` (via MCP)

**概要:** 扉の開閉を制御。条件を満たしたらアニメーション（Transform 移動）を再生する。

- [ ] **Step 1: DoorController.cs を作成**

```csharp
using UdonSharp;
using UnityEngine;
using VRC.SDKBase;

[UdonBehaviourSyncMode(BehaviourSyncMode.Manual)]
public class DoorController : UdonSharpBehaviour
{
    [Header("Door Settings")]
    [SerializeField] private Transform doorTransform;
    [SerializeField] private Vector3 openOffset = new Vector3(0f, 3f, 0f);
    [SerializeField] private float openSpeed = 2f;

    [Header("Audio")]
    [SerializeField] private AudioSource doorAudio;

    [UdonSynced] private bool isOpen;
    private Vector3 closedPosition;
    private Vector3 openPosition;
    private bool isMoving;

    public void Start()
    {
        if (doorTransform == null)
            doorTransform = transform;

        closedPosition = doorTransform.localPosition;
        openPosition = closedPosition + openOffset;
    }

    public void Update()
    {
        if (!isMoving) return;

        Vector3 target = isOpen ? openPosition : closedPosition;
        doorTransform.localPosition = Vector3.MoveTowards(
            doorTransform.localPosition, target, openSpeed * Time.deltaTime);

        if (Vector3.Distance(doorTransform.localPosition, target) < 0.01f)
        {
            doorTransform.localPosition = target;
            isMoving = false;
        }
    }

    public void _Open()
    {
        if (isOpen) return;

        Networking.SetOwner(Networking.LocalPlayer, gameObject);
        isOpen = true;
        isMoving = true;
        RequestSerialization();

        if (doorAudio != null)
            doorAudio.Play();

        Debug.Log($"[DoorController] {gameObject.name} opened");
    }

    public void _Close()
    {
        if (!isOpen) return;

        Networking.SetOwner(Networking.LocalPlayer, gameObject);
        isOpen = false;
        isMoving = true;
        RequestSerialization();
    }

    public override void OnDeserialization()
    {
        isMoving = true;
        if (doorAudio != null && isOpen)
            doorAudio.Play();
    }

    public bool _IsOpen()
    {
        return isOpen;
    }
}
```

Write to: `D:/VRChatProject/PS-WORLD-TEST/Assets/ForgottenTemple/Scripts/DoorController.cs`

- [ ] **Step 2: コンパイル + アセット作成**

MCP: `editor_recompile`
MCP: `editor_create_udon_program_asset` with:
- `script_path`: `Assets/ForgottenTemple/Scripts/DoorController.cs`
- `output_path`: `Assets/ForgottenTemple/Scripts/DoorController.asset`

- [ ] **Step 3: GameManager の DoorController 参照型を確認**

GameManager.cs が `DoorController` 型を参照しているので、再コンパイルしてエラーが無いことを確認:

MCP: `editor_recompile`
MCP: `editor_console` でエラー確認

Expected: コンパイルエラーなし

- [ ] **Step 4: コミット**

```bash
cd "D:/VRChatProject/PS-WORLD-TEST"
git add Assets/ForgottenTemple/Scripts/DoorController.*
git commit -m "feat: add DoorController script for door open/close animation"
```

---

## Task 4: PressurePlate スクリプト

**Files:**
- Create: `Assets/ForgottenTemple/Scripts/PressurePlate.cs`
- Create: `Assets/ForgottenTemple/Scripts/PressurePlate.asset` (via MCP)

**概要:** プレイヤーが乗ると起動する圧力板。2枚ペアで同時検知して扉を開ける。

- [ ] **Step 1: PressurePlate.cs を作成**

```csharp
using UdonSharp;
using UnityEngine;
using VRC.SDKBase;

[UdonBehaviourSyncMode(BehaviourSyncMode.NoVariableSync)]
public class PressurePlate : UdonSharpBehaviour
{
    [Header("Pair Settings")]
    [SerializeField] private PressurePlate pairedPlate;
    [SerializeField] private DoorController targetDoor;
    [SerializeField] private GameManager gameManager;
    [SerializeField] private int roomNumber;

    [Header("Visual")]
    [SerializeField] private Renderer plateRenderer;
    [SerializeField] private Color activeColor = new Color(0.2f, 1f, 0.2f);
    [SerializeField] private Color inactiveColor = new Color(0.5f, 0.5f, 0.5f);

    [Header("Audio")]
    [SerializeField] private AudioSource plateAudio;

    [Header("Timing")]
    [SerializeField] private float graceTime = 3f;

    private bool isPlayerOn;
    private float activatedTime;
    private MaterialPropertyBlock propBlock;

    public void Start()
    {
        propBlock = new MaterialPropertyBlock();
        _SetColor(inactiveColor);
    }

    public override void OnPlayerTriggerEnter(VRCPlayerApi player)
    {
        if (player != Networking.LocalPlayer) return;

        isPlayerOn = true;
        activatedTime = Time.time;
        _SetColor(activeColor);

        if (plateAudio != null)
            plateAudio.Play();

        SendCustomNetworkEvent(VRC.Udon.Common.Interfaces.NetworkEventTarget.All, nameof(_CheckBothPlates));
    }

    public override void OnPlayerTriggerExit(VRCPlayerApi player)
    {
        if (player != Networking.LocalPlayer) return;

        isPlayerOn = false;
        _SetColor(inactiveColor);
    }

    public void _CheckBothPlates()
    {
        if (!isPlayerOn) return;

        // pairedPlate が null ならソロモード（1人で踏めばクリア）
        // pairedPlate があれば両方アクティブでクリア
        if (pairedPlate != null && !pairedPlate._IsPlayerOn())
            return;

        if (targetDoor != null)
            targetDoor._Open();

        if (gameManager != null)
            gameManager._OnRoomCleared(roomNumber);

        Debug.Log($"[PressurePlate] Room {roomNumber} cleared.");
    }

    public bool _IsPlayerOn()
    {
        if (!isPlayerOn) return false;

        if (Time.time - activatedTime > graceTime)
        {
            isPlayerOn = false;
            _SetColor(inactiveColor);
            return false;
        }

        return true;
    }

    private void _SetColor(Color color)
    {
        if (plateRenderer == null) return;
        propBlock.SetColor("_Color", color);
        plateRenderer.SetPropertyBlock(propBlock);
    }
}
```

Write to: `D:/VRChatProject/PS-WORLD-TEST/Assets/ForgottenTemple/Scripts/PressurePlate.cs`

- [ ] **Step 2: コンパイル + アセット作成**

MCP: `editor_recompile`
MCP: `editor_create_udon_program_asset` with:
- `script_path`: `Assets/ForgottenTemple/Scripts/PressurePlate.cs`
- `output_path`: `Assets/ForgottenTemple/Scripts/PressurePlate.asset`

- [ ] **Step 3: コミット**

```bash
cd "D:/VRChatProject/PS-WORLD-TEST"
git add Assets/ForgottenTemple/Scripts/PressurePlate.*
git commit -m "feat: add PressurePlate script with paired detection and grace time"
```

---

## Task 5: TorchLight スクリプト

**Files:**
- Create: `Assets/ForgottenTemple/Scripts/TorchLight.cs`
- Create: `Assets/ForgottenTemple/Scripts/TorchLight.asset` (via MCP)

**概要:** たいまつの Pickup。持つとライトが点灯、離すと消灯。一定時間放置でリスポーン。

- [ ] **Step 1: TorchLight.cs を作成**

```csharp
using UdonSharp;
using UnityEngine;
using VRC.SDKBase;

[UdonBehaviourSyncMode(BehaviourSyncMode.Manual)]
public class TorchLight : UdonSharpBehaviour
{
    [Header("Light")]
    [SerializeField] private Light torchPointLight;
    [SerializeField] private ParticleSystem flameParticle;

    [Header("Respawn")]
    [SerializeField] private float respawnTime = 30f;

    [Header("Audio")]
    [SerializeField] private AudioSource flameAudio;

    [UdonSynced] private bool isHeld;

    private Vector3 spawnPosition;
    private Quaternion spawnRotation;
    private float dropTime;
    private bool wasDropped;

    public void Start()
    {
        spawnPosition = transform.localPosition;
        spawnRotation = transform.localRotation;

        _SetLightActive(false);
    }

    public override void OnPickup()
    {
        Networking.SetOwner(Networking.LocalPlayer, gameObject);
        isHeld = true;
        wasDropped = false;
        RequestSerialization();

        _SetLightActive(true);
        Debug.Log("[TorchLight] Picked up");
    }

    public override void OnDrop()
    {
        Networking.SetOwner(Networking.LocalPlayer, gameObject);
        isHeld = false;
        wasDropped = true;
        dropTime = Time.time;
        RequestSerialization();

        _SetLightActive(false);
        Debug.Log("[TorchLight] Dropped");
    }

    public void Update()
    {
        if (!wasDropped) return;
        if (isHeld) return;

        if (Time.time - dropTime > respawnTime)
        {
            _Respawn();
        }
    }

    public void _Respawn()
    {
        wasDropped = false;
        transform.localPosition = spawnPosition;
        transform.localRotation = spawnRotation;

        var rb = GetComponent<Rigidbody>();
        if (rb != null)
        {
            rb.velocity = Vector3.zero;
            rb.angularVelocity = Vector3.zero;
        }

        Debug.Log("[TorchLight] Respawned to original position");
    }

    public override void OnDeserialization()
    {
        _SetLightActive(isHeld);
    }

    private void _SetLightActive(bool active)
    {
        if (torchPointLight != null)
            torchPointLight.enabled = active;

        if (flameParticle != null)
        {
            if (active)
                flameParticle.Play();
            else
                flameParticle.Stop();
        }

        if (flameAudio != null)
        {
            if (active)
                flameAudio.Play();
            else
                flameAudio.Stop();
        }
    }
}
```

Write to: `D:/VRChatProject/PS-WORLD-TEST/Assets/ForgottenTemple/Scripts/TorchLight.cs`

**必須コンポーネント（GameObject 構成）:**
- `VRCPickup` (InteractionText = "たいまつ")
- `VRCObjectSync`
- `Rigidbody` (isKinematic = false)
- `Collider` (Box or Capsule)
- `TorchLight` (本スクリプト)
- 子オブジェクト: Point Light + Particle System

- [ ] **Step 2: コンパイル + アセット作成**

MCP: `editor_recompile`
MCP: `editor_create_udon_program_asset` with:
- `script_path`: `Assets/ForgottenTemple/Scripts/TorchLight.cs`
- `output_path`: `Assets/ForgottenTemple/Scripts/TorchLight.asset`

- [ ] **Step 3: コミット**

```bash
cd "D:/VRChatProject/PS-WORLD-TEST"
git add Assets/ForgottenTemple/Scripts/TorchLight.*
git commit -m "feat: add TorchLight script with pickup, light toggle, and auto-respawn"
```

---

## Task 6: MirrorRotator スクリプト

**Files:**
- Create: `Assets/ForgottenTemple/Scripts/MirrorRotator.cs`
- Create: `Assets/ForgottenTemple/Scripts/MirrorRotator.asset` (via MCP)

**概要:** Interact で鏡を90度ずつ回転。現在の角度を同期。

- [ ] **Step 1: MirrorRotator.cs を作成**

```csharp
using UdonSharp;
using UnityEngine;
using VRC.SDKBase;

[UdonBehaviourSyncMode(BehaviourSyncMode.Manual)]
public class MirrorRotator : UdonSharpBehaviour
{
    [Header("Rotation")]
    [SerializeField] private Vector3 rotationAxis = Vector3.up;
    [SerializeField] private float rotationStep = 90f;

    [Header("Audio")]
    [SerializeField] private AudioSource rotateAudio;

    [Header("Beam Reference")]
    [SerializeField] private LightBeam lightBeam;

    [UdonSynced] private int rotationIndex;

    private Quaternion baseRotation;

    public void Start()
    {
        baseRotation = transform.localRotation;
        _ApplyRotation();
    }

    public override void Interact()
    {
        Networking.SetOwner(Networking.LocalPlayer, gameObject);
        rotationIndex = (rotationIndex + 1) % 4;
        RequestSerialization();

        _ApplyRotation();

        if (rotateAudio != null)
            rotateAudio.Play();

        Debug.Log($"[MirrorRotator] {gameObject.name} rotated to index {rotationIndex}");
    }

    public override void OnDeserialization()
    {
        _ApplyRotation();
    }

    private void _ApplyRotation()
    {
        float angle = rotationIndex * rotationStep;
        transform.localRotation = baseRotation * Quaternion.AngleAxis(angle, rotationAxis);

        if (lightBeam != null)
            lightBeam._UpdateBeam();
    }

    public int _GetRotationIndex()
    {
        return rotationIndex;
    }

    public Vector3 _GetForward()
    {
        return transform.forward;
    }
}
```

Write to: `D:/VRChatProject/PS-WORLD-TEST/Assets/ForgottenTemple/Scripts/MirrorRotator.cs`

- [ ] **Step 2: コンパイル + アセット作成**

MCP: `editor_recompile`

コンパイルエラーが出る場合（LightBeam が未定義）、`LightBeam` 型を一時的にコメントアウトするか、Task 7 と並行して進める。

MCP: `editor_create_udon_program_asset` with:
- `script_path`: `Assets/ForgottenTemple/Scripts/MirrorRotator.cs`
- `output_path`: `Assets/ForgottenTemple/Scripts/MirrorRotator.asset`

- [ ] **Step 3: コミット**

```bash
cd "D:/VRChatProject/PS-WORLD-TEST"
git add Assets/ForgottenTemple/Scripts/MirrorRotator.*
git commit -m "feat: add MirrorRotator script with 90-degree rotation and sync"
```

---

## Task 7: LightBeam スクリプト

**Files:**
- Create: `Assets/ForgottenTemple/Scripts/LightBeam.cs`
- Create: `Assets/ForgottenTemple/Scripts/LightBeam.asset` (via MCP)

**概要:** LineRenderer で光線を描画。鏡の角度に応じてレイキャストし、反射経路を計算してクリスタルに当たったかを判定。

- [ ] **Step 1: LightBeam.cs を作成**

```csharp
using UdonSharp;
using UnityEngine;
using VRC.SDKBase;

[UdonBehaviourSyncMode(BehaviourSyncMode.NoVariableSync)]
public class LightBeam : UdonSharpBehaviour
{
    [Header("Beam Settings")]
    [SerializeField] private LineRenderer lineRenderer;
    [SerializeField] private Transform beamOrigin;
    [SerializeField] private Vector3 beamDirection = Vector3.down;
    [SerializeField] private float maxDistance = 50f;
    [SerializeField] private int maxBounces = 3;
    [SerializeField] private LayerMask reflectLayers;

    [Header("Target")]
    [SerializeField] private Transform crystalTarget;
    [SerializeField] private float hitRadius = 0.5f;
    [SerializeField] private DoorController targetDoor;
    [SerializeField] private GameManager gameManager;
    [SerializeField] private int roomNumber;

    [Header("Crystal Effect")]
    [SerializeField] private ParticleSystem crystalParticle;
    [SerializeField] private AudioSource crystalAudio;
    [SerializeField] private Light crystalLight;

    private bool isSolved;
    private Vector3[] beamPoints;

    public void Start()
    {
        beamPoints = new Vector3[maxBounces + 2];
        _UpdateBeam();
    }

    public void _UpdateBeam()
    {
        if (lineRenderer == null || beamOrigin == null) return;

        Vector3 origin = beamOrigin.position;
        Vector3 direction = beamOrigin.TransformDirection(beamDirection).normalized;

        int pointCount = 1;
        beamPoints[0] = origin;

        for (int i = 0; i < maxBounces + 1; i++)
        {
            RaycastHit hit;
            if (Physics.Raycast(origin, direction, out hit, maxDistance, reflectLayers))
            {
                beamPoints[pointCount] = hit.point;
                pointCount++;

                if (hit.collider.CompareTag("Mirror"))
                {
                    direction = Vector3.Reflect(direction, hit.normal);
                    origin = hit.point + direction * 0.01f;
                }
                else
                {
                    break;
                }
            }
            else
            {
                beamPoints[pointCount] = origin + direction * maxDistance;
                pointCount++;
                break;
            }
        }

        lineRenderer.positionCount = pointCount;
        for (int i = 0; i < pointCount; i++)
        {
            lineRenderer.SetPosition(i, beamPoints[i]);
        }

        _CheckCrystalHit(beamPoints, pointCount);
    }

    private void _CheckCrystalHit(Vector3[] points, int count)
    {
        if (crystalTarget == null) return;
        if (isSolved) return;

        Vector3 lastPoint = points[count - 1];
        float distance = Vector3.Distance(lastPoint, crystalTarget.position);

        if (distance < hitRadius)
        {
            _OnCrystalHit();
        }
    }

    private void _OnCrystalHit()
    {
        isSolved = true;

        if (crystalParticle != null)
            crystalParticle.Play();

        if (crystalAudio != null)
            crystalAudio.Play();

        if (crystalLight != null)
            crystalLight.enabled = true;

        if (targetDoor != null)
            targetDoor._Open();

        if (gameManager != null)
            gameManager._OnRoomCleared(roomNumber);

        Debug.Log($"[LightBeam] Crystal hit! Room {roomNumber} solved.");
    }

    public void _Reset()
    {
        isSolved = false;

        if (crystalParticle != null)
            crystalParticle.Stop();

        if (crystalLight != null)
            crystalLight.enabled = false;
    }
}
```

Write to: `D:/VRChatProject/PS-WORLD-TEST/Assets/ForgottenTemple/Scripts/LightBeam.cs`

**シーン構成の注意:**
- 鏡オブジェクトには `"Mirror"` タグを設定する
- `reflectLayers` に鏡とクリスタルのレイヤーを含める
- 鏡の Collider は BoxCollider で、法線が反射面と一致するようにする

- [ ] **Step 2: コンパイル + アセット作成**

MCP: `editor_recompile`
MCP: `editor_create_udon_program_asset` with:
- `script_path`: `Assets/ForgottenTemple/Scripts/LightBeam.cs`
- `output_path`: `Assets/ForgottenTemple/Scripts/LightBeam.asset`

- [ ] **Step 3: MirrorRotator を再コンパイル**

MirrorRotator が LightBeam を参照しているので再コンパイル:

MCP: `editor_recompile`
MCP: `editor_console` でエラーなしを確認

- [ ] **Step 4: コミット**

```bash
cd "D:/VRChatProject/PS-WORLD-TEST"
git add Assets/ForgottenTemple/Scripts/LightBeam.* Assets/ForgottenTemple/Scripts/MirrorRotator.*
git commit -m "feat: add LightBeam script with raycast reflection and crystal detection"
```

---

## Task 8: AltarFinish スクリプト

**Files:**
- Create: `Assets/ForgottenTemple/Scripts/AltarFinish.cs`
- Create: `Assets/ForgottenTemple/Scripts/AltarFinish.asset` (via MCP)

**概要:** 祭壇の秘宝。2人同時に Interact すると解放演出。

- [ ] **Step 1: AltarFinish.cs を作成**

```csharp
using UdonSharp;
using UnityEngine;
using UnityEngine.UI;
using VRC.SDKBase;

[UdonBehaviourSyncMode(BehaviourSyncMode.Manual)]
public class AltarFinish : UdonSharpBehaviour
{
    [Header("Relic")]
    [SerializeField] private GameObject relicObject;
    [SerializeField] private ParticleSystem clearParticle;
    [SerializeField] private Light clearLight;

    [Header("Audio")]
    [SerializeField] private AudioSource clearBGM;
    [SerializeField] private AudioSource explorationBGM;

    [Header("UI")]
    [SerializeField] private Text clearTimeText;
    [SerializeField] private GameObject clearUI;

    [Header("Game")]
    [SerializeField] private GameManager gameManager;

    [Header("Timing")]
    [SerializeField] private float interactGrace = 3f;

    [UdonSynced] private bool isCleared;
    [UdonSynced] private int interactCount;
    [UdonSynced] private float firstInteractTime;

    private bool localInteracted;

    public override void Interact()
    {
        if (isCleared) return;
        if (localInteracted) return;

        localInteracted = true;

        Networking.SetOwner(Networking.LocalPlayer, gameObject);
        interactCount++;

        if (interactCount == 1)
        {
            firstInteractTime = Time.time;
        }

        RequestSerialization();

        SendCustomNetworkEvent(
            VRC.Udon.Common.Interfaces.NetworkEventTarget.All,
            nameof(_CheckClear));

        Debug.Log($"[AltarFinish] Player interacted. Count={interactCount}");
    }

    public void _CheckClear()
    {
        if (isCleared) return;

        if (interactCount >= 2)
        {
            Networking.SetOwner(Networking.LocalPlayer, gameObject);
            isCleared = true;
            RequestSerialization();

            _PlayClearEffect();
        }
    }

    public void Update()
    {
        if (isCleared) return;
        if (interactCount < 1) return;

        if (Time.time - firstInteractTime > interactGrace)
        {
            Networking.SetOwner(Networking.LocalPlayer, gameObject);
            interactCount = 0;
            localInteracted = false;
            RequestSerialization();

            Debug.Log("[AltarFinish] Interact timed out, resetting");
        }
    }

    private void _PlayClearEffect()
    {
        if (clearParticle != null)
            clearParticle.Play();

        if (clearLight != null)
            clearLight.enabled = true;

        if (clearBGM != null)
            clearBGM.Play();

        if (explorationBGM != null)
            explorationBGM.Stop();

        if (clearUI != null)
            clearUI.SetActive(true);

        if (clearTimeText != null && gameManager != null)
        {
            float elapsed = gameManager._GetElapsedTime();
            int minutes = (int)(elapsed / 60f);
            int seconds = (int)(elapsed % 60f);
            clearTimeText.text = $"Clear Time: {minutes:00}:{seconds:00}";
        }

        Debug.Log("[AltarFinish] GAME CLEARED!");
    }

    public override void OnDeserialization()
    {
        if (isCleared)
            _PlayClearEffect();
    }
}
```

Write to: `D:/VRChatProject/PS-WORLD-TEST/Assets/ForgottenTemple/Scripts/AltarFinish.cs`

- [ ] **Step 2: コンパイル + アセット作成**

MCP: `editor_recompile`
MCP: `editor_create_udon_program_asset` with:
- `script_path`: `Assets/ForgottenTemple/Scripts/AltarFinish.cs`
- `output_path`: `Assets/ForgottenTemple/Scripts/AltarFinish.asset`

- [ ] **Step 3: コミット**

```bash
cd "D:/VRChatProject/PS-WORLD-TEST"
git add Assets/ForgottenTemple/Scripts/AltarFinish.*
git commit -m "feat: add AltarFinish script with dual-interact clear effect"
```

---

## Task 9: FallRespawn スクリプト

**Files:**
- Create: `Assets/ForgottenTemple/Scripts/FallRespawn.cs`
- Create: `Assets/ForgottenTemple/Scripts/FallRespawn.asset` (via MCP)

**概要:** 床下に配置するトリガー。プレイヤーが落下したら直近のリスポーンポイントに戻す。

- [ ] **Step 1: FallRespawn.cs を作成**

```csharp
using UdonSharp;
using UnityEngine;
using VRC.SDKBase;

[UdonBehaviourSyncMode(BehaviourSyncMode.NoVariableSync)]
public class FallRespawn : UdonSharpBehaviour
{
    [Header("Respawn Points (one per room, in order)")]
    [SerializeField] private Transform[] respawnPoints;

    [Header("Game")]
    [SerializeField] private GameManager gameManager;

    public override void OnPlayerTriggerEnter(VRCPlayerApi player)
    {
        if (player != Networking.LocalPlayer) return;

        int room = 0;
        if (gameManager != null)
            room = gameManager._GetCurrentRoom();

        int index = Mathf.Clamp(room, 0, respawnPoints.Length - 1);

        if (respawnPoints.Length > 0 && respawnPoints[index] != null)
        {
            player.TeleportTo(
                respawnPoints[index].position,
                respawnPoints[index].rotation);
        }

        Debug.Log($"[FallRespawn] Player respawned to room {index}");
    }
}
```

Write to: `D:/VRChatProject/PS-WORLD-TEST/Assets/ForgottenTemple/Scripts/FallRespawn.cs`

- [ ] **Step 2: コンパイル + アセット作成**

MCP: `editor_recompile`
MCP: `editor_create_udon_program_asset` with:
- `script_path`: `Assets/ForgottenTemple/Scripts/FallRespawn.cs`
- `output_path`: `Assets/ForgottenTemple/Scripts/FallRespawn.asset`

- [ ] **Step 3: コミット**

```bash
cd "D:/VRChatProject/PS-WORLD-TEST"
git add Assets/ForgottenTemple/Scripts/FallRespawn.*
git commit -m "feat: add FallRespawn script for player fall detection"
```

---

## Task 10: 全スクリプトのコンパイル検証

**Files:**
- 変更なし（検証のみ）

**概要:** 全スクリプトが揃ったので、コンパイルエラーがないことと相互参照の整合性を確認する。

- [ ] **Step 1: 全体リコンパイル**

MCP: `editor_recompile`
MCP: `editor_console` でエラー確認

Expected: コンパイルエラー 0 件

- [ ] **Step 2: コンパイルエラーがあれば修正**

よくあるエラー:
- 型の相互参照不整合 → 型名のスペル確認
- `using` 不足 → `VRC.Udon.Common.Interfaces` の追加
- UdonSharp 非対応構文 → 配列以外のコレクション使用がないか確認

修正後は再度 `editor_recompile` + `editor_console` で確認。

- [ ] **Step 3: コミット（修正があった場合のみ）**

```bash
cd "D:/VRChatProject/PS-WORLD-TEST"
git add Assets/ForgottenTemple/Scripts/
git commit -m "fix: resolve compile errors across all game scripts"
```

---

## Task 11: ロビー（チュートリアル）のシーン構築

**Files:**
- Modify: `Assets/ForgottenTemple/Scenes/ForgottenTemple.unity`

**概要:** ロビーエリアをシーンに構築。床、壁、2つの台座（PressurePlate）、扉（DoorController）、GameManager を配置して配線。

- [ ] **Step 1: GameManager をシーンに配置**

MCP 操作:
1. `editor_instantiate` で空オブジェクト作成 → `editor_rename` で `GameManager` に
2. `editor_add_component` で `GameManager` コンポーネント追加

- [ ] **Step 2: ロビーの構造物を配置**

MCP 操作:
1. 空オブジェクト `Lobby` を作成（親コンテナ）
2. 子に Cube を配置: `Lobby/Floor` (Scale: 10, 0.1, 10)
3. 子に Cube x4: `Lobby/Wall_N`, `Wall_S`, `Wall_E`, `Wall_W` （壁）
4. 子に Cube: `Lobby/Door` (扉メッシュ)
5. `Lobby/Door` に `DoorController` コンポーネント追加

- [ ] **Step 3: 台座（PressurePlate）を配置**

MCP 操作:
1. `Lobby/PlateA` — Cube (Scale: 1, 0.05, 1) + BoxCollider (Is Trigger = true)
2. `Lobby/PlateB` — 同様に反対側に配置
3. 両方に `PressurePlate` コンポーネント追加
4. `editor_set_property` で配線:
   - `PlateA.pairedPlate` → PlateB
   - `PlateA.targetDoor` → Door の DoorController
   - `PlateA.gameManager` → GameManager
   - `PlateA.roomNumber` → 0
   - PlateB も同様（pairedPlate → PlateA）

- [ ] **Step 4: 配線検証**

MCP: `inspect_wiring` で GameManager, DoorController, PressurePlate の参照を確認
MCP: `validate_refs` で broken reference がないことを確認

Expected: null 参照なし

- [ ] **Step 5: ClientSim で動作確認**

Unity でプレイモードに入り以下を確認:
1. 台座 A に立つ → 台座が緑に光る
2. （1人テストなので2人同時は不可。ログで `OnPlayerTriggerEnter` 発火を確認）
3. DoorController のログ確認

MCP: `editor_console` でエラー確認

- [ ] **Step 6: コミット**

```bash
cd "D:/VRChatProject/PS-WORLD-TEST"
git add Assets/ForgottenTemple/
git commit -m "feat: build lobby tutorial room with pressure plates and door"
```

---

## Task 12: 部屋1（灯火の間）のシーン構築

**Files:**
- Modify: `Assets/ForgottenTemple/Scenes/ForgottenTemple.unity`

**概要:** 暗い部屋にたいまつと3つの通路を配置。正解の通路にシンボル、反対側にヒント碑文。

- [ ] **Step 1: 部屋1の構造物を配置**

MCP 操作:
1. 空オブジェクト `Room1_Torch` を作成
2. 子に Floor, Walls（ロビーと同様）
3. 子に3つの通路入口: `Passage_Sun`（正解）, `Passage_Moon`, `Passage_Star`
4. 正解通路の奥に次の部屋への接続
5. 不正解通路は行き止まり（短い通路 + 壁）

- [ ] **Step 2: たいまつを配置**

MCP 操作:
1. 空オブジェクト `Room1_Torch/TorchPickup` を作成
2. 子に Capsule (たいまつメッシュ代用)
3. 子に Point Light (Range: 5, Intensity: 1.5, Color: warm orange)
4. 子に Particle System (炎エフェクト)
5. `TorchPickup` に以下コンポーネント追加:
   - `Rigidbody`
   - `CapsuleCollider`
   - `VRC.SDK3.Components.VRCPickup` (InteractionText = "たいまつ")
   - `VRC.SDK3.Components.VRCObjectSync`
   - `TorchLight`
6. `editor_set_property` で TorchLight の参照を配線:
   - `torchPointLight` → 子の Point Light
   - `flameParticle` → 子の Particle System

- [ ] **Step 3: ヒント碑文を配置**

1. `Room1_Torch/HintInscription` — 3D Text または Canvas + Text
2. テキスト: 「太陽の印を追え」
3. たいまつの反対側の壁に配置（たいまつを持っていない人が読める位置）

- [ ] **Step 4: 正解通路のクリアトリガーを配置**

1. `Room1_Torch/Passage_Sun/ClearTrigger` — 空オブジェクト + BoxCollider (Is Trigger = true)
2. 正解通路の奥に配置（プレイヤーが通ると発火）
3. `PressurePlate` コンポーネント追加（ソロモード: `pairedPlate` = null）
4. 配線:
   - `pairedPlate` → null（ソロモード: 1人で踏めばクリア）
   - `targetDoor` → Door
   - `gameManager` → GameManager
   - `roomNumber` → 1

- [ ] **Step 5: 扉を配置**

1. `Room1_Torch/Door` に `DoorController` 追加
2. 正解通路の奥、ClearTrigger の先に配置
3. GameManager の `room1Door` に配線

- [ ] **Step 5: 部屋のライティング**

1. 部屋全体を暗くする（Ambient Light を最低にするか、部屋を囲むように配置）
2. たいまつの Point Light だけが光源になるようにする
3. ベイクドライティングは部屋の通路にわずかな光を入れる（完全な暗闇はUX悪化）

- [ ] **Step 6: 配線検証 + ClientSim テスト**

MCP: `inspect_wiring` で TorchLight の参照確認
MCP: `validate_refs` で broken reference なし

ClientSim:
1. たいまつを掴む → ライト点灯確認
2. 離す → ライト消灯確認
3. 30秒放置 → リスポーン確認

- [ ] **Step 7: コミット**

```bash
cd "D:/VRChatProject/PS-WORLD-TEST"
git add Assets/ForgottenTemple/
git commit -m "feat: build Room 1 (torch room) with pickup light and hint inscriptions"
```

---

## Task 13: 部屋2（重圧の間）のシーン構築

**Files:**
- Modify: `Assets/ForgottenTemple/Scenes/ForgottenTemple.unity`

**概要:** 2つの圧力板を部屋の両端に配置。ロビーの PressurePlate を再利用。

- [ ] **Step 1: 部屋2の構造物を配置**

MCP 操作:
1. 空オブジェクト `Room2_Pressure` を作成
2. 子に Floor, Walls
3. 部屋の両端に `PlateA_R2`, `PlateB_R2` を配置（距離を十分に離す: 8m 程度）
4. 各プレートに: Cube (Scale: 1.5, 0.05, 1.5) + BoxCollider (Is Trigger) + `PressurePlate`

- [ ] **Step 2: 扉と配線**

1. `Room2_Pressure/Door` に `DoorController` 追加
2. 両プレートの配線:
   - `pairedPlate` → 相手プレート
   - `targetDoor` → Door
   - `gameManager` → GameManager
   - `roomNumber` → 2
3. GameManager の `room2Door` に配線

- [ ] **Step 3: 視覚フィードバック追加**

1. 各プレートに光るマテリアル（Emission）を設定
2. plateRenderer に Cube の Renderer を配線

- [ ] **Step 4: 配線検証 + ClientSim テスト**

MCP: `inspect_wiring`, `validate_refs`

ClientSim:
1. 片方のプレートに乗る → 緑に光る
2. 3秒後にリセットされる → 色が戻る
3. ログで `_CheckBothPlates` の発火を確認

- [ ] **Step 5: コミット**

```bash
cd "D:/VRChatProject/PS-WORLD-TEST"
git add Assets/ForgottenTemple/
git commit -m "feat: build Room 2 (pressure room) with dual plates"
```

---

## Task 14: 部屋3（鏡の間）のシーン構築

**Files:**
- Modify: `Assets/ForgottenTemple/Scenes/ForgottenTemple.unity`

**概要:** 天井から光線、鏡2枚、クリスタルターゲット。光を反射させてクリスタルに当てる。

- [ ] **Step 1: 部屋3の構造物を配置**

MCP 操作:
1. 空オブジェクト `Room3_Mirror` を作成
2. 子に Floor, Walls, 天井
3. 天井に穴（光源の入口）を作る

- [ ] **Step 2: 光源と LightBeam を配置**

1. `Room3_Mirror/LightSource` — 天井の穴の位置に空オブジェクト
2. `Room3_Mirror/LightBeam` — 空オブジェクト + LineRenderer + `LightBeam` コンポーネント
3. LineRenderer 設定:
   - Width: 0.05
   - Color: 黄色 / 白
   - Material: Default-Line または Unlit
4. `LightBeam` の配線:
   - `lineRenderer` → LineRenderer
   - `beamOrigin` → LightSource Transform
   - `beamDirection` → (0, -1, 0)
   - `maxBounces` → 3
   - `reflectLayers` → Default レイヤー

- [ ] **Step 3: 鏡2枚を配置**

1. `Room3_Mirror/MirrorA` — Quad (片面) + BoxCollider + Tag: "Mirror" + `MirrorRotator`
2. `Room3_Mirror/MirrorB` — 同様
3. 鏡を正解角度で光がクリスタルに届く位置に配置
4. 各 MirrorRotator の `lightBeam` → LightBeam を配線

**配置のポイント:**
- MirrorA: 光源の直下付近、回転軸 Y
- MirrorB: MirrorA から反射した光が届く位置
- 正解は MirrorA=index 1 (90°), MirrorB=index 2 (180°) など

- [ ] **Step 4: クリスタルターゲットを配置**

1. `Room3_Mirror/Crystal` — Sphere (Scale: 0.3) + Emission マテリアル
2. 子に Point Light (初期 disabled)
3. 子に Particle System (初期 stopped)
4. LightBeam の配線:
   - `crystalTarget` → Crystal Transform
   - `hitRadius` → 0.5
   - `targetDoor` → Door
   - `gameManager` → GameManager
   - `roomNumber` → 3
   - `crystalParticle` → Crystal の Particle System
   - `crystalLight` → Crystal の Point Light

- [ ] **Step 5: 扉を配置**

1. `Room3_Mirror/Door` に `DoorController` 追加
2. GameManager の `room3Door` に配線

- [ ] **Step 6: Mirror タグを作成**

Unity の Tag Manager に `"Mirror"` タグを追加（MCP または手動）。
MirrorA, MirrorB に `"Mirror"` タグを設定。

- [ ] **Step 7: 配線検証 + ClientSim テスト**

MCP: `inspect_wiring` で LightBeam, MirrorRotator の参照確認
MCP: `validate_refs`

ClientSim:
1. 光線が天井から表示されていることを確認
2. 鏡A を Interact → 90度回転、光線の方向が変わる
3. 鏡B を Interact → 光線がさらに反射
4. 正解角度にすると Crystal が光る + 扉が開く

- [ ] **Step 8: コミット**

```bash
cd "D:/VRChatProject/PS-WORLD-TEST"
git add Assets/ForgottenTemple/
git commit -m "feat: build Room 3 (mirror room) with light beam reflection puzzle"
```

---

## Task 15: 部屋4（試練の間）のシーン構築

**Files:**
- Modify: `Assets/ForgottenTemple/Scenes/ForgottenTemple.unity`

**概要:** 灯火 + 圧力板 + 鏡の複合パズル。暗い部屋でたいまつを使い、ヒントを読み、鏡を合わせ、圧力板で光源起動。

- [ ] **Step 1: 部屋4の構造物を配置**

MCP 操作:
1. 空オブジェクト `Room4_Trial` を作成
2. 子に Floor, Walls（暗い部屋）
3. 壁にヒント碑文: 「光を目覚めさせよ。一人は鏡を、一人は大地を踏め」

- [ ] **Step 2: たいまつを配置**

1. `Room4_Trial/TorchPickup2` — Room1 と同じ構成
2. TorchLight コンポーネント + VRCPickup + VRCObjectSync + Rigidbody + Collider
3. 子に Point Light + Particle System
4. 全て配線

- [ ] **Step 3: 圧力板2枚を配置**

1. `Room4_Trial/PlateC`, `Room4_Trial/PlateD` — PressurePlate コンポーネント
2. ただし、この部屋では圧力板が扉を直接開けるのではなく**光源を起動**する
3. PressurePlate の `targetDoor` は null のまま
4. 代わりに、2枚同時に乗ると光源 GameObject が SetActive(true) になるカスタム動作が必要

**対応方法:** PressurePlate の `targetDoor` の代わりに、圧力板が CheckBothPlates で光源オブジェクトを有効化する。
これは PressurePlate に `targetObject` (GameObject) フィールドを追加するか、別のアプローチとする。

**シンプルな方法:** PressurePlate に `targetActivateObject` フィールドを追加:

PressurePlate.cs に以下を追加:
```csharp
    [Header("Activate Object (alternative to door)")]
    [SerializeField] private GameObject targetActivateObject;
```

`_CheckBothPlates()` 内に追加:
```csharp
        if (targetActivateObject != null)
            targetActivateObject.SetActive(true);
```

- [ ] **Step 4: 鏡1枚 + LightBeam を配置**

1. `Room4_Trial/LightSource2` — 圧力板で有効化される光源（初期 disabled の GameObject に LineRenderer 含む）
2. `Room4_Trial/LightBeam2` に `LightBeam` コンポーネント
3. `Room4_Trial/MirrorC` に `MirrorRotator` コンポーネント + Tag: "Mirror"
4. `Room4_Trial/Crystal2` — ターゲットクリスタル
5. LightBeam2 の配線:
   - `targetDoor` → Room4 の Door
   - `gameManager` → GameManager
   - `roomNumber` → 4

- [ ] **Step 5: 扉を配置**

1. `Room4_Trial/Door` に `DoorController` 追加
2. GameManager の `room4Door` に配線

- [ ] **Step 6: PressurePlate.cs を更新してリコンパイル**

PressurePlate.cs に `targetActivateObject` を追加した後:

MCP: `editor_recompile`
MCP: `editor_console` でエラー確認

- [ ] **Step 7: 配線検証 + ClientSim テスト**

MCP: `inspect_wiring`, `validate_refs`

ClientSim:
1. 暗い部屋 → たいまつで壁のヒントを確認
2. 圧力板に乗る → 光源が起動
3. 鏡を回転 → 光がクリスタルに当たる → 扉が開く

- [ ] **Step 8: コミット**

```bash
cd "D:/VRChatProject/PS-WORLD-TEST"
git add Assets/ForgottenTemple/
git commit -m "feat: build Room 4 (trial room) with composite torch+plate+mirror puzzle"
```

---

## Task 16: 祭壇エリアのシーン構築

**Files:**
- Modify: `Assets/ForgottenTemple/Scenes/ForgottenTemple.unity`

**概要:** クリア演出エリア。秘宝オブジェクト + AltarFinish + 記念撮影スポット。

- [ ] **Step 1: 祭壇の構造物を配置**

MCP 操作:
1. 空オブジェクト `Altar` を作成
2. 子に Floor, Walls（装飾的な祭壇空間）
3. 中央に台座: Cylinder (Scale: 0.5, 1, 0.5)

- [ ] **Step 2: 秘宝オブジェクトを配置**

1. `Altar/Relic` — Sphere (Scale: 0.3) + Emission マテリアル（光り輝く）
2. 台座の上に浮かせて配置 (Y 位置を台座より上に)
3. `Relic` に `AltarFinish` コンポーネント追加
4. 子に Particle System (クリア演出用、初期 stopped)
5. 子に Point Light (クリア演出用、初期 disabled)

- [ ] **Step 3: クリアUI を配置**

1. `Altar/ClearCanvas` — Canvas (World Space)
2. 子に Text: "CLEAR!" (大きなフォント)
3. 子に Text: クリアタイム表示用
4. 初期状態で Canvas を非アクティブ

- [ ] **Step 4: AltarFinish の配線**

`editor_set_property`:
- `relicObject` → Relic
- `clearParticle` → Relic の子 Particle System
- `clearLight` → Relic の子 Point Light
- `clearTimeText` → クリアタイム Text
- `clearUI` → ClearCanvas
- `gameManager` → GameManager

- [ ] **Step 5: 記念撮影スポットを配置**

1. `Altar/PhotoSpot` — 2人が並べる台座（Cube, Scale: 2, 0.1, 1）
2. 背景に装飾（秘宝が光っている状態が映える位置）

- [ ] **Step 6: 配線検証 + ClientSim テスト**

MCP: `inspect_wiring`, `validate_refs`

ClientSim:
1. 秘宝に Interact → ログに `interactCount=1`
2. 3秒後にリセット → ログ確認
3. （2人テストは後のインテグレーションで確認）

- [ ] **Step 7: コミット**

```bash
cd "D:/VRChatProject/PS-WORLD-TEST"
git add Assets/ForgottenTemple/
git commit -m "feat: build Altar area with clear effect and photo spot"
```

---

## Task 17: 落下リスポーン + 全体接続

**Files:**
- Modify: `Assets/ForgottenTemple/Scenes/ForgottenTemple.unity`

**概要:** FallRespawn を配置し、全部屋をつなぐ通路を構築。VRCSceneDescriptor のスポーン設定。

- [ ] **Step 1: リスポーンポイントを各部屋に配置**

MCP 操作:
1. 各部屋の入口付近に空オブジェクト `RespawnPoint_RoomN` を配置（N = 0〜5）
2. ロビー、部屋1〜4、祭壇の6箇所

- [ ] **Step 2: FallRespawn を配置**

1. シーン最下部に大きなトリガーコライダー: `FallZone` (BoxCollider, Is Trigger, Scale: 100, 1, 100, Y = -10)
2. `FallRespawn` コンポーネント追加
3. `respawnPoints` 配列に6つのリスポーンポイントを配線
4. `gameManager` → GameManager

- [ ] **Step 3: 部屋間の通路を構築**

各部屋の扉の先に短い通路（Cube の床と壁）を配置して次の部屋に接続:
- ロビー → 部屋1
- 部屋1 → 部屋2
- 部屋2 → 部屋3
- 部屋3 → 部屋4
- 部屋4 → 祭壇

- [ ] **Step 4: VRCSceneDescriptor を設定**

1. VRCWorld の `spawns` にロビーのスポーンポイントを設定
2. `RespawnHeightY` → -15 (FallZone より下)

MCP: `editor_set_property` で VRCSceneDescriptor のプロパティを設定

- [ ] **Step 5: GameManager に全 DoorController を配線**

MCP: `editor_set_property`:
- `lobbyDoor` → Lobby/Door
- `room1Door` → Room1_Torch/Door
- `room2Door` → Room2_Pressure/Door
- `room3Door` → Room3_Mirror/Door
- `room4Door` → Room4_Trial/Door

- [ ] **Step 6: 配線検証**

MCP: `inspect_wiring` で GameManager の全参照を確認
MCP: `validate_refs` で全体の broken reference チェック

Expected: null 参照なし、broken reference なし

- [ ] **Step 7: コミット**

```bash
cd "D:/VRChatProject/PS-WORLD-TEST"
git add Assets/ForgottenTemple/
git commit -m "feat: connect all rooms with corridors, fall respawn, and VRCWorld settings"
```

---

## Task 18: オーディオ統合

**Files:**
- Modify: `Assets/ForgottenTemple/Scenes/ForgottenTemple.unity`
- Create: `Assets/ForgottenTemple/Audio/` 内にプレースホルダー AudioClip

**概要:** BGM とSE の AudioSource をシーンに配置して配線。

- [ ] **Step 1: BGM 用 AudioSource を配置**

MCP 操作:
1. `Audio/BGM_Exploration` — AudioSource (Play On Awake, Loop, Volume: 0.3)
2. `Audio/BGM_Clear` — AudioSource (Play On Awake: false, Loop: true, Volume: 0.5)
3. AltarFinish に配線:
   - `explorationBGM` → BGM_Exploration
   - `clearBGM` → BGM_Clear

- [ ] **Step 2: SE 用 AudioSource を各ギミックに配置**

1. 各 DoorController の子に AudioSource → `doorAudio` 配線
2. 各 PressurePlate の子に AudioSource → `plateAudio` 配線
3. TorchLight の子に AudioSource (Loop, Play On Awake: false) → `flameAudio` 配線
4. 各 MirrorRotator の子に AudioSource → `rotateAudio` 配線
5. 各 LightBeam のクリスタルに AudioSource → `crystalAudio` 配線

**注意:** 実際の AudioClip はフリー素材サイトからダウンロードしてインポートする。この段階では AudioSource コンポーネントの配置と配線のみ。

- [ ] **Step 3: 配線検証**

MCP: `inspect_wiring` で全オーディオ参照の確認

- [ ] **Step 4: コミット**

```bash
cd "D:/VRChatProject/PS-WORLD-TEST"
git add Assets/ForgottenTemple/
git commit -m "feat: add audio sources for BGM and SE across all rooms"
```

---

## Task 19: ライティング + マテリアル設定

**Files:**
- Modify: `Assets/ForgottenTemple/Scenes/ForgottenTemple.unity`
- Create: `Assets/ForgottenTemple/Materials/` 内にマテリアル

**概要:** ベイクドライティング設定、マテリアル作成、パフォーマンス最適化。

- [ ] **Step 1: 共通マテリアルを作成**

以下のマテリアルを作成（Standard Shader / Mobile-friendly）:

| マテリアル名 | 用途 | 色 |
|-------------|------|-----|
| `M_StoneWall` | 壁 | 灰色、ラフ |
| `M_StoneFloor` | 床 | 暗い灰色 |
| `M_Door` | 扉 | 茶色 |
| `M_PressurePlate` | 圧力板 | 金属的な灰色 |
| `M_Mirror` | 鏡 | 反射的、明るい |
| `M_Crystal` | クリスタル | Emission 青白 |
| `M_Relic` | 秘宝 | Emission 金色 |

MCP: `editor_set_material` で各オブジェクトにマテリアルを設定

- [ ] **Step 2: ライティング設定**

1. Directional Light: 弱め (Intensity: 0.2)、ベイクドモードに設定
2. 各部屋の入口付近にベイクド Point Light（薄暗い誘導光）
3. たいまつの Point Light: Realtime、Range: 5、Intensity: 1.5
4. ライトプローブグループを通路に配置

- [ ] **Step 3: ライトベイク**

```
editor_execute_menu_item: "Window/Rendering/Lighting"
```

ライトマップをベイク（時間がかかる場合あり）

- [ ] **Step 4: パフォーマンス確認**

Stats ウィンドウで以下を確認:
- Triangle count < 50k
- Batches < 100
- リアルタイム Point Light: 同時2個以下

- [ ] **Step 5: コミット**

```bash
cd "D:/VRChatProject/PS-WORLD-TEST"
git add Assets/ForgottenTemple/
git commit -m "feat: add materials and baked lighting for temple atmosphere"
```

---

## Task 20: フルインテグレーションテスト

**Files:**
- 変更なし（テストのみ）

**概要:** 全エリアを通しでプレイし、ゲーム進行の整合性を確認。

- [ ] **Step 1: ClientSim で通しプレイ**

以下のフローを確認:

1. **ロビー**: スポーン → 台座に乗る → 扉が開く（1人テストなので手動でデバッグ）
2. **部屋1**: たいまつを拾う → 光る → 壁のシンボルを確認 → 正解通路を進む
3. **部屋2**: 圧力板に乗る → 光る → （ペアテストは後日）
4. **部屋3**: 鏡を Interact で回転 → 光線が反射 → クリスタルに当たる → 扉が開く
5. **部屋4**: たいまつ拾う → ヒント確認 → 鏡セット → 圧力板 → 光源起動 → クリスタル
6. **祭壇**: 秘宝に Interact → （ペアテストは後日）

MCP: `editor_console` で各ステップのログ確認

- [ ] **Step 2: エラーの修正**

コンソールに出た Warning/Error を修正:
- null reference → 配線漏れを `inspect_wiring` で特定して修正
- 同期エラー → `OnDeserialization` のタイミング確認

- [ ] **Step 3: validate_refs で最終チェック**

MCP: `validate_refs` でシーン全体の参照検証
MCP: `validate_structure` で構造的な問題がないか確認

Expected: error / critical = 0件

- [ ] **Step 4: コミット**

```bash
cd "D:/VRChatProject/PS-WORLD-TEST"
git add Assets/ForgottenTemple/
git commit -m "fix: integration test fixes for full game flow"
```

---

## タスク依存関係

```
Task 1 (Project Structure)
  ├── Task 2 (GameManager) ─┐
  ├── Task 3 (DoorController) ──┤
  ├── Task 4 (PressurePlate) ───┤
  ├── Task 5 (TorchLight) ─────┤
  ├── Task 6 (MirrorRotator) ──┤
  ├── Task 7 (LightBeam) ──────┤
  ├── Task 8 (AltarFinish) ────┤
  └── Task 9 (FallRespawn) ────┤
                                │
  Task 10 (Compile Check) ◄────┘
    │
    ├── Task 11 (Lobby Scene)
    ├── Task 12 (Room 1 Scene)
    ├── Task 13 (Room 2 Scene)
    ├── Task 14 (Room 3 Scene)
    ├── Task 15 (Room 4 Scene) ← depends on PressurePlate update
    └── Task 16 (Altar Scene)
          │
          ├── Task 17 (Fall Respawn + Connections)
          ├── Task 18 (Audio)
          └── Task 19 (Lighting + Materials)
                │
                └── Task 20 (Integration Test)
```

Tasks 2-9 は相互参照があるため、まとめてコンパイルする Task 10 を間に挟む。
Tasks 11-16 はシーン構築で順序依存なし（ただし通路接続は Task 17 で行う）。
