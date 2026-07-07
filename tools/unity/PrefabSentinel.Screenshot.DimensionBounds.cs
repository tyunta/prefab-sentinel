namespace PrefabSentinel
{

internal static class ScreenshotDimensionBounds
{
    internal const int MinDimension = 0;
    internal const int MaxDimension = 4096;
    internal const string BridgeOutOfRangeCode =
        "EDITOR_CTRL_SCREENSHOT_DIMENSIONS_OUT_OF_RANGE";

    internal static bool Accepts(int width, int height)
    {
        return AcceptsDimension(width) && AcceptsDimension(height);
    }

    internal static string BuildMessage(int width, int height)
    {
        return $"width={width} and height={height} must each be 0 or within "
            + $"[1, {MaxDimension}] pixels.";
    }

    private static bool AcceptsDimension(int value)
    {
        return value >= MinDimension && value <= MaxDimension;
    }
}
}
