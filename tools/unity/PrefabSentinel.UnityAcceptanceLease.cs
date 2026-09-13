using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;

namespace PrefabSentinel
{
    [Serializable]
    internal sealed class UnityAcceptanceLeaseScene
    {
        public string path = string.Empty;
        public bool is_loaded;
        public bool is_active;
        public bool is_dirty;

        internal UnityAcceptanceLeaseScene Copy()
        {
            return new UnityAcceptanceLeaseScene
            {
                path = path,
                is_loaded = is_loaded,
                is_active = is_active,
                is_dirty = is_dirty,
            };
        }
    }

    [Serializable]
    internal sealed class UnityAcceptanceLeaseState
    {
        internal const int CurrentSchemaVersion = 1;
        internal const string ReservedPhase = "reserved";
        internal const string FixtureCreatedPhase = "fixture_created";
        internal const string SmokeCompletePhase = "smoke_complete";
        internal const string CleanupStartedPhase = "cleanup_started";
        internal const string CleanedPhase = "cleaned";

        internal const string FixtureRoot =
            "Assets/PrefabSentinelIntegrationTests";

        private static readonly string[] PhaseOrder =
        {
            ReservedPhase,
            FixtureCreatedPhase,
            SmokeCompletePhase,
            CleanupStartedPhase,
            CleanedPhase,
        };

        private static readonly string[] RequestArtifactSuffixes =
        {
            ".request.json",
            ".request.json.tmp",
            ".response.json",
            ".response.json.tmp",
            ".publication-failed.json",
        };

        public int schema_version;
        public string run_id = string.Empty;
        public string phase = string.Empty;
        public UnityAcceptanceLeaseScene[] original_scenes =
            Array.Empty<UnityAcceptanceLeaseScene>();
        public string[] fixture_paths = Array.Empty<string>();
        public string request_id = string.Empty;
        public string[] request_paths = Array.Empty<string>();
        public string created_utc = string.Empty;
        public string updated_utc = string.Empty;

        internal static UnityAcceptanceLeaseState Create(
            string runId,
            UnityAcceptanceLeaseScene[] originalScenes,
            string[] fixturePaths)
        {
            return Create(
                runId,
                originalScenes,
                fixturePaths,
                string.Empty,
                Array.Empty<string>());
        }

        internal static UnityAcceptanceLeaseState Create(
            string runId,
            UnityAcceptanceLeaseScene[] originalScenes,
            string[] fixturePaths,
            string requestId,
            string[] requestPaths)
        {
            if (!IsValidRunId(runId))
                throw new ArgumentException(
                    "runId must be exactly 32 lowercase hexadecimal characters.",
                    nameof(runId));

            string now = DateTimeOffset.UtcNow.ToString(
                "O",
                CultureInfo.InvariantCulture);
            var lease = new UnityAcceptanceLeaseState
            {
                schema_version = CurrentSchemaVersion,
                run_id = runId,
                phase = ReservedPhase,
                original_scenes = CopyScenes(originalScenes),
                fixture_paths = CopyStrings(fixturePaths),
                request_id = requestId ?? string.Empty,
                request_paths = CopyStrings(requestPaths),
                created_utc = now,
                updated_utc = now,
            };

            if (!lease.TryValidate(out _, out string errorMessage))
                throw new ArgumentException(errorMessage, nameof(originalScenes));
            return lease;
        }

        internal static string FixtureRootForRun(string runId)
        {
            if (!IsValidRunId(runId))
                throw new ArgumentException(
                    "runId must be exactly 32 lowercase hexadecimal characters.",
                    nameof(runId));
            return FixtureRoot + "/" + runId;
        }

        internal bool Owns(string path)
        {
            if (!IsValidRunId(run_id) || string.IsNullOrEmpty(path))
                return false;

            string normalized = path.Replace('\\', '/');
            if (normalized.StartsWith("/", StringComparison.Ordinal)
                || normalized.EndsWith("/", StringComparison.Ordinal))
            {
                return false;
            }

            string[] segments = normalized.Split('/');
            foreach (string segment in segments)
            {
                if (string.IsNullOrEmpty(segment)
                    || segment == "."
                    || segment == "..")
                {
                    return false;
                }
            }

            string root = FixtureRootForRun(run_id);
            return normalized == root
                || normalized.StartsWith(root + "/", StringComparison.Ordinal);
        }

