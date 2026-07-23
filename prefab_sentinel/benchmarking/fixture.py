from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from prefab_sentinel.benchmarking.manifest import EXPECTED_CARDINALITIES, BenchmarkManifest

YAML_HEADER = "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n"
MATCH_SCRIPT_GUID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
OTHER_SCRIPT_GUID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
SHADER_GUID = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"


@dataclass(frozen=True, slots=True)
class GeneratedFixture:
    project_root: Path
    fixture_hash: str
    cardinalities: Mapping[str, int]
    asset_files: tuple[str, ...]


TEXTURE_GUID = "ffffffffffffffffffffffffffffffff"
_BENCHMARK_TEXTURE_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010804000000b51c0c02"
    "0000000b4944415478da6364f80f00010501012718e3660000000049454e44ae426082"
)

def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _meta(guid: str) -> str:
    return f"fileFormatVersion: 2\nguid: {guid}\n"


def _game_object(file_id: int, name: str, component_ids: tuple[int, ...]) -> str:
    components = "\n".join(f"  - component: {{fileID: {value}}}" for value in component_ids)
    return f"--- !u!1 &{file_id}\nGameObject:\n  m_Component:\n{components}\n  m_Name: {name}\n"


def _transform(file_id: int, game_object_id: int, parent_id: int, children: tuple[int, ...]) -> str:
    child_block = "  m_Children: []" if not children else "  m_Children:\n" + "\n".join(
        f"  - {{fileID: {value}}}" for value in children
    )
    return (
        f"--- !u!4 &{file_id}\nTransform:\n"
        f"  m_GameObject: {{fileID: {game_object_id}}}\n"
        f"  m_Father: {{fileID: {parent_id}}}\n{child_block}\n"
        "  m_LocalPosition: {x: 0, y: 0, z: 0}\n"
        "  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}\n"
        "  m_LocalScale: {x: 1, y: 1, z: 1}\n"
    )


def _mono_behaviour(file_id: int, game_object_id: int, script_guid: str) -> str:
    return (
        f"--- !u!114 &{file_id}\nMonoBehaviour:\n"
        f"  m_GameObject: {{fileID: {game_object_id}}}\n"
        f"  m_Script: {{fileID: 11500000, guid: {script_guid}, type: 3}}\n"
        "  benchmarkReference: {fileID: 0}\n"
    )


def _prefab_instance(file_id: int, source_guid: str, parent_id: int) -> str:
    return (
        f"--- !u!1001 &{file_id}\nPrefabInstance:\n"
        "  m_Modification:\n"
        f"    m_TransformParent: {{fileID: {parent_id}}}\n"
        "    m_Modifications: []\n"
        f"  m_SourcePrefab: {{fileID: 100100000, guid: {source_guid}, type: 3}}\n"
    )


def _source_prefab(index: int) -> tuple[str, str]:
    guid = f"{0xc0 + index:032x}"
    body = YAML_HEADER + _game_object(100, f"NestedSource{index}", (200,)) + _transform(200, 100, 0, ())
    return guid, body


def _hierarchy_prefab(source_guids: tuple[str, ...]) -> str:
    local_count = EXPECTED_CARDINALITIES["game_objects"] - EXPECTED_CARDINALITIES["nested_instances"]
    blocks = [YAML_HEADER]
    child_map: dict[int, list[int]] = {index: [] for index in range(local_count)}
    for index in range(1, local_count):
        parent_index = index - 1 if index <= EXPECTED_CARDINALITIES["hierarchy_depth"] else 0
        child_map[parent_index].append(200000 + index)
    for index in range(local_count):
        game_object_id = 100000 + index
        transform_id = 200000 + index
        components = (transform_id, 300000 + index) if index < 256 else (transform_id,)
        blocks.append(_game_object(game_object_id, f"BenchmarkNode{index:03d}", components))
        parent = 0 if index == 0 else (200000 + index - 1 if index <= 8 else 200000)
        blocks.append(_transform(transform_id, game_object_id, parent, tuple(child_map[index])))
        if index < 256:
            guid = MATCH_SCRIPT_GUID if index < 128 else OTHER_SCRIPT_GUID
            blocks.append(_mono_behaviour(300000 + index, game_object_id, guid))
    for index in range(16):
        blocks.append(_prefab_instance(900000 + index, source_guids[index % 4], 200000))
    return "".join(blocks)


