using System;
using UnityEditor;
using UnityEngine;
namespace PrefabSentinel
{
    public static partial class UnityPatchBridge
    {
        private static bool TryResolveManagedReferenceTargetType(
            SerializedProperty property,
            string rawJson,
            out Type targetType,
            out string error
        )
        {
            targetType = null;
            error = string.Empty;

            string typeHint;
            if (TryReadManagedReferenceTypeHint(rawJson, out typeHint))
            {
                if (!TryResolveType(typeHint, out targetType, out error))
                {
                    error = $"failed to resolve managed reference __type '{typeHint}': {error}";
                    return false;
                }
                return true;
            }

            object current = property.managedReferenceValue;
            if (current != null)
            {
                targetType = current.GetType();
                return true;
            }

            if (!TryResolveManagedReferenceFieldType(property, out targetType, out error))
            {
                return false;
            }
            if (targetType.IsInterface || targetType.IsAbstract)
            {
                error =
                    $"managed reference field type '{targetType.FullName}' is abstract/interface; provide __type in value_json";
                return false;
            }
            return true;
        }
        private static bool TryResolveManagedReferenceFieldType(
            SerializedProperty property,
            out Type fieldType,
            out string error
        )
        {
            fieldType = null;
            error = string.Empty;
            string raw = property.managedReferenceFieldTypename ?? string.Empty;
            if (string.IsNullOrWhiteSpace(raw))
            {
                error = "managedReferenceFieldTypename is empty";
                return false;
            }
            int separator = raw.IndexOf(" ", StringComparison.Ordinal);
            if (separator <= 0 || separator >= raw.Length - 1)
            {
                error = $"managedReferenceFieldTypename has invalid format: '{raw}'";
                return false;
            }
            string assemblyName = raw.Substring(0, separator).Trim();
            string typeName = raw.Substring(separator + 1).Trim();
            if (!TryResolveType($"{typeName}, {assemblyName}", out fieldType, out error))
            {
                error = $"failed to resolve managed reference field type '{raw}': {error}";
                return false;
            }
            return true;
        }
        private static bool TryReadManagedReferenceTypeHint(string rawJson, out string typeName)
        {
            typeName = string.Empty;
            if (string.IsNullOrWhiteSpace(rawJson))
            {
                return false;
            }
            try
            {
                ManagedReferenceTypeHintPayload payload = JsonUtility.FromJson<ManagedReferenceTypeHintPayload>(rawJson);
                if (payload == null || string.IsNullOrWhiteSpace(payload.__type))
                {
                    return false;
                }
                typeName = payload.__type.Trim();
                return true;
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[PrefabSentinel] TryReadManagedReferenceTypeHint: {ex.GetType().Name}: {ex.Message}");
                return false;
            }
        }
    }
}
