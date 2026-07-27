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
                        "parents": {"totalCount": 1},
                    },
                    {
                        "oid": "merge",
                        "additions": 99,
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
                        "parents": {"totalCount": 1},
                    },
                    {
                        "oid": "second",
                        "additions": 20,
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
            total = profile_stats.lines_written(
                "user-id",
                ["owner/missing", "owner/repo", "owner/repo"],
            )

        self.assertEqual(total, 30)
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


class RenderingTests(unittest.TestCase):
    def test_streak_units_are_singular(self) -> None:
        svg = profile_stats.render_svg(
            {
                "contributions": 2,
                "lines_written": 10,
                "commits": 2,
                "repositories": 1,
                "pull_requests": 1,
                "stars": 1,
                "current_streak": 1,
                "longest_streak": 1,
            },
            datetime(2026, 7, 28, tzinfo=timezone.utc),
        )
        self.assertIn(">1 DAY</text>", svg)
        self.assertNotIn(">1 DAYS</text>", svg)

    def test_contribution_graph_contains_exactly_30_days(self) -> None:
        now = datetime(2026, 7, 30, tzinfo=timezone.utc)
        svg = profile_stats.render_contribution_graph(
            {
                "2026-07-01": 1,
                "2026-07-30": 2,
            },
            now,
        )
        self.assertEqual(svg.count("<title>"), 30)
        self.assertIn("2026-07-01: 1 contributions", svg)
        self.assertIn("2026-07-30: 2 contributions", svg)

    def test_contribution_graph_ends_on_profile_calendar_day(self) -> None:
        now = datetime(2026, 7, 27, 21, 0, tzinfo=timezone.utc)
        with mock.patch.dict(
            profile_stats.os.environ,
            {"PROFILE_TIMEZONE": "Asia/Kolkata"},
        ):
            svg = profile_stats.render_contribution_graph(
                {"2026-07-28": 2},
                now,
            )
        self.assertIn("2026-07-28: 2 contributions", svg)

    def test_cache_keys_follow_each_asset_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            stats_output = root / "stats.svg"
            graph_output = root / "contribution-graph.svg"
            readme = root / "README.md"
            stats_output.write_text("stats-one", encoding="utf-8")
            graph_output.write_text("graph-one", encoding="utf-8")
            readme.write_text(
                './assets/stats.svg?v=old"\n'
                './assets/contribution-graph.svg?v=old"\n',
                encoding="utf-8",
            )

            with (
                mock.patch.object(profile_stats, "OUTPUT", stats_output),
                mock.patch.object(profile_stats, "GRAPH_OUTPUT", graph_output),
                mock.patch.object(profile_stats, "README", readme),
            ):
                profile_stats.update_readme_cache_keys()
                first = readme.read_text(encoding="utf-8")
                stats_output.write_text("stats-two", encoding="utf-8")
                profile_stats.update_readme_cache_keys()
                second = readme.read_text(encoding="utf-8")

            first_stats, first_graph = first.splitlines()
            second_stats, second_graph = second.splitlines()
            self.assertNotEqual(first_stats, second_stats)
            self.assertEqual(first_graph, second_graph)


if __name__ == "__main__":
    unittest.main()
