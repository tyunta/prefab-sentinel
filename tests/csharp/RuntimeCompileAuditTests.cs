using Xunit;

namespace PrefabSentinel.Tests;

public class RuntimeCompileAuditTests
{
    // Break caught: reordering preflight branches exposes a less-actionable failure.
    [Fact]
    public void EvaluatePreflight_Uses_The_Required_Precedence_And_Sorts_Offenders()
    {
        var decision = RuntimeCompileAudit.EvaluatePreflight(
            new RuntimeCompileAudit.Snapshot
            {
                inventory_stable = false,
                prefab_repair_paths = new[] { "Assets/Z.prefab", "Assets/A.prefab" },
                related_assets = new[] { Asset("program", 8, "Assets/Udon/Z.asset", "UdonSharpProgramAsset", true) },
                loaded_scenes = new[] { Scene("Assets/Z.unity", true) },
                generated_asset_plan = Plan(created: new[] { "Assets/SerializedUdonPrograms/generated.asset" }),
            },
            RuntimeCompileAudit.GeneratedAssetPolicy.Deny,
            allowDirtyPrograms: false,
            allowDirtyScenes: false);

        Assert.False(decision.compile_allowed);
        Assert.Equal("UDON_COMPILE_PREFLIGHT_INDETERMINATE", decision.code);
        Assert.Empty(decision.offending_assets);
        Assert.Empty(decision.offending_paths);
        Assert.Empty(decision.attribution_unknown);
    }

    // Break caught: a policy branch accepts a forbidden compiler side effect or loses audit facts.
    [Fact]
    public void EvaluatePreflight_Applies_Each_Exact_Policy_Row()
    {
        foreach (PreflightCase row in PreflightRows())
        {
            var decision = RuntimeCompileAudit.EvaluatePreflight(
                row.snapshot, row.policy, row.allow_dirty_programs, row.allow_dirty_scenes);

            Assert.Equal(row.expected_allowed, decision.compile_allowed);
            Assert.Equal(row.expected_code, decision.code);
            Assert.Equal(row.expected_asset_paths, decision.offending_assets.Select(asset => asset.path));
            Assert.Equal(row.expected_paths, decision.offending_paths);
            Assert.Equal(row.expected_attribution_unknown, decision.attribution_unknown);
        }
    }

    // Break caught: path-only comparisons hide identity replacement at the same path.
    [Fact]
    public void Diff_Retains_Exact_Identity_And_Each_Independent_Delta()
    {
        var before = new RuntimeCompileAudit.Snapshot
        {
            related_assets = new[]
            {
                Asset("foo", 1, "Assets/Udon/Foo.asset", "UdonSharpProgramAsset", false),
                Asset("same-path-old", 2, "Assets/Udon/Same.asset", "UdonSharpProgramAsset", true),
                Asset("removed", 3, "Assets/SerializedUdonPrograms/Removed.asset", "SerializedUdonProgramAsset", true),
            },
            loaded_scenes = new[] { Scene("Assets/World.unity", false), Scene("Assets/Removed.unity", true) },
            project_dirty_paths = new[] { "Assets/Unrelated/Z.asset", "Assets/Unrelated/A.asset" },
        };
        var after = new RuntimeCompileAudit.Snapshot
        {
            related_assets = new[]
            {
                Asset("foo", 1, "Assets/Udon/Foo.asset", "UdonSharpProgramAsset", true),
                Asset("same-path-new", 4, "Assets/Udon/Same.asset", "UdonSharpProgramAsset", false),
                Asset("added", 5, "Assets/SerializedUdonPrograms/abc.asset", "SerializedUdonProgramAsset", true),
            },
            loaded_scenes = new[] { Scene("Assets/World.unity", true), Scene("Assets/Added.unity", true) },
            project_dirty_paths = new[] { "Assets/Unrelated/A.asset", "Assets/Unrelated/New.asset" },
        };

        var delta = RuntimeCompileAudit.Diff(
            before,
            after,
            Plan(
                created: new[] { "Assets/SerializedUdonPrograms/planned-new.asset" },
                deleted: new[] { "Assets/SerializedUdonPrograms/planned-old.asset" }));

        Assert.Equal(new[] { "Assets/SerializedUdonPrograms/abc.asset", "Assets/Udon/Foo.asset" }, delta.newly_dirty_paths);
        Assert.Equal(new[] { "Assets/SerializedUdonPrograms/Removed.asset", "Assets/Udon/Same.asset" }, delta.no_longer_dirty_paths);
        Assert.Equal(new[] { "Assets/Added.unity", "Assets/World.unity" }, delta.newly_dirty_scene_paths);
        Assert.Equal(new[] { "Assets/Removed.unity" }, delta.no_longer_dirty_scene_paths);
        Assert.Equal(new[] { "Assets/SerializedUdonPrograms/planned-new.asset" }, delta.planned_created_paths);
        Assert.Equal(new[] { "Assets/SerializedUdonPrograms/planned-old.asset" }, delta.planned_deleted_paths);
        Assert.Equal(new[] { "Assets/SerializedUdonPrograms/abc.asset", "Assets/Udon/Same.asset" }, delta.actual_created_paths);
        Assert.Equal(new[] { "Assets/SerializedUdonPrograms/Removed.asset", "Assets/Udon/Same.asset" }, delta.actual_deleted_paths);
        Assert.Equal(new[] { "Assets/Unrelated/A.asset", "Assets/Unrelated/Z.asset" }, delta.unrelated_dirty_paths_before);
        Assert.Equal(new[] { "Assets/Unrelated/A.asset", "Assets/Unrelated/New.asset" }, delta.unrelated_dirty_paths_after);
    }

    // Break caught: classification that omits a side-effect category reports a clean compile as info.
    [Fact]
    public void Classify_Reports_The_Required_Severity_And_Retains_The_Delta()
    {
        foreach (ClassificationCase row in ClassificationRows())
        {
            var classification = RuntimeCompileAudit.Classify(row.compile_succeeded, row.delta);

            Assert.Equal(row.expected_severity, classification.severity);
            Assert.Same(row.delta, classification.delta);
        }
    }


