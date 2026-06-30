using Xunit;

namespace PrefabSentinel.Tests;

public class AssetOpsPathValidationTests
{
    [Theory]
    [InlineData(null, "GENERATED_ASSET_INVALID_PATH", "asset_path_required")]
    [InlineData("", "GENERATED_ASSET_INVALID_PATH", "asset_path_required")]
    [InlineData("Assets/Foo.renderTexture\0", "GENERATED_ASSET_INVALID_PATH", "nul_byte")]
    [InlineData("/Assets/Foo.renderTexture", "GENERATED_ASSET_INVALID_PATH", "absolute_path")]
    [InlineData("Assets\\Foo.renderTexture", "GENERATED_ASSET_INVALID_PATH", "backslash")]
    [InlineData("Packages/Foo.renderTexture", "GENERATED_ASSET_INVALID_PATH", "must_start_with_assets")]
    [InlineData("Assets", "GENERATED_ASSET_INVALID_PATH", "assets_root_not_asset")]
    [InlineData("Assets/Test/", "GENERATED_ASSET_INVALID_PATH", "empty_path_segment")]
    [InlineData("Assets/./Foo.renderTexture", "GENERATED_ASSET_INVALID_PATH", "dot_segment")]
    [InlineData("Assets/Foo.renderTexture.meta", "GENERATED_ASSET_PATH_IS_META_FILE", "meta_file_path")]
    [InlineData("Assets/.renderTexture", "GENERATED_ASSET_INVALID_PATH", "asset_name_stem_required")]
    [InlineData("Assets/Foo.rendertexture", "GENERATED_ASSET_INVALID_PATH", "extension_mismatch")]
    public void Generated_Asset_Path_Rejects_With_Python_Parity_Code(
        string? assetPath,
        string code,
        string reason)
    {
        var result = AssetOpsPathValidation.ValidateGeneratedAssetPath(assetPath);

        Assert.Equal((false, code, reason), (result.IsValid, result.Code, result.Reason));
    }

    [Fact]
    public void Generated_Asset_Path_Accepts_Dot_Start_And_Multiple_Dot_Stem()
    {
        var result = AssetOpsPathValidation.ValidateGeneratedAssetPath(
            "Assets/.foo.bar.renderTexture");

        Assert.Equal(
            (true, "Assets/.foo.bar.renderTexture", ".foo.bar", ".renderTexture"),
            (result.IsValid, result.Path, result.Stem, result.Extension));
    }

    [Fact]
    public void Generated_Asset_Path_Accepts_Digit_Zero_In_Stem()
    {
        var result = AssetOpsPathValidation.ValidateGeneratedAssetPath(
            "Assets/Issue0Smoke.renderTexture");

        Assert.Equal(
            (true, "Assets/Issue0Smoke.renderTexture", "Issue0Smoke", ".renderTexture"),
            (result.IsValid, result.Path, result.Stem, result.Extension));
    }

    [Theory]
    [InlineData(
        "Assets/Foo.renderTexture",
        "Assets/Foo.mat.meta",
        "ASSET_DESTINATION_IS_META_FILE",
        "meta_file_path")]
    [InlineData(
        "Assets/Foo.mat",
        "Assets/Foo.asset",
        "ASSET_EXTENSION_MISMATCH",
        "extension_mismatch")]
    [InlineData(
        "Assets/Foo.mat",
        "Assets/Foo.mat",
        "ASSET_MOVE_SAME_PATH",
        "same_path")]
    [InlineData(
        "Assets/Foo.mat",
        "assets/foo.mat",
        "ASSET_MOVE_CASE_ONLY_RENAME_UNSUPPORTED",
        "case_only_path")]
    public void Move_Path_Rejects_With_Python_Parity_Code(
        string sourceAssetPath,
        string destinationAssetPath,
        string code,
        string reason)
    {
        var result = AssetOpsPathValidation.ValidateMoveAssetPaths(
            sourceAssetPath,
            destinationAssetPath);

        Assert.Equal((false, code, reason), (result.IsValid, result.Code, result.Reason));
    }

    [Fact]
    public void Move_Path_Accepts_Matching_Extensions_And_Reports_Stems()
    {
        var result = AssetOpsPathValidation.ValidateMoveAssetPaths(
            "Assets/Source.foo.renderTexture",
            "Assets/Destination.foo.renderTexture");

        Assert.Equal(
            (
                true,
                "Assets/Source.foo.renderTexture",
                "Assets/Destination.foo.renderTexture",
                "Source.foo",
                "Destination.foo"),
            (
                result.IsValid,
                result.SourcePath,
                result.DestinationPath,
                result.SourceStem,
                result.DestinationStem));
    }
}
