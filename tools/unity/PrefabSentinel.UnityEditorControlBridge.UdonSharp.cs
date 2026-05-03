using System;
using System.Collections.Generic;
using System.Reflection;
using UnityEditor;
using UnityEditor.Events;
using UnityEngine;
using UnityEngine.Events;

namespace PrefabSentinel
{
    /// <summary>
    /// UdonSharp authoring partial (issue #119).  Houses the three
    /// high-level handlers that wrap the UdonSharp / UnityEvent surface
    /// so callers do not have to write raw editor C# through
    /// ``editor_run_script``:
    ///
    ///   editor_add_udonsharp_component   — Upsert.  AddComponent →
    ///       UdonSharpEditorUtility.RunBehaviourSetup → optional initial
    ///       field assignment → CopyProxyToUdon, with idempotent reuse
    ///       on a pre-existing matching component.
    ///   editor_set_udonsharp_field       — SerializedObject field write
    ///       on the unique UdonSharpBehaviour at the path, including the
    ///       VRChat URL field shape, followed by CopyProxyToUdon as one
    ///       transaction.
    ///   editor_wire_persistent_listener  — Idempotent string-mode wrap
    ///       of UnityEventTools.AddStringPersistentListener so a Slider /
    ///       Toggle.onValueChanged → UdonBehaviour.SendCustomEvent
    ///       wiring can be authored declaratively.
    ///
    /// UdonSharp is reached through reflection (see
    /// ``ResolveUdonSharpBehaviourType`` /
    /// ``ResolveUdonSharpEditorUtilityType`` in HierarchyComponents) so
    /// the bridge keeps building when UdonSharp is absent.
    /// </summary>
    public static partial class UnityEditorControlBridge
    {
        // PersistentListenerMode.String — see UnityEngine.Events.
        // Hard-coded to avoid a UnityEngine.Events.PersistentListenerMode
        // namespace collision with our reflective UdonSharp helpers.
        private const int PersistentListenerModeString = 5;

        // ── Add UdonSharp Component ──

