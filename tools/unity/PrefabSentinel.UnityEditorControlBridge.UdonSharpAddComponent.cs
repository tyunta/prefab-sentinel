using System;
using System.Collections.Generic;
using System.Reflection;
using UnityEditor;
using UnityEngine;

namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
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

            if (!TryResolveGameObjectInActiveStage(
                request.hierarchy_path, out GameObject go, out var ambiguity))
            {
                if (ambiguity != null) return ambiguity;
                return BuildError(
                    "EDITOR_CTRL_UDON_ADD_NOT_FOUND",
                    $"GameObject not found at hierarchy_path: {request.hierarchy_path}");
            }

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

            // Issue #46: confirm the target type's UdonSharp program asset
            // exists and is compiled before any scene mutation. Adding the
            // component without a program asset silently attaches a plain
            // MonoBehaviour rather than an UdonBehaviour, and an uncompiled
            // program asset cannot back a working UdonBehaviour. Both are
            // surfaced as actionable envelopes naming the corrective step
            // instead of letting the add proceed into a broken state.
            EditorControlResponse programAssetErr =
                CheckUdonProgramAssetReady(compType);
            if (programAssetErr != null) return programAssetErr;

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
        /// Issue #46: pre-check that <paramref name="compType"/> has a
        /// compiled UdonSharp program asset. Returns <c>null</c> when the
        /// type is ready to add, an actionable error envelope otherwise.
        ///
        /// The program asset is located by walking
        /// ``UdonSharpProgramAsset.GetAllUdonSharpPrograms()`` and
        /// matching ``sourceCsScript.GetClass()`` against the target
        /// type — the same lookup ``editor_add_component`` uses to flag a
        /// missing program asset. Compiled state is read from the
        /// asset's ``SerializedProgramAsset`` property, which UdonSharp
        /// leaves null until the program is compiled.
        /// </summary>
        private static EditorControlResponse CheckUdonProgramAssetReady(
            Type compType)
        {
            Type programAssetType = null;
            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                programAssetType = assembly.GetType(
                    "UdonSharp.UdonSharpProgramAsset", false);
                if (programAssetType != null) break;
            }
            // UdonSharp not loaded — the later UdonSharpEditorUtility check
            // owns that failure; treat the program-asset pre-check as
            // inconclusive and let dispatch continue.
            if (programAssetType == null) return null;

            MethodInfo getAllPrograms = programAssetType.GetMethod(
                "GetAllUdonSharpPrograms",
                BindingFlags.Public | BindingFlags.Static);
            PropertyInfo csScriptProp = programAssetType.GetProperty(
                "sourceCsScript",
                BindingFlags.Public | BindingFlags.Instance);
            if (getAllPrograms == null || csScriptProp == null) return null;

            Array programs = getAllPrograms.Invoke(null, null) as Array;
            object matched = null;
            if (programs != null)
            {
                foreach (object program in programs)
                {
                    MonoScript script = csScriptProp.GetValue(program) as MonoScript;
                    if (script != null && script.GetClass() == compType)
                    {
                        matched = program;
                        break;
                    }
                }
            }

            if (matched == null)
                return BuildError(
                    "EDITOR_CTRL_UDON_ADD_NO_PROGRAM_ASSET",
                    $"No UdonSharp program asset exists for {compType.Name}. " +
                    "Create one with editor_create_udon_program_asset, then " +
                    "run editor_recompile, and retry editor_add_udonsharp_component.");

            // ``SerializedProgramAsset`` is null until the program is
            // compiled; a present-but-uncompiled asset cannot back a
            // working UdonBehaviour.
            PropertyInfo serializedProp = programAssetType.GetProperty(
                "SerializedProgramAsset",
                BindingFlags.Public | BindingFlags.Instance);
            if (serializedProp != null)
            {
                object serialized = serializedProp.GetValue(matched);
                if (serialized == null
                    || (serialized is UnityEngine.Object uo && uo == null))
                {
                    return BuildError(
                        "EDITOR_CTRL_UDON_ADD_PROGRAM_NOT_COMPILED",
                        $"The UdonSharp program asset for {compType.Name} " +
                        "exists but is not compiled. Run editor_recompile, " +
                        "then retry editor_add_udonsharp_component.");
                }
            }

            return null;
        }

        private static int ComputeComponentIndex(GameObject go, Component proxy, Type compType)
        {
            Component[] all = go.GetComponents(compType);
            for (int i = 0; i < all.Length; i++)
                if (all[i] == proxy) return i;
            return -1;
        }
    }
}
