# ClientSim lifecycle and profile addressability design

## Context

PR #165 adds open-composition verification and semantic inspector profiles. Final review found two release blockers:

1. `run_clientsim` creates an edit-mode `GameObject` runner, then opens the target scene in `Single` mode. The scene change can destroy the runner before it writes a response or performs cleanup. The dispatcher also treats this operation as synchronous, so the caller can return `EDITOR_BRIDGE_NO_RESPONSE` while ClientSim objects remain behind.
2. `inspect_with_profile` validates writable declarations with an unconditional addressability result. It can therefore advertise `writable.enabled=true` without proving that the current asset, local fileID, and writer operation are addressable.

The ClientSim design follows VRChat's documented lifecycle: enter Play Mode and let `ClientSimRuntimeLoader` initialize ClientSim. The requested scene must already be the only loaded, active scene. The controller temporarily clears Unity's `EditorSceneManager.playModeStartScene` so Play Mode uses that exact in-memory scene, including explicitly allowed unsaved changes, without opening any scene in Edit Mode.

## Goals

- Never create a scene-bound runner in Edit Mode.
- Use ClientSim's public Play Mode lifecycle instead of reflecting its private initialization coroutine.
- Return the bridge response only after Play Mode has exited and editor settings have been restored.
- Preserve the existing dirty-scene rejection and no-autosave contracts.
- Revalidate writable profile operations against the current target on every inspection.

## Non-goals

- Do not emulate ClientSim when the package is absent or disabled.
- Do not add fallback scene discovery, automatic saving, or alternate runtime initialization paths.
- Do not persist writer-validation receipts across calls.
- Do not run ClientSim again in the user's working Unity project as part of this repair.

## ClientSim state machine

`run_clientsim` becomes an editor-owned asynchronous action. A static, reload-safe controller owns the operation; no `MonoBehaviour` or temporary `GameObject` participates.

The controller stores the full request and operation record in `SessionState`: response path, target scene, phase, absolute deadlines, and the pre-run snapshot. A separate restoration lease stores the prior `playModeStartScene` GUID (or an explicit null marker), target GUID, and response path. The independent lease permits restoration even if the main record is corrupt. Both records survive the assembly reload that normally accompanies entering Play Mode but are scoped to the current editor session.

State transitions:

1. `Begin`
   - Reject concurrent operations, an already-playing editor, a missing/non-scene target, a target that is not the only loaded active scene, and disallowed dirty scenes.
   - Fix the absolute operation deadline before snapshot/preflight work. If preflight consumes it, return a typed timeout before acquiring a restoration lease or entering Play Mode.
   - Preflight the ClientSim package, its enabled setting, and the required public readiness APIs before entering Play Mode.
   - Capture the pre-run snapshot and previous `playModeStartScene`. Dirty-asset observation considers already loaded persistent dirty objects only and never loads every project asset.
   - Persist the restoration lease before changing editor state, persist the full operation, set `playModeStartScene` to null, and call `EditorApplication.EnterPlaymode()`.
2. `EnteringPlayMode`
   - Wait for `PlayModeStateChange.EnteredPlayMode` until the enter deadline.
3. `WaitingForClientSim`
   - Let the official RuntimeLoader create ClientSim.
   - Poll only public ClientSim state until its instance exists and reports network-ready, or until the ready deadline.
   - Capture the runtime snapshot and request `EditorApplication.ExitPlaymode()`.
4. `ExitingPlayMode`
   - Wait for `PlayModeStateChange.EnteredEditMode` until the exit deadline.
5. `Finish`
   - Restore the previous `playModeStartScene` before writing the response.
   - Capture the post-run editor snapshot and report before/runtime/after side effects, distinguishing runtime-created objects from residual edit-mode changes. Snapshot differences are multiset differences so duplicate-name siblings and repeated component types cannot collapse; dirty-asset candidates use a symmetric before/after difference so clean/unloaded transitions are not lost.
   - Persist the terminal result, publish the response with a strict atomic temp-file move, and clear the operation/restoration lease only from the producer's success result. There is no direct-write fallback on this reload-owned path. Restoration failure retains the lease and terminal evidence for retry and publishes no response. A reload that sees an existing response clears state without writing a duplicate response; consumer deletion after publication cannot retain or duplicate state.

