#nullable enable

using System;
using System.Collections.Generic;

namespace PrefabSentinel
{
    internal static class InspectorSceneLifecycle
    {
        internal enum Ownership
        {
            None,
            Borrowed,
            Owned
        }

        internal enum DecisionState
        {
            Ready,
            Dirty,
            Ambiguous
        }

        [Serializable]
        internal sealed class Entry
        {
            public int order;
            public int handle;
            public string path = string.Empty;
            public bool dirty;
        }

        [Serializable]
        internal sealed class Snapshot
        {
            public Entry[] scenes = Array.Empty<Entry>();
            public int active_handle;
            public string active_path = string.Empty;
        }

        internal sealed class Decision
        {
            internal DecisionState State;
            internal Ownership Ownership;
            internal int TargetHandle;
            internal int[] MatchedHandles = Array.Empty<int>();
        }

        internal sealed class Completion
        {
            internal string[] FailedPostconditions = Array.Empty<string>();
            internal bool Success => FailedPostconditions.Length == 0;
        }

        [Serializable]
        internal sealed class Evidence
        {
            public string asset_path = string.Empty;
            public string ownership = string.Empty;
            public int[] matched_handles = Array.Empty<int>();
            public bool cleanup_attempted = false;
            public string close_result = "not_attempted";
            public bool active_restore_attempted = false;
            public string active_restore_result = "not_attempted";
            public Snapshot before = new Snapshot();
            public Snapshot after = new Snapshot();
            public string[] failed_postconditions = Array.Empty<string>();
        }

        internal static Decision Decide(Snapshot before, string targetPath)
        {
            var matchedHandles = new List<int>();
            Entry? matchedEntry = null;

            foreach (Entry entry in before.scenes)
            {
                if (!string.Equals(entry.path, targetPath, StringComparison.Ordinal))
                {
                    continue;
                }

                matchedHandles.Add(entry.handle);
                matchedEntry = entry;
            }

            if (matchedHandles.Count == 0)
            {
                return new Decision
                {
                    State = DecisionState.Ready,
                    Ownership = Ownership.Owned,
                    TargetHandle = 0
                };
            }

            if (matchedHandles.Count > 1)
            {
                return new Decision
                {
                    State = DecisionState.Ambiguous,
                    Ownership = Ownership.None,
                    TargetHandle = 0,
                    MatchedHandles = matchedHandles.ToArray()
                };
            }

            return new Decision
            {
                State = matchedEntry!.dirty
                    ? DecisionState.Dirty
                    : DecisionState.Ready,
                Ownership = matchedEntry.dirty
                    ? Ownership.None
                    : Ownership.Borrowed,
                TargetHandle = matchedEntry.handle,
                MatchedHandles = matchedHandles.ToArray()
            };
        }

        internal static Completion EvaluateCompletion(
            Snapshot before,
            Snapshot after,
            Ownership ownership,
            int targetHandle,
            bool closeAttempted,
            bool closeSucceeded,
            bool activeRestoreRequired,
            bool activeRestored)
        {
            var failures = new List<string>();

            if (!PreservesStartupOrder(before, after))
            {
                failures.Add("startup_scene_order");
            }

            if (before.active_handle != after.active_handle
                || !string.Equals(
                    before.active_path,
                    after.active_path,
                    StringComparison.Ordinal))
            {
                failures.Add("active_scene");
            }

            if (!PreservesStartupDirtyState(before, after))
            {
                failures.Add("startup_scene_dirty_state");
            }

            bool targetPresent = ContainsHandle(after, targetHandle);
            if (ownership == Ownership.Borrowed && !targetPresent)
            {
                failures.Add("borrowed_scene_missing");
            }

            if (ownership == Ownership.Owned)
            {
                if (!closeAttempted)
                {
                    failures.Add("owned_close_not_attempted");
                }
                else if (!closeSucceeded)
                {
                    failures.Add("owned_close_failed");
                }

                if (targetPresent)
                {
                    failures.Add("owned_scene_present");
                }
            }

            if (activeRestoreRequired && !activeRestored)
            {
                failures.Add("active_scene_restore_failed");
            }

            return new Completion
            {
                FailedPostconditions = failures.ToArray()
            };
        }

        private static bool PreservesStartupOrder(
            Snapshot before,
            Snapshot after)
        {
            if (before.scenes.Length != after.scenes.Length)
            {
                return false;
            }

            for (int index = 0; index < before.scenes.Length; index++)
            {
                Entry startupEntry = before.scenes[index];
                Entry afterEntry = after.scenes[index];
                if (startupEntry.order != afterEntry.order
                    || startupEntry.handle != afterEntry.handle
                    || !string.Equals(
                        startupEntry.path,
                        afterEntry.path,
                        StringComparison.Ordinal))
                {
                    return false;
                }
            }

            return true;
        }

        private static bool PreservesStartupDirtyState(
            Snapshot before,
            Snapshot after)
        {
            foreach (Entry startupEntry in before.scenes)
            {
                Entry? afterEntry = FindByHandle(after, startupEntry.handle);
                if (afterEntry == null
                    || afterEntry.dirty != startupEntry.dirty)
                {
                    return false;
                }
            }

            return true;
        }

        private static bool ContainsHandle(Snapshot snapshot, int handle)
        {
            foreach (Entry entry in snapshot.scenes)
            {
                if (entry.handle == handle)
                {
                    return true;
                }
            }

            return false;
        }

        private static Entry? FindByHandle(Snapshot snapshot, int handle)
        {
            foreach (Entry entry in snapshot.scenes)
            {
                if (entry.handle == handle)
                {
                    return entry;
                }
            }

            return null;
        }
    }
}
