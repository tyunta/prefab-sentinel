"""Generate ignored Bridge analysis metadata from one verified Unity compilation.

Only Unity 2022.3's line-oriented response-file surface is supported. Reference
copying and Unity execution are explicit operator steps, never fallback paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from io import BytesIO
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

PROJECT_NAME = "PrefabSentinel.Editor.Analysis.local.csproj"
_SEMANTIC_PROPERTIES = {
    "langversion": "LangVersion",
    "debug": "DebugType",
    "runtimemetadataversion": "RuntimeMetadataVersion",
    "preferreduilang": "PreferredUILang",
}
_REQUIRED_OPTIONS = {"target", "out", "refout", "langversion", "debug", "runtimemetadataversion",
                     "optimize", "deterministic"}
_UNITY_ANALYZERS = {"Unity.SourceGenerators.dll", "Unity.Properties.SourceGenerator.dll"}


def _value(text: str) -> str:
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    elif any(character.isspace() for character in text):
        raise ValueError("unsupported multiple arguments or unquoted whitespace")
    if not text or any(character in text for character in '\"\x00\r\n'):
        raise ValueError("invalid response option value")
    return text


def _literal(text: str) -> str:
    # XML escaping alone does not prevent MSBuild property/item expansion.
    if any(character in text for character in "$@%;*?\x00\r\n"):
        raise ValueError("MSBuild expressions or wildcards are unsupported in input paths/values")
    return text


def _parse_response(raw: bytes) -> tuple[dict[str, str], list[str], list[str]]:
    properties: dict[str, str] = {}
    options: set[str] = set()
    defines: list[str] = []
    references: list[str] = []
    warnings: list[str] = []
    source_count = 0
    for raw_line in raw.decode("utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not line.startswith(("-", "/")):
            source = PurePosixPath(_value(line).replace("\\", "/"))
            if not source.name.startswith("PrefabSentinel.") or source.suffix != ".cs":
                raise ValueError("unsupported source or nested response file")
            source_count += 1
            continue
        key, separator, argument = line[1:].partition(":")
        key = key.lower()
        if key in {"define", "r", "nowarn", "analyzer", "additionalfile"}:
            if not separator:
                raise ValueError("response option requires a value")
            value = _value(argument)
            if key == "define":
                if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", value) or value in defines:
                    raise ValueError("unsupported or duplicate conditional define")
                defines.append(value)
            elif key == "r":
                if not value.lower().endswith(".dll") or "=" in value or value in references:
                    raise ValueError("unsupported or duplicate assembly reference")
                references.append(value)
            elif key == "nowarn":
                if not re.fullmatch(r"(?:CS)?\d+(?:[,;](?:CS)?\d+)*", value):
                    raise ValueError("unsupported warning identifier")
                warnings.extend(re.split("[,;]", value))
            elif key == "analyzer":
                if PurePosixPath(value.replace("\\", "/")).name not in _UNITY_ANALYZERS:
                    raise ValueError("unsupported analyzer; analysis omits only Unity's known generators")
            elif PurePosixPath(value.replace("\\", "/")).name != "PrefabSentinel.Editor.UnityAdditionalFile.txt":
                raise ValueError("unsupported additional file")
            continue
        if key in {"optimize", "optimize+", "optimize-", "deterministic", "deterministic+", "deterministic-"} and not separator:
            normalized = key.rstrip("+-")
            properties[normalized.title()] = "false" if key.endswith("-") else "true"
            key = normalized
        elif key in _SEMANTIC_PROPERTIES and separator:
            value = _literal(_value(argument))
            if key == "debug" and value not in {"portable", "embedded", "full", "pdbonly", "none"}:
                raise ValueError("unsupported debug option")
            if key == "langversion" and not re.fullmatch(r"\d+(?:\.\d+)?", value):
                raise ValueError("unsupported language version; use an explicit numeric version")
            properties[_SEMANTIC_PROPERTIES[key]] = value
        elif key in {"nologo", "utf8output"} and not separator:
            properties[{"nologo": "NoLogo", "utf8output": "Utf8Output"}[key]] = "true"
        elif key in {"target", "out", "refout"} and separator:
            expected = {"target": "library", "out": "PrefabSentinel.Editor.dll", "refout": "PrefabSentinel.Editor.ref.dll"}[key]
            actual = _value(argument) if key == "target" else PurePosixPath(_value(argument).replace("\\", "/")).name
            if actual != expected:
                raise ValueError("unsupported compilation target or assembly name")
        else:
            raise ValueError("unsupported response switch: " + key)
        if key in options:
            raise ValueError("duplicate response switch: " + key)
        options.add(key)
    if not _REQUIRED_OPTIONS.issubset(options) or not source_count or not references:
        raise ValueError("incomplete Unity compilation response")
    if not {"UNITY_EDITOR", "UNITY_2022_3", "NET_UNITY_4_8"}.issubset(defines):
        raise ValueError("unsupported compilation: Unity 2022.3 Editor with Unity .NET 4.8 references is required")
    properties["DefineConstants"] = ";".join(defines)
    properties["NoWarn"] = ";".join(warnings)
    return properties, defines, references


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _references(manifest_path: Path, raw: bytes, expected: list[str]) -> list[tuple[str, Path]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "response_file_sha256", "references"}:
        raise ValueError("invalid reference manifest fields")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise ValueError("unsupported reference manifest schema")
    if manifest["response_file_sha256"] != hashlib.sha256(raw).hexdigest():
        raise ValueError("response_file_sha256 mismatch")
    entries = manifest["references"]
    if not isinstance(entries, list):
        raise ValueError("reference manifest requires a references array")
    root = manifest_path.parent.resolve()
    mapped: dict[str, tuple[str, Path]] = {}
    names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"response_reference", "relative_path", "sha256"}:
            raise ValueError("invalid reference entry fields")
        if not all(isinstance(value, str) and value for value in entry.values()):
            raise ValueError("invalid reference entry values")
        original = entry["response_reference"]
        relative = PurePosixPath(entry["relative_path"])
        if (relative.is_absolute() or ".." in relative.parts or "\\" in entry["relative_path"]
                or relative.as_posix() != entry["relative_path"]):
            raise ValueError("reference relative_path must remain within the manifest directory")
        path = root.joinpath(*relative.parts).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("reference file is missing or escapes the manifest directory")
        _literal(str(path))
        name = PurePosixPath(original.replace("\\", "/")).stem
        _literal(name)
        if original in mapped or name in names:
            raise ValueError("duplicate reference mapping or assembly name")
        if not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"]) or _digest(path) != entry["sha256"]:
            raise ValueError("reference sha256 mismatch")
        names.add(name)
        mapped[original] = (name, path)
    if set(mapped) != set(expected):
        raise ValueError("reference manifest does not match the response reference set exactly")
    return [mapped[original] for original in expected]


def _render(properties: dict[str, str], references: list[tuple[str, Path]], framework: Path) -> bytes:
    project = ET.Element("Project")
    project.append(ET.Comment("Generated machine-local analysis metadata. Do not commit or distribute."))
    setup = ET.SubElement(project, "PropertyGroup")
    for key, value in {
        "ImportDirectoryBuildProps": "false", "ImportDirectoryBuildTargets": "false",
        "BaseIntermediateOutputPath": ".serena/analysis/obj/",
        "MSBuildProjectExtensionsPath": ".serena/analysis/obj/",
    }.items():
        ET.SubElement(setup, key).text = value
    ET.SubElement(project, "Import", Project="Sdk.props", Sdk="Microsoft.NET.Sdk")
    configuration = ET.SubElement(project, "PropertyGroup")
    for key, value in {
        "TargetFramework": "net48", "FrameworkPathOverride": str(framework),
        "AutomaticallyUseReferenceAssemblyPackages": "false", "DisableImplicitFrameworkReferences": "true",
        "DisableImplicitFrameworkDefines": "true", "NoStdLib": "true", "EnableDefaultItems": "false",
        "GenerateAssemblyInfo": "false", "GenerateTargetFrameworkAttribute": "false",
        "EnableNETAnalyzers": "false", "RunAnalyzersDuringBuild": "false", "ImplicitUsings": "disable",
        "Nullable": "disable", "AssemblyName": "PrefabSentinel.Editor", "OutputType": "Library",
        "OutputPath": ".serena/analysis/bin/",
        # Satellite discovery is unrelated to a compile-only metadata project.
        "ResolveAssemblyReferencesFindRelatedSatellites": "false",
    }.items():
        ET.SubElement(configuration, key).text = value
    items = ET.SubElement(project, "ItemGroup")
    ET.SubElement(items, "Compile", Include="PrefabSentinel.*.cs")
    for name, path in references:
        item = ET.SubElement(items, "Reference", Include=name)
        ET.SubElement(item, "HintPath").text = str(path)
        ET.SubElement(item, "Private").text = "false"
    ET.SubElement(project, "Import", Project="Sdk.targets", Sdk="Microsoft.NET.Sdk")
    # Set after SDK imports so their configuration defaults do not add defines.
    semantics = ET.SubElement(project, "PropertyGroup")
    for key, value in properties.items():
        ET.SubElement(semantics, key).text = value
    validation = ET.SubElement(project, "Target", Name="ValidateUnityAnalysisReferences", BeforeTargets="ResolveReferences")
    ET.SubElement(validation, "Error", Condition="!Exists('%(Reference.HintPath)')",
                  Text="A required Unity analysis reference is missing; regenerate local metadata.")
    ET.indent(project, space="  ")
    buffer = BytesIO()
    ET.ElementTree(project).write(buffer, encoding="utf-8", xml_declaration=True)
    return buffer.getvalue() + b"\n"


def generate(response: Path, manifest: Path, framework: Path, output: Path) -> tuple[int, int]:
    """Validate all inputs before replacing the one explicit local project."""
    if (output.name != PROJECT_NAME or output.parent.name != "unity" or output.parent.parent.name != "tools"
            or output.is_symlink() or not output.parent.is_dir()):
        raise ValueError("output must be tools/unity/" + PROJECT_NAME)
    if not any(output.parent.glob("PrefabSentinel.*.cs")):
        raise ValueError("output directory contains no Bridge sources")
    framework = framework.resolve()
    _literal(str(framework))
    if not framework.is_dir() or not (framework / "mscorlib.dll").is_file():
        raise ValueError("framework reference directory must contain mscorlib.dll")
    raw = response.read_bytes()
    properties, defines, expected = _parse_response(raw)
    references = _references(manifest, raw, expected)
    rendered = _render(properties, references, framework)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(rendered)
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return len(defines), len(references)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--response-file", type=Path, required=True)
    parser.add_argument("--reference-manifest", type=Path, required=True)
    parser.add_argument("--framework-reference-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        defines, references = generate(arguments.response_file, arguments.reference_manifest,
                                       arguments.framework_reference_dir, arguments.output)
    except (ValueError, OSError) as error:
        message = str(error) if isinstance(error, ValueError) else "required input/output is unreadable or unavailable"
        print("configuration error: " + message, file=sys.stderr)
        return 1
    print(f"Generated local Unity analysis project: defines={defines}, references={references}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
