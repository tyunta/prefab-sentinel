using System;
using System.Collections.Generic;
using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

public class InspectorSceneLifecycleTests
{
    public static IEnumerable<object[]> PreflightCases()
    {
        yield return new object[]
        {
            Snapshot(
                Scene(1, "Assets/Other.unity", 0, dirty: false),
                activeHandle: 1,
                activePath: "Assets/Other.unity"),
            "Assets/Target.unity",
            Decision(
                InspectorSceneLifecycle.DecisionState.Ready,
                InspectorSceneLifecycle.Ownership.Owned,
                targetHandle: 0)
        };

        yield return new object[]
        {
            Snapshot(
                Scene(7, "Assets/Target.unity", 0, dirty: false),
                activeHandle: 7,
                activePath: "Assets/Target.unity"),
            "Assets/Target.unity",
            Decision(
                InspectorSceneLifecycle.DecisionState.Ready,
                InspectorSceneLifecycle.Ownership.Borrowed,
                targetHandle: 7,
                matchedHandles: new[] { 7 })
        };

        yield return new object[]
        {
            Snapshot(
                Scene(8, "Assets/Target.unity", 0, dirty: true),
                activeHandle: 8,
                activePath: "Assets/Target.unity"),
            "Assets/Target.unity",
            Decision(
                InspectorSceneLifecycle.DecisionState.Dirty,
                InspectorSceneLifecycle.Ownership.None,
                targetHandle: 8,
                matchedHandles: new[] { 8 })
        };

        yield return new object[]
        {
            Snapshot(
                Scene(10, "Assets/Target.unity", 0, dirty: false),
                Scene(11, "Assets/Target.unity", 1, dirty: false),
                activeHandle: 10,
                activePath: "Assets/Target.unity"),
            "Assets/Target.unity",
            Decision(
                InspectorSceneLifecycle.DecisionState.Ambiguous,
                InspectorSceneLifecycle.Ownership.None,
                targetHandle: 0,
                matchedHandles: new[] { 10, 11 })
        };

        yield return new object[]
        {
            Snapshot(
                Scene(12, "Assets/Other/Target.unity", 0, dirty: false),
                activeHandle: 12,
                activePath: "Assets/Other/Target.unity"),
            "Assets/Target.unity",
            Decision(
                InspectorSceneLifecycle.DecisionState.Ready,
                InspectorSceneLifecycle.Ownership.Owned,
                targetHandle: 0)
        };
    }

    [Theory]
    [MemberData(nameof(PreflightCases))]
    public void Decide_ClassifiesExactLoadedPathMatches(
        object before,
        string targetPath,
        object expected)
    {
        InspectorSceneLifecycle.Decision actual =
            InspectorSceneLifecycle.Decide(
                (InspectorSceneLifecycle.Snapshot)before,
                targetPath);
        InspectorSceneLifecycle.Decision expectedDecision =
            (InspectorSceneLifecycle.Decision)expected;

        Assert.Equal(expectedDecision.State, actual.State);
        Assert.Equal(expectedDecision.Ownership, actual.Ownership);
        Assert.Equal(expectedDecision.TargetHandle, actual.TargetHandle);
        Assert.Equal(expectedDecision.MatchedHandles, actual.MatchedHandles);
    }

    public static IEnumerable<object[]> CompletionCases()
    {
        InspectorSceneLifecycle.Snapshot before = StartupSnapshot();

        yield return new object[]
        {
            "borrowed unchanged",
            before,
            before,
            InspectorSceneLifecycle.Ownership.Borrowed,
            2,
            false,
            false,
            false,
            false,
            Array.Empty<string>()
        };

        InspectorSceneLifecycle.Snapshot appendedScene = StartupSnapshot();
        appendedScene.scenes = new[]
        {
            Scene(1, "Assets/A.unity", 0, dirty: false),
            Scene(2, "Assets/B.unity", 1, dirty: false),
            Scene(3, "Assets/Appended.unity", 2, dirty: false)
        };
        yield return FailureCase(
            "appended scene",
            appendedScene,
            InspectorSceneLifecycle.Ownership.Borrowed,
            2,
            false,
            false,
            false,
            false,
            new[] { "startup_scene_order" });

        yield return new object[]
        {
            "owned restored success",
            before,
            before,
            InspectorSceneLifecycle.Ownership.Owned,
            3,
            true,
            true,
            true,
            true,
            Array.Empty<string>()
        };

        InspectorSceneLifecycle.Snapshot startupOrderChanged = StartupSnapshot();
        startupOrderChanged.scenes = new[]
        {
            Scene(2, "Assets/B.unity", 0, dirty: false),
            Scene(1, "Assets/A.unity", 1, dirty: false)
        };
        yield return FailureCase(
            "startup scene order",
            startupOrderChanged,
            InspectorSceneLifecycle.Ownership.Borrowed,
            2,
            false,
            false,
            false,
            false,
            new[] { "startup_scene_order" });

        InspectorSceneLifecycle.Snapshot activeChanged = StartupSnapshot();
        activeChanged.active_handle = 2;
        activeChanged.active_path = "Assets/B.unity";
        yield return FailureCase(
            "active scene",
            activeChanged,
            InspectorSceneLifecycle.Ownership.Borrowed,
            2,
            false,
            false,
            false,
            false,
            new[] { "active_scene" });

        InspectorSceneLifecycle.Snapshot dirtyChanged = StartupSnapshot();
        dirtyChanged.scenes[0].dirty = true;
        yield return FailureCase(
            "startup scene dirty state",
            dirtyChanged,
            InspectorSceneLifecycle.Ownership.Borrowed,
            2,
            false,
            false,
            false,
            false,
            new[] { "startup_scene_dirty_state" });

        InspectorSceneLifecycle.Snapshot borrowedMissing = StartupSnapshot();
        borrowedMissing.scenes = new[]
        {
            Scene(1, "Assets/A.unity", 0, dirty: false)
        };
        yield return FailureCase(
            "borrowed scene missing",
            borrowedMissing,
            InspectorSceneLifecycle.Ownership.Borrowed,
            2,
            false,
            false,
            false,
            false,
            new[]
            {
                "startup_scene_order",
                "startup_scene_dirty_state",
                "borrowed_scene_missing"
            });

        yield return FailureCase(
            "owned close not attempted",
            before,
            InspectorSceneLifecycle.Ownership.Owned,
            3,
            false,
            false,
            false,
            false,
            new[] { "owned_close_not_attempted" });

        InspectorSceneLifecycle.Snapshot ownedScenePresent = StartupSnapshot();
        ownedScenePresent.scenes = new[]
        {
            Scene(1, "Assets/A.unity", 0, dirty: false),
            Scene(2, "Assets/B.unity", 1, dirty: false),
            Scene(3, "Assets/Owned.unity", 2, dirty: false)
        };
        yield return FailureCase(
            "owned close failed",
            ownedScenePresent,
            InspectorSceneLifecycle.Ownership.Owned,
            3,
            true,
            false,
            false,
            false,
            new[]
            {
                "startup_scene_order",
                "owned_close_failed",
                "owned_scene_present"
            });

        yield return FailureCase(
            "owned scene present",
            ownedScenePresent,
            InspectorSceneLifecycle.Ownership.Owned,
            3,
            true,
            true,
            false,
            false,
            new[] { "startup_scene_order", "owned_scene_present" });

        yield return FailureCase(
            "active scene restore failed",
            before,
            InspectorSceneLifecycle.Ownership.Owned,
            3,
            true,
            true,
            true,
            false,
            new[] { "active_scene_restore_failed" });
    }