        private static EditorControlResponse HandleAddUdonSharpComponent(
            EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError(
                    "EDITOR_CTRL_UDON_ADD_NO_PATH",
                    "hierarchy_path is required for editor_add_udonsharp_component.");
            if (string.IsNullOrEmpty(request.component_type))
                return BuildError(
                    "EDITOR_CTRL_UDON_ADD_NO_TYPE",
                    "component_type is required for editor_add_udonsharp_component.");

            GameObject go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError(
                    "EDITOR_CTRL_UDON_ADD_NOT_FOUND",
                    $"GameObject not found at hierarchy_path: {request.hierarchy_path}");

            Type compType = ResolveComponentType(request.component_type);
            if (compType == null)
                return BuildError(
                    "EDITOR_CTRL_UDON_ADD_TYPE_NOT_FOUND",
                    $"component_type not found: {request.component_type}. " +
                    "Short names (e.g. \"MyController\") and fully qualified " +
                    "names both work.");

            Type usbType = ResolveUdonSharpBehaviourType();
            if (usbType == null || !usbType.IsAssignableFrom(compType))
                return BuildError(
                    "EDITOR_CTRL_UDON_ADD_NOT_USHARP",
                    $"component_type {compType.FullName} is not an " +
                    "UdonSharpBehaviour subclass; use editor_add_component " +
                    "for non-UdonSharp components.");

            // Pre-validate the field map so a malformed payload aborts
            // before any scene mutation happens.
            Dictionary<string, string> fieldMap;
            string fieldMapError = TryParseFieldsJson(request.fields_json, out fieldMap);
            if (fieldMapError != null)
                return BuildError(
                    "EDITOR_CTRL_UDON_ADD_BAD_FIELDS_JSON",
                    "fields_json must be a JSON object mapping " +
                    "field name → string value: " + fieldMapError);

            Type editorUtilType = ResolveUdonSharpEditorUtilityType();
            if (editorUtilType == null)
                return BuildError(
                    "EDITOR_CTRL_UDON_ADD_NOT_USHARP",
                    "UdonSharpEditorUtility is not loaded; UdonSharp must be " +
                    "installed for editor_add_udonsharp_component to function.");

            Component proxy = go.GetComponent(compType);
            bool wasExisting = proxy != null;

            if (proxy == null)
            {
                // ``UdonSharpUndo.AddComponent`` is the documented public
                // entry point; internally it calls ``Undo.AddComponent``
                // followed by ``UdonSharpEditorUtility.RunBehaviourSetupWithUndo``
                // (the per-overload setup hook is ``internal``, so the
                // wrapper is the only stable public surface).
                EditorControlResponse createErr = InvokeUdonSharpUndoAddComponent(
                    go, compType, out proxy);
                if (createErr != null) return createErr;
                if (proxy == null)
                    return BuildError(
                        "EDITOR_CTRL_UDON_ADD_COMPONENT_FAILED",
                        $"UdonSharpUndo.AddComponent returned null for " +
                        $"{compType.FullName} on {request.hierarchy_path}.");
            }

            var appliedFields = new List<string>();
            EditorControlResponse applyErr = ApplyUdonSharpInitialFields(
                proxy, fieldMap, appliedFields);
            if (applyErr != null) return applyErr;

            EditorControlResponse syncErr = InvokeUdonSharpCopyProxyToUdon(
                editorUtilType, proxy);
            if (syncErr != null) return syncErr;

            EditorUtility.SetDirty(proxy);
            EditorUtility.SetDirty(go);

            string programAssetPath = ResolveUdonProgramAssetPath(editorUtilType, proxy);
            int componentIndex = ComputeComponentIndex(go, proxy, compType);

            return BuildSuccess(
                wasExisting
                    ? "EDITOR_CTRL_UDON_ADD_REUSED"
                    : "EDITOR_CTRL_UDON_ADD_CREATED",
                wasExisting
                    ? $"Reused existing {compType.Name}; applied {appliedFields.Count} field(s)."
                    : $"Created {compType.Name}; applied {appliedFields.Count} initial field(s).",
                new EditorControlData
                {
                    selected_object = request.hierarchy_path,
                    asset_path = compType.FullName,
                    executed = true,
                    read_only = false,
                    was_existing = wasExisting,
                    applied_fields = appliedFields.ToArray(),
                    component_handle = new UdonSharpComponentHandle
                    {
                        hierarchy_path = request.hierarchy_path,
                        type_full_name = compType.FullName,
                        component_index = componentIndex,
                    },
                    udon_program_asset_path = programAssetPath ?? string.Empty,
                });
        }

        /// <summary>
        /// Best-effort JSON object parse: ``{ "field": "value", ... }``.
        /// Uses a tiny tokenizer rather than ``JsonUtility`` because the
        /// shape is dynamic (caller-defined keys) — JsonUtility cannot
        /// deserialise into ``Dictionary&lt;string,string&gt;``.
        /// Returns null on success and stores the parsed map in ``map``;
        /// returns a human-readable error string on failure.  An empty or
        /// whitespace-only payload is treated as ``{}``.
        /// </summary>
        private static string TryParseFieldsJson(
            string raw, out Dictionary<string, string> map)
        {
            map = new Dictionary<string, string>();
            if (string.IsNullOrWhiteSpace(raw)) return null;

            int i = 0;
            string err;
            if (!SkipWhitespace(raw, ref i)) return "unexpected end of input.";
            if (raw[i] != '{') return $"expected '{{' at position {i}.";
            i++;
            if (!SkipWhitespace(raw, ref i)) return "unexpected end of input after '{'.";
            if (raw[i] == '}') { i++; return null; }

            while (true)
            {
                string key;
                err = ParseJsonString(raw, ref i, out key);
                if (err != null) return err;
                if (!SkipWhitespace(raw, ref i)) return "unexpected end of input after key.";
                if (raw[i] != ':') return $"expected ':' at position {i}.";
                i++;
                if (!SkipWhitespace(raw, ref i)) return "unexpected end of input after ':'.";
                string value;
                if (raw[i] == '"')
                {
                    err = ParseJsonString(raw, ref i, out value);
                    if (err != null) return err;
                }
                else
                {
                    err = ParseJsonScalar(raw, ref i, out value);
                    if (err != null) return err;
                }
                map[key] = value;
                if (!SkipWhitespace(raw, ref i)) return "unexpected end of input after value.";
                if (raw[i] == ',') { i++; continue; }
                if (raw[i] == '}') { i++; return null; }
                return $"expected ',' or '}}' at position {i}.";
            }
        }