    // Break caught: ClientSim starts after a compile whose generated mutation
    // plan and observed result disagree.
    [Fact]
    public void GeneratedAssetOutcomeMatchesPlan_Rejects_Each_Mismatch_Lane()
    {
        var matching = new RuntimeCompileAudit.Delta
        {
            planned_created_paths = new[] { "Assets/New.asset" },
            planned_deleted_paths = new[] { "Assets/Old.asset" },
            actual_created_paths = new[] { "Assets/New.asset" },
            actual_deleted_paths = new[] { "Assets/Old.asset" },
        };
        Assert.True(RuntimeCompileAudit.GeneratedAssetOutcomeMatchesPlan(matching));

        foreach (RuntimeCompileAudit.Delta mismatch in new[]
        {
            new RuntimeCompileAudit.Delta
            {
                planned_created_paths = new[] { "Assets/New.asset" },
            },
            new RuntimeCompileAudit.Delta
            {
                actual_created_paths = new[] { "Assets/New.asset" },
            },
            new RuntimeCompileAudit.Delta
            {
                planned_deleted_paths = new[] { "Assets/Old.asset" },
            },
            new RuntimeCompileAudit.Delta
            {
                actual_deleted_paths = new[] { "Assets/Old.asset" },
            },
        })
        {
            Assert.False(
                RuntimeCompileAudit.GeneratedAssetOutcomeMatchesPlan(mismatch));
        }
    }


    // Break caught: a normal UdonSharp compile failure loses its compiler code
    // and diagnostics when a partial generated-asset plan cannot be applied.
    [Fact]
    public void GeneratedAssetOutcomeMismatchRequiresFailure_Is_Success_Only()
    {
        var mismatch = new RuntimeCompileAudit.Delta
        {
            planned_created_paths = new[] { "Assets/New.asset" },
        };

        Assert.True(
            RuntimeCompileAudit.GeneratedAssetOutcomeMismatchRequiresFailure(
                compileSucceeded: true,
                mismatch));
        Assert.False(
            RuntimeCompileAudit.GeneratedAssetOutcomeMismatchRequiresFailure(
                compileSucceeded: false,
                mismatch));
        Assert.False(
            RuntimeCompileAudit.GeneratedAssetOutcomeMismatchRequiresFailure(
                compileSucceeded: true,
                new RuntimeCompileAudit.Delta()));
    }


    [Fact]
    public void ParsePolicy_Maps_Only_The_Declared_Json_Values()
    {
        Assert.Equal(RuntimeCompileAudit.GeneratedAssetPolicy.Deny, RuntimeCompileAudit.ParsePolicy("deny"));
        Assert.Equal(RuntimeCompileAudit.GeneratedAssetPolicy.Create, RuntimeCompileAudit.ParsePolicy("create"));
        Assert.Equal(RuntimeCompileAudit.GeneratedAssetPolicy.Replace, RuntimeCompileAudit.ParsePolicy("replace"));
    }

    // Break caught: incomplete authorization evidence must never be upgraded to a clean preflight.
    [Fact]
    public void EvaluatePreflight_Rejects_Null_And_Incomplete_Snapshot_Evidence()
    {
        var incomplete = new RuntimeCompileAudit.Snapshot?[]
        {
            null,
            new RuntimeCompileAudit.Snapshot { prefab_repair_paths = null! },
            new RuntimeCompileAudit.Snapshot { related_assets = null! },
            new RuntimeCompileAudit.Snapshot { related_assets = new RuntimeCompileAudit.AssetIdentity[] { null! } },
            new RuntimeCompileAudit.Snapshot { loaded_scenes = null! },
            new RuntimeCompileAudit.Snapshot { loaded_scenes = new RuntimeCompileAudit.SceneIdentity[] { null! } },
            new RuntimeCompileAudit.Snapshot { generated_asset_plan = null! },
            new RuntimeCompileAudit.Snapshot { generated_asset_plan = new RuntimeCompileAudit.GeneratedAssetPlan { planned_created_paths = null! } },
            new RuntimeCompileAudit.Snapshot { generated_asset_plan = new RuntimeCompileAudit.GeneratedAssetPlan { planned_deleted_paths = null! } },
            new RuntimeCompileAudit.Snapshot { project_dirty_paths = null! },
        };

        foreach (RuntimeCompileAudit.Snapshot? snapshot in incomplete)
        {
            var decision = RuntimeCompileAudit.EvaluatePreflight(
                snapshot!, RuntimeCompileAudit.GeneratedAssetPolicy.Replace, false, false);
            Assert.False(decision.compile_allowed);
            Assert.Equal("UDON_COMPILE_PREFLIGHT_INDETERMINATE", decision.code);
        }
    }

    // Break caught: lower-priority preflight failures can mask a stronger authorization refusal.
    [Fact]
    public void EvaluatePreflight_Peels_One_Gate_At_A_Time_With_All_Lower_Conflicts()
    {
        var rows = new List<(RuntimeCompileAudit.Snapshot snapshot, string code)>();
        var unstable = AllConflicts();
        unstable.inventory_stable = false;
        rows.Add((unstable, "UDON_COMPILE_PREFLIGHT_INDETERMINATE"));
        rows.Add((AllConflicts(), "UDON_COMPILE_PREFAB_REPAIR_REQUIRED"));
        var dirtyProgram = AllConflicts();
        dirtyProgram.prefab_repair_paths = Array.Empty<string>();
        rows.Add((dirtyProgram, "UDON_COMPILE_DIRTY_PRECONDITION"));
        var dirtyScene = AllConflicts();
        dirtyScene.prefab_repair_paths = Array.Empty<string>();
        dirtyScene.related_assets = new[] { Asset("clean", 1, "Assets/Udon/Clean.asset", "UdonSharpProgramAsset", false) };
        rows.Add((dirtyScene, "UDON_COMPILE_DIRTY_SCENE_PRECONDITION"));
        var replacement = AllConflicts();
        replacement.prefab_repair_paths = Array.Empty<string>();
        replacement.related_assets = new[] { Asset("clean", 1, "Assets/Udon/Clean.asset", "UdonSharpProgramAsset", false) };
        replacement.loaded_scenes = new[] { Scene("Assets/Clean.unity", false) };
        rows.Add((replacement, "UDON_GENERATED_ASSET_REPLACEMENT_REQUIRED"));

        foreach ((RuntimeCompileAudit.Snapshot snapshot, string expectedCode) in rows)
        {
            var decision = RuntimeCompileAudit.EvaluatePreflight(
                snapshot, RuntimeCompileAudit.GeneratedAssetPolicy.Deny, false, false);
            Assert.False(decision.compile_allowed);
            Assert.Equal(expectedCode, decision.code);
        }
    }

