using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

// Material slot enumeration and shader-property read handlers.
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
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

        private static List<string> CollectShaderPropertyNames(Shader shader)
        {
            var names = new List<string>();
            int count = shader.GetPropertyCount();
            for (int i = 0; i < count; i++)
                names.Add(shader.GetPropertyName(i));
            return names;
        }
    }
}
