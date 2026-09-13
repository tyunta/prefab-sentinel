#nullable enable

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.Serialization.Json;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;

namespace PrefabSentinel
{
    internal static class BridgeDeployPromotionEvidence
    {
        private static StringComparison Comparison =>
            Path.DirectorySeparatorChar == '\\'
                ? StringComparison.OrdinalIgnoreCase
                : StringComparison.Ordinal;

        internal static bool ShareReportedVolume(string first, string second)
        {
            try
            {
                return ShareVolume(
                    first,
                    second,
                    DriveInfo.GetDrives()
                        .Select(drive => drive.RootDirectory.FullName));
            }
            catch (Exception)
            {
                return false;
            }
        }

        internal static bool ShareVolume(
            string first,
            string second,
            IEnumerable<string> roots)
        {
            return TryResolveVolumeRoot(first, roots, out var firstRoot)
                && TryResolveVolumeRoot(second, roots, out var secondRoot)
                && string.Equals(firstRoot, secondRoot, Comparison);
        }

        internal static bool TryResolveVolumeRoot(
            string path,
            IEnumerable<string> roots,
            out string root)
        {
            root = string.Empty;
            try
            {
                string canonicalPath = Path.GetFullPath(path);
                foreach (string candidate in roots)
                {
                    string canonicalRoot = CanonicalRoot(candidate);
                    if ((string.Equals(canonicalPath, canonicalRoot, Comparison)
                            || Contained(canonicalRoot, canonicalPath))
                        && canonicalRoot.Length > root.Length)
                    {
                        root = canonicalRoot;
                    }
                }
                return root.Length > 0;
            }
            catch (Exception)
            {
                root = string.Empty;
                return false;
            }
        }

        internal static string DecideRecovery(
            string state,
            bool targetExists,
            bool backupExists,
            bool stagingExists)
        {
            if (state == "ready" && targetExists && !backupExists && stagingExists)
            {
                return "pre_backup_intact";
            }
            if ((state == "ready" || state == "old_backed_up")
                && !targetExists && backupExists && stagingExists)
            {
                return "restore_backup";
            }
            if (state == "new_promoted"
                && targetExists && backupExists && !stagingExists)
            {
                return "move_promoted_then_restore";
            }
            return "ambiguous";
        }

        internal static bool IsRestoredLayout(
            bool targetExists,
            bool backupExists,
            bool stagingExists)
        {
            return targetExists && !backupExists && stagingExists;
        }

        internal static BridgeDeployPrivateManifest? ReadManifest(string path)
        {
            string document = File.ReadAllText(path, Encoding.UTF8);
            string canonical = document.EndsWith("\n", StringComparison.Ordinal)
                ? document.Substring(0, document.Length - 1)
                : document;
            var serializer = new DataContractJsonSerializer(
                typeof(BridgeDeployPrivateManifest));
            BridgeDeployPrivateManifest? manifest;
            using (var input = new MemoryStream(Encoding.UTF8.GetBytes(canonical)))
            {
                manifest = serializer.ReadObject(input)
                    as BridgeDeployPrivateManifest;
            }
            using (var output = new MemoryStream())
            {
                serializer.WriteObject(output, manifest);
                string roundTrip = Encoding.UTF8
                    .GetString(output.ToArray())
                    .Replace("\\/", "/");
                return roundTrip == canonical ? manifest : null;
            }
        }

