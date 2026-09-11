using System;
using System.Globalization;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

namespace PrefabSentinel
{
    internal static partial class EditorBridgeWatchIdentity
    {
        private static readonly Regex StatusPattern = new Regex(
            "^\\{\"schema_version\":1,"
                + "\"watch_identity\":\"([0-9a-f]{32})\","
                + "\"bridge_session_id\":\"([0-9a-f]{32})\","
                + "\"bridge_instance_id\":\"([0-9a-f]{32})\","
                + "\"updated_at_unix_ms\":(0|-?[1-9][0-9]*)\\}\\z",
            RegexOptions.CultureInvariant);

        private static bool IsValidIdentity(string value)
        {
            if (value == null || value.Length != 32)
            {
                return false;
            }

            for (int index = 0; index < value.Length; index++)
            {
                char character = value[index];
                if (!((character >= '0' && character <= '9')
                    || (character >= 'a' && character <= 'f')))
                {
                    return false;
                }
            }

            return true;
        }

        private static ArtifactReadState ReadMarker(
            string markerPath,
            out string identity)
        {
            identity = string.Empty;
            ArtifactReadState state = InspectRegularFile(
                markerPath,
                minimumLength: 32,
                maximumLength: 32);
            if (state != ArtifactReadState.Valid)
            {
                return state;
            }

            byte[] bytes = new byte[33];
            int length = 0;
            using (var stream = new FileStream(
                markerPath,
                FileMode.Open,
                FileAccess.Read,
                FileShare.ReadWrite | FileShare.Delete))
            {
                if (!stream.CanSeek || stream.Length != 32)
                {
                    return ArtifactReadState.Invalid;
                }

                while (length < bytes.Length)
                {
                    int read = stream.Read(bytes, length, bytes.Length - length);
                    if (read == 0)
                    {
                        break;
                    }

                    length += read;
                }
            }

            if (length != 32)
            {
                return ArtifactReadState.Invalid;
            }

            string candidate;
            try
            {
                candidate = StrictStatusEncoding.GetString(bytes, 0, length);
            }
            catch (DecoderFallbackException)
            {
                return ArtifactReadState.Invalid;
            }

            if (!IsValidIdentity(candidate))
            {
                return ArtifactReadState.Invalid;
            }

            identity = candidate;
            return ArtifactReadState.Valid;
        }

        private static ArtifactReadState ReadStatus(
            string statusPath,
            out string bridgeInstanceId)
        {
            bridgeInstanceId = string.Empty;
            ArtifactReadState state = InspectRegularFile(
                statusPath,
                minimumLength: 1,
                maximumLength: StatusMaxBytes);
            if (state != ArtifactReadState.Valid)
            {
                return state;
            }

            byte[] buffer = new byte[StatusMaxBytes + 1];
            int length = 0;
            using (var stream = new FileStream(
                statusPath,
                FileMode.Open,
                FileAccess.Read,
                FileShare.ReadWrite | FileShare.Delete))
            {
                if (!stream.CanSeek
                    || stream.Length < 1
                    || stream.Length > StatusMaxBytes)
                {
                    return ArtifactReadState.Invalid;
                }

                while (length < buffer.Length)
                {
                    int read = stream.Read(buffer, length, buffer.Length - length);
                    if (read == 0)
                    {
                        break;
                    }

                    length += read;
                }
            }

            if (length > StatusMaxBytes)
            {
                return ArtifactReadState.Invalid;
            }

            string payload;
            try
            {
                payload = StrictStatusEncoding.GetString(buffer, 0, length);
            }
            catch (DecoderFallbackException)
            {
                return ArtifactReadState.Invalid;
            }

            Match match = StatusPattern.Match(payload);
            if (!match.Success
                || !long.TryParse(
                    match.Groups[4].Value,
                    NumberStyles.AllowLeadingSign,
                    CultureInfo.InvariantCulture,
                    out _))
            {
                return ArtifactReadState.Invalid;
            }

            bridgeInstanceId = match.Groups[3].Value;
            return ArtifactReadState.Valid;
        }

        private static ArtifactReadState InspectRegularFile(
            string path,
            long minimumLength,
            long maximumLength)
        {
            try
            {
                FileAttributes attributes = File.GetAttributes(path);
                if ((attributes & FileAttributes.Directory) != 0
                    || (attributes & FileAttributes.ReparsePoint) != 0)
                {
                    return ArtifactReadState.Invalid;
                }

                // FileAttributes.Device is not a portable Unix discriminator.
                // The artifact's nonzero size contract rejects FIFO entries before
                // a blocking open; readers then revalidate the opened handle.
                long length = new FileInfo(path).Length;
                return length >= minimumLength && length <= maximumLength
                    ? ArtifactReadState.Valid
                    : ArtifactReadState.Invalid;
            }
            catch (FileNotFoundException)
            {
                return ArtifactReadState.Missing;
            }
            catch (DirectoryNotFoundException)
            {
                return ArtifactReadState.Missing;
            }
        }

        private enum ArtifactReadState
        {
            Missing,
            Invalid,
            Valid,
        }
    }
}
