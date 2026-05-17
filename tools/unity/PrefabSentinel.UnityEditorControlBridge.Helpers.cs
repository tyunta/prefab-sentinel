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
