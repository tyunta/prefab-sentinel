"""Tests for issue #229 — validate-refs refresh flag and stale-cache hint.

The orchestrator entry point exposes a ``refresh_guid_index`` flag that
invalidates the resolver's GUID index cache before scanning. On the
failure path (any missing-asset GUIDs reported and the refresh flag not
set), the orchestrator runs a one-shot fresh meta-file scan and emits a
``STALE_GUID_INDEX_HINT`` warning diagnostic when the intersection of
that fresh scan with the resolver's unique-missing-GUID list is
non-empty.

The hint never fires when the refresh flag is set, when no missing
assets were reported, or when the fresh scan also fails to resolve
anything (true missing assets).
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prefab_sentinel.contracts import Severity
from prefab_sentinel.orchestrator_validation import (
    STALE_GUID_INDEX_HINT_DETAIL,
    validate_refs,
)
from prefab_sentinel.services import reference_resolver as _reference_resolver
from prefab_sentinel.services.reference_resolver import ReferenceResolverService
from tests.bridge_test_helpers import write_file

_COLLECT_GUID_INDEX_TARGET = (
    "prefab_sentinel.services.reference_resolver.collect_project_guid_index"
)


@contextlib.contextmanager
def _spy_collect_guid_index():
    """Patch ``collect_project_guid_index`` with a ``wraps=`` spy so
    the real disk-walk runs while ``call_count`` is observable.

    This is the boundary between the resolver service and the
    filesystem walker; centralizing the patch target prevents drift
    across the seven TTL test rows that share the same setup.
    """
    with patch(
        _COLLECT_GUID_INDEX_TARGET,
        wraps=_reference_resolver.collect_project_guid_index,
    ) as spy:
        yield spy

BASE_GUID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SECOND_GUID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
THIRD_GUID = "cccccccccccccccccccccccccccccccc"
VARIANT_GUID = "dddddddddddddddddddddddddddddddd"
TRULY_MISSING_GUID = "ffffffffffffffffffffffffffffffff"


def _write_base_meta(root: Path) -> None:
    """Single Base.prefab + .meta with BASE_GUID."""
    write_file(
        root / "Assets" / "Base.prefab",
        """%YAML 1.1
--- !u!1 &100100000
GameObject:
  m_Name: Base
""",
    )
    write_file(
        root / "Assets" / "Base.prefab.meta",
        f"""fileFormatVersion: 2
guid: {BASE_GUID}
""",
    )


def _write_variant_referring_to(root: Path, guid: str) -> None:
    """Variant prefab whose modifications reference *guid*."""
    write_file(
        root / "Assets" / "Variant.prefab",
        f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
  m_Modification:
    m_Modifications:
    - target: {{fileID: 100100000, guid: {guid}, type: 3}}
      propertyPath: missing.ref
      value: 0
      objectReference: {{fileID: 0}}
""",
    )
    write_file(
        root / "Assets" / "Variant.prefab.meta",
        f"""fileFormatVersion: 2
guid: {VARIANT_GUID}
""",
    )


