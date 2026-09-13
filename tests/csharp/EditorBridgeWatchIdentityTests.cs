using System.Diagnostics;
using System.Text;

using Xunit;
namespace PrefabSentinel.Tests
{
    public sealed class EditorBridgeWatchIdentityTests : IDisposable
    {
        private readonly string _rootDirectory;
        private readonly string _watchDirectory;
        private readonly string _projectRoot;
        private readonly string _statusPath;
        private readonly List<Exception> _failures = new();

        public EditorBridgeWatchIdentityTests()
        {
            _rootDirectory = Path.Combine(
                Path.GetTempPath(),
                "PrefabSentinel.Tests",
                Guid.NewGuid().ToString("N"));
            _watchDirectory = Path.Combine(_rootDirectory, "watch");
            _projectRoot = Path.Combine(_rootDirectory, "project");
            _statusPath = Path.Combine(
                _projectRoot,
                "Library",
                "PrefabSentinel",
                "bridge-status-v1.json");
            Directory.CreateDirectory(_watchDirectory);
            Directory.CreateDirectory(_projectRoot);
        }

        public void Dispose()
        {
            if (Directory.Exists(_rootDirectory))
            {
                Directory.Delete(_rootDirectory, recursive: true);
            }
        }


        private static void CreateFifo(string path)
        {
            var startInfo = new ProcessStartInfo("mkfifo")
            {
                UseShellExecute = false,
            };
            startInfo.ArgumentList.Add(path);

            using Process process =
                Process.Start(startInfo)
                ?? throw new InvalidOperationException("Unable to start mkfifo.");
            process.WaitForExit();
            Assert.Equal(0, process.ExitCode);
        }

        private static string ValidStatusPayload(string instanceId)
        {
            return "{\"schema_version\":1,"
                + "\"watch_identity\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\","
                + "\"bridge_session_id\":\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\","
                + "\"bridge_instance_id\":\"" + instanceId + "\","
                + "\"updated_at_unix_ms\":1234}";
        }

        [Fact]
        public void Marker_MissingEntryCreatesValidatedIdentity()
        {
            string expected = new string('a', 32);

            bool ok = EditorBridgeWatchIdentity.TryEnsureMarker(
                _watchDirectory,
                () => expected,
                _failures.Add,
                out string actual);

            Assert.True(ok);
            Assert.Equal(expected, actual);
            Assert.Equal(
                expected,
                File.ReadAllText(
                    Path.Combine(
                        _watchDirectory,
                        EditorBridgeWatchIdentity.MarkerFileName)));
            Assert.Empty(_failures);
        }

        [Fact]
        public void Marker_CreateNewCollision_PreservesWinner()
        {
            string expected = new string('a', 32);
            File.WriteAllText(
                Path.Combine(
                    _watchDirectory,
                    EditorBridgeWatchIdentity.MarkerFileName),
                expected);

            bool ok = EditorBridgeWatchIdentity.TryEnsureMarker(
                _watchDirectory,
                () => new string('b', 32),
                _failures.Add,
                out string actual);

            Assert.True(ok);
            Assert.Equal(expected, actual);
            Assert.Equal(
                expected,
                File.ReadAllText(
                    Path.Combine(
                        _watchDirectory,
                        EditorBridgeWatchIdentity.MarkerFileName)));
            Assert.Empty(_failures);
        }

        [Fact]
        public void Marker_CreateRace_PreservesWinner()
        {
            string expected = new string('a', 32);
            string markerPath = Path.Combine(
                _watchDirectory,
                EditorBridgeWatchIdentity.MarkerFileName);

            bool ok = EditorBridgeWatchIdentity.TryEnsureMarker(
                _watchDirectory,
                () =>
                {
                    File.WriteAllText(markerPath, expected);
                    return new string('b', 32);
                },
                _failures.Add,
                out string actual);

            Assert.True(ok);
            Assert.Equal(expected, actual);
            Assert.Equal(expected, File.ReadAllText(markerPath));
            Assert.Empty(_failures);
        }

