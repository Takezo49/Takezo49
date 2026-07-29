#!/usr/bin/env python3
"""Update live GitHub statistics embedded in the profile README."""

from __future__ import annotations

import json
import os
import pathlib
import re
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


LOGIN = "Takezo49"
GRAPHQL_URL = "https://api.github.com/graphql"
README = pathlib.Path(__file__).resolve().parents[1] / "README.md"
STATS_START = "<!-- github-stats:start -->"
STATS_END = "<!-- github-stats:end -->"


def graphql(query: str, variables: dict | None = None) -> dict:
    token = os.environ.get("PROFILE_STATS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("PROFILE_STATS_TOKEN or GITHUB_TOKEN is required")

    request = urllib.request.Request(
        GRAPHQL_URL,
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": f"{LOGIN}-profile-stats",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"GitHub GraphQL request failed: {detail}") from exc

    if payload.get("errors"):
        raise RuntimeError(f"GitHub GraphQL error: {payload['errors']}")
    return payload["data"]


def account_summary() -> dict:
    query = """
    query($login: String!) {
      user(login: $login) {
        id
        createdAt
        repositoriesContributedTo(
          first: 100
          includeUserRepositories: true
          contributionTypes: [COMMIT, PULL_REQUEST]
        ) {
          totalCount
          nodes {
            nameWithOwner
          }
          pageInfo {
            endCursor
            hasNextPage
          }
        }
        repositories(
          first: 100
          ownerAffiliations: OWNER
          privacy: PUBLIC
        ) {
          totalCount
          nodes {
            stargazerCount
          }
          pageInfo {
            endCursor
            hasNextPage
          }
        }
      }
    }
    """
    user = graphql(query, {"login": LOGIN})["user"]
    contributed = user["repositoriesContributedTo"]
    repositories = [repo["nameWithOwner"] for repo in contributed["nodes"]]
    cursor = contributed["pageInfo"]["endCursor"]

    while contributed["pageInfo"]["hasNextPage"]:
        page_query = """
        query($login: String!, $after: String!) {
          user(login: $login) {
            repositoriesContributedTo(
              first: 100
              after: $after
              includeUserRepositories: true
              contributionTypes: [COMMIT, PULL_REQUEST]
            ) {
              nodes {
                nameWithOwner
              }
              pageInfo {
                endCursor
                hasNextPage
              }
            }
          }
        }
        """
        contributed = graphql(
            page_query,
            {"login": LOGIN, "after": cursor},
        )["user"]["repositoriesContributedTo"]
        repositories.extend(
            repo["nameWithOwner"] for repo in contributed["nodes"]
        )
        cursor = contributed["pageInfo"]["endCursor"]

    owned = user["repositories"]
    repository_count = owned["totalCount"]
    stars = sum(repo["stargazerCount"] for repo in owned["nodes"])
    cursor = owned["pageInfo"]["endCursor"]

    while owned["pageInfo"]["hasNextPage"]:
        page_query = """
        query($login: String!, $after: String!) {
          user(login: $login) {
            repositories(
              first: 100
              after: $after
              ownerAffiliations: OWNER
              privacy: PUBLIC
            ) {
              nodes {
                stargazerCount
              }
              pageInfo {
                endCursor
                hasNextPage
              }
            }
          }
        }
        """
        owned = graphql(
            page_query,
            {"login": LOGIN, "after": cursor},
        )["user"]["repositories"]
        stars += sum(repo["stargazerCount"] for repo in owned["nodes"])
        cursor = owned["pageInfo"]["endCursor"]

    return {
        "_user_id": user["id"],
        "_repositories": repositories,
        "created_at": datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00")),
        "repositories": repository_count,
        "stars": stars,
    }


def commit_stats(
    author_id: str,
    repositories: list[str],
) -> tuple[int, dict[tuple[str, str], int]]:
    """Count authored lines and collect UTC-to-profile-day commit moves."""
    total = 0
    seen_commits: set[str] = set()
    day_moves: dict[tuple[str, str], int] = {}
    profile_timezone = get_profile_timezone()
    query = """
    query(
      $owner: String!
      $name: String!
      $authorId: ID!
      $after: String
    ) {
      repository(owner: $owner, name: $name) {
        defaultBranchRef {
          target {
            ... on Commit {
              history(
                first: 100
                after: $after
                author: {id: $authorId}
              ) {
                nodes {
                  oid
                  additions
                  authoredDate
                  parents(first: 2) {
                    totalCount
                  }
                }
                pageInfo {
                  endCursor
                  hasNextPage
                }
              }
            }
          }
        }
      }
    }
    """

    for name_with_owner in sorted(set(repositories)):
        owner, name = name_with_owner.split("/", 1)
        cursor = None

        while True:
            data = graphql(
                query,
                {
                    "owner": owner,
                    "name": name,
                    "authorId": author_id,
                    "after": cursor,
                },
            )
            target = (
                ((data.get("repository") or {}).get("defaultBranchRef") or {})
                .get("target")
            )
            if not target:
                break

            history = target["history"]
            for commit in history["nodes"]:
                if commit["oid"] in seen_commits:
                    continue
                seen_commits.add(commit["oid"])

                authored_at = datetime.fromisoformat(
                    commit["authoredDate"].replace("Z", "+00:00")
                )
                utc_day = authored_at.astimezone(timezone.utc).date().isoformat()
                profile_day = (
                    authored_at.astimezone(profile_timezone).date().isoformat()
                )
                if utc_day != profile_day:
                    move = (utc_day, profile_day)
                    day_moves[move] = day_moves.get(move, 0) + 1

                if commit["parents"]["totalCount"] > 1:
                    continue
                total += commit["additions"]

            if not history["pageInfo"]["hasNextPage"]:
                break
            cursor = history["pageInfo"]["endCursor"]

    return total, day_moves


def contribution_period(start: datetime, end: datetime) -> dict:
    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          totalCommitContributions
          totalIssueContributions
          totalPullRequestContributions
          contributionCalendar {
            totalContributions
            weeks {
              contributionDays {
                contributionCount
                date
              }
            }
          }
        }
      }
    }
    """
    variables = {
        "login": LOGIN,
        "from": start.isoformat().replace("+00:00", "Z"),
        "to": end.isoformat().replace("+00:00", "Z"),
    }
    return graphql(query, variables)["user"]["contributionsCollection"]


def get_profile_timezone() -> ZoneInfo:
    timezone_name = os.environ.get("PROFILE_TIMEZONE", "UTC")
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(
            f"Invalid PROFILE_TIMEZONE: {timezone_name}"
        ) from exc


def profile_today(now: datetime) -> date:
    return now.astimezone(get_profile_timezone()).date()


def apply_commit_day_moves(
    days: dict[str, int],
    day_moves: dict[tuple[str, str], int],
) -> dict[str, int]:
    adjusted = dict(days)
    for (utc_day, profile_day), requested_count in day_moves.items():
        moved_count = min(adjusted.get(utc_day, 0), requested_count)
        if moved_count <= 0:
            continue
        adjusted[utc_day] -= moved_count
        adjusted[profile_day] = adjusted.get(profile_day, 0) + moved_count
    return adjusted


def calculate_streaks(days: dict[str, int], today: date) -> tuple[int, int]:
    dated_counts = {
        date.fromisoformat(day): count
        for day, count in days.items()
        if date.fromisoformat(day) <= today
    }
    if not dated_counts:
        return 0, 0

    longest = run = 0
    previous_day: date | None = None
    for day in sorted(dated_counts):
        count = dated_counts[day]
        if count:
            run = run + 1 if previous_day == day - timedelta(days=1) else 1
            longest = max(longest, run)
        else:
            run = 0
        previous_day = day

    cursor = today
    if dated_counts.get(cursor, 0) == 0:
        cursor -= timedelta(days=1)

    current = 0
    while dated_counts.get(cursor, 0) > 0:
        current += 1
        cursor -= timedelta(days=1)

    return current, longest


def contribution_stats(
    created_at: datetime,
    now: datetime,
    commit_day_moves: dict[tuple[str, str], int] | None = None,
) -> dict:
    totals = {
        "contributions": 0,
        "commits": 0,
        "pull_requests": 0,
        "issues": 0,
    }
    days: dict[str, int] = {}

    for year in range(created_at.year, now.year + 1):
        start = max(created_at, datetime(year, 1, 1, tzinfo=timezone.utc))
        end = min(now, datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc))
        period = contribution_period(start, end)
        totals["contributions"] += period["contributionCalendar"]["totalContributions"]
        totals["commits"] += period["totalCommitContributions"]
        totals["pull_requests"] += period["totalPullRequestContributions"]
        totals["issues"] += period["totalIssueContributions"]
        for week in period["contributionCalendar"]["weeks"]:
            for day in week["contributionDays"]:
                days[day["date"]] = day["contributionCount"]

    days = apply_commit_day_moves(days, commit_day_moves or {})
    current, longest = calculate_streaks(days, profile_today(now))

    return {
        **totals,
        "current_streak": current,
        "longest_streak": longest,
        "days": days,
    }


def render_stats_markdown(stats: dict) -> str:
    current_unit = "DAY" if stats["current_streak"] == 1 else "DAYS"
    longest_unit = "DAY" if stats["longest_streak"] == 1 else "DAYS"
    return f"""| Total contributions | Lines written | Commits | Repositories | Pull requests | Stars |
