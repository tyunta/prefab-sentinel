using System;
using System.Collections.Generic;
using System.Reflection;
using UnityEditor;
using UnityEngine;

namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        // ── Set UdonSharp Field ──

        private static EditorControlResponse HandleSetUdonSharpField(
            EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError(
                    "EDITOR_CTRL_UDON_SET_FIELD_NO_PATH",
                    "hierarchy_path is required for editor_set_udonsharp_field.");
            if (string.IsNullOrEmpty(request.field_name))
                return BuildError(
                    "EDITOR_CTRL_UDON_SET_FIELD_NO_NAME",
                    "field_name is required for editor_set_udonsharp_field.");

            GameObject go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError(
                    "EDITOR_CTRL_UDON_SET_FIELD_NOT_FOUND",
                    $"GameObject not found at hierarchy_path: {request.hierarchy_path}");

            Type usbType = ResolveUdonSharpBehaviourType();
            if (usbType == null)
                return BuildError(
                    "EDITOR_CTRL_UDON_SET_FIELD_NOT_FOUND",
                    "UdonSharp is not loaded — install UdonSharp before " +
                    "calling editor_set_udonsharp_field.");

            var matches = new List<Component>();
            foreach (var comp in go.GetComponents<Component>())
            {
                if (comp == null) continue;
                if (usbType.IsAssignableFrom(comp.GetType())) matches.Add(comp);
            }
            if (matches.Count == 0)
            {
                return BuildError(
                    "EDITOR_CTRL_UDON_SET_FIELD_NOT_FOUND",
                    $"No UdonSharpBehaviour found on {request.hierarchy_path}. " +
                    "Use editor_add_udonsharp_component first.",
                    new EditorControlData
                    {
                        selected_object = request.hierarchy_path,
                        component_count = 0,
                    });
            }
            if (matches.Count > 1)
            {
                var typeNames = new string[matches.Count];
                for (int i = 0; i < matches.Count; i++)
                    typeNames[i] = matches[i].GetType().FullName;
                return BuildError(
                    "EDITOR_CTRL_UDON_SET_FIELD_AMBIGUOUS",
                    $"Multiple UdonSharpBehaviours on {request.hierarchy_path}: " +
                    $"{string.Join(", ", typeNames)}. The field-set surface " +
                    "requires a unique target.",
                    new EditorControlData
                    {
                        selected_object = request.hierarchy_path,
                        component_count = matches.Count,
                        suggestions = typeNames,
                    });
            }

            Component proxy = matches[0];
            var so = new SerializedObject(proxy);
            SerializedProperty prop = so.FindProperty(request.field_name);
            if (prop == null)
            {
                var available = new List<string>();
                SerializedProperty walker = so.GetIterator();
                if (walker.NextVisible(true))
                {
                    do { available.Add(walker.name); }
                    while (walker.NextVisible(false));
                }
                return BuildError(
                    "EDITOR_CTRL_UDON_SET_FIELD_FIELD_NOT_FOUND",
                    $"Field '{request.field_name}' not found on " +
                    $"{proxy.GetType().Name}.",
                    new EditorControlData
                    {
                        selected_object = request.hierarchy_path,
                        suggestions = SuggestSimilar(
                            request.field_name, available, 5),
                    });
            }

            // Conflict / underspecification mirror the existing
            // editor_set_property surface so the client envelope codes
            // map 1:1.
            bool hasValue = !string.IsNullOrEmpty(request.property_value);
            bool hasRef = !string.IsNullOrEmpty(request.object_reference);
            if (hasValue && hasRef)
                return BuildError(
                    "EDITOR_CTRL_SET_PROP_BOTH_VALUE",
                    "property_value and object_reference are mutually exclusive; " +
                    "supply exactly one.");
            if (!hasValue && !hasRef)
                return BuildError(
                    "EDITOR_CTRL_UDON_SET_FIELD_NO_VALUE",
                    "Either property_value or object_reference is required.");

            // VRChat URL fields appear as a Generic SerializedProperty
            // wrapping a private "url" string — UdonSharp deserializes
            // them through that nested member, so the bridge writes the
            // string there rather than constructing a VRCUrl instance.
            FieldInfo fieldInfo = ResolveDeclaredField(proxy.GetType(), request.field_name);
            bool isVRCUrl = fieldInfo != null
                && fieldInfo.FieldType.FullName != null
                && fieldInfo.FieldType.FullName.EndsWith("VRCUrl", StringComparison.Ordinal);
            if (isVRCUrl && hasValue && prop.propertyType == SerializedPropertyType.Generic)
            {
                SerializedProperty urlProp = prop.FindPropertyRelative("url");
                if (urlProp == null)
                    return BuildError(
                        "EDITOR_CTRL_UDON_SET_FIELD_FIELD_NOT_FOUND",
                        "VRCUrl wrapper has no 'url' sub-field — VRChat SDK " +
                        "version mismatch.");
                urlProp.stringValue = request.property_value;
            }
            else if (hasRef)
            {
                if (prop.propertyType != SerializedPropertyType.ObjectReference)
                    return BuildError(
                        "EDITOR_CTRL_UDON_SET_FIELD_FAILED",
                        $"Field {request.field_name} is " +
                        $"{prop.propertyType}; object_reference is only valid " +
                        "for ObjectReference fields.");
                var (obj, refError) = ResolveObjectReference(request.object_reference);
                if (obj == null)
                    return BuildError(
                        "EDITOR_CTRL_UDON_SET_FIELD_FAILED",
                        refError ?? $"Failed to resolve object_reference: {request.object_reference}.");
                prop.objectReferenceValue = obj;
            }
            else
            {
                if (!ApplyPropertyValue(prop, request.property_value))
                    return BuildError(
                        "EDITOR_CTRL_UDON_SET_FIELD_FAILED",
                        $"Failed to apply value '{request.property_value}' to " +
                        $"{request.field_name} ({prop.propertyType}).");
            }
            so.ApplyModifiedProperties();

            // Spec mandates "set + sync" as one transaction; if the
            // sync entry point is unavailable, the proxy is now ahead
            // of its backing UdonBehaviour and the run-time read will
            // see stale data — fail loudly rather than leave the pair
            // out of sync.
            Type editorUtilType = ResolveUdonSharpEditorUtilityType();
            if (editorUtilType == null)
                return BuildError(
                    "EDITOR_CTRL_UDON_SET_FIELD_FAILED",
                    "UdonSharpEditorUtility unavailable — CopyProxyToUdon " +
                    "skipped; UdonSharp must be installed for " +
                    "editor_set_udonsharp_field to complete the sync step.");
            EditorControlResponse syncErr = InvokeUdonSharpCopyProxyToUdon(
                editorUtilType, proxy);
            if (syncErr != null) return syncErr;

            EditorUtility.SetDirty(proxy);
            EditorUtility.SetDirty(go);

            return BuildSuccess(
                "EDITOR_CTRL_UDON_SET_FIELD_OK",
                $"Wrote {request.field_name} on {proxy.GetType().Name}.",
                new EditorControlData
                {
                    selected_object = request.hierarchy_path,
                    asset_path = proxy.GetType().FullName,
                    executed = true,
                    read_only = false,
                });
        }

        /// <summary>
        /// Walk the type chain (declared-only at each rung) to find a
        /// field by name; the default ``GetField`` only searches a
        /// single visibility set, and UdonSharp fields are typically
        /// declared as ``[SerializeField] private`` at various base
        /// classes.
        /// </summary>
        private static FieldInfo ResolveDeclaredField(Type t, string name)
        {
            const BindingFlags flags = BindingFlags.Public | BindingFlags.NonPublic
                | BindingFlags.Instance | BindingFlags.DeclaredOnly;
            for (Type cursor = t; cursor != null; cursor = cursor.BaseType)
            {
                FieldInfo f = cursor.GetField(name, flags);
                if (f != null) return f;
            }
            return null;
        }

        private static bool ApplySerializedFieldValue(
            Component proxy, SerializedProperty prop, string raw)
        {
            FieldInfo fi = ResolveDeclaredField(proxy.GetType(), prop.name);
            if (fi != null
                && fi.FieldType.FullName != null
                && fi.FieldType.FullName.EndsWith("VRCUrl", StringComparison.Ordinal)
                && prop.propertyType == SerializedPropertyType.Generic)
            {
                SerializedProperty urlProp = prop.FindPropertyRelative("url");
                if (urlProp == null) return false;
                urlProp.stringValue = raw ?? string.Empty;
                return true;
            }
            return ApplyPropertyValue(prop, raw ?? string.Empty);
        }
    }
}
