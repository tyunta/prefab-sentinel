# Phase 3: VRCSDK Upload — Design Spec

## Background

Phase 1 (Editor Bridge) and Phase 2 (Material Editing) are complete. Phase 3 adds VRC SDK build + upload capability via MCP, enabling avatar/world update workflows without leaving the AI conversation.

## Scope

| # | Feature | Status |
|---|---------|--------|
| 3.1 | `VRCSDKUploadHandler.cs` — Unity C# handler | Target |
| 3.2 | `vrcsdk_upload` MCP tool — Bridge + Python | Target |

## Constraints

- **Existing asset update only** — `blueprint_id` is required. New asset creation is out of scope (use VRChat SDK control panel for first upload).
- **Synchronous blocking** — `GetAwaiter().GetResult()` to sync-ify VRC SDK async APIs. Editor freezes during build/upload (acceptable — it freezes during manual upload too).
- **No retry** — failures return error immediately. User decides whether to re-run.
- **VRC SDK 3.x only** — `#if VRC_SDK_VRCSDK3` compile guard. SDK-absent projects get a clean error.
- **Avatar + World** — both target types supported.

**Deadlock contingency:** `GetAwaiter().GetResult()` on the Unity main thread can deadlock if VRC SDK posts continuations to `SynchronizationContext`. If this occurs at implementation time, fall back to `EditorApplication.delayCall` + polling pattern with a status file.

## Out of Scope

- New asset creation (`blueprint_id` empty → new VRC asset record)
- Retry / exponential backoff
- Progress streaming mid-operation
- Multi-platform build (Phase 4)
- Thumbnail capture (Phase 4)
- Versioning / Git tagging (Phase 4)

## Roadmap Deviations

This spec supersedes the roadmap (`2026-03-25-prefab-sentinel-roadmap-design.md` Phase 3) on these points:
- **Retry**: roadmap specified 3x exponential backoff → removed (YAGNI)
- **New asset creation**: roadmap allowed empty `blueprint_id` → `blueprint_id` required
- **Phase streaming**: roadmap implied `"building"` → `"uploading_bundle"` → `"complete"` intermediate responses → single synchronous response only (bridge protocol is request/response, no streaming)

---

## 3.1 C# Handler: `VRCSDKUploadHandler`

### File

`tools/unity/PrefabSentinel.VRCSDKUploadHandler.cs` — new file, entirely wrapped in `#if VRC_SDK_VRCSDK3`.

### Entry Point

```csharp
public static EditorControlResponse Handle(EditorControlRequest request)
```

Called from `UnityEditorControlBridge` dispatch:

```csharp
case "vrcsdk_upload":
#if VRC_SDK_VRCSDK3
    response = VRCSDKUploadHandler.Handle(request);
#else
    response = BuildError("VRCSDK_NOT_AVAILABLE",
        "VRC SDK not found in project. Install VRChat SDK 3.x");
#endif
    break;
```

### Processing Flow

1. **Input validation** — `target_type`, `asset_path`, `blueprint_id` are required
2. **VRC SDK login check** — `APIUser.IsLoggedIn` → false: `VRCSDK_NOT_LOGGED_IN`
3. **Asset validation**
   - Avatar: `AssetDatabase.LoadAssetAtPath<GameObject>(asset_path)` → `GetComponent<VRCAvatarDescriptor>()`
   - World: `EditorSceneManager.OpenScene(asset_path)` → `FindObjectOfType<VRC_SceneDescriptor>()`
   - Missing descriptor → `VRCSDK_MISSING_DESCRIPTOR`
4. **dry-run return** — if `confirm` is false, return `phase: "validated"` here
5. **Build + Upload** (synchronous)
   - Avatar: `VRCSdkControlPanel.TryGetBuilder<IVRCSdkAvatarBuilderApi>(out var builder)` → `builder.BuildAndUpload(prefab, blueprintId, ...).GetAwaiter().GetResult()`
   - World: same pattern with `IVRCSdkWorldBuilderApi`
6. **Result** — return `phase: "complete"` with `elapsed_sec`

### EditorControlRequest Fields

Existing fields reused:
- `asset_path` — already exists on DTO, used by `instantiate_to_scene` / `ping_object`
- `confirm` — add `public bool confirm = false;` to the DTO (does not exist yet)

New fields:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `target_type` | `string` | `""` | `"avatar"` or `"world"` |
| `blueprint_id` | `string` | `""` | Existing VRC asset ID (e.g. `avtr_xxx...`) |
| `description` | `string` | `""` | Description text (empty = no change) |
| `tags` | `string` | `""` | JSON array string (empty = no change) |
| `release_status` | `string` | `""` | `"public"` or `"private"` (empty = no change) |

### EditorControlData Fields (new, for response)

| Field | Type | Description |
|-------|------|-------------|
| `target_type` | `string` | Echo of request target_type |
| `blueprint_id` | `string` | Echo of request blueprint_id |
| `phase` | `string` | `"validated"` or `"complete"` |
| `elapsed_sec` | `float` | Build + upload wall time (0 for dry-run) |

### Error Codes

| Code | Condition | Severity |
|------|-----------|----------|
| `VRCSDK_NOT_AVAILABLE` | VRC SDK not installed (`#else` branch) | error |
| `VRCSDK_NOT_LOGGED_IN` | `APIUser.IsLoggedIn == false` | error |
| `VRCSDK_INVALID_TARGET_TYPE` | target_type is not `"avatar"` or `"world"` | error |
| `VRCSDK_ASSET_NOT_FOUND` | asset_path does not exist or can't be loaded | error |
| `VRCSDK_MISSING_DESCRIPTOR` | No VRCAvatarDescriptor / VRC_SceneDescriptor | error |
| `VRCSDK_MISSING_BLUEPRINT_ID` | blueprint_id is empty | error |
| `VRCSDK_BUILD_FAILED` | SDK build step failed (error details in message) | error |
| `VRCSDK_UPLOAD_FAILED` | SDK upload step failed | error |

