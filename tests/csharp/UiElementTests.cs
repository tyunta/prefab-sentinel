using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

/// <summary>
/// Issue #13 (H-5) — exercises the UI element type allow-list and the
/// font-missing message selector extracted from the UI-element handler.
/// </summary>
public class UiElementTypeAllowlistTests
{
    [Theory]
    [InlineData("Image", true)]
    [InlineData("TextMeshProUGUI", true)]
    [InlineData("Button", true)]
    [InlineData("Slider", true)]
    [InlineData("Toggle", true)]
    [InlineData("Dropdown", false)]
    [InlineData("", false)]
    public void Only_The_Supported_Type_Tokens_Are_Allowed(
        string typeToken, bool expected)
    {
        Assert.Equal(expected, UiElementTypeAllowlist.IsAllowed(typeToken));
    }
}

/// <summary>Font-missing message arm selection.</summary>
public class UiFontMissingMessageTests
{
    private const string CanonicalDefault =
        "Assets/TextMesh Pro/Resources/Fonts & Materials/LiberationSans SDF.asset";
    private const string ElementPath = "Canvas/Label";

    [Fact]
    public void An_Empty_Caller_Font_Path_Yields_The_Canonical_Default_Message()
    {
        string message = UiFontMissingMessage.ForMissingFont(
            "", CanonicalDefault, ElementPath);

        Assert.Contains("canonical default path", message);
        Assert.Contains(CanonicalDefault, message);
        Assert.Contains(ElementPath, message);
    }

    [Fact]
    public void A_Non_Empty_Caller_Font_Path_Yields_The_Caller_Supplied_Message()
    {
        const string callerPath = "Assets/Fonts/MyFont.asset";

        string message = UiFontMissingMessage.ForMissingFont(
            callerPath, CanonicalDefault, ElementPath);

        Assert.Contains("caller-supplied path", message);
        Assert.Contains(callerPath, message);
        // The caller arm must not name the canonical default path.
        Assert.DoesNotContain(CanonicalDefault, message);
    }
}
