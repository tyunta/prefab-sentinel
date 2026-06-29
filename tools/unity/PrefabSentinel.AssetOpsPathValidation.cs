using System;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;

namespace PrefabSentinel
{

internal static class AssetOpsPathValidation
{
    private const string RenderTextureExtension = ".renderTexture";

    internal static AssetOpsPathValidationResult ValidateGeneratedAssetPath(
        string? assetPath)
    {
        var result = ValidateAssetPath(
            assetPath,
            "GENERATED_ASSET_INVALID_PATH",
            "GENERATED_ASSET_PATH_IS_META_FILE",
            true,
            "asset_path");
        if (!result.IsValid)
        {
            return result;
        }
        if (result.Extension != RenderTextureExtension)
        {
            return AssetOpsPathValidationResult.Invalid(
                "GENERATED_ASSET_INVALID_PATH",
                "extension_mismatch");
        }
        return result;
    }

    internal static AssetOpsMovePathValidationResult ValidateMoveAssetPaths(
        string? sourceAssetPath,
        string? destinationAssetPath)
    {
        var source = ValidateAssetPath(
            sourceAssetPath,
            "ASSET_SOURCE_INVALID_PATH",
            "ASSET_SOURCE_IS_META_FILE",
            false,
            "source_asset_path");
        if (!source.IsValid)
        {
            return AssetOpsMovePathValidationResult.Invalid(source.Code, source.Reason);
        }
        if (IsCaseOnlyMove(sourceAssetPath, destinationAssetPath))
        {
            return AssetOpsMovePathValidationResult.Invalid(
                "ASSET_MOVE_CASE_ONLY_RENAME_UNSUPPORTED",
                "case_only_path");
        }

        var destination = ValidateAssetPath(
            destinationAssetPath,
            "ASSET_DESTINATION_INVALID_PATH",
            "ASSET_DESTINATION_IS_META_FILE",
            true,
            "destination_asset_path");
        if (!destination.IsValid)
        {
            return AssetOpsMovePathValidationResult.Invalid(
                destination.Code,
                destination.Reason);
        }
        if (source.Extension != destination.Extension)
        {
            return AssetOpsMovePathValidationResult.Invalid(
                "ASSET_EXTENSION_MISMATCH",
                "extension_mismatch");
        }
        if (source.Path == destination.Path)
        {
            return AssetOpsMovePathValidationResult.Invalid(
                "ASSET_MOVE_SAME_PATH",
                "same_path");
        }
        return AssetOpsMovePathValidationResult.Valid(
            source.Path,
            destination.Path,
            source.Stem,
            destination.Stem,
            source.Extension);
    }

    private static AssetOpsPathValidationResult ValidateAssetPath(
        string? assetPath,
        string invalidCode,
        string metaCode,
        bool requireDestinationStem,
        string field)
    {
        if (string.IsNullOrEmpty(assetPath))
        {
            return AssetOpsPathValidationResult.Invalid(
                invalidCode,
                $"{field}_required");
        }
        if (assetPath.Contains('\0', StringComparison.Ordinal))
        {
            return AssetOpsPathValidationResult.Invalid(invalidCode, "nul_byte");
        }
        if (assetPath.StartsWith("/", StringComparison.Ordinal)
            || Regex.IsMatch(assetPath, "^[A-Za-z]:"))
        {
            return AssetOpsPathValidationResult.Invalid(invalidCode, "absolute_path");
        }
        if (assetPath.Contains('\\', StringComparison.Ordinal))
        {
            return AssetOpsPathValidationResult.Invalid(invalidCode, "backslash");
        }
        if (assetPath == "Assets")
        {
            return AssetOpsPathValidationResult.Invalid(
                invalidCode,
                "assets_root_not_asset");
        }
        if (!assetPath.StartsWith("Assets/", StringComparison.Ordinal))
        {
            return AssetOpsPathValidationResult.Invalid(
                invalidCode,
                "must_start_with_assets");
        }

        var segments = assetPath.Split('/');
        if (assetPath.EndsWith("/", StringComparison.Ordinal)
            || segments.Any(segment => segment.Length == 0))
        {
            return AssetOpsPathValidationResult.Invalid(
                invalidCode,
                "empty_path_segment");
        }
        if (segments.Any(segment => segment is "." or ".."))
        {
            return AssetOpsPathValidationResult.Invalid(invalidCode, "dot_segment");
        }
        if (assetPath.EndsWith(".meta", StringComparison.Ordinal))
        {
            return AssetOpsPathValidationResult.Invalid(metaCode, "meta_file_path");
        }

        var leaf = Path.GetFileName(assetPath);
        var dotIndex = leaf.LastIndexOf(".", StringComparison.Ordinal);
        var stem = dotIndex < 0 ? leaf : leaf[..dotIndex];
        var extension = dotIndex < 0 ? string.Empty : leaf[dotIndex..];
        if (requireDestinationStem && stem.Length == 0)
        {
            return AssetOpsPathValidationResult.Invalid(
                invalidCode,
                "asset_name_stem_required");
        }
        return AssetOpsPathValidationResult.Valid(assetPath, stem, extension);
    }

    private static bool IsCaseOnlyMove(string? sourceAssetPath, string? destinationAssetPath)
    {
        if (sourceAssetPath is null || destinationAssetPath is null)
        {
            return false;
        }
        return sourceAssetPath != destinationAssetPath
            && string.Equals(
                sourceAssetPath,
                destinationAssetPath,
                StringComparison.OrdinalIgnoreCase);
    }
}

internal sealed record AssetOpsPathValidationResult(
    bool IsValid,
    string Code,
    string Reason,
    string Path,
    string Stem,
    string Extension)
{
    internal static AssetOpsPathValidationResult Invalid(string code, string reason)
    {
        return new AssetOpsPathValidationResult(
            false,
            code,
            reason,
            string.Empty,
            string.Empty,
            string.Empty);
    }

    internal static AssetOpsPathValidationResult Valid(
        string path,
        string stem,
        string extension)
    {
        return new AssetOpsPathValidationResult(
            true,
            "OK",
            string.Empty,
            path,
            stem,
            extension);
    }
}

internal sealed record AssetOpsMovePathValidationResult(
    bool IsValid,
    string Code,
    string Reason,
    string SourcePath,
    string DestinationPath,
    string SourceStem,
    string DestinationStem,
    string Extension)
{
    internal static AssetOpsMovePathValidationResult Invalid(string code, string reason)
    {
        return new AssetOpsMovePathValidationResult(
            false,
            code,
            reason,
            string.Empty,
            string.Empty,
            string.Empty,
            string.Empty,
            string.Empty);
    }

    internal static AssetOpsMovePathValidationResult Valid(
        string sourcePath,
        string destinationPath,
        string sourceStem,
        string destinationStem,
        string extension)
    {
        return new AssetOpsMovePathValidationResult(
            true,
            "OK",
            string.Empty,
            sourcePath,
            destinationPath,
            sourceStem,
            destinationStem,
            extension);
    }
}
}
