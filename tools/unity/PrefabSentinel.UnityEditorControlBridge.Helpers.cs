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

            // 2. Try scene hierarchy via the Prefab-Stage-aware helper.
            //    Issue #258: when a Prefab Stage is open, ``GameObject.Find``
            //    silently returns null for hierarchies inside the stage
            //    because Unity isolates stage contents from the global
            //    scene-search.  ``ResolveGameObjectInActiveStage`` consults
            //    the stage root first and transparently falls through to
            //    ``GameObject.Find`` when no stage is active, so the
            //    out-of-stage contract is preserved.
            var go = ResolveGameObjectInActiveStage(goPath);
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
            // Issue H-4: the Enum and ObjectReference cases need Unity
            // reflection / object resolution and stay inline; every other
            // case delegates textual parsing to the Unity-free
            // ``PropertyValueParser`` and applies the parsed result here.
            switch (prop.propertyType)
            {
                case SerializedPropertyType.Enum:
                {
#pragma warning disable 0618
                    int idx = System.Array.IndexOf(prop.enumNames, v);
#pragma warning restore 0618
                    if (idx >= 0) { prop.enumValueIndex = idx; return true; }
                    if (int.TryParse(v, out int ei)) { prop.enumValueIndex = ei; return true; }
                    return false;
                }
                case SerializedPropertyType.ObjectReference:
                {
                    var (obj, _) = ResolveObjectReference(v);
                    if (obj != null)
                    { prop.objectReferenceValue = obj; return true; }
                    return false;
                }
            }

            if (!TryMapSerializedPropertyKind(prop.propertyType, out SerializedPropertyKind kind))
                return false;
            if (!PropertyValueParser.TryParse(kind, v, out ParsedPropertyValue parsed))
                return false;

            switch (kind)
            {
                case SerializedPropertyKind.Integer:
                case SerializedPropertyKind.IntSize:
                    prop.intValue = parsed.IntValue;
                    return true;
                case SerializedPropertyKind.Float:
                    prop.floatValue = parsed.FloatValue;
                    return true;
                case SerializedPropertyKind.Boolean:
                    prop.boolValue = parsed.BoolValue;
                    return true;
                case SerializedPropertyKind.String:
                    prop.stringValue = parsed.StringValue;
                    return true;
                case SerializedPropertyKind.Vector2:
                    prop.vector2Value = new Vector2(
                        parsed.Components[0], parsed.Components[1]);
                    return true;
                case SerializedPropertyKind.Vector3:
                    prop.vector3Value = new Vector3(
                        parsed.Components[0], parsed.Components[1], parsed.Components[2]);
                    return true;
                case SerializedPropertyKind.Vector4:
                    prop.vector4Value = new Vector4(
                        parsed.Components[0], parsed.Components[1],
                        parsed.Components[2], parsed.Components[3]);
                    return true;
                case SerializedPropertyKind.Color:
                    prop.colorValue = new Color(
                        parsed.Components[0], parsed.Components[1],
                        parsed.Components[2], parsed.Components[3]);
                    return true;
            }
            return false;
        }

        /// <summary>
        /// Map a live <see cref="SerializedPropertyType"/> to the Unity-free
        /// <see cref="SerializedPropertyKind"/> the parser understands.
        /// Returns false for types the parser does not cover (Enum and
        /// ObjectReference are handled inline by the caller).
        /// </summary>
        private static bool TryMapSerializedPropertyKind(
            SerializedPropertyType type, out SerializedPropertyKind kind)
        {
            switch (type)
            {
                case SerializedPropertyType.Integer:
                    kind = SerializedPropertyKind.Integer; return true;
                case SerializedPropertyType.Float:
                    kind = SerializedPropertyKind.Float; return true;
                case SerializedPropertyType.Boolean:
                    kind = SerializedPropertyKind.Boolean; return true;
                case SerializedPropertyType.String:
                    kind = SerializedPropertyKind.String; return true;
                case SerializedPropertyType.Vector2:
                    kind = SerializedPropertyKind.Vector2; return true;
                case SerializedPropertyType.Vector3:
                    kind = SerializedPropertyKind.Vector3; return true;
                case SerializedPropertyType.Vector4:
                    kind = SerializedPropertyKind.Vector4; return true;
                case SerializedPropertyType.Color:
                    kind = SerializedPropertyKind.Color; return true;
                case SerializedPropertyType.ArraySize:
                case SerializedPropertyType.FixedBufferSize:
                    kind = SerializedPropertyKind.IntSize; return true;
                default:
                    kind = default;
                    return false;
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