    // Break caught: a create/delete replacement under deny is misclassified as create-only.
    [Fact]
    public void EvaluatePreflight_Recognizes_A_Replacement_Before_Creation()
    {
        var decision = RuntimeCompileAudit.EvaluatePreflight(
            new RuntimeCompileAudit.Snapshot
            {
                generated_asset_plan = Plan(
                    created: new[] { "Assets/SerializedUdonPrograms/new.asset" },
                    deleted: new[] { "Assets/SerializedUdonPrograms/old.asset" }),
            },
            RuntimeCompileAudit.GeneratedAssetPolicy.Deny,
            false,
            false);

        Assert.False(decision.compile_allowed);
        Assert.Equal("UDON_GENERATED_ASSET_REPLACEMENT_REQUIRED", decision.code);
        Assert.Equal(new[] { "Assets/SerializedUdonPrograms/old.asset" }, decision.offending_paths);
    }

    // Break caught: a removed clean Scene is reported as a dirty-to-clean transition.
    [Fact]
    public void Diff_Does_Not_Report_A_Removed_Clean_Scene_As_No_Longer_Dirty()
    {
        var delta = RuntimeCompileAudit.Diff(
            new RuntimeCompileAudit.Snapshot { loaded_scenes = new[] { Scene("Assets/Clean.unity", false) } },
            new RuntimeCompileAudit.Snapshot(),
            Plan());

        Assert.Empty(delta.no_longer_dirty_scene_paths);
    }

    // Break caught: incomplete post-mutation evidence is silently converted into a clean delta/classification.
    [Fact]
    public void Diff_And_Classify_Reject_Null_Or_Incomplete_PostMutation_Evidence()
    {
        ArgumentNullException nullBefore = Assert.Throws<ArgumentNullException>(() =>
            RuntimeCompileAudit.Diff(null!, new RuntimeCompileAudit.Snapshot(), Plan()));
        Assert.Equal("before", nullBefore.ParamName);
        var incompleteBefore = new RuntimeCompileAudit.Snapshot { related_assets = null! };
        ArgumentException incomplete = Assert.Throws<ArgumentException>(() =>
            RuntimeCompileAudit.Diff(incompleteBefore, new RuntimeCompileAudit.Snapshot(), Plan()));
        Assert.Equal("before", incomplete.ParamName);
        ArgumentNullException nullPlan = Assert.Throws<ArgumentNullException>(() =>
            RuntimeCompileAudit.Diff(new RuntimeCompileAudit.Snapshot(), new RuntimeCompileAudit.Snapshot(), null!));
        Assert.Equal("planned", nullPlan.ParamName);
        var incompleteDelta = new RuntimeCompileAudit.Delta { actual_created_paths = null! };
        ArgumentException classificationIncomplete = Assert.Throws<ArgumentException>(() =>
            RuntimeCompileAudit.Classify(true, incompleteDelta));
        Assert.Equal("delta", classificationIncomplete.ParamName);
        ArgumentNullException classificationNull = Assert.Throws<ArgumentNullException>(() =>
            RuntimeCompileAudit.Classify(true, null!));
        Assert.Equal("delta", classificationNull.ParamName);
    }

    // Break caught: any individual side-effect lane, including changed unrelated baselines, can disappear from severity.
    [Fact]
    public void Classify_Covers_Each_SideEffect_Lane_And_Unrelated_Baseline_Delta()
    {
        var warnings = new RuntimeCompileAudit.Delta[]
        {
            new RuntimeCompileAudit.Delta { newly_dirty_paths = new[] { "Assets/A.asset" } },
            new RuntimeCompileAudit.Delta { no_longer_dirty_paths = new[] { "Assets/A.asset" } },
            new RuntimeCompileAudit.Delta { newly_dirty_scene_paths = new[] { "Assets/A.unity" } },
            new RuntimeCompileAudit.Delta { no_longer_dirty_scene_paths = new[] { "Assets/A.unity" } },
            new RuntimeCompileAudit.Delta { planned_created_paths = new[] { "Assets/A.asset" } },
            new RuntimeCompileAudit.Delta { planned_deleted_paths = new[] { "Assets/A.asset" } },
            new RuntimeCompileAudit.Delta { actual_created_paths = new[] { "Assets/A.asset" } },
            new RuntimeCompileAudit.Delta { actual_deleted_paths = new[] { "Assets/A.asset" } },
            new RuntimeCompileAudit.Delta { attribution_unknown = new[] { "asset-attribution" } },
            new RuntimeCompileAudit.Delta { unrelated_dirty_paths_before = new[] { "Assets/A.asset" }, unrelated_dirty_paths_after = new[] { "Assets/B.asset" } },
        };

        foreach (RuntimeCompileAudit.Delta delta in warnings)
        {
            Assert.Equal("warning", RuntimeCompileAudit.Classify(true, delta).severity);
        }
        var unchanged = new RuntimeCompileAudit.Delta
        {
            unrelated_dirty_paths_before = new[] { "Assets/A.asset", "Assets/Z.asset" },
            unrelated_dirty_paths_after = new[] { "Assets/A.asset", "Assets/Z.asset" },
        };
        Assert.Equal("info", RuntimeCompileAudit.Classify(true, unchanged).severity);
    }

