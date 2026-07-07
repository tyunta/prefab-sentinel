using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

// SerializedProperty traversal and suggestion payloads are grouped separately from write mutation logic.
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private static string BuildSerializedPropertyJson(
            SerializedProperty property,
            SerializedPropertyTraversalOptions childOptions,
            SerializedPropertyStateEvidence state)
        {
            StringBuilder json = new StringBuilder();
            json.Append('{');
            AppendJsonField(json, "property_path", property.propertyPath, false);
            AppendJsonField(json, "display_name", property.displayName, true);
            AppendJsonField(json, "property_type", property.propertyType.ToString(), true);
            AppendJsonField(json, "serialized_type", property.type, true);
            AppendJsonField(json, "value_kind", SerializedPropertyValueKind(property), true);
            AppendJsonValue(json, "depth", property.depth, true);
            AppendPropertyValueFields(json, property);
            json.Append(",\"children\":");
            json.Append(BuildChildSummaryJson(property, childOptions));
            json.Append(",\"unsupported\":");
            json.Append(SerializedPropertyValueKind(property) == "unsupported"
                ? "true"
                : "false");
            json.Append(",\"state\":");
            json.Append(BuildStateJson(state));
            json.Append('}');
            return json.ToString();
        }

        private static string BuildSerializedPropertyListJson(
            List<string> entries,
            bool truncated,
            int nextCursor,
            SerializedPropertyStateEvidence state)
        {
            StringBuilder json = new StringBuilder();
            json.Append("{\"items\":[");
            for (int i = 0; i < entries.Count; i++)
            {
                if (i > 0) json.Append(',');
                json.Append(entries[i]);
            }
            json.Append("],\"truncated\":");
            json.Append(truncated ? "true" : "false");
            json.Append(",\"next_cursor\":");
            AppendJsonString(json, truncated
                ? nextCursor.ToString(CultureInfo.InvariantCulture)
                : string.Empty);
            json.Append(",\"state\":");
            json.Append(BuildStateJson(state));
            json.Append('}');
            return json.ToString();
        }

        private static void CollectSerializedPropertyList(
            SerializedProperty iterator,
            SerializedProperty root,
            SerializedPropertyTraversalOptions options,
            SerializedPropertyStateEvidence state,
            List<string> entries,
            out bool truncated,
            out int nextCursor)
        {
            truncated = false;
            nextCursor = 0;
            SerializedProperty end = root != null ? root.GetEndProperty() : null;
            bool includeCurrent = root != null;
            bool enterChildren = true;
            int visibleIndex = 0;
            while (includeCurrent || iterator.NextVisible(enterChildren))
            {
                enterChildren = true;
                if (!includeCurrent && end != null
                    && SerializedProperty.EqualContents(iterator, end))
                    break;
                includeCurrent = false;
                int baseDepth = root != null ? root.depth : -1;
                int relativeDepth = iterator.depth - baseDepth;
                int maxRelativeDepth = root != null ? options.Depth : options.Depth + 1;
                if (relativeDepth > maxRelativeDepth) continue;
                if (visibleIndex++ < options.Cursor) continue;
                if (entries.Count >= options.Cap)
                {
                    truncated = true;
                    nextCursor = visibleIndex - 1;
                    break;
                }
                SerializedPropertyTraversalOptions childOptions =
                    SerializedPropertyTraversalOptions.Parse(1, 1, string.Empty);
                entries.Add(BuildSerializedPropertyJson(iterator, childOptions, state));
            }
        }

        private static string BuildChildSummaryJson(
            SerializedProperty property,
            SerializedPropertyTraversalOptions options)
        {
            if (!property.hasVisibleChildren || options.Depth <= 0)
                return "[]";

            List<string> children = new List<string>();
            SerializedProperty cursor = property.Copy();
            SerializedProperty end = property.GetEndProperty();
            bool enterChildren = true;
            int seen = 0;
            bool truncated = false;
            while (cursor.NextVisible(enterChildren)
                && !SerializedProperty.EqualContents(cursor, end))
            {
                enterChildren = false;
                if (cursor.depth != property.depth + 1) continue;
                if (seen++ < options.Cursor) continue;
                if (children.Count >= options.Cap)
                {
                    truncated = true;
                    break;
                }
                StringBuilder child = new StringBuilder();
                child.Append('{');
                AppendJsonField(child, "property_path", cursor.propertyPath, false);
                AppendJsonField(child, "display_name", cursor.displayName, true);
                AppendJsonField(child, "property_type", cursor.propertyType.ToString(), true);
                AppendJsonField(child, "value_kind", SerializedPropertyValueKind(cursor), true);
                child.Append('}');
                children.Add(child.ToString());
            }

            StringBuilder json = new StringBuilder();
            json.Append('[');
            for (int i = 0; i < children.Count; i++)
            {
                if (i > 0) json.Append(',');
                json.Append(children[i]);
            }
            json.Append(']');
            if (!truncated) return json.ToString();
            return "{\"items\":" + json + ",\"truncated\":true}";
        }

        private static string BuildSuggestionJson(
            string[] ranked,
            Dictionary<string, SerializedProperty> byPath,
            bool truncated,
            SerializedPropertyStateEvidence state)
        {
            StringBuilder json = new StringBuilder();
            json.Append("{\"suggestions\":[");
            for (int i = 0; i < ranked.Length; i++)
            {
                if (i > 0) json.Append(',');
                SerializedProperty property;
                byPath.TryGetValue(ranked[i], out property);
                json.Append('{');
                AppendJsonField(json, "property_path", ranked[i], false);
                if (property != null)
                {
                    AppendJsonField(json, "display_name", property.displayName, true);
                    AppendJsonField(json, "property_type", property.propertyType.ToString(), true);
                    AppendJsonValue(json, "depth", property.depth, true);
                }
                json.Append('}');
            }
            json.Append("],\"truncated\":");
            json.Append(truncated ? "true" : "false");
            json.Append(",\"state\":");
            json.Append(BuildStateJson(state));
            json.Append('}');
            return json.ToString();
        }
    }
}
