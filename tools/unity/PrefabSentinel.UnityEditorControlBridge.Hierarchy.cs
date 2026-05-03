using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private static EditorControlResponse HandleDeleteObject(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "hierarchy_path is required for delete_object.");

            GameObject go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            // Prefab instance roots cannot be directly destroyed; unpack first.
            if (PrefabUtility.IsPartOfPrefabInstance(go)
                && PrefabUtility.GetOutermostPrefabInstanceRoot(go) == go)
            {
                PrefabUtility.UnpackPrefabInstance(go, PrefabUnpackMode.Completely, InteractionMode.AutomatedAction);
            }

            string name = go.name;
            int childCount = go.transform.childCount;
            Undo.DestroyObjectImmediate(go);

            return BuildSuccess("EDITOR_CTRL_DELETE_OK",
                $"Deleted: {name} ({childCount} children)",
                data: new EditorControlData
                {
                    deleted_object = name,
                    deleted_child_count = childCount,
                    read_only = false,
                    executed = true
                });
        }

        private static EditorControlResponse HandleListChildren(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "hierarchy_path is required for list_children.");

            GameObject go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            int maxDepth = Math.Min(Math.Max(request.depth, 1), 50);
            var children = new List<ChildEntry>();
            CollectChildren(go.transform, maxDepth, 0, children);

            return BuildSuccess("EDITOR_CTRL_LIST_CHILDREN_OK",
                $"Found {children.Count} children under {go.name}",
                data: new EditorControlData
                {
                    children = children.ToArray(),
                    total_entries = children.Count,
                    read_only = true,
                    executed = true
                });
        }

        private static EditorControlResponse HandleListRoots(EditorControlRequest request)
        {
            var prefabStage = UnityEditor.SceneManagement.PrefabStageUtility.GetCurrentPrefabStage();
            if (prefabStage != null)
            {
                var root = prefabStage.prefabContentsRoot;
                if (root == null)
                    return BuildError("EDITOR_CTRL_PREFAB_STAGE_FAILED", "Prefab Stage root is null.");

                return BuildSuccess("EDITOR_CTRL_LIST_ROOTS_OK",
                    $"Prefab Stage root: {root.name}",
                    data: new EditorControlData
                    {
                        root_objects = new[] { root.name },
                        children = new[] { new ChildEntry
                        {
                            name = root.name,
                            path = "/" + root.name,
                            child_count = root.transform.childCount,
                            depth = 0,
                            active = root.activeSelf,
                            tag = root.tag
                        }},
                        total_entries = 1,
                        read_only = true,
                        executed = true
                    });
            }

            var scene = SceneManager.GetActiveScene();
            if (!scene.IsValid())
                return BuildError("EDITOR_CTRL_NO_SCENE", "No valid active scene found.");

            var rootObjects = scene.GetRootGameObjects();
            var names = new string[rootObjects.Length];
            var entries = new List<ChildEntry>();

            for (int i = 0; i < rootObjects.Length; i++)
            {
                names[i] = rootObjects[i].name;
                entries.Add(new ChildEntry
                {
                    name = rootObjects[i].name,
                    path = "/" + rootObjects[i].name,
                    child_count = rootObjects[i].transform.childCount,
                    depth = 0,
                    active = rootObjects[i].activeSelf,
                    tag = rootObjects[i].tag
                });
            }

            return BuildSuccess("EDITOR_CTRL_LIST_ROOTS_OK",
                $"Found {rootObjects.Length} root objects in scene '{scene.name}'",
                data: new EditorControlData
                {
                    root_objects = names,
                    children = entries.ToArray(),
                    total_entries = entries.Count,
                    read_only = true,
                    executed = true
                });
        }

        private static EditorControlResponse HandleFindRenderersByMaterial(EditorControlRequest request)
        {
            string guid = request.material_guid;
            if (!string.IsNullOrEmpty(request.material_path))
            {
                if (!string.IsNullOrEmpty(guid))
                    return BuildError("EDITOR_CTRL_FIND_RENDERERS_CONFLICT",
                        "Cannot specify both material_guid and material_path. Use one.");
                guid = AssetDatabase.AssetPathToGUID(request.material_path);
                if (string.IsNullOrEmpty(guid))
                    return BuildError("EDITOR_CTRL_MATERIAL_NOT_FOUND",
                        $"Material not found at path: {request.material_path}");
            }
            if (string.IsNullOrEmpty(guid))
                return BuildError("EDITOR_CTRL_MATERIAL_NOT_FOUND",
                    "material_guid or material_path is required.");

            string targetPath = AssetDatabase.GUIDToAssetPath(guid);
            if (string.IsNullOrEmpty(targetPath))
                return BuildError("EDITOR_CTRL_MATERIAL_NOT_FOUND",
                    $"No asset found for GUID: {guid}");

            var renderers = UnityEngine.Object.FindObjectsOfType<Renderer>();
            var matches = new List<MaterialSlotEntry>();
            foreach (var renderer in renderers)
            {
                var mats = renderer.sharedMaterials;
                for (int i = 0; i < mats.Length; i++)
                {
                    if (mats[i] == null) continue;
                    string matPath = AssetDatabase.GetAssetPath(mats[i]);
                    if (matPath == targetPath)
                    {
                        matches.Add(new MaterialSlotEntry
                        {
                            renderer_path = GetHierarchyPath(renderer.transform),
                            renderer_type = renderer.GetType().Name,
                            slot_index = i,
                            material_name = mats[i].name,
                            material_asset_path = matPath,
                            material_guid = guid,
                        });
                    }
                }
            }

            return BuildSuccess("EDITOR_CTRL_FIND_RENDERERS_OK",
                $"Found {matches.Count} slot(s) using material across {renderers.Length} renderers",
                data: new EditorControlData
                {
                    material_slots = matches.ToArray(),
                    total_entries = renderers.Length,
                    executed = true,
                });
        }

        private static EditorControlResponse HandleEditorRename(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_RENAME_NO_PATH", "hierarchy_path is required.");
            if (string.IsNullOrEmpty(request.new_name))
                return BuildError("EDITOR_CTRL_RENAME_NO_NAME", "new_name is required.");

            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_RENAME_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            string oldName = go.name;
            Undo.RecordObject(go, $"PrefabSentinel: Rename {oldName}");
            go.name = request.new_name;

            var resp = BuildSuccess("EDITOR_CTRL_RENAME_OK",
                $"Renamed '{oldName}' to '{request.new_name}'",
                data: new EditorControlData
                {
                    selected_object = request.new_name,
                    executed = true,
                    read_only = false,
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.RecordObject"
            }};
            return resp;
        }

        private static EditorControlResponse HandleEditorSetParent(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_SET_PARENT_NO_PATH", "hierarchy_path is required.");

            var child = GameObject.Find(request.hierarchy_path);
            if (child == null)
                return BuildError("EDITOR_CTRL_SET_PARENT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            Transform newParent = null;
            if (!string.IsNullOrEmpty(request.new_name))
            {
                var parentGo = GameObject.Find(request.new_name);
                if (parentGo == null)
                    return BuildError("EDITOR_CTRL_SET_PARENT_PARENT_NOT_FOUND",
                        $"Parent GameObject not found: {request.new_name}");
                newParent = parentGo.transform;
            }

            Undo.SetTransformParent(child.transform, newParent,
                $"PrefabSentinel: SetParent {child.name}");

            string parentName = newParent != null ? newParent.name : "(scene root)";
            var resp = BuildSuccess("EDITOR_CTRL_SET_PARENT_OK",
                $"Moved '{child.name}' under '{parentName}'",
                data: new EditorControlData
                {
                    selected_object = child.name,
                    executed = true,
                    read_only = false,
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.SetTransformParent"
            }};
            return resp;
        }

        private static void CollectChildren(Transform parent, int maxDepth, int currentDepth, List<ChildEntry> result)
        {
            for (int i = 0; i < parent.childCount; i++)
            {
                Transform child = parent.GetChild(i);
                result.Add(new ChildEntry
                {
                    name = child.name,
                    path = GetHierarchyPath(child),
                    child_count = child.childCount,
                    depth = currentDepth + 1,
                    active = child.gameObject.activeSelf,
                    tag = child.gameObject.tag
                });
                if (currentDepth + 1 < maxDepth)
                    CollectChildren(child, maxDepth, currentDepth + 1, result);
            }
        }

        private static string GetHierarchyPath(Transform t)
        {
            string path = t.name;
            while (t.parent != null)
            {
                t = t.parent;
                path = t.name + "/" + path;
            }
            return "/" + path;
        }
    }
}