        internal static bool ValidEntries(
            BridgeDeployManifestEntry[] entries,
            string aggregate)
        {
            if (entries.Length == 0)
            {
                return false;
            }
            string previous = string.Empty;
            foreach (var entry in entries)
            {
                if (string.IsNullOrEmpty(entry.path)
                    || entry.path.Contains("/")
                    || entry.path.Contains("\\")
                    || entry.path is "." or ".."
                    || entry.path.Any(char.IsControl)
                    || (!entry.path.EndsWith(".cs", StringComparison.Ordinal)
                        && !entry.path.EndsWith(".asmdef", StringComparison.Ordinal))
                    || entry.size < 0
                    || !LowerHex(entry.sha256, 64)
                    || (previous.Length > 0
                        && string.CompareOrdinal(previous, entry.path) >= 0))
                {
                    return false;
                }
                previous = entry.path;
            }
            using (var bytes = new MemoryStream())
            using (SHA256 sha = SHA256.Create())
            {
                foreach (var entry in entries)
                {
                    Write(bytes, entry.path);
                    bytes.WriteByte(0);
                    Write(
                        bytes,
                        entry.size.ToString(CultureInfo.InvariantCulture));
                    bytes.WriteByte(0);
                    Write(bytes, entry.sha256);
                }
                bytes.Position = 0;
                return Hex(sha.ComputeHash(bytes)) == aggregate;
            }
        }

        internal static bool Relative(
            string path,
            string requiredRoot,
            out string normalized)
        {
            normalized = string.Empty;
            if (string.IsNullOrEmpty(path)
                || path.IndexOf((char)0) >= 0
                || path.StartsWith("/")
                || path.StartsWith("\\")
                || Regex.IsMatch(path, "^[A-Za-z]:")
                || path.Contains("\\"))
            {
                return false;
            }
            string[] parts = path.Split('/');
            if (parts.Length == 0
                || parts[0] != requiredRoot
                || parts.Any(
                    part => part.Length == 0 || part is "." or ".."))
            {
                return false;
            }
            normalized = string.Join("/", parts);
            return true;
        }

        internal static string Full(string root, string path)
        {
            return Path.GetFullPath(
                Path.Combine(
                    root,
                    path.Replace('/', Path.DirectorySeparatorChar)));
        }

        internal static bool Contained(string root, string path)
        {
            string prefix = root.EndsWith(
                Path.DirectorySeparatorChar.ToString(),
                StringComparison.Ordinal)
                ? root
                : root + Path.DirectorySeparatorChar;
            return path.StartsWith(prefix, Comparison);
        }

        internal static bool Reparse(string root, string path)
        {
            string current = Path.GetFullPath(path);
            string boundary = Path.GetFullPath(root);
            while (true)
            {
                if ((File.GetAttributes(current)
                        & FileAttributes.ReparsePoint) != 0)
                {
                    return true;
                }
                if (string.Equals(current, boundary, Comparison))
                {
                    return false;
                }
                DirectoryInfo? parent = Directory.GetParent(current);
                if (parent == null || !Contained(boundary, current))
                {
                    return true;
                }
                current = parent.FullName;
            }
        }

        internal static bool LowerHex(string value, int length)
        {
            return value != null
                && value.Length == length
                && value.All(
                    character =>
                        character >= '0' && character <= '9'
                        || character >= 'a' && character <= 'f');
        }

        internal static string FileHash(string path)
        {
            using (var file = new FileStream(
                path,
                FileMode.Open,
                FileAccess.Read,
                FileShare.Read))
            using (SHA256 sha = SHA256.Create())
            {
                return Hex(sha.ComputeHash(file));
            }
        }

        private static string CanonicalRoot(string path)
        {
            string full = Path.GetFullPath(path);
            string trimmed = full.TrimEnd(
                Path.DirectorySeparatorChar,
                Path.AltDirectorySeparatorChar);
            return trimmed.Length == 0
                ? Path.DirectorySeparatorChar.ToString()
                : trimmed;
        }

        private static void Write(Stream stream, string value)
        {
            byte[] bytes = Encoding.UTF8.GetBytes(value);
            stream.Write(bytes, 0, bytes.Length);
        }

        private static string Hex(byte[] bytes)
        {
            return string.Concat(
                bytes.Select(
                    value => value.ToString(
                        "x2",
                        CultureInfo.InvariantCulture)));
        }
    }
}
