import pathlib
import tempfile
import unittest
from datetime import date, datetime, timezone
from unittest import mock

from scripts import update_profile_stats as profile_stats


class AccountSummaryTests(unittest.TestCase):
    def test_uses_owned_repository_count_and_paginates_stars(self) -> None:
        responses = [
            {
                "user": {
                    "id": "user-id",
                    "createdAt": "2024-01-01T00:00:00Z",
                    "repositoriesContributedTo": {
                        "nodes": [{"nameWithOwner": "owner/first"}],
                        "pageInfo": {
                            "endCursor": "contributed-page",
                            "hasNextPage": True,
                        },
                    },
                    "repositories": {
                        "totalCount": 101,
                        "nodes": [{"stargazerCount": 2}],
                        "pageInfo": {
                            "endCursor": "owned-page",
                            "hasNextPage": True,
                        },
                    },
                }
            },
            {
                "user": {
                    "repositoriesContributedTo": {
                        "nodes": [{"nameWithOwner": "owner/second"}],
                        "pageInfo": {
                            "endCursor": None,
                            "hasNextPage": False,
                        },
                    }
                }
            },
            {
                "user": {
                    "repositories": {
                        "nodes": [{"stargazerCount": 3}],
                        "pageInfo": {
                            "endCursor": None,
                            "hasNextPage": False,
                        },
                    }
                }
            },
        ]

        with mock.patch.object(
            profile_stats,
            "graphql",
            side_effect=responses,
        ):
            summary = profile_stats.account_summary()

        self.assertEqual(summary["repositories"], 101)
        self.assertEqual(summary["stars"], 5)
        self.assertEqual(
            summary["_repositories"],
            ["owner/first", "owner/second"],
        )


class LinesWrittenTests(unittest.TestCase):
    @staticmethod
    def history_response(nodes: list[dict], has_next: bool, cursor: str | None):
        return {
            "repository": {
                "defaultBranchRef": {
                    "target": {
                        "history": {
                            "nodes": nodes,
                            "pageInfo": {
                                "endCursor": cursor,
                                "hasNextPage": has_next,
                            },
                        }
                    }
                }
            }
        }

    def test_paginates_deduplicates_and_excludes_merge_commits(self) -> None:
        responses = [
            self.history_response(
                [
                    {
                        "oid": "first",
                        "additions": 10,
                        "authoredDate": "2026-07-27T20:00:00Z",
                        "parents": {"totalCount": 1},
                    },
                    {
                        "oid": "merge",
                        "additions": 99,
                        "authoredDate": "2026-07-27T21:00:00Z",
                        "parents": {"totalCount": 2},
                    },
                ],
                True,
                "next",
            ),
            self.history_response(
                [
                    {
                        "oid": "first",
                        "additions": 10,
                        "authoredDate": "2026-07-27T20:00:00Z",
                        "parents": {"totalCount": 1},
                    },
                    {
                        "oid": "second",
                        "additions": 20,
                        "authoredDate": "2026-07-28T10:00:00Z",
                        "parents": {"totalCount": 1},
                    },
                ],
                False,
                None,
            ),
            {"repository": None},
        ]

        with mock.patch.object(
            profile_stats,
            "graphql",
            side_effect=responses,
        ) as graphql:
            with mock.patch.dict(
                profile_stats.os.environ,
                {"PROFILE_TIMEZONE": "Asia/Kolkata"},
            ):
                total, day_moves = profile_stats.commit_stats(
                    "user-id",
                    ["owner/missing", "owner/repo", "owner/repo"],
                )

        self.assertEqual(total, 30)
        self.assertEqual(
            day_moves,
            {("2026-07-27", "2026-07-28"): 2},
        )
        self.assertEqual(graphql.call_count, 3)


