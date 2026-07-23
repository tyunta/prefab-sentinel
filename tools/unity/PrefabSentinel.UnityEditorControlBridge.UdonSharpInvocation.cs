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

private static bool TryInvokeUniqueUdonSharpProxyMethod(
            Type utilityType, string methodName, Component proxy,
            out object result, out string error)
        {
            result = null;
            error = null;
            MethodInfo selected = null;
            foreach (MethodInfo candidate in utilityType.GetMethods(
                         BindingFlags.Public | BindingFlags.Static))
            {
                if (!string.Equals(candidate.Name, methodName, StringComparison.Ordinal))
                    continue;
                ParameterInfo[] parameters = candidate.GetParameters();
                if (parameters.Length != 1
                    || !parameters[0].ParameterType.IsAssignableFrom(proxy.GetType()))
                    continue;
                if (selected != null)
                {
                    error = $"More than one compatible public static {methodName} overload was found.";
                    return false;
                }
                selected = candidate;
            }
            if (selected == null)
            {
                error = $"UdonSharpEditorUtility.{methodName} not found via reflection — UdonSharp version mismatch.";
                return false;
            }
            try
            {
                result = selected.Invoke(null, new object[] { proxy });
                return true;
            }
            catch (TargetInvocationException ex)
            {
                Debug.LogException(ex.InnerException ?? ex);
                error = $"{methodName} failed. Inspect the Unity console for details.";
                return false;
            }
            catch (Exception ex)
            {
                Debug.LogException(ex);
                error = $"{methodName} failed. Inspect the Unity console for details.";
                return false;
            }
        }

        internal static bool TrySynchronizeUdonSharpProxy(
            Component component, out string error)
        {
            error = null;
            if (component == null)
            {
                error = "UdonSharp proxy target resolved to null.";
                return false;
            }

            Type udonSharpBehaviourType = ResolveUdonSharpBehaviourType();
            if (udonSharpBehaviourType == null
                || !udonSharpBehaviourType.IsAssignableFrom(component.GetType()))
                return true;

            Type editorUtilityType = ResolveUdonSharpEditorUtilityType();
            if (editorUtilityType == null)
            {
                error = "UdonSharpEditorUtility is unavailable; backing UdonBehaviour sync is required.";
                return false;
            }
            if (!TryInvokeUniqueUdonSharpProxyMethod(
                    editorUtilityType, "IsProxyBehaviour", component,
                    out object isProxyResult, out error))
                return false;
            if (!(isProxyResult is bool isProxy) || !isProxy)
            {
                error = "Component is not a linked UdonSharp proxy.";
                return false;
            }
            return TryInvokeUniqueUdonSharpProxyMethod(
                editorUtilityType, "CopyProxyToUdon", component, out _, out error);
        }

        private static EditorControlResponse InvokeUdonSharpCopyProxyToUdon(
            Type editorUtilType, Component proxy)
        {
            if (TryInvokeUniqueUdonSharpProxyMethod(
                    editorUtilType, "CopyProxyToUdon", proxy, out _, out string error))
                return null;
            return BuildError("EDITOR_CTRL_UDON_ADD_FIELD_FAILED", error);
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


        /// <summary>
        /// Handle an existing UdonSharp proxy for editor_add_component.
        /// Complete pairs are reused; stranded proxies are repaired through
        /// UdonSharp's setup-with-Undo path and verified by re-reading the
        /// backing behaviour. Returns null only when no proxy exists; setup
        /// failures return a typed error and never fall through to a generic
        /// AddComponent call.
        /// </summary>

        private static EditorControlResponse HandleExistingUdonSharpAddComponent(
            GameObject go, Type compType, string hierarchyPath)
        {
            var proxy = go.GetComponent(compType);
            if (proxy == null) return null;

            Type editorUtilType = ResolveUdonSharpEditorUtilityType();
            if (editorUtilType == null)
            {
                return BuildError(
                    "EDITOR_CTRL_UDON_ADD_COMPONENT_FAILED",
                    "UdonSharpEditorUtility is not loaded; cannot safely reuse "
                    + $"the existing {compType.Name} proxy at {hierarchyPath}.");
            }

            MethodInfo getBacking = editorUtilType.GetMethod(
                "GetBackingUdonBehaviour",
                BindingFlags.Public | BindingFlags.Static
            );
            if (getBacking == null)
            {
                return BuildError(
                    "EDITOR_CTRL_UDON_ADD_COMPONENT_FAILED",
                    "UdonSharpEditorUtility.GetBackingUdonBehaviour is unavailable; "
                    + $"cannot safely reuse {compType.Name} at {hierarchyPath}.");
            }

            object backing;
            try
            {
                backing = getBacking.Invoke(null, new object[] { proxy });
            }
            catch (Exception ex)
            {
                Debug.LogWarning(
                    $"[PrefabSentinel] HandleExistingUdonSharpAddComponent: "
                    + $"{ex.GetType().Name}: {ex.Message}");
                return BuildError(
                    "EDITOR_CTRL_UDON_ADD_COMPONENT_FAILED",
                    "GetBackingUdonBehaviour failed while checking the existing "
                    + $"{compType.Name} proxy at {hierarchyPath}: "
                    + $"{ex.GetType().Name}. Inspect the Unity console for details.");
            }

            if (backing != null)
            {
                return BuildSuccess(
                    "EDITOR_CTRL_ADD_COMPONENT_REUSED",
                    $"Existing UdonSharp pair reused for {compType.Name}",
                    new EditorControlData
                    {
                        selected_object = go.name,
                        asset_path = compType.FullName,
                        executed = false,
                        read_only = false,
                    });
            }

            // UdonSharp's public CreateBehaviourForProxy does not create a
            // missing backing behaviour. The editor's own repair path calls
            // this internal setup entry, so invoke that exact operation and
            // verify the postcondition before reporting success.
            MethodInfo setupWithUndo = editorUtilType.GetMethod(
                "RunBehaviourSetupWithUndo",
                BindingFlags.NonPublic | BindingFlags.Static
            );
            if (setupWithUndo == null)
            {
                return BuildError(
                    "EDITOR_CTRL_UDON_ADD_COMPONENT_FAILED",
                    "UdonSharpEditorUtility.RunBehaviourSetupWithUndo is unavailable; "
                    + $"the stranded {compType.Name} proxy at {hierarchyPath} "
                    + "was not modified.");
            }

            try
            {
                setupWithUndo.Invoke(null, new object[] { proxy });
            }
            catch (Exception ex)
            {
                Debug.LogWarning(
                    $"[PrefabSentinel] HandleExistingUdonSharpAddComponent: "
                    + $"{ex.GetType().Name}: {ex.Message}");
                return BuildError(
                    "EDITOR_CTRL_UDON_ADD_COMPONENT_FAILED",
                    "RunBehaviourSetupWithUndo failed for the stranded "
                    + $"{compType.Name} proxy at {hierarchyPath}: "
                    + $"{ex.GetType().Name}. Inspect the Unity console for details.");
            }

            try
            {
                backing = getBacking.Invoke(null, new object[] { proxy });
            }
            catch (Exception ex)
            {
                Debug.LogWarning(
                    $"[PrefabSentinel] HandleExistingUdonSharpAddComponent: "
                    + $"{ex.GetType().Name}: {ex.Message}");
                return BuildError(
                    "EDITOR_CTRL_UDON_ADD_COMPONENT_FAILED",
                    "GetBackingUdonBehaviour failed after UdonSharp setup for "
                    + $"{compType.Name} at {hierarchyPath}: {ex.GetType().Name}. "
                    + "Inspect the Unity console for details.");
            }

            if (backing == null)
            {
                return BuildError(
                    "EDITOR_CTRL_UDON_ADD_COMPONENT_FAILED",
                    "RunBehaviourSetupWithUndo completed without creating a backing "
                    + $"UdonBehaviour for {compType.Name} at {hierarchyPath}.");
            }

            return BuildSuccess(
                "EDITOR_CTRL_ADD_COMPONENT_RELINKED",
                $"Existing proxy re-linked to new UdonBehaviour for {compType.Name}",
                new EditorControlData
                {
                    selected_object = go.name,
                    asset_path = compType.FullName,
                    executed = true,
                    read_only = false,
                });
        }
    }
}