        internal bool TryValidate(
            out string errorCode,
            out string errorMessage)
        {
            errorCode = string.Empty;
            errorMessage = string.Empty;

            if (schema_version != CurrentSchemaVersion
                || !IsValidRunId(run_id)
                || Array.IndexOf(PhaseOrder, phase) < 0)
            {
                return Invalid(
                    out errorCode,
                    out errorMessage,
                    "Acceptance lease schema, run ID, or phase is invalid.");
            }

            if (!TryParseTimestamp(created_utc, out DateTimeOffset created)
                || !TryParseTimestamp(updated_utc, out DateTimeOffset updated)
                || updated < created)
            {
                return Invalid(
                    out errorCode,
                    out errorMessage,
                    "Acceptance lease timestamps are invalid.");
            }

            if (original_scenes == null || original_scenes.Length == 0)
            {
                return Invalid(
                    out errorCode,
                    out errorMessage,
                    "Acceptance lease must retain at least one original Scene.");
            }

            int activeCount = 0;
            var scenePaths = new HashSet<string>(StringComparer.Ordinal);
            foreach (UnityAcceptanceLeaseScene scene in original_scenes)
            {
                if (scene == null
                    || string.IsNullOrEmpty(scene.path)
                    || !scenePaths.Add(scene.path)
                    || scene.is_dirty)
                {
                    return Invalid(
                        out errorCode,
                        out errorMessage,
                        "Acceptance lease original Scene state is invalid.");
                }

                if (scene.is_active)
                {
                    if (!scene.is_loaded)
                    {
                        return Invalid(
                            out errorCode,
                            out errorMessage,
                            "The active original Scene must be loaded.");
                    }
                    activeCount++;
                }
            }

            if (activeCount != 1)
            {
                return Invalid(
                    out errorCode,
                    out errorMessage,
                    "Acceptance lease must retain exactly one active Scene.");
            }

            if (fixture_paths == null || fixture_paths.Length == 0)
            {
                return Invalid(
                    out errorCode,
                    out errorMessage,
                    "Acceptance lease must retain task-owned fixture paths.");
            }

            var fixturePaths = new HashSet<string>(StringComparer.Ordinal);
            foreach (string fixturePath in fixture_paths)
            {
                if (!Owns(fixturePath) || !fixturePaths.Add(fixturePath))
                {
                    return Invalid(
                        out errorCode,
                        out errorMessage,
                        "Acceptance lease contains an unowned fixture path.");
                }
            }

            if (request_paths == null || request_paths.Length == 0)
            {
                return Invalid(
                    out errorCode,
                    out errorMessage,
                    "Acceptance lease request paths are missing.");
            }

            if (!IsValidRequestArtifactSet(
                request_id,
                request_paths))
            {
                return Invalid(
                    out errorCode,
                    out errorMessage,
                    "Acceptance lease request paths are invalid.");
            }

            return true;
        }

        internal bool TryAssertOwner(
            string runId,
            out string errorCode,
            out string errorMessage)
        {
            if (!IsValidRunId(runId))
            {
                errorCode = "EDITOR_CTRL_ACCEPTANCE_RUN_ID_INVALID";
                errorMessage =
                    "run_id must be exactly 32 lowercase hexadecimal characters.";
                return false;
            }

            if (!TryValidate(out errorCode, out errorMessage))
                return false;

            if (run_id != runId)
            {
                errorCode = "EDITOR_CTRL_ACCEPTANCE_RUN_MISMATCH";
                errorMessage =
                    "The active acceptance lease belongs to a different run.";
                return false;
            }

            return true;
        }

