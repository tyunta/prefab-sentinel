// RunScriptCompile diagnostic redaction — Unity-free extractions from the
// recompile catch sites (issues #204 / #214 / H-7). The caller-visible
// envelope must never carry Unity exception text; the full detail is routed
// to the Unity console sink inside the bridge catch site only.
namespace PrefabSentinel
{
    /// <summary>
    /// Supplies the fixed schedule-failure message for a rejected
    /// compilation request, carrying no exception text.
    /// </summary>
    internal static class ScheduleFailureEnvelope
    {
        internal const string Message =
            "editor_recompile_and_wait: failed to schedule compilation.";

        public static string RedactedMessage()
        {
            return Message;
        }
    }
}
