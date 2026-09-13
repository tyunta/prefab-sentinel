using System;

namespace PrefabSentinel
{
    internal static class EditorBridgeTerminalFailureBoundary
    {
        public static void ReportFailure(
            Exception exception,
            Action<Exception> reportPrivateFailure,
            Action<string> reportPublicFailure)
        {
            reportPrivateFailure(exception);
            reportPublicFailure("Editor Bridge request processing failed.");
        }
    }
}
