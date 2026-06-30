namespace PrefabSentinel
{
    internal static class BackgroundCompileDeferralClassifier
    {
        public static bool Classify(bool? editorFocused, bool deadlineElapsed)
        {
            return deadlineElapsed && editorFocused == false;
        }
    }
}