        [Fact]
        public void Marker_InvalidExistingContent_IsNotOverwritten()
        {
            const string invalid = "NOT-A-LOWERCASE-32-HEX-ID";
            string markerPath = Path.Combine(
                _watchDirectory,
                EditorBridgeWatchIdentity.MarkerFileName);
            File.WriteAllText(markerPath, invalid);

            bool ok = EditorBridgeWatchIdentity.TryEnsureMarker(
                _watchDirectory,
                () => new string('b', 32),
                _failures.Add,
                out string actual);

            Assert.False(ok);
            Assert.Equal(string.Empty, actual);
            Assert.Equal(invalid, File.ReadAllText(markerPath));
            Assert.Empty(_failures);
        }

        [Fact]
        public void Marker_DirectoryEntry_IsNotOverwritten()
        {
            string markerPath = Path.Combine(
                _watchDirectory,
                EditorBridgeWatchIdentity.MarkerFileName);
            Directory.CreateDirectory(markerPath);

            bool ok = EditorBridgeWatchIdentity.TryEnsureMarker(
                _watchDirectory,
                () => new string('b', 32),
                _failures.Add,
                out string actual);

            Assert.False(ok);
            Assert.Equal(string.Empty, actual);
            Assert.True(Directory.Exists(markerPath));
            Assert.Empty(_failures);
        }


        [Fact]
        public async Task Marker_FifoEntryIsRejectedWithoutBlockingOrOverwrite()
        {
            if (OperatingSystem.IsWindows())
            {
                return;
            }

            string markerPath = Path.Combine(
                _watchDirectory,
                EditorBridgeWatchIdentity.MarkerFileName);
            CreateFifo(markerPath);

            Task<(bool Ok, string Identity)> operation = Task.Run(() =>
            {
                bool ok = EditorBridgeWatchIdentity.TryEnsureMarker(
                    _watchDirectory,
                    () => new string('b', 32),
                    _failures.Add,
                    out string identity);
                return (ok, identity);
            });
            Task completed = await Task.WhenAny(
                operation,
                Task.Delay(TimeSpan.FromSeconds(1)));

            Assert.Same(operation, completed);
            (bool ok, string identity) =
                await operation.WaitAsync(TimeSpan.FromSeconds(5));
            Assert.False(ok);
            Assert.Equal(string.Empty, identity);
            Assert.Equal(0, new FileInfo(markerPath).Length);
            Assert.Empty(_failures);
        }

        [Fact]
        public void Marker_ReparseEntryIsRejectedWithoutFollowingOrOverwrite()
        {
            if (OperatingSystem.IsWindows())
            {
                return;
            }

            string targetPath = Path.Combine(_rootDirectory, "marker-target");
            string markerPath = Path.Combine(
                _watchDirectory,
                EditorBridgeWatchIdentity.MarkerFileName);
            File.WriteAllText(targetPath, new string('a', 32));
            File.CreateSymbolicLink(markerPath, targetPath);

            Assert.False(EditorBridgeWatchIdentity.TryEnsureMarker(
                _watchDirectory,
                () => new string('b', 32),
                _failures.Add,
                out string identity));

            Assert.Equal(string.Empty, identity);
            Assert.Equal(new string('a', 32), File.ReadAllText(targetPath));
            Assert.True(File.Exists(markerPath));
            Assert.Empty(_failures);
        }

        [Fact]
        public void Status_PublishWritesExactJsonAndRemovesTemporarySibling()
        {
            bool ok = EditorBridgeWatchIdentity.TryPublishStatus(
                _projectRoot,
                new string('a', 32),
                new string('b', 32),
                new string('c', 32),
                1234,
                _failures.Add);

            Assert.True(ok);
            Assert.Equal(
                "{\"schema_version\":1,"
                    + "\"watch_identity\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\","
                    + "\"bridge_session_id\":\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\","
                    + "\"bridge_instance_id\":\"cccccccccccccccccccccccccccccccc\","
                    + "\"updated_at_unix_ms\":1234}",
                File.ReadAllText(_statusPath, Encoding.UTF8));
            Assert.False(File.Exists(_statusPath + ".tmp"));
            Assert.Empty(_failures);
        }


