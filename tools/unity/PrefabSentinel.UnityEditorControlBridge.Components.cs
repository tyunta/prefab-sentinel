using System;
using System.Collections.Generic;
using System.Reflection;
using UnityEditor;
using UnityEngine;

namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {

        private static EditorControlResponse HandleEditorAddComponent(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_ADD_COMP_NO_PATH", "hierarchy_path is required.");
            if (string.IsNullOrEmpty(request.component_type))
                return BuildError("EDITOR_CTRL_ADD_COMP_NO_TYPE", "component_type is required.");

            if (!TryResolveGameObjectInActiveStage(
                    request.hierarchy_path, out GameObject go, out var ambiguity))
                return ambiguity ?? BuildError("EDITOR_CTRL_ADD_COMP_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            System.Type compType = ResolveComponentType(request.component_type);
            if (compType == null)
                return BuildError("EDITOR_CTRL_ADD_COMP_TYPE_NOT_FOUND",
                    $"Component type not found: {request.component_type}. " +
                    "Short names (e.g. 'BoxCollider') and fully qualified names both work.");

            Type usbTypeForGuard = ResolveUdonSharpBehaviourType();
            bool isUdonSharpComponent = usbTypeForGuard != null
                && usbTypeForGuard.IsAssignableFrom(compType);
            Component added;
            if (isUdonSharpComponent)
            {
                // Existing UdonSharp proxies are either complete pairs to
                // reuse or stranded proxies to repair. The helper returns
                // null only when no proxy exists yet.
                EditorControlResponse idempotent =
                    HandleExistingUdonSharpAddComponent(
                        go, compType, request.hierarchy_path);
                if (idempotent != null) return idempotent;

                EditorControlResponse programAssetErr =
                    CheckUdonProgramAssetReady(compType);
                if (programAssetErr != null) return programAssetErr;

                // Fresh UdonSharp additions must use the setup-aware public
                // entry point so the proxy and backing UdonBehaviour are
                // created as one Undo operation.
                EditorControlResponse createErr =
                    InvokeUdonSharpUndoAddComponent(go, compType, out added);
                if (createErr != null) return createErr;
            }
            else
            {
                added = Undo.AddComponent(go, compType);
            }

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
                            if (prop == null)
                            {
                                diagList.Add(InitialPropertyFailure(request.hierarchy_path, entry.name,
                                    $"Property not found on {compType.Name}: {entry.name}.", "SerializedObject.FindProperty returned null"));
                                continue;
                            }
                            if (!string.IsNullOrEmpty(entry.object_reference))
                            {
                                var (obj, refError) = ResolveObjectReference(entry.object_reference);
                                if (obj != null) prop.objectReferenceValue = obj;
                                else diagList.Add(InitialPropertyFailure(request.hierarchy_path, entry.name,
                                    $"Object reference could not be resolved: {entry.object_reference}.", refError));
                            }
                            else if (!string.IsNullOrEmpty(entry.value))
                            {
                                PropertyWriteResult writeResult = WritePropertyValue(prop, entry.value);
                                if (!writeResult.Success)
                                    diagList.Add(InitialPropertyFailure(request.hierarchy_path, entry.name,
                                        writeResult.ErrorMessage, writeResult.ErrorCode));
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

            // Keep the legacy post-add ProgramAsset diagnostic lookup for
            // UdonSharp additions; type classification was cached above.
            bool udonProgramAssetMissing = false;
            if (isUdonSharpComponent)
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
            // Issue #27: diagList here holds only initial-property / parse failures
            // (runtime-mod note appended below); escalate severity.
            if (diagList.Count > 0) resp.severity = "warning";
            diagList.Add(new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = isUdonSharpComponent
                    ? "UdonSharpEditor.UdonSharpUndo.AddComponent"
                    : "Undo.AddComponent"
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

        // Issue #27: per-entry diagnostic for a failed add-component initial property.
        private static EditorControlDiagnostic InitialPropertyFailure(string hierarchyPath, string entryName, string detail, string evidence) =>
            new EditorControlDiagnostic { path = hierarchyPath, location = $"properties_json[{entryName}]", detail = detail, evidence = evidence };

        private static EditorControlResponse HandleEditorRemoveComponent(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_REM_COMP_NO_PATH", "hierarchy_path is required.");
            if (string.IsNullOrEmpty(request.component_type))
                return BuildError("EDITOR_CTRL_REM_COMP_NO_TYPE", "component_type is required.");

            if (!TryResolveGameObjectInActiveStage(
                    request.hierarchy_path, out GameObject go, out var ambiguity))
                return ambiguity ?? BuildError("EDITOR_CTRL_REM_COMP_NOT_FOUND",
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
