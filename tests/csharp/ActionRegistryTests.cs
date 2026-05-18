using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

/// <summary>
/// Issue #16 (H-8) — exercises the action registry classification and the
/// relocated request DTO. The dispatcher sources its membership sets from the
/// registry; the DTO is now constructible directly in this Unity-free harness.
/// </summary>
public class ActionRegistryTests
{
    [Fact]
    public void An_Async_Action_Is_Supported_And_Reported_As_Asynchronous()
    {
        Assert.Contains("run_script", ActionRegistry.Supported);
        Assert.Contains("run_script", ActionRegistry.Async);
    }

    [Fact]
    public void A_Synchronous_Action_Is_Supported_With_The_Async_Flag_Clear()
    {
        Assert.Contains("select_object", ActionRegistry.Supported);
        Assert.DoesNotContain("select_object", ActionRegistry.Async);
    }

    [Fact]
    public void An_Unknown_Action_Is_Not_Supported()
    {
        Assert.DoesNotContain("no_such_action", ActionRegistry.Supported);
        Assert.DoesNotContain("no_such_action", ActionRegistry.Async);
    }

    [Fact]
    public void Every_Async_Action_Is_Also_A_Supported_Action()
    {
        // The async set is a subset of the supported set — the dispatcher
        // must never mark an unsupported action asynchronous.
        Assert.Subset(ActionRegistry.Supported, ActionRegistry.Async);
    }
}

/// <summary>
/// The relocated EditorControlRequest DTO is constructible in the Unity-free
/// test assembly and exposes every documented field with its declared default.
/// </summary>
public class EditorControlRequestTests
{
    [Fact]
    public void A_Default_Constructed_Request_Exposes_Its_Documented_Field_Defaults()
    {
        var request = new EditorControlRequest();

        Assert.Equal(0, request.protocol_version);
        Assert.Equal(string.Empty, request.action);
        Assert.Equal("scene", request.view);
        Assert.Equal(0, request.width);
        Assert.Equal(string.Empty, request.hierarchy_path);
        Assert.Equal(200, request.max_entries);
        Assert.Equal("all", request.log_type_filter);
        Assert.Equal(string.Empty, request.order);
        Assert.Equal(string.Empty, request.cursor);
        Assert.Equal(1, request.depth);
        Assert.Equal(-1, request.material_index);
        Assert.Equal(-1, request.camera_orthographic);
        Assert.False(request.confirm);
        Assert.False(request.assume_compiled);
        // Issue #45: the fire-and-return recompile carries a caller-supplied
        // reimport-target path array (replaces the old force_reimport bool);
        // a default-constructed request supplies none.
        Assert.Null(request.reimport_paths);
        Assert.Equal(0, request.compile_timeout);
        Assert.Equal(0f, request.timeout_sec);
        Assert.Equal("all", request.classification_filter);
        Assert.Equal("all", request.phase_filter);
        Assert.Equal("single", request.open_scene_mode);
        Assert.Equal("all", request.scope);
        Assert.Equal(-1, request.component_index);
        Assert.Equal(200, request.limit);
        Assert.True(request.save_on_close);
        Assert.False(request.cleanup_on_timeout);
    }

    [Fact]
    public void A_Default_Constructed_Request_Carries_The_Keep_Current_Camera_Sentinels()
    {
        var request = new EditorControlRequest();

        // yaw / pitch use NaN as the "keep current" sentinel; distance uses -1.
        Assert.True(float.IsNaN(request.yaw));
        Assert.True(float.IsNaN(request.pitch));
        Assert.Equal(-1f, request.distance);
    }
}