        [Fact]
        public void Status_PublishRejectsFifoDestinationWithoutReplacement()
        {
            if (OperatingSystem.IsWindows())
            {
                return;
            }

            Directory.CreateDirectory(Path.GetDirectoryName(_statusPath)!);
            CreateFifo(_statusPath);

            Assert.False(EditorBridgeWatchIdentity.TryPublishStatus(
                _projectRoot,
                new string('a', 32),
                new string('b', 32),
                new string('c', 32),
                1234,
                _failures.Add));

            Assert.Equal(0, new FileInfo(_statusPath).Length);
            Assert.False(File.Exists(_statusPath + ".tmp"));
            Assert.Single(_failures);
        }

        [Fact]
        public void Status_PublishRejectsReparseDestinationWithoutReplacement()
        {
            if (OperatingSystem.IsWindows())
            {
                return;
            }

            string targetPath = Path.Combine(_rootDirectory, "status-target");
            File.WriteAllText(targetPath, "preserve target");
            Directory.CreateDirectory(Path.GetDirectoryName(_statusPath)!);
            File.CreateSymbolicLink(_statusPath, targetPath);

            Assert.False(EditorBridgeWatchIdentity.TryPublishStatus(
                _projectRoot,
                new string('a', 32),
                new string('b', 32),
                new string('c', 32),
                1234,
                _failures.Add));

            Assert.Equal("preserve target", File.ReadAllText(targetPath));
            Assert.True(File.Exists(_statusPath));
            Assert.False(File.Exists(_statusPath + ".tmp"));
            Assert.Single(_failures);
        }

        [Fact]
        public void Status_InvalidIdentifiersDoNotWrite()
        {
            (string Watch, string Session, string Instance)[] cases =
            {
                ("Aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", new string('b', 32), new string('c', 32)),
                (new string('a', 32), new string('b', 31), new string('c', 32)),
                (new string('a', 32), new string('b', 32), new string('g', 32)),
            };

            foreach ((string watch, string session, string instance) in cases)
            {
                Assert.False(EditorBridgeWatchIdentity.TryPublishStatus(
                    _projectRoot,
                    watch,
                    session,
                    instance,
                    1234,
                    _failures.Add));
                Assert.False(File.Exists(_statusPath));
                Assert.False(File.Exists(_statusPath + ".tmp"));
            }

            Assert.Empty(_failures);
        }

        [Fact]
        public void Status_CleanupPreservesForeignInstance()
        {
            Assert.True(EditorBridgeWatchIdentity.TryPublishStatus(
                _projectRoot,
                new string('a', 32),
                new string('b', 32),
                new string('c', 32),
                1234,
                _failures.Add));

            Assert.False(EditorBridgeWatchIdentity.TryDeleteOwnedStatus(
                _projectRoot,
                new string('d', 32),
                _failures.Add));

            Assert.True(File.Exists(_statusPath));
            Assert.Empty(_failures);
        }

        [Fact]
        public void Status_CleanupDeletesOwnedInstance()
        {
            string instanceId = new string('c', 32);
            Assert.True(EditorBridgeWatchIdentity.TryPublishStatus(
                _projectRoot,
                new string('a', 32),
                new string('b', 32),
                instanceId,
                1234,
                _failures.Add));

            Assert.True(EditorBridgeWatchIdentity.TryDeleteOwnedStatus(
                _projectRoot,
                instanceId,
                _failures.Add));

            Assert.False(File.Exists(_statusPath));
            Assert.Empty(_failures);
        }


