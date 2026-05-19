using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using UnityEditor;
using UnityEngine;
namespace PrefabSentinel
{
    public static partial class UnityPatchBridge
    {
        private static string NormalizeHandle(string raw)
        {
            string normalized = (raw ?? string.Empty).Trim();
            if (normalized.StartsWith("$", StringComparison.Ordinal))
            {
                normalized = normalized.Substring(1);
            }
            return normalized.Trim();
        }
        private static bool TryRegisterHandle(
            string rawHandle,
            UnityEngine.Object obj,
            Dictionary<string, UnityEngine.Object> handles,
            string requestTarget,
            int opIndex,
            List<BridgeDiagnostic> diagnostics
        )
        {
            string handle = NormalizeHandle(rawHandle);
            if (string.IsNullOrWhiteSpace(handle))
            {
                return true;
            }
            if (handles.ContainsKey(handle))
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = requestTarget,
                        location = $"ops[{opIndex}].result",
                        detail = "schema_error",
                        evidence = $"handle '{handle}' is already defined"
                    }
                );
                return false;
            }
            handles[handle] = obj;
            return true;
        }
        private static bool TryResolveHandle(
            string rawHandle,
            Dictionary<string, UnityEngine.Object> handles,
            out UnityEngine.Object obj,
            out string error
        )
        {
            obj = null;
            string handle = NormalizeHandle(rawHandle);
            if (string.IsNullOrWhiteSpace(handle))
            {
                error = "handle is required";
                return false;
            }
            if (!handles.TryGetValue(handle, out obj) || obj == null)
            {
                error = $"unknown handle '{handle}'";
                return false;
            }
            error = string.Empty;
            return true;
        }
        private static bool TryResolveGameObjectHandle(
            string rawHandle,
            Dictionary<string, UnityEngine.Object> handles,
            out GameObject obj,
            out string error
        )
        {
            obj = null;
            UnityEngine.Object handleObject;
            if (!TryResolveHandle(rawHandle, handles, out handleObject, out error))
            {
                return false;
            }
            obj = handleObject as GameObject;
            if (obj == null)
            {
                error = $"handle '{NormalizeHandle(rawHandle)}' does not reference a GameObject";
                return false;
            }
            return true;
        }
        private static bool TryResolveComponentHandle(
            string rawHandle,
            Dictionary<string, UnityEngine.Object> handles,
            out Component component,
            out string error
        )
        {
            component = null;
            UnityEngine.Object handleObject;
            if (!TryResolveHandle(rawHandle, handles, out handleObject, out error))
            {
                return false;
            }
            component = handleObject as Component;
            if (component == null)
            {
                error = $"handle '{NormalizeHandle(rawHandle)}' does not reference a Component";
                return false;
            }
            return true;
        }
        private static bool TryResolveAssetHandle(
            string rawHandle,
            Dictionary<string, UnityEngine.Object> handles,
            out UnityEngine.Object assetObject,
            out string error
        )
        {
            assetObject = null;
            UnityEngine.Object handleObject;
            if (!TryResolveHandle(rawHandle, handles, out handleObject, out error))
            {
                return false;
            }
            if (handleObject is GameObject || handleObject is Component)
            {
                error = $"handle '{NormalizeHandle(rawHandle)}' does not reference an asset";
                return false;
            }
            assetObject = handleObject;
            return true;
        }
        private static bool TryResolveSceneParentHandle(
            string rawHandle,
            Dictionary<string, UnityEngine.Object> handles,
            out GameObject parentObject,
            out bool isSceneRoot,
            out string error
        )
        {
            parentObject = null;
            isSceneRoot = false;
            string normalized = NormalizeHandle(rawHandle);
            if (string.Equals(normalized, SceneHandleName, StringComparison.Ordinal))
            {
                error = string.Empty;
                isSceneRoot = true;
                return true;
            }
            return TryResolveGameObjectHandle(rawHandle, handles, out parentObject, out error);
        }
        private static bool TrySetupUdonSharpBacking(
            GameObject targetObject,
            Component addedComponent,
            Type componentType,
            Dictionary<string, UnityEngine.Object> handles,
            string handleName,
            string requestTarget,
            int opIndex,
            List<BridgeDiagnostic> diagnostics
        )
        {
            Type usbType = null;
            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                usbType = assembly.GetType("UdonSharp.UdonSharpBehaviour", false);
                if (usbType != null) break;
            }
            if (usbType == null || !usbType.IsAssignableFrom(componentType))
            {
                return true;
            }

            Type udonBehaviourType = null;
            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                udonBehaviourType = assembly.GetType("VRC.Udon.UdonBehaviour", false);
                if (udonBehaviourType != null) break;
            }
            if (udonBehaviourType == null)
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = requestTarget,
                        location = $"ops[{opIndex}]",
                        detail = "apply_error",
                        evidence = "UdonSharpBehaviour detected but VRC.Udon.UdonBehaviour type not found"
                    }
                );
                return false;
            }

            Component backing = targetObject.AddComponent(udonBehaviourType);
            if (backing == null)
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = requestTarget,
                        location = $"ops[{opIndex}]",
                        detail = "apply_error",
                        evidence = "Failed to create backing UdonBehaviour"
                    }
                );
                return false;
            }

            SerializedObject usbSerialized = new SerializedObject(addedComponent);
            SerializedProperty backingProp = usbSerialized.FindProperty("_udonSharpBackingUdonBehaviour");
            if (backingProp != null)
            {
                backingProp.objectReferenceValue = backing;
                usbSerialized.ApplyModifiedPropertiesWithoutUndo();
            }

            Type programAssetType = null;
            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
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
                            if (script != null && script.GetClass() == componentType)
                            {
                                SerializedObject backingSO = new SerializedObject(backing);
                                SerializedProperty programSourceProp =
                                    backingSO.FindProperty("programSource");
                                if (programSourceProp != null)
                                {
                                    programSourceProp.objectReferenceValue =
                                        program as UnityEngine.Object;
                                    backingSO.ApplyModifiedPropertiesWithoutUndo();
                                }
                                break;
                            }
                        }
                    }
                }
            }

            if (!string.IsNullOrWhiteSpace(handleName))
            {
                string backingHandle = $"backing_{handleName}";
                if (!handles.ContainsKey(backingHandle))
                {
                    handles[backingHandle] = backing;
                }
            }

            return true;
        }
        private static bool TryResolveComponentType(
            string rawTypeName,
            out Type componentType,
            out string error
        )
        {
            componentType = null;
            error = string.Empty;
            Type resolvedType;
            if (!TryResolveType(rawTypeName, out resolvedType, out error))
            {
                return false;
            }
            if (!typeof(Component).IsAssignableFrom(resolvedType))
            {
                error = $"type '{resolvedType.FullName ?? resolvedType.Name}' is not a UnityEngine.Component";
                return false;
            }
            componentType = resolvedType;
            return true;
        }
        private static bool TryFindUniqueComponentOnObject(
            GameObject targetObject,
            string rawTypeName,
            out Component component,
            out string error
        )
        {
            component = null;
            error = string.Empty;
            Type targetType;
            if (!TryResolveComponentType(rawTypeName, out targetType, out error))
            {
                return false;
            }

            Component[] components = targetObject.GetComponents<Component>();
            List<Component> matches = new List<Component>();
            HashSet<string> availableTypeNames = new HashSet<string>(StringComparer.Ordinal);
            for (int i = 0; i < components.Length; i++)
            {
                Component candidate = components[i];
                if (candidate == null)
                {
                    continue;
                }

                Type candidateType = candidate.GetType();
                availableTypeNames.Add(candidateType.FullName ?? candidateType.Name);
                if (targetType.IsAssignableFrom(candidateType))
                {
                    matches.Add(candidate);
                }
            }

            string objectPath = BuildHierarchyPath(targetObject.transform);
            if (matches.Count == 1)
            {
                component = matches[0];
                return true;
            }
            if (matches.Count == 0)
            {
                string available = BuildTypeNameSample(availableTypeNames, 8);
                error = string.IsNullOrEmpty(available)
                    ? $"component type '{rawTypeName}' was not found on '{objectPath}'"
                    : $"component type '{rawTypeName}' was not found on '{objectPath}'. available types: {available}";
                return false;
            }

            error = $"component type '{rawTypeName}' matched {matches.Count} components on '{objectPath}'";
            return false;
        }
        private static bool TryResolveAssetPath(
            string target,
            bool allowMissing,
            out string assetPath,
            out string error
        )
        {
            assetPath = (target ?? string.Empty).Trim().Replace('\\', '/');
            error = string.Empty;
            if (string.IsNullOrWhiteSpace(assetPath))
            {
                error = "target is empty.";
                return false;
            }

            string projectRoot = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            if (Path.IsPathRooted(assetPath))
            {
                string fullTarget = Path.GetFullPath(assetPath).Replace('\\', '/');
                string fullProjectRoot = projectRoot.Replace('\\', '/');
                string prefix = fullProjectRoot + "/";
                if (!fullTarget.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                {
                    error = "absolute target must be inside the Unity project root.";
                    return false;
                }
                assetPath = fullTarget.Substring(prefix.Length);
            }

            assetPath = assetPath.Replace('\\', '/');
            if (!assetPath.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase))
            {
                error = "target must resolve to an Assets/ path.";
                return false;
            }
            if (!allowMissing && !File.Exists(Path.Combine(projectRoot, assetPath)))
            {
                error = "target file was not found.";
                return false;
            }
            return true;
        }
        /// <summary>
        /// Issue #37: resolve the component within <paramref name="root"/>
        /// whose Unity local fileID equals <paramref name="rawFileId"/>.
        /// The local fileID is read through Unity's global-object-id
        /// facility (<see cref="GlobalObjectId.targetObjectId"/>), so a
        /// same-type sibling on a single GameObject — which a type-name
        /// selector cannot disambiguate — is uniquely addressable.
        /// Reports a fail-fast failure reason when no component matches.
        /// </summary>
        private static bool TryResolveComponentByFileId(
            GameObject root,
            string rawFileId,
            out Component component,
            out string error
        )
        {
            component = null;
            error = string.Empty;
            string fileId = (rawFileId ?? string.Empty).Trim();
            if (string.IsNullOrEmpty(fileId))
            {
                error = "file_id is empty";
                return false;
            }
            if (!ulong.TryParse(fileId, out ulong targetFileId))
            {
                error = $"file_id '{rawFileId}' is not a valid fileID";
                return false;
            }

            Component[] components = root.GetComponentsInChildren<Component>(true);
            for (int i = 0; i < components.Length; i++)
            {
                Component candidate = components[i];
                if (candidate == null)
                {
                    continue;
                }
                GlobalObjectId gid = GlobalObjectId.GetGlobalObjectIdSlow(candidate);
                if (gid.targetObjectId == targetFileId)
                {
                    component = candidate;
                    return true;
                }
            }

            error = $"component with file_id '{fileId}' was not found in the asset";
            return false;
        }
        private static bool TryFindUniqueComponent(
            GameObject root,
            string selector,
            out Component component,
            out string error
        )
        {
            component = null;
            error = string.Empty;

            string typeSelector;
            string hierarchySelector;
            if (!TryParseComponentSelector(selector, out typeSelector, out hierarchySelector, out error))
            {
                return false;
            }

            // Issue #38: when the selector carries a hierarchy part, the
            // ``#N``-aware segment resolution is delegated to the
            // Unity-free ``SymbolPathResolver`` — the same resolver the
            // live stage resolver uses — so a same-named-sibling segment
            // is disambiguated by ``#N`` and an ambiguous bare segment is
            // rejected rather than first-picked.  The resolver narrows
            // the candidate set to one GameObject; the type filter then
            // selects the component on it.  With no hierarchy part the
            // whole subtree is scanned by type alone (unchanged).
            Transform hierarchyTarget = null;
            if (!string.IsNullOrWhiteSpace(hierarchySelector))
            {
                if (!TryResolveHierarchyPathWithResolver(
                        root, hierarchySelector, out hierarchyTarget,
                        out string hierarchyError))
                {
                    error = hierarchyError;
                    return false;
                }
            }

            Component[] components = root.GetComponentsInChildren<Component>(true);
            List<Component> matches = new List<Component>();
            List<Component> typeMatches = new List<Component>();
            HashSet<string> availableTypeNames = new HashSet<string>(StringComparer.Ordinal);
            for (int i = 0; i < components.Length; i++)
            {
                Component candidate = components[i];
                if (candidate == null)
                {
                    continue;
                }

                Type type = candidate.GetType();
                if (!string.IsNullOrEmpty(type.FullName))
                {
                    availableTypeNames.Add(type.FullName);
                }
                else
                {
                    availableTypeNames.Add(type.Name);
                }

                if (
                    !string.Equals(type.FullName, typeSelector, StringComparison.Ordinal)
                    && !string.Equals(type.Name, typeSelector, StringComparison.Ordinal)
                    && !string.Equals(type.AssemblyQualifiedName, typeSelector, StringComparison.Ordinal)
                )
                {
                    continue;
                }

                typeMatches.Add(candidate);
                if (hierarchyTarget != null && candidate.transform != hierarchyTarget)
                {
                    continue;
                }
                matches.Add(candidate);
            }

            if (matches.Count == 1)
            {
                component = matches[0];
                return true;
            }
            if (matches.Count == 0)
            {
                if (!string.IsNullOrWhiteSpace(hierarchySelector) && typeMatches.Count > 0)
                {
                    string candidates = BuildComponentSample(typeMatches, 5);
                    error = string.IsNullOrEmpty(candidates)
                        ? $"component path filter did not match any '{typeSelector}' components: '{hierarchySelector}'"
                        : $"component path filter did not match any '{typeSelector}' components at '{hierarchySelector}'. available paths: {candidates}";
                    return false;
                }

                string available = BuildTypeNameSample(availableTypeNames, 8);
                error = string.IsNullOrEmpty(available)
                    ? $"component not found: '{selector}'"
                    : $"component not found: '{selector}'. available types: {available}";
                return false;
            }

            string matchedCandidates = BuildComponentSample(matches, 5);
            error = string.IsNullOrEmpty(matchedCandidates)
                ? $"component selector is ambiguous: '{selector}' matched {matches.Count} components"
                : $"component selector is ambiguous: '{selector}' matched {matches.Count} components ({matchedCandidates})";
            return false;
        }
        /// <summary>
        /// Issue #38: resolve the hierarchy part of a ``TypeName@/path``
        /// component selector to a single live ``Transform`` by delegating
        /// ``#N`` segment resolution to the Unity-free
        /// <see cref="SymbolPathResolver"/>.  The selector path is rooted
        /// at <paramref name="root"/>; its first segment names
        /// <paramref name="root"/> itself when present.  An ambiguous
        /// segment (same-named siblings, no ``#N``) is reported as an
        /// error rather than first-picked.
        /// </summary>
        private static bool TryResolveHierarchyPathWithResolver(
            GameObject root,
            string hierarchySelector,
            out Transform target,
            out string error
        )
        {
            target = null;
            error = string.Empty;

            string normalized = hierarchySelector.Trim()
                .Replace('\\', '/')
                .TrimStart('/');
            if (string.IsNullOrEmpty(normalized))
            {
                error = "component selector hierarchy path is empty";
                return false;
            }

            var idToTransform = new Dictionary<string, Transform>();
            SymbolPathNode rootNode = BuildSelectorNode(
                root.transform, idToTransform);
            string[] segments = normalized.Split('/');

            SymbolPathResolution resolution = SymbolPathResolver.Resolve(
                new[] { rootNode }, segments);

            if (resolution.Outcome == SymbolPathOutcome.Ambiguous)
            {
                error = $"component selector hierarchy path '{hierarchySelector}' "
                    + $"matched {resolution.MatchCount} same-named objects; "
                    + "disambiguate a segment with a '#N' suffix "
                    + "(0-based, child order)";
                return false;
            }
            if (resolution.Outcome != SymbolPathOutcome.Unique)
            {
                error = $"component selector hierarchy path '{hierarchySelector}' "
                    + "did not match any object";
                return false;
            }

            if (!idToTransform.TryGetValue(resolution.Node.Id, out target)
                || target == null)
            {
                error = $"component selector hierarchy path '{hierarchySelector}' "
                    + "resolved to a stale object";
                return false;
            }
            return true;
        }

        /// <summary>
        /// Build a <see cref="SymbolPathNode"/> tree mirroring a live
        /// transform subtree for selector resolution.  Each node's
        /// ``Id`` keys <paramref name="idToTransform"/> so a unique
        /// resolution maps back to the live ``Transform``; children are
        /// in ``Transform.GetChild`` order.
        /// </summary>
        private static SymbolPathNode BuildSelectorNode(
            Transform transform,
            Dictionary<string, Transform> idToTransform
        )
        {
            string id = transform.GetInstanceID().ToString();
            idToTransform[id] = transform;
            var children = new List<SymbolPathNode>(transform.childCount);
            for (int i = 0; i < transform.childCount; i++)
            {
                children.Add(BuildSelectorNode(
                    transform.GetChild(i), idToTransform));
            }
            return new SymbolPathNode(id, transform.name, children);
        }

        private static bool TryFindGameObjectByPath(
            GameObject root,
            string hierarchyPath,
            out GameObject result,
            out string error
        )
        {
            result = null;
            error = string.Empty;

            if (string.IsNullOrWhiteSpace(hierarchyPath))
            {
                result = root;
                return true;
            }

            string normalized = hierarchyPath.Trim().TrimStart('/');
            if (string.IsNullOrEmpty(normalized))
            {
                result = root;
                return true;
            }

            Transform found = root.transform.Find(normalized);
            if (found != null)
            {
                result = found.gameObject;
                return true;
            }

            // Build list of available children for diagnostics.
            List<string> available = new List<string>();
            CollectChildPaths(root.transform, "", available, 32);
            string hint = available.Count > 0
                ? $"available paths: {string.Join(", ", available.ToArray())}"
                : "no child objects found";
            error = $"game object not found at path '/{normalized}'. {hint}";
            return false;
        }
        private static void CollectChildPaths(
            Transform parent,
            string prefix,
            List<string> paths,
            int limit
        )
        {
            for (int i = 0; i < parent.childCount && paths.Count < limit; i++)
            {
                Transform child = parent.GetChild(i);
                string childPath = string.IsNullOrEmpty(prefix)
                    ? child.name
                    : prefix + "/" + child.name;
                paths.Add("/" + childPath);
                CollectChildPaths(child, childPath, paths, limit);
            }
        }
        private static bool TryParseComponentSelector(
            string selector,
            out string typeSelector,
            out string hierarchySelector,
            out string error
        )
        {
            typeSelector = string.Empty;
            hierarchySelector = string.Empty;
            error = string.Empty;

            string raw = (selector ?? string.Empty).Trim();
            if (string.IsNullOrWhiteSpace(raw))
            {
                error = "component selector is empty";
                return false;
            }

            int delimiter = raw.IndexOf('@');
            if (delimiter < 0)
            {
                typeSelector = raw;
                return true;
            }

            typeSelector = raw.Substring(0, delimiter).Trim();
            hierarchySelector = raw.Substring(delimiter + 1).Trim().Replace('\\', '/');
            if (string.IsNullOrWhiteSpace(typeSelector))
            {
                error = "component selector must include type before '@'";
                return false;
            }
            if (string.IsNullOrWhiteSpace(hierarchySelector))
            {
                error = "component selector must include hierarchy path after '@'";
                return false;
            }
            return true;
        }
    }
}
