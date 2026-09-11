using System;
using System.IO;

#nullable enable

namespace PrefabSentinel.EditorBridge
{
    public sealed class EditorBridgeSetup
    {
        public string WatchDirectory { get; }
        public bool Enabled { get; }
        public bool CreateWatchDirectory { get; }

        private EditorBridgeSetup(
            string watchDirectory,
            bool enabled,
            bool createWatchDirectory)
        {
            WatchDirectory = watchDirectory;
            Enabled = enabled;
            CreateWatchDirectory = createWatchDirectory;
        }

        public static EditorBridgeSetup Resolve(
            string projectRoot,
            string? savedWatchDirectory,
            bool? savedEnabled)
        {
            bool enabled = savedEnabled ?? true;
            return new EditorBridgeSetup(
                savedWatchDirectory
                    ?? Path.Combine(
                        projectRoot,
                        "Library",
                        "PrefabSentinel",
                        "BridgeWatch"),
                enabled,
                savedWatchDirectory == null && enabled);
        }

        public static string PreferenceKey(string prefix, string projectRoot)
        {
            return prefix + "." + projectRoot;
        }

        public static string ConnectionInfo(
            string projectRoot,
            string watchDirectory,
            string instanceId)
        {
            RequireIdentity(instanceId);
            return "Project: " + projectRoot
                + "\nWatch Directory: " + watchDirectory
                + "\nInstance ID: " + instanceId;
        }

        public static string BashLaunchCommand(
            string projectRoot,
            string watchDirectory,
            string instanceId)
        {
            RequireIdentity(instanceId);
            string root = BashQuote(ToBashPath(projectRoot));
            string watch = BashQuote(ToBashPath(watchDirectory));
            return "env UNITYTOOL_UNITY_PROJECT_PATH=" + root
                + " UNITYTOOL_BRIDGE_WATCH_DIR=" + watch
                + " UNITYTOOL_BRIDGE_INSTANCE_ID=" + BashQuote(instanceId)
                + " codex -C " + root;
        }

        public static string PowerShellLaunchCommand(
            string projectRoot,
            string watchDirectory,
            string instanceId)
        {
            RequireIdentity(instanceId);
            return "$env:UNITYTOOL_UNITY_PROJECT_PATH="
                + PowerShellQuote(projectRoot)
                + "; $env:UNITYTOOL_BRIDGE_WATCH_DIR="
                + PowerShellQuote(watchDirectory)
                + "; $env:UNITYTOOL_BRIDGE_INSTANCE_ID="
                + PowerShellQuote(instanceId)
                + "; codex -C "
                + PowerShellQuote(projectRoot);
        }

        private static void RequireIdentity(string instanceId)
        {
            if (!IsLowerHexIdentity(instanceId))
                throw new ArgumentException(
                    "A valid Editor instance ID is required.",
                    nameof(instanceId));
        }

        private static bool IsLowerHexIdentity(string value)
        {
            if (value == null || value.Length != 32)
                return false;
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

        private static string ToBashPath(string path)
        {
            if (path.Length >= 3
                && char.IsLetter(path[0])
                && path[1] == ':'
                && (path[2] == '\\' || path[2] == '/'))
            {
                return "/mnt/"
                    + char.ToLowerInvariant(path[0])
                    + path.Substring(2).Replace('\\', '/');
            }
            if (!path.StartsWith("/", StringComparison.Ordinal)
                || path.StartsWith("//", StringComparison.Ordinal))
            {
                throw new ArgumentException(
                    "WSL/Bash copying requires an absolute POSIX path "
                    + "or a Windows drive path.");
            }
            return path;
        }

        private static string BashQuote(string value)
        {
            return "'" + value.Replace("'", "'\"'\"'") + "'";
        }

        private static string PowerShellQuote(string value)
        {
            return "'" + value.Replace("'", "''") + "'";
        }
    }
}