    // Break caught: one identity field is ignored, collapsing distinct compiler artifacts.
    [Fact]
    public void Diff_Uses_Each_Identity_Field_And_Ordinal_Path_Order()
    {
        AssertIdentityChange(Asset("guid-b", 1, "Assets/Z.asset", "Type", false), new[] { "Assets/Z.asset" }, new[] { "Assets/Z.asset" });
        AssertIdentityChange(Asset("guid-a", 2, "Assets/Z.asset", "Type", false), new[] { "Assets/Z.asset" }, new[] { "Assets/Z.asset" });
        AssertIdentityChange(Asset("guid-a", 1, "Assets/A.asset", "Type", false), new[] { "Assets/A.asset" }, new[] { "Assets/Z.asset" });
        AssertIdentityChange(Asset("guid-a", 1, "Assets/Z.asset", "OtherType", false), new[] { "Assets/Z.asset" }, new[] { "Assets/Z.asset" });
        var decision = RuntimeCompileAudit.EvaluatePreflight(
            new RuntimeCompileAudit.Snapshot { prefab_repair_paths = new[] { "Assets/z.prefab", "Assets/A.prefab", "Assets/a.prefab" } },
            RuntimeCompileAudit.GeneratedAssetPolicy.Replace,
            false,
            false);
        Assert.Equal(new[] { "Assets/A.prefab", "Assets/a.prefab", "Assets/z.prefab" }, decision.offending_paths);
    }

    [Fact]
    public void ParsePolicy_Rejects_WrongCase_Unknown_And_Null_Values()
    {
        foreach (string? value in new string?[] { "Deny", "CREATE", "replace ", "unknown", null })
        {
            ArgumentException error = Assert.Throws<ArgumentException>(() => RuntimeCompileAudit.ParsePolicy(value!));
            Assert.Equal("value", error.ParamName);
        }
    }

    // Break caught: a fallback-only UdonSharp program is silently accepted as the exact compile set.
    [Fact]
    public void InventorySetsAgree_Rejects_Disagreement_And_Ignores_Ordering()
    {
        var exact = new[]
        {
            Asset("guid-a", 1, "Assets/Udon/A.asset", "UdonSharpProgramAsset", false),
            Asset("guid-b", 2, "Assets/Udon/B.asset", "UdonSharpProgramAsset", false),
        };
        var reordered = new[] { exact[1], exact[0] };
        var missing = new[] { exact[0] };
        var extra = new[]
        {
            exact[0],
            exact[1],
            Asset("guid-c", 3, "Assets/Udon/C.asset", "UdonSharpProgramAsset", false),
        };

        Assert.True(RuntimeCompileAudit.InventorySetsAgree(exact, reordered));
        Assert.False(RuntimeCompileAudit.InventorySetsAgree(exact, missing));
        Assert.False(RuntimeCompileAudit.InventorySetsAgree(exact, extra));
    }

    // Break caught: generated assets are predicted from a display name instead of the stable program GUID.
    [Fact]
    public void CanonicalGeneratedAssetPath_Is_Constructed_From_The_Program_Guid()
    {
        Assert.Equal(
            "Assets/SerializedUdonPrograms/0123456789abcdef.asset",
            RuntimeCompileAudit.CanonicalGeneratedAssetPath("0123456789abcdef"));
    }

    // Break caught: a noncanonical serialized-program reference loses either required replacement lane.
    [Fact]
    public void PlanGeneratedAsset_Follows_Reference_Name_Not_Reference_Path()
    {
        var movedCanonicalName = RuntimeCompileAudit.PlanGeneratedAsset(
            "program-guid",
            hasReferencedAsset: true,
            referencedName: "program-guid",
            referencedPath: "Assets/Moved/program-guid.asset",
            canonicalPathOccupied: false,
            canonicalHasExpectedType: false);
        Assert.Empty(movedCanonicalName.planned_created_paths);
        Assert.Empty(movedCanonicalName.planned_deleted_paths);

        var movedLegacyName = RuntimeCompileAudit.PlanGeneratedAsset(
            "program-guid",
            hasReferencedAsset: true,
            referencedName: "legacy-name",
            referencedPath: "Assets/Moved/renamed-reference.asset",
            canonicalPathOccupied: false,
            canonicalHasExpectedType: false);
        Assert.Equal(
            new[] { "Assets/SerializedUdonPrograms/program-guid.asset" },
            movedLegacyName.planned_created_paths);
        Assert.Equal(
            new[] { "Assets/SerializedUdonPrograms/legacy-name.asset" },
            movedLegacyName.planned_deleted_paths);
        Assert.DoesNotContain(
            "Assets/Moved/renamed-reference.asset",
            movedLegacyName.planned_deleted_paths);

        var existingCanonical = RuntimeCompileAudit.PlanGeneratedAsset(
            "program-guid",
            hasReferencedAsset: true,
            referencedName: "legacy-name",
            referencedPath: "Assets/Moved/legacy-name.asset",
            canonicalPathOccupied: true,
            canonicalHasExpectedType: true);
        Assert.Empty(existingCanonical.planned_created_paths);
        Assert.Equal(
            new[] { "Assets/SerializedUdonPrograms/legacy-name.asset" },
            existingCanonical.planned_deleted_paths);
    }

