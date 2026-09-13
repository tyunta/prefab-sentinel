// Cohesive BridgeDeploy concern: validated promotion state, rollback, and refresh barrier.
using System; using System.Collections.Generic; using System.Globalization;
using System.IO; using System.Linq; using System.Runtime.Serialization;
using System.Runtime.Serialization.Json; using System.Security.Cryptography;
using System.Text; using System.Text.RegularExpressions;
#if UNITY_EDITOR
using UnityEditor; using UnityEngine;
#endif
#nullable enable
namespace PrefabSentinel {
    [DataContract]
    internal sealed class BridgeDeployManifestEntry : IExtensibleDataObject {
        [DataMember(Name = "path", IsRequired = true, Order = 0)] public string path = ""; [DataMember(Name = "sha256", IsRequired = true, Order = 1)] public string sha256 = "";
        [DataMember(Name = "size", IsRequired = true, Order = 2)] public long size = 0L; public ExtensionDataObject? ExtensionData { get; set; }
    }
    [DataContract]
    internal sealed class BridgeDeployPrivateManifest : IExtensibleDataObject {
        [DataMember(Name = "bridge_version", IsRequired = true, Order = 0)] public string bridge_version = "";
        [DataMember(Name = "files", IsRequired = true, Order = 1)] public BridgeDeployManifestEntry[] files = Array.Empty<BridgeDeployManifestEntry>();
        [DataMember(Name = "manifest_sha256", IsRequired = true, Order = 2)] public string manifest_sha256 = "";
        [DataMember(Name = "run_id", IsRequired = true, Order = 3)] public string run_id = ""; [DataMember(Name = "schema", IsRequired = true, Order = 4)] public string schema = ""; [DataMember(Name = "target", IsRequired = true, Order = 5)] public string target = "";
        public ExtensionDataObject? ExtensionData { get; set; }
    }
    internal sealed class BridgeDeployPromotionRequest {
        private const string Schema = "prefab_sentinel_bridge_source_manifest.v1";
        private static readonly Regex Version = new Regex(@"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$", RegexOptions.CultureInvariant);
        private BridgeDeployPromotionRequest(
            string root, string target, string transaction, string runId,
            string hash, string version, BridgeDeployManifestEntry[] entries) {
            ProjectRoot = root;
            TargetPath = target;
            TransactionPath = transaction;
            StagingPath = Path.Combine(transaction, "staged-target");
            BackupPath = Path.Combine(transaction, "backup-target");
            RunId = runId;
            ManifestSha256 = hash;
            BridgeVersion = version;
            Entries = entries;
        }
        internal string ProjectRoot { get; } internal string TargetPath { get; }
        internal string TransactionPath { get; } internal string StagingPath { get; }
        internal string BackupPath { get; } internal string RunId { get; }
        internal string ManifestSha256 { get; } internal string BridgeVersion { get; }
        internal BridgeDeployManifestEntry[] Entries { get; }
        internal bool SameFileSystem => true; internal string State { get; private set; } = "ready";
        internal static bool TryCreate(
            string projectRoot, string targetPath, string transactionPath,
            string runId, string hash, string version,
            out BridgeDeployPromotionRequest request, out string code) {
            request = null!;
            code = "DEPLOY_STAGING_MISMATCH";
            if (!BridgeDeployPromotionEvidence.LowerHex(runId, 32)
                || !BridgeDeployPromotionEvidence.LowerHex(hash, 64)
                || string.IsNullOrEmpty(version) || !Version.IsMatch(version))
                return false;
            if (!BridgeDeployPromotionEvidence.Relative(
                    targetPath, "Assets", out var relativeTarget)
                || relativeTarget == "Assets"
                || !BridgeDeployPromotionEvidence.Relative(
                    transactionPath, "Library", out var relativeTransaction)
                || relativeTransaction != "Library/PrefabSentinel/deploy-transactions/" + runId) {
                code = "DEPLOY_OUTSIDE_PROJECT";
                return false;
            }
            try {
                string root = Path.GetFullPath(projectRoot);
                string target = BridgeDeployPromotionEvidence.Full(root, relativeTarget);
                string transaction = BridgeDeployPromotionEvidence.Full(root, relativeTransaction);
                string staging = Path.Combine(transaction, "staged-target");
                string backup = Path.Combine(transaction, "backup-target");
                string manifestPath = Path.Combine(transaction, "source-manifest-v1.json");
                if (!Path.IsPathRooted(projectRoot) || !Directory.Exists(root)
                    || !BridgeDeployPromotionEvidence.Contained(root, target)
                    || !BridgeDeployPromotionEvidence.Contained(root, transaction)) {
                    code = "DEPLOY_OUTSIDE_PROJECT";
                    return false;
                }
                if (!Directory.Exists(target) || !Directory.Exists(transaction)
                    || !Directory.Exists(staging) || Directory.Exists(backup)
                    || !File.Exists(manifestPath)
                    || BridgeDeployPromotionEvidence.Reparse(root, target)
                    || BridgeDeployPromotionEvidence.Reparse(root, transaction)
                    || BridgeDeployPromotionEvidence.Reparse(root, staging)
                    || BridgeDeployPromotionEvidence.Reparse(root, manifestPath)) {
                    code = "DEPLOY_STAGING_FAILED";
                    return false;
                }
                if (!BridgeDeployPromotionEvidence.ShareReportedVolume(
                        Path.GetDirectoryName(target)!,
                        transaction)) {
                    code = "DEPLOY_CROSS_FILESYSTEM";
                    return false;
                }
                BridgeDeployPrivateManifest? manifest =
                    BridgeDeployPromotionEvidence.ReadManifest(manifestPath);
                if (manifest == null || manifest.files == null
                    || manifest.files.Any(entry => entry == null)
                    || manifest.schema != Schema || manifest.run_id != runId
                    || manifest.target != relativeTarget || manifest.bridge_version != version
                    || manifest.manifest_sha256 != hash
                    || !BridgeDeployPromotionEvidence.ValidEntries(
                        manifest.files, hash))
                    return false;
                request = new BridgeDeployPromotionRequest(
                    root, target, transaction, runId, hash, version, manifest.files);
                code = "DEPLOY_OK";
                return true;
            }
            catch (SerializationException) {
                return false;
            }
            catch (Exception) {
                code = "DEPLOY_STAGING_FAILED";
                return false;
            }
        }
        internal bool TryMarkOldBackedUp(out string code) => Transition("ready", "old_backed_up", out code);
        internal bool TryMarkNewPromoted(out string code) => Transition("old_backed_up", "new_promoted", out code);
        internal bool TryMarkVerified(out string code) => Transition("new_promoted", "verified", out code);
        internal bool TryMarkComplete(out string code) => Transition("verified", "complete", out code);
        internal bool TryMarkRolledBack(out string code) => Transition("rollback_started", "rolled_back", out code);
        internal bool TryMarkRollbackFailed(out string code) => Transition("rollback_started", "rollback_failed", out code);
        internal bool TryBeginRollback(out string code) {
            if (State != "ready"
                && State != "old_backed_up"
                && State != "new_promoted") {
                code = "DEPLOY_PROMOTION_FAILED";
                return false;
            }
            State = "rollback_started";
            code = "DEPLOY_OK";
            return true;
        }
        internal bool VerifyPromotedTarget(out string code) {
            try {
                var expected = Entries.ToDictionary(entry => entry.path, StringComparer.Ordinal);
                var seen = new HashSet<string>(StringComparer.Ordinal);
                if (!Directory.Exists(TargetPath)
                    || BridgeDeployPromotionEvidence.Reparse(
                        ProjectRoot, TargetPath))
                    throw new IOException();
                foreach (string child in Directory.EnumerateFileSystemEntries(TargetPath)) {
                    if (BridgeDeployPromotionEvidence.Reparse(TargetPath, child)
                        || Directory.Exists(child))
                        throw new IOException();
                    string name = Path.GetFileName(child);
                    if (expected.TryGetValue(name, out BridgeDeployManifestEntry? entry)) {
                        if (new FileInfo(child).Length != entry.size
                            || BridgeDeployPromotionEvidence.FileHash(child)
                                != entry.sha256)
                            throw new IOException();
                        seen.Add(name);
                    }
                    else if (!name.EndsWith(".meta", StringComparison.Ordinal)
                        || !expected.ContainsKey(name.Substring(0, name.Length - 5)))
                        throw new IOException();
                }
                if (seen.Count != expected.Count)
                    throw new IOException();
                code = "DEPLOY_OK";
                return true;
            }
            catch (Exception) {
                code = "DEPLOY_FINAL_MANIFEST_MISMATCH";
                return false;
            }
        }
        private bool Transition(string expected, string next, out string code) {
            if (State != expected) {
                code = "DEPLOY_PROMOTION_FAILED";
                return false;
            }
            State = next;
            code = "DEPLOY_OK";
            return true;
        }
    }
    [Serializable]
    internal sealed class BridgeDeployPromotionResult {
        public bool success, barrier_used, rollback_attempted, rollback_restored, backup_retained, target_complete;
        public string severity = "error", code = "", message = "", promotion_state = "not_attempted", manifest_sha256 = "", bridge_version = "";
        private static BridgeDeployPromotionResult Create(
            bool success, string severity, string code, string message,
            string state, string hash, string version) => new BridgeDeployPromotionResult
        {
            success = success, severity = severity, code = code, message = message,
            promotion_state = state, barrier_used = state != "not_attempted",
            manifest_sha256 = hash, bridge_version = version,
        };
        internal static BridgeDeployPromotionResult Promoted(string hash, string version) {
            var result = Create(true, "info", "DEPLOY_OK",
                "Bridge bundle promotion completed.", "promoted", hash, version);
            result.backup_retained = result.target_complete = true;
            return result;
        }
        internal static BridgeDeployPromotionResult RolledBack(
            string hash, string version, bool attempted) {
            var result = Create(false, "error", "DEPLOY_ROLLED_BACK",
                "Bridge bundle promotion failed and the previous target was restored.",
                "rolled_back", hash, version);
            result.rollback_attempted = attempted;
            result.rollback_restored = result.target_complete = true;
            return result;
        }
        internal static BridgeDeployPromotionResult RollbackFailed(
            string hash, string version, bool backupRetained) {
            var result = Create(false, "critical", "DEPLOY_ROLLBACK_FAILED",
                "Bridge bundle promotion and rollback failed.",
                "rollback_failed", hash, version);
            result.rollback_attempted = true;
            result.backup_retained = backupRetained;
            return result;
        }
        internal static BridgeDeployPromotionResult CompilationRequestFailed(
            BridgeDeployPromotionResult complete) {
            var result = Create(false, "error", "DEPLOY_REFRESH_FAILED",
                "Bridge bundle promotion completed but source refresh/import failed.",
                complete.promotion_state, complete.manifest_sha256, complete.bridge_version);
            result.rollback_attempted = complete.rollback_attempted;
            result.rollback_restored = complete.rollback_restored;
            result.backup_retained = complete.backup_retained;
            result.target_complete = complete.target_complete;
            return result;
        }
        internal static BridgeDeployPromotionResult NotAttempted(string code) => Create(
            false, "error", code, "Bridge bundle promotion request was rejected.",
            "not_attempted", "", "");
        internal static BridgeDeployPromotionResult PreBackupFailed(
            string hash, string version, bool targetComplete) {
            var result = Create(false, "error", "DEPLOY_PROMOTION_FAILED",
                "Bridge bundle promotion failed before the old target was backed up.",
                "not_attempted", hash, version);
            result.barrier_used = true;
            result.target_complete = targetComplete;
            return result;
        }
        internal static BridgeDeployPromotionResult BarrierUnavailable(
            string hash, string version) => Create(
                false, "error", "DEPLOY_BARRIER_UNAVAILABLE",
                "Bridge bundle promotion could not acquire the refresh barrier.",
                "not_attempted", hash, version);
    }
#if UNITY_EDITOR
    public static partial class UnityEditorControlBridge {
        private static EditorControlResponse HandlePromoteBridgeBundle(
            EditorControlRequest request) {
            if (!BridgeDeployPromotionRequest.TryCreate(
                CurrentProjectRoot(), request.deploy_target_path,
                request.deploy_transaction_path, request.deploy_run_id,
                request.deploy_manifest_sha256, request.deploy_bridge_version,
                out BridgeDeployPromotionRequest promotion, out string stateCode))
                return BuildBridgeDeployResponse(
                    BridgeDeployPromotionResult.NotAttempted(stateCode));
            BridgeDeployPromotionResult result =
                BridgeDeployPromotionResult.BarrierUnavailable(
                    promotion.ManifestSha256, promotion.BridgeVersion);
            bool barrierAcquired = false;
            try {
                try {
                    AssetDatabase.DisallowAutoRefresh();
                    barrierAcquired = true;
                }
                catch (Exception barrierException) {
                    Debug.LogError(
                        $"Bridge refresh barrier acquisition failed. {barrierException}");
                    result = BridgeDeployPromotionResult.BarrierUnavailable(
                        promotion.ManifestSha256, promotion.BridgeVersion);
                }
                if (barrierAcquired) {
                    try {
                        Directory.Move(promotion.TargetPath, promotion.BackupPath);
                        RequireDeployTransition(
                            promotion.TryMarkOldBackedUp(out stateCode));
                        Directory.Move(promotion.StagingPath, promotion.TargetPath);
                        RequireDeployTransition(
                            promotion.TryMarkNewPromoted(out stateCode));
                        if (!promotion.VerifyPromotedTarget(out stateCode))
                            throw new InvalidDataException(
                                "Promoted Bridge source did not match.");
                        RequireDeployTransition(
                            promotion.TryMarkVerified(out stateCode));
                        RequireDeployTransition(
                            promotion.TryMarkComplete(out stateCode));
                        result = BridgeDeployPromotionResult.Promoted(
                            promotion.ManifestSha256, promotion.BridgeVersion);
                    }
                    catch (Exception ex) {
                        Debug.LogError(
                            $"Bridge promotion failed and recovery will be evaluated. {ex}");
                        string decision = BridgeDeployPromotionEvidence.DecideRecovery(
                            promotion.State,
                            Directory.Exists(promotion.TargetPath),
                            Directory.Exists(promotion.BackupPath),
                            Directory.Exists(promotion.StagingPath));
                        if (decision == "pre_backup_intact") {
                            result = BridgeDeployPromotionResult.PreBackupFailed(
                                promotion.ManifestSha256,
                                promotion.BridgeVersion,
                                targetComplete: true);
                        }
                        else {
                            bool rollbackStarted = promotion.TryBeginRollback(out stateCode);
                            if (decision == "ambiguous" || !rollbackStarted) {
                                if (rollbackStarted)
                                    promotion.TryMarkRollbackFailed(out stateCode);
                                result = BridgeDeployPromotionResult.RollbackFailed(
                                    promotion.ManifestSha256,
                                    promotion.BridgeVersion,
                                    Directory.Exists(promotion.BackupPath));
                            }
                            else if (decision == "restore_backup"
                            || decision == "move_promoted_then_restore") {
                            try {
                                if (decision == "move_promoted_then_restore")
                                    Directory.Move(
                                        promotion.TargetPath,
                                        promotion.StagingPath);
                                Directory.Move(
                                    promotion.BackupPath,
                                    promotion.TargetPath);
                                if (!BridgeDeployPromotionEvidence.IsRestoredLayout(
                                        Directory.Exists(promotion.TargetPath),
                                        Directory.Exists(promotion.BackupPath),
                                        Directory.Exists(promotion.StagingPath)))
                                    throw new InvalidDataException(
                                        "Bridge rollback layout is incomplete.");
                                RequireDeployTransition(
                                    promotion.TryMarkRolledBack(out stateCode));
                                result = BridgeDeployPromotionResult.RolledBack(
                                    promotion.ManifestSha256,
                                    promotion.BridgeVersion,
                                    true);
                            }
                            catch (Exception rollbackException) {
                                Debug.LogError(
                                    $"Bridge rollback failed and is retained. {rollbackException}");
                                promotion.TryMarkRollbackFailed(out stateCode);
                                result = BridgeDeployPromotionResult.RollbackFailed(
                                    promotion.ManifestSha256,
                                    promotion.BridgeVersion,
                                    Directory.Exists(promotion.BackupPath));
                            }
                        }
                        }
                    }
                }
                if (result.target_complete
                    && (result.promotion_state == "promoted"
                        || result.promotion_state == "rolled_back")) {
                    try {
                        Debug.Log("[PrefabSentinel.EditorBridge] Bridge deploy source refresh begin.");
                        AssetDatabase.Refresh(
                            ImportAssetOptions.ForceSynchronousImport);
                        Debug.Log("[PrefabSentinel.EditorBridge] Bridge deploy source refresh end.");
                    }
                    catch (Exception scheduleException) {
                        Debug.LogError(
                            $"Bridge asset refresh failed. {scheduleException}");
                        result = BridgeDeployPromotionResult.CompilationRequestFailed(result);
                    }
                }
            }
            finally {
                if (barrierAcquired)
                    AssetDatabase.AllowAutoRefresh();
            }
            return BuildBridgeDeployResponse(result);
        }
        private static void RequireDeployTransition(bool transitioned) {
            if (!transitioned)
                throw new InvalidOperationException("Bridge deploy state transition failed.");
        }
        private static EditorControlResponse BuildBridgeDeployResponse(
            BridgeDeployPromotionResult result) {
            var data = new EditorControlData
            {
                executed = result.promotion_state != "not_attempted",
                promotion_state = result.promotion_state,
                barrier_used = result.barrier_used,
                rollback_attempted = result.rollback_attempted,
                rollback_restored = result.rollback_restored,
                backup_retained = result.backup_retained,
                target_complete = result.target_complete,
                manifest_sha256 = result.manifest_sha256,
                bridge_version = result.bridge_version,
            };
            EditorControlResponse response = result.success
                ? BuildSuccess(result.code, result.message, data)
                : BuildError(result.code, result.message, data);
            response.severity = result.severity;
            return response;
        }
    }
#endif
}
