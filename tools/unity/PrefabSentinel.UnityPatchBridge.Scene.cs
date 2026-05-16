using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
namespace PrefabSentinel
{
    public static partial class UnityPatchBridge
    {
        private static BridgeResponse ApplySceneOperations(BridgeRequest request, string assetPath, bool createMode)
        {
            int applied = 0;
            List<BridgeDiagnostic> diagnostics = new List<BridgeDiagnostic>();
            Scene scene = default(Scene);
            bool sceneOpened = false;
            bool saved = false;
            Dictionary<string, UnityEngine.Object> handles = new Dictionary<string, UnityEngine.Object>(StringComparer.Ordinal);
            try
            {
                string fullAssetPath = Path.Combine(
                    Path.GetFullPath(Path.Combine(Application.dataPath, "..")),
                    assetPath
                );
                if (createMode)
                {
                    if (File.Exists(fullAssetPath))
                    {
                        return BuildError(
                            "UNITY_BRIDGE_TARGET_EXISTS",
                            "target file already exists.",
                            request.target,
                            request.ops.Length,
                            executed: false
                        );
                    }
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
                }

                string requiredInitialOp = createMode ? "create_scene" : "open_scene";
                for (int i = 0; i < request.ops.Length; i++)
                {
                    PatchOp op = request.ops[i];
                    string opName = (op?.op ?? string.Empty).Trim();
                    if (i == 0)
                    {
                        if (!string.Equals(opName, requiredInitialOp, StringComparison.Ordinal))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = "ops[0].op",
                                    detail = "schema_error",
                                    evidence = $"scene {(createMode ? "create" : "open")} mode must start with {requiredInitialOp}"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }

                        scene = createMode
                            ? EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single)
                            : EditorSceneManager.OpenScene(assetPath, OpenSceneMode.Single);
                        if (!scene.IsValid() || !scene.isLoaded)
                        {
                            return BuildError(
                                createMode ? "UNITY_BRIDGE_SCENE_CREATE" : "UNITY_BRIDGE_SCENE_OPEN",
                                createMode ? "Failed to create empty scene." : "Failed to open scene.",
                                request.target,
                                request.ops.Length,
                                executed: true
                            );
                        }
                        sceneOpened = true;
                        applied += 1;
                        continue;
                    }

                    if (
                        string.Equals(opName, "create_scene", StringComparison.Ordinal)
                        || string.Equals(opName, "open_scene", StringComparison.Ordinal)
                    )
                    {
                        diagnostics.Add(
                            new BridgeDiagnostic
                            {
                                path = request.target,
                                location = $"ops[{i}].op",
                                detail = "schema_error",
                                evidence = $"{opName} may appear only as the first operation"
                            }
                        );
                        return BuildError(
                            "UNITY_BRIDGE_SCHEMA",
                            "Invalid scene plan.",
                            request.target,
                            request.ops.Length,
                            executed: false,
                            applied: applied,
                            diagnostics: diagnostics.ToArray()
                        );
                    }

                    if (!sceneOpened)
                    {
                        return BuildError(
                            "UNITY_BRIDGE_SCHEMA",
                            "Invalid scene plan.",
                            request.target,
                            request.ops.Length,
                            executed: false,
                            applied: applied
                        );
                    }

                    if (string.Equals(opName, "create_game_object", StringComparison.Ordinal))
                    {
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
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        GameObject parentObject;
                        bool parentIsSceneRoot;
                        string parentHandleError;
                        if (!TryResolveSceneParentHandle(op.parent, handles, out parentObject, out parentIsSceneRoot, out parentHandleError))
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
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }

                        GameObject child = new GameObject(op.name.Trim());
                        SceneManager.MoveGameObjectToScene(child, scene);
                        if (!parentIsSceneRoot)
                        {
                            child.transform.SetParent(parentObject.transform, false);
                        }
                        if (!TryRegisterHandle(op.result, child, handles, request.target, i, diagnostics))
                        {
                            UnityEngine.Object.DestroyImmediate(child);
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid scene plan.",
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

                    if (string.Equals(opName, "instantiate_prefab", StringComparison.Ordinal))
                    {
                        string prefabTarget;
                        string prefabResolveError;
                        if (!TryResolveAssetPath(op.prefab, allowMissing: false, out prefabTarget, out prefabResolveError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].prefab",
                                    detail = "apply_error",
                                    evidence = prefabResolveError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to resolve prefab asset.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        GameObject prefabAsset = AssetDatabase.LoadAssetAtPath<GameObject>(prefabTarget);
                        if (prefabAsset == null)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].prefab",
                                    detail = "apply_error",
                                    evidence = $"prefab asset was not found: '{prefabTarget}'"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to resolve prefab asset.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        GameObject parentObject;
                        bool parentIsSceneRoot;
                        string parentHandleError;
                        if (!TryResolveSceneParentHandle(op.parent, handles, out parentObject, out parentIsSceneRoot, out parentHandleError))
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
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }

                        GameObject instantiated = PrefabUtility.InstantiatePrefab(prefabAsset, scene) as GameObject;
                        if (instantiated == null)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}]",
                                    detail = "apply_error",
                                    evidence = "PrefabUtility.InstantiatePrefab returned null"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to instantiate prefab.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (!parentIsSceneRoot)
                        {
                            instantiated.transform.SetParent(parentObject.transform, false);
                        }
                        if (!TryRegisterHandle(op.result, instantiated, handles, request.target, i, diagnostics))
                        {
                            UnityEngine.Object.DestroyImmediate(instantiated);
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid scene plan.",
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
                                "Invalid scene plan.",
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
                                "Invalid scene plan.",
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
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        GameObject parentObject;
                        bool parentIsSceneRoot;
                        string parentHandleError;
                        if (!TryResolveSceneParentHandle(op.parent, handles, out parentObject, out parentIsSceneRoot, out parentHandleError))
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
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (!parentIsSceneRoot && ReferenceEquals(targetObject, parentObject))
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
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (parentIsSceneRoot)
                        {
                            targetObject.transform.SetParent(null, false);
                            SceneManager.MoveGameObjectToScene(targetObject, scene);
                        }
                        else
                        {
                            targetObject.transform.SetParent(parentObject.transform, false);
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
                                "Invalid scene plan.",
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
                                "Invalid scene plan.",
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
                                "Invalid scene plan.",
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
                                "Invalid scene plan.",
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
                                "Invalid scene plan.",
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
                                "Invalid scene plan.",
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

                    if (string.Equals(opName, "save_scene", StringComparison.Ordinal))
                    {
                        if (i != request.ops.Length - 1)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "schema_error",
                                    evidence = "save_scene must be the final operation in scene mode"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (!EditorSceneManager.SaveScene(scene, assetPath))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "apply_error",
                                    evidence = "EditorSceneManager.SaveScene returned false"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to save scene asset.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        AssetDatabase.SaveAssets();
                        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                        Scene reopened = EditorSceneManager.OpenScene(assetPath, OpenSceneMode.Single);
                        if (!reopened.IsValid() || !reopened.isLoaded)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "apply_error",
                                    evidence = "failed to reopen scene after save"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to reopen scene after save.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        scene = reopened;
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
                            evidence = $"unsupported scene op '{opName}'"
                        }
                    );
                    return BuildError(
                        "UNITY_BRIDGE_SCHEMA",
                        "Invalid scene plan.",
                        request.target,
                        request.ops.Length,
                        executed: false,
                        applied: applied,
                        diagnostics: diagnostics.ToArray()
                    );
                }

                if (!sceneOpened || !saved)
                {
                    return BuildError(
                        "UNITY_BRIDGE_SCHEMA",
                        "Scene mode requires an initial open/create op and save_scene.",
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
                    message = "Scene plan applied via Unity executeMethod.",
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
        }
    }
}
