using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

/// <summary>
/// Issue #19 (H-11) — exercises the prefab-apply rejection envelope assembled
/// from the three diagnostic strings the prefab apply handler extracts from a
/// rejected PatchOp.
/// </summary>
public class PrefabApplyRejectionEnvelopeTests
{
    [Fact]
    public void The_Envelope_Carries_The_Apply_Rejected_Code()
    {
        PrefabApplyRejection rejection = PrefabApplyRejectionEnvelope.Build(
            new PrefabApplyFailure("m_Priority", "AudioSource", "256"));

        Assert.Equal("SER_APPLY_REJECTED", rejection.Code);
    }

    [Fact]
    public void The_Evidence_String_Conveys_The_Property_Path_Component_Type_And_Value()
    {
        PrefabApplyRejection rejection = PrefabApplyRejectionEnvelope.Build(
            new PrefabApplyFailure("m_Priority", "AudioSource", "256"));

        Assert.Equal(
            "property_path=m_Priority; component_type=AudioSource; attempted_value=256",
            rejection.Evidence);
    }

    [Fact]
    public void Missing_Failure_Fields_Render_As_Empty_Segments_Not_Null()
    {
        // The apply handler may extract empty strings for an op with no path
        // or component; the evidence string must still be well-formed.
        PrefabApplyRejection rejection = PrefabApplyRejectionEnvelope.Build(
            new PrefabApplyFailure(null!, null!, "true"));

        Assert.Equal(
            "property_path=; component_type=; attempted_value=true",
            rejection.Evidence);
    }
}