    [Theory]
    [MemberData(nameof(CompletionCases))]
    public void EvaluateCompletion_ReportsOrderedPostconditionFailures(
        string caseName,
        object before,
        object after,
        object ownership,
        int targetHandle,
        bool closeAttempted,
        bool closeSucceeded,
        bool activeRestoreRequired,
        bool activeRestored,
        string[] expectedFailures)
    {
        InspectorSceneLifecycle.Completion completion =
            InspectorSceneLifecycle.EvaluateCompletion(
                (InspectorSceneLifecycle.Snapshot)before,
                (InspectorSceneLifecycle.Snapshot)after,
                (InspectorSceneLifecycle.Ownership)ownership,
                targetHandle,
                closeAttempted,
                closeSucceeded,
                activeRestoreRequired,
                activeRestored);

        Assert.Equal(expectedFailures, completion.FailedPostconditions);
        Assert.True(completion.Success == (expectedFailures.Length == 0), caseName);
    }

    private static object[] FailureCase(
        string name,
        InspectorSceneLifecycle.Snapshot after,
        InspectorSceneLifecycle.Ownership ownership,
        int targetHandle,
        bool closeAttempted,
        bool closeSucceeded,
        bool activeRestoreRequired,
        bool activeRestored,
        string[] expectedFailures)
    {
        return new object[]
        {
            name,
            StartupSnapshot(),
            after,
            ownership,
            targetHandle,
            closeAttempted,
            closeSucceeded,
            activeRestoreRequired,
            activeRestored,
            expectedFailures
        };
    }

    private static InspectorSceneLifecycle.Snapshot StartupSnapshot()
    {
        return Snapshot(
            Scene(1, "Assets/A.unity", 0, dirty: false),
            Scene(2, "Assets/B.unity", 1, dirty: false),
            activeHandle: 1,
            activePath: "Assets/A.unity");
    }

    private static InspectorSceneLifecycle.Snapshot Snapshot(
        params InspectorSceneLifecycle.Entry[] scenes)
    {
        return Snapshot(scenes, activeHandle: 0, activePath: string.Empty);
    }

    private static InspectorSceneLifecycle.Snapshot Snapshot(
        InspectorSceneLifecycle.Entry scene,
        int activeHandle,
        string activePath)
    {
        return Snapshot(new[] { scene }, activeHandle, activePath);
    }

    private static InspectorSceneLifecycle.Snapshot Snapshot(
        InspectorSceneLifecycle.Entry scene1,
        InspectorSceneLifecycle.Entry scene2,
        int activeHandle,
        string activePath)
    {
        return Snapshot(new[] { scene1, scene2 }, activeHandle, activePath);
    }

    private static InspectorSceneLifecycle.Snapshot Snapshot(
        InspectorSceneLifecycle.Entry[] scenes,
        int activeHandle,
        string activePath)
    {
        return new InspectorSceneLifecycle.Snapshot
        {
            scenes = scenes,
            active_handle = activeHandle,
            active_path = activePath
        };
    }

    private static InspectorSceneLifecycle.Entry Scene(
        int handle,
        string path,
        int order,
        bool dirty)
    {
        return new InspectorSceneLifecycle.Entry
        {
            handle = handle,
            path = path,
            order = order,
            dirty = dirty
        };
    }

    private static InspectorSceneLifecycle.Decision Decision(
        InspectorSceneLifecycle.DecisionState state,
        InspectorSceneLifecycle.Ownership ownership,
        int targetHandle,
        int[]? matchedHandles = null)
    {
        return new InspectorSceneLifecycle.Decision
        {
            State = state,
            Ownership = ownership,
            TargetHandle = targetHandle,
            MatchedHandles = matchedHandles ?? Array.Empty<int>()
        };
    }
}
