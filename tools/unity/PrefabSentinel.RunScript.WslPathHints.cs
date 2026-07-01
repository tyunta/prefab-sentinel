using System;
using System.Linq;
using System.Text.RegularExpressions;

namespace PrefabSentinel
{
    [Serializable]
    public sealed class WslPathHint
    {
        public string detected_path = string.Empty;
        public string windows_path = string.Empty;
        public string asset_relative_path = string.Empty;
        public string application_data_path = string.Empty;

        public string DetectedPath => detected_path;
        public string WindowsPath => windows_path;
        public string AssetRelativePath => asset_relative_path;
        public string ApplicationDataPath => application_data_path;
    }

    public static class WslPathHintDetector
    {
        private static readonly Regex MountedDrivePath = new(
            @"/mnt/([A-Za-z])(/.*?)(?=$|[""'<>\r\n])",
            RegexOptions.Compiled);

        private static readonly Regex SpacedWindowsPath = new(
            @"[A-Za-z]:[\\/].*?(?=$|[""'<>\r\n])",
            RegexOptions.Compiled);

        private static readonly Regex SpacedUncPath = new(
            @"\\\\.*?(?=$|[""'<>\r\n])",
            RegexOptions.Compiled);

        private static readonly Regex AbsolutePath = new(
            @"(/(?!/)(?:[^\s""'<>/)]*/)[^\s""'<>)]*)",
            RegexOptions.Compiled);

        public static WslPathHint[] FindHints(string source, string exceptionText = "")
        {
            string combined = string.Join("\n", new[] { source, exceptionText });
            return MountedDrivePath.Matches(combined)
                .Cast<Match>()
                .Select(match => BuildHint(TrimPath(match.Value), match.Groups[1].Value))
                .Where(hint => !string.IsNullOrEmpty(hint.detected_path))
                .GroupBy(hint => hint.detected_path)
                .Select(group => group.First())
                .ToArray();
        }

        public static WslPathHint[] FindHints(string source, Exception exception)
        {
            return FindHints(source, exception != null ? exception.ToString() : string.Empty);
        }

        public static string RedactMountedDrivePaths(string text)
        {
            if (string.IsNullOrEmpty(text))
                return string.Empty;
            return MountedDrivePath.Replace(text, "<wsl-path>");
        }

        public static string RedactAbsolutePaths(string text)
        {
            if (string.IsNullOrEmpty(text))
                return string.Empty;
            string redacted = RedactMountedDrivePaths(text);
            redacted = SpacedWindowsPath.Replace(redacted, "<absolute-path>");
            redacted = SpacedUncPath.Replace(redacted, "<absolute-path>");
            return AbsolutePath.Replace(redacted, match =>
                match.Value == "<wsl-path>" ? match.Value : "<absolute-path>");
        }

        private static WslPathHint BuildHint(string detectedPath, string drive)
        {
            if (string.IsNullOrEmpty(detectedPath) || detectedPath.Length <= "/mnt/x".Length)
                return new WslPathHint();

            string relative = detectedPath.Substring("/mnt/x/".Length);
            string windowsPath = drive.ToUpperInvariant() + @":\" + relative.Replace('/', '\\');
            string assetRelative = AssetRelativePath(detectedPath);
            return new WslPathHint
            {
                detected_path = detectedPath,
                windows_path = windowsPath,
                asset_relative_path = assetRelative,
                application_data_path = ApplicationDataPath(assetRelative),
            };
        }

        private static string AssetRelativePath(string detectedPath)
        {
            const string marker = "/Assets/";
            int index = detectedPath.IndexOf(marker, StringComparison.Ordinal);
            if (index < 0)
                return string.Empty;
            return detectedPath.Substring(index + 1);
        }

        private static string ApplicationDataPath(string assetRelative)
        {
            const string assetsPrefix = "Assets/";
            if (!assetRelative.StartsWith(assetsPrefix, StringComparison.Ordinal))
                return string.Empty;
            string underAssets = assetRelative.Substring(assetsPrefix.Length);
            return "Application.dataPath + \"/" + underAssets + "\"";
        }

        private static string TrimPath(string path)
        {
            return path.TrimEnd('.', ',', ';', ':', ')', ']');
        }
    }
}
