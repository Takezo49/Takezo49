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
from datetime import date, datetime, timedelta, timezone


LOGIN = "Takezo49"
GRAPHQL_URL = "https://api.github.com/graphql"
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
        createdAt
        repositoriesContributedTo(
          first: 1
          includeUserRepositories: true
          contributionTypes: [COMMIT, PULL_REQUEST]
        ) {
          totalCount
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
    return {
        "created_at": datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00")),
        "repositories": user["repositoriesContributedTo"]["totalCount"],
        "stars": sum(repo["stargazerCount"] for repo in user["repositories"]["nodes"]),
    }


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
    ]
    value_columns = "\n".join(
        f"""
        <g transform="translate({430 + index * 185} 94)">
          <text class="label">{html.escape(label)}</text>
          <text class="value" y="42">{value:,}</text>
        </g>
        """
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
      .value {{ font: 700 30px ui-monospace, SFMono-Regular, Menlo, monospace; fill: #f4fffd; }}
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
    start = end - timedelta(days=364)
    # Align the grid to Sunday so weekdays stay in consistent rows.
    display_start = start - timedelta(days=(start.weekday() + 1) % 7)
    week_count = ((end - display_start).days // 7) + 1
    cell = 13
    gap = 6
    pitch = cell + gap
    grid_x = 92
    grid_y = 82

    def color(count: int) -> str:
        if count <= 0:
            return "#0b211e"
        if count <= 2:
            return "#0e4f46"
        if count <= 5:
            return "#118a79"
        if count <= 9:
            return "#19c4b1"
        return "#24ebd9"

    cells: list[str] = []
    cursor = display_start
    while cursor <= end:
        week = (cursor - display_start).days // 7
        weekday = (cursor.weekday() + 1) % 7
        count = days.get(cursor.isoformat(), 0)
        opacity = "0.28" if cursor < start else "1"
        cells.append(
            f'<rect x="{grid_x + week * pitch}" y="{grid_y + weekday * pitch}" '
            f'width="{cell}" height="{cell}" rx="2" fill="{color(count)}" '
            f'opacity="{opacity}"><title>{cursor.isoformat()}: {count} contributions</title></rect>'
        )
        cursor += timedelta(days=1)

    month_labels: list[str] = []
    cursor = date(start.year, start.month, 1)
    if cursor < start:
        cursor = date(
            start.year + (1 if start.month == 12 else 0),
            1 if start.month == 12 else start.month + 1,
            1,
        )
    while cursor <= end:
        week = (cursor - display_start).days // 7
        month_labels.append(
            f'<text class="month" x="{grid_x + week * pitch}" y="67">'
            f"{cursor.strftime('%b')}</text>"
        )
        cursor = date(
            cursor.year + (1 if cursor.month == 12 else 0),
            1 if cursor.month == 12 else cursor.month + 1,
            1,
        )

    rolling_total = sum(
        count
        for day, count in days.items()
        if start <= date.fromisoformat(day) <= end
    )
    grid_width = week_count * pitch
    legend_x = min(1010, grid_x + grid_width - 165)
    legend = "".join(
        f'<rect x="{legend_x + index * 20}" y="228" width="12" height="12" rx="2" fill="{shade}"/>'
        for index, shade in enumerate(
            ["#0b211e", "#0e4f46", "#118a79", "#19c4b1", "#24ebd9"]
        )
    )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="270" viewBox="0 0 1200 270" role="img" aria-label="Live rolling-year contribution activity for {LOGIN}">
  <defs>
    <linearGradient id="panel" x1="0" x2="1">
      <stop offset="0" stop-color="#071513"/>
      <stop offset="1" stop-color="#091b18"/>
    </linearGradient>
    <filter id="glow">
      <feGaussianBlur stdDeviation="4" result="blur"/>
      <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <style>
      .title {{ font: 700 15px ui-monospace, SFMono-Regular, Menlo, monospace; fill: #24ebd9; letter-spacing: 1.5px; }}
      .total {{ font: 700 24px ui-monospace, SFMono-Regular, Menlo, monospace; fill: #f4fffd; }}
      .month, .weekday, .meta {{ font: 500 10px ui-monospace, SFMono-Regular, Menlo, monospace; fill: #75afa8; }}
    </style>
  </defs>
  <rect x="1" y="1" width="1198" height="268" rx="12" fill="url(#panel)" stroke="#1b625a"/>
  <circle cx="41" cy="33" r="5" fill="#24ebd9" filter="url(#glow)"/>
  <text class="title" x="58" y="39">CONTRIBUTION ACTIVITY // ROLLING YEAR</text>
  <text class="total" x="1158" y="39" text-anchor="end">{rolling_total:,}</text>
  <text class="meta" x="1158" y="57" text-anchor="end">ACCOUNT-VISIBLE CONTRIBUTIONS</text>
  {''.join(month_labels)}
  <text class="weekday" x="44" y="{grid_y + pitch + 10}">MON</text>
  <text class="weekday" x="44" y="{grid_y + pitch * 3 + 10}">WED</text>
  <text class="weekday" x="44" y="{grid_y + pitch * 5 + 10}">FRI</text>
  {''.join(cells)}
  <text class="meta" x="42" y="238">SOURCE: GITHUB GRAPHQL // SYNCED HOURLY</text>
  <text class="meta" x="{legend_x - 38}" y="238">LESS</text>
  {legend}
  <text class="meta" x="{legend_x + 105}" y="238">MORE</text>
</svg>
"""


def update_readme_cache_key(contributions: int) -> None:
    content = README.read_text(encoding="utf-8")
    updated = content
    for asset in ("stats.svg", "contribution-graph.svg"):
        updated, matches = re.subn(
            rf"(\./assets/{re.escape(asset)})(?:\?v=\d+)?",
            rf"\1?v={contributions}",
            updated,
            count=1,
        )
        if matches != 1:
            raise RuntimeError(f"Could not locate {asset} in README.md")
    README.write_text(updated, encoding="utf-8")


def main() -> None:
    now = datetime.now(timezone.utc)
    summary = account_summary()
    stats = {
        **summary,
        **contribution_stats(summary["created_at"], now),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render_svg(stats, now), encoding="utf-8")
    GRAPH_OUTPUT.write_text(
        render_contribution_graph(stats["days"], now),
        encoding="utf-8",
    )
    update_readme_cache_key(stats["contributions"])
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
