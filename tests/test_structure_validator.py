"""Tests for prefab_sentinel.structure_validator."""

from __future__ import annotations

from prefab_sentinel.contracts import Severity
from prefab_sentinel.structure_validator import validate_structure
from tests.yaml_helpers import YAML_HEADER, make_gameobject, make_transform

# ---------------------------------------------------------------------------
# Empty / clean cases
# ---------------------------------------------------------------------------


class TestClean:
    def test_empty_text(self) -> None:
        result = validate_structure("", "test.prefab")
        assert result.max_severity == Severity.INFO
        assert result.duplicate_file_ids == []

    def test_valid_structure(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Root", ["200"])
            + make_transform("200", "100", children_file_ids=["400"])
            + make_gameobject("300", "Child", ["400"])
            + make_transform("400", "300", father_file_id="200")
        )
        result = validate_structure(text, "test.prefab")
        assert result.max_severity == Severity.INFO
        assert result.duplicate_file_ids == []
        assert result.transform_inconsistencies == []
        assert result.missing_components == []
        assert result.orphaned_transforms == []


# ---------------------------------------------------------------------------
# Duplicate fileID
# ---------------------------------------------------------------------------


class TestDuplicateFileId:
    def test_duplicate_detected(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "A", ["200"])
            + make_transform("200", "100")
            + "--- !u!4 &200\nTransform:\n  m_GameObject: {fileID: 100}\n"
            + "  m_Father: {fileID: 0}\n  m_Children: []\n"
            + "  m_LocalPosition: {x: 0, y: 0, z: 0}\n"
            + "  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}\n"
            + "  m_LocalScale: {x: 1, y: 1, z: 1}\n"
        )
        result = validate_structure(text, "test.prefab")
        assert len(result.duplicate_file_ids) == 1
        assert "200" in result.duplicate_file_ids[0].detail
        assert result.max_severity == Severity.ERROR


# ---------------------------------------------------------------------------
# Transform bidirectional consistency
# ---------------------------------------------------------------------------


class TestTransformConsistency:
    def test_father_missing_child_in_list(self) -> None:
        # Child says father=200, but father's m_Children doesn't list child
        text = (
            YAML_HEADER
            + make_gameobject("100", "Parent", ["200"])
            + make_transform("200", "100", children_file_ids=[])  # missing child 400
            + make_gameobject("300", "Child", ["400"])
            + make_transform("400", "300", father_file_id="200")
        )
        result = validate_structure(text, "test.prefab")
        assert len(result.transform_inconsistencies) >= 1
        assert any("m_Children does not include" in d.detail for d in result.transform_inconsistencies)
        assert result.max_severity == Severity.ERROR

    def test_child_wrong_father(self) -> None:
        # Father lists child 400, but child says father=999 (non-existent)
        text = (
            YAML_HEADER
            + make_gameobject("100", "Parent", ["200"])
            + make_transform("200", "100", children_file_ids=["400"])
            + make_gameobject("300", "Child", ["400"])
            + make_transform("400", "300", father_file_id="999")
        )
        result = validate_structure(text, "test.prefab")
        assert len(result.transform_inconsistencies) >= 1
        assert result.max_severity == Severity.ERROR

    def test_father_references_nonexistent_transform(self) -> None:
        # Transform says father=999 which doesn't exist
        text = (
            YAML_HEADER
            + make_gameobject("100", "Obj", ["200"])
            + make_transform("200", "100", father_file_id="999")
        )
        result = validate_structure(text, "test.prefab")
        assert len(result.transform_inconsistencies) >= 1
        assert any("non-existent transform" in d.detail for d in result.transform_inconsistencies)

    def test_children_references_nonexistent_transform(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Obj", ["200"])
            + make_transform("200", "100", children_file_ids=["999"])
        )
        result = validate_structure(text, "test.prefab")
        assert len(result.transform_inconsistencies) >= 1
        assert any("non-existent transform" in d.detail for d in result.transform_inconsistencies)


# ---------------------------------------------------------------------------
# Missing component references
# ---------------------------------------------------------------------------


class TestMissingComponents:
    def test_missing_component(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Obj", ["200", "999"])
            + make_transform("200", "100")
        )
        result = validate_structure(text, "test.prefab")
        assert len(result.missing_components) == 1
        assert "999" in result.missing_components[0].detail
        assert result.max_severity == Severity.ERROR

    def test_all_components_present(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Obj", ["200"])
            + make_transform("200", "100")
        )
        result = validate_structure(text, "test.prefab")
        assert result.missing_components == []


# ---------------------------------------------------------------------------
# Orphaned transforms
# ---------------------------------------------------------------------------


class TestOrphanedTransforms:
    def test_orphaned_transform(self) -> None:
        # Transform with father=888 that doesn't exist — not root, not valid parent
        text = (
            YAML_HEADER
            + make_gameobject("100", "Root", ["200"])
            + make_transform("200", "100")
            + make_gameobject("300", "Orphan", ["400"])
            + make_transform("400", "300", father_file_id="888")
        )
        result = validate_structure(text, "test.prefab")
        assert len(result.orphaned_transforms) == 1
        assert "888" in result.orphaned_transforms[0].detail
        assert result.max_severity in (Severity.WARNING, Severity.ERROR)

    def test_root_is_not_orphaned(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Root", ["200"])
            + make_transform("200", "100", father_file_id="0")
        )
        result = validate_structure(text, "test.prefab")
        assert result.orphaned_transforms == []

    def test_empty_father_is_root(self) -> None:
        # father="0" should be treated as root
        text = (
            YAML_HEADER
            + make_gameobject("100", "Root", ["200"])
            + "--- !u!4 &200\nTransform:\n  m_GameObject: {fileID: 100}\n"
            + "  m_Father: {fileID: 0}\n  m_Children: []\n"
            + "  m_LocalPosition: {x: 0, y: 0, z: 0}\n"
            + "  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}\n"
            + "  m_LocalScale: {x: 1, y: 1, z: 1}\n"
        )
        result = validate_structure(text, "test.prefab")
        assert result.orphaned_transforms == []


# ---------------------------------------------------------------------------
# Combined severity
# ---------------------------------------------------------------------------


class TestSeverity:
    def test_multiple_issues_highest_wins(self) -> None:
        # Duplicate (ERROR) + orphan (WARNING) → ERROR
        text = (
            YAML_HEADER
            + make_gameobject("100", "A", ["200"])
            + make_transform("200", "100")
            + "--- !u!4 &200\nTransform:\n  m_GameObject: {fileID: 100}\n"
            + "  m_Father: {fileID: 0}\n  m_Children: []\n"
            + "  m_LocalPosition: {x: 0, y: 0, z: 0}\n"
            + "  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}\n"
            + "  m_LocalScale: {x: 1, y: 1, z: 1}\n"
            + make_gameobject("300", "Orphan", ["400"])
            + make_transform("400", "300", father_file_id="888")
        )
        result = validate_structure(text, "test.prefab")
        assert result.max_severity == Severity.ERROR
