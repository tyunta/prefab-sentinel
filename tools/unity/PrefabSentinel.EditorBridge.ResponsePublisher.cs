using System;

namespace PrefabSentinel
{
    internal sealed class EditorBridgeResponsePublicationException : Exception
    {
        public EditorBridgeResponsePublicationException(
            Exception atomicFailure,
            Exception fallbackFailure)
            : base(
                "Editor Bridge response publication failed.",
                new AggregateException(atomicFailure, fallbackFailure))
        {
            AtomicFailure = atomicFailure;
            FallbackFailure = fallbackFailure;
        }

        public Exception AtomicFailure { get; }

        public Exception FallbackFailure { get; }
    }

    internal static class EditorBridgeResponsePublisher
    {
        public const string FailureSuffix = ".publication-failed.json";

        public static void Publish(
            string path,
            string content,
            Action<string, string> writeAllText,
            Action<string> delete,
            Action<string, string> move,
            Action<Exception> reportAtomicFailure)
        {
            try
            {
                string tmpPath = path + ".tmp";
                writeAllText(tmpPath, content);
                delete(path);
                move(tmpPath, path);
            }
            catch (Exception atomicFailure)
            {
                reportAtomicFailure(atomicFailure);
                try
                {
                    writeAllText(path, content);
                }
                catch (Exception fallbackFailure)
                {
                    throw new EditorBridgeResponsePublicationException(
                        atomicFailure,
                        fallbackFailure);
                }
            }
        }

        public static bool TryPreserveRequest(
            string requestPath,
            string failurePath,
            Action<string, string> move,
            Action<Exception> reportPrivateFailure)
        {
            try
            {
                move(requestPath, failurePath);
                return true;
            }
            catch (Exception exception)
            {
                reportPrivateFailure(exception);
                return false;
            }
        }
    }
}
