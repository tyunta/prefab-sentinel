using System;
using System.Globalization;
using System.IO;
using System.Text;

namespace PrefabSentinel
{
    internal static partial class EditorBridgeWatchIdentity
    {
        public const string MarkerFileName =
            ".prefab-sentinel-watch-identity";
        public const string StatusRelativePath =
            "Library/PrefabSentinel/bridge-status-v1.json";
        public const int SchemaVersion = 1;
        public const int StatusMaxBytes = 4096;
        public const int HeartbeatIntervalMilliseconds = 1000;

        private static readonly Encoding StrictStatusEncoding =
            new UTF8Encoding(false, true);
        private static readonly object StatusGate = new object();

        public static bool TryEnsureMarker(
            string watchDirectory,
            Func<string> createIdentity,
            Action<Exception> reportPrivateFailure,
            out string identity)
        {
            identity = string.Empty;
            string markerPath = Path.Combine(watchDirectory, MarkerFileName);

            try
            {
                ArtifactReadState initialState =
                    ReadMarker(markerPath, out string existingIdentity);
                if (initialState == ArtifactReadState.Valid)
                {
                    identity = existingIdentity;
                    return true;
                }

                if (initialState == ArtifactReadState.Invalid)
                {
                    return false;
                }

                string candidate = createIdentity();
                if (!IsValidIdentity(candidate))
                {
                    return false;
                }

                FileStream stream;
                try
                {
                    stream = new FileStream(
                        markerPath,
                        FileMode.CreateNew,
                        FileAccess.Write,
                        FileShare.Read);
                }
                catch (IOException collision)
                {
                    ArtifactReadState winnerState =
                        ReadMarker(markerPath, out string winnerIdentity);
                    if (winnerState == ArtifactReadState.Valid)
                    {
                        identity = winnerIdentity;
                        return true;
                    }

                    if (winnerState == ArtifactReadState.Missing)
                    {
                        reportPrivateFailure(collision);
                    }

                    return false;
                }

                using (stream)
                {
                    byte[] bytes = StrictStatusEncoding.GetBytes(candidate);
                    stream.Write(bytes, 0, bytes.Length);
                    stream.Flush(flushToDisk: true);
                }

                identity = candidate;
                return true;
            }
            catch (Exception exception)
            {
                reportPrivateFailure(exception);
                return false;
            }
        }

        public static bool TryPublishStatus(
            string projectRoot,
            string watchIdentity,
            string bridgeSessionId,
            string bridgeInstanceId,
            long updatedAtUnixMs,
            Action<Exception> reportPrivateFailure)
        {
            return TryPublishStatus(
                projectRoot,
                watchIdentity,
                bridgeSessionId,
                bridgeInstanceId,
                updatedAtUnixMs,
                PromoteTemporaryStatus,
                reportPrivateFailure);
        }

        internal static bool TryPublishStatus(
            string projectRoot,
            string watchIdentity,
            string bridgeSessionId,
            string bridgeInstanceId,
            long updatedAtUnixMs,
            Action<string, string> promoteTemporaryStatus,
            Action<Exception> reportPrivateFailure)
        {
            lock (StatusGate)
            {
                return TryPublishStatusLocked(
                    projectRoot,
                    watchIdentity,
                    bridgeSessionId,
                    bridgeInstanceId,
                    updatedAtUnixMs,
                    promoteTemporaryStatus,
                    reportPrivateFailure);
            }
        }

