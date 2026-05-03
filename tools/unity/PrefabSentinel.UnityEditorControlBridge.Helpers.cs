using System;
using System.Reflection;
using UnityEditor;
using UnityEngine;

// Shared component/object/property resolution helpers and UdonSharp program-asset creation.
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
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
    }
}