        private static bool SkipWhitespace(string s, ref int i)
        {
            while (i < s.Length && char.IsWhiteSpace(s[i])) i++;
            return i < s.Length;
        }

        private static string ParseJsonString(string s, ref int i, out string value)
        {
            value = null;
            if (i >= s.Length || s[i] != '"') return $"expected string literal at position {i}.";
            i++;
            var sb = new System.Text.StringBuilder();
            while (i < s.Length && s[i] != '"')
            {
                if (s[i] == '\\' && i + 1 < s.Length)
                {
                    char esc = s[i + 1];
                    switch (esc)
                    {
                        case '"': sb.Append('"'); break;
                        case '\\': sb.Append('\\'); break;
                        case '/': sb.Append('/'); break;
                        case 'n': sb.Append('\n'); break;
                        case 't': sb.Append('\t'); break;
                        case 'r': sb.Append('\r'); break;
                        case 'b': sb.Append('\b'); break;
                        case 'f': sb.Append('\f'); break;
                        default: return $"unsupported escape \\{esc} at position {i}.";
                    }
                    i += 2;
                    continue;
                }
                sb.Append(s[i]);
                i++;
            }
            if (i >= s.Length) return "unterminated string literal.";
            i++;
            value = sb.ToString();
            return null;
        }

        private static string ParseJsonScalar(string s, ref int i, out string value)
        {
            int start = i;
            while (i < s.Length && s[i] != ',' && s[i] != '}' && !char.IsWhiteSpace(s[i])) i++;
            if (i == start) { value = null; return $"empty value at position {i}."; }
            value = s.Substring(start, i - start);
            return null;
        }

        /// <summary>
        /// Resolve the ``UdonSharpEditor.UdonSharpUndo`` static class via
        /// reflection.  Returned to the caller so the bridge keeps
        /// building when UdonSharp is absent.
        /// </summary>
        private static Type ResolveUdonSharpUndoType()
        {
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                Type t = assembly.GetType("UdonSharpEditor.UdonSharpUndo", false);
                if (t != null) return t;
            }
            return null;
        }

        /// <summary>
        /// Add a fresh UdonSharpBehaviour through the public
        /// ``UdonSharpUndo.AddComponent(GameObject, Type)`` entry, which
        /// internally chains ``Undo.AddComponent`` and
        /// ``UdonSharpEditorUtility.RunBehaviourSetupWithUndo`` so the
        /// proxy + backing UdonBehaviour are wired in one Undo group.
        /// Reflection lookup is required because UdonSharp is not a
        /// hard dependency of the bridge.
        /// Source: https://github.com/vrchat-community/UdonSharp/blob/master/Packages/com.vrchat.UdonSharp/Editor/UdonSharpUndo.cs
        /// </summary>
        private static EditorControlResponse InvokeUdonSharpUndoAddComponent(
            GameObject go, Type compType, out Component proxy)
        {
            proxy = null;
            Type undoType = ResolveUdonSharpUndoType();
            if (undoType == null)
                return BuildError(
                    "EDITOR_CTRL_UDON_ADD_NOT_USHARP",
                    "UdonSharpEditor.UdonSharpUndo not found via reflection " +
                    "— UdonSharp must be installed for editor_add_udonsharp_component.");
            MethodInfo addComp = undoType.GetMethod(
                "AddComponent",
                BindingFlags.Public | BindingFlags.Static,
                null, new Type[] { typeof(GameObject), typeof(Type) }, null);
            if (addComp == null)
                return BuildError(
                    "EDITOR_CTRL_UDON_ADD_COMPONENT_FAILED",
                    "UdonSharpUndo.AddComponent(GameObject, Type) not found " +
                    "via reflection — UdonSharp version mismatch.");
            try
            {
                object result = addComp.Invoke(null, new object[] { go, compType });
                proxy = result as Component;
                return null;
            }
            catch (TargetInvocationException ex)
            {
                Exception inner = ex.InnerException ?? ex;
                return BuildError(
                    "EDITOR_CTRL_UDON_ADD_COMPONENT_FAILED",
                    $"UdonSharpUndo.AddComponent threw {inner.GetType().Name}: {inner.Message}");
            }
            catch (Exception ex)
            {
                return BuildError(
                    "EDITOR_CTRL_UDON_ADD_COMPONENT_FAILED",
                    $"UdonSharpUndo.AddComponent threw {ex.GetType().Name}: {ex.Message}");
            }
        }

