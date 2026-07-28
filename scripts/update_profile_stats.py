#!/usr/bin/env python3
"""Generate the profile's live GitHub statistics card."""

from __future__ import annotations

import html
import json
import os
import pathlib
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone


LOGIN = "Takezo49"
GRAPHQL_URL = "https://api.github.com/graphql"
GRAPH_STYLE_VERSION = 2
OUTPUT = pathlib.Path(__file__).resolve().parents[1] / "assets" / "stats.svg"
GRAPH_OUTPUT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "assets"
    / "contribution-graph.svg"
)
README = pathlib.Path(__file__).resolve().parents[1] / "README.md"


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
          nodes {
            stargazerCount
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

    return {
        "_user_id": user["id"],
        "_repositories": repositories,
        "created_at": datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00")),
        "repositories": len(repositories),
        "stars": sum(repo["stargazerCount"] for repo in user["repositories"]["nodes"]),
    }


def lines_written(author_id: str, repositories: list[str]) -> int:
    """Count additions from authored, non-merge commits without double counting."""
    total = 0
    seen_commits: set[str] = set()
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
                if commit["parents"]["totalCount"] > 1:
                    continue
                if commit["oid"] in seen_commits:
                    continue
                seen_commits.add(commit["oid"])
                total += commit["additions"]

            if not history["pageInfo"]["hasNextPage"]:
                break
            cursor = history["pageInfo"]["endCursor"]

    return total


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


def contribution_stats(created_at: datetime, now: datetime) -> dict:
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

    longest = current = run = 0
    ordered_days = sorted(days.items())
    for _, count in ordered_days:
        run = run + 1 if count else 0
        longest = max(longest, run)

    # A zero-contribution current day does not break a streak ending yesterday.
    started = False
    for _, count in reversed(ordered_days):
        if not started and not count:
            continue
        if not count:
            break
        started = True
        current += 1

    return {
        **totals,
        "current_streak": current,
        "longest_streak": longest,
        "days": days,
    }


def render_svg(stats: dict, now: datetime) -> str:
    values = [
        ("COMMITS", stats["commits"]),
        ("PULL REQUESTS", stats["pull_requests"]),
        ("REPOSITORIES", stats["repositories"]),
        ("STARS", stats["stars"]),
        ("LINES WRITTEN", stats["lines_written"]),
    ]
    value_columns = "\n".join(
        f"""        <g transform="translate({400 + index * 150} 94)">
          <text class="label">{html.escape(label)}</text>
          <text class="value" y="42">{value:,}</text>
        </g>"""
        for index, (label, value) in enumerate(values)
    )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="300" viewBox="0 0 1200 300" role="img" aria-label="Live GitHub statistics for {LOGIN}">
  <defs>
    <linearGradient id="panel" x1="0" x2="1">
      <stop offset="0" stop-color="#071513"/>
      <stop offset="1" stop-color="#091b18"/>
    </linearGradient>
    <radialGradient id="signal">
      <stop offset="0" stop-color="#24ebd9" stop-opacity=".22"/>
      <stop offset="1" stop-color="#24ebd9" stop-opacity="0"/>
    </radialGradient>
    <filter id="glow">
      <feGaussianBlur stdDeviation="5" result="blur"/>
      <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <style>
      .eyebrow {{ font: 600 13px ui-monospace, SFMono-Regular, Menlo, monospace; fill: #24ebd9; letter-spacing: 2px; }}
      .hero {{ font: 700 54px ui-monospace, SFMono-Regular, Menlo, monospace; fill: #f4fffd; }}
      .label {{ font: 600 12px ui-monospace, SFMono-Regular, Menlo, monospace; fill: #75afa8; letter-spacing: 1px; }}
      .value {{ font: 700 27px ui-monospace, SFMono-Regular, Menlo, monospace; fill: #f4fffd; }}
      .streak {{ font: 700 25px ui-monospace, SFMono-Regular, Menlo, monospace; fill: #24ebd9; }}
      .meta {{ font: 500 11px ui-monospace, SFMono-Regular, Menlo, monospace; fill: #75afa8; }}
    </style>
  </defs>
  <rect x="1" y="1" width="1198" height="298" rx="12" fill="url(#panel)" stroke="#1b625a"/>
  <circle cx="220" cy="150" r="190" fill="url(#signal)"/>
  <g opacity=".13" stroke="#24ebd9">
    <path d="M0 58H1200M0 118H1200M0 178H1200M0 238H1200"/>
    <path d="M60 0V300M180 0V300M300 0V300M420 0V300M540 0V300M660 0V300M780 0V300M900 0V300M1020 0V300M1140 0V300"/>
  </g>
  <g transform="translate(42 48)">
    <circle cx="7" cy="7" r="6" fill="#24ebd9" filter="url(#glow)"/>
    <text class="eyebrow" x="24" y="12">GITHUB SIGNAL // LIVE</text>
    <text class="hero" y="92">{stats["contributions"]:,}</text>
    <text class="label" y="120">TOTAL CONTRIBUTIONS</text>
  </g>
  {value_columns}
  <path d="M42 185H1158" stroke="#1b625a"/>
  <g transform="translate(42 222)">
    <text class="label">CURRENT STREAK</text>
    <text class="streak" x="145">{stats["current_streak"]} DAYS</text>
    <text class="label" x="345">LONGEST STREAK</text>
    <text class="streak" x="492">{stats["longest_streak"]} DAYS</text>
    <text class="meta" x="790">SYNCED HOURLY FROM GITHUB</text>
  </g>
</svg>
"""


def render_contribution_graph(days: dict[str, int], now: datetime) -> str:
    end = now.date()
    start = end - timedelta(days=30)
    activity = [
        (start + timedelta(days=index), days.get((start + timedelta(days=index)).isoformat(), 0))
        for index in range(31)
    ]
    chart_left = 82
    chart_right = 1150
    chart_top = 82
    chart_bottom = 288
    chart_width = chart_right - chart_left
    chart_height = chart_bottom - chart_top
    maximum = max((count for _, count in activity), default=0)
    axis_max = max(5, ((maximum + 4) // 5) * 5)

    points: list[tuple[float, float, str, int]] = []
    for index, (day, count) in enumerate(activity):
        x = chart_left + (chart_width * index / (len(activity) - 1))
        y = chart_bottom - (chart_height * count / axis_max)
        points.append((x, y, day.isoformat(), count))

    line_path = " ".join(
        f"{'M' if index == 0 else 'L'} {x:.2f} {y:.2f}"
        for index, (x, y, _, _) in enumerate(points)
    )
    area_path = (
        f"M {points[0][0]:.2f} {chart_bottom} "
        + " ".join(f"L {x:.2f} {y:.2f}" for x, y, _, _ in points)
        + f" L {points[-1][0]:.2f} {chart_bottom} Z"
    )
    point_nodes = "".join(
        f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3.6" fill="#f4fffd" '
        f'stroke="#24ebd9" stroke-width="1.5"><title>{day}: {count} contributions</title></circle>'
        for x, y, day, count in points
    )

    horizontal_grid: list[str] = []
    for index in range(6):
        value = axis_max * index // 5
        y = chart_bottom - chart_height * index / 5
        horizontal_grid.append(
            f'<path d="M {chart_left} {y:.2f} H {chart_right}" class="grid"/>'
            f'<text class="axis" x="{chart_left - 14}" y="{y + 4:.2f}" text-anchor="end">{value}</text>'
        )

    vertical_grid: list[str] = []
    x_labels: list[str] = []
    for index, (x, _, day, _) in enumerate(points):
        if index % 3 != 0 and index != len(points) - 1:
            continue
        vertical_grid.append(
            f'<path d="M {x:.2f} {chart_top} V {chart_bottom}" class="grid"/>'
        )
        label = datetime.fromisoformat(day).strftime("%d")
        x_labels.append(
            f'<text class="axis" x="{x:.2f}" y="{chart_bottom + 23}" text-anchor="middle">{label}</text>'
        )

    period_total = sum(count for _, count in activity)

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="360" viewBox="0 0 1200 360" role="img" aria-label="Live 30-day contribution activity for {LOGIN}">
  <defs>
    <linearGradient id="panel" x1="0" x2="1">
      <stop offset="0" stop-color="#071513"/>
      <stop offset="1" stop-color="#091b18"/>
    </linearGradient>
    <linearGradient id="area" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#24ebd9" stop-opacity=".34"/>
      <stop offset="1" stop-color="#24ebd9" stop-opacity=".02"/>
    </linearGradient>
    <filter id="glow">
      <feGaussianBlur stdDeviation="4" result="blur"/>
      <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <style>
      .title {{ font: 700 15px ui-monospace, SFMono-Regular, Menlo, monospace; fill: #24ebd9; letter-spacing: 1.5px; }}
      .total {{ font: 700 24px ui-monospace, SFMono-Regular, Menlo, monospace; fill: #f4fffd; }}
      .axis, .meta {{ font: 500 10px ui-monospace, SFMono-Regular, Menlo, monospace; fill: #75afa8; }}
      .grid {{ fill: none; stroke: #1b625a; stroke-width: 1; stroke-dasharray: 2 4; opacity: .72; }}
    </style>
  </defs>
  <rect x="1" y="1" width="1198" height="358" rx="12" fill="url(#panel)" stroke="#1b625a"/>
  <circle cx="41" cy="33" r="5" fill="#24ebd9" filter="url(#glow)"/>
  <text class="title" x="58" y="39">CONTRIBUTION ACTIVITY // LAST 30 DAYS</text>
  <text class="total" x="1158" y="39" text-anchor="end">{period_total:,}</text>
  <text class="meta" x="1158" y="57" text-anchor="end">ACCOUNT-VISIBLE CONTRIBUTIONS</text>
  {''.join(horizontal_grid)}
  {''.join(vertical_grid)}
  <path d="{area_path}" fill="url(#area)"/>
  <path d="{line_path}" fill="none" stroke="#24ebd9" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" filter="url(#glow)"/>
  {point_nodes}
  {''.join(x_labels)}
  <text class="meta" x="22" y="206" transform="rotate(-90 22 206)">CONTRIBUTIONS</text>
  <text class="meta" x="616" y="338" text-anchor="middle">DAYS</text>
  <text class="meta" x="1158" y="338" text-anchor="end">SOURCE: GITHUB GRAPHQL // SYNCED HOURLY</text>
</svg>
"""


def update_readme_cache_key(contributions: int, lines: int) -> None:
    content = README.read_text(encoding="utf-8")
    updated = content
    assets = {
        "stats.svg": f"{contributions}-{lines}",
        "contribution-graph.svg": f"{contributions}-{GRAPH_STYLE_VERSION}",
    }
    for asset, cache_key in assets.items():
        updated, matches = re.subn(
            rf'(\./assets/{re.escape(asset)})(?:\?v=[^"\s]+)?',
            rf"\1?v={cache_key}",
            updated,
            count=1,
        )
        if matches != 1:
            raise RuntimeError(f"Could not locate {asset} in README.md")
    README.write_text(updated, encoding="utf-8")


def main() -> None:
    now = datetime.now(timezone.utc)
    summary = account_summary()
    author_id = summary.pop("_user_id")
    repositories = summary.pop("_repositories")
    stats = {
        **summary,
        **contribution_stats(summary["created_at"], now),
        "lines_written": lines_written(author_id, repositories),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render_svg(stats, now), encoding="utf-8")
    GRAPH_OUTPUT.write_text(
        render_contribution_graph(stats["days"], now),
        encoding="utf-8",
    )
    update_readme_cache_key(stats["contributions"], stats["lines_written"])
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