def _write_three_missing_variants(root: Path) -> None:
    """Three variant prefabs each referring to a distinct missing GUID."""
    for index, guid in enumerate((TRULY_MISSING_GUID, SECOND_GUID, THIRD_GUID)):
        write_file(
            root / "Assets" / f"V{index}.prefab",
            f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
  m_Modification:
    m_Modifications:
    - target: {{fileID: 100100000, guid: {guid}, type: 3}}
      propertyPath: missing.ref
      value: 0
      objectReference: {{fileID: 0}}
""",
        )
        write_file(
            root / "Assets" / f"V{index}.prefab.meta",
            f"""fileFormatVersion: 2
guid: {VARIANT_GUID[:-1]}{index}
""",
        )


def _drop_meta_for_guid(resolver: ReferenceResolverService, guid: str) -> Path:
    """Pretend *guid* is missing on disk by hiding its .meta file.

    Returns the renamed path so the caller can restore it later.
    """
    asset_path = resolver._guid_map().get(guid)  # noqa: SLF001
    if asset_path is None:
        raise AssertionError(f"GUID {guid} not in index — fixture broken")
    meta_path = asset_path.with_suffix(asset_path.suffix + ".meta")
    if not meta_path.exists():
        raise AssertionError(f"meta file missing for {asset_path}")
    hidden_path = meta_path.with_suffix(meta_path.suffix + ".hidden")
    meta_path.rename(hidden_path)
    return hidden_path


class RefreshFlagInvalidationTests(unittest.TestCase):
    """The ``refresh_guid_index`` flag drives a single cache invalidation
    call before the resolver scans, and the default value leaves the
    cache untouched.
    """

    def test_refresh_flag_invalidates_cache_before_scan(self) -> None:
        """Issue #229 — a caller that asserts the cache is stale forces a
        fresh GUID lookup. Spy on ``invalidate_guid_index`` to confirm
        the orchestrator invokes it exactly once before the scan.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_base_meta(root)
            resolver = ReferenceResolverService(project_root=root)
            with patch.object(
                resolver,
                "invalidate_guid_index",
                wraps=resolver.invalidate_guid_index,
            ) as spy:
                validate_refs(resolver, scope="Assets", refresh_guid_index=True)
        self.assertEqual(1, spy.call_count)

    def test_refresh_flag_default_does_not_invalidate(self) -> None:
        """Issue #229 — the flag defaults to ``False`` so existing fast
        paths preserve their cached GUID index across calls.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_base_meta(root)
            resolver = ReferenceResolverService(project_root=root)
            with patch.object(
                resolver,
                "invalidate_guid_index",
                wraps=resolver.invalidate_guid_index,
            ) as spy:
                validate_refs(resolver, scope="Assets")
        self.assertEqual(0, spy.call_count)


class ResolverScanShapeTests(unittest.TestCase):
    """The resolver scan exposes the full sorted unique-missing-GUID set
    in its response data so the orchestrator's hint detector can
    intersect it against a fresh meta-file scan.
    """

    def test_scan_exposes_full_sorted_unique_missing_guid_set(self) -> None:
        """Issue #229 — three distinct missing GUIDs round-trip as a
        sorted list under ``data.unique_missing_asset_guids`` regardless
        of the existing ``top_missing_asset_guids`` (which is capped by
        ``top_guid_limit``).
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_base_meta(root)
            _write_three_missing_variants(root)
            resolver = ReferenceResolverService(project_root=root)
            scan = resolver.scan_broken_references(scope="Assets")
        observed = scan.data["unique_missing_asset_guids"]
        expected = sorted({TRULY_MISSING_GUID, SECOND_GUID, THIRD_GUID})
        self.assertEqual(expected, observed)


class StaleCacheHintTests(unittest.TestCase):
    """The stale-cache hint fires only on the documented failure path
    when at least one missing GUID would resolve on a fresh meta-file
    scan and the caller did not already pass ``refresh_guid_index``.
    """

    def test_hint_fires_when_missing_guid_resolves_on_fresh_scan(self) -> None:
        """Issue #229 — the cached resolver reports the GUID missing,
        but the meta file actually exists on disk; the orchestrator
        emits the hint diagnostic with severity warning and an evidence
        message that names the refresh flag the caller should set.

        We seed the resolver with one Base.prefab and a Variant whose
        modification references a *second* GUID that has no meta on
        disk yet. We then write the second meta file and seed the
        cache so the resolver still considers the GUID missing,
        emulating the post-asset-creation lag described in #229.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_base_meta(root)
            _write_variant_referring_to(root, SECOND_GUID)
            resolver = ReferenceResolverService(project_root=root)
            # Warm the GUID index cache so it does not see the second
            # asset that we are about to add.
            resolver._guid_map()  # noqa: SLF001
            # Now create the second asset so the fresh scan can resolve
            # the previously-missing GUID.
            write_file(
                root / "Assets" / "Second.asset",
                "%YAML 1.1\n",
            )
            write_file(
                root / "Assets" / "Second.asset.meta",
                f"""fileFormatVersion: 2
guid: {SECOND_GUID}
""",
            )
            response = validate_refs(resolver, scope="Assets")
        hint_diagnostics = [
            d for d in response.diagnostics
            if d.detail == STALE_GUID_INDEX_HINT_DETAIL
        ]
        self.assertEqual(1, len(hint_diagnostics))
        hint = hint_diagnostics[0]
        # The hint only fires on the failure path; pin response.severity
        # as ``error`` so the contract "hint accompanies REF001 failure"
        # cannot regress to "hint fires on any response".
        self.assertEqual(Severity.ERROR, response.severity)
        # Evidence names the refresh flag and the count of stale-resolved
        # GUIDs so the caller can act without consulting external docs.
        self.assertIn("refresh_guid_index", hint.evidence)
        self.assertIn("1", hint.evidence)

    def test_hint_suppressed_when_refresh_flag_already_set(self) -> None:
        """Issue #229 — once the caller forced a refresh, repeating the
        hint would be redundant. The diagnostic must not appear.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_base_meta(root)
            _write_variant_referring_to(root, SECOND_GUID)
            resolver = ReferenceResolverService(project_root=root)
            resolver._guid_map()  # noqa: SLF001
            write_file(
                root / "Assets" / "Second.asset",
                "%YAML 1.1\n",
            )
            write_file(
                root / "Assets" / "Second.asset.meta",
                f"""fileFormatVersion: 2
guid: {SECOND_GUID}
""",
            )
            response = validate_refs(
                resolver, scope="Assets", refresh_guid_index=True,
            )
        details = [d.detail for d in response.diagnostics]
        self.assertNotIn(STALE_GUID_INDEX_HINT_DETAIL, details)

    def test_hint_suppressed_when_no_missing_assets(self) -> None:
        """Issue #229 — the hint only applies on the failure path. On a
        clean scope with zero missing assets the diagnostic is absent.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_base_meta(root)
            resolver = ReferenceResolverService(project_root=root)
            response = validate_refs(resolver, scope="Assets")
        details = [d.detail for d in response.diagnostics]
        self.assertNotIn(STALE_GUID_INDEX_HINT_DETAIL, details)

    def test_hint_suppressed_when_fresh_scan_also_fails(self) -> None:
        """Issue #229 — a truly missing asset must not be mislabelled as
        cache staleness. When the meta file does not exist on disk
        either, the fresh scan finds nothing and the hint stays silent.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_base_meta(root)
            _write_variant_referring_to(root, TRULY_MISSING_GUID)
            resolver = ReferenceResolverService(project_root=root)
            response = validate_refs(resolver, scope="Assets")
        details = [d.detail for d in response.diagnostics]
        self.assertNotIn(STALE_GUID_INDEX_HINT_DETAIL, details)


class FreshDiskGuidIndexTTLTests(unittest.TestCase):
    """Issue #230 — ``ReferenceResolverService.fresh_disk_guid_index``
    memoizes the disk scan inside a monotonic-clock freshness window
    and rebuilds when the window elapses or the cache is invalidated.

    Tests pin the boundary behavior with an injected monotonic clock
    so the off-by-one risk between ``<`` and ``<=`` is observable.
    The spied function is ``collect_project_guid_index`` at the
    resolver's import site — the boundary between the resolver service
    and the filesystem walker.
    """

    @staticmethod
    def _seed_meta(root: Path, guid: str) -> Path:
        """Seed one prefab + meta file pair returning the prefab path."""
        prefab_path = root / "Assets" / "Seed.prefab"
        write_file(prefab_path, "%YAML 1.1\n")
        write_file(
            root / "Assets" / "Seed.prefab.meta",
            f"fileFormatVersion: 2\nguid: {guid}\n",
        )
        return prefab_path

    def test_first_call_walks_disk_and_returns_seeded_guid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._seed_meta(root, BASE_GUID)
            resolver = ReferenceResolverService(project_root=root)
            with _spy_collect_guid_index() as spy:
                index = resolver.fresh_disk_guid_index(
                    ttl_seconds=30.0, now_monotonic=100.0,
                )
        self.assertEqual(
            1, spy.call_count,
            msg=(
                "First call must invoke the disk-walk exactly once "
                "(no pre-existing memoized state)."
            ),
        )
        # Returned mapping must carry the seeded GUID; pin the value
        # rather than membership so a regression that swaps key/value
        # is caught.
        self.assertIn(
            BASE_GUID, index,
            msg=(
                "fresh_disk_guid_index result must contain the seeded "
                "GUID written to disk."
            ),
        )
        self.assertEqual(
            "Seed.prefab", index[BASE_GUID].name,
            msg=(
                "fresh_disk_guid_index must map the seeded GUID to "
                "the prefab path on disk."
            ),
        )

    def test_second_call_within_window_is_memoized(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._seed_meta(root, BASE_GUID)
            resolver = ReferenceResolverService(project_root=root)
            with _spy_collect_guid_index() as spy:
                first = resolver.fresh_disk_guid_index(
                    ttl_seconds=30.0, now_monotonic=100.0,
                )
                second = resolver.fresh_disk_guid_index(
                    ttl_seconds=30.0, now_monotonic=110.0,
                )
        self.assertEqual(
            1, spy.call_count,
            msg=(
                "Second call within the freshness window must NOT "
                "re-walk disk; only one underlying scan is expected."
            ),
        )
        # Identity check — the memoized branch must return the same
        # mapping object, not a freshly built copy that happens to be
        # equal.
        self.assertIs(
            first, second,
            msg=(
                "Memoized branch must return the same mapping object, "
                "not an equal-but-distinct rebuild."
            ),
        )

    def test_boundary_at_window_edge_triggers_rebuild(self) -> None:
        """A call placed exactly at ``built_at + ttl_seconds`` must
        rebuild.  The window is interpreted as ``now < built_at +
        ttl_seconds`` (strict <), so the boundary value falls into
        the rebuild branch.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._seed_meta(root, BASE_GUID)
            resolver = ReferenceResolverService(project_root=root)
            with _spy_collect_guid_index() as spy:
                resolver.fresh_disk_guid_index(
                    ttl_seconds=30.0, now_monotonic=100.0,
                )
                resolver.fresh_disk_guid_index(
                    ttl_seconds=30.0, now_monotonic=130.0,
                )
        self.assertEqual(
            2, spy.call_count,
            msg=(
                "A call at exactly built_at + ttl_seconds must trigger "
                "a rebuild (strict-< boundary; off-by-one guard, "
                "issue #230)."
            ),
        )

    def test_boundary_just_before_window_edge_is_still_a_hit(self) -> None:
        """A call placed just before ``built_at + ttl_seconds`` reuses
        the memoized result (boundary-minus-epsilon).
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._seed_meta(root, BASE_GUID)
            resolver = ReferenceResolverService(project_root=root)
            with _spy_collect_guid_index() as spy:
                resolver.fresh_disk_guid_index(
                    ttl_seconds=30.0, now_monotonic=100.0,
                )
                resolver.fresh_disk_guid_index(
                    ttl_seconds=30.0, now_monotonic=129.999,
                )
        self.assertEqual(
            1, spy.call_count,
            msg=(
                "A call at built_at + ttl_seconds - epsilon must still "
                "hit the memoized window (strict-< boundary)."
            ),
        )

    def test_zero_window_forces_rebuild_every_call(self) -> None:
        """``ttl_seconds=0`` collapses the freshness window so each
        call rebuilds.  ``now < built_at + 0`` is always false for a
        monotonic clock that does not move backward.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._seed_meta(root, BASE_GUID)
            resolver = ReferenceResolverService(project_root=root)
            with _spy_collect_guid_index() as spy:
                resolver.fresh_disk_guid_index(
                    ttl_seconds=0.0, now_monotonic=100.0,
                )
                resolver.fresh_disk_guid_index(
                    ttl_seconds=0.0, now_monotonic=100.5,
                )
        self.assertEqual(
            2, spy.call_count,
            msg=(
                "ttl_seconds=0 collapses the freshness window so every "
                "call must rebuild (now < built_at + 0 is always false)."
            ),
        )

    def test_invalidate_drops_fresh_disk_cache(self) -> None:
        """``invalidate_guid_index`` clears both the primary cache and
        the fresh-disk scan cache so a forced refresh affects both
        lookups (issue #230).
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._seed_meta(root, BASE_GUID)
            resolver = ReferenceResolverService(project_root=root)
            with _spy_collect_guid_index() as spy:
                resolver.fresh_disk_guid_index(
                    ttl_seconds=30.0, now_monotonic=100.0,
                )
                resolver.invalidate_guid_index()
                resolver.fresh_disk_guid_index(
                    ttl_seconds=30.0, now_monotonic=110.0,
                )
        self.assertEqual(
            2, spy.call_count,
            msg=(
                "invalidate_guid_index must drop the fresh-disk cache "
                "so the next call within the window re-walks disk."
            ),
        )

    def test_filesystem_error_propagates_and_does_not_poison_cache(
        self,
    ) -> None:
        """A filesystem error raised by the underlying scan surfaces
        verbatim to the caller; a subsequent call within the
        freshness window still triggers a rebuild rather than
        returning a cached failure.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._seed_meta(root, BASE_GUID)
            resolver = ReferenceResolverService(project_root=root)
            real_fn = _reference_resolver.collect_project_guid_index
            call_count = {"n": 0}

            def raise_once_then_real(*args, **kwargs):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    raise OSError("simulated scan failure")
                return real_fn(*args, **kwargs)

            with patch(
                _COLLECT_GUID_INDEX_TARGET, side_effect=raise_once_then_real,
            ) as spy:
                with self.assertRaises(OSError) as ctx:
                    resolver.fresh_disk_guid_index(
                        ttl_seconds=30.0, now_monotonic=100.0,
                    )
                self.assertEqual(
                    "simulated scan failure", str(ctx.exception),
                    msg=(
                        "Filesystem error must propagate verbatim, "
                        "not be masked by the accessor."
                    ),
                )
                index = resolver.fresh_disk_guid_index(
                    ttl_seconds=30.0, now_monotonic=110.0,
                )
        self.assertEqual(
            2, spy.call_count,
            msg=(
                "Failed scan must not poison the cache; the next "
                "call within the window must invoke the underlying "
                "scan again."
            ),
        )
        self.assertIn(
            BASE_GUID, index,
            msg=(
                "Post-recovery mapping must contain the seeded GUID, "
                "proving the second scan executed and returned a "
                "usable index (not a cached failure)."
            ),
        )


class DetectStaleCacheUsesResolverFreshIndexTests(unittest.TestCase):
    """Issue #230 — the validate-refs stale-cache detector consults
    the resolver's ``fresh_disk_guid_index`` accessor exactly once
    per failure-path invocation, so the freshness window amortizes
    repeated retries without changing the hint contract.
    """

    def test_detector_delegates_to_resolver_fresh_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_base_meta(root)
            _write_variant_referring_to(root, SECOND_GUID)
            resolver = ReferenceResolverService(project_root=root)
            resolver._guid_map()  # noqa: SLF001 — prime cache
            write_file(
                root / "Assets" / "Second.asset",
                "%YAML 1.1\n",
            )
            write_file(
                root / "Assets" / "Second.asset.meta",
                f"fileFormatVersion: 2\nguid: {SECOND_GUID}\n",
            )
            with patch.object(
                resolver,
                "fresh_disk_guid_index",
                wraps=resolver.fresh_disk_guid_index,
            ) as spy:
                response = validate_refs(resolver, scope="Assets")
        self.assertEqual(
            1, spy.call_count,
            msg=(
                "Validate-refs failure path must consult the "
                "resolver's fresh_disk_guid_index exactly once."
            ),
        )
        # Hint contract preserved end-to-end: the stale-cache hint
        # still fires, still attaches to the REF001 failure envelope,
        # and still carries the documented detail string.
        self.assertEqual(
            Severity.ERROR, response.severity,
            msg=(
                "Stale-cache scenario must still surface as error "
                "severity after the detector refactor (REF001 contract)."
            ),
        )
        details = [d.detail for d in response.diagnostics]
        self.assertIn(
            STALE_GUID_INDEX_HINT_DETAIL, details,
            msg=(
                "Hint contract must be preserved after detector "
                "refactor: STALE_GUID_INDEX_HINT must appear in "
                "diagnostics."
            ),
        )

    def test_existing_hint_contract_preserved_end_to_end(self) -> None:
        """The four pre-existing hint scenarios continue to produce
        the documented envelopes after the detector switches to the
        resolver-accessor path.  Each scenario is a separate run
        against a fresh resolver so cache state cannot bleed between
        them.
        """
        # Scenario A: hint fires on resolvable stale.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_base_meta(root)
            _write_variant_referring_to(root, SECOND_GUID)
            resolver = ReferenceResolverService(project_root=root)
            resolver._guid_map()  # noqa: SLF001
            write_file(
                root / "Assets" / "Second.asset",
                "%YAML 1.1\n",
            )
            write_file(
                root / "Assets" / "Second.asset.meta",
                f"fileFormatVersion: 2\nguid: {SECOND_GUID}\n",
            )
            response_a = validate_refs(resolver, scope="Assets")
        details_a = [d.detail for d in response_a.diagnostics]
        self.assertEqual(
            Severity.ERROR, response_a.severity,
            msg=(
                "Scenario A: hint fires on the REF001 failure path — "
                "response severity must be error."
            ),
        )
        self.assertEqual(
            "REF001", response_a.code,
            msg=(
                "Scenario A: failure code must remain REF001 after "
                "the detector refactor."
            ),
        )
        self.assertIn(
            STALE_GUID_INDEX_HINT_DETAIL, details_a,
            msg=(
                "Scenario A: stale-cache hint must attach to the "
                "REF001 diagnostics when a fresh scan resolves the "
                "missing GUID."
            ),
        )

        # Scenario B: refresh flag asserted suppresses the hint.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_base_meta(root)
            _write_variant_referring_to(root, SECOND_GUID)
            resolver = ReferenceResolverService(project_root=root)
            resolver._guid_map()  # noqa: SLF001
            write_file(
                root / "Assets" / "Second.asset",
                "%YAML 1.1\n",
            )
            write_file(
                root / "Assets" / "Second.asset.meta",
                f"fileFormatVersion: 2\nguid: {SECOND_GUID}\n",
            )
            response_b = validate_refs(
                resolver, scope="Assets", refresh_guid_index=True,
            )
        details_b = [d.detail for d in response_b.diagnostics]
        self.assertNotIn(
            STALE_GUID_INDEX_HINT_DETAIL, details_b,
            msg=(
                "Scenario B: caller already asserted refresh — the "
                "stale-cache hint must be suppressed."
            ),
        )

        # Scenario C: no missing assets suppresses the hint.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_base_meta(root)
            resolver = ReferenceResolverService(project_root=root)
            response_c = validate_refs(resolver, scope="Assets")
        details_c = [d.detail for d in response_c.diagnostics]
        self.assertNotIn(
            STALE_GUID_INDEX_HINT_DETAIL, details_c,
            msg=(
                "Scenario C: no missing assets — the stale-cache "
                "hint must not appear."
            ),
        )

        # Scenario D: truly-missing asset (fresh scan also fails)
        # suppresses the hint.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_base_meta(root)
            _write_variant_referring_to(root, TRULY_MISSING_GUID)
            resolver = ReferenceResolverService(project_root=root)
            response_d = validate_refs(resolver, scope="Assets")
        details_d = [d.detail for d in response_d.diagnostics]
        self.assertNotIn(
            STALE_GUID_INDEX_HINT_DETAIL, details_d,
            msg=(
                "Scenario D: fresh scan also fails (truly missing "
                "GUID) — the stale-cache hint must remain suppressed."
            ),
        )