        private static EditorControlResponse InvokeUdonSharpCopyProxyToUdon(
            Type editorUtilType, Component proxy)
        {
            MethodInfo copy = editorUtilType.GetMethod(
                "CopyProxyToUdon",
                BindingFlags.Public | BindingFlags.Static,
                null, new Type[] { proxy.GetType().BaseType ?? proxy.GetType() }, null)
                ?? editorUtilType.GetMethod(
                    "CopyProxyToUdon",
                    BindingFlags.Public | BindingFlags.Static);
            if (copy == null)
                return BuildError(
                    "EDITOR_CTRL_UDON_ADD_FIELD_FAILED",
                    "UdonSharpEditorUtility.CopyProxyToUdon not found via " +
                    "reflection — UdonSharp version mismatch.");
            try
            {
                copy.Invoke(null, new object[] { proxy });
                return null;
            }
            catch (TargetInvocationException ex)
            {
                Exception inner = ex.InnerException ?? ex;
                return BuildError(
                    "EDITOR_CTRL_UDON_ADD_FIELD_FAILED",
                    $"CopyProxyToUdon threw {inner.GetType().Name}: {inner.Message}");
            }
            catch (Exception ex)
            {
                return BuildError(
                    "EDITOR_CTRL_UDON_ADD_FIELD_FAILED",
                    $"CopyProxyToUdon threw {ex.GetType().Name}: {ex.Message}");
            }
        }

        /// <summary>
        /// Apply each requested initial field through the SerializedObject
        /// surface.  Stops at the first failure and reports the names of
        /// fields applied prior — the upsert contract permits no rollback;
        /// the next call recovers via reuse.
        /// Note: ``ApplyModifiedProperties`` is only invoked on the success
        /// path (after the loop completes).  The ``applied_fields`` array
        /// surfaced in error envelopes therefore lists fields that passed
        /// the per-field value-apply step but were *not* persisted to the
        /// serialised object on the failing call.  This is intentional: the
        /// upsert path re-applies all fields on retry, so durability of
        /// partial writes would only complicate recovery.
        /// </summary>
        private static EditorControlResponse ApplyUdonSharpInitialFields(
            Component proxy,
            Dictionary<string, string> fieldMap,
            List<string> appliedFields)
        {
            if (fieldMap == null || fieldMap.Count == 0) return null;

            var so = new SerializedObject(proxy);
            foreach (var kv in fieldMap)
            {
                SerializedProperty prop = so.FindProperty(kv.Key);
                if (prop == null)
                {
                    return BuildError(
                        "EDITOR_CTRL_UDON_ADD_FIELD_FAILED",
                        $"Field not found on {proxy.GetType().Name}: {kv.Key}. " +
                        $"Applied {appliedFields.Count} field(s) prior to failure.",
                        new EditorControlData
                        {
                            applied_fields = appliedFields.ToArray(),
                        });
                }
                if (!ApplySerializedFieldValue(proxy, prop, kv.Value))
                {
                    return BuildError(
                        "EDITOR_CTRL_UDON_ADD_FIELD_FAILED",
                        $"Failed to apply field {kv.Key} = {kv.Value} on " +
                        $"{proxy.GetType().Name}. Applied {appliedFields.Count} " +
                        "field(s) prior to failure.",
                        new EditorControlData
                        {
                            applied_fields = appliedFields.ToArray(),
                        });
                }
                appliedFields.Add(kv.Key);
            }
            so.ApplyModifiedProperties();
            return null;
        }

