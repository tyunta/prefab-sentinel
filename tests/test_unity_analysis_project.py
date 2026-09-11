"""Exercise the local metadata CLI without Unity or redistributed assemblies."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

pytestmark = pytest.mark.source_text_invariant

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/generate_unity_analysis_project.py"


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    bridge = tmp_path / "checkout" / "tools" / "unity"
    bridge.mkdir(parents=True)
    (bridge / "PrefabSentinel.First.cs").write_text("// synthetic fixture\n")
    cache = tmp_path / "native references & metadata"
    cache.mkdir()
    response = cache / "PrefabSentinel.Editor.rsp"
    response.write_bytes(
        b'-target:library\r\n-out:"Library/PrefabSentinel.Editor.dll"\r\n'
        b'-refout:"Library/PrefabSentinel.Editor.ref.dll"\r\n'
        b'-define:UNITY_2022_3\r\n-define:UNITY_EDITOR\r\n-define:NET_UNITY_4_8\r\n'
        b'-r:"C:/Unity & SDK/mscorlib.dll"\r\n-r:"Library/UnityEditor.CoreModule.dll"\r\n'
        b'-analyzer:"C:/Unity/Unity.SourceGenerators/Unity.SourceGenerators.dll"\r\n'
        b'"Assets/Editor/PrefabSentinel/PrefabSentinel.First.cs"\r\n'
        b'-langversion:9.0\r\n/deterministic\r\n/optimize+\r\n/debug:portable\r\n'
        b'/nologo\r\n/RuntimeMetadataVersion:v4.0.30319\r\n/nowarn:0169\r\n/nowarn:0649\r\n'
        b'/utf8output\r\n/preferreduilang:en-US\r\n'
        b'/additionalfile:"Library/PrefabSentinel.Editor.UnityAdditionalFile.txt"\r\n'
    )
    references = []
    for index, (original, name) in enumerate((
        ("C:/Unity & SDK/mscorlib.dll", "mscorlib.dll"),
        ("Library/UnityEditor.CoreModule.dll", "UnityEditor.CoreModule.dll"),
    )):
        destination = cache / str(index) / name
        destination.parent.mkdir()
        destination.write_bytes(f"synthetic reference {index}".encode())
        references.append({
            "response_reference": original,
            "relative_path": destination.relative_to(cache).as_posix(),
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        })
    manifest = cache / "references.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "response_file_sha256": hashlib.sha256(response.read_bytes()).hexdigest(),
        "references": references,
    }))
    return response, manifest, cache / "0", bridge / "PrefabSentinel.Editor.Analysis.local.csproj"


def _run(inputs: tuple[Path, Path, Path, Path]) -> subprocess.CompletedProcess[str]:
    response, manifest, framework, output = inputs
    return subprocess.run([
        sys.executable, str(SCRIPT), "--response-file", str(response),
        "--reference-manifest", str(manifest), "--framework-reference-dir", str(framework),
        "--output", str(output),
    ], capture_output=True, text=True, check=False, timeout=10)


def _change_response(inputs: tuple[Path, Path, Path, Path], old: str, new: str) -> None:
    response, manifest, _, _ = inputs
    response.write_bytes(response.read_bytes().replace(old.encode(), new.encode()))
    value = json.loads(manifest.read_text())
    value["response_file_sha256"] = hashlib.sha256(response.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(value))


class UnityAnalysisProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp_path = Path(temporary.name)

    def test_generates_complete_deterministic_isolated_project(self) -> None:
        inputs = _fixture(self.tmp_path)
        first = _run(inputs)
        assert first.returncode == 0, first.stderr
        output = inputs[3]
        original = output.read_bytes()
        root = ET.fromstring(original)
        properties = {item.tag: item.text for group in root.findall("PropertyGroup") for item in group}
        expected = {
            "TargetFramework": "net48", "LangVersion": "9.0", "Nullable": "disable",
            "DefineConstants": "UNITY_2022_3;UNITY_EDITOR;NET_UNITY_4_8",
            "NoWarn": "0169;0649", "RuntimeMetadataVersion": "v4.0.30319",
            "Optimize": "true", "Deterministic": "true", "DebugType": "portable",
            "NoLogo": "true", "Utf8Output": "true", "PreferredUILang": "en-US",
            "NoStdLib": "true", "EnableDefaultItems": "false",
            "DisableImplicitFrameworkReferences": "true", "DisableImplicitFrameworkDefines": "true",
            "AutomaticallyUseReferenceAssemblyPackages": "false",
            "ImportDirectoryBuildProps": "false", "ImportDirectoryBuildTargets": "false",
            "EnableNETAnalyzers": "false", "RunAnalyzersDuringBuild": "false",
            "ResolveAssemblyReferencesFindRelatedSatellites": "false",
            "GenerateAssemblyInfo": "false", "GenerateTargetFrameworkAttribute": "false",
            "AssemblyName": "PrefabSentinel.Editor", "OutputType": "Library",
            "BaseIntermediateOutputPath": ".serena/analysis/obj/",
            "MSBuildProjectExtensionsPath": ".serena/analysis/obj/",
            "OutputPath": ".serena/analysis/bin/",
        }
        assert {key: properties.get(key) for key in expected} == expected
        assert properties["FrameworkPathOverride"] == str(inputs[2])
        assert [item.attrib for item in root.findall("ItemGroup/Compile")] == [
            {"Include": "PrefabSentinel.*.cs"},
        ]
        references = root.findall("ItemGroup/Reference")
        assert [item.attrib["Include"] for item in references] == ["mscorlib", "UnityEditor.CoreModule"]
        assert [item.findtext("Private") for item in references] == ["false", "false"]
        assert [item.findtext("HintPath") for item in references] == [
            str(inputs[1].parent / "0/mscorlib.dll"),
            str(inputs[1].parent / "1/UnityEditor.CoreModule.dll"),
        ]
        assert [item.attrib for item in root.findall("Import")] == [
            {"Project": "Sdk.props", "Sdk": "Microsoft.NET.Sdk"},
            {"Project": "Sdk.targets", "Sdk": "Microsoft.NET.Sdk"},
        ]
        assert root.findall("ItemGroup/Analyzer") == []
        assert root.findall("ItemGroup/AdditionalFiles") == []
        second = _run(inputs)
        assert second.returncode == 0, second.stderr
        assert output.read_bytes() == original
        assert "references=2" in first.stdout
        assert first.stderr == ""


    def test_preserves_changed_supported_compiler_semantics(self) -> None:
        inputs = _fixture(self.tmp_path)
        for old, new in (("-langversion:9.0", "-langversion:7.3"),
                         ("/optimize+", "/optimize-"), ("/deterministic", "/deterministic-"),
                         ("/debug:portable", "/debug:embedded")):
            _change_response(inputs, old, new)
        result = _run(inputs)
        assert result.returncode == 0, result.stderr
        root = ET.parse(inputs[3]).getroot()
        properties = {item.tag: item.text for group in root.findall("PropertyGroup") for item in group}
        assert {key: properties[key] for key in ("LangVersion", "Optimize", "Deterministic", "DebugType")} == {
            "LangVersion": "7.3", "Optimize": "false", "Deterministic": "false", "DebugType": "embedded",
        }


    def test_rejects_unsupported_compilation_inputs_before_output(self) -> None:
        for index, extra in enumerate(["/unsafe+","/nullable:enable","/checked+","/features:custom","@other.rsp","-analyzer:\"C:/SDK/Unknown.Generator.dll\"","\"Assets/Other.cs\""]):
            with self.subTest(extra=extra):
                inputs = _fixture(self.tmp_path / str(index))
                _change_response(inputs, "/nologo", "/nologo\r\n" + extra)
                result = _run(inputs)
                assert result.returncode == 1
                assert "unsupported" in result.stderr.lower()
                assert not inputs[3].exists()


    def test_rejects_unbound_references_without_replacing_existing_project(self) -> None:
        for index, failure in enumerate(["rsp_hash","dll_hash","missing","extra","duplicate","escape"]):
            with self.subTest(failure=failure):
                inputs = _fixture(self.tmp_path / str(index))
                response, manifest, _, output = inputs
                output.write_text("previous local metadata")
                value = json.loads(manifest.read_text())
                if failure == "rsp_hash":
                    response.write_bytes(response.read_bytes() + b"\n")
                elif failure == "dll_hash":
                    (manifest.parent / value["references"][1]["relative_path"]).write_bytes(b"changed")
                elif failure == "missing":
                    value["references"].pop()
                elif failure == "extra":
                    value["references"].append({**value["references"][1], "response_reference": "extra.dll"})
                elif failure == "duplicate":
                    value["references"].append(value["references"][1])
                else:
                    value["references"][1]["relative_path"] = "../outside.dll"
                manifest.write_text(json.dumps(value))
                result = _run(inputs)
                assert result.returncode == 1
                assert "configuration error:" in result.stderr
                assert output.read_text() == "previous local metadata"
                assert list(output.parent.glob("*.tmp")) == []


    def test_missing_reference_file_fails_before_writing_project(self) -> None:
        inputs = _fixture(self.tmp_path)
        (inputs[1].parent / "1/UnityEditor.CoreModule.dll").unlink()
        result = _run(inputs)
        assert result.returncode == 1
        assert "reference" in result.stderr.lower()
        assert not inputs[3].exists()


    def test_output_requires_local_analysis_filename(self) -> None:
        response, manifest, framework, output = _fixture(self.tmp_path)
        result = _run((response, manifest, framework, output.with_name("Other.csproj")))
        assert result.returncode == 1
        assert "output" in result.stderr.lower()
        assert not output.with_name("Other.csproj").exists()


    def test_msbuild_rejects_reference_removed_after_generation(self) -> None:
        if shutil.which("dotnet") is None:
            self.skipTest("dotnet is required to exercise the generated reference validation target")
        inputs = _fixture(self.tmp_path)
        result = _run(inputs)
        assert result.returncode == 0, result.stderr
        command = ["dotnet", "msbuild", str(inputs[3]), "-target:ValidateUnityAnalysisReferences", "-nologo"]
        present = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
        assert present.returncode == 0, present.stdout + present.stderr
        (inputs[1].parent / "1/UnityEditor.CoreModule.dll").unlink()
        missing = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
        assert missing.returncode == 1
        assert "A required Unity analysis reference is missing" in missing.stdout


    def test_generated_project_is_ignored_by_repository_policy(self) -> None:
        result = subprocess.run([
            "git", "check-ignore", "--no-index", "tools/unity/PrefabSentinel.Editor.Analysis.local.csproj",
        ], cwd=SCRIPT.parents[1], capture_output=True, text=True, check=False)
        assert result.returncode == 0
        assert result.stdout.strip() == "tools/unity/PrefabSentinel.Editor.Analysis.local.csproj"


    def test_malformed_supported_switch_does_not_become_valid_metadata(self) -> None:
        for index, (old, new) in enumerate([["/optimize+","/optimize++"],["-target:library","-target:elsewhere/library"],["/RuntimeMetadataVersion:v4.0.30319","/RuntimeMetadataVersion:v4.0.30319 /unsafe+"]]):
            with self.subTest(old=old, new=new):
                inputs = _fixture(self.tmp_path / str(index))
                _change_response(inputs, old, new)
                result = _run(inputs)
                assert result.returncode == 1
                assert "unsupported" in result.stderr
                assert not inputs[3].exists()


    @pytest.mark.wheel_build
    def test_distributions_keep_bridge_assets_without_local_analysis_metadata(self) -> None:
        if shutil.which("uv") is None:
            self.skipTest("uv is required to exercise the repository build backend")
        source = self.tmp_path / "source"
        source.mkdir()
        repository = SCRIPT.parents[1]
        for name in ("pyproject.toml", "README.md", ".gitignore"):
            shutil.copy2(repository / name, source / name)
        for name in ("prefab_sentinel", "tools/unity", "knowledge"):
            shutil.copytree(repository / name, source / name,
                            ignore=shutil.ignore_patterns(".serena", "__pycache__", "*.local.csproj"))
        bridge = source / "tools/unity"
        expected = {
            path.relative_to(bridge).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in bridge.rglob("*") if path.is_file() and path.suffix in {".cs", ".asmdef"}
        }
        assert expected
        for name in ("PrefabSentinel.Editor.Analysis.local.csproj", ".serena/references.json",
                     ".serena/analysis/obj/private.cache", ".serena/analysis/bin/private.dll"):
            path = bridge / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("local-machine-marker")
        output = self.tmp_path / "distributions"
        completed = subprocess.run([
            "uv", "build", "--offline", "--wheel", "--sdist", "--out-dir", str(output), str(source),
        ], capture_output=True, text=True, check=False, timeout=120)
        assert completed.returncode == 0, completed.stderr
        wheel, = output.glob("*.whl")
        sdist, = output.glob("*.tar.gz")
        prefix = "prefab_sentinel/_bridge_files/"
        with zipfile.ZipFile(wheel) as archive:
            bundled = {name.removeprefix(prefix): archive.read(name)
                       for name in archive.namelist() if name.startswith(prefix)}
        assert {name: hashlib.sha256(data).hexdigest() for name, data in bundled.items()} == expected
        with tarfile.open(sdist) as archive:
            members = [item for item in archive.getmembers() if item.isfile()]
            relative = {item.name.split("/", 1)[1]: item for item in members}
            assert not any(".serena" in Path(name).parts or name.endswith(".local.csproj") for name in relative)
            source_assets = {}
            for name, member in relative.items():
                if name.startswith("tools/unity/") and Path(name).suffix in {".cs", ".asmdef"}:
                    stream = archive.extractfile(member)
                    assert stream is not None
                    source_assets[name.removeprefix("tools/unity/")] = hashlib.sha256(stream.read()).hexdigest()
            assert source_assets == expected
        # The sdist may omit Git metadata; build its wheel through the same backend.
        rebuilt = self.tmp_path / "rebuilt"
        completed = subprocess.run([
            "uv", "build", "--offline", "--wheel", "--out-dir", str(rebuilt), str(sdist),
        ], capture_output=True, text=True, check=False, timeout=120)
        assert completed.returncode == 0, completed.stderr
        rebuilt_wheel, = rebuilt.glob("*.whl")
        with zipfile.ZipFile(rebuilt_wheel) as archive:
            assert {
                name.removeprefix(prefix): hashlib.sha256(archive.read(name)).hexdigest()
                for name in archive.namelist() if name.startswith(prefix)
            } == expected