        internal bool TryAssertOwner(
            string runId,
            string currentRequestPath,
            out string errorCode,
            out string errorMessage)
        {
            if (!TryAssertOwner(runId, out errorCode, out errorMessage))
                return false;

            if (!IsValidRequestArtifactSet(
                request_id,
                request_paths,
                currentRequestPath))
            {
                return Invalid(
                    out errorCode,
                    out errorMessage,
                    "Acceptance lease request paths are outside the active Bridge watch directory.");
            }

            return true;
        }

        internal static string[] BuildRequestArtifactPaths(
            string trustedRequestPath,
            string requestId)
        {
            if (!IsValidRunId(requestId))
                throw new ArgumentException(
                    "requestId must be exactly 32 lowercase hexadecimal characters.",
                    nameof(requestId));

            if (!TryGetCanonicalParentDirectory(
                trustedRequestPath,
                out string trustedDirectory))
            {
                throw new ArgumentException(
                    "trustedRequestPath must identify a canonical request in a non-reparse directory.",
                    nameof(trustedRequestPath));
            }

            return BuildRequestArtifactPathsInDirectory(
                trustedDirectory,
                requestId);
        }

        internal bool TryDeleteRequestArtifacts(
            string currentRequestPath,
            out string errorCode,
            out string errorMessage)
        {
            if (!TryValidate(out errorCode, out errorMessage))
                return false;

            if (!TryGetCanonicalParentDirectory(
                currentRequestPath,
                out string trustedDirectory)
                || !IsValidRequestArtifactSet(
                    request_id,
                    request_paths,
                    currentRequestPath))
            {
                return Invalid(
                    out errorCode,
                    out errorMessage,
                    "Acceptance lease request paths are outside the active Bridge watch directory.");
            }

            for (int index = 0; index < RequestArtifactSuffixes.Length; index++)
            {
                if (!TryGetCanonicalParentDirectory(
                    currentRequestPath,
                    out string currentTrustedDirectory)
                    || !string.Equals(
                        trustedDirectory,
                        currentTrustedDirectory,
                        StringComparison.Ordinal))
                {
                    return Invalid(
                        out errorCode,
                        out errorMessage,
                        "The active Bridge watch directory changed during acceptance cleanup.");
                }

                // The parent check is repeated immediately before each delete.
                // System.IO cannot make this check-and-delete pair atomic, so a
                // concurrent filesystem mutation remains a residual TOCTOU risk.
                string deletePath = Path.Combine(
                    currentTrustedDirectory,
                    request_id + RequestArtifactSuffixes[index]);
                File.Delete(deletePath);
            }

            errorCode = string.Empty;
            errorMessage = string.Empty;
            return true;
        }

        internal bool TryTransition(
            string runId,
            string nextPhase,
            out string errorCode,
            out string errorMessage)
        {
            if (!TryAssertOwner(runId, out errorCode, out errorMessage))
                return false;

            int currentIndex = Array.IndexOf(PhaseOrder, phase);
            int nextIndex = Array.IndexOf(PhaseOrder, nextPhase);
            if (nextIndex != currentIndex + 1)
            {
                errorCode = "EDITOR_CTRL_ACCEPTANCE_PHASE_INVALID";
                errorMessage =
                    "Acceptance lease transitions must follow the fixed phase order.";
                return false;
            }

            phase = nextPhase;
            updated_utc = DateTimeOffset.UtcNow.ToString(
                "O",
                CultureInfo.InvariantCulture);
            return true;
        }

        internal bool TryBeginCleanup(
            string runId,
            out string errorCode,
            out string errorMessage)
        {
            if (!TryAssertOwner(runId, out errorCode, out errorMessage))
                return false;

            if (phase == CleanupStartedPhase)
                return true;

            if (phase != ReservedPhase
                && phase != FixtureCreatedPhase
                && phase != SmokeCompletePhase)
            {
                errorCode = "EDITOR_CTRL_ACCEPTANCE_PHASE_INVALID";
                errorMessage =
                    "Acceptance cleanup may begin only from an unfinished phase.";
                return false;
            }

            phase = CleanupStartedPhase;
            updated_utc = DateTimeOffset.UtcNow.ToString(
                "O",
                CultureInfo.InvariantCulture);
            return true;
        }