    // Break caught: the after snapshot treats a dirty transition of the same compiler asset as replacement.
    [Fact]
    public void Same_Identity_After_Snapshot_Is_A_Dirty_Delta_Not_Asset_Replacement()
    {
        var beforeAsset = Asset(
            "program-guid", 11400000, "Assets/Udon/Program.asset", "UdonSharpProgramAsset", false);
        var afterAsset = Asset(
            "program-guid", 11400000, "Assets/Udon/Program.asset", "UdonSharpProgramAsset", true);

        Assert.True(RuntimeCompileAudit.InventorySetsAgree(
            new[] { beforeAsset },
            new[] { afterAsset }));

        var delta = RuntimeCompileAudit.Diff(
            new RuntimeCompileAudit.Snapshot { related_assets = new[] { beforeAsset } },
            new RuntimeCompileAudit.Snapshot { related_assets = new[] { afterAsset } },
            Plan());

        Assert.Equal(new[] { "Assets/Udon/Program.asset" }, delta.newly_dirty_paths);
        Assert.Empty(delta.actual_created_paths);
        Assert.Empty(delta.actual_deleted_paths);
    }

    // Break caught: a reloaded Scene at the same path is collapsed into the prior loaded identity.
    [Fact]
    public void Diff_Distinguishes_Loaded_Scenes_By_Path_And_Handle()
    {
        var delta = RuntimeCompileAudit.Diff(
            new RuntimeCompileAudit.Snapshot
            {
                loaded_scenes = new[] { Scene("Assets/World.unity", true, handle: 10) },
            },
            new RuntimeCompileAudit.Snapshot
            {
                loaded_scenes = new[] { Scene("Assets/World.unity", true, handle: 11) },
            },
            Plan());

        Assert.Equal(new[] { "Assets/World.unity" }, delta.newly_dirty_scene_paths);
        Assert.Equal(new[] { "Assets/World.unity" }, delta.no_longer_dirty_scene_paths);
    }

    // Break caught: an unrelated object at the canonical path is mistaken for a usable serialized program.
    [Fact]
    public void Wrong_Type_Canonical_Occupant_Is_A_Replacement_With_Exact_Type_Delta()
    {
        const string canonical =
            "Assets/SerializedUdonPrograms/program-guid.asset";
        var plan = RuntimeCompileAudit.PlanGeneratedAsset(
            "program-guid",
            hasReferencedAsset: false,
            referencedName: string.Empty,
            referencedPath: string.Empty,
            canonicalPathOccupied: true,
            canonicalHasExpectedType: false);

        Assert.Equal(new[] { canonical }, plan.planned_created_paths);
        Assert.Equal(new[] { canonical }, plan.planned_deleted_paths);

        foreach (var row in new[]
        {
            (RuntimeCompileAudit.GeneratedAssetPolicy.Deny, false),
            (RuntimeCompileAudit.GeneratedAssetPolicy.Create, false),
            (RuntimeCompileAudit.GeneratedAssetPolicy.Replace, true),
        })
        {
            var decision = RuntimeCompileAudit.EvaluatePreflight(
                new RuntimeCompileAudit.Snapshot
                {
                    related_assets = new[]
                    {
                        Asset(
                            "wrong-type-guid",
                            11400000,
                            canonical,
                            "UnityEngine.TextAsset",
                            false),
                    },
                    generated_asset_plan = plan,
                },
                row.Item1,
                allowDirtyPrograms: false,
                allowDirtyScenes: false);

            Assert.Equal(row.Item2, decision.compile_allowed);
            Assert.Equal(
                row.Item2
                    ? string.Empty
                    : "UDON_GENERATED_ASSET_REPLACEMENT_REQUIRED",
                decision.code);
        }

        var delta = RuntimeCompileAudit.Diff(
            new RuntimeCompileAudit.Snapshot
            {
                related_assets = new[]
                {
                    Asset(
                        "wrong-type-guid",
                        11400000,
                        canonical,
                        "UnityEngine.TextAsset",
                        false),
                },
            },
            new RuntimeCompileAudit.Snapshot
            {
                related_assets = new[]
                {
                    Asset(
                        "serialized-guid",
                        11400000,
                        canonical,
                        "VRC.Udon.ProgramSources.SerializedUdonProgramAsset",
                        false),
                },
            },
            plan);

        Assert.Equal(new[] { canonical }, delta.actual_created_paths);
        Assert.Equal(new[] { canonical }, delta.actual_deleted_paths);
    }

    // Break caught: failed observation is converted into an observed-empty after snapshot.
    [Fact]
    public void SnapshotAfterCaptureFailure_Preserves_Exact_Facts_And_Marks_Unknown()
    {
        const string unknown = "post_compile_snapshot_incomplete";
        var before = new RuntimeCompileAudit.Snapshot
        {
            related_assets = new[]
            {
                Asset(
                    "program-guid",
                    1,
                    "Assets/Udon/Program.asset",
                    "UdonSharpProgramAsset",
                    true,
                    new[] { "dirty_before" }),
                Asset(
                    "generated-guid",
                    2,
                    "Assets/SerializedUdonPrograms/program-guid.asset",
                    "SerializedUdonProgramAsset",
                    false),
            },
            loaded_scenes = new[]
            {
                Scene(
                    "Assets/Dirty.unity",
                    true,
                    new[] { "scene_dirty_before" },
                    handle: 41),
                Scene("Assets/Clean.unity", false, handle: 42),
            },
            project_dirty_paths = new[] { "Assets/Unrelated.asset" },
        };

        RuntimeCompileAudit.Snapshot after =
            RuntimeCompileAudit.SnapshotAfterCaptureFailure(before, unknown);

        Assert.False(after.inventory_stable);
        Assert.NotSame(before.related_assets, after.related_assets);
        Assert.NotSame(before.loaded_scenes, after.loaded_scenes);
        Assert.Equal(
            before.related_assets.Select(asset =>
                (asset.guid, asset.local_file_id, asset.path, asset.type, asset.dirty)),
            after.related_assets.Select(asset =>
                (asset.guid, asset.local_file_id, asset.path, asset.type, asset.dirty)));
        Assert.Equal(
            before.loaded_scenes.Select(scene =>
                (scene.path, scene.handle, scene.dirty)),
            after.loaded_scenes.Select(scene =>
                (scene.path, scene.handle, scene.dirty)));
        Assert.All(
            after.related_assets,
            asset => Assert.Contains(unknown, asset.attribution_unknown));
        Assert.All(
            after.loaded_scenes,
            scene => Assert.Contains(unknown, scene.attribution_unknown));
        Assert.Equal(before.project_dirty_paths, after.project_dirty_paths);

        RuntimeCompileAudit.Delta delta =
            RuntimeCompileAudit.Diff(before, after, Plan());
        Assert.Empty(delta.newly_dirty_paths);
        Assert.Empty(delta.no_longer_dirty_paths);
        Assert.Empty(delta.newly_dirty_scene_paths);
        Assert.Empty(delta.no_longer_dirty_scene_paths);
        Assert.Empty(delta.actual_created_paths);
        Assert.Empty(delta.actual_deleted_paths);
        Assert.Contains(unknown, delta.attribution_unknown);
    }

