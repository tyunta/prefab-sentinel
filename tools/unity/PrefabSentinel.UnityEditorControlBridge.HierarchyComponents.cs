using System;
using System.Collections.Generic;
using System.Reflection;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace PrefabSentinel
{
    /// <summary>
    /// Hierarchy + components partial: hierarchy listing, add/remove/rename,
    /// property and material setters, blend-shape access, menu enumeration
    /// and execution, UdonSharp program-asset creation, and the existing
    /// add-component idempotency reuse / relink path used by the
    /// <c>editor_add_component</c> action when the type derives from
    /// <c>UdonSharpBehaviour</c>.
    /// </summary>
    public static partial class UnityEditorControlBridge
    {
        // ── Set / list / read material ──

        private static EditorControlResponse HandleSetMaterial(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_SET_MATERIAL_NO_PATH", "hierarchy_path is required.");
            if (request.material_index < 0)
                return BuildError("EDITOR_CTRL_SET_MATERIAL_NO_INDEX", "material_index is required (>= 0).");

            string guid = request.material_guid;
            if (!string.IsNullOrEmpty(request.material_path))
            {
                if (!string.IsNullOrEmpty(guid))
                    return BuildError("EDITOR_CTRL_SET_MATERIAL_CONFLICT",
                        "Cannot specify both material_guid and material_path. Use one.");
                guid = AssetDatabase.AssetPathToGUID(request.material_path);
                if (string.IsNullOrEmpty(guid))
                    return BuildError("EDITOR_CTRL_SET_MATERIAL_NOT_FOUND",
                        $"Material not found at path: {request.material_path}");
            }
            if (string.IsNullOrEmpty(guid))
                return BuildError("EDITOR_CTRL_SET_MATERIAL_NO_GUID",
                    "material_guid or material_path is required.");

            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_SET_MATERIAL_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            var renderer = go.GetComponent<Renderer>();
            if (renderer == null)
                return BuildError("EDITOR_CTRL_SET_MATERIAL_NO_RENDERER",
                    $"No Renderer on: {request.hierarchy_path}");

            var mats = renderer.sharedMaterials;
            if (request.material_index >= mats.Length)
                return BuildError("EDITOR_CTRL_SET_MATERIAL_INDEX_OOB",
                    $"material_index {request.material_index} out of range (length={mats.Length}).");

            string assetPath = AssetDatabase.GUIDToAssetPath(guid);
            if (string.IsNullOrEmpty(assetPath))
                return BuildError("EDITOR_CTRL_SET_MATERIAL_GUID_NOT_FOUND",
                    $"No asset found for GUID: {guid}");

            var mat = AssetDatabase.LoadAssetAtPath<Material>(assetPath);
            if (mat == null)
                return BuildError("EDITOR_CTRL_SET_MATERIAL_LOAD_FAILED",
                    $"Failed to load Material at: {assetPath}");

            Undo.RecordObject(renderer, $"PrefabSentinel: Set material[{request.material_index}]");
            mats[request.material_index] = mat;
            renderer.sharedMaterials = mats;

            var resp = BuildSuccess("EDITOR_CTRL_SET_MATERIAL_OK",
                $"Set material[{request.material_index}] to {assetPath}",
                data: new EditorControlData { executed = true, read_only = false });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.RecordObject"
            }};
            return resp;
        }

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

        // ── Hierarchy listing ──

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

        private static EditorControlResponse HandleListMaterials(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "hierarchy_path is required for list_materials.");

            GameObject go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            var renderers = go.GetComponentsInChildren<Renderer>();
            var slots = new List<MaterialSlotEntry>();

            foreach (var renderer in renderers)
            {
                string rendererPath = GetHierarchyPath(renderer.transform);
                string rendererType = renderer.GetType().Name;
                var materials = renderer.sharedMaterials;

                for (int i = 0; i < materials.Length; i++)
                {
                    var mat = materials[i];
                    string matName = mat != null ? mat.name : "(none)";
                    string matAssetPath = "";
                    string matGuid = "";

                    if (mat != null)
                    {
                        matAssetPath = AssetDatabase.GetAssetPath(mat);
                        matGuid = AssetDatabase.AssetPathToGUID(matAssetPath);
                    }

                    slots.Add(new MaterialSlotEntry
                    {
                        renderer_path = rendererPath,
                        renderer_type = rendererType,
                        slot_index = i,
                        material_name = matName,
                        material_asset_path = matAssetPath,
                        material_guid = matGuid
                    });
                }
            }

            return BuildSuccess("EDITOR_CTRL_LIST_MATERIALS_OK",
                $"Found {slots.Count} material slots on {renderers.Length} renderers under {go.name}",
                data: new EditorControlData
                {
                    material_slots = slots.ToArray(),
                    total_entries = slots.Count,
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

        // ── Material property read / write ──

        private static EditorControlResponse HandleGetMaterialProperty(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "hierarchy_path is required for get_material_property.");
            if (request.material_index < 0)
                return BuildError("EDITOR_CTRL_MISSING_INDEX", "material_index is required (>= 0).");

            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            var renderer = go.GetComponent<Renderer>();
            if (renderer == null)
                return BuildError("EDITOR_CTRL_NO_RENDERER",
                    $"No Renderer on: {request.hierarchy_path}");

            var mats = renderer.sharedMaterials;
            if (request.material_index >= mats.Length)
                return BuildError("EDITOR_CTRL_INDEX_OOB",
                    $"material_index {request.material_index} out of range (length={mats.Length}).");

            var mat = mats[request.material_index];
            if (mat == null)
                return BuildError("EDITOR_CTRL_MATERIAL_NULL",
                    $"Material at index {request.material_index} is null.");

            var shader = mat.shader;
            if (shader == null)
                return BuildError("EDITOR_CTRL_SHADER_NULL",
                    $"Material at index {request.material_index} has no shader assigned.");

            var properties = new List<MaterialPropertyEntry>();
            int propCount = shader.GetPropertyCount();

            for (int i = 0; i < propCount; i++)
            {
                string propName = shader.GetPropertyName(i);
                var propType = shader.GetPropertyType(i);

                if (!string.IsNullOrEmpty(request.property_name) && propName != request.property_name)
                    continue;

                string valueStr;
                switch (propType)
                {
                    case UnityEngine.Rendering.ShaderPropertyType.Color:
                        valueStr = mat.GetColor(propName).ToString();
                        break;
                    case UnityEngine.Rendering.ShaderPropertyType.Float:
                    case UnityEngine.Rendering.ShaderPropertyType.Range:
                        valueStr = mat.GetFloat(propName).ToString("G9");
                        break;
                    case UnityEngine.Rendering.ShaderPropertyType.Vector:
                        valueStr = mat.GetVector(propName).ToString();
                        break;
                    case UnityEngine.Rendering.ShaderPropertyType.Texture:
                        var tex = mat.GetTexture(propName);
                        valueStr = tex != null ? AssetDatabase.GetAssetPath(tex) : "(none)";
                        break;
                    case UnityEngine.Rendering.ShaderPropertyType.Int:
                        valueStr = mat.GetInteger(propName).ToString();
                        break;
                    default:
                        valueStr = "(unknown type)";
                        break;
                }

                properties.Add(new MaterialPropertyEntry
                {
                    property_name = propName,
                    property_type = propType.ToString(),
                    value = valueStr
                });
            }

            if (!string.IsNullOrEmpty(request.property_name) && properties.Count == 0)
                return BuildError("EDITOR_CTRL_PROPERTY_NOT_FOUND",
                    $"Property '{request.property_name}' not found on shader '{shader.name}'.",
                    new EditorControlData
                    {
                        suggestions = SuggestSimilar(request.property_name, CollectShaderPropertyNames(shader)),
                    });

            return BuildSuccess("EDITOR_CTRL_GET_MATERIAL_PROPERTY_OK",
                $"Found {properties.Count} properties on material '{mat.name}' (shader: {shader.name})",
                data: new EditorControlData
                {
                    material_properties = properties.ToArray(),
                    total_entries = properties.Count,
                    read_only = true,
                    executed = true
                });
        }

        private static EditorControlResponse HandleSetMaterialProperty(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "hierarchy_path is required for set_material_property.");
            if (request.material_index < 0)
                return BuildError("EDITOR_CTRL_MISSING_INDEX", "material_index is required (>= 0).");
            if (string.IsNullOrEmpty(request.property_name))
                return BuildError("EDITOR_CTRL_MISSING_PROPERTY", "property_name is required.");
            if (string.IsNullOrEmpty(request.property_value))
                return BuildError("EDITOR_CTRL_MISSING_VALUE", "property_value is required.");

            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            var renderer = go.GetComponent<Renderer>();
            if (renderer == null)
                return BuildError("EDITOR_CTRL_NO_RENDERER",
                    $"No Renderer on: {request.hierarchy_path}");

            var mats = renderer.sharedMaterials;
            if (request.material_index >= mats.Length)
                return BuildError("EDITOR_CTRL_INDEX_OOB",
                    $"material_index {request.material_index} out of range (length={mats.Length}).");

            var mat = mats[request.material_index];
            if (mat == null)
                return BuildError("EDITOR_CTRL_MATERIAL_NULL",
                    $"Material at index {request.material_index} is null.");

            var shader = mat.shader;
            if (shader == null)
                return BuildError("EDITOR_CTRL_SHADER_NULL",
                    $"Material at index {request.material_index} has no shader assigned.");

            Undo.RecordObject(mat, $"Set {request.property_name}");

            var applyError = ApplyMaterialPropertyValue(mat, request.property_name, request.property_value);
            if (applyError != null) return applyError;

            var propType = shader.GetPropertyType(shader.FindPropertyIndex(request.property_name));
            string readBack;
            switch (propType)
            {
                case UnityEngine.Rendering.ShaderPropertyType.Color:
                    readBack = mat.GetColor(request.property_name).ToString();
                    break;
                case UnityEngine.Rendering.ShaderPropertyType.Float:
                case UnityEngine.Rendering.ShaderPropertyType.Range:
                    readBack = mat.GetFloat(request.property_name).ToString("G9");
                    break;
                case UnityEngine.Rendering.ShaderPropertyType.Vector:
                    readBack = mat.GetVector(request.property_name).ToString();
                    break;
                case UnityEngine.Rendering.ShaderPropertyType.Texture:
                    var readTex = mat.GetTexture(request.property_name);
                    readBack = readTex != null ? AssetDatabase.GetAssetPath(readTex) : "(none)";
                    break;
                case UnityEngine.Rendering.ShaderPropertyType.Int:
                    readBack = mat.GetInteger(request.property_name).ToString();
                    break;
                default:
                    readBack = "(unknown)";
                    break;
            }

            SceneView sv = SceneView.lastActiveSceneView;
            if (sv != null) ForceRenderAndRepaint(sv);

            var resp = BuildSuccess("EDITOR_CTRL_SET_MATERIAL_PROPERTY_OK",
                $"Set {request.property_name} on material '{mat.name}'",
                data: new EditorControlData
                {
                    material_properties = new[] { new MaterialPropertyEntry
                    {
                        property_name = request.property_name,
                        property_type = propType.ToString(),
                        value = readBack
                    }},
                    total_entries = 1,
                    executed = true
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.RecordObject"
            }};
            return resp;
        }

        /// <summary>
        /// Apply a single shader property value to a material.
        /// Returns null on success, or an error response on failure.
        /// The caller is responsible for Undo.RecordObject before calling this method.
        /// </summary>
        private static EditorControlResponse ApplyMaterialPropertyValue(
            Material mat, string propertyName, string propertyValue)
        {
            var shader = mat.shader;
            int propIdx = shader.FindPropertyIndex(propertyName);
            if (propIdx < 0)
                return BuildError("EDITOR_CTRL_PROPERTY_NOT_FOUND",
                    $"Property '{propertyName}' not found on shader '{shader.name}'.",
                    new EditorControlData
                    {
                        suggestions = SuggestSimilar(propertyName, CollectShaderPropertyNames(shader)),
                    });

            var propType = shader.GetPropertyType(propIdx);
            string val = propertyValue;

            try
            {
                switch (propType)
                {
                    case UnityEngine.Rendering.ShaderPropertyType.Float:
                    case UnityEngine.Rendering.ShaderPropertyType.Range:
                        mat.SetFloat(propertyName, float.Parse(val, System.Globalization.CultureInfo.InvariantCulture));
                        break;

                    case UnityEngine.Rendering.ShaderPropertyType.Int:
                        mat.SetInteger(propertyName, int.Parse(val, System.Globalization.CultureInfo.InvariantCulture));
                        break;

                    case UnityEngine.Rendering.ShaderPropertyType.Color:
                    {
                        var trimmed = val.Trim('[', ']');
                        var parts = trimmed.Split(',');
                        if (parts.Length != 4)
                            return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                                $"Color requires [r,g,b,a], got {parts.Length} components.");
                        mat.SetColor(propertyName, new Color(
                            float.Parse(parts[0].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[1].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[2].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[3].Trim(), System.Globalization.CultureInfo.InvariantCulture)));
                        break;
                    }

                    case UnityEngine.Rendering.ShaderPropertyType.Vector:
                    {
                        var trimmed = val.Trim('[', ']');
                        var parts = trimmed.Split(',');
                        if (parts.Length != 4)
                            return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                                $"Vector requires [x,y,z,w], got {parts.Length} components.");
                        mat.SetVector(propertyName, new Vector4(
                            float.Parse(parts[0].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[1].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[2].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[3].Trim(), System.Globalization.CultureInfo.InvariantCulture)));
                        break;
                    }

                    case UnityEngine.Rendering.ShaderPropertyType.Texture:
                    {
                        if (string.IsNullOrEmpty(val))
                        {
                            mat.SetTexture(propertyName, null);
                        }
                        else if (val.StartsWith("guid:"))
                        {
                            string guid = val.Substring(5);
                            string texPath = AssetDatabase.GUIDToAssetPath(guid);
                            if (string.IsNullOrEmpty(texPath))
                                return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                                    $"Texture GUID not found: {guid}");
                            var tex = AssetDatabase.LoadAssetAtPath<Texture>(texPath);
                            if (tex == null)
                            {
                                if (texPath.EndsWith(".mat"))
                                    return BuildError("EDITOR_CTRL_SET_MAT_PROP_WRONG_GUID",
                                        $"The specified GUID points to a material asset '{texPath}'. " +
                                        "Please specify a texture GUID instead.");
                                return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                                    $"Failed to load texture from GUID '{guid}' (resolved to '{texPath}').");
                            }
                            mat.SetTexture(propertyName, tex);
                        }
                        else if (val.StartsWith("path:"))
                        {
                            string texPath = val.Substring(5);
                            var tex = AssetDatabase.LoadAssetAtPath<Texture>(texPath);
                            if (tex == null)
                                return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                                    $"Texture not found at path: {texPath}");
                            mat.SetTexture(propertyName, tex);
                        }
                        else
                        {
                            return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                                "Texture value must be 'guid:<hex>', 'path:<asset_path>', or empty string for null.");
                        }
                        break;
                    }

                    default:
                        return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                            $"Unsupported property type: {propType}");
                }
            }
            catch (FormatException ex)
            {
                return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                    $"Failed to parse value '{val}' for {propType}: {ex.Message}");
            }

            return null;
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

        private static List<string> CollectShaderPropertyNames(Shader shader)
        {
            var names = new List<string>();
            int count = shader.GetPropertyCount();
            for (int i = 0; i < count; i++)
                names.Add(shader.GetPropertyName(i));
            return names;
        }

        // ── Blend shape access ──

        private static EditorControlResponse HandleGetBlendShapes(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "hierarchy_path is required for get_blend_shapes");

            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            var smr = go.GetComponent<SkinnedMeshRenderer>();
            if (smr == null)
                return BuildError("EDITOR_CTRL_NO_SMR",
                    $"No SkinnedMeshRenderer on: {request.hierarchy_path}");

            var mesh = smr.sharedMesh;
            if (mesh == null)
                return BuildError("EDITOR_CTRL_NO_SMR",
                    $"SkinnedMeshRenderer has no mesh: {request.hierarchy_path}");

            int count = mesh.blendShapeCount;
            var entries = new List<BlendShapeEntry>();
            string filter = request.filter ?? "";

            for (int i = 0; i < count; i++)
            {
                string shapeName = mesh.GetBlendShapeName(i);
                if (filter.Length > 0 && shapeName.IndexOf(filter, System.StringComparison.OrdinalIgnoreCase) < 0)
                    continue;
                entries.Add(new BlendShapeEntry
                {
                    index = i,
                    name = shapeName,
                    weight = smr.GetBlendShapeWeight(i),
                });
            }

            return BuildSuccess("EDITOR_CTRL_BLEND_SHAPES_OK",
                $"Found {entries.Count} blend shapes (total: {count})",
                data: new EditorControlData
                {
                    blend_shapes = entries.ToArray(),
                    total_entries = count,
                    renderer_path = GetRelativePath(go.transform, smr.transform),
                    read_only = true,
                    executed = true,
                });
        }

        /// <summary>Returns the relative path from root to target (or target name if same).</summary>
        private static string GetRelativePath(Transform root, Transform target)
        {
            if (root == target) return target.name;
            var parts = new List<string>();
            var current = target;
            while (current != null && current != root)
            {
                parts.Add(current.name);
                current = current.parent;
            }
            parts.Reverse();
            return string.Join("/", parts);
        }

        private static EditorControlResponse HandleSetBlendShape(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "hierarchy_path is required for set_blend_shape");
            if (string.IsNullOrEmpty(request.blend_shape_name))
                return BuildError("EDITOR_CTRL_MISSING_PROPERTY", "blend_shape_name is required for set_blend_shape");

            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            var smr = go.GetComponent<SkinnedMeshRenderer>();
            if (smr == null)
                return BuildError("EDITOR_CTRL_NO_SMR",
                    $"No SkinnedMeshRenderer on: {request.hierarchy_path}");

            var mesh = smr.sharedMesh;
            if (mesh == null)
                return BuildError("EDITOR_CTRL_NO_SMR",
                    $"SkinnedMeshRenderer has no mesh: {request.hierarchy_path}");

            int index = mesh.GetBlendShapeIndex(request.blend_shape_name);
            if (index < 0)
                return BuildError("EDITOR_CTRL_BLENDSHAPE_NOT_FOUND",
                    $"BlendShape not found: {request.blend_shape_name}");

            float before = smr.GetBlendShapeWeight(index);
            float weight = Mathf.Clamp(request.blend_shape_weight, 0f, 100f);

            Undo.RecordObject(smr, $"Set BlendShape {request.blend_shape_name}");
            smr.SetBlendShapeWeight(index, weight);

            SceneView sv = SceneView.lastActiveSceneView;
            if (sv != null) ForceRenderAndRepaint(sv);

            var resp = BuildSuccess("EDITOR_CTRL_BLEND_SHAPE_SET_OK",
                $"BlendShape '{request.blend_shape_name}' set from {before} to {weight}",
                data: new EditorControlData
                {
                    blend_shape_index = index,
                    blend_shape_name = request.blend_shape_name,
                    blend_shape_before = before,
                    blend_shape_after = weight,
                    executed = true,
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.RecordObject"
            }};
            return resp;
        }

        // ── Material reverse lookup ──

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

        // ── Rename + Reparent ──

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

        // ── UdonSharp idempotency helpers (issue #103) ──

        /// <summary>
        /// Resolve the UdonSharp.UdonSharpBehaviour type via reflection,
        /// returning null when UdonSharp is not present in the project.
        /// </summary>
        private static Type ResolveUdonSharpBehaviourType()
        {
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                Type t = assembly.GetType("UdonSharp.UdonSharpBehaviour", false);
                if (t != null) return t;
            }
            return null;
        }

        /// <summary>
        /// Resolve UdonSharpEditor.UdonSharpEditorUtility via reflection.
        /// </summary>
        private static Type ResolveUdonSharpEditorUtilityType()
        {
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                Type t = assembly.GetType("UdonSharpEditor.UdonSharpEditorUtility", false);
                if (t != null) return t;
            }
            return null;
        }

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
            catch
            {
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
            catch
            {
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

        // ── Add / Remove / SetProperty / Udon program asset ──

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

        private static EditorControlResponse HandleCreateUdonProgramAsset(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.asset_path))
                return BuildError("EDITOR_CTRL_UDON_NO_SCRIPT", "asset_path (.cs file) is required.");

            var script = AssetDatabase.LoadAssetAtPath<MonoScript>(request.asset_path);
            if (script == null)
                return BuildError("EDITOR_CTRL_UDON_SCRIPT_NOT_FOUND",
                    $"MonoScript not found: {request.asset_path}");

            // Resolve UdonSharpProgramAsset via reflection
            var assetType = System.Type.GetType(
                "UdonSharp.UdonSharpProgramAsset, UdonSharp.Editor");
            if (assetType == null)
                return BuildError("EDITOR_CTRL_UDON_NOT_AVAILABLE",
                    "UdonSharp.Editor not found. Is UdonSharp installed?");

            var asset = ScriptableObject.CreateInstance(assetType);

            var field = assetType.GetField("sourceCsScript",
                System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
                | System.Reflection.BindingFlags.Instance);
            if (field != null)
                field.SetValue(asset, script);

            // Output path: use description field if provided, otherwise derive from .cs path
            string outputPath = string.IsNullOrEmpty(request.description)
                ? request.asset_path.Replace(".cs", ".asset")
                : request.description;

            AssetDatabase.CreateAsset(asset, outputPath);
            AssetDatabase.SaveAssets();

            return BuildSuccess("EDITOR_CTRL_UDON_ASSET_CREATED",
                $"Created Udon Program Asset: {outputPath}",
                data: new EditorControlData
                {
                    output_path = outputPath,
                    asset_path = request.asset_path,
                    executed = true,
                });
        }

        private static EditorControlResponse HandleEditorSetProperty(EditorControlRequest request)
        {
            // ── Validation ──
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_SET_PROP_NO_PATH", "hierarchy_path is required.");
            if (string.IsNullOrEmpty(request.component_type))
                return BuildError("EDITOR_CTRL_SET_PROP_NO_COMP", "component_type is required.");
            if (string.IsNullOrEmpty(request.property_name))
                return BuildError("EDITOR_CTRL_SET_PROP_NO_FIELD", "property_name is required.");

            bool hasValue = !string.IsNullOrEmpty(request.property_value);
            bool hasRef = !string.IsNullOrEmpty(request.object_reference);
            if (!hasValue && !hasRef)
                return BuildError("EDITOR_CTRL_SET_PROP_NO_VALUE",
                    "Either property_value or object_reference is required.");
            if (hasValue && hasRef)
                return BuildError("EDITOR_CTRL_SET_PROP_BOTH_VALUE",
                    "Provide property_value or object_reference, not both.");

            // ── Resolve target ──
            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_SET_PROP_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            // GameObject-as-target branch: when the caller addresses the
            // GameObject itself (not a component on it), the SerializedObject
            // is constructed directly from the GameObject and writes are
            // restricted to the small allowlist of GameObject-level fields.
            bool gameObjectTarget =
                string.Equals(request.component_type, "GameObject", StringComparison.Ordinal);

            // GameObject-level serialized properties accepted by the
            // GameObject-as-target branch. Writes to anything outside this
            // allowlist return EDITOR_CTRL_SET_PROP_GAMEOBJECT_PROP_NOT_ALLOWED.
            string[] gameObjectAllowedProperties =
                new[] { "m_IsActive", "m_Layer", "m_Name", "m_TagString" };

            SerializedObject so;
            if (gameObjectTarget)
            {
                if (Array.IndexOf(gameObjectAllowedProperties, request.property_name) < 0)
                {
                    string allowed = string.Join(", ", gameObjectAllowedProperties);
                    return BuildError("EDITOR_CTRL_SET_PROP_GAMEOBJECT_PROP_NOT_ALLOWED",
                        $"GameObject-level property '{request.property_name}' is not allowed. " +
                        $"Allowed: {allowed}.");
                }
                so = new SerializedObject(go);
            }
            else
            {
                System.Type compType = ResolveComponentType(request.component_type);
                if (compType == null)
                    return BuildError("EDITOR_CTRL_SET_PROP_COMP_NOT_FOUND",
                        $"Component type not found: {request.component_type}");

                var component = go.GetComponent(compType);
                if (component == null)
                    return BuildError("EDITOR_CTRL_SET_PROP_COMP_NOT_FOUND",
                        $"Component {request.component_type} not found on {request.hierarchy_path}");

                so = new SerializedObject(component);
            }

            // ── Find property ──
            var prop = so.FindProperty(request.property_name);
            if (prop == null)
            {
                var candidates = new List<string>();
                var iter = so.GetIterator();
                if (iter.NextVisible(true))
                {
                    do
                    {
                        candidates.Add(iter.propertyPath);
                    } while (iter.NextVisible(false));
                }
                string[] suggestions = SuggestSimilar(
                    request.property_name, candidates, maxResults: 5);
                var data = new EditorControlData();
                data.suggestions = suggestions.Length > 0
                    ? suggestions
                    : Array.Empty<string>();
                string baseMessage = gameObjectTarget
                    ? $"Property not found: {request.property_name} on GameObject"
                    : $"Property not found: {request.property_name} on {request.component_type}";
                string message = data.suggestions.Length > 0
                    ? $"{baseMessage}. Did you mean: {string.Join(", ", data.suggestions)}?"
                    : baseMessage;
                return BuildError("EDITOR_CTRL_SET_PROP_FIELD_NOT_FOUND", message, data);
            }

            // ── Set value by type ──
            string v = request.property_value;
            try
            {
                switch (prop.propertyType)
                {
                    case SerializedPropertyType.Integer:
                        prop.intValue = int.Parse(v, System.Globalization.CultureInfo.InvariantCulture);
                        break;
                    case SerializedPropertyType.Float:
                        prop.floatValue = float.Parse(v, System.Globalization.CultureInfo.InvariantCulture);
                        break;
                    case SerializedPropertyType.Boolean:
                        prop.boolValue = bool.Parse(v);
                        break;
                    case SerializedPropertyType.String:
                        prop.stringValue = v;
                        break;
                    case SerializedPropertyType.Enum:
                    {
                        // enumNames returns internal C# names (preferred for programmatic input).
                        // enumDisplayNames (Unity 2021.1+) returns formatted display names which
                        // may contain spaces; unsuitable for API input.
#pragma warning disable 0618  // enumNames deprecated but intentionally used
                        int idx = System.Array.IndexOf(prop.enumNames, v);
                        if (idx >= 0)
                            prop.enumValueIndex = idx;
                        else if (int.TryParse(v, out int numIdx))
                            prop.enumValueIndex = numIdx;
                        else
                            return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                                $"Enum value '{v}' not found. Valid: {string.Join(", ", prop.enumNames)}");
#pragma warning restore 0618
                        break;
                    }
                    case SerializedPropertyType.Color:
                    {
                        var parts = v.Split(',');
                        if (parts.Length < 3)
                            return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                                "Color requires 3 or 4 comma-separated floats (r,g,b[,a]).");
                        float r = float.Parse(parts[0].Trim(), System.Globalization.CultureInfo.InvariantCulture);
                        float g = float.Parse(parts[1].Trim(), System.Globalization.CultureInfo.InvariantCulture);
                        float b = float.Parse(parts[2].Trim(), System.Globalization.CultureInfo.InvariantCulture);
                        float a = parts.Length >= 4
                            ? float.Parse(parts[3].Trim(), System.Globalization.CultureInfo.InvariantCulture)
                            : 1f;
                        prop.colorValue = new Color(r, g, b, a);
                        break;
                    }
                    case SerializedPropertyType.Vector2:
                    {
                        var parts = v.Split(',');
                        if (parts.Length < 2)
                            return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                                "Vector2 requires 2 comma-separated floats (x,y).");
                        prop.vector2Value = new Vector2(
                            float.Parse(parts[0].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[1].Trim(), System.Globalization.CultureInfo.InvariantCulture));
                        break;
                    }
                    case SerializedPropertyType.Vector3:
                    {
                        var parts = v.Split(',');
                        if (parts.Length < 3)
                            return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                                "Vector3 requires 3 comma-separated floats (x,y,z).");
                        prop.vector3Value = new Vector3(
                            float.Parse(parts[0].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[1].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[2].Trim(), System.Globalization.CultureInfo.InvariantCulture));
                        break;
                    }
                    case SerializedPropertyType.Vector4:
                    {
                        var parts = v.Split(',');
                        if (parts.Length < 4)
                            return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                                "Vector4 requires 4 comma-separated floats (x,y,z,w).");
                        prop.vector4Value = new Vector4(
                            float.Parse(parts[0].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[1].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[2].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[3].Trim(), System.Globalization.CultureInfo.InvariantCulture));
                        break;
                    }
                    case SerializedPropertyType.Quaternion:
                    {
                        // Issue #111: accept only the four-component xyzw form.
                        // Euler input is intentionally not supported here — the
                        // dedicated euler-hint property already covers that
                        // shape, and mixing two value shapes inside one type
                        // case obscures the contract.
                        var parts = v.Split(',');
                        if (parts.Length != 4)
                            return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                                "Quaternion requires exactly 4 comma-separated floats (x,y,z,w).");
                        float qx = float.Parse(parts[0].Trim(), System.Globalization.CultureInfo.InvariantCulture);
                        float qy = float.Parse(parts[1].Trim(), System.Globalization.CultureInfo.InvariantCulture);
                        float qz = float.Parse(parts[2].Trim(), System.Globalization.CultureInfo.InvariantCulture);
                        float qw = float.Parse(parts[3].Trim(), System.Globalization.CultureInfo.InvariantCulture);
                        // Norm tolerance 1e-4 — matches the precision we
                        // expect from float32 quaternion encodings emitted
                        // by Unity's Transform.localRotation. Tighter than
                        // Mathf.Approximately but still loose enough that
                        // round-tripping a serialized quaternion does not
                        // get rejected.
                        float norm = Mathf.Sqrt(qx * qx + qy * qy + qz * qz + qw * qw);
                        if (Mathf.Abs(norm - 1f) > 1e-4f)
                            return BuildError("EDITOR_CTRL_SET_PROP_QUATERNION_NOT_NORMALIZED",
                                $"Quaternion value (x={qx}, y={qy}, z={qz}, w={qw}) has norm {norm}; "
                                + "unit norm (1.0 ± 1e-4) is required. Normalize the input on the caller side.");
                        prop.quaternionValue = new Quaternion(qx, qy, qz, qw);
                        break;
                    }
                    case SerializedPropertyType.ArraySize:
                    case SerializedPropertyType.FixedBufferSize:
                        prop.intValue = int.Parse(v, System.Globalization.CultureInfo.InvariantCulture);
                        break;
                    case SerializedPropertyType.ObjectReference:
                    {
                        string refPath = hasRef ? request.object_reference : v;
                        var (obj, refError) = ResolveObjectReference(refPath);
                        if (obj == null)
                            return BuildError("EDITOR_CTRL_SET_PROP_REF_NOT_FOUND",
                                refError ?? $"Object reference not found: {refPath}");
                        prop.objectReferenceValue = obj;
                        break;
                    }
                    default:
                        return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                            $"Unsupported property type: {prop.propertyType}");
                }
            }
            catch (System.FormatException ex)
            {
                return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                    $"Failed to parse value '{v}' for {prop.propertyType}: {ex.Message}");
            }

            so.ApplyModifiedProperties();

            var resp = BuildSuccess("EDITOR_CTRL_SET_PROP_OK",
                $"Set {request.property_name} on {request.component_type} at {request.hierarchy_path}",
                data: new EditorControlData
                {
                    selected_object = go.name,
                    executed = true,
                    read_only = false,
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = $"Property type: {prop.propertyType}. Save the scene to persist.",
                evidence = "SerializedObject.ApplyModifiedProperties"
            }};
            return resp;
        }

        // ── Batch operations layered over the single-write handlers ──

        private static EditorControlResponse HandleEditorBatchSetProperty(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.batch_operations_json))
                return BuildError("EDITOR_CTRL_BATCH_SET_NO_DATA", "batch_operations_json is required.");

            BatchSetPropertyArray wrapper;
            try
            {
                wrapper = JsonUtility.FromJson<BatchSetPropertyArray>(
                    "{\"items\":" + request.batch_operations_json + "}");
            }
            catch (System.Exception ex)
            {
                return BuildError("EDITOR_CTRL_BATCH_SET_JSON_ERROR",
                    $"Failed to parse batch_operations_json: {ex.Message}");
            }

            if (wrapper.items == null || wrapper.items.Length == 0)
                return BuildError("EDITOR_CTRL_BATCH_SET_EMPTY", "batch_operations_json is empty.");

            int undoGroup = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("PrefabSentinel: Batch SetProperty");

            var results = new List<string>();

            foreach (var op in wrapper.items)
            {
                var subReq = new EditorControlRequest
                {
                    action = "editor_set_property",
                    hierarchy_path = op.hierarchy_path,
                    component_type = op.component_type,
                    property_name = op.property_name,
                    property_value = op.value,
                    object_reference = op.object_reference,
                };
                var subResp = HandleEditorSetProperty(subReq);
                if (!subResp.success)
                {
                    Undo.CollapseUndoOperations(undoGroup);
                    return BuildError("EDITOR_CTRL_BATCH_SET_FAILED",
                        $"Operation failed at index {results.Count}: {subResp.message}");
                }
                results.Add($"{op.hierarchy_path}/{op.component_type}.{op.property_name}");
            }

            Undo.CollapseUndoOperations(undoGroup);

            var batchSetResp = BuildSuccess("EDITOR_CTRL_BATCH_SET_OK",
                $"Set {results.Count} properties",
                data: new EditorControlData
                {
                    executed = true,
                    read_only = false,
                    suggestions = results.ToArray(),
                });
            batchSetResp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.CollapseUndoOperations"
            }};
            return batchSetResp;
        }

        private static EditorControlResponse HandleEditorBatchSetMaterialProperty(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.batch_operations_json))
                return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_NO_DATA",
                    "batch_operations_json is required.");

            BatchSetMaterialPropertyArray wrapper;
            try
            {
                wrapper = JsonUtility.FromJson<BatchSetMaterialPropertyArray>(
                    "{\"items\":" + request.batch_operations_json + "}");
            }
            catch (System.Exception ex)
            {
                return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_JSON_ERROR",
                    $"Failed to parse batch_operations_json: {ex.Message}");
            }

            if (wrapper.items == null || wrapper.items.Length == 0)
                return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_EMPTY",
                    "batch_operations_json is empty.");

            bool hasHierarchy = !string.IsNullOrEmpty(request.hierarchy_path);
            bool hasMatPath = !string.IsNullOrEmpty(request.material_path);
            bool hasMatGuid = !string.IsNullOrEmpty(request.material_guid);

            if (hasHierarchy && (hasMatPath || hasMatGuid))
                return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_CONFLICT",
                    "Cannot specify both hierarchy_path and material_path/material_guid. Use one targeting mode.");

            if (!hasHierarchy && !hasMatPath && !hasMatGuid)
                return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_NO_TARGET",
                    "Specify a target: hierarchy_path + material_index, or material_path, or material_guid.");

            int undoGroup = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("PrefabSentinel: Batch SetMaterialProperty");

            var results = new List<string>();

            if (hasHierarchy)
            {
                foreach (var op in wrapper.items)
                {
                    var subReq = new EditorControlRequest
                    {
                        action = "set_material_property",
                        hierarchy_path = request.hierarchy_path,
                        material_index = request.material_index,
                        property_name = op.name,
                        property_value = op.value,
                    };
                    var subResp = HandleSetMaterialProperty(subReq);
                    if (!subResp.success)
                    {
                        Undo.CollapseUndoOperations(undoGroup);
                        return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_FAILED",
                            $"Operation failed at index {results.Count}: {subResp.message}");
                    }
                    results.Add(op.name);
                }
            }
            else
            {
                if (hasMatPath && hasMatGuid)
                    return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_MAT_CONFLICT",
                        "Cannot specify both material_path and material_guid. Use one.");

                string guid = request.material_guid;
                if (hasMatPath)
                {
                    guid = AssetDatabase.AssetPathToGUID(request.material_path);
                    if (string.IsNullOrEmpty(guid))
                        return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_NOT_FOUND",
                            $"Material not found at path: {request.material_path}");
                }

                string assetPath = AssetDatabase.GUIDToAssetPath(guid);
                if (string.IsNullOrEmpty(assetPath))
                    return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_NOT_FOUND",
                        $"No asset found for GUID: {guid}");

                var mat = AssetDatabase.LoadAssetAtPath<Material>(assetPath);
                if (mat == null)
                    return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_NOT_FOUND",
                        $"Failed to load Material at: {assetPath}");

                var shader = mat.shader;
                if (shader == null)
                    return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_NOT_FOUND",
                        $"Material '{mat.name}' has no shader assigned.");

                Undo.RecordObject(mat, "PrefabSentinel: Batch SetMaterialProperty");

                foreach (var op in wrapper.items)
                {
                    var applyError = ApplyMaterialPropertyValue(mat, op.name, op.value);
                    if (applyError != null)
                    {
                        Undo.CollapseUndoOperations(undoGroup);
                        return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_FAILED",
                            $"Operation failed at index {results.Count}: {applyError.message}");
                    }
                    results.Add(op.name);
                }

                SceneView sv = SceneView.lastActiveSceneView;
                if (sv != null) ForceRenderAndRepaint(sv);
            }

            Undo.CollapseUndoOperations(undoGroup);

            var resp = BuildSuccess("EDITOR_CTRL_BATCH_SET_MAT_PROP_OK",
                $"Set {results.Count} material properties",
                data: new EditorControlData
                {
                    executed = true, read_only = false,
                    suggestions = results.ToArray(),
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.CollapseUndoOperations"
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

        // ── Menu enumeration / execution ──

        private static readonly string[] MenuDenyPrefixes = new string[]
        {
            "File/New Scene",
            "File/New Project",
            "Assets/Delete",
        };

        private static EditorControlResponse HandleListMenuItems(EditorControlRequest request)
        {
            string prefix = request.filter ?? "";
            var items = new List<MenuItemEntry>();
            int totalScanned = 0;  // pre-filter count (all non-validate [MenuItem])

            foreach (var assembly in System.AppDomain.CurrentDomain.GetAssemblies())
            {
                System.Type[] types;
                try
                {
                    types = assembly.GetTypes();
                }
                catch (System.Reflection.ReflectionTypeLoadException ex)
                {
                    types = System.Array.FindAll(ex.Types, t => t != null);
                }

                foreach (var type in types)
                {
                    var methods = type.GetMethods(
                        System.Reflection.BindingFlags.Static |
                        System.Reflection.BindingFlags.Public |
                        System.Reflection.BindingFlags.NonPublic);

                    foreach (var method in methods)
                    {
                        var attrs = method.GetCustomAttributes(typeof(UnityEditor.MenuItem), false);
                        foreach (UnityEditor.MenuItem attr in attrs)
                        {
                            if (attr.validate)
                                continue;

                            totalScanned++;
                            string menuPath = attr.menuItem;
                            if (prefix.Length > 0 && !menuPath.StartsWith(prefix, System.StringComparison.Ordinal))
                                continue;

                            items.Add(new MenuItemEntry
                            {
                                path = menuPath,
                                shortcut = ExtractShortcut(menuPath),
                            });
                        }
                    }
                }
            }

            items.Sort((a, b) => string.Compare(a.path, b.path, System.StringComparison.Ordinal));

            return BuildSuccess("EDITOR_CTRL_MENU_LIST_OK",
                $"Found {items.Count} menu items (total: {totalScanned})",
                data: new EditorControlData
                {
                    menu_items = items.ToArray(),
                    total_entries = totalScanned,
                    read_only = true,
                    executed = true,
                });
        }

        /// <summary>Extract keyboard shortcut from MenuItem path (e.g. "Tools/Foo %t" → "%t").</summary>
        private static string ExtractShortcut(string menuPath)
        {
            // Unity shortcut chars: % (Cmd/Ctrl), # (Shift), & (Alt), _ (no modifier)
            int spaceIdx = menuPath.LastIndexOf(' ');
            if (spaceIdx < 0) return "";
            string candidate = menuPath.Substring(spaceIdx + 1);
            if (candidate.Length > 0 && (candidate[0] == '%' || candidate[0] == '#' || candidate[0] == '&' || candidate[0] == '_'))
                return candidate;
            return "";
        }

        private static EditorControlResponse HandleExecuteMenuItem(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.menu_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "menu_path is required for execute_menu_item");

            foreach (var denied in MenuDenyPrefixes)
            {
                if (request.menu_path.StartsWith(denied, System.StringComparison.Ordinal))
                    return BuildError("EDITOR_CTRL_MENU_DENIED",
                        $"Menu item denied by safety policy: {request.menu_path}");
            }

            bool result = EditorApplication.ExecuteMenuItem(request.menu_path);
            if (!result)
                return BuildError("EDITOR_CTRL_MENU_NOT_FOUND",
                    $"Menu item not found or not executable: {request.menu_path}");

            return BuildSuccess("EDITOR_CTRL_MENU_EXEC_OK",
                $"Menu item executed: {request.menu_path}");
        }

        // ── Shared property write / object resolution helpers ──

        private static bool ApplyPropertyValue(SerializedProperty prop, string v)
        {
            var ci = System.Globalization.CultureInfo.InvariantCulture;
            switch (prop.propertyType)
            {
                case SerializedPropertyType.Integer:
                    if (int.TryParse(v, System.Globalization.NumberStyles.Integer, ci, out int iv))
                    { prop.intValue = iv; return true; }
                    return false;
                case SerializedPropertyType.Float:
                    if (float.TryParse(v, System.Globalization.NumberStyles.Float, ci, out float fv))
                    { prop.floatValue = fv; return true; }
                    return false;
                case SerializedPropertyType.Boolean:
                    if (bool.TryParse(v, out bool bv))
                    { prop.boolValue = bv; return true; }
                    return false;
                case SerializedPropertyType.String:
                    prop.stringValue = v; return true;
                case SerializedPropertyType.Enum:
                {
#pragma warning disable 0618
                    int idx = System.Array.IndexOf(prop.enumNames, v);
#pragma warning restore 0618
                    if (idx >= 0) { prop.enumValueIndex = idx; return true; }
                    if (int.TryParse(v, out int ei)) { prop.enumValueIndex = ei; return true; }
                    return false;
                }
                case SerializedPropertyType.Vector3:
                {
                    var parts = v.Split(',');
                    if (parts.Length >= 3
                        && float.TryParse(parts[0].Trim(), System.Globalization.NumberStyles.Float, ci, out float x)
                        && float.TryParse(parts[1].Trim(), System.Globalization.NumberStyles.Float, ci, out float y)
                        && float.TryParse(parts[2].Trim(), System.Globalization.NumberStyles.Float, ci, out float z))
                    { prop.vector3Value = new Vector3(x, y, z); return true; }
                    return false;
                }
                case SerializedPropertyType.Color:
                {
                    var parts = v.Split(',');
                    if (parts.Length < 3) return false;
                    if (!float.TryParse(parts[0].Trim(), System.Globalization.NumberStyles.Float, ci, out float r)
                        || !float.TryParse(parts[1].Trim(), System.Globalization.NumberStyles.Float, ci, out float g)
                        || !float.TryParse(parts[2].Trim(), System.Globalization.NumberStyles.Float, ci, out float b))
                        return false;
                    float a = 1f;
                    if (parts.Length >= 4
                        && float.TryParse(parts[3].Trim(), System.Globalization.NumberStyles.Float, ci, out float aParsed))
                        a = aParsed;
                    prop.colorValue = new Color(r, g, b, a);
                    return true;
                }
                case SerializedPropertyType.Vector2:
                {
                    var parts = v.Split(',');
                    if (parts.Length >= 2
                        && float.TryParse(parts[0].Trim(), System.Globalization.NumberStyles.Float, ci, out float x)
                        && float.TryParse(parts[1].Trim(), System.Globalization.NumberStyles.Float, ci, out float y))
                    { prop.vector2Value = new Vector2(x, y); return true; }
                    return false;
                }
                case SerializedPropertyType.Vector4:
                {
                    var parts = v.Split(',');
                    if (parts.Length >= 4
                        && float.TryParse(parts[0].Trim(), System.Globalization.NumberStyles.Float, ci, out float x)
                        && float.TryParse(parts[1].Trim(), System.Globalization.NumberStyles.Float, ci, out float y)
                        && float.TryParse(parts[2].Trim(), System.Globalization.NumberStyles.Float, ci, out float z)
                        && float.TryParse(parts[3].Trim(), System.Globalization.NumberStyles.Float, ci, out float w))
                    { prop.vector4Value = new Vector4(x, y, z, w); return true; }
                    return false;
                }
                case SerializedPropertyType.ObjectReference:
                {
                    var (obj, _) = ResolveObjectReference(v);
                    if (obj != null)
                    { prop.objectReferenceValue = obj; return true; }
                    return false;
                }
                case SerializedPropertyType.ArraySize:
                case SerializedPropertyType.FixedBufferSize:
                    if (int.TryParse(v, System.Globalization.NumberStyles.Integer, ci, out int av))
                    { prop.intValue = av; return true; }
                    return false;
                default: return false;
            }
        }


        private static System.Type ResolveComponentType(string typeName)
        {
            // 1. Fully qualified name (fastest path)
            var t = System.Type.GetType(typeName);
            if (t != null && typeof(Component).IsAssignableFrom(t))
                return t;

            // 2. Search all loaded assemblies by full name
            foreach (var asm in System.AppDomain.CurrentDomain.GetAssemblies())
            {
                t = asm.GetType(typeName);
                if (t != null && typeof(Component).IsAssignableFrom(t))
                    return t;
            }

            // 3. Search all loaded assemblies by simple name (handles short names
            //    like "BoxCollider" that live in UnityEngine.PhysicsModule etc.)
            //    First match wins; use fully qualified name to disambiguate.
            foreach (var asm in System.AppDomain.CurrentDomain.GetAssemblies())
            {
                System.Type[] exported;
                try
                {
                    exported = asm.GetExportedTypes();
                }
                catch (System.Reflection.ReflectionTypeLoadException ex)
                {
                    exported = System.Array.FindAll(ex.Types, t => t != null);
                }

                foreach (var type in exported)
                {
                    if (type.Name == typeName && typeof(Component).IsAssignableFrom(type))
                        return type;
                }
            }

            return null;
        }

        private static (UnityEngine.Object obj, string error) ResolveObjectReference(string reference)
        {
            if (string.IsNullOrEmpty(reference))
                return (null, "object_reference is empty.");

            // 1. Check for component specifier (path:ComponentType)
            string goPath = reference;
            string componentName = null;
            int colonIdx = reference.LastIndexOf(':');
            if (colonIdx > 0)
            {
                goPath = reference.Substring(0, colonIdx);
                componentName = reference.Substring(colonIdx + 1);
            }

            // 2. Try scene hierarchy
            var go = GameObject.Find(goPath);
            if (go != null)
            {
                if (componentName != null)
                {
                    var compType = ResolveComponentType(componentName);
                    if (compType == null)
                        return (null, $"Component type not found: {componentName}");
                    var comp = go.GetComponent(compType);
                    if (comp == null)
                        return (null, $"GameObject '{goPath}' has no {componentName} component.");
                    return (comp, null);
                }
                return (go, null);
            }

            // 3. Try asset path
            var asset = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(reference);
            if (asset != null)
                return (asset, null);

            return (null, $"Not found in scene hierarchy or project assets: {reference}");
        }
    }
}
