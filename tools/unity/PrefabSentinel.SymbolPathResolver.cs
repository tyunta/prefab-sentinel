// Unity-free #N-disambiguating, ambiguity-rejecting segment resolver
// (issue #38). The live stage resolver (PrefabStage.cs) and the patch v2
// selector (UnityPatchBridge.Resolve.cs) both delegate `#N` segment
// resolution here so a single resolution rule governs every scene-side
// addressing track. The class references only the base class library so
// the xUnit harness can exercise it directly; the cross-language
// conformance fixture (tests/fixtures/symbol_resolution_conformance.json)
// pins parity with the Python offline symbol tree's _resolve_segments.
//
// The result DTO carries a null Node on the ambiguous / not-found
// outcomes; ``#nullable disable`` keeps the file warning-clean under both
// the Unity assembly (nullable off) and the xUnit harness (nullable on).
#nullable disable
using System;
using System.Collections.Generic;

namespace PrefabSentinel
{
    /// <summary>
    /// Synthetic node consumed by <see cref="SymbolPathResolver"/>. The
    /// caller builds this tree from whatever live representation it owns
    /// (Unity <c>Transform</c> children for the live stage resolver, the
    /// patch-selector hierarchy match for the patch bridge); the resolver
    /// itself never touches a Unity type. <see cref="Children"/> order is
    /// the resolution-significant order — for a Unity adapter this is the
    /// <c>m_Children</c> / <c>Transform.GetChild</c> order.
    /// </summary>
    public sealed class SymbolPathNode
    {
        public SymbolPathNode(
            string id, string name, IReadOnlyList<SymbolPathNode> children)
        {
            Id = id;
            Name = name;
            Children = children ?? Array.Empty<SymbolPathNode>();
        }

        /// <summary>Opaque caller identity returned on a unique match.</summary>
        public string Id { get; }

        /// <summary>Segment name this node matches.</summary>
        public string Name { get; }

        /// <summary>Child nodes in resolution-significant order.</summary>
        public IReadOnlyList<SymbolPathNode> Children { get; }
    }

    /// <summary>Resolution outcome kind — no exceptions are thrown.</summary>
    public enum SymbolPathOutcome
    {
        /// <summary>Exactly one node matched the full segment path.</summary>
        Unique,

        /// <summary>More than one node matched; resolution stopped.</summary>
        Ambiguous,

        /// <summary>No node matched the full segment path.</summary>
        NotFound,
    }

    /// <summary>
    /// Enum-tagged resolution result. <see cref="Node"/> is non-null only
    /// when <see cref="Outcome"/> is <see cref="SymbolPathOutcome.Unique"/>;
    /// <see cref="MatchCount"/> carries the final match-list size so the
    /// caller can word an ambiguity diagnostic without re-walking.
    /// </summary>
    public sealed class SymbolPathResolution
    {
        private SymbolPathResolution(
            SymbolPathOutcome outcome, SymbolPathNode node, int matchCount)
        {
            Outcome = outcome;
            Node = node;
            MatchCount = matchCount;
        }

        public SymbolPathOutcome Outcome { get; }
        public SymbolPathNode Node { get; }
        public int MatchCount { get; }

        internal static SymbolPathResolution Unique(SymbolPathNode node)
            => new SymbolPathResolution(SymbolPathOutcome.Unique, node, 1);

        internal static SymbolPathResolution Ambiguous(int matchCount)
            => new SymbolPathResolution(
                SymbolPathOutcome.Ambiguous, null, matchCount);

        internal static SymbolPathResolution NotFound()
            => new SymbolPathResolution(SymbolPathOutcome.NotFound, null, 0);
    }

