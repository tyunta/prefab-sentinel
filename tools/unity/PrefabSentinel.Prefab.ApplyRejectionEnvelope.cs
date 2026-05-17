using System;

// Prefab-apply rejection envelope — Unity-free decision extracted from
// BuildPrefabApplyRejectionDiagnostics (issue #298 / H-11). The prefab apply
// handler extracts the three diagnostic strings from the rejected PatchOp and
// delegates envelope assembly here.
namespace PrefabSentinel
{
    /// <summary>
    /// Unity-free failure record for a rejected prefab apply: the property
    /// path, component type, and a textual summary of the attempted value.
    /// </summary>
    internal readonly struct PrefabApplyFailure
    {
        public string PropertyPath { get; }
        public string ComponentType { get; }
        public string AttemptedValue { get; }

        public PrefabApplyFailure(
            string propertyPath, string componentType, string attemptedValue)
        {
            PropertyPath = propertyPath;
            ComponentType = componentType;
            AttemptedValue = attemptedValue;
        }
    }

    /// <summary>
    /// The assembled rejection result: the apply-rejected code and the
    /// evidence string conveying the three diagnostic values.
    /// </summary>
    internal readonly struct PrefabApplyRejection
    {
        public string Code { get; }
        public string Evidence { get; }

        public PrefabApplyRejection(string code, string evidence)
        {
            Code = code;
            Evidence = evidence;
        }
    }

    /// <summary>
    /// Assembles the structured envelope for a SerializedObject apply
    /// rejection.
    /// </summary>
    internal static class PrefabApplyRejectionEnvelope
    {
        internal const string RejectedCode = "SER_APPLY_REJECTED";

        public static PrefabApplyRejection Build(PrefabApplyFailure failure)
        {
            string evidence = string.Format(
                "property_path={0}; component_type={1}; attempted_value={2}",
                failure.PropertyPath ?? string.Empty,
                failure.ComponentType ?? string.Empty,
                failure.AttemptedValue ?? string.Empty);
            return new PrefabApplyRejection(RejectedCode, evidence);
        }
    }
}
