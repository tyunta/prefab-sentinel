using System;
using System.Collections.Generic;
using System.Reflection;
using UnityEditor;
using UnityEngine;

namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        /// <summary>
        /// Short-circuit ``editor_add_component`` for UdonSharp behaviours.
        ///
        /// Returns a reuse response when the GameObject already carries the
        /// proxy + matching UdonBehaviour pair. Returns a relink response
        /// when only a stranded proxy is present and a fresh UdonBehaviour
        /// has been linked. Returns null in every other case so the caller
        /// can fall through to the regular ``Undo.AddComponent`` path.
        /// </summary>
        private static EditorControlResponse HandleUdonSharpAddComponentIdempotent(
            GameObject go, Type compType, string hierarchyPath)
        {
            var proxy = go.GetComponent(compType);
            if (proxy == null) return null;

            Type editorUtilType = ResolveUdonSharpEditorUtilityType();
            if (editorUtilType == null) return null;

            MethodInfo getBacking = editorUtilType.GetMethod(
                "GetBackingUdonBehaviour",
                BindingFlags.Public | BindingFlags.Static
            );
            if (getBacking == null) return null;

            object backing;
            try
            {
                backing = getBacking.Invoke(null, new object[] { proxy });
            }
            catch (Exception ex)
            {
                // Issue #137: intentional best-effort fallback. The
                // reflective call into the optional UdonSharp editor
                // utility may throw when the assembly version is older
                // than expected; return ``null`` so the caller falls
                // through to the regular ``Undo.AddComponent`` path.
                Debug.LogWarning($"[PrefabSentinel] HandleUdonSharpAddComponentIdempotent: {ex.GetType().Name}: {ex.Message}");
                return null;
            }

            if (backing != null)
            {
                return BuildSuccess(
                    "EDITOR_CTRL_ADD_COMPONENT_REUSED",
                    $"Existing UdonSharp pair reused for {compType.Name}",
                    new EditorControlData
                    {
                        selected_object = go.name,
                        asset_path = compType.FullName,
                        executed = false,
                        read_only = false,
                    });
            }

            // Stranded proxy: link a freshly created UdonBehaviour to it.
            MethodInfo createForProxy = editorUtilType.GetMethod(
                "CreateBehaviourForProxy",
                BindingFlags.Public | BindingFlags.Static
            );
            if (createForProxy == null) return null;

            try
            {
                createForProxy.Invoke(null, new object[] { proxy });
            }
            catch (Exception ex)
            {
                // Issue #137: intentional best-effort fallback. The
                // reflective call into ``CreateBehaviourForProxy`` may
                // throw when the optional UdonSharp editor utility's
                // signature drifts; return ``null`` so the caller falls
                // through to the regular ``Undo.AddComponent`` path.
                Debug.LogWarning($"[PrefabSentinel] HandleUdonSharpAddComponentIdempotent: {ex.GetType().Name}: {ex.Message}");
                return null;
            }

            return BuildSuccess(
                "EDITOR_CTRL_ADD_COMPONENT_RELINKED",
                $"Existing proxy re-linked to new UdonBehaviour for {compType.Name}",
                new EditorControlData
                {
                    selected_object = go.name,
                    asset_path = compType.FullName,
                    executed = true,
                    read_only = false,
                });
        }

        private static EditorControlResponse HandleEditorAddComponent(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_ADD_COMP_NO_PATH", "hierarchy_path is required.");
            if (string.IsNullOrEmpty(request.component_type))
                return BuildError("EDITOR_CTRL_ADD_COMP_NO_TYPE", "component_type is required.");

            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_ADD_COMP_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            System.Type compType = ResolveComponentType(request.component_type);
            if (compType == null)
                return BuildError("EDITOR_CTRL_ADD_COMP_TYPE_NOT_FOUND",
                    $"Component type not found: {request.component_type}. " +
                    "Short names (e.g. 'BoxCollider') and fully qualified names both work.");

            // Idempotency guard for UdonSharpBehaviour subclasses (issue #103).
            // Adding the same UdonSharp class twice via the public AddComponent
            // path otherwise produces a second proxy MonoBehaviour without a
            // matching UdonBehaviour, leaving the GameObject with mismatched
            // pairs. Short-circuit to a reuse / relink response when an
            // existing proxy is detected.
            Type usbTypeForGuard = ResolveUdonSharpBehaviourType();
            if (usbTypeForGuard != null && usbTypeForGuard.IsAssignableFrom(compType))
            {
                EditorControlResponse idempotent =
                    HandleUdonSharpAddComponentIdempotent(go, compType, request.hierarchy_path);
                if (idempotent != null) return idempotent;
            }

            var added = Undo.AddComponent(go, compType);
            if (added == null)
                return BuildError("EDITOR_CTRL_ADD_COMP_FAILED",
                    $"Failed to add component: {request.component_type}");

            // Apply initial properties if provided
            var diagList = new List<EditorControlDiagnostic>();
            if (!string.IsNullOrEmpty(request.properties_json))
            {
                try
                {
                    var propWrapper = JsonUtility.FromJson<PropertyEntryArray>(
                        "{\"items\":" + request.properties_json + "}");
                    if (propWrapper.items != null)
                    {
                        var so = new SerializedObject(added);
                        foreach (var entry in propWrapper.items)
                        {
                            var prop = so.FindProperty(entry.name);
                            if (prop == null) continue;
                            if (!string.IsNullOrEmpty(entry.object_reference))
                            {
                                var (obj, _) = ResolveObjectReference(entry.object_reference);
                                if (obj != null) prop.objectReferenceValue = obj;
                            }
                            else if (!string.IsNullOrEmpty(entry.value))
                            {
                                ApplyPropertyValue(prop, entry.value);
                            }
                        }
                        so.ApplyModifiedProperties();
                    }
                }
                catch (System.Exception ex)
                {
                    diagList.Add(new EditorControlDiagnostic
                    {
                        detail = $"Failed to apply initial properties: {ex.Message}",
                        evidence = "properties_json"
                    });
                }
            }

            // Check if the added type is UdonSharpBehaviour without a matching ProgramAsset.
            // ``usbTypeForGuard`` was already resolved above via ``ResolveUdonSharpBehaviourType``;
            // reuse it instead of walking ``AppDomain.CurrentDomain.GetAssemblies()`` a second time.
            Type usbType = usbTypeForGuard;

            bool udonProgramAssetMissing = false;
            if (usbType != null && usbType.IsAssignableFrom(compType))
            {
                udonProgramAssetMissing = true;
                Type programAssetType = null;
                foreach (System.Reflection.Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
                {
                    programAssetType = assembly.GetType("UdonSharp.UdonSharpProgramAsset", false);
                    if (programAssetType != null) break;
                }

                if (programAssetType != null)
                {
                    MethodInfo getAllPrograms = programAssetType.GetMethod(
                        "GetAllUdonSharpPrograms",
                        BindingFlags.Public | BindingFlags.Static
                    );
                    if (getAllPrograms != null)
                    {
                        Array programs = getAllPrograms.Invoke(null, null) as Array;
                        if (programs != null)
                        {
                            PropertyInfo csScriptProp = programAssetType.GetProperty(
                                "sourceCsScript",
                                BindingFlags.Public | BindingFlags.Instance
                            );
                            foreach (object program in programs)
                            {
                                if (csScriptProp == null) continue;
                                MonoScript script = csScriptProp.GetValue(program) as MonoScript;
                                if (script != null && script.GetClass() == compType)
                                {
                                    udonProgramAssetMissing = false;
                                    break;
                                }
                            }
                        }
                    }
                }
            }

            var resp = BuildSuccess("EDITOR_CTRL_ADD_COMP_OK",
                $"Added {compType.FullName} to {request.hierarchy_path}",
                data: new EditorControlData
                {
                    selected_object = go.name,
                    asset_path = compType.FullName,
                    executed = true,
                    read_only = false,
                });
            diagList.Add(new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.AddComponent"
            });
            if (udonProgramAssetMissing)
            {
                diagList.Add(new EditorControlDiagnostic
                {
                    path = request.hierarchy_path,
                    detail = $"UdonSharpProgramAsset not found for {compType.Name}. The component was added as a regular MonoBehaviour, not UdonBehaviour. Run editor_create_udon_program_asset first, then retry.",
                    evidence = "UdonSharp.UdonSharpProgramAsset.GetAllUdonSharpPrograms"
                });
            }
            resp.diagnostics = diagList.ToArray();
            return resp;
        }

        private static EditorControlResponse HandleEditorRemoveComponent(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_REM_COMP_NO_PATH", "hierarchy_path is required.");
            if (string.IsNullOrEmpty(request.component_type))
                return BuildError("EDITOR_CTRL_REM_COMP_NO_TYPE", "component_type is required.");

            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_REM_COMP_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            System.Type compType = ResolveComponentType(request.component_type);
            if (compType == null)
                return BuildError("EDITOR_CTRL_REM_COMP_TYPE_NOT_FOUND",
                    $"Component type not found: {request.component_type}. " +
                    "Short names (e.g. 'BoxCollider') and fully qualified names both work.");

            var components = go.GetComponents(compType);
            if (components.Length == 0)
                return BuildError("EDITOR_CTRL_REM_COMP_NONE",
                    $"No {request.component_type} component found on {request.hierarchy_path}");

            Component target;
            if (request.component_index == -1)
            {
                if (components.Length == 1)
                {
                    target = components[0];
                }
                else
                {
                    return BuildError("EDITOR_CTRL_REM_COMP_AMBIGUOUS",
                        $"Found {components.Length} {request.component_type} components on {request.hierarchy_path}. " +
                        $"Specify index (0-{components.Length - 1}) to select.",
                        new EditorControlData { component_count = components.Length });
                }
            }
            else
            {
                if (request.component_index < 0 || request.component_index >= components.Length)
                    return BuildError("EDITOR_CTRL_REM_COMP_INDEX_OUT_OF_RANGE",
                        $"index {request.component_index} out of range. " +
                        $"{request.hierarchy_path} has {components.Length} {request.component_type} component(s) " +
                        $"(valid: 0-{components.Length - 1}).",
                        new EditorControlData { component_count = components.Length });
                target = components[request.component_index];
            }

            if (target is Transform)
                return BuildError("EDITOR_CTRL_REM_COMP_IS_TRANSFORM",
                    "Cannot remove Transform — it is a required component.");

            Undo.DestroyObjectImmediate(target);

            var resp = BuildSuccess("EDITOR_CTRL_REM_COMP_OK",
                $"Removed {compType.FullName} from {request.hierarchy_path}",
                data: new EditorControlData
                {
                    selected_object = go.name,
                    asset_path = compType.FullName,
                    executed = true,
                    read_only = false,
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.DestroyObjectImmediate"
            }};
            return resp;
        }

        private static EditorControlResponse HandleEditorBatchAddComponent(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.batch_operations_json))
                return BuildError("EDITOR_CTRL_BATCH_ADD_COMP_NO_DATA",
                    "batch_operations_json is required.");

            BatchAddComponentArray wrapper;
            try
            {
                wrapper = JsonUtility.FromJson<BatchAddComponentArray>(
                    "{\"items\":" + request.batch_operations_json + "}");
            }
            catch (System.Exception ex)
            {
                return BuildError("EDITOR_CTRL_BATCH_ADD_COMP_JSON_ERROR",
                    $"Failed to parse batch_operations_json: {ex.Message}");
            }

            if (wrapper.items == null || wrapper.items.Length == 0)
                return BuildError("EDITOR_CTRL_BATCH_ADD_COMP_EMPTY",
                    "batch_operations_json is empty.");

            int undoGroup = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("PrefabSentinel: Batch AddComponent");

            var results = new List<string>();

            foreach (var op in wrapper.items)
            {
                var subReq = new EditorControlRequest
                {
                    action = "editor_add_component",
                    hierarchy_path = op.hierarchy_path,
                    component_type = op.component_type,
                    properties_json = op.properties_json,
                };
                var subResp = HandleEditorAddComponent(subReq);
                if (!subResp.success)
                {
                    Undo.CollapseUndoOperations(undoGroup);
                    return BuildError("EDITOR_CTRL_BATCH_ADD_COMP_FAILED",
                        $"Operation failed at index {results.Count}: {subResp.message}");
                }
                results.Add($"{op.hierarchy_path}: {op.component_type}");
            }

            Undo.CollapseUndoOperations(undoGroup);

            var resp = BuildSuccess("EDITOR_CTRL_BATCH_ADD_COMP_OK",
                $"Added {results.Count} components",
                data: new EditorControlData
                {
                    executed = true,
                    read_only = false,
                    suggestions = results.ToArray(),
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.CollapseUndoOperations"
            }};
            return resp;
        }
    }
}