---

## 3.2 MCP Tool: `vrcsdk_upload`

### Tool Signature

```
vrcsdk_upload(
    target_type: str,             # "avatar" or "world"
    asset_path: str,              # Prefab path (avatar) or Scene path (world)
    blueprint_id: str,            # Existing VRC asset ID (required)
    description: str = "",        # Description text (empty = no change)
    tags: str = "",               # JSON array of tag strings (empty = no change)
    release_status: str = "",     # "public" | "private" (empty = no change)
    confirm: bool = False,        # False = dry-run, True = build + upload
    change_reason: str = "",      # Required when confirm=True (audit log)
    timeout_sec: int = 600,       # Bridge timeout for long-running upload
)
```

### dry-run (confirm=False)

Validates without building:
- SDK login state
- Asset existence + descriptor presence
- blueprint_id non-empty

Response:
```json
{
  "success": true,
  "severity": "info",
  "code": "VRCSDK_VALIDATED",
  "message": "Validation passed for avatar at Assets/Avatars/MyAvatar.prefab",
  "data": {
    "target_type": "avatar",
    "asset_path": "Assets/Avatars/MyAvatar.prefab",
    "blueprint_id": "avtr_xxx...",
    "phase": "validated",
    "elapsed_sec": 0
  }
}
```

### confirm=True

Builds and uploads. Response:
```json
{
  "success": true,
  "severity": "info",
  "code": "VRCSDK_UPLOAD_OK",
  "message": "Uploaded avatar 'MyAvatar' (avtr_xxx...) in 45.2s",
  "data": {
    "target_type": "avatar",
    "asset_path": "Assets/Avatars/MyAvatar.prefab",
    "blueprint_id": "avtr_xxx...",
    "phase": "complete",
    "elapsed_sec": 45.2
  }
}
```

### Python Implementation

| File | Change |
|------|--------|
| `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` | Add `"vrcsdk_upload"` to `SupportedActions` HashSet + dispatch case + DTO fields (`confirm`, `target_type`, `blueprint_id`, `description`, `tags`, `release_status`) + response DTO fields (`target_type`, `blueprint_id`, `phase`, `elapsed_sec`) |
| `tools/unity/PrefabSentinel.VRCSDKUploadHandler.cs` | New file: `Handle()` entry point, validation, build + upload logic |
| `prefab_sentinel/editor_bridge.py` | Add `"vrcsdk_upload"` to `SUPPORTED_ACTIONS` |
| `prefab_sentinel/mcp_server.py` | Register `vrcsdk_upload` tool, delegate to `send_action()` with `timeout_sec` parameter |
| `tests/test_mcp_server.py` | Tool registration + count update + delegation test |
| `tests/test_editor_bridge.py` | SUPPORTED_ACTIONS test update |

The MCP tool is a thin wrapper:
1. Validate `change_reason` when `confirm=True` — return `VRCSDK_REASON_REQUIRED` error (standard envelope) if empty. This check is Python-side only; `change_reason` is an audit field and is NOT transmitted to C#.
2. Delegate to `send_action(action="vrcsdk_upload", timeout_sec=timeout_sec, ...)`.

No orchestrator method needed (purely bridge-delegated).

### Bridge Action

`"vrcsdk_upload"` added to `SUPPORTED_ACTIONS` (Python) and `SupportedActions` (C#).

---

## Error Handling

| Phase | Error | Response |
|-------|-------|----------|
| Python | `confirm=True` + empty `change_reason` | `VRCSDK_REASON_REQUIRED` (error) — before bridge call |
| Validation | SDK not installed | `VRCSDK_NOT_AVAILABLE` (error) |
| Validation | Not logged in | `VRCSDK_NOT_LOGGED_IN` (error) |
| Validation | Asset not found | `VRCSDK_ASSET_NOT_FOUND` (error) |
| Validation | No descriptor | `VRCSDK_MISSING_DESCRIPTOR` (error) |
| Validation | No blueprint_id | `VRCSDK_MISSING_BLUEPRINT_ID` (error) |
| Build | Build failure | `VRCSDK_BUILD_FAILED` (error), SDK error in message |
| Upload | Upload failure | `VRCSDK_UPLOAD_FAILED` (error), SDK error in message |
| Bridge | Timeout (>600s) | Bridge-level timeout error (existing mechanism) |

No retry. No auto-refresh (upload is not a local asset mutation).

---

## Testing

### Python (automated)

| Test | Content |
|------|---------|
| MCP tool registration | `"vrcsdk_upload"` in tool set + count update |
| SUPPORTED_ACTIONS | `"vrcsdk_upload"` in frozenset |
| `send_action` delegation | Mock: verify parameters passed correctly including `timeout_sec=600` |
| `change_reason` gate | `confirm=True` + empty `change_reason` → `VRCSDK_REASON_REQUIRED` error (standard envelope, no bridge call) |

### Unity (manual, SDK + Bridge required)

| Test | Procedure |
|------|-----------|
| dry-run: not logged in | Logout → `confirm=False` → `VRCSDK_NOT_LOGGED_IN` |
| dry-run: valid | Login + valid asset → `confirm=False` → `phase: "validated"` |
| confirm: avatar upload | Full build + upload → `phase: "complete"` + `elapsed_sec` |
| SDK absent | No VRC SDK project → `VRCSDK_NOT_AVAILABLE` |

### Not Tested

- VRC API mocks (external dependency)
- World upload integration (avatar test covers the same code path)
