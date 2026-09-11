using System;

namespace PrefabSentinel
{
    internal enum UnityIntegrationTestProfile
    {
        Default,
        BridgeAcceptance,
    }

    internal sealed class UnityAcceptanceRequest
    {
        private const string DefaultProfile = "default";
        private const string AcceptanceProfile = "bridge_acceptance";

        private UnityAcceptanceRequest(
            UnityIntegrationTestProfile profile,
            bool runLiveProbes,
            string runId)
        {
            Profile = profile;
            RunLiveProbes = runLiveProbes;
            RunId = runId;
        }

        public UnityIntegrationTestProfile Profile { get; }
        public bool RunLiveProbes { get; }
        public string RunId { get; }

        public static bool TryCreate(
            string testProfile,
            bool runLiveProbes,
            string runId,
            out UnityAcceptanceRequest request,
            out string errorCode,
            out string errorMessage)
        {
            string profile = testProfile;
            string normalizedRunId = runId ?? string.Empty;

            request = new UnityAcceptanceRequest(
                UnityIntegrationTestProfile.Default,
                false,
                string.Empty);
            errorCode = string.Empty;
            errorMessage = string.Empty;

            if (profile != DefaultProfile && profile != AcceptanceProfile)
            {
                errorCode = "EDITOR_CTRL_TEST_PROFILE_INVALID";
                errorMessage =
                    "test_profile must be 'default' or 'bridge_acceptance'.";
                return false;
            }

            if (profile == DefaultProfile)
            {
                if (runLiveProbes)
                {
                    errorCode = "EDITOR_CTRL_TEST_LIVE_PROBES_INVALID";
                    errorMessage =
                        "test_profile='default' requires run_live_probes=false.";
                    return false;
                }

                if (normalizedRunId.Length != 0)
                {
                    errorCode = "EDITOR_CTRL_TEST_RUN_ID_INVALID";
                    errorMessage = "test_profile='default' requires an empty run_id.";
                    return false;
                }

                return true;
            }

            if (!runLiveProbes)
            {
                errorCode = "EDITOR_CTRL_TEST_LIVE_PROBES_REQUIRED";
                errorMessage =
                    "test_profile='bridge_acceptance' requires run_live_probes=true.";
                return false;
            }

            if (!IsLowercaseHexRunId(normalizedRunId))
            {
                errorCode = "EDITOR_CTRL_TEST_RUN_ID_INVALID";
                errorMessage =
                    "test_profile='bridge_acceptance' requires run_id to be exactly "
                    + "32 lowercase hexadecimal characters.";
                return false;
            }

            request = new UnityAcceptanceRequest(
                UnityIntegrationTestProfile.BridgeAcceptance,
                true,
                normalizedRunId);
            return true;
        }

        private static bool IsLowercaseHexRunId(string runId)
        {
            if (runId.Length != 32) return false;

            for (int index = 0; index < runId.Length; index++)
            {
                char character = runId[index];
                bool isDigit = character >= '0' && character <= '9';
                bool isLowerHex = character >= 'a' && character <= 'f';
                if (!isDigit && !isLowerHex) return false;
            }

            return true;
        }
    }
}