        [Fact]
        public async Task Status_CleanupSerializesPublicationAfterOwnershipValidation()
        {
            string oldInstanceId = new string('c', 32);
            string newInstanceId = new string('d', 32);
            Assert.True(EditorBridgeWatchIdentity.TryPublishStatus(
                _projectRoot,
                new string('a', 32),
                new string('b', 32),
                oldInstanceId,
                1234,
                _failures.Add));

            using var ownershipValidated = new ManualResetEventSlim();
            using var releaseCleanup = new ManualResetEventSlim();
            using var publicationStarted = new ManualResetEventSlim();
            Task<bool> cleanup = Task.Run(() =>
                EditorBridgeWatchIdentity.TryDeleteOwnedStatus(
                    _projectRoot,
                    oldInstanceId,
                    () =>
                    {
                        ownershipValidated.Set();
                        if (!releaseCleanup.Wait(TimeSpan.FromSeconds(5)))
                        {
                            throw new TimeoutException(
                                "Cleanup test gate was not released.");
                        }
                    },
                    _failures.Add));

            Assert.True(ownershipValidated.Wait(TimeSpan.FromSeconds(5)));
            Task<bool> publication = Task.Run(() =>
            {
                publicationStarted.Set();
                return EditorBridgeWatchIdentity.TryPublishStatus(
                    _projectRoot,
                    new string('a', 32),
                    new string('b', 32),
                    newInstanceId,
                    1235,
                    _failures.Add);
            });
            Assert.True(publicationStarted.Wait(TimeSpan.FromSeconds(5)));
            Task earlyCompletion = await Task.WhenAny(
                publication,
                Task.Delay(TimeSpan.FromMilliseconds(500)));
            bool publicationWasSerialized =
                !ReferenceEquals(earlyCompletion, publication);

            releaseCleanup.Set();
            Assert.True(await cleanup.WaitAsync(TimeSpan.FromSeconds(5)));
            Assert.True(await publication.WaitAsync(TimeSpan.FromSeconds(5)));
            Assert.True(publicationWasSerialized);
            Assert.True(File.Exists(_statusPath));
            Assert.True(EditorBridgeWatchIdentity.TryDeleteOwnedStatus(
                _projectRoot,
                newInstanceId,
                _failures.Add));
            Assert.Empty(_failures);
        }


        [Fact]
        public async Task Status_CleanupRejectsFifoWithoutBlockingOrDeletion()
        {
            if (OperatingSystem.IsWindows())
            {
                return;
            }

            Directory.CreateDirectory(Path.GetDirectoryName(_statusPath)!);
            CreateFifo(_statusPath);

            Task<bool> operation = Task.Run(() =>
                EditorBridgeWatchIdentity.TryDeleteOwnedStatus(
                    _projectRoot,
                    new string('c', 32),
                    _failures.Add));
            Task completed = await Task.WhenAny(
                operation,
                Task.Delay(TimeSpan.FromSeconds(1)));
            bool completedWithoutBlocking = ReferenceEquals(completed, operation);

            if (!completedWithoutBlocking)
            {
                using (var writer = new FileStream(
                    _statusPath,
                    FileMode.Open,
                    FileAccess.Write,
                    FileShare.ReadWrite))
                {
                    writer.WriteByte((byte)'x');
                }
            }

            Assert.False(await operation.WaitAsync(TimeSpan.FromSeconds(5)));
            Assert.True(
                completedWithoutBlocking,
                "FIFO reached the blocking FileStream open.");
            Assert.Equal(0, new FileInfo(_statusPath).Length);
            Assert.Empty(_failures);
        }