        /// <summary>
        /// Resolve the .asset path of the UdonSharpProgramAsset attached
        /// to a proxy, or null when UdonSharp does not expose one.
        /// Returned to the caller so it can read or modify the program
        /// without re-querying.
        /// </summary>
        private static string ResolveUdonProgramAssetPath(
            Type editorUtilType, Component proxy)
        {
            try
            {
                MethodInfo getAsset = editorUtilType.GetMethod(
                    "GetUdonSharpProgramAsset",
                    BindingFlags.Public | BindingFlags.Static,
                    null, new Type[] { proxy.GetType().BaseType ?? proxy.GetType() }, null)
                    ?? editorUtilType.GetMethod(
                        "GetUdonSharpProgramAsset",
                        BindingFlags.Public | BindingFlags.Static);
                if (getAsset == null) return null;
                object asset = getAsset.Invoke(null, new object[] { proxy });
                if (asset == null) return null;
                var unityAsset = asset as UnityEngine.Object;
                if (unityAsset == null) return null;
                return AssetDatabase.GetAssetPath(unityAsset);
            }
            catch (Exception ex)
            {
                // Returning null surfaces an empty ``udon_program_asset_path``
                // in the success envelope; without a log line a future
                // SDK-version mismatch would silently strip a documented
                // response field.
                Debug.LogWarning(
                    $"[PrefabSentinel] ResolveUdonProgramAssetPath: " +
                    $"{ex.GetType().Name}: {ex.Message}");
                return null;
            }
        }

        private static int ComputeComponentIndex(GameObject go, Component proxy, Type compType)
        {
            Component[] all = go.GetComponents(compType);
            for (int i = 0; i < all.Length; i++)
                if (all[i] == proxy) return i;
            return -1;
        }

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

        // ── Wire Persistent Listener ──

        private static EditorControlResponse HandleWirePersistentListener(
            EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError(
                    "EDITOR_CTRL_UDON_WIRE_NO_PATH",
                    "hierarchy_path is required for editor_wire_persistent_listener.");
            if (string.IsNullOrEmpty(request.event_path))
                return BuildError(
                    "EDITOR_CTRL_UDON_WIRE_EVENT_NOT_FOUND",
                    "event_path is required.");
            if (string.IsNullOrEmpty(request.target_path))
                return BuildError(
                    "EDITOR_CTRL_UDON_WIRE_TARGET_NOT_FOUND",
                    "target_path is required.");
            if (string.IsNullOrEmpty(request.method))
                return BuildError(
                    "EDITOR_CTRL_UDON_WIRE_METHOD_NOT_FOUND",
                    "method is required.");

            GameObject sourceGo = GameObject.Find(request.hierarchy_path);
            if (sourceGo == null)
                return BuildError(
                    "EDITOR_CTRL_UDON_WIRE_EVENT_NOT_FOUND",
                    $"Source GameObject not found: {request.hierarchy_path}");

            Component sourceComp;
            UnityEventBase eventBase;
            string eventResolveError = ResolveSourceEvent(
                sourceGo, request.event_path, out sourceComp, out eventBase);
            if (eventResolveError != null)
                return BuildError(
                    "EDITOR_CTRL_UDON_WIRE_EVENT_NOT_FOUND",
                    eventResolveError);

            GameObject targetGo = GameObject.Find(request.target_path);
            if (targetGo == null)
                return BuildError(
                    "EDITOR_CTRL_UDON_WIRE_TARGET_NOT_FOUND",
                    $"Target GameObject not found: {request.target_path}");

            Component targetComp;
            MethodInfo methodInfo;
            string methodResolveError = ResolveTargetStringMethod(
                targetGo, request.method, out targetComp, out methodInfo);
            if (methodResolveError != null)
                return BuildError(
                    "EDITOR_CTRL_UDON_WIRE_METHOD_NOT_FOUND",
                    methodResolveError);

            // Idempotency: walk persistent calls.  Fast public-API pass
            // first to short-circuit on target / method mismatch; the
            // SerializedObject pass then verifies mode and string arg.
            int existingCount = eventBase.GetPersistentEventCount();
            for (int i = 0; i < existingCount; i++)
            {
                UnityEngine.Object t = eventBase.GetPersistentTarget(i);
                string m = eventBase.GetPersistentMethodName(i);
                if (t == targetComp && m == request.method)
                {
                    if (PersistentArgumentMatches(sourceComp, i, request.arg))
                        return BuildSuccess(
                            "EDITOR_CTRL_UDON_WIRE_NOOP",
                            "Existing matching persistent listener; no change.",
                            new EditorControlData
                            {
                                selected_object = request.hierarchy_path,
                                executed = false,
                                read_only = false,
                            });
                }
            }

            UnityAction<string> action;
            try
            {
                action = (UnityAction<string>)Delegate.CreateDelegate(
                    typeof(UnityAction<string>), targetComp, methodInfo);
            }
            catch (Exception ex)
            {
                return BuildError(
                    "EDITOR_CTRL_UDON_WIRE_METHOD_NOT_FOUND",
                    $"Could not bind {request.method} on {targetComp.GetType().Name} " +
                    $"as UnityAction<string>: {ex.Message}");
            }

            UnityEventTools.AddStringPersistentListener(eventBase, action, request.arg);
            EditorUtility.SetDirty(sourceComp);

            return BuildSuccess(
                "EDITOR_CTRL_UDON_WIRE_OK",
                $"Wired {sourceComp.GetType().Name}.{request.event_path} → " +
                $"{targetComp.GetType().Name}.{request.method}(\"{request.arg}\").",
                new EditorControlData
                {
                    selected_object = request.hierarchy_path,
                    asset_path = sourceComp.GetType().FullName,
                    executed = true,
                    read_only = false,
                });
        }

