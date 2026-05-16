using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;
namespace PrefabSentinel
{
    public static partial class UnityPatchBridge
    {
        private static string BuildTypeNameSample(HashSet<string> availableTypeNames, int maxItems)
        {
            if (availableTypeNames == null || availableTypeNames.Count == 0)
            {
                return string.Empty;
            }

            List<string> values = new List<string>(availableTypeNames);
            values.Sort(StringComparer.Ordinal);
            int take = Math.Min(maxItems, values.Count);
            List<string> sample = new List<string>();
            for (int i = 0; i < take; i++)
            {
                sample.Add(values[i]);
            }
            if (values.Count > take)
            {
                sample.Add("...");
            }
            return string.Join(", ", sample.ToArray());
        }
        private static string BuildComponentSample(List<Component> matches, int maxItems)
        {
            if (matches == null || matches.Count == 0)
            {
                return string.Empty;
            }

            int take = Math.Min(maxItems, matches.Count);
            List<string> sample = new List<string>();
            for (int i = 0; i < take; i++)
            {
                sample.Add(DescribeComponent(matches[i]));
            }
            if (matches.Count > take)
            {
                sample.Add("...");
            }
            return string.Join("; ", sample.ToArray());
        }
        private static string DescribeComponent(Component component)
        {
            if (component == null)
            {
                return "(missing component)";
            }
            Type type = component.GetType();
            string typeName = type.FullName ?? type.Name;
            return $"{typeName} @ {BuildHierarchyPath(component.transform)}";
        }
        private static string BuildHierarchyPath(Transform transform)
        {
            if (transform == null)
            {
                return "(unknown)";
            }

            List<string> parts = new List<string>();
            Transform current = transform;
            while (current != null)
            {
                parts.Add(current.name);
                current = current.parent;
            }
            parts.Reverse();
            return string.Join("/", parts.ToArray());
        }
        private static string BuildSetPathHint(string propertyPath)
        {
            if (string.IsNullOrWhiteSpace(propertyPath))
            {
                return string.Empty;
            }
            if (propertyPath.EndsWith(ArrayDataSuffix, StringComparison.Ordinal))
            {
                return "set path cannot end with '.Array.data'; use '.Array.size' or '.Array.data[index].field'";
            }

            int index = propertyPath.IndexOf(".Array.data", StringComparison.Ordinal);
            if (index < 0)
            {
                return string.Empty;
            }

            string suffix = propertyPath.Substring(index + ".Array.data".Length);
            if (suffix.Length == 0)
            {
                return "array element path should include an index like '.Array.data[0]'";
            }
            if (!suffix.StartsWith("[", StringComparison.Ordinal))
            {
                return "array element path should include an index like '.Array.data[0]'";
            }
            if (suffix.IndexOf(']') < 0)
            {
                return "array element index is missing closing ']'";
            }
            return string.Empty;
        }
        private static BridgeResponse BuildError(
            string code,
            string message,
            string target,
            int opCount,
            bool executed,
            int applied = 0,
            BridgeDiagnostic[] diagnostics = null
        )
        {
            return new BridgeResponse
            {
                protocol_version = ProtocolVersion,
                success = false,
                severity = "error",
                code = code,
                message = message,
                data = new BridgeData
                {
                    target = target ?? string.Empty,
                    op_count = opCount,
                    applied = applied,
                    read_only = false,
                    executed = executed,
                    protocol_version = ProtocolVersion
                },
                diagnostics = diagnostics ?? Array.Empty<BridgeDiagnostic>()
            };
        }
        private static void WriteResponseSafe(string responsePath, BridgeResponse response)
        {
            try
            {
                if (!string.IsNullOrWhiteSpace(responsePath))
                {
                    string dir = Path.GetDirectoryName(responsePath);
                    if (!string.IsNullOrWhiteSpace(dir))
                    {
                        Directory.CreateDirectory(dir);
                    }
                    string json = JsonUtility.ToJson(response);
                    string tmpPath = responsePath + ".tmp";
                    File.WriteAllText(tmpPath, json);
                    if (File.Exists(responsePath)) File.Delete(responsePath);
                    File.Move(tmpPath, responsePath);
                    return;
                }
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[PrefabSentinel] WriteResponseSafe: {ex.GetType().Name}: {ex.Message}");
                // Fallback: direct write if atomic rename failed.
                try { File.WriteAllText(responsePath, JsonUtility.ToJson(response)); }
                catch (Exception fallbackEx)
                {
                    Debug.LogWarning($"[PrefabSentinel] WriteResponseSafe: {fallbackEx.GetType().Name}: {fallbackEx.Message}");
                }
                return;
            }

            Debug.LogError("[PrefabSentinel] Response path is empty; bridge response was not written.");
        }
    }
}
