using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

/// <summary>
/// Issue #38 (T-38-c2 / T-38-3 / T-38-4) — exercises the Unity-free
/// <see cref="SymbolPathResolver"/> against the cross-language
/// conformance fixture <c>tests/fixtures/symbol_resolution_conformance.json</c>.
/// The same fixture is run Python-side by
/// <c>tests/test_symbol_resolution_conformance.py</c>; running it on both
/// sides pins parity between the C# resolver and the Python offline
/// symbol tree's <c>_resolve_segments</c>.
/// </summary>
public class SymbolPathResolverTests
{
    // The conformance fixture is repo-relative; the test assembly runs out
    // of tests/csharp/bin/<config>/<tfm>/, so walk parent directories until
    // the fixture file is found.
    private static string LocateFixture()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir != null)
        {
            string candidate = Path.Combine(
                dir.FullName, "tests", "fixtures",
                "symbol_resolution_conformance.json");
            if (File.Exists(candidate)) return candidate;
            dir = dir.Parent;
        }
        throw new FileNotFoundException(
            "symbol_resolution_conformance.json not found in any ancestor "
            + "of " + AppContext.BaseDirectory);
    }

    private static IReadOnlyList<SymbolPathNode> BuildNodes(JsonElement array)
    {
        var nodes = new List<SymbolPathNode>();
        foreach (JsonElement element in array.EnumerateArray())
        {
            string id = element.GetProperty("id").GetString() ?? string.Empty;
            string name = element.GetProperty("name").GetString() ?? string.Empty;
            IReadOnlyList<SymbolPathNode> children =
                element.TryGetProperty("children", out JsonElement childArray)
                    ? BuildNodes(childArray)
                    : Array.Empty<SymbolPathNode>();
            nodes.Add(new SymbolPathNode(id, name, children));
        }
        return nodes;
    }

    public static IEnumerable<object[]> ConformanceCases()
    {
        using JsonDocument doc = JsonDocument.Parse(
            File.ReadAllText(LocateFixture()));
        JsonElement root = doc.RootElement;
        IReadOnlyList<SymbolPathNode> roots = BuildNodes(
            root.GetProperty("roots"));

        foreach (JsonElement caseElement in
                 root.GetProperty("cases").EnumerateArray())
        {
            string name = caseElement.GetProperty("name").GetString() ?? "";
            string path = caseElement.GetProperty("path").GetString() ?? "";
            string outcome =
                caseElement.GetProperty("outcome").GetString() ?? "";
            string expectedId =
                caseElement.TryGetProperty("expected_id", out JsonElement idEl)
                    ? idEl.GetString() ?? string.Empty
                    : string.Empty;
            // Each MemberData row carries the prebuilt roots so the resolver
            // sees the same node tree the Python suite builds.
            yield return new object[]
                { name, path, outcome, expectedId, roots };
        }
    }

    [Theory]
    [MemberData(nameof(ConformanceCases))]
    public void Resolve_Conforms_To_Cross_Language_Fixture(
        string caseName,
        string path,
        string expectedOutcome,
        string expectedId,
        IReadOnlyList<SymbolPathNode> roots)
    {
        string[] segments = path.Split('/');
        SymbolPathResolution result = SymbolPathResolver.Resolve(
            roots, segments);

        switch (expectedOutcome)
        {
            case "unique":
                Assert.Equal(SymbolPathOutcome.Unique, result.Outcome);
                Assert.NotNull(result.Node);
                Assert.Equal(expectedId, result.Node!.Id);
                break;
            case "ambiguous":
                Assert.Equal(SymbolPathOutcome.Ambiguous, result.Outcome);
                Assert.Null(result.Node);
                Assert.True(
                    result.MatchCount > 1,
                    $"case '{caseName}': ambiguous outcome must carry a "
                    + $"match count > 1, got {result.MatchCount}");
                break;
            case "not_found":
                Assert.Equal(SymbolPathOutcome.NotFound, result.Outcome);
                Assert.Null(result.Node);
                break;
            default:
                throw new Xunit.Sdk.XunitException(
                    $"case '{caseName}': unknown expected outcome "
                    + $"'{expectedOutcome}'");
        }
    }

    // T-38-4: the #N selector picks the N-th same-named sibling in child
    // order — a direct boundary check independent of the fixture so an
    // off-by-one in ParseSegment or the index pick is caught here too.
    [Fact]
    public void Hash_Index_Selects_The_Nth_Same_Named_Sibling_In_Child_Order()
    {
        var roots = new[]
        {
            new SymbolPathNode("first", "Mesh", Array.Empty<SymbolPathNode>()),
            new SymbolPathNode("second", "Mesh", Array.Empty<SymbolPathNode>()),
            new SymbolPathNode("third", "Mesh", Array.Empty<SymbolPathNode>()),
        };

        Assert.Equal(
            "first",
            SymbolPathResolver.Resolve(roots, new[] { "Mesh#0" }).Node!.Id);
        Assert.Equal(
            "second",
            SymbolPathResolver.Resolve(roots, new[] { "Mesh#1" }).Node!.Id);
        Assert.Equal(
            "third",
            SymbolPathResolver.Resolve(roots, new[] { "Mesh#2" }).Node!.Id);
    }

    // T-38-3: a bare segment matching same-named siblings is rejected as
    // ambiguous, never first-picked.
    [Fact]
    public void Bare_Segment_With_Same_Named_Siblings_Is_Ambiguous()
    {
        var roots = new[]
        {
            new SymbolPathNode("a", "Mesh", Array.Empty<SymbolPathNode>()),
            new SymbolPathNode("b", "Mesh", Array.Empty<SymbolPathNode>()),
        };

        SymbolPathResolution result =
            SymbolPathResolver.Resolve(roots, new[] { "Mesh" });

        Assert.Equal(SymbolPathOutcome.Ambiguous, result.Outcome);
        Assert.Null(result.Node);
        Assert.Equal(2, result.MatchCount);
    }
}
