using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

public class GameObjectPropertyAllowlistTests
{
    [Theory]
    [InlineData("m_IsActive", true)]
    [InlineData("m_Layer", true)]
    [InlineData("m_Name", true)]
    [InlineData("m_TagString", true)]
    [InlineData("m_LocalPosition", false)]
    [InlineData("", false)]
    public void Only_Allow_Listed_Property_Names_Are_Permitted(
        string propertyName, bool expected)
    {
        Assert.Equal(expected, GameObjectPropertyAllowlist.IsAllowed(propertyName));
    }
}
