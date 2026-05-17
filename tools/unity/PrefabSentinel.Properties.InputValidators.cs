using System;
using System.Globalization;

// Property-write input validators — Unity-free decisions extracted from
// HandleEditorSetProperty (issues #111 / H-4). The handler reads the request
// value, delegates validation, and applies the parsed result to the live
// SerializedProperty.
namespace PrefabSentinel
{
    /// <summary>
    /// Result of <see cref="QuaternionInputValidator.Validate"/>: a success
    /// flag, the rejection code on failure, and the four parsed components.
    /// </summary>
    internal readonly struct QuaternionParse
    {
        public bool Success { get; }
        public string ErrorCode { get; }
        public float X { get; }
        public float Y { get; }
        public float Z { get; }
        public float W { get; }

        private QuaternionParse(
            bool success, string errorCode, float x, float y, float z, float w)
        {
            Success = success;
            ErrorCode = errorCode;
            X = x;
            Y = y;
            Z = z;
            W = w;
        }

        public static QuaternionParse Accepted(float x, float y, float z, float w)
        {
            return new QuaternionParse(true, string.Empty, x, y, z, w);
        }

        /// <summary>
        /// Rejection carrying no components — used when the input does not
        /// have exactly four components to parse.
        /// </summary>
        public static QuaternionParse Rejected(string code)
        {
            return new QuaternionParse(false, code, 0f, 0f, 0f, 0f);
        }

        /// <summary>
        /// Rejection carrying the four parsed components — used when the
        /// components parsed but the norm is non-unit.
        /// </summary>
        public static QuaternionParse Rejected(
            string code, float x, float y, float z, float w)
        {
            return new QuaternionParse(false, code, x, y, z, w);
        }
    }

    /// <summary>
    /// Validates the four-component xyzw quaternion input form. Requires
    /// exactly four comma-separated components and unit norm within
    /// <see cref="NormTolerance"/>.
    /// </summary>
    internal static class QuaternionInputValidator
    {
        internal const string TypeMismatchCode = "EDITOR_CTRL_SET_PROP_TYPE_MISMATCH";
        internal const string NotNormalizedCode =
            "EDITOR_CTRL_SET_PROP_QUATERNION_NOT_NORMALIZED";

        // Norm tolerance 1e-4 — matches the precision of float32 quaternion
        // encodings emitted by Unity's Transform.localRotation.
        internal const float NormTolerance = 1e-4f;

        /// <summary>
        /// Parse and validate <paramref name="raw"/>. A non-numeric component
        /// raises a <see cref="FormatException"/>, preserving the handler's
        /// pre-existing exception contract.
        /// </summary>
        public static QuaternionParse Validate(string raw)
        {
            var parts = (raw ?? string.Empty).Split(',');
            if (parts.Length != 4)
            {
                return QuaternionParse.Rejected(TypeMismatchCode);
            }

            float qx = float.Parse(parts[0].Trim(), CultureInfo.InvariantCulture);
            float qy = float.Parse(parts[1].Trim(), CultureInfo.InvariantCulture);
            float qz = float.Parse(parts[2].Trim(), CultureInfo.InvariantCulture);
            float qw = float.Parse(parts[3].Trim(), CultureInfo.InvariantCulture);

            // Single-precision norm math preserves the documented tolerance
            // boundary; double-precision would shift it.
            float norm = MathF.Sqrt(qx * qx + qy * qy + qz * qz + qw * qw);
            if (MathF.Abs(norm - 1f) > NormTolerance)
            {
                return QuaternionParse.Rejected(NotNormalizedCode, qx, qy, qz, qw);
            }
            return QuaternionParse.Accepted(qx, qy, qz, qw);
        }
    }

    /// <summary>
    /// The fixed set of GameObject-level serialized properties permitted by
    /// the GameObject-as-target write branch.
    /// </summary>
    internal static class GameObjectPropertyAllowlist
    {
        internal static readonly string[] AllowedProperties =
            { "m_IsActive", "m_Layer", "m_Name", "m_TagString" };

        /// <summary>
        /// Return true when <paramref name="propertyName"/> is one of the
        /// allow-listed GameObject-level property names.
        /// </summary>
        public static bool IsAllowed(string propertyName)
        {
            return Array.IndexOf(AllowedProperties, propertyName) >= 0;
        }
    }
}
