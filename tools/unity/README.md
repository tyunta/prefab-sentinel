# Bridge C# local analysis project

Issue #246 supplies the project metadata that Roslyn needs to resolve references
between Bridge partials. The generator uses an actual Unity 2022.3 Editor
compilation response file and a complete, explicitly prepared reference manifest.
The generated project compiles this checkout's `PrefabSentinel.*.cs` together.
It does not change Unity assets, asmdefs, deployed Bridge files, or the independent
`tests/csharp` harness. An analysis build is not the live Unity acceptance gate
described in [TESTING.md](../../TESTING.md#unity-依存-bridge-c-のコンパイル検証).

## Inputs and ownership

- Python 3.11+ and a .NET SDK compatible with the installed Serena Roslyn server.
- The real `Library/Bee/artifacts/<dag>/PrefabSentinel.Editor.rsp` from the approved
  Unity project, following a successful compilation of that Editor assembly.
- All DLLs named by that exact response file, copied explicitly to a directory
  readable by the analysis host. Do not substitute stubs or a different SDK.
- An explicit Unity .NET 4.8 framework reference directory containing
  `mscorlib.dll`, provided through `--framework-reference-dir`.

Keep the response file, manifest, references and archives outside the tracked tree
or under an ignored `.serena` directory. The generated
`tools/unity/PrefabSentinel.Editor.Analysis.local.csproj` is ignored explicitly;
its intermediate/output files stay in `tools/unity/.serena/analysis/`. None of
these machine-local paths or Unity/SDK binaries should be committed or published.
Hatch's normal file selection also excludes the generated project and `.serena`
metadata from wheels and source archives. The tooling README is retained in the
source archive alongside the generator, but omitted from the runtime wheel.
Do not replace normal selection with a directory `force-include`: forced directory
contents bypass this exclusion boundary. Artifact tests build both formats and
rebuild the wheel from the source archive while checking every Bridge file's hash.

The manifest is UTF-8 JSON with exactly this structure (the hashes below describe
the caller's own files, not repository-provided reference binaries):

```text
{
  "schema_version": 1,
  "response_file_sha256": "<SHA-256 of the exact response-file bytes>",
  "references": [
    {
      "response_reference": "<the exact unquoted -r: value>",
      "relative_path": "000/<reference filename>.dll",
      "sha256": "<SHA-256 of that DLL>"
    }
  ]
}
```

There must be exactly one entry per response-file reference. `relative_path` is
relative to the manifest directory, must be canonical, and cannot escape it.
Ambiguous assembly names, missing entries/files, extra entries, digest drift,
unknown fields and unsupported compiler switches stop generation. Validation
finishes before an existing local project is replaced. No files are downloaded,
copied, repaired, or searched for as replacements by the generator.

## Explicit Windows-to-WSL reference preparation

The observed WSL host could list Windows-mounted DLLs but failed to read some of
them with `ENOMEM`. Reading the DLLs on Windows, then extracting a Windows-created
archive onto the native Linux filesystem, worked. This is an explicit preparation
procedure for that host condition, not an automatic fallback or a claim about all
Windows mounts. Other hosts can prepare the same manifest contract directly.

Run this in PowerShell on the Windows host that owns the Unity compilation. It
copies only the references named by the selected response file, verifies every
copy, and creates a local archive. Resolve missing inputs before continuing.

```powershell
$ErrorActionPreference = 'Stop'
$analysisProject = Read-Host 'Absolute Unity project directory'
$analysisResponse = Read-Host 'Absolute PrefabSentinel.Editor.rsp path'
$analysisBundle = Join-Path ([IO.Path]::GetTempPath()) ([IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Path $analysisBundle | Out-Null
$analysisEntries = @()
foreach ($analysisLine in Get-Content -LiteralPath $analysisResponse) {
    if ($analysisLine -notmatch '^-r:"([^"]+)"$') { continue }
    $analysisReference = $Matches[1]
    $analysisSource = if ([IO.Path]::IsPathRooted($analysisReference)) {
        $analysisReference
    } else { Join-Path $analysisProject $analysisReference }
    $analysisRelative = ('{0:D3}/{1}' -f $analysisEntries.Count, [IO.Path]::GetFileName($analysisSource))
    $analysisDestination = Join-Path $analysisBundle $analysisRelative
    New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($analysisDestination)) | Out-Null
    $analysisHash = (Get-FileHash -LiteralPath $analysisSource -Algorithm SHA256).Hash.ToLowerInvariant()
    Copy-Item -LiteralPath $analysisSource -Destination $analysisDestination
    if ((Get-FileHash -LiteralPath $analysisDestination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $analysisHash) {
        throw 'Reference copy hash mismatch'
    }
    $analysisEntries += @{
        response_reference = $analysisReference
        relative_path = $analysisRelative
        sha256 = $analysisHash
    }
}
if ($analysisEntries.Count -eq 0) { throw 'No supported response references found' }
Copy-Item -LiteralPath $analysisResponse -Destination (Join-Path $analysisBundle 'PrefabSentinel.Editor.rsp')
$analysisManifest = @{
    schema_version = 1
    response_file_sha256 = (Get-FileHash -LiteralPath $analysisResponse -Algorithm SHA256).Hash.ToLowerInvariant()
    references = $analysisEntries
}
[IO.File]::WriteAllText(
    (Join-Path $analysisBundle 'references.json'),
    ($analysisManifest | ConvertTo-Json -Depth 4),
    [Text.UTF8Encoding]::new($false)
)
$analysisArchive = "$analysisBundle.tar"
tar.exe -cf $analysisArchive -C $analysisBundle .
if ($LASTEXITCODE -ne 0) { throw 'Reference archive creation failed' }
Write-Output $analysisArchive
```

In WSL, explicitly choose the archive path and a new native destination. For
example, use `mktemp -d /tmp/unity-analysis-refs.XXXXXX`, then
`tar -xf "$analysis_archive" -C "$analysis_native"` with those task-specific
variables set to the chosen paths. Keep the archive private; no automatic cleanup
or deletion is performed. The reference hashes are checked again during generation.

## Generate and verify

From the repository checkout, set the three input variables to the prepared local
paths, then run:

```bash
python scripts/generate_unity_analysis_project.py \
  --response-file "$analysis_response" \
  --reference-manifest "$analysis_manifest" \
  --framework-reference-dir "$analysis_framework" \
  --output tools/unity/PrefabSentinel.Editor.Analysis.local.csproj
dotnet restore tools/unity/PrefabSentinel.Editor.Analysis.local.csproj
dotnet build tools/unity/PrefabSentinel.Editor.Analysis.local.csproj --no-restore
git check-ignore tools/unity/PrefabSentinel.Editor.Analysis.local.csproj
```

Stop at the first nonzero exit. Generation prints observed define/reference counts.
Re-run generation before validation after changing the response file, references,
Unity/SDK version or checkout. The same inputs produce identical project bytes.
A missing reference after generation also fails the project's `ResolveReferences`
target; do not accept reference warnings or miscellaneous-project empty diagnostics
as successful analysis. Actual compilation must report zero errors. Classify any
warnings explicitly rather than hiding them with additional `NoWarn` entries.

The supported response format contains one argument per line, quoted paths, and
the observed `-define`, `-r`, `-target:library`, `-out`, `-refout`, `-langversion`,
`/deterministic[+|-]`, `/optimize[+|-]`, `/debug`, `/RuntimeMetadataVersion`,
`/nowarn`, `/nologo`, `/utf8output` and `/preferreduilang` switches. Definitions and
compiler-property values are retained; numeric language versions are read from
the response instead of assuming C# 9. The required defines establish Unity
2022.3 Editor / Unity .NET 4.8. Output paths are replaced with ignored local paths,
and response source paths are replaced with this checkout's Bridge glob.

Only the known `Unity.SourceGenerators.dll`,
`Unity.Properties.SourceGenerator.dll` and
`PrefabSentinel.Editor.UnityAdditionalFile.txt` inputs are deliberately omitted:
this project does not run Unity's generators/analyzers. Unknown analyzers, nested
response files, reference aliases, multiple arguments per line, and other semantic
switches are rejected. Extend support only after checking the new compilation
contract; do not discard an unrecognized flag merely to make generation succeed.
SDK implicit framework references/defines, default source items, generated assembly
attributes and directory build imports are disabled. Satellite-assembly discovery
is disabled because this project performs compile-only analysis.

After the build succeeds, activate/reload the exact checkout with Serena. Confirm
in its language-server log that this local analysis project loaded, with the full
Bridge source inventory, rather than a canonical miscellaneous project or the
test harness. Use semantic tools to verify:

1. `ResolveComponentType` callers in other partials.
2. SaveScene and AnimationClip handlers' `DispatchAction` caller.
3. `read_only` assignments in other partials.

Record symbol locations and current counts; the initial 112-source / 124-define /
271-reference fixture is historical evidence, not a permanent inventory limit.
If the project or references do not load, stop source work and report that failure.
Do not switch to generic source reading/editing or global MCP changes.

Generator tests use synthetic files and do not require Unity:

```bash
python -m pytest -q tests/test_unity_analysis_project.py
```

Sources: [C# response files](https://learn.microsoft.com/en-us/dotnet/csharp/language-reference/compiler-options/miscellaneous#responsefiles),
[MSBuild properties](https://learn.microsoft.com/en-us/visualstudio/msbuild/common-msbuild-project-properties?view=vs-2022),
[Serena C# configuration](https://oraios.github.io/serena/02-usage/050_configuration.html#c-roslyn-language-server).
