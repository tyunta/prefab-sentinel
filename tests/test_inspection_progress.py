import unittest


class InspectionProgressTests(unittest.TestCase):
    def test_stage_counts_serialize_in_record_order_with_next_action(self) -> None:
        try:
            from prefab_sentinel.inspection_progress import InspectionProgress
        except ModuleNotFoundError as exc:
            self.fail(
                "Expected prefab_sentinel.inspection_progress.InspectionProgress "
                f"to exist, observed missing module {exc.name!r}."
            )

        progress = InspectionProgress()
        progress.record_stage("queued_targets", count=3)
        progress.record_stage("scanned_targets", completed=True, count=2)
        progress.record_stage("material_candidates", completed=True, count=0)
        progress.record_stage("invalid_count", completed=True, count=-1)
        progress.record_stage("waiting_for_editor")

        self.assertEqual(
            {
                "progress_summary": [
                    {
                        "name": "queued_targets",
                        "completed": False,
                        "count": 3,
                    },
                    {
                        "name": "scanned_targets",
                        "completed": True,
                        "count": 2,
                    },
                    {
                        "name": "material_candidates",
                        "completed": True,
                        "count": 0,
                    },
                    {
                        "name": "invalid_count",
                        "completed": True,
                    },
                    {
                        "name": "waiting_for_editor",
                        "completed": False,
                    },
                ],
                "partial_counts": {
                    "queued_targets": 3,
                    "scanned_targets": 2,
                    "material_candidates": 0,
                },
                "current_or_slowest_step": "material_candidates",
                "suggested_next_action": "Use a narrower scope.",
            },
            progress.to_data(
                current_or_slowest_step="material_candidates",
                suggested_next_action="Use a narrower scope.",
            ),
        )
