using System;
using System.Collections.Generic;
using System.Reflection;
using UnityEditor;
using UnityEngine;

// Type-aware object-reference shorthand resolution for editor property writes.
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private sealed class ObjectReferenceResolution
        {
            public bool Success;
            public UnityEngine.Object Object;
            public string ErrorCode = string.Empty;
            public string ErrorMessage = string.Empty;
            public string[] Candidates = Array.Empty<string>();

            public static ObjectReferenceResolution Ok(UnityEngine.Object obj)
            {
                return new ObjectReferenceResolution { Success = true, Object = obj };
            }

            public static ObjectReferenceResolution Failure(
                string code,
                string message,
                string[] candidates = null)
            {
                return new ObjectReferenceResolution
                {
                    Success = false,
                    ErrorCode = code,
                    ErrorMessage = message,
                    Candidates = candidates ?? Array.Empty<string>(),
                };
            }
        }

        private sealed class ExpectedObjectReferenceResolution
        {
            public bool Success;
            public Type Type;
            public string ErrorCode = string.Empty;
            public string ErrorMessage = string.Empty;
            public string[] Candidates = Array.Empty<string>();

            public static ExpectedObjectReferenceResolution Ok(Type type)
            {
                return new ExpectedObjectReferenceResolution
                {
                    Success = true,
                    Type = type,
                };
            }

            public static ExpectedObjectReferenceResolution Failure(
                string code,
                string message,
                string[] candidates)
            {
                return new ExpectedObjectReferenceResolution
                {
                    Success = false,
                    ErrorCode = code,
                    ErrorMessage = message,
                    Candidates = candidates,
                };
            }
        }

        private static ExpectedObjectReferenceResolution ResolveExpectedObjectReferenceType(
            SerializedProperty prop)
        {
            string propType = prop.type ?? string.Empty;
            const string prefix = "PPtr<";
            if (!propType.StartsWith(prefix, StringComparison.Ordinal)
                || !propType.EndsWith(">", StringComparison.Ordinal))
            {
                return ExpectedObjectReferenceResolution.Ok(typeof(UnityEngine.Object));
            }

            string typeName = propType.Substring(prefix.Length, propType.Length - prefix.Length - 1)
                .TrimStart('$');
            Type resolved = ResolveUnityObjectReferenceType(typeName);
            if (resolved != null)
                return ExpectedObjectReferenceResolution.Ok(resolved);
            return ExpectedObjectReferenceResolution.Failure(
                "EDITOR_CTRL_SET_PROP_OBJECT_REF_TYPE_MISMATCH",
                $"Serialized object reference type '{typeName}' could not be resolved.",
                new[] { typeName });
        }

        private static Type ResolveUnityObjectReferenceType(string typeName)
        {
            Type resolved = ResolveComponentType(typeName);
            if (resolved != null) return resolved;

            foreach (string candidate in UnityObjectTypeCandidates(typeName))
            {
                resolved = Type.GetType(candidate);
                if (IsUnityObjectType(resolved)) return resolved;
                foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
                {
                    resolved = assembly.GetType(candidate, false);
                    if (IsUnityObjectType(resolved)) return resolved;
                }
            }

            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                Type[] exported;
                try
                {
                    exported = assembly.GetExportedTypes();
                }
                catch (ReflectionTypeLoadException ex)
                {
                    exported = Array.FindAll(ex.Types, t => t != null);
                }
                foreach (Type type in exported)
                {
                    if (type.Name == typeName && IsUnityObjectType(type))
                        return type;
                }
            }
            return null;
        }

        private static IEnumerable<string> UnityObjectTypeCandidates(string typeName)
        {
            yield return typeName;
            if (!typeName.Contains("."))
                yield return "UnityEngine." + typeName;
        }

        private static bool IsUnityObjectType(Type type)
        {
            return type != null && typeof(UnityEngine.Object).IsAssignableFrom(type);
        }

        private static ObjectReferenceResolution ResolveTypedObjectReference(
            string reference,
            Type expectedType)
        {
            if (string.IsNullOrEmpty(reference))
                return ObjectReferenceResolution.Failure(
                    "EDITOR_CTRL_SET_PROP_OBJECT_REF_NOT_FOUND",
                    "object_reference is empty.");

            UnityEngine.Object asset = AssetDatabase.LoadAssetAtPath(reference, expectedType);
            if (asset != null) return ObjectReferenceResolution.Ok(asset);
            UnityEngine.Object genericAsset = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(reference);
            if (genericAsset != null)
                return ObjectReferenceResolution.Failure(
                    "EDITOR_CTRL_SET_PROP_OBJECT_REF_TYPE_MISMATCH",
                    $"Asset '{reference}' is not assignable to {expectedType.Name}.",
                    new[] { expectedType.FullName ?? expectedType.Name });

            string goPath = reference;
            string componentName = null;
            int colonIdx = reference.LastIndexOf(':');
            if (colonIdx > 0)
            {
                goPath = reference.Substring(0, colonIdx);
                componentName = reference.Substring(colonIdx + 1);
            }

            TryResolveGameObjectInActiveStage(goPath, out GameObject go, out var ambiguity);
            if (ambiguity != null)
                return ObjectReferenceResolution.Failure(
                    "EDITOR_CTRL_SET_PROP_OBJECT_REF_AMBIGUOUS",
                    ambiguity.message);
            if (go != null)
            {
                if (!string.IsNullOrEmpty(componentName))
                    return ResolveExplicitComponentReference(go, goPath, componentName, expectedType);
                return ResolveImplicitSceneReference(go, goPath, expectedType);
            }

            return ObjectReferenceResolution.Failure(
                "EDITOR_CTRL_SET_PROP_OBJECT_REF_NOT_FOUND",
                $"Not found in project assets or scene hierarchy: {reference}");
        }

        private static ObjectReferenceResolution ResolveExplicitComponentReference(
            GameObject go,
            string goPath,
            string componentName,
            Type expectedType)
        {
            Type compType = ResolveComponentType(componentName);
            if (compType == null)
                return ObjectReferenceResolution.Failure(
                    "EDITOR_CTRL_SET_PROP_OBJECT_REF_NOT_FOUND",
                    $"Component type not found: {componentName}");
            Component comp = go.GetComponent(compType);
            if (comp == null)
                return ObjectReferenceResolution.Failure(
                    "EDITOR_CTRL_SET_PROP_OBJECT_REF_NOT_FOUND",
                    $"GameObject '{goPath}' has no {componentName} component.");
            if (!expectedType.IsAssignableFrom(comp.GetType()))
                return ObjectReferenceResolution.Failure(
                    "EDITOR_CTRL_SET_PROP_OBJECT_REF_TYPE_MISMATCH",
                    $"Component '{componentName}' is not assignable to {expectedType.Name}.",
                    new[] { expectedType.FullName ?? expectedType.Name });
            return ObjectReferenceResolution.Ok(comp);
        }

        private static ObjectReferenceResolution ResolveImplicitSceneReference(
            GameObject go,
            string goPath,
            Type expectedType)
        {
            if (expectedType.IsAssignableFrom(typeof(GameObject)))
                return ObjectReferenceResolution.Ok(go);
            if (!typeof(Component).IsAssignableFrom(expectedType))
                return ObjectReferenceResolution.Failure(
                    "EDITOR_CTRL_SET_PROP_OBJECT_REF_TYPE_MISMATCH",
                    $"GameObject '{goPath}' is not assignable to {expectedType.Name}.",
                    new[] { expectedType.FullName ?? expectedType.Name });

            Component[] matches = go.GetComponents(expectedType);
            if (matches.Length == 1)
                return ObjectReferenceResolution.Ok(matches[0]);
            if (matches.Length == 0)
                return ObjectReferenceResolution.Failure(
                    "EDITOR_CTRL_SET_PROP_OBJECT_REF_NOT_FOUND",
                    $"GameObject '{goPath}' has no component assignable to {expectedType.Name}.",
                    new[] { expectedType.FullName ?? expectedType.Name });
            string[] candidates = Array.ConvertAll(
                matches,
                component => component.GetType().FullName ?? component.GetType().Name);
            return ObjectReferenceResolution.Failure(
                "EDITOR_CTRL_SET_PROP_OBJECT_REF_AMBIGUOUS",
                $"GameObject '{goPath}' has multiple components assignable to {expectedType.Name}; use path:ComponentType.",
                candidates);
        }
    }
}
