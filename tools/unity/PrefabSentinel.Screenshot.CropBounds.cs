namespace PrefabSentinel
{
    internal static class ScreenshotCropBounds
    {
        internal static bool FitsWithinFrame(
            int x,
            int y,
            int width,
            int height,
            int frameWidth,
            int frameHeight)
        {
            if (x < 0 || y < 0 || width <= 0 || height <= 0)
            {
                return false;
            }
            if (frameWidth <= 0 || frameHeight <= 0)
            {
                return false;
            }

            long right = (long)x + width;
            long bottom = (long)y + height;
            return right <= frameWidth && bottom <= frameHeight;
        }
    }
}
