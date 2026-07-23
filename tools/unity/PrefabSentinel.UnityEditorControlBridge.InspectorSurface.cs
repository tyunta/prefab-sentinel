using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private static bool TryValidateInspectorAssetPath(
            EditorControlRequest request,
            out string normalized)
        {
            normalized = request.asset_path;
            if (string.IsNullOrWhiteSpace(normalized)
                || Path.IsPathRooted(normalized)
                || normalized.IndexOf('\\') >= 0
                || !normalized.StartsWith("Assets/", StringComparison.Ordinal))
                return false;
            string[] segments = normalized.Split('/');
            foreach (string segment in segments)
            {
                if (string.IsNullOrWhiteSpace(segment)
                    || segment == "."
                    || segment == "..")
                    return false;
            }

            try
            {
                string currentRoot = Path.GetFullPath(CurrentProjectRoot())
                    .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                string expectedRoot = Path.GetFullPath(request.expected_project_root)
                    .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                if (!string.Equals(currentRoot, expectedRoot, StringComparison.OrdinalIgnoreCase))
                    return false;
                string assetsRoot = Path.GetFullPath(Application.dataPath)
                    .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                string fullPath = Path.GetFullPath(Path.Combine(currentRoot, normalized));
                return fullPath.StartsWith(
                    assetsRoot + Path.DirectorySeparatorChar,
                    StringComparison.OrdinalIgnoreCase);
            }
            catch (ArgumentException)
            {
                return false;
            }
            catch (NotSupportedException)
            {
                return false;
            }
            catch (PathTooLongException)
            {
                return false;
            }
            catch (System.Security.SecurityException)
            {
                return false;
            }
        }

        private static EditorControlResponse HandleInspectSerializedSurface(
            EditorControlRequest request)
        {
            if (!TryValidateInspectorAssetPath(request, out string assetPath))
                return InspectorSurfaceAddressError(
                    "asset_path must be a project-relative path below the activated project's Assets directory.");
            string extension = Path.GetExtension(assetPath).ToLowerInvariant();
            if ((extension == ".prefab" || extension == ".unity")
                && string.IsNullOrWhiteSpace(request.symbol_path))
                return InspectorSurfaceAddressError(
                    "symbol_path is required for component assets.");
            if (extension == ".asset" && !string.IsNullOrWhiteSpace(request.symbol_path))
                return InspectorSurfaceAddressError(
                    "symbol_path is not allowed for ScriptableObject assets.");

            if (extension == ".prefab")
                return InspectPrefabSerializedSurface(request);
            if (extension == ".unity")
                return InspectSceneSerializedSurface(request);
            if (extension == ".asset")
                return InspectScriptableObjectSurface(request);
            return InspectorSurfaceAddressError(
                "asset_path must identify a Prefab, Scene, or ScriptableObject asset.");
        }

        private static EditorControlResponse InspectPrefabSerializedSurface(
            EditorControlRequest request)
        {
            GameObject root = null;
            try
            {
                root = PrefabUtility.LoadPrefabContents(request.asset_path);
                UnityEngine.Object target = ResolveInspectorComponent(
                    new[] { root }, request.symbol_path);
                return target == null
                    ? InspectorSurfaceTargetNotFound()
                    : BuildInspectorSurfaceResponse(request, target);
            }
            finally
            {
                if (root != null) PrefabUtility.UnloadPrefabContents(root);
            }
        }

        private static EditorControlResponse InspectSceneSerializedSurface(
            EditorControlRequest request)
        {
            Scene scene = default(Scene);
            try
            {
                // Unity 2022.3 has no OpenPreviewScene API. Open additively so
                // the caller's active Scene remains loaded, then always remove
                // this last-saved inspection Scene without saving it.
                scene = EditorSceneManager.OpenScene(
                    request.asset_path, OpenSceneMode.Additive);
                UnityEngine.Object target = ResolveInspectorComponent(
                    scene.GetRootGameObjects(), request.symbol_path);
                return target == null
                    ? InspectorSurfaceTargetNotFound()
                    : BuildInspectorSurfaceResponse(request, target);
            }
            finally
            {
                if (scene.IsValid()) EditorSceneManager.CloseScene(scene, true);
            }
        }

        private static EditorControlResponse InspectScriptableObjectSurface(
            EditorControlRequest request)
        {
            ScriptableObject target =
                AssetDatabase.LoadAssetAtPath<ScriptableObject>(request.asset_path);
            if (target == null)
                return InspectorSurfaceTargetNotFound();
            if (EditorUtility.IsDirty(target))
            {
                return BuildError(
                    "EDITOR_CTRL_INSPECTOR_SURFACE_DIRTY",
                    "The ScriptableObject asset has unsaved changes; save or discard them "
                    + "before requesting its last-saved serialized surface.");
            }
            return BuildInspectorSurfaceResponse(request, target);
        }

        private static EditorControlResponse InspectorSurfaceAddressError(string message)
        {
            return BuildError("EDITOR_CTRL_INSPECTOR_ADDRESS_INVALID", message);
        }

        private static EditorControlResponse InspectorSurfaceTargetNotFound()
        {
            return BuildError(
                "EDITOR_CTRL_INSPECTOR_TARGET_NOT_FOUND",
                "The requested serialized target was not found.");
        }

        private static UnityEngine.Object ResolveInspectorComponent(
            IReadOnlyList<GameObject> roots,
            string symbolPath)
        {
            string[] segments = symbolPath.Split('/');
            if (segments.Length < 2) return null;

            Transform current = SelectInspectorTransform(roots, segments[0]);
            for (int i = 1; current != null && i < segments.Length - 1; i++)
                current = SelectInspectorTransform(current, segments[i]);
            return current == null
                ? null
                : SelectInspectorComponent(current.gameObject, segments[segments.Length - 1]);
        }

        private static Transform SelectInspectorTransform(
            IReadOnlyList<GameObject> roots,
            string segment)
        {
            var candidates = new List<Transform>();
            for (int i = 0; i < roots.Count; i++)
            {
                if (roots[i] != null) candidates.Add(roots[i].transform);
            }
            return SelectInspectorTransform(candidates, segment);
        }

        private static Transform SelectInspectorTransform(Transform parent, string segment)
        {
            var candidates = new List<Transform>();
            for (int i = 0; i < parent.childCount; i++)
                candidates.Add(parent.GetChild(i));
            return SelectInspectorTransform(candidates, segment);
        }

        private static Transform SelectInspectorTransform(
            IReadOnlyList<Transform> candidates,
            string segment)
        {
            SymbolPathResolver.ParseSegment(segment, out string name, out int index);
            var matches = new List<Transform>();
            for (int i = 0; i < candidates.Count; i++)
            {
                if (string.Equals(candidates[i].name, name, StringComparison.Ordinal))
                    matches.Add(candidates[i]);
            }
            if (index >= 0) return index < matches.Count ? matches[index] : null;
            return matches.Count == 1 ? matches[0] : null;
        }

        private static Component SelectInspectorComponent(GameObject gameObject, string segment)
        {
            SymbolPathResolver.ParseSegment(segment, out string selector, out int index);
            var matches = new List<Component>();
            Component[] components = gameObject.GetComponents<Component>();
            for (int i = 0; i < components.Length; i++)
            {
                Component component = components[i];
                if (component != null && InspectorComponentMatches(component, selector))
                    matches.Add(component);
            }
            if (index >= 0) return index < matches.Count ? matches[index] : null;
            return matches.Count == 1 ? matches[0] : null;
        }

        private static bool InspectorComponentMatches(Component component, string selector)
        {
            const string prefix = "MonoBehaviour(";
            if (selector.StartsWith(prefix, StringComparison.Ordinal)
                && selector.EndsWith(")", StringComparison.Ordinal))
            {
                MonoBehaviour behaviour = component as MonoBehaviour;
                if (behaviour == null) return false;
                MonoScript script = MonoScript.FromMonoBehaviour(behaviour);
                if (script == null) return false;
                string inner = selector.Substring(prefix.Length, selector.Length - prefix.Length - 1);
                if (inner.StartsWith("guid:", StringComparison.Ordinal))
                {
                    string guid = AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(script));
                    return guid.StartsWith(inner.Substring(5), StringComparison.OrdinalIgnoreCase);
                }
                return string.Equals(script.name, inner, StringComparison.Ordinal);
            }
            Type type = component.GetType();
            return string.Equals(type.Name, selector, StringComparison.Ordinal)
                || string.Equals(type.FullName, selector, StringComparison.Ordinal);
        }

        private static EditorControlResponse BuildInspectorSurfaceResponse(
            EditorControlRequest request,
            UnityEngine.Object target)
        {
            string json = BuildInspectorSurfaceJson(target, request.include_override_origin);
            return BuildSuccess(
                "EDITOR_CTRL_INSPECTOR_SURFACE_OK",
                "Serialized surface inspected.",
                new EditorControlData
                {
                    asset_path = request.asset_path,
                    read_only = true,
                    executed = false,
                    serialized_surface_json = json,
                });
        }

    }
}
