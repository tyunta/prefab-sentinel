using System;
using System.Collections.Generic;

// Fuzzy suggestion ranking — Unity-free decision extracted verbatim from the
// SuggestSimilar / LevenshteinDistance helpers (issue H-4). The property-write
// handler delegates its "Did you mean" suggestion list to this class.
namespace PrefabSentinel
{
    /// <summary>
    /// Ranks candidate strings against a query word by Levenshtein edit
    /// distance, keeping only candidates within the documented
    /// distance-ratio threshold.
    /// </summary>
    internal static class SuggestionRanker
    {
        // A candidate is kept when its edit distance is within this fraction
        // of the longer of (word, candidate). Loose enough to catch typos,
        // tight enough to exclude unrelated names.
        internal const float DistanceRatioThreshold = 0.4f;

        /// <summary>
        /// Return the candidates within the distance-ratio threshold, ordered
        /// by ascending edit distance and truncated to
        /// <paramref name="maxResults"/>. An empty word or an empty candidate
        /// list yields an empty array.
        /// </summary>
        public static string[] SuggestSimilar(
            string word, IReadOnlyList<string> candidates, int maxResults = 3)
        {
            if (string.IsNullOrEmpty(word) || candidates == null || candidates.Count == 0)
            {
                return Array.Empty<string>();
            }

            var scored = new List<(string name, int dist)>();
            foreach (var candidate in candidates)
            {
                int dist = LevenshteinDistance(word, candidate);
                int maxLen = Math.Max(word.Length, candidate.Length);
                if (maxLen > 0 && dist <= maxLen * DistanceRatioThreshold)
                {
                    scored.Add((candidate, dist));
                }
            }
            scored.Sort((a, b) => a.dist.CompareTo(b.dist));

            var result = new string[Math.Min(maxResults, scored.Count)];
            for (int i = 0; i < result.Length; i++)
            {
                result[i] = scored[i].name;
            }
            return result;
        }

        /// <summary>
        /// Edit distance between two strings. A null or empty operand yields
        /// the other operand's length.
        /// </summary>
        public static int LevenshteinDistance(string a, string b)
        {
            if (string.IsNullOrEmpty(a))
            {
                return b?.Length ?? 0;
            }
            if (string.IsNullOrEmpty(b))
            {
                return a.Length;
            }

            var dp = new int[a.Length + 1, b.Length + 1];
            for (int i = 0; i <= a.Length; i++) dp[i, 0] = i;
            for (int j = 0; j <= b.Length; j++) dp[0, j] = j;

            for (int i = 1; i <= a.Length; i++)
            {
                for (int j = 1; j <= b.Length; j++)
                {
                    int cost = a[i - 1] == b[j - 1] ? 0 : 1;
                    dp[i, j] = Math.Min(
                        Math.Min(dp[i - 1, j] + 1, dp[i, j - 1] + 1),
                        dp[i - 1, j - 1] + cost);
                }
            }
            return dp[a.Length, b.Length];
        }
    }
}
