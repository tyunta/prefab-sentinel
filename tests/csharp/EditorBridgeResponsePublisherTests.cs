using System;
using System.Collections.Generic;
using System.IO;
using Xunit;

namespace PrefabSentinel.Tests;

public sealed class EditorBridgeResponsePublisherTests
{
    private const string ResponsePath = @"C:\private\bridge\request.response.json";
    private const string RequestPath = @"C:\private\bridge\request.request.json";
    private const string FailurePath = @"C:\private\bridge\request.publication-failed.json";

    [Fact]
    public void Publish_UsesDirectFallbackAfterAtomicFailure()
    {
        var writes = new List<string>();
        Exception atomicFailure = new IOException("ATOMIC_SECRET");
        Exception? reportedAtomicFailure = null;

        EditorBridgeResponsePublisher.Publish(
            ResponsePath,
            "{}",
            (path, _) =>
            {
                writes.Add(path);
                if (path.EndsWith(".tmp", StringComparison.Ordinal))
                {
                    throw atomicFailure;
                }
            },
            _ => throw new Xunit.Sdk.XunitException("delete must not run"),
            (_, _) => throw new Xunit.Sdk.XunitException("move must not run"),
            exception => reportedAtomicFailure = exception);

        Assert.Equal(
            new[] { ResponsePath + ".tmp", ResponsePath },
            writes);
        Assert.Same(atomicFailure, reportedAtomicFailure);
    }

    [Fact]
    public void Publish_ThrowsTypedFailureWhenAtomicAndFallbackWritesFail()
    {
        Exception atomicFailure = new IOException(
            $"ATOMIC_SECRET at {ResponsePath}");
        Exception fallbackFailure = new UnauthorizedAccessException(
            $"FALLBACK_SECRET at {ResponsePath}");
        Exception? reportedAtomicFailure = null;

        EditorBridgeResponsePublicationException thrown =
            Assert.Throws<EditorBridgeResponsePublicationException>(
                () => EditorBridgeResponsePublisher.Publish(
                    ResponsePath,
                    "{}",
                    (path, _) =>
                    {
                        if (path.EndsWith(".tmp", StringComparison.Ordinal))
                        {
                            throw atomicFailure;
                        }

                        throw fallbackFailure;
                    },
                    _ => { },
                    (_, _) => { },
                    exception => reportedAtomicFailure = exception));

        Assert.Equal(
            "Editor Bridge response publication failed.",
            thrown.Message);
        Assert.DoesNotContain("ATOMIC_SECRET", thrown.Message);
        Assert.DoesNotContain("FALLBACK_SECRET", thrown.Message);
        Assert.DoesNotContain(ResponsePath, thrown.Message);
        Assert.Same(atomicFailure, thrown.AtomicFailure);
        Assert.Same(fallbackFailure, thrown.FallbackFailure);
        Assert.Same(atomicFailure, reportedAtomicFailure);

        string privateText = thrown.ToString();
        Assert.Contains("ATOMIC_SECRET", privateText);
        Assert.Contains("FALLBACK_SECRET", privateText);
        Assert.Contains(ResponsePath, privateText);
    }

    [Fact]
    public void TryPreserveRequest_MovesRequestToTaggedFailureMarker()
    {
        string? movedFrom = null;
        string? movedTo = null;
        int privateReportCount = 0;

        bool preserved = EditorBridgeResponsePublisher.TryPreserveRequest(
            RequestPath,
            FailurePath,
            (source, destination) =>
            {
                movedFrom = source;
                movedTo = destination;
            },
            _ => privateReportCount++);

        Assert.True(preserved);
        Assert.Equal(RequestPath, movedFrom);
        Assert.Equal(FailurePath, movedTo);
        Assert.Equal(0, privateReportCount);
        Assert.Equal(
            ".publication-failed.json",
            EditorBridgeResponsePublisher.FailureSuffix);
    }

    [Fact]
    public void TryPreserveRequest_LeavesCleanupDisabledWhenMarkerMoveFails()
    {
        Exception markerFailure = new IOException(
            $"MARKER_SECRET at {FailurePath}");
        Exception? privateFailure = null;

        bool preserved = EditorBridgeResponsePublisher.TryPreserveRequest(
            RequestPath,
            FailurePath,
            (_, _) => throw markerFailure,
            exception => privateFailure = exception);

        Assert.False(preserved);
        Assert.Same(markerFailure, privateFailure);
    }
}
