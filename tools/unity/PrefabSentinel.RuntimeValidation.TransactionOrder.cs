namespace PrefabSentinel
{
    internal static class RuntimeValidationTransactionOrder
    {
        internal sealed class Decision
        {
            internal bool compile_allowed;
            internal bool clientsim_allowed;
        }

        internal static Decision Decide(
            bool clientSimRequested,
            bool reportAndAuditValid,
            bool compileInventoryValid,
            bool clientSimPreflightValid,
            bool initialSceneDirty,
            bool allowInitialDirtyScene,
            bool compileSucceeded,
            bool sceneDirtyAfterCompile)
        {
            bool initialSceneAuthorized =
                !clientSimRequested
                || !initialSceneDirty
                || allowInitialDirtyScene;
            bool allRequiredPreflightValid =
                reportAndAuditValid
                && compileInventoryValid
                && initialSceneAuthorized
                && (!clientSimRequested || clientSimPreflightValid);

            // Post-compile dirtiness is evidence, not a second authorization gate.
            _ = sceneDirtyAfterCompile;
            return new Decision
            {
                compile_allowed = allRequiredPreflightValid,
                clientsim_allowed =
                    clientSimRequested
                    && allRequiredPreflightValid
                    && compileSucceeded,
            };
        }
    }
}