        internal static bool IsValidRunId(string runId)
        {
            if (runId == null || runId.Length != 32) return false;

            for (int index = 0; index < runId.Length; index++)
            {
                char character = runId[index];
                bool isDigit = character >= '0' && character <= '9';
                bool isLowerHex = character >= 'a' && character <= 'f';
                if (!isDigit && !isLowerHex) return false;
            }

            return true;
        }

        private static bool IsValidRequestArtifactSet(
            string requestId,
            string[] paths)
        {
            if (paths == null || paths.Length == 0)
                return false;
            return IsValidRequestArtifactSet(requestId, paths, paths[0]);
        }

        private static bool IsValidRequestArtifactSet(
            string requestId,
            string[] paths,
            string trustedRequestPath)
        {
            if (!IsValidRunId(requestId)
                || paths == null
                || paths.Length != RequestArtifactSuffixes.Length)
            {
                return false;
            }

            string directorySource = string.IsNullOrEmpty(trustedRequestPath)
                ? paths[0]
                : trustedRequestPath;
            if (!TryGetCanonicalParentDirectory(
                directorySource,
                out string trustedDirectory))
            {
                return false;
            }

            string[] expected = BuildRequestArtifactPathsInDirectory(
                trustedDirectory,
                requestId);
            var actual = new HashSet<string>(StringComparer.Ordinal);
            foreach (string path in paths)
            {
                if (!TryGetCanonicalParentDirectory(
                    path,
                    out string artifactDirectory)
                    || !string.Equals(
                        artifactDirectory,
                        trustedDirectory,
                        StringComparison.Ordinal))
                {
                    return false;
                }

                string fullPath = Path.GetFullPath(path);
                if (!actual.Add(fullPath))
                    return false;
            }

            return actual.SetEquals(expected);
        }

        private static string[] BuildRequestArtifactPathsInDirectory(
            string directory,
            string requestId)
        {
            var paths = new string[RequestArtifactSuffixes.Length];
            for (int index = 0; index < RequestArtifactSuffixes.Length; index++)
            {
                paths[index] = Path.Combine(
                    directory,
                    requestId + RequestArtifactSuffixes[index]);
            }
            return paths;
        }

        private static bool TryGetCanonicalParentDirectory(
            string path,
            out string directory)
        {
            directory = string.Empty;
            if (string.IsNullOrEmpty(path) || !Path.IsPathRooted(path))
                return false;

            try
            {
                string fullPath = Path.GetFullPath(path);
                if (!string.Equals(path, fullPath, StringComparison.Ordinal))
                    return false;

                string parentPath = Path.GetDirectoryName(fullPath) ?? string.Empty;
                if (string.IsNullOrEmpty(parentPath))
                    return false;

                string canonicalParent = Path.GetFullPath(parentPath);
                if (!string.Equals(
                    parentPath,
                    canonicalParent,
                    StringComparison.Ordinal))
                {
                    return false;
                }

                var parent = new DirectoryInfo(canonicalParent);
                parent.Refresh();
                if (!parent.Exists
                    || (parent.Attributes & FileAttributes.ReparsePoint) != 0
                    || !string.Equals(
                        parent.FullName,
                        canonicalParent,
                        StringComparison.Ordinal))
                {
                    return false;
                }

                directory = canonicalParent;
                return true;
            }
            catch (Exception ex) when (
                ex is ArgumentException
                || ex is NotSupportedException
                || ex is PathTooLongException
                || ex is UnauthorizedAccessException
                || ex is IOException
                || ex is System.Security.SecurityException)
            {
                return false;
            }
        }

        private static UnityAcceptanceLeaseScene[] CopyScenes(
            UnityAcceptanceLeaseScene[] scenes)
        {
            if (scenes == null)
                return Array.Empty<UnityAcceptanceLeaseScene>();

            var copied = new UnityAcceptanceLeaseScene[scenes.Length];
            for (int index = 0; index < scenes.Length; index++)
            {
                UnityAcceptanceLeaseScene scene = scenes[index];
                copied[index] = scene == null
                    ? new UnityAcceptanceLeaseScene()
                    : scene.Copy();
            }
            return copied;
        }

