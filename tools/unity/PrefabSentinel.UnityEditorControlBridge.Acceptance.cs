using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;

// Unity acceptance request execution, evidence projection, and recovery handlers.
namespace PrefabSentinel
{
    /// <summary>
    ///     Owns the bounded Unity acceptance request, evidence, status, and cleanup handlers.
    /// </summary>
    public static partial class UnityEditorControlBridge
    {
        private static AcceptanceCaseEvidence[] BuildAcceptanceCaseEvidence(
            UnityIntegrationTests.TestCaseResult[] cases)
        {
            var evidence = new List<AcceptanceCaseEvidence>();
            if (cases == null) return evidence.ToArray();
            foreach (UnityIntegrationTests.TestCaseResult testCase in cases)
            {
                if (testCase == null) continue;
                evidence.Add(new AcceptanceCaseEvidence
                {
                    name = testCase.name,
                    passed = testCase.passed,
                    code = testCase.passed
                        ? "ACCEPTANCE_CASE_OK"
                        : "ACCEPTANCE_CASE_FAILED",
                });
            }
            return evidence.ToArray();
        }

        private static EditorControlResponse HandleRunIntegrationTests(
            EditorControlRequest request,
            string requestPath,
            string responsePath)
        {
            UnityAcceptanceRequest acceptanceRequest;
            string validationCode;
            string validationMessage;
            if (!UnityAcceptanceRequest.TryCreate(
                request.test_profile,
                request.run_live_probes,
                request.run_id,
                out acceptanceRequest,
                out validationCode,
                out validationMessage))
            {
                return BuildError(validationCode, validationMessage);
            }

            try
            {
                string originalRequestId =
                    DeriveTransportRequestId(requestPath);
                var result = acceptanceRequest.Profile
                    == UnityIntegrationTestProfile.BridgeAcceptance
                    ? UnityIntegrationTests.RunAcceptanceTestSuite(
                        acceptanceRequest.RunId,
                        originalRequestId,
                        BuildAcceptanceRequestArtifactPaths(
                            requestPath,
                            responsePath,
                            originalRequestId))
                    : UnityIntegrationTests.RunTestSuite();
                string json = JsonUtility.ToJson(result, true);
                var data = new EditorControlData
                {
                    executed = true,
                    run_id = acceptanceRequest.RunId,
                    fixture_owned = result.data.fixture_owned,
                    lease_phase = result.data.lease_phase,
                    acceptance_total = result.data.total,
                    acceptance_passed = result.data.passed,
                    acceptance_failed = result.data.failed,
                    acceptance_cases =
                        BuildAcceptanceCaseEvidence(result.data.cases),
                };
                if (result.success)
                    return BuildSuccess("EDITOR_CTRL_TESTS_PASSED", json, data);
                return BuildError("EDITOR_CTRL_TESTS_FAILED", json, data);
            }
            catch (Exception ex)
            {
                Debug.LogWarning(
                    $"[PrefabSentinel] HandleRunIntegrationTests: integration-test suite threw: {ex}");
                return BuildError(
                    "EDITOR_CTRL_TESTS_ERROR",
                    "editor_run_tests: integration-test suite threw an exception.");
            }
        }

        private static string[] BuildAcceptanceRequestArtifactPaths(
            string requestPath,
            string responsePath,
            string originalRequestId)
        {
            string[] paths = UnityAcceptanceLeaseState.BuildRequestArtifactPaths(
                requestPath,
                originalRequestId);
            string canonicalResponsePath = Path.GetFullPath(responsePath);
            if (!string.Equals(
                paths[2],
                canonicalResponsePath,
                StringComparison.Ordinal))
            {
                throw new InvalidOperationException(
                    "Acceptance response path must match the run request UUID and watch directory.");
            }

            return paths;
        }

        private static EditorControlResponse HandleAcceptanceStatus(
            EditorControlRequest request,
            string requestPath)
        {
            UnityAcceptanceLeaseSummary summary =
                UnityIntegrationTests.GetAcceptanceLeaseStatus(
                    request.run_id,
                    requestPath);
            var data = new EditorControlData
            {
                executed = true,
                run_id = request.run_id,
                phase = summary.Phase,
                cleanup_required = summary.CleanupRequired,
                cleanup_performed = summary.CleanupComplete,
            };
            return summary.Success
                ? BuildSuccess(summary.Code, summary.Message, data)
                : BuildError(summary.Code, summary.Message, data);
        }

        private static EditorControlResponse HandleCleanupIntegrationTests(
            EditorControlRequest request,
            string requestPath)
        {
            UnityAcceptanceLeaseSummary summary =
                UnityIntegrationTests.CleanupAcceptanceLease(
                    request.run_id,
                    requestPath);
            var data = new EditorControlData
            {
                executed = true,
                run_id = request.run_id,
                phase = summary.Phase,
                cleanup_required = summary.CleanupRequired,
                cleanup_performed = summary.CleanupComplete,
                scene_setup_restored = summary.SceneSetupRestored,
                deleted_fixture_count = summary.DeletedFixtureCount,
                deleted_request_artifact_count =
                    summary.DeletedRequestArtifactCount,
                lease_removed = summary.LeaseRemoved,
            };
            return summary.Success
                ? BuildSuccess(summary.Code, summary.Message, data)
                : BuildError(summary.Code, summary.Message, data);
        }
    }
}
