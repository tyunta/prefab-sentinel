using UnityEditor;
using UnityEngine;

namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
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
            catch (System.FormatException ex)
            {
                return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                    $"Failed to parse value '{val}' for {propType}: {ex.Message}");
            }

            return null;
        }
    }
}
