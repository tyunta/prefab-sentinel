using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;
namespace PrefabSentinel
{
    public static partial class UnityPatchBridge
    {
        private static BridgeResponse ApplyPrefabOperations(BridgeRequest request, string assetPath)
        {
            int applied = 0;
            List<BridgeDiagnostic> diagnostics = new List<BridgeDiagnostic>();
            GameObject prefabRoot = null;
            try
            {
                prefabRoot = PrefabUtility.LoadPrefabContents(assetPath);
                if (prefabRoot == null)
                {
                    return BuildError(
                        "UNITY_BRIDGE_PREFAB_LOAD",
                        "Failed to load prefab contents.",
                        request.target,
                        request.ops.Length,
                        executed: true
                    );
                }

                for (int i = 0; i < request.ops.Length; i++)
                {
                    PatchOp op = request.ops[i];
                    if (!TryApplyOp(prefabRoot, request.target, op, i, diagnostics))
                    {
                        // Issue #298: the SerializedObject apply
                        // rejection path now emits a structured
                        // envelope carrying property_path,
                        // component_type, and attempted_value so known
                        // traps (such as AudioSource.m_Priority) are
                        // diagnosable from the response alone. The
                        // pre-existing exception path is unchanged and
                        // retains its UNITY_BRIDGE_APPLY_EXCEPTION
                        // code for unexpected exceptions outside the
                        // rejection contract.
                        BridgeDiagnostic[] failureDiagnostics = BuildPrefabApplyRejectionDiagnostics(
                            request.target,
                            op,
                            i,
                            diagnostics
                        );
                        return BuildError(
                            "SER_APPLY_REJECTED",
                            "SerializedObject apply rejected by Unity.",
                            request.target,
                            request.ops.Length,
                            executed: true,
                            applied: applied,
                            diagnostics: failureDiagnostics
                        );
                    }
                    applied += 1;
                }

                PrefabUtility.SaveAsPrefabAsset(prefabRoot, assetPath);
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                return new BridgeResponse
                {
                    protocol_version = ProtocolVersion,
                    success = true,
                    severity = "info",
                    code = "SER_APPLY_OK",
                    message = "SerializedObject patch applied via Unity executeMethod.",
                    data = new BridgeData
                    {
                        target = request.target,
                        op_count = request.ops.Length,
                        applied = applied,
                        read_only = false,
                        executed = true,
                        protocol_version = ProtocolVersion
                    },
                    diagnostics = Array.Empty<BridgeDiagnostic>()
                };
            }
            catch (Exception ex)
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = request.target,
                        location = "apply",
                        detail = "exception",
                        evidence = ex.ToString()
                    }
                );
                return BuildError(
                    "UNITY_BRIDGE_APPLY_EXCEPTION",
                    $"Unexpected apply exception: {ex.Message}",
                    request.target,
                    request.ops.Length,
                    executed: true,
                    applied: applied,
                    diagnostics: diagnostics.ToArray()
                );
            }
            finally
            {
                if (prefabRoot != null)
                {
                    PrefabUtility.UnloadPrefabContents(prefabRoot);
                }
            }
        }
        private static BridgeResponse ApplyPrefabCreateOperations(BridgeRequest request, string assetPath)
        {
            int applied = 0;
            List<BridgeDiagnostic> diagnostics = new List<BridgeDiagnostic>();
            GameObject prefabRoot = null;
            bool saved = false;
            Dictionary<string, UnityEngine.Object> handles = new Dictionary<string, UnityEngine.Object>(StringComparer.Ordinal);
            try
            {
                if (File.Exists(Path.Combine(Path.GetFullPath(Path.Combine(Application.dataPath, "..")), assetPath)))
                {
                    return BuildError(
                        "UNITY_BRIDGE_TARGET_EXISTS",
                        "target file already exists.",
                        request.target,
                        request.ops.Length,
                        executed: false
                    );
                }

                string fullAssetPath = Path.Combine(
                    Path.GetFullPath(Path.Combine(Application.dataPath, "..")),
                    assetPath
                );
                string parentDir = Path.GetDirectoryName(fullAssetPath);
                if (string.IsNullOrWhiteSpace(parentDir) || !Directory.Exists(parentDir))
                {
                    return BuildError(
                        "UNITY_BRIDGE_TARGET_PATH",
                        "target directory was not found.",
                        request.target,
                        request.ops.Length,
                        executed: false
                    );
                }

                for (int i = 0; i < request.ops.Length; i++)
                {
                    PatchOp op = request.ops[i];
                    string opName = (op?.op ?? string.Empty).Trim();
                    if (
                        string.Equals(opName, "create_prefab", StringComparison.Ordinal)
                        || string.Equals(opName, "create_root", StringComparison.Ordinal)
                    )
                    {
                        if (prefabRoot != null)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "schema_error",
                                    evidence = "prefab root may be created only once"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }

                        string rootName;
                        if (string.Equals(opName, "create_root", StringComparison.Ordinal))
                        {
                            if (string.IsNullOrWhiteSpace(op.name))
                            {
                                diagnostics.Add(
                                    new BridgeDiagnostic
                                    {
                                        path = request.target,
                                        location = $"ops[{i}].name",
                                        detail = "schema_error",
                                        evidence = "create_root requires name"
                                    }
                                );
                                return BuildError(
                                    "UNITY_BRIDGE_SCHEMA",
                                    "Invalid prefab create plan.",
                                    request.target,
                                    request.ops.Length,
                                    executed: false,
                                    applied: applied,
                                    diagnostics: diagnostics.ToArray()
                                );
                            }
                            rootName = op.name.Trim();
                        }
                        else
                        {
                            rootName = string.IsNullOrWhiteSpace(op.name)
                                ? Path.GetFileNameWithoutExtension(assetPath)
                                : op.name.Trim();
                        }
                        prefabRoot = new GameObject(rootName);
                        handles["root"] = prefabRoot;
                        if (!TryRegisterHandle(op.result, prefabRoot, handles, request.target, i, diagnostics))
                        {
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        applied += 1;
                        continue;
                    }

                    if (string.Equals(opName, "create_game_object", StringComparison.Ordinal))
                    {
                        if (prefabRoot == null)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "schema_error",
                                    evidence = "create_game_object requires a prefab root first"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (string.IsNullOrWhiteSpace(op.name))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].name",
                                    detail = "schema_error",
                                    evidence = "create_game_object requires name"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        GameObject parentObject;
                        string handleError;
                        if (!TryResolveGameObjectHandle(op.parent, handles, out parentObject, out handleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].parent",
                                    detail = "schema_error",
                                    evidence = handleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        GameObject child = new GameObject(op.name.Trim());
                        child.transform.SetParent(parentObject.transform, false);
                        if (!TryRegisterHandle(op.result, child, handles, request.target, i, diagnostics))
                        {
                            UnityEngine.Object.DestroyImmediate(child);
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        applied += 1;
                        continue;
                    }

                    if (string.Equals(opName, "add_component", StringComparison.Ordinal))
                    {
                        GameObject targetObject;
                        string targetHandleError;
                        if (!TryResolveGameObjectHandle(op.target, handles, out targetObject, out targetHandleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "schema_error",
                                    evidence = targetHandleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        Type componentType;
                        string typeError;
                        if (!TryResolveComponentType(op.type, out componentType, out typeError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].type",
                                    detail = "apply_error",
                                    evidence = typeError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to add component.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (componentType.IsAbstract || componentType.ContainsGenericParameters)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].type",
                                    detail = "apply_error",
                                    evidence = $"component type '{componentType.FullName ?? componentType.Name}' cannot be instantiated"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to add component.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (componentType == typeof(Transform))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].type",
                                    detail = "apply_error",
                                    evidence = "Transform is implicit and cannot be added"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to add component.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }

                        Component addedComponent = targetObject.AddComponent(componentType);
                        if (addedComponent == null)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}]",
                                    detail = "apply_error",
                                    evidence = $"AddComponent returned null for '{componentType.FullName ?? componentType.Name}'"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to add component.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        string resultHandle = NormalizeHandle(op.result);
                        if (!TrySetupUdonSharpBacking(
                            targetObject, addedComponent, componentType, handles,
                            resultHandle, request.target, i, diagnostics))
                        {
                            UnityEngine.Object.DestroyImmediate(addedComponent);
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to setup UdonSharp backing.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (!TryRegisterHandle(op.result, addedComponent, handles, request.target, i, diagnostics))
                        {
                            UnityEngine.Object.DestroyImmediate(addedComponent);
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        applied += 1;
                        continue;
                    }

                    if (string.Equals(opName, "find_component", StringComparison.Ordinal))
                    {
                        GameObject targetObject;
                        string targetHandleError;
                        if (!TryResolveGameObjectHandle(op.target, handles, out targetObject, out targetHandleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "schema_error",
                                    evidence = targetHandleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        Component foundComponent;
                        string componentError;
                        if (!TryFindUniqueComponentOnObject(targetObject, op.type, out foundComponent, out componentError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].type",
                                    detail = "apply_error",
                                    evidence = componentError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to resolve component.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (!TryRegisterHandle(op.result, foundComponent, handles, request.target, i, diagnostics))
                        {
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        applied += 1;
                        continue;
                    }

                    if (string.Equals(opName, "remove_component", StringComparison.Ordinal))
                    {
                        Component targetComponent;
                        string componentHandleError;
                        if (!TryResolveComponentHandle(op.target, handles, out targetComponent, out componentHandleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "schema_error",
                                    evidence = componentHandleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (targetComponent is Transform)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "apply_error",
                                    evidence = "Transform cannot be removed"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to remove component.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        UnityEngine.Object.DestroyImmediate(targetComponent);
                        applied += 1;
                        continue;
                    }

                    if (
                        string.Equals(opName, "set", StringComparison.Ordinal)
                        || string.Equals(opName, "insert_array_element", StringComparison.Ordinal)
                        || string.Equals(opName, "remove_array_element", StringComparison.Ordinal)
                    )
                    {
                        Component targetComponent;
                        string componentHandleError;
                        if (!TryResolveComponentHandle(op.target, handles, out targetComponent, out componentHandleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "schema_error",
                                    evidence = componentHandleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        s_currentHandles = handles;
                        try
                        {
                            if (!TryApplyMutationOpToComponent(targetComponent, request.target, op, i, diagnostics))
                            {
                                return BuildError(
                                    "UNITY_BRIDGE_APPLY",
                                    "Failed to apply component mutation.",
                                    request.target,
                                    request.ops.Length,
                                    executed: true,
                                    applied: applied,
                                    diagnostics: diagnostics.ToArray()
                                );
                            }
                        }
                        finally
                        {
                            s_currentHandles = null;
                        }
                        applied += 1;
                        continue;
                    }

                    if (string.Equals(opName, "rename_object", StringComparison.Ordinal))
                    {
                        GameObject targetObject;
                        string handleError;
                        if (!TryResolveGameObjectHandle(op.target, handles, out targetObject, out handleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "schema_error",
                                    evidence = handleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (string.IsNullOrWhiteSpace(op.name))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].name",
                                    detail = "schema_error",
                                    evidence = "rename_object requires name"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        targetObject.name = op.name.Trim();
                        applied += 1;
                        continue;
                    }

                    if (string.Equals(opName, "reparent", StringComparison.Ordinal))
                    {
                        GameObject targetObject;
                        string targetHandleError;
                        if (!TryResolveGameObjectHandle(op.target, handles, out targetObject, out targetHandleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "schema_error",
                                    evidence = targetHandleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        GameObject parentObject;
                        string parentHandleError;
                        if (!TryResolveGameObjectHandle(op.parent, handles, out parentObject, out parentHandleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].parent",
                                    detail = "schema_error",
                                    evidence = parentHandleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (ReferenceEquals(targetObject, prefabRoot))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "schema_error",
                                    evidence = "root handle cannot be reparented"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (ReferenceEquals(targetObject, parentObject))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}]",
                                    detail = "schema_error",
                                    evidence = "target and parent handles must differ"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        targetObject.transform.SetParent(parentObject.transform, false);
                        applied += 1;
                        continue;
                    }

                    if (string.Equals(opName, "save", StringComparison.Ordinal))
                    {
                        if (prefabRoot == null)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "schema_error",
                                    evidence = "save requires a prefab root first"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (i != request.ops.Length - 1)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "schema_error",
                                    evidence = "save must be the final operation in create mode"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }

                        GameObject savedPrefab = PrefabUtility.SaveAsPrefabAsset(prefabRoot, assetPath);
                        if (savedPrefab == null)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "apply_error",
                                    evidence = "PrefabUtility.SaveAsPrefabAsset returned null"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to save prefab asset.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        AssetDatabase.SaveAssets();
                        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                        saved = true;
                        applied += 1;
                        continue;
                    }

                    diagnostics.Add(
                        new BridgeDiagnostic
                        {
                            path = request.target,
                            location = $"ops[{i}].op",
                            detail = "schema_error",
                            evidence = $"unsupported prefab create op '{opName}'"
                        }
                    );
                    return BuildError(
                        "UNITY_BRIDGE_SCHEMA",
                        "Invalid prefab create plan.",
                        request.target,
                        request.ops.Length,
                        executed: false,
                        applied: applied,
                        diagnostics: diagnostics.ToArray()
                    );
                }

                if (prefabRoot == null || !saved)
                {
                    return BuildError(
                        "UNITY_BRIDGE_SCHEMA",
                        "Prefab create mode requires a root creation operation and save.",
                        request.target,
                        request.ops.Length,
                        executed: false,
                        applied: applied
                    );
                }

                return new BridgeResponse
                {
                    protocol_version = ProtocolVersion,
                    success = true,
                    severity = "info",
                    code = "SER_APPLY_OK",
                    message = "Prefab create plan applied via Unity executeMethod.",
                    data = new BridgeData
                    {
                        target = request.target,
                        op_count = request.ops.Length,
                        applied = applied,
                        read_only = false,
                        executed = true,
                        protocol_version = ProtocolVersion
                    },
                    diagnostics = Array.Empty<BridgeDiagnostic>()
                };
            }
            catch (Exception ex)
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = request.target,
                        location = "apply",
                        detail = "exception",
                        evidence = ex.ToString()
                    }
                );
                return BuildError(
                    "UNITY_BRIDGE_APPLY_EXCEPTION",
                    $"Unexpected apply exception: {ex.Message}",
                    request.target,
                    request.ops.Length,
                    executed: true,
                    applied: applied,
                    diagnostics: diagnostics.ToArray()
                );
            }
            finally
            {
                if (prefabRoot != null)
                {
                    UnityEngine.Object.DestroyImmediate(prefabRoot);
                }
            }
        }

        /// <summary>
        /// Issue #298: enrich the apply-rejection diagnostics list with
        /// structured fields (``property_path``, ``component_type``,
        /// ``attempted_value``) so callers can diagnose known traps
        /// without re-reading the request payload. The existing
        /// per-failure ``BridgeDiagnostic`` entries collected by
        /// ``TryApplyOp`` are preserved verbatim; this helper appends a
        /// summary entry whose payload carries the structured fields.
        /// </summary>
        private static BridgeDiagnostic[] BuildPrefabApplyRejectionDiagnostics(
            string target,
            PatchOp op,
            int opIndex,
            List<BridgeDiagnostic> innerDiagnostics
        )
        {
            string propertyPath = op != null && !string.IsNullOrEmpty(op.path) ? op.path : string.Empty;
            string componentType = op != null && !string.IsNullOrEmpty(op.component) ? op.component : string.Empty;
            string attemptedValue = SummarizePatchOpValue(op);
            // Issue #298 / H-11: the apply-rejected code and the evidence
            // string conveying the three diagnostic values are assembled by
            // the Unity-free ``PrefabApplyRejectionEnvelope``.
            PrefabApplyRejection rejection = PrefabApplyRejectionEnvelope.Build(
                new PrefabApplyFailure(propertyPath, componentType, attemptedValue));
            BridgeDiagnostic summary = new BridgeDiagnostic
            {
                path = target,
                location = string.Format("ops[{0}]", opIndex),
                detail = rejection.Code,
                evidence = rejection.Evidence
            };
            List<BridgeDiagnostic> combined = new List<BridgeDiagnostic>(innerDiagnostics);
            combined.Add(summary);
            return combined.ToArray();
        }

        /// <summary>
        /// Issue #298: render the attempted ``PatchOp`` value as a
        /// short textual summary so the rejection envelope's payload
        /// can carry the value without re-serialising the entire op.
        /// Unknown value-kinds fall back to the literal ``value_kind``
        /// token so the field remains non-empty.
        /// </summary>
        private static string SummarizePatchOpValue(PatchOp op)
        {
            if (op == null) return string.Empty;
            string kind = op.value_kind ?? string.Empty;
            switch (kind)
            {
                case "int":
                    return op.value_int.ToString(System.Globalization.CultureInfo.InvariantCulture);
                case "float":
                    return op.value_float.ToString(System.Globalization.CultureInfo.InvariantCulture);
                case "bool":
                    return op.value_bool ? "true" : "false";
                case "string":
                    return op.value_string ?? string.Empty;
                case "handle":
                    return op.value_string ?? string.Empty;
                case "json":
                    return op.value_json ?? string.Empty;
                case "null":
                    return "null";
                default:
                    return kind;
            }
        }
    }
}