def _material(name: str, property_count: int) -> str:
    floats = "\n".join(f"    - _Benchmark{index:02d}: {index / 10:.1f}" for index in range(property_count))
    return (
        f"{YAML_HEADER}--- !u!21 &2100000\nMaterial:\n  m_Name: {name}\n"
        f"  m_Shader: {{fileID: 4800000, guid: {SHADER_GUID}, type: 3}}\n"
        "  m_SavedProperties:\n"
        "    m_TexEnvs:\n"
        "    - _MainTex:\n"
        f"        m_Texture: {{fileID: 2800000, guid: {TEXTURE_GUID}, type: 3}}\n"
        "        m_Scale: {x: 1, y: 1}\n"
        "        m_Offset: {x: 0, y: 0}\n"
        "    m_Floats:\n"
        f"{floats}\n    m_Colors: []\n    m_Ints: []\n"
    )


def _renderer_prefab(index: int, material_guids: tuple[str, ...]) -> str:
    references = "\n".join(
        f"  - {{fileID: 2100000, guid: {guid}, type: 2}}" for guid in material_guids
    )
    return (
        f"{YAML_HEADER}{_game_object(1000, f'RendererHost{index:02d}', (2000, 3000))}"
        f"{_transform(2000, 1000, 0, ())}"
        "--- !u!23 &3000\nMeshRenderer:\n  m_GameObject: {fileID: 1000}\n"
        f"  m_Materials:\n{references}\n"
    )


def _hash_assets(project_root: Path) -> tuple[str, tuple[str, ...]]:
    files = tuple(
        sorted(
            path.relative_to(project_root).as_posix()
            for path in project_root.rglob("*")
            if path.is_file()
        )
    )
    digest = sha256()
    for relative_path in files:
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update((project_root / relative_path).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest(), files


def generate_fixture(project_root: Path, manifest: BenchmarkManifest) -> GeneratedFixture:
    if manifest.fixture_cardinalities != EXPECTED_CARDINALITIES:
        raise ValueError("manifest fixture cardinalities must equal the fixed workload")
    assets = project_root / "Assets" / "Benchmark"
    _write(assets / "Scripts" / "BenchmarkMatch.cs", "public class BenchmarkMatch : UnityEngine.MonoBehaviour {}\n")
    _write(assets / "Scripts" / "BenchmarkMatch.cs.meta", _meta(MATCH_SCRIPT_GUID))
    _write(assets / "Scripts" / "BenchmarkOther.cs", "public class BenchmarkOther : UnityEngine.MonoBehaviour {}\n")
    _write(assets / "Scripts" / "BenchmarkOther.cs.meta", _meta(OTHER_SCRIPT_GUID))
    _write(assets / "Shaders" / "Benchmark.shader", 'Shader "Benchmark/Synthetic" { SubShader { Pass {} } }\n')
    _write(assets / "Shaders" / "Benchmark.shader.meta", _meta(SHADER_GUID))
    _write_bytes(assets / "Textures" / "BenchmarkMainTexture.png", _BENCHMARK_TEXTURE_PNG)
    _write(assets / "Textures" / "BenchmarkMainTexture.png.meta", _meta(TEXTURE_GUID))
    source_guids: list[str] = []
    for index in range(4):
        guid, body = _source_prefab(index)
        source_guids.append(guid)
        _write(assets / "Nested" / f"Source{index}.prefab", body)
        _write(assets / "Nested" / f"Source{index}.prefab.meta", _meta(guid))
    _write(assets / "InspectionTarget.prefab", _hierarchy_prefab(tuple(source_guids)))
    _write(assets / "InspectionTarget.prefab.meta", _meta("dddddddddddddddddddddddddddddddd"))
    material_guids: list[str] = []
    for index in range(256):
        guid = f"{index + 1:032x}"
        material_guids.append(guid)
        property_count = 64 if index == 0 else 1
        _write(assets / "Materials" / f"Material{index:03d}.mat", _material(f"Material{index:03d}", property_count))
        _write(assets / "Materials" / f"Material{index:03d}.mat.meta", _meta(guid))
    for index in range(64):
        offset = index * 16
        selected = tuple(material_guids[offset:offset + 16])
        _write(assets / "Renderers" / f"RendererHost{index:02d}.prefab", _renderer_prefab(index, selected))
    fixture_hash, files = _hash_assets(project_root)
    return GeneratedFixture(project_root, fixture_hash, dict(EXPECTED_CARDINALITIES), files)