class StreakTests(unittest.TestCase):
    def test_profile_date_uses_configured_timezone(self) -> None:
        now = datetime(2026, 7, 27, 21, 0, tzinfo=timezone.utc)
        with mock.patch.dict(
            profile_stats.os.environ,
            {"PROFILE_TIMEZONE": "Asia/Kolkata"},
        ):
            self.assertEqual(
                profile_stats.profile_today(now),
                date(2026, 7, 28),
            )

    def test_invalid_profile_timezone_fails_clearly(self) -> None:
        with mock.patch.dict(
            profile_stats.os.environ,
            {"PROFILE_TIMEZONE": "Not/A-Timezone"},
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Invalid PROFILE_TIMEZONE",
            ):
                profile_stats.profile_today(
                    datetime(2026, 7, 28, tzinfo=timezone.utc)
                )

    def test_commit_day_moves_preserve_totals_and_shift_local_activity(self) -> None:
        adjusted = profile_stats.apply_commit_day_moves(
            {
                "2026-07-27": 19,
                "2026-07-28": 0,
            },
            {("2026-07-27", "2026-07-28"): 2},
        )
        self.assertEqual(adjusted["2026-07-27"], 17)
        self.assertEqual(adjusted["2026-07-28"], 2)
        self.assertEqual(sum(adjusted.values()), 19)
        self.assertEqual(
            profile_stats.calculate_streaks(
                adjusted,
                date(2026, 7, 28),
            )[0],
            2,
        )

    def test_counts_today_and_previous_consecutive_days(self) -> None:
        current, longest = profile_stats.calculate_streaks(
            {
                "2026-07-26": 0,
                "2026-07-27": 3,
                "2026-07-28": 1,
            },
            date(2026, 7, 28),
        )
        self.assertEqual((current, longest), (2, 2))

    def test_allows_only_current_day_as_grace(self) -> None:
        current, longest = profile_stats.calculate_streaks(
            {
                "2026-07-25": 1,
                "2026-07-26": 2,
                "2026-07-27": 3,
                "2026-07-28": 0,
            },
            date(2026, 7, 28),
        )
        self.assertEqual((current, longest), (3, 3))

    def test_does_not_preserve_stale_streak_across_multiple_empty_days(self) -> None:
        current, longest = profile_stats.calculate_streaks(
            {
                "2026-07-24": 1,
                "2026-07-25": 1,
                "2026-07-26": 0,
                "2026-07-27": 0,
                "2026-07-28": 0,
            },
            date(2026, 7, 28),
        )
        self.assertEqual((current, longest), (0, 2))

    def test_missing_dates_break_streak_and_future_days_are_ignored(self) -> None:
        current, longest = profile_stats.calculate_streaks(
            {
                "2026-07-25": 1,
                "2026-07-27": 1,
                "2026-07-28": 1,
                "2026-07-29": 99,
            },
            date(2026, 7, 28),
        )
        self.assertEqual((current, longest), (2, 2))

    def test_year_boundary_and_empty_history(self) -> None:
        self.assertEqual(
            profile_stats.calculate_streaks(
                {
                    "2025-12-31": 1,
                    "2026-01-01": 1,
                },
                date(2026, 1, 1),
            ),
            (2, 2),
        )
        self.assertEqual(
            profile_stats.calculate_streaks({}, date(2026, 1, 1)),
            (0, 0),
        )


class ReadmeStatsTests(unittest.TestCase):
    @staticmethod
    def stats() -> dict:
        return {
            "contributions": 871,
            "lines_written": 471056,
            "commits": 861,
            "repositories": 5,
            "pull_requests": 3,
            "stars": 6,
            "current_streak": 1,
            "longest_streak": 10,
        }

    def test_renders_formatted_markdown_values_and_streak_units(self) -> None:
        markdown = profile_stats.render_stats_markdown(self.stats())
        self.assertIn("**471,056**", markdown)
        self.assertIn("**1 day**", markdown)
        self.assertIn("**10 days**", markdown)
        self.assertNotIn("<svg", markdown)

    def test_replaces_only_the_managed_readme_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            readme = root / "README.md"
            readme.write_text(
                "before\n"
                f"{profile_stats.STATS_START}\n"
                "old values\n"
                f"{profile_stats.STATS_END}\n"
                "after\n",
                encoding="utf-8",
            )

            with mock.patch.object(profile_stats, "README", readme):
                profile_stats.update_readme_stats(self.stats())

            updated = readme.read_text(encoding="utf-8")
            self.assertTrue(updated.startswith("before\n"))
            self.assertTrue(updated.endswith("after\n"))
            self.assertIn("**871**", updated)
            self.assertNotIn("old values", updated)

    def test_missing_markers_fail_without_rewriting_readme(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            readme = pathlib.Path(directory) / "README.md"
            readme.write_text("unchanged\n", encoding="utf-8")

            with mock.patch.object(profile_stats, "README", readme):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "statistics markers",
                ):
                    profile_stats.update_readme_stats(self.stats())

            self.assertEqual(readme.read_text(encoding="utf-8"), "unchanged\n")


if __name__ == "__main__":
    unittest.main()