        private static bool TryPublishStatusLocked(
            string projectRoot,
            string watchIdentity,
            string bridgeSessionId,
            string bridgeInstanceId,
            long updatedAtUnixMs,
            Action<string, string> promoteTemporaryStatus,
            Action<Exception> reportPrivateFailure)
        {
            if (!IsValidIdentity(watchIdentity)
                || !IsValidIdentity(bridgeSessionId)
                || !IsValidIdentity(bridgeInstanceId))
            {
                return false;
            }

            string payload = BuildStatusJson(
                watchIdentity,
                bridgeSessionId,
                bridgeInstanceId,
                updatedAtUnixMs);
            byte[] bytes = StrictStatusEncoding.GetBytes(payload);
            if (bytes.Length > StatusMaxBytes)
            {
                return false;
            }

            string statusPath = GetStatusPath(projectRoot);
            string temporaryPath = statusPath + ".tmp";

            try
            {
                string statusDirectory =
                    Path.GetDirectoryName(statusPath)
                    ?? throw new InvalidOperationException(
                        "Private status path has no parent directory.");
                Directory.CreateDirectory(statusDirectory);

                File.Delete(temporaryPath);
                using (var stream = new FileStream(
                    temporaryPath,
                    FileMode.CreateNew,
                    FileAccess.Write,
                    FileShare.None))
                {
                    stream.Write(bytes, 0, bytes.Length);
                    stream.Flush(flushToDisk: true);
                }

                promoteTemporaryStatus(temporaryPath, statusPath);
                return true;
            }
            catch (Exception exception)
            {
                TryDeleteTemporarySibling(temporaryPath);
                reportPrivateFailure(exception);
                return false;
            }
        }

        private static void PromoteTemporaryStatus(
            string temporaryPath,
            string statusPath)
        {
            ArtifactReadState targetState = InspectRegularFile(
                statusPath,
                minimumLength: 1,
                maximumLength: StatusMaxBytes);
            if (targetState == ArtifactReadState.Invalid)
            {
                throw new InvalidDataException(
                    "Private status target is not a regular file.");
            }

            if (targetState == ArtifactReadState.Valid)
            {
                File.Replace(temporaryPath, statusPath, null);
            }
            else
            {
                File.Move(temporaryPath, statusPath);
            }
        }

        public static bool TryDeleteOwnedStatus(
            string projectRoot,
            string bridgeInstanceId,
            Action<Exception> reportPrivateFailure)
        {
            return TryDeleteOwnedStatus(
                projectRoot,
                bridgeInstanceId,
                () => { },
                reportPrivateFailure);
        }

        internal static bool TryDeleteOwnedStatus(
            string projectRoot,
            string bridgeInstanceId,
            Action afterOwnershipValidated,
            Action<Exception> reportPrivateFailure)
        {
            if (!IsValidIdentity(bridgeInstanceId))
            {
                return false;
            }

            lock (StatusGate)
            {
                string statusPath = GetStatusPath(projectRoot);
                try
                {
                    ArtifactReadState state = ReadStatus(
                        statusPath,
                        out string ownerInstanceId);
                    if (state != ArtifactReadState.Valid
                        || !string.Equals(
                            ownerInstanceId,
                            bridgeInstanceId,
                            StringComparison.Ordinal))
                    {
                        return false;
                    }

                    afterOwnershipValidated();
                    File.Delete(statusPath);
                    return true;
                }
                catch (Exception exception)
                {
                    reportPrivateFailure(exception);
                    return false;
                }
            }
        }

        private static string BuildStatusJson(
            string watchIdentity,
            string bridgeSessionId,
            string bridgeInstanceId,
            long updatedAtUnixMs)
        {
            return "{"
                + "\"schema_version\":1,"
                + "\"watch_identity\":\"" + watchIdentity + "\","
                + "\"bridge_session_id\":\"" + bridgeSessionId + "\","
                + "\"bridge_instance_id\":\"" + bridgeInstanceId + "\","
                + "\"updated_at_unix_ms\":"
                + updatedAtUnixMs.ToString(CultureInfo.InvariantCulture)
                + "}";
        }

        private static string GetStatusPath(string projectRoot)
        {
            return Path.Combine(
                projectRoot,
                StatusRelativePath.Replace('/', Path.DirectorySeparatorChar));
        }

        private static void TryDeleteTemporarySibling(string temporaryPath)
        {
            try
            {
                File.Delete(temporaryPath);
            }
            catch
            {
                // Preserve the publication failure as the private diagnostic.
            }
        }
    }
}
