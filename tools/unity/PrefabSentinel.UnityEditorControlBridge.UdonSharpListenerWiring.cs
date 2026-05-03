using System;
using System.Reflection;
using UnityEditor;
using UnityEditor.Events;
using UnityEngine;
using UnityEngine.Events;

namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        // PersistentListenerMode.String — see UnityEngine.Events.
        // Hard-coded to avoid a UnityEngine.Events.PersistentListenerMode
        // namespace collision with our reflective UdonSharp helpers.
        private const int PersistentListenerModeString = 5;

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
