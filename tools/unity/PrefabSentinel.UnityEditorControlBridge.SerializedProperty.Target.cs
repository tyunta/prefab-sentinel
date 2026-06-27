using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

// SerializedProperty target resolution and state evidence are shared by read, list, and write handlers.
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private sealed class SerializedPropertyTarget
        {
            public GameObject GameObject;
            public UnityEngine.Object TargetObject;
            public Component Component;
            public SerializedPropertyStateEvidence State;
        }

        private sealed class SerializedPropertyStateEvidence
        {
            public string mode = string.Empty;
            public string hierarchy_path = string.Empty;
            public string scene_path = string.Empty;
            public string prefab_asset_path = string.Empty;
        }

        private sealed class SerializedPropertyWritePlan
        {
            public bool Success;
            public string ErrorCode = string.Empty;
            public string ErrorMessage = string.Empty;
            public bool WouldChange;
            public string CurrentJson = "{}";
            public string ProposedJson = "{}";
        }

        private sealed class SerializedPropertyObjectReference
        {
            public bool Success;
            public string ErrorCode = string.Empty;
            public string ErrorMessage = string.Empty;
            public UnityEngine.Object Value;
            public string EvidenceJson = "{}";
            public bool WouldChange;
        }

        private sealed class UdonSharpSyncEvidence
        {
            public string status = "not_applicable";
            public string detail = string.Empty;
            public bool warning = false;
        }

        private struct IntegerRange
        {
            public long min;
            public long max;
            public ulong unsignedMax;
            public bool unsigned;
            public bool useUnsignedMax;
        }
        private static EditorControlResponse ValidateSerializedPropertyAddress(
            EditorControlRequest request,
            bool requirePropertyPath)
        {
            if (string.IsNullOrWhiteSpace(request.hierarchy_path))
                return BuildError(
                    "EDITOR_CTRL_SERIALIZED_PROPERTY_NO_PATH",
                    "hierarchy_path is required.");
            if (string.IsNullOrWhiteSpace(request.component_type))
                return BuildError(
                    "EDITOR_CTRL_SERIALIZED_PROPERTY_NO_COMPONENT_TYPE",
                    "component_type is required.");
            if (requirePropertyPath && string.IsNullOrWhiteSpace(request.property_path))
                return BuildError(
                    "EDITOR_CTRL_SERIALIZED_PROPERTY_NO_PROPERTY_PATH",
                    "property_path is required.");
            return null;
        }

        private static EditorControlResponse ResolveSerializedPropertyTarget(
            EditorControlRequest request,
            out SerializedPropertyTarget target)
        {
            target = null;
            if (!TryResolveGameObjectInActiveStage(
                    request.hierarchy_path, out GameObject go,
                    out EditorControlResponse ambiguity))
            {
                if (ambiguity != null) return ambiguity;
                return BuildError(
                    "EDITOR_CTRL_SERIALIZED_PROPERTY_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");
            }

            UnityEngine.Object targetObject;
            Component component = null;
            int selectedIndex = -1;
            if (string.Equals(
                    request.component_type, "GameObject", StringComparison.Ordinal))
            {
                targetObject = go;
            }
            else
            {
                Type componentType = ResolveComponentType(request.component_type);
                if (componentType == null)
                    return BuildError(
                        "EDITOR_CTRL_SERIALIZED_PROPERTY_COMPONENT_NOT_FOUND",
                        $"Component type not found: {request.component_type}");

                Component[] candidates = go.GetComponents(componentType);
                if (candidates.Length == 0)
                    return BuildError(
                        "EDITOR_CTRL_SERIALIZED_PROPERTY_COMPONENT_NOT_FOUND",
                        $"Component {request.component_type} not found on {request.hierarchy_path}");
                if (request.component_index >= 0)
                {
                    if (request.component_index >= candidates.Length)
                        return BuildError(
                            "EDITOR_CTRL_SERIALIZED_PROPERTY_COMPONENT_NOT_FOUND",
                            $"component_index {request.component_index} is outside 0..{candidates.Length - 1}.");
                    selectedIndex = request.component_index;
                }
                else if (candidates.Length > 1)
                {
                    return BuildComponentAmbiguityError(candidates);
                }
                else
                {
                    selectedIndex = 0;
                }
                component = candidates[selectedIndex];
                targetObject = component;
            }

            target = new SerializedPropertyTarget
            {
                GameObject = go,
                TargetObject = targetObject,
                Component = component,
                State = BuildSerializedPropertyStateEvidence(go, request.hierarchy_path),
            };
            return null;
        }

        private static EditorControlResponse BuildComponentAmbiguityError(
            Component[] candidates)
        {
            StringBuilder json = new StringBuilder();
            json.Append("{\"candidates\":[");
            for (int i = 0; i < candidates.Length; i++)
            {
                if (i > 0) json.Append(',');
                json.Append("{\"candidate\":true,\"component_index\":");
                json.Append(i.ToString(CultureInfo.InvariantCulture));
                json.Append(",\"type\":");
                AppendJsonString(json, candidates[i].GetType().FullName);
                json.Append('}');
            }
            json.Append("]}");
            return BuildError(
                "EDITOR_CTRL_SERIALIZED_PROPERTY_COMPONENT_AMBIGUOUS",
                "Multiple matching components found; pass component_index.",
                new EditorControlData { serialized_property_json = json.ToString() });
        }

        private static SerializedPropertyStateEvidence BuildSerializedPropertyStateEvidence(
            GameObject go,
            string hierarchyPath)
        {
            PrefabStage stage = PrefabStageUtility.GetCurrentPrefabStage();
            if (stage != null && go.scene == stage.scene)
            {
                return new SerializedPropertyStateEvidence
                {
                    mode = "prefab_stage",
                    hierarchy_path = hierarchyPath,
                    scene_path = stage.scene.path,
                    prefab_asset_path = stage.assetPath,
                };
            }

            Scene scene = go.scene;
            return new SerializedPropertyStateEvidence
            {
                mode = "scene",
                hierarchy_path = hierarchyPath,
                scene_path = scene.path,
                prefab_asset_path = string.Empty,
            };
        }

        private static EditorControlResponse BuildPropertyNotFoundError(
            SerializedObject serializedObject,
            string propertyPath,
            SerializedPropertyStateEvidence state)
        {
            List<string> candidates = new List<string>();
            Dictionary<string, SerializedProperty> byPath =
                new Dictionary<string, SerializedProperty>();
            SerializedProperty iterator = serializedObject.GetIterator();
            bool truncated = false;
            if (iterator.NextVisible(true))
            {
                do
                {
                    if (candidates.Count >= 50)
                    {
                        truncated = true;
                        break;
                    }
                    candidates.Add(iterator.propertyPath);
                    byPath[iterator.propertyPath] = iterator.Copy();
                } while (iterator.NextVisible(false));
            }
            string[] ranked = SuggestionRanker.SuggestSimilar(
                propertyPath, candidates, maxResults: 5);
            string json = BuildSuggestionJson(ranked, byPath, truncated, state);
            return BuildError(
                "EDITOR_CTRL_SERIALIZED_PROPERTY_NOT_FOUND",
                $"SerializedProperty not found: {propertyPath}",
                new EditorControlData
                {
                    suggestions = ranked,
                    serialized_property_json = json,
                });
        }
    }
}
