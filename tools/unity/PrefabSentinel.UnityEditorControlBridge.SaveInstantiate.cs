using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace PrefabSentinel
{
    /// <summary>
    /// Save / instantiate / scene partial: prefab save, scene open / save /
    /// create, instantiation, primitive and empty creation, and the batch
    /// creation handler.  TryParseVector3 is the local CSV parser shared by
    /// the create handlers.
    /// </summary>
    public static partial class UnityEditorControlBridge
    {
        private static bool TryParseVector3(string csv, out Vector3 result)
        {
            result = Vector3.zero;
            if (string.IsNullOrEmpty(csv)) return false;
            var parts = csv.Split(',');
            if (parts.Length < 3) return false;
            var ci = System.Globalization.CultureInfo.InvariantCulture;
            return float.TryParse(parts[0].Trim(), System.Globalization.NumberStyles.Float, ci, out result.x)
                && float.TryParse(parts[1].Trim(), System.Globalization.NumberStyles.Float, ci, out result.y)
                && float.TryParse(parts[2].Trim(), System.Globalization.NumberStyles.Float, ci, out result.z);
        }

        private static EditorControlResponse HandleInstantiateToScene(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.asset_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "asset_path is required for instantiate_to_scene.");

            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(request.asset_path);
            if (prefab == null)
                return BuildError("EDITOR_CTRL_ASSET_NOT_FOUND",
                    $"Prefab not found at: {request.asset_path}");

            // Issue #117: snapshot before-instantiate timestamp so the
            // non-fatal pattern table can classify any exceptions emitted
            // during the operation without losing them as fatal errors.
            double instantiateSnapshotTime = EditorApplication.timeSinceStartup;

            GameObject instance = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
            if (instance == null)
                return BuildError("EDITOR_CTRL_INSTANTIATE_FAILED",
                    $"Failed to instantiate: {request.asset_path}");

            if (!string.IsNullOrEmpty(request.hierarchy_path))
            {
                GameObject parent = GameObject.Find(request.hierarchy_path);
                if (parent != null)
                {
                    instance.transform.SetParent(parent.transform, false);
                }
                else
                {
                    UnityEngine.Object.DestroyImmediate(instance);
                    return BuildError("EDITOR_CTRL_PARENT_NOT_FOUND",
                        $"Parent not found: {request.hierarchy_path}");
                }
            }

            if (request.position != null && request.position.Length >= 3)
                instance.transform.localPosition = new Vector3(request.position[0], request.position[1], request.position[2]);

            Selection.activeGameObject = instance;
            Undo.RegisterCreatedObjectUndo(instance, $"PrefabSentinel: Instantiate {prefab.name}");

            var (instObsCount, instLabels) = ConsoleLogBuffer
                .CollectNonFatalCountsSince(instantiateSnapshotTime);

            return BuildSuccess("EDITOR_CTRL_INSTANTIATE_OK",
                $"Instantiated {prefab.name} as {instance.name}",
                data: new EditorControlData
                {
                    instantiated_object = instance.name,
                    selected_object = instance.name,
                    read_only = false,
                    executed = true,
                    warnings = new EditorControlWarnings
                    {
                        udonsharp_obs_nre_count = instObsCount,
                        nonfatal_patterns = instLabels.ToArray(),
                    },
                });
        }

        private static EditorControlResponse HandleEditorCreateEmpty(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.new_name))
                return BuildError("EDITOR_CTRL_CREATE_EMPTY_NO_NAME", "new_name (object name) is required.");

            Transform parentTransform = null;
            if (!string.IsNullOrEmpty(request.hierarchy_path))
            {
                var parentGo = GameObject.Find(request.hierarchy_path);
                if (parentGo == null)
                    return BuildError("EDITOR_CTRL_CREATE_EMPTY_PARENT_NOT_FOUND",
                        $"Parent not found: {request.hierarchy_path}");
                parentTransform = parentGo.transform;
            }

            var go = new GameObject(request.new_name);
            Undo.RegisterCreatedObjectUndo(go, $"PrefabSentinel: Create {request.new_name}");

            if (parentTransform != null)
                Undo.SetTransformParent(go.transform, parentTransform,
                    $"PrefabSentinel: SetParent {request.new_name}");

            if (TryParseVector3(request.property_value, out Vector3 pos))
                go.transform.localPosition = pos;

            string path = GetHierarchyPath(go.transform);
            var resp = BuildSuccess("EDITOR_CTRL_CREATE_EMPTY_OK",
                $"Created empty GameObject '{request.new_name}' at {path}",
                data: new EditorControlData
                {
                    selected_object = request.new_name,
                    output_path = path,
                    executed = true,
                    read_only = false,
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.RegisterCreatedObjectUndo"
            }};
            return resp;
        }

        private static EditorControlResponse HandleEditorCreatePrimitive(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.primitive_type))
                return BuildError("EDITOR_CTRL_CREATE_PRIM_NO_TYPE", "primitive_type is required.");

            PrimitiveType primType;
            try
            {
                primType = (PrimitiveType)System.Enum.Parse(typeof(PrimitiveType), request.primitive_type, true);
            }
            catch (System.ArgumentException)
            {
                return BuildError("EDITOR_CTRL_CREATE_PRIM_BAD_TYPE",
                    $"Invalid primitive_type: {request.primitive_type}. " +
                    "Valid: Cube, Sphere, Cylinder, Capsule, Plane, Quad.");
            }

            Transform parentTransform = null;
            if (!string.IsNullOrEmpty(request.hierarchy_path))
            {
                var parentGo = GameObject.Find(request.hierarchy_path);
                if (parentGo == null)
                    return BuildError("EDITOR_CTRL_CREATE_PRIM_PARENT_NOT_FOUND",
                        $"Parent not found: {request.hierarchy_path}");
                parentTransform = parentGo.transform;
            }

            var go = GameObject.CreatePrimitive(primType);
            Undo.RegisterCreatedObjectUndo(go, $"PrefabSentinel: Create {request.primitive_type}");

            if (!string.IsNullOrEmpty(request.new_name))
            {
                Undo.RecordObject(go, $"PrefabSentinel: Rename {go.name}");
                go.name = request.new_name;
            }

            if (parentTransform != null)
                Undo.SetTransformParent(go.transform, parentTransform,
                    $"PrefabSentinel: SetParent {go.name}");

            if (TryParseVector3(request.property_value, out Vector3 position))
                go.transform.localPosition = position;
            if (TryParseVector3(request.scale, out Vector3 scl))
                go.transform.localScale = scl;
            if (TryParseVector3(request.rotation, out Vector3 rot))
                go.transform.localEulerAngles = rot;

            string primPath = GetHierarchyPath(go.transform);
            var resp = BuildSuccess("EDITOR_CTRL_CREATE_PRIM_OK",
                $"Created {request.primitive_type} '{go.name}' at {primPath}",
                data: new EditorControlData
                {
                    selected_object = go.name,
                    output_path = primPath,
                    executed = true,
                    read_only = false,
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.RegisterCreatedObjectUndo"
            }};
            return resp;
        }

        private static EditorControlResponse HandleEditorBatchCreate(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.batch_objects_json))
                return BuildError("EDITOR_CTRL_BATCH_CREATE_NO_DATA", "batch_objects_json is required.");

            BatchObjectArray wrapper;
            try
            {
                wrapper = JsonUtility.FromJson<BatchObjectArray>(
                    "{\"items\":" + request.batch_objects_json + "}");
            }
            catch (System.Exception ex)
            {
                return BuildError("EDITOR_CTRL_BATCH_CREATE_JSON_ERROR",
                    $"Failed to parse batch_objects_json: {ex.Message}");
            }

            if (wrapper.items == null || wrapper.items.Length == 0)
                return BuildError("EDITOR_CTRL_BATCH_CREATE_EMPTY", "batch_objects_json is empty.");

            int undoGroup = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("PrefabSentinel: Batch Create");

            var createdPaths = new List<string>();
            var warnings = new List<EditorControlDiagnostic>();

            foreach (var spec in wrapper.items)
            {
                GameObject go;
                if (!string.IsNullOrEmpty(spec.type) && !string.Equals(spec.type, "Empty", System.StringComparison.OrdinalIgnoreCase))
                {
                    try
                    {
                        var primType = (PrimitiveType)System.Enum.Parse(typeof(PrimitiveType), spec.type, true);
                        go = GameObject.CreatePrimitive(primType);
                    }
                    catch (System.ArgumentException)
                    {
                        Undo.CollapseUndoOperations(undoGroup);
                        return BuildError("EDITOR_CTRL_BATCH_CREATE_BAD_TYPE",
                            $"Invalid type at index {createdPaths.Count}: {spec.type}. " +
                            "Valid: Cube, Sphere, Cylinder, Capsule, Plane, Quad, Empty.");
                    }
                }
                else
                {
                    go = new GameObject("GameObject");
                }
                Undo.RegisterCreatedObjectUndo(go, $"PrefabSentinel: Create {spec.name}");

                if (!string.IsNullOrEmpty(spec.name))
                    go.name = spec.name;

                if (!string.IsNullOrEmpty(spec.parent))
                {
                    var parent = GameObject.Find(spec.parent);
                    if (parent != null)
                        Undo.SetTransformParent(go.transform, parent.transform,
                            $"PrefabSentinel: SetParent {go.name}");
                    else
                        warnings.Add(new EditorControlDiagnostic
                        {
                            path = spec.parent,
                            location = $"batch item index {createdPaths.Count}",
                            detail = $"Parent not found: {spec.parent}. Object '{go.name}' created at scene root.",
                            evidence = "GameObject.Find returned null"
                        });
                }

                if (TryParseVector3(spec.position, out Vector3 pos))
                    go.transform.localPosition = pos;
                if (TryParseVector3(spec.scale, out Vector3 scl))
                    go.transform.localScale = scl;
                if (TryParseVector3(spec.rotation, out Vector3 rot))
                    go.transform.localEulerAngles = rot;

                if (spec.components != null)
                {
                    foreach (var compTypeName in spec.components)
                    {
                        if (string.IsNullOrEmpty(compTypeName)) continue;
                        var compType = ResolveComponentType(compTypeName);
                        if (compType == null)
                        {
                            warnings.Add(new EditorControlDiagnostic
                            {
                                path = GetHierarchyPath(go.transform),
                                location = $"batch item index {createdPaths.Count}",
                                detail = $"Component type not found: {compTypeName}. Skipped.",
                                evidence = "ResolveComponentType returned null"
                            });
                            continue;
                        }
                        Undo.AddComponent(go, compType);
                    }
                }

                createdPaths.Add(GetHierarchyPath(go.transform));
            }

            Undo.CollapseUndoOperations(undoGroup);

            var batchCreateResp = BuildSuccess("EDITOR_CTRL_BATCH_CREATE_OK",
                $"Created {createdPaths.Count} objects",
                data: new EditorControlData
                {
                    executed = true,
                    read_only = false,
                    suggestions = createdPaths.ToArray(),
                });
            var diagList = new List<EditorControlDiagnostic>(warnings);
            diagList.Add(new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.CollapseUndoOperations"
            });
            batchCreateResp.diagnostics = diagList.ToArray();
            if (warnings.Count > 0)
                batchCreateResp.severity = "warning";
            return batchCreateResp;
        }

        private static EditorControlResponse HandleEditorOpenScene(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.asset_path))
                return BuildError("EDITOR_CTRL_OPEN_SCENE_NO_PATH", "asset_path is required.");

            if (!System.IO.File.Exists(request.asset_path))
                return BuildError("EDITOR_CTRL_OPEN_SCENE_NOT_FOUND",
                    $"Scene file not found: {request.asset_path}");

            var mode = string.Equals(request.open_scene_mode, "additive",
                System.StringComparison.OrdinalIgnoreCase)
                ? OpenSceneMode.Additive
                : OpenSceneMode.Single;

            var scene = EditorSceneManager.OpenScene(request.asset_path, mode);

            return BuildSuccess("EDITOR_CTRL_OPEN_SCENE_OK",
                $"Opened scene: {request.asset_path} ({request.open_scene_mode})",
                data: new EditorControlData
                {
                    asset_path = request.asset_path,
                    output_path = scene.name,
                    executed = true,
                });
        }

        private static EditorControlResponse HandleEditorSaveScene(EditorControlRequest request)
        {
            if (!string.IsNullOrEmpty(request.asset_path))
            {
                var scene = SceneManager.GetActiveScene();
                bool ok = EditorSceneManager.SaveScene(scene, request.asset_path);
                if (!ok)
                    return BuildError("EDITOR_CTRL_SAVE_SCENE_FAILED",
                        $"Failed to save scene to: {request.asset_path}");
                return BuildSuccess("EDITOR_CTRL_SAVE_SCENE_OK",
                    $"Saved scene to: {request.asset_path}",
                    data: new EditorControlData
                    {
                        asset_path = request.asset_path,
                        executed = true,
                    });
            }
            else
            {
                bool ok = EditorSceneManager.SaveOpenScenes();
                if (!ok)
                    return BuildError("EDITOR_CTRL_SAVE_SCENE_FAILED",
                        "Failed to save open scenes.");
                var scene = SceneManager.GetActiveScene();
                return BuildSuccess("EDITOR_CTRL_SAVE_SCENE_OK",
                    $"Saved all open scenes (active: {scene.name})",
                    data: new EditorControlData
                    {
                        asset_path = scene.path,
                        executed = true,
                    });
            }
        }

        private static EditorControlResponse HandleEditorCreateScene(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.asset_path))
                return BuildError("EDITOR_CTRL_CREATE_SCENE_NO_PATH", "asset_path is required.");

            if (!request.asset_path.EndsWith(".unity", System.StringComparison.OrdinalIgnoreCase))
                return BuildError("EDITOR_CTRL_CREATE_SCENE_BAD_EXT",
                    $"asset_path must end with .unity: {request.asset_path}");

            string dir = System.IO.Path.GetDirectoryName(request.asset_path);
            if (!string.IsNullOrEmpty(dir) && !System.IO.Directory.Exists(dir))
                System.IO.Directory.CreateDirectory(dir);

            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            bool ok = EditorSceneManager.SaveScene(scene, request.asset_path);
            if (!ok)
                return BuildError("EDITOR_CTRL_CREATE_SCENE_FAILED",
                    $"Failed to save new scene to: {request.asset_path}");

            return BuildSuccess("EDITOR_CTRL_CREATE_SCENE_OK",
                $"Created new scene: {request.asset_path}",
                data: new EditorControlData
                {
                    asset_path = request.asset_path,
                    output_path = scene.name,
                    executed = true,
                    read_only = false,
                });
        }

        // Issue #193 — safe-save handler.  Takes a non-empty list of
        // protected component type names, calls the underlying
        // ``SaveAsPrefabAsset`` invocation through the private
        // ``SaveAsPrefabCore`` helper, then verifies that every protected
        // type is attached on the saved asset.  When a protected type was
        // stripped during the save (the documented VRC_UiShape and
        // missing-script cases) the handler re-attaches it via
        // ``Undo.AddComponent`` and re-saves so the resulting asset
        // matches the safe-save contract.  The response payload carries
        // the list of re-attached component types and the list of
        // parent-prefab modification overrides that became orphan as a
        // result of the save.
        private static EditorControlResponse HandleSafeSaveAsPrefab(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.protect_components_json))
                return BuildError(
                    "EDITOR_CTRL_SAFE_SAVE_PREFAB_PROTECT_REQUIRED",
                    "protect_components is required and must be a non-empty list of component type names.");

            string[] protectTypes;
            try
            {
                var wrapper = JsonUtility.FromJson<StringArrayWrapper>(
                    "{\"items\":" + request.protect_components_json + "}");
                protectTypes = wrapper != null && wrapper.items != null
                    ? wrapper.items
                    : Array.Empty<string>();
            }
            catch (Exception ex)
            {
                return BuildError(
                    "EDITOR_CTRL_SAFE_SAVE_PREFAB_BAD_JSON",
                    $"protect_components_json could not be parsed as a string array: {ex.Message}");
            }

            if (protectTypes.Length == 0)
                return BuildError(
                    "EDITOR_CTRL_SAFE_SAVE_PREFAB_PROTECT_REQUIRED",
                    "protect_components must list at least one component type name.");

            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError(
                    "EDITOR_CTRL_SAFE_SAVE_PREFAB_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            // Capture pre-save parent-prefab modifications so we can detect
            // which overrides became orphan once the save runs.
            var preSaveModifications = CollectParentModifications(go);

            EditorControlResponse coreResponse = SaveAsPrefabCore(go, request, out GameObject savedAsset);
            if (!coreResponse.success || savedAsset == null)
                return coreResponse;

            var reattached = new List<string>();
            foreach (string typeName in protectTypes)
            {
                if (string.IsNullOrEmpty(typeName)) continue;
                if (HasComponentByName(savedAsset, typeName)) continue;

                Type resolved = ResolveComponentType(typeName);
                if (resolved == null)
                {
                    // Unknown type — skip rather than crash; surface in diagnostics.
                    coreResponse.diagnostics = AppendDiagnostic(coreResponse.diagnostics,
                        new EditorControlDiagnostic
                        {
                            path = request.asset_path,
                            location = "protect_components",
                            detail = "warning",
                            evidence = $"protected component type '{typeName}' could not be resolved",
                        });
                    continue;
                }
                Undo.AddComponent(savedAsset, resolved);
                reattached.Add(typeName);
            }

            if (reattached.Count > 0)
            {
                bool resaveSuccess;
                PrefabUtility.SaveAsPrefabAsset(savedAsset, request.asset_path, out resaveSuccess);
                if (!resaveSuccess)
                    return BuildError(
                        "EDITOR_CTRL_SAFE_SAVE_PREFAB_FAILED",
                        $"safe-save re-save after re-attach failed for: {request.asset_path}");
            }

            var orphanEntries = ComputeOrphanModifications(preSaveModifications, savedAsset);
            coreResponse.data.reattached_components = reattached.ToArray();
            coreResponse.data.orphan_modifications = orphanEntries;
            if (orphanEntries.Length > 0)
            {
                coreResponse.severity = "warning";
                coreResponse.diagnostics = AppendDiagnostic(coreResponse.diagnostics,
                    new EditorControlDiagnostic
                    {
                        path = request.asset_path,
                        location = "orphan_modifications",
                        detail = "warning",
                        evidence = $"{orphanEntries.Length} parent-prefab override(s) became orphan after save",
                    });
            }
            return coreResponse;
        }

        [Serializable]
        private sealed class StringArrayWrapper
        {
            public string[] items;
        }

        // Underlying prefab-save invocation used by the safe-save handler.
        // Encapsulates path validation, directory creation, the
        // ``PrefabUtility.SaveAsPrefabAsset`` call, and the non-fatal
        // classification snapshot.  The saved asset is returned via the
        // out parameter so the caller can re-attach protected components.
        private static EditorControlResponse SaveAsPrefabCore(
            GameObject go,
            EditorControlRequest request,
            out GameObject savedAsset)
        {
            savedAsset = null;
            if (string.IsNullOrEmpty(request.asset_path))
                return BuildError("EDITOR_CTRL_SAFE_SAVE_PREFAB_FAILED",
                    "asset_path is required.");
            if (!request.asset_path.EndsWith(".prefab", System.StringComparison.OrdinalIgnoreCase))
                return BuildError("EDITOR_CTRL_SAFE_SAVE_PREFAB_FAILED",
                    $"asset_path must end with .prefab: {request.asset_path}");

            string dir = System.IO.Path.GetDirectoryName(request.asset_path);
            if (!string.IsNullOrEmpty(dir) && !System.IO.Directory.Exists(dir))
                System.IO.Directory.CreateDirectory(dir);

            bool isVariant = PrefabUtility.IsPartOfPrefabInstance(go);
            string basePrefabPath = "";
            if (isVariant)
            {
                var baseObj = PrefabUtility.GetCorrespondingObjectFromSource(go);
                if (baseObj != null)
                    basePrefabPath = AssetDatabase.GetAssetPath(baseObj);
            }

            bool wasVariant = isVariant;
            if (request.force_original && isVariant)
            {
                Undo.RegisterFullObjectHierarchyUndo(go, "Unpack prefab for force_original save");
                PrefabUtility.UnpackPrefabInstance(go, PrefabUnpackMode.Completely, InteractionMode.AutomatedAction);
                isVariant = false;
                basePrefabPath = "";
            }

            // Issue #117: snapshot the console buffer time so that any
            // exceptions logged during ``SaveAsPrefabAsset`` can be matched
            // against the non-fatal pattern table afterwards. The snapshot
            // is taken before the operation so first-frame OnBeforeSerialize
            // noise is captured.
            double saveSnapshotTime = EditorApplication.timeSinceStartup;

            bool success;
            savedAsset = PrefabUtility.SaveAsPrefabAsset(go, request.asset_path, out success);
            if (!success)
                return BuildError("EDITOR_CTRL_SAFE_SAVE_PREFAB_FAILED",
                    $"SaveAsPrefabAsset failed for: {request.asset_path}");

            var (saveObsCount, saveLabels) = ConsoleLogBuffer
                .CollectNonFatalCountsSince(saveSnapshotTime);

            string kind = isVariant ? "Prefab Variant" : "Prefab";
            var resp = BuildSuccess("EDITOR_CTRL_SAFE_SAVE_PREFAB_OK",
                $"Saved {request.hierarchy_path} as {kind}: {request.asset_path}",
                data: new EditorControlData
                {
                    output_path = request.asset_path,
                    asset_path = basePrefabPath,
                    executed = true,
                    read_only = false,
                    warnings = new EditorControlWarnings
                    {
                        udonsharp_obs_nre_count = saveObsCount,
                        nonfatal_patterns = saveLabels.ToArray(),
                    },
                });

            var diags = new List<EditorControlDiagnostic>();
            diags.Add(new EditorControlDiagnostic
            {
                detail = $"Created as {kind}.",
                evidence = "PrefabUtility.SaveAsPrefabAsset"
            });
            if (isVariant && !string.IsNullOrEmpty(basePrefabPath))
                diags.Add(new EditorControlDiagnostic
                {
                    detail = $"Base Prefab: {basePrefabPath}",
                    evidence = "PrefabUtility.GetCorrespondingObjectFromSource"
                });
            if (request.force_original && wasVariant)
                diags.Add(new EditorControlDiagnostic
                {
                    detail = "force_original: Prefab Instance was unpacked before saving. Scene GameObject is now unconnected.",
                    evidence = "PrefabUtility.UnpackPrefabInstance(PrefabUnpackMode.Completely)"
                });
            resp.diagnostics = diags.ToArray();
            return resp;
        }

        private static bool HasComponentByName(GameObject go, string typeName)
        {
            if (go == null || string.IsNullOrEmpty(typeName)) return false;
            foreach (var comp in go.GetComponentsInChildren<Component>(true))
            {
                if (comp == null) continue;
                Type t = comp.GetType();
                if (t.Name == typeName || t.FullName == typeName)
                    return true;
            }
            return false;
        }

        private static List<PropertyModification> CollectParentModifications(GameObject go)
        {
            var result = new List<PropertyModification>();
            if (go == null) return result;
            if (!PrefabUtility.IsPartOfPrefabInstance(go)) return result;
            var mods = PrefabUtility.GetPropertyModifications(go);
            if (mods == null) return result;
            foreach (var mod in mods) result.Add(mod);
            return result;
        }

        private static OrphanModificationEntry[] ComputeOrphanModifications(
            List<PropertyModification> preSaveModifications,
            GameObject savedAsset)
        {
            if (preSaveModifications == null || preSaveModifications.Count == 0
                || savedAsset == null)
                return Array.Empty<OrphanModificationEntry>();

            var assetTransforms = savedAsset.GetComponentsInChildren<Transform>(true);
            var assetPaths = new HashSet<string>();
            foreach (var t in assetTransforms)
            {
                if (t != null) assetPaths.Add(GetHierarchyPath(t));
            }

            var orphans = new List<OrphanModificationEntry>();
            foreach (var mod in preSaveModifications)
            {
                if (mod == null || mod.target == null) continue;
                string targetPath = "";
                var targetGo = mod.target as GameObject;
                if (targetGo != null) targetPath = GetHierarchyPath(targetGo.transform);
                else
                {
                    var targetComp = mod.target as Component;
                    if (targetComp != null) targetPath = GetHierarchyPath(targetComp.transform);
                }
                if (string.IsNullOrEmpty(targetPath)) continue;
                if (!assetPaths.Contains(targetPath))
                {
                    orphans.Add(new OrphanModificationEntry
                    {
                        target_object_path = targetPath,
                        property_path = mod.propertyPath ?? "",
                    });
                }
            }
            return orphans.ToArray();
        }

        private static EditorControlDiagnostic[] AppendDiagnostic(
            EditorControlDiagnostic[] existing, EditorControlDiagnostic added)
        {
            if (existing == null) return new[] { added };
            var combined = new EditorControlDiagnostic[existing.Length + 1];
            Array.Copy(existing, combined, existing.Length);
            combined[existing.Length] = added;
            return combined;
        }
    }
}
