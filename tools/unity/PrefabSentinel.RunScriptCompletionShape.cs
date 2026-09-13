using System;

namespace PrefabSentinel
{
    internal static class RunScriptCompletionShape
    {
        internal static bool HasExactlyOneObjectData(string json)
        {
            if (json == null) return false;

            try
            {
                var reader = new JsonSyntaxReader(
                    json,
                    () => new FormatException());
                int dataCount = 0;
                reader.ReadObject(member =>
                {
                    if (string.Equals(member, "data", StringComparison.Ordinal))
                    {
                        dataCount++;
                        reader.ReadObject(_ => reader.SkipValue(depth: 0));
                    }
                    else
                    {
                        reader.SkipValue(depth: 0);
                    }
                });
                reader.RequireEnd();
                return dataCount == 1;
            }
            catch (FormatException)
            {
                return false;
            }
        }
    }
}
