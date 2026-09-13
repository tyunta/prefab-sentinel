using System;

namespace PrefabSentinel
{
    internal static class VRCSDKUploadFailureBoundary
    {
        private const string PublicCode = "VRCSDK_BUILD_FAILED";
        private const string PublicMessage =
            "VRC SDK build or upload failed. See the Unity Console for details.";

        public static void ReportFailure(
            Exception exception,
            Action<Exception> reportPrivateFailure,
            Action<string, string> reportPublicFailure)
        {
            reportPrivateFailure(exception);
            reportPublicFailure(PublicCode, PublicMessage);
        }
    }
}
