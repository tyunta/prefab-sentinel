namespace PrefabSentinel.Camera
{
    public static class ProjectionStateStability
    {
        public static bool IsStableForPositionMode(
            bool sceneViewOrthographic,
            bool cameraOrthographic,
            float cameraFieldOfView)
        {
            if (sceneViewOrthographic != cameraOrthographic)
            {
                return false;
            }
            if (sceneViewOrthographic)
            {
                return true;
            }
            return !float.IsNaN(cameraFieldOfView)
                && !float.IsInfinity(cameraFieldOfView)
                && cameraFieldOfView > 0f;
        }
    }
}