    private static RuntimeCompileAudit.Snapshot AllConflicts()
    {
        return new RuntimeCompileAudit.Snapshot
        {
            prefab_repair_paths = new[] { "Assets/Repair.prefab" },
            related_assets = new[] { Asset("dirty", 1, "Assets/Udon/Dirty.asset", "UdonSharpProgramAsset", true) },
            loaded_scenes = new[] { Scene("Assets/Dirty.unity", true) },
            generated_asset_plan = Plan(
                created: new[] { "Assets/SerializedUdonPrograms/new.asset" },
                deleted: new[] { "Assets/SerializedUdonPrograms/old.asset" }),
        };
    }

    private static void AssertIdentityChange(RuntimeCompileAudit.AssetIdentity after, string[] expectedCreated, string[] expectedDeleted)
    {
        var delta = RuntimeCompileAudit.Diff(
            new RuntimeCompileAudit.Snapshot { related_assets = new[] { Asset("guid-a", 1, "Assets/Z.asset", "Type", false) } },
            new RuntimeCompileAudit.Snapshot { related_assets = new[] { after } },
            Plan());
        Assert.Equal(expectedCreated, delta.actual_created_paths);
        Assert.Equal(expectedDeleted, delta.actual_deleted_paths);
    }

    // Break caught: nested missing identity evidence is treated as a complete authorization snapshot.
    [Fact]
    public void EvaluatePreflight_Rejects_Malformed_Nested_Evidence()
    {
        var snapshots = new RuntimeCompileAudit.Snapshot[]
        {
            new RuntimeCompileAudit.Snapshot { related_assets = new[] { Asset(null!, 1, "Assets/A.asset", "UdonSharpProgramAsset", false) } },
            new RuntimeCompileAudit.Snapshot { related_assets = new[] { Asset("", 1, "Assets/A.asset", "UdonSharpProgramAsset", false) } },
            new RuntimeCompileAudit.Snapshot { related_assets = new[] { Asset("guid", 1, "", "UdonSharpProgramAsset", false) } },
            new RuntimeCompileAudit.Snapshot { related_assets = new[] { Asset("guid", 1, "Assets/A.asset", "", false) } },
            new RuntimeCompileAudit.Snapshot { related_assets = new[] { new RuntimeCompileAudit.AssetIdentity { guid = "guid", local_file_id = 1, path = "Assets/A.asset", type = "UdonSharpProgramAsset", attribution_unknown = null! } } },
            new RuntimeCompileAudit.Snapshot { loaded_scenes = new[] { Scene(null!, false) } },
            new RuntimeCompileAudit.Snapshot { loaded_scenes = new[] { Scene("", false) } },
            new RuntimeCompileAudit.Snapshot { loaded_scenes = new[] { new RuntimeCompileAudit.SceneIdentity { path = "Assets/A.unity", attribution_unknown = null! } } },
            new RuntimeCompileAudit.Snapshot { prefab_repair_paths = new string[] { null! } },
            new RuntimeCompileAudit.Snapshot { prefab_repair_paths = new[] { "" } },
            new RuntimeCompileAudit.Snapshot { project_dirty_paths = new string[] { null! } },
            new RuntimeCompileAudit.Snapshot { project_dirty_paths = new[] { "" } },
            new RuntimeCompileAudit.Snapshot { generated_asset_plan = new RuntimeCompileAudit.GeneratedAssetPlan { planned_created_paths = new string[] { null! } } },
            new RuntimeCompileAudit.Snapshot { generated_asset_plan = new RuntimeCompileAudit.GeneratedAssetPlan { planned_deleted_paths = new[] { "" } } },
        };

        foreach (RuntimeCompileAudit.Snapshot snapshot in snapshots)
        {
            var decision = RuntimeCompileAudit.EvaluatePreflight(
                snapshot, RuntimeCompileAudit.GeneratedAssetPolicy.Replace, false, false);
            Assert.False(decision.compile_allowed);
            Assert.Equal("UDON_COMPILE_PREFLIGHT_INDETERMINATE", decision.code);
        }
    }

    // Break caught: malformed nested post-mutation facts reach diff/classification as clean evidence.
    [Fact]
    public void Diff_And_Classify_Reject_Malformed_Nested_Evidence_With_Parameter_Names()
    {
        ArgumentException before = Assert.Throws<ArgumentException>(() => RuntimeCompileAudit.Diff(
            new RuntimeCompileAudit.Snapshot { related_assets = new[] { Asset("", 1, "Assets/A.asset", "Type", false) } },
            new RuntimeCompileAudit.Snapshot(),
            Plan()));
        Assert.Equal("before", before.ParamName);
        ArgumentException after = Assert.Throws<ArgumentException>(() => RuntimeCompileAudit.Diff(
            new RuntimeCompileAudit.Snapshot(),
            new RuntimeCompileAudit.Snapshot { loaded_scenes = new[] { new RuntimeCompileAudit.SceneIdentity { path = "Assets/A.unity", attribution_unknown = null! } } },
            Plan()));
        Assert.Equal("after", after.ParamName);
        ArgumentException planned = Assert.Throws<ArgumentException>(() => RuntimeCompileAudit.Diff(
            new RuntimeCompileAudit.Snapshot(),
            new RuntimeCompileAudit.Snapshot(),
            new RuntimeCompileAudit.GeneratedAssetPlan { planned_created_paths = new[] { "" } }));
        Assert.Equal("planned", planned.ParamName);

        foreach (RuntimeCompileAudit.Delta delta in new RuntimeCompileAudit.Delta[]
        {
            new RuntimeCompileAudit.Delta { actual_created_paths = null! },
            new RuntimeCompileAudit.Delta { actual_created_paths = new string[] { null! } },
            new RuntimeCompileAudit.Delta { actual_created_paths = new[] { "" } },
        })
        {
            ArgumentException classification = Assert.Throws<ArgumentException>(() => RuntimeCompileAudit.Classify(true, delta));
            Assert.Equal("delta", classification.ParamName);
        }
    }