        [Fact]
        public void Status_CleanupRejectsReparseWithoutFollowingOrDeletion()
        {
            if (OperatingSystem.IsWindows())
            {
                return;
            }

            string instanceId = new string('c', 32);
            string targetPath = Path.Combine(_rootDirectory, "cleanup-target");
            File.WriteAllText(targetPath, ValidStatusPayload(instanceId));
            Directory.CreateDirectory(Path.GetDirectoryName(_statusPath)!);
            File.CreateSymbolicLink(_statusPath, targetPath);

            Assert.False(EditorBridgeWatchIdentity.TryDeleteOwnedStatus(
                _projectRoot,
                instanceId,
                _failures.Add));

            Assert.Equal(ValidStatusPayload(instanceId), File.ReadAllText(targetPath));
            Assert.True(File.Exists(_statusPath));
            Assert.Empty(_failures);
        }

        [Fact]
        public void Status_CleanupRejectsTrailingNewline()
        {
            string instanceId = new string('c', 32);
            Assert.True(EditorBridgeWatchIdentity.TryPublishStatus(
                _projectRoot,
                new string('a', 32),
                new string('b', 32),
                instanceId,
                1234,
                _failures.Add));
            File.AppendAllText(_statusPath, "\n", Encoding.UTF8);

            Assert.False(EditorBridgeWatchIdentity.TryDeleteOwnedStatus(
                _projectRoot,
                instanceId,
                _failures.Add));

            Assert.True(File.Exists(_statusPath));
            Assert.Empty(_failures);
        }

        [Fact]
        public void Status_CleanupRejectsPayloadOverByteCeiling()
        {
            Directory.CreateDirectory(Path.GetDirectoryName(_statusPath)!);
            File.WriteAllBytes(
                _statusPath,
                Enumerable.Repeat((byte)'x', EditorBridgeWatchIdentity.StatusMaxBytes + 1)
                    .ToArray());

            Assert.False(EditorBridgeWatchIdentity.TryDeleteOwnedStatus(
                _projectRoot,
                new string('c', 32),
                _failures.Add));

            Assert.Equal(
                EditorBridgeWatchIdentity.StatusMaxBytes + 1,
                new FileInfo(_statusPath).Length);
            Assert.Empty(_failures);
        }



        [Fact]
        public void Status_PromotionFailurePreservesTargetAndReportsCompleteException()
        {
            string oldInstanceId = new string('c', 32);
            Assert.True(EditorBridgeWatchIdentity.TryPublishStatus(
                _projectRoot,
                new string('a', 32),
                new string('b', 32),
                oldInstanceId,
                1234,
                _failures.Add));
            string original = File.ReadAllText(_statusPath, Encoding.UTF8);
            _failures.Clear();
            Exception expected = new IOException(
                "PROMOTION_SECRET at " + _statusPath);

            Assert.False(EditorBridgeWatchIdentity.TryPublishStatus(
                _projectRoot,
                new string('a', 32),
                new string('b', 32),
                new string('d', 32),
                1235,
                (temporaryPath, statusPath) =>
                {
                    Assert.Equal(_statusPath + ".tmp", temporaryPath);
                    Assert.Equal(_statusPath, statusPath);
                    Assert.True(File.Exists(temporaryPath));
                    throw expected;
                },
                _failures.Add));

            Assert.Equal(original, File.ReadAllText(_statusPath, Encoding.UTF8));
            Assert.False(File.Exists(_statusPath + ".tmp"));
            Assert.Same(expected, Assert.Single(_failures));
        }

        [Fact]
        public void Status_PublicationFailureReportsCompleteExceptionAndLeavesNoTemporarySibling()
        {
            string libraryPath = Path.Combine(_projectRoot, "Library");
            File.WriteAllText(libraryPath, "blocking file");

            Assert.False(EditorBridgeWatchIdentity.TryPublishStatus(
                _projectRoot,
                new string('a', 32),
                new string('b', 32),
                new string('c', 32),
                1234,
                _failures.Add));

            Exception failure = Assert.Single(_failures);
            Assert.Contains(libraryPath, failure.ToString());
            Assert.False(File.Exists(_statusPath));
            Assert.False(File.Exists(_statusPath + ".tmp"));
        }
    }
}