    /// <summary>
    /// Resolves a `/`-delimited segment path against a synthetic node
    /// tree. A segment is <c>name</c> or <c>name#N</c> where <c>N</c> is
    /// the 0-based occurrence among same-named siblings in child order.
    /// A non-<c>#N</c> segment matches every same-named sibling and
    /// recursion unions the results; the final match-list size decides
    /// the outcome (0 → not found, 1 → unique, &gt;1 → ambiguous). This
    /// mirrors the Python offline symbol tree's <c>_resolve_segments</c>.
    /// </summary>
    public static class SymbolPathResolver
    {
        /// <summary>
        /// Resolve <paramref name="segments"/> against
        /// <paramref name="roots"/>. The roots are treated as the
        /// sibling set the first segment matches; resolution then
        /// descends one segment per level. Ambiguity and not-found are
        /// returned as <see cref="SymbolPathResolution"/> signals, never
        /// thrown.
        /// </summary>
        public static SymbolPathResolution Resolve(
            IReadOnlyList<SymbolPathNode> roots,
            IReadOnlyList<string> segments)
        {
            if (segments == null || segments.Count == 0)
                return SymbolPathResolution.NotFound();

            var matches = new List<SymbolPathNode>();
            ResolveInto(
                roots ?? Array.Empty<SymbolPathNode>(), segments, 0, matches);

            if (matches.Count == 1)
                return SymbolPathResolution.Unique(matches[0]);
            if (matches.Count == 0)
                return SymbolPathResolution.NotFound();
            return SymbolPathResolution.Ambiguous(matches.Count);
        }

        // Depth-first union walk: at each level select the same-named
        // siblings the current segment names (one by #N index, or all by
        // bare name), then recurse on each survivor's children with the
        // next segment. Leaf segments append the matched node itself.
        private static void ResolveInto(
            IReadOnlyList<SymbolPathNode> siblings,
            IReadOnlyList<string> segments,
            int depth,
            List<SymbolPathNode> matches)
        {
            string segment = segments[depth];
            ParseSegment(segment, out string name, out int index);

            // Same-named siblings in child order — the candidate set the
            // segment selects from.
            var sameName = new List<SymbolPathNode>();
            for (int i = 0; i < siblings.Count; i++)
            {
                SymbolPathNode child = siblings[i];
                if (child != null
                    && string.Equals(child.Name, name, StringComparison.Ordinal))
                {
                    sameName.Add(child);
                }
            }

            bool isLeaf = depth == segments.Count - 1;
            if (index >= 0)
            {
                // #N selects exactly the N-th same-named sibling; an index
                // past the last occurrence contributes no match.
                if (index >= sameName.Count) return;
                AppendOrDescend(sameName[index], segments, depth, isLeaf, matches);
                return;
            }

            // Bare name matches every same-named sibling; recursion unions.
            for (int i = 0; i < sameName.Count; i++)
            {
                AppendOrDescend(sameName[i], segments, depth, isLeaf, matches);
            }
        }

        private static void AppendOrDescend(
            SymbolPathNode node,
            IReadOnlyList<string> segments,
            int depth,
            bool isLeaf,
            List<SymbolPathNode> matches)
        {
            if (isLeaf)
            {
                matches.Add(node);
                return;
            }
            ResolveInto(node.Children, segments, depth + 1, matches);
        }

        /// <summary>
        /// Split a segment into its <paramref name="name"/> and 0-based
        /// <paramref name="index"/>. A segment with no <c>#N</c> suffix —
        /// or whose suffix is not a non-negative integer — yields
        /// <paramref name="index"/> = -1 ("match all same-named
        /// siblings"). The whole segment (including a malformed
        /// <c>#</c>-suffix) is then treated as the literal name.
        /// </summary>
        public static void ParseSegment(
            string segment, out string name, out int index)
        {
            name = segment ?? string.Empty;
            index = -1;
            if (string.IsNullOrEmpty(segment)) return;

            int hash = segment.LastIndexOf('#');
            if (hash <= 0 || hash == segment.Length - 1) return;

            string suffix = segment.Substring(hash + 1);
            if (!int.TryParse(
                    suffix,
                    System.Globalization.NumberStyles.None,
                    System.Globalization.CultureInfo.InvariantCulture,
                    out int parsed)
                || parsed < 0)
            {
                return;
            }

            name = segment.Substring(0, hash);
            index = parsed;
        }
    }
}