|---:|---:|---:|---:|---:|---:|
| **{stats["contributions"]:,}** | **{stats["lines_written"]:,}** | **{stats["commits"]:,}** | **{stats["repositories"]:,}** | **{stats["pull_requests"]:,}** | **{stats["stars"]:,}** |

| Current streak | Longest streak |
|---:|---:|
| **{stats["current_streak"]} {current_unit.lower()}** | **{stats["longest_streak"]} {longest_unit.lower()}** |

<sub>Refresh scheduled every 15 minutes through GitHub GraphQL. GitHub renders the native contribution graph below this README.</sub>"""


def update_readme_stats(stats: dict) -> None:
    content = README.read_text(encoding="utf-8")
    block = f"{STATS_START}\n{render_stats_markdown(stats)}\n{STATS_END}"
    updated, matches = re.subn(
        rf"{re.escape(STATS_START)}.*?{re.escape(STATS_END)}",
        lambda _: block,
        content,
        count=1,
        flags=re.DOTALL,
    )
    if matches != 1:
        raise RuntimeError("Could not locate GitHub statistics markers in README.md")
    README.write_text(updated, encoding="utf-8")


def main() -> None:
    now = datetime.now(timezone.utc)
    summary = account_summary()
    author_id = summary.pop("_user_id")
    repositories = summary.pop("_repositories")
    lines_written, commit_day_moves = commit_stats(author_id, repositories)
    stats = {
        **summary,
        **contribution_stats(
            summary["created_at"],
            now,
            commit_day_moves,
        ),
        "lines_written": lines_written,
    }
    update_readme_stats(stats)
    print(
        json.dumps(
            {
                key: value
                for key, value in stats.items()
                if key not in {"created_at", "days"}
            }
        )
    )


if __name__ == "__main__":
    main()