    [Fact]
    public void AttributionEvidence_Rejects_Null_And_Empty_Entries()
    {
        var snapshots = new RuntimeCompileAudit.Snapshot[]
        {
            new RuntimeCompileAudit.Snapshot { related_assets = new[] { new RuntimeCompileAudit.AssetIdentity { guid = "guid", path = "Assets/A.asset", type = "Type", attribution_unknown = new string[] { null! } } } },
            new RuntimeCompileAudit.Snapshot { related_assets = new[] { new RuntimeCompileAudit.AssetIdentity { guid = "guid", path = "Assets/A.asset", type = "Type", attribution_unknown = new[] { "" } } } },
            new RuntimeCompileAudit.Snapshot { loaded_scenes = new[] { new RuntimeCompileAudit.SceneIdentity { path = "Assets/A.unity", attribution_unknown = new string[] { null! } } } },
            new RuntimeCompileAudit.Snapshot { loaded_scenes = new[] { new RuntimeCompileAudit.SceneIdentity { path = "Assets/A.unity", attribution_unknown = new[] { "" } } } },
        };
        foreach (RuntimeCompileAudit.Snapshot snapshot in snapshots)
        {
            var decision = RuntimeCompileAudit.EvaluatePreflight(snapshot, RuntimeCompileAudit.GeneratedAssetPolicy.Replace, false, false);
            Assert.Equal("UDON_COMPILE_PREFLIGHT_INDETERMINATE", decision.code);
        }
        ArgumentException before = Assert.Throws<ArgumentException>(() => RuntimeCompileAudit.Diff(snapshots[0], new RuntimeCompileAudit.Snapshot(), Plan()));
        Assert.Equal("before", before.ParamName);
        ArgumentException after = Assert.Throws<ArgumentException>(() => RuntimeCompileAudit.Diff(new RuntimeCompileAudit.Snapshot(), snapshots[2], Plan()));
        Assert.Equal("after", after.ParamName);
    }

    private static IEnumerable<PreflightCase> PreflightRows()
    {
        yield return Row(
            new RuntimeCompileAudit.Snapshot { prefab_repair_paths = new[] { "Assets/Z.prefab", "Assets/A.prefab" } },
            RuntimeCompileAudit.GeneratedAssetPolicy.Replace, false, false, false,
            "UDON_COMPILE_PREFAB_REPAIR_REQUIRED", Array.Empty<string>(),
            new[] { "Assets/A.prefab", "Assets/Z.prefab" }, Array.Empty<string>());
        yield return Row(
            new RuntimeCompileAudit.Snapshot
            {
                related_assets = new[]
                {
                    Asset("program-z", 20, "Assets/Udon/Z.asset", "UdonSharpProgramAsset", true, new[] { "program-z", "program-a" }),
                    Asset("serialized-a", 10, "Assets/SerializedUdonPrograms/A.asset", "SerializedUdonProgramAsset", true),
                    Asset("script", 30, "Assets/Udon/Foo.cs", "MonoScript", true),
                },
            },
            RuntimeCompileAudit.GeneratedAssetPolicy.Replace, false, false, false,
            "UDON_COMPILE_DIRTY_PRECONDITION",
            new[] { "Assets/SerializedUdonPrograms/A.asset", "Assets/Udon/Z.asset" },
            Array.Empty<string>(), Array.Empty<string>());
        yield return Row(
            new RuntimeCompileAudit.Snapshot { related_assets = new[] { Asset("program-z", 20, "Assets/Udon/Z.asset", "UdonSharpProgramAsset", true, new[] { "program-z", "program-a" }) } },
            RuntimeCompileAudit.GeneratedAssetPolicy.Replace, true, false, true,
            string.Empty, Array.Empty<string>(), Array.Empty<string>(), new[] { "program-a", "program-z" });
        yield return Row(
            new RuntimeCompileAudit.Snapshot { loaded_scenes = new[] { Scene("Assets/Z.unity", true, new[] { "scene-z", "scene-a" }) } },
            RuntimeCompileAudit.GeneratedAssetPolicy.Replace, false, false, false,
            "UDON_COMPILE_DIRTY_SCENE_PRECONDITION", Array.Empty<string>(), new[] { "Assets/Z.unity" }, Array.Empty<string>());
        yield return Row(
            new RuntimeCompileAudit.Snapshot { loaded_scenes = new[] { Scene("Assets/Z.unity", true, new[] { "scene-z", "scene-a" }) } },
            RuntimeCompileAudit.GeneratedAssetPolicy.Replace, false, true, true,
            string.Empty, Array.Empty<string>(), Array.Empty<string>(), new[] { "scene-a", "scene-z" });
        yield return Row(
            new RuntimeCompileAudit.Snapshot { generated_asset_plan = Plan(created: new[] { "Assets/SerializedUdonPrograms/new.asset" }) },
            RuntimeCompileAudit.GeneratedAssetPolicy.Deny, false, false, false,
            "UDON_GENERATED_ASSET_CREATION_REQUIRED", Array.Empty<string>(), new[] { "Assets/SerializedUdonPrograms/new.asset" }, Array.Empty<string>());
        yield return Row(
            new RuntimeCompileAudit.Snapshot { generated_asset_plan = Plan(created: new[] { "Assets/SerializedUdonPrograms/new.asset" }, deleted: new[] { "Assets/SerializedUdonPrograms/old.asset" }) },
            RuntimeCompileAudit.GeneratedAssetPolicy.Create, false, false, false,
            "UDON_GENERATED_ASSET_REPLACEMENT_REQUIRED", Array.Empty<string>(), new[] { "Assets/SerializedUdonPrograms/old.asset" }, Array.Empty<string>());
        yield return Row(
            new RuntimeCompileAudit.Snapshot { generated_asset_plan = Plan(created: new[] { "Assets/SerializedUdonPrograms/new.asset" }) },
            RuntimeCompileAudit.GeneratedAssetPolicy.Create, false, false, true,
            string.Empty, Array.Empty<string>(), Array.Empty<string>(), Array.Empty<string>());
        yield return Row(
            new RuntimeCompileAudit.Snapshot { generated_asset_plan = Plan(created: new[] { "Assets/SerializedUdonPrograms/new.asset" }) },
            RuntimeCompileAudit.GeneratedAssetPolicy.Replace, false, false, true,
            string.Empty, Array.Empty<string>(), Array.Empty<string>(), Array.Empty<string>());
        yield return Row(
            new RuntimeCompileAudit.Snapshot { generated_asset_plan = Plan(created: new[] { "Assets/SerializedUdonPrograms/new.asset" }, deleted: new[] { "Assets/SerializedUdonPrograms/old.asset" }) },
            RuntimeCompileAudit.GeneratedAssetPolicy.Replace, false, false, true,
            string.Empty, Array.Empty<string>(), Array.Empty<string>(), Array.Empty<string>());
    }

