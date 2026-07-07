using System.Globalization;
using System.Text;
using UnityEngine;

// Object-reference write helpers stay isolated from payload serialization to keep partial concerns bounded.
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private static SerializedPropertyObjectReference ObjectReferenceFailure(
            string code,
            string message)
        {
            return ObjectReferenceFailure(code, message, "{}");
        }

        private static SerializedPropertyObjectReference ObjectReferenceFailure(
            string code,
            string message,
            string evidenceJson)
        {
            return new SerializedPropertyObjectReference
            {
                Success = false,
                ErrorCode = code,
                ErrorMessage = message,
                EvidenceJson = evidenceJson,
            };
        }

        private static string BuildObjectReferenceAmbiguityJson(
            string hierarchyPath,
            Component[] candidates)
        {
            StringBuilder json = new StringBuilder();
            json.Append("{\"object_reference_hierarchy_path\":");
            AppendJsonString(json, hierarchyPath);
            json.Append(",\"candidates\":[");
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
            return json.ToString();
        }
    }
}