        private static string ResolveSourceEvent(
            GameObject go,
            string eventName,
            out Component foundComp,
            out UnityEventBase foundEvent)
        {
            foundComp = null;
            foundEvent = null;
            const BindingFlags flags = BindingFlags.Instance
                | BindingFlags.Public | BindingFlags.NonPublic;
            foreach (var comp in go.GetComponents<Component>())
            {
                if (comp == null) continue;
                Type t = comp.GetType();
                FieldInfo f = ResolveDeclaredField(t, eventName);
                if (f != null && typeof(UnityEventBase).IsAssignableFrom(f.FieldType))
                {
                    foundComp = comp;
                    foundEvent = (UnityEventBase)f.GetValue(comp);
                    if (foundEvent != null) return null;
                }
                PropertyInfo p = t.GetProperty(eventName, flags);
                if (p != null && typeof(UnityEventBase).IsAssignableFrom(p.PropertyType))
                {
                    foundComp = comp;
                    foundEvent = (UnityEventBase)p.GetValue(comp, null);
                    if (foundEvent != null) return null;
                }
            }
            return $"No UnityEvent field/property named '{eventName}' on " +
                   $"{go.name}.";
        }

        private static string ResolveTargetStringMethod(
            GameObject go,
            string methodName,
            out Component foundComp,
            out MethodInfo foundMethod)
        {
            foundComp = null;
            foundMethod = null;
            const BindingFlags flags = BindingFlags.Instance
                | BindingFlags.Public | BindingFlags.NonPublic;
            foreach (var comp in go.GetComponents<Component>())
            {
                if (comp == null) continue;
                MethodInfo m = comp.GetType().GetMethod(
                    methodName, flags, null,
                    new Type[] { typeof(string) }, null);
                if (m != null && m.ReturnType == typeof(void))
                {
                    foundComp = comp;
                    foundMethod = m;
                    return null;
                }
            }
            return $"No void {methodName}(string) method on {go.name}.";
        }

        private static bool PersistentArgumentMatches(
            Component sourceComp, int index, string expectedArg)
        {
            try
            {
                var so = new SerializedObject(sourceComp);
                SerializedProperty calls = so.FindProperty("m_PersistentCalls.m_Calls");
                if (calls == null || !calls.isArray) return false;
                if (index < 0 || index >= calls.arraySize) return false;
                SerializedProperty elem = calls.GetArrayElementAtIndex(index);
                SerializedProperty mode = elem.FindPropertyRelative("m_Mode");
                SerializedProperty argStr = elem.FindPropertyRelative(
                    "m_Arguments.m_StringArgument");
                if (mode == null || argStr == null) return false;
                if (mode.intValue != PersistentListenerModeString) return false;
                return argStr.stringValue == (expectedArg ?? string.Empty);
            }
            catch (Exception ex)
            {
                // Idempotency-check failure must surface in the editor
                // log: silently returning ``false`` would let the caller
                // add a *duplicate* persistent listener — exactly the
                // behaviour the idempotency walk exists to prevent.
                Debug.LogWarning(
                    $"[PrefabSentinel] PersistentArgumentMatches: " +
                    $"exception at index {index}: {ex.GetType().Name}: {ex.Message}");
                return false;
            }
        }
    }
}