        private static string[] CopyStrings(string[] values)
        {
            if (values == null) return Array.Empty<string>();
            return (string[])values.Clone();
        }

        private static bool TryParseTimestamp(
            string value,
            out DateTimeOffset parsed)
        {
            return DateTimeOffset.TryParse(
                value,
                CultureInfo.InvariantCulture,
                DateTimeStyles.RoundtripKind,
                out parsed);
        }

        private static bool Invalid(
            out string errorCode,
            out string errorMessage,
            string message)
        {
            errorCode = "EDITOR_CTRL_ACCEPTANCE_LEASE_INVALID";
            errorMessage = message;
            return false;
        }
    }

    internal static class UnityAcceptanceSceneSetupComparer
    {
        internal static bool Matches(
            UnityAcceptanceLeaseScene[] expected,
            UnityAcceptanceLeaseScene[] actual)
        {
            if (expected == null
                || actual == null
                || expected.Length != actual.Length)
            {
                return false;
            }

            for (int index = 0; index < expected.Length; index++)
            {
                UnityAcceptanceLeaseScene left = expected[index];
                UnityAcceptanceLeaseScene right = actual[index];
                if (left == null
                    || right == null
                    || left.path != right.path
                    || left.is_loaded != right.is_loaded
                    || left.is_active != right.is_active
                    || left.is_dirty != right.is_dirty)
                {
                    return false;
                }
            }
            return true;
        }
    }

    internal static class UnityAcceptanceLeaseFile
    {
        internal static void Publish(string leasePath, string serializedLease)
        {
            if (string.IsNullOrEmpty(leasePath))
                throw new ArgumentException(
                    "A lease path is required.",
                    nameof(leasePath));
            if (serializedLease == null)
                throw new ArgumentNullException(nameof(serializedLease));

            string directory = Path.GetDirectoryName(leasePath) ?? string.Empty;
            if (string.IsNullOrEmpty(directory))
                throw new ArgumentException(
                    "The lease path must include a directory.",
                    nameof(leasePath));
            Directory.CreateDirectory(directory);

            string temporaryPath = leasePath
                + "."
                + Guid.NewGuid().ToString("N")
                + ".tmp";
            try
            {
                File.WriteAllText(temporaryPath, serializedLease);
                if (File.Exists(leasePath))
                    File.Replace(temporaryPath, leasePath, null);
                else
                    File.Move(temporaryPath, leasePath);
            }
            finally
            {
                if (File.Exists(temporaryPath))
                    File.Delete(temporaryPath);
            }
        }
    }

    internal sealed class UnityAcceptanceLeaseSummary
    {
        internal bool Success;
        internal string Code = string.Empty;
        internal string Message = string.Empty;
        internal string Phase = string.Empty;
        internal bool CleanupRequired;
        internal bool CleanupComplete;
        internal bool SceneSetupRestored;
        internal int DeletedFixtureCount;
        internal int DeletedRequestArtifactCount;
        internal bool LeaseRemoved;

        internal static UnityAcceptanceLeaseSummary Ok(
            string code,
            string message,
            string phase,
            bool cleanupRequired,
            bool cleanupComplete,
            bool sceneSetupRestored = false,
            int deletedFixtureCount = 0,
            int deletedRequestArtifactCount = 0,
            bool leaseRemoved = false)
        {
            return new UnityAcceptanceLeaseSummary
            {
                Success = true,
                Code = code,
                Message = message,
                Phase = phase,
                CleanupRequired = cleanupRequired,
                CleanupComplete = cleanupComplete,
                SceneSetupRestored = sceneSetupRestored,
                DeletedFixtureCount = deletedFixtureCount,
                DeletedRequestArtifactCount = deletedRequestArtifactCount,
                LeaseRemoved = leaseRemoved,
            };
        }

        internal static UnityAcceptanceLeaseSummary Error(
            string code,
            string message)
        {
            return new UnityAcceptanceLeaseSummary
            {
                Success = false,
                Code = code,
                Message = message,
            };
        }
    }
}