    private static IEnumerable<ClassificationCase> ClassificationRows()
    {
        yield return new ClassificationCase(true, new RuntimeCompileAudit.Delta(), "info");
        yield return new ClassificationCase(true, new RuntimeCompileAudit.Delta { actual_created_paths = new[] { "Assets/SerializedUdonPrograms/abc.asset" } }, "warning");
        yield return new ClassificationCase(true, new RuntimeCompileAudit.Delta { attribution_unknown = new[] { "program-attribution" } }, "warning");
        yield return new ClassificationCase(false, new RuntimeCompileAudit.Delta { newly_dirty_paths = new[] { "Assets/Udon/Foo.asset" } }, "error");
    }

    private static PreflightCase Row(
        RuntimeCompileAudit.Snapshot snapshot,
        RuntimeCompileAudit.GeneratedAssetPolicy policy,
        bool allowDirtyPrograms,
        bool allowDirtyScenes,
        bool expectedAllowed,
        string expectedCode,
        string[] expectedAssetPaths,
        string[] expectedPaths,
        string[] expectedAttributionUnknown)
    {
        return new PreflightCase(snapshot, policy, allowDirtyPrograms, allowDirtyScenes, expectedAllowed, expectedCode, expectedAssetPaths, expectedPaths, expectedAttributionUnknown);
    }

    private static RuntimeCompileAudit.AssetIdentity Asset(string guid, long localFileId, string path, string type, bool dirty, string[]? attributionUnknown = null)
    {
        return new RuntimeCompileAudit.AssetIdentity { guid = guid, local_file_id = localFileId, path = path, type = type, dirty = dirty, attribution_unknown = attributionUnknown ?? Array.Empty<string>() };
    }

    private static RuntimeCompileAudit.SceneIdentity Scene(
        string path,
        bool dirty,
        string[]? attributionUnknown = null,
        int handle = 0)
    {
        return new RuntimeCompileAudit.SceneIdentity
        {
            path = path,
            handle = handle,
            dirty = dirty,
            attribution_unknown = attributionUnknown ?? Array.Empty<string>(),
        };
    }

    private static RuntimeCompileAudit.GeneratedAssetPlan Plan(string[]? created = null, string[]? deleted = null)
    {
        return new RuntimeCompileAudit.GeneratedAssetPlan { planned_created_paths = created ?? Array.Empty<string>(), planned_deleted_paths = deleted ?? Array.Empty<string>() };
    }

    private sealed class PreflightCase
    {
        public PreflightCase(RuntimeCompileAudit.Snapshot snapshot, RuntimeCompileAudit.GeneratedAssetPolicy policy, bool allowDirtyPrograms, bool allowDirtyScenes, bool expectedAllowed, string expectedCode, string[] expectedAssetPaths, string[] expectedPaths, string[] expectedAttributionUnknown)
        {
            this.snapshot = snapshot;
            this.policy = policy;
            allow_dirty_programs = allowDirtyPrograms;
            allow_dirty_scenes = allowDirtyScenes;
            expected_allowed = expectedAllowed;
            expected_code = expectedCode;
            expected_asset_paths = expectedAssetPaths;
            expected_paths = expectedPaths;
            expected_attribution_unknown = expectedAttributionUnknown;
        }

        public readonly RuntimeCompileAudit.Snapshot snapshot;
        public readonly RuntimeCompileAudit.GeneratedAssetPolicy policy;
        public readonly bool allow_dirty_programs;
        public readonly bool allow_dirty_scenes;
        public readonly bool expected_allowed;
        public readonly string expected_code;
        public readonly string[] expected_asset_paths;
        public readonly string[] expected_paths;
        public readonly string[] expected_attribution_unknown;
    }

    private sealed class ClassificationCase
    {
        public ClassificationCase(bool compileSucceeded, RuntimeCompileAudit.Delta delta, string expectedSeverity)
        {
            compile_succeeded = compileSucceeded;
            this.delta = delta;
            expected_severity = expectedSeverity;
        }

        public readonly bool compile_succeeded;
        public readonly RuntimeCompileAudit.Delta delta;
        public readonly string expected_severity;
    }
}
