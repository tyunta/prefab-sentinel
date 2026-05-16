using System;
using System.Collections.Generic;
using System.Reflection;
using UnityEditor;
using UnityEngine;

// UdonSharp reflection-based invocation helpers (Undo.AddComponent,
// CopyProxyToUdon, initial field application, program-asset path lookup).
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
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
    }
}