Every failure after `Begin` converges on the same exit-and-restore path. Enter/readiness share one operation deadline; exit has a bounded cleanup deadline and a distinct error code. The Python transport deadline is longer than the operation deadline by the cleanup grace plus a dispatch margin, so it cannot abandon a valid cleanup response. A stale persisted operation discovered after assembly reload reconciles its recorded phase against the editor's actual playing state; invalid state requests Play Mode exit, restores from the independent lease, and fails closed. Readiness ignores persistent Resources prefab objects returned by `Resources.FindObjectsOfTypeAll` and accepts only one non-persistent component in a valid loaded scene.

The bridge dispatcher includes runtime-validation asynchronous actions in the same pending-response contract already used by editor-control asynchronous actions. It must not emit a synchronous no-response error for `run_clientsim`.

## Inspector profile addressability

`inspect_with_profile` passes the existing call-local `_addressability_checker` to whole-profile validation instead of an unconditional callback. Unity's serialized-surface payload includes the actual inspected target's local fileID from `AssetDatabase.TryGetGUIDAndLocalFileIdentifier`. That identity is required for every writable probe; the probe then follows the existing resource grammar: exact `file_id` for Prefab components, `$asset` for the already-root-addressed ScriptableObject `.asset`, and fail-closed for Scene components because the current writer cannot bind a local fileID to an exact component handle.

- Profiles without writable declarations do not invoke the writer probe.
- Each declared writable operation uses the current surface identity and performs the existing `serialized_value_patch_apply(..., dry_run=True, confirm=False)` probe with the real resource-kind address grammar.
- All probes succeeding permits the declared writable capability.
- Resolution, operation construction, or dry-run rejection returns `INSPECTOR_PROFILE_INVALID` with the existing addressability diagnostic.
- No write is performed and no cross-call cache or receipt is introduced.

This keeps validation and inspection governed by the same invariant: `writable=true` means the current writer has successfully accepted an exact dry-run address for the current target.

## Verification

Tests are added before implementation and must initially fail for the observed behavior.

- Source-contract tests prohibit the edit-mode runner, private ClientSim initialization, and scene replacement path; require a reload-safe static lifecycle; and require runtime actions to use asynchronous dispatch.
- State-transition tests pin enter, ready, exit, cleanup, timeout, and single-flight behavior where the existing harness can exercise it without launching ClientSim. Python contract tests require `executed: bool`, fail closed on missing or malformed executed reports, and distinguish a runtime-only missing snapshot (trusted before/after residuals remain reportable) from a missing before/after snapshot (derived cleanup claims are suppressed).
- Inspector tests require a successful dry-run before `writable=true`, reject an unaddressable target, and prove read-only profiles do not dispatch a writer probe.
- Documentation is synchronized with the new lifecycle and call-time addressability contract.
- Full Python, C#, packaging, performance, and bridge-constant gates run.
- The updated bridge is deployed only for Unity compilation/console verification. ClientSim is not started in the user's project.

## Sources

- [VRChat ClientSim](https://creators.vrchat.com/worlds/clientsim/)
- [VRChat ClientSim Runtime Loader](https://creators.vrchat.com/worlds/clientsim/systems/runtime/runtime-loader/)
- [Unity `EditorSceneManager.playModeStartScene`](https://docs.unity3d.com/2022.3/Documentation/ScriptReference/SceneManagement.EditorSceneManager-playModeStartScene.html)
- [Unity `EditorApplication.playModeStateChanged`](https://docs.unity3d.com/2022.3/Documentation/ScriptReference/EditorApplication-playModeStateChanged.html)
- [Unity `SessionState`](https://docs.unity3d.com/2022.3/Documentation/ScriptReference/SessionState.html)
