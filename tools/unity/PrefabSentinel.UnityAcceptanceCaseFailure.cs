using System;

namespace PrefabSentinel
{
    internal sealed class UnityAcceptanceCaseFailure
    {
        private UnityAcceptanceCaseFailure(string code, string message)
        {
            Code = code;
            Message = message;
        }

        public string Code { get; }
        public string Message { get; }

        public static UnityAcceptanceCaseFailure FromException(
            string caseName,
            Exception exception)
        {
            // The exception is intentionally accepted but never retained in the
            // public result. Its detail belongs only in the local Unity Console.
            _ = exception;
            return new UnityAcceptanceCaseFailure(
                "ACCEPTANCE_CASE_EXCEPTION",
                caseName
                    + " failed unexpectedly; inspect the Unity Console for "
                    + "local exception detail.");
        }
    }
}
