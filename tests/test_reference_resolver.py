from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prefab_sentinel.services.reference_resolver import ReferenceResolverService
from tests._assertion_helpers import assert_error_envelope
from tests.bridge_test_helpers import write_file

_TARGET_GUID = "1111111111111111111111111111aaaa"


def _seed_minimal_project(root: Path) -> None:
    (root / "Assets").mkdir(parents=True, exist_ok=True)


class WhereUsedFilesystemStatusErrorTests(unittest.TestCase):
    def _call_where_used_without_raw_status_exception(
        self,
        service: ReferenceResolverService,
        asset_or_guid: str,
        *,
        scope: str | None = None,
    ):
        try:
            return service.where_used(asset_or_guid, scope=scope)
        except OSError as exc:
            self.fail(
                "where_used raised raw "
                f"{type(exc).__name__} for filesystem status failure: {exc}"
            )

    def _patch_status_failure(self, failing_path: Path, exception: OSError):
        original_stat = Path.stat

        def fake_stat(path: Path, *args, **kwargs):
            if path == failing_path:
                raise exception
            return original_stat(path, *args, **kwargs)

        return patch.object(Path, "stat", fake_stat)

    def test_scope_status_permission_error_returns_ref404(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_minimal_project(root)
            service = ReferenceResolverService(project_root=root)

            with self._patch_status_failure(root / "Assets", PermissionError("denied")):
                response = self._call_where_used_without_raw_status_exception(
                    service,
                    _TARGET_GUID,
                    scope="Assets",
                )

        assert_error_envelope(
            response,
            code="REF404",
            severity="error",
            message_match="scope path status",
            data={"scope": "Assets", "read_only": True},
        )
        self.assertEqual("scope path status could not be read", response.message)

    def test_target_asset_status_os_error_returns_ref404(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_minimal_project(root)
            target = root / "Assets" / "Target.asset"
            write_file(target, "body\n")
            service = ReferenceResolverService(project_root=root)

            with self._patch_status_failure(target, OSError("stat failed")):
                response = self._call_where_used_without_raw_status_exception(
                    service,
                    "Assets/Target.asset",
                )

        assert_error_envelope(
            response,
            code="REF404",
            severity="error",
            message_match="target asset path status",
            data={"asset_or_guid": "Assets/Target.asset", "read_only": True},
        )
        self.assertEqual("target asset path status could not be read", response.message)

    def test_target_meta_status_permission_error_returns_ref001(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_minimal_project(root)
            target = root / "Assets" / "Target.asset"
            write_file(target, "body\n")
            meta_path = root / "Assets" / "Target.asset.meta"
            write_file(meta_path, f"fileFormatVersion: 2\nguid: {_TARGET_GUID}\n")
            service = ReferenceResolverService(project_root=root)

            with self._patch_status_failure(meta_path, PermissionError("denied")):
                response = self._call_where_used_without_raw_status_exception(
                    service,
                    "Assets/Target.asset",
                )

        assert_error_envelope(
            response,
            code="REF001",
            severity="error",
            message_match="target meta path status",
            data={"asset_or_guid": "Assets/Target.asset", "read_only": True},
        )
        self.assertEqual("target meta path status could not be read", response.message)

    def test_target_meta_read_os_error_returns_ref001(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_minimal_project(root)
            target = root / "Assets" / "Target.asset"
            write_file(target, "body\n")
            write_file(
                root / "Assets" / "Target.asset.meta",
                f"fileFormatVersion: 2\nguid: {_TARGET_GUID}\n",
            )
            service = ReferenceResolverService(project_root=root)

            with patch(
                "prefab_sentinel.services.reference_resolver.extract_meta_guid",
                side_effect=OSError("read failed"),
            ):
                response = self._call_where_used_without_raw_status_exception(
                    service,
                    "Assets/Target.asset",
                )

        assert_error_envelope(
            response,
            code="REF001",
            severity="error",
            message_match="target meta metadata",
            data={"asset_or_guid": "Assets/Target.asset", "read_only": True},
        )
        self.assertEqual("target meta metadata could not be read", response.message)
