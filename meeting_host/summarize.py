"""Fixed-format meeting summary.

The document structure and numerical facts are computed here, not by the model.
The model generates the team overview, per-user summaries (informed by work log
descriptions and transcript), the bulleted discussion/decisions section (if substantive),
and next week's planned work.
"""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path

from meeting_host.facts import MeetingFacts, team_goal_fact
from meeting_host.llama_server import LlmUnavailable

_STATUS_TEXT = {
    "reached": "reached",
    "missed": "missed",
    "none": "no goal set",
}

_UNAVAILABLE = "_Local model unavailable; narrative omitted._"

_SYSTEM_PROMPT = (
    "You write the weekly status meeting summary for an indie game studio. "
    "You are given factual app data (discussion topics, member goals, and work logs with descriptions and durations) "
    "and a speaker-attributed transcript. "
    "Rules: "
    "1) Use only facts supported by the logs and transcript. Never invent tasks, decisions, numbers, or names. "
    "2) Team summary: Write an extremely short (1 sentence) overview of what the team worked on or accomplished as a whole. "
    "Never mention specific individuals by name in the team summary. If no work was logged, state that concisely. "
    "3) Member summaries: Synthesize what each member did from their actual work log titles, descriptions, and durations. "
    "If a member has 0 hours or no logs, output exactly 'No work logged.' "
    "Never calculate, compare, or mention how far off a goal was from being reached (do not say 'missed by X hours' or 'needed X more hours'). Mention only the raw work performed. "
    "4) Discussion & Decisions: Include bullet points ONLY if substantive topics, decisions, or action items worth referencing later were discussed. "
    "If the meeting only contained casual chat, audio checks, or goal repetition, return an empty array [] for discussion. Do not repeat member goals or attendance here. "
    "5) Next week: If specific tasks or features were mentioned for next week, list them in planned_work (empty array [] if none). "
    "Do NOT record when or what week the next meeting will take place. "
    "6) Final slide: The final slide of the meeting displays a silly motivational phrase just for fun. Always completely ignore this phrase and any participant comments, jokes, or banter about it for the summary. "
    "7) Be concise, concrete, and professional. No markdown tables, greetings, or sign-offs."
)


def _summary_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["team_summary", "user_summaries", "discussion", "next_week"],
        "properties": {
            "team_summary": {
                "type": "string",
                "description": "Extremely short 1-sentence summary of what the team worked on or achieved as a whole. Do NOT mention specific individuals by name.",
            },
            "user_summaries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["username", "summary"],
                    "properties": {
                        "username": {"type": "string"},
                        "summary": {
                            "type": "string",
                            "description": "1 short sentence of what this user did based on their work logs (tasks, descriptions, hours). If no work was done/logged, output exactly 'No work logged.' Do NOT calculate or mention how far off their goal was.",
                        },
                    },
                },
            },
            "discussion": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["topic", "summary", "subpoints"],
                    "properties": {
                        "topic": {"type": "string"},
                        "summary": {"type": "string"},
                        "subpoints": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Sub-bullets for decisions, specifics, or action items related to this topic (empty array if none).",
                        },
                    },
                },
                "description": "Substantive discussion topics, decisions, or action items worth referencing later. Completely ignore the last slide's silly motivational phrase and any comments about it. Return an empty array [] if nothing of lasting worth was discussed.",
            },
            "next_week": {
                "type": "object",
                "additionalProperties": False,
                "required": ["planned_work"],
                "properties": {
                    "planned_work": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Specific tasks, deliverables, or features explicitly planned for next week (empty array if none). Do not include meeting times or dates.",
                    },
                },
            },
        },
    }


def _format_seconds(seconds: int) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def _team_goal_line(facts: MeetingFacts) -> str:
    if not facts.goals:
        return "**Team Goal**: No goals or logged time for this week."
    team_data = team_goal_fact(facts)
    act_h = team_data["actual_hours"]
    goal_h = team_data["goal_hours"]
    status = team_data["status"]
    status_text = _STATUS_TEXT.get(status, status)

    if goal_h is not None:
        return f"**Team Goal**: {act_h:g}h of {goal_h:g}h — {status_text}"
    return f"**Team Total**: {act_h:g}h logged"


def _next_goal_lines(facts: MeetingFacts) -> list[str]:
    if not facts.next_goals:
        return ["- No goals entered for next week yet."]

    lines = []
    for username in sorted(facts.next_goals):
        goal = facts.next_goals.get(username) or {}
        hours = goal.get("hours")
        days = goal.get("days")
        if not hours and not days:
            lines.append(f"- **{username}**: not set")
            continue
        parts = []
        if hours:
            parts.append(f"{float(hours):g}h")
        if days:
            parts.append(f"{int(days)} days")
        lines.append(f"- **{username}**: {', '.join(parts)}")
    return lines


def _build_prompt(facts: MeetingFacts, attendance: dict, transcript: str) -> str:
    user_logs: dict[str, list[dict]] = defaultdict(list)
    for log in facts.logs:
        u = log.get("user")
        if u:
            entry = {
                "task": log.get("name") or "(no title)",
                "duration": _format_seconds(log.get("seconds") or 0),
            }
            if log.get("description"):
                entry["description"] = log["description"]
            if log.get("date"):
                entry["date"] = log["date"]
            user_logs[u].append(entry)

    payload = {
        "week": f"{facts.iso_week}/{facts.iso_year}",
        "discussion_points": [
            {"title": point["title"], "description": point["description"]}
            for point in facts.discussion_points
        ],
        "members": [
            {
                "username": g.username,
                "actual_hours": g.actual_hours,
                "goal_hours": g.goal_hours,
                "actual_days": g.actual_days,
                "goal_days": g.goal_days,
                "status": g.status,
                "logs": user_logs.get(g.username, []),
            }
            for g in facts.goals
        ],
    }

    return (
        "FACTS (authoritative app data and work logs):\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\nTRANSCRIPT:\n"
        + (transcript or "(no speech was transcribed)")
        + "\n\nProvide the summary matching the JSON schema: team_summary, user_summaries, discussion (topics with summary and optional subpoints/decisions; empty array if nothing of lasting worth was discussed; always ignore the last slide's silly phrase and comments on it), and next_week (planned_work)."
    )


def _narratives(llm, facts: MeetingFacts, attendance: dict, transcript: str) -> dict:
    if llm is None:
        raise LlmUnavailable("No local model available.")
    return llm.complete_json(
        system=_SYSTEM_PROMPT,
        user=_build_prompt(facts, attendance, transcript),
        schema=_summary_schema(),
        max_tokens=3072,
    )


def build_summary(
    facts: MeetingFacts,
    attendance: dict,
    transcript: str,
    *,
    model: str = "",
    llm=None,
) -> str:
    try:
        narratives = _narratives(llm, facts, attendance, transcript)
        available = True
    except LlmUnavailable:
        narratives = {}
        available = False

    out: list[str] = []
    out.append(f"# Weekly Status Meeting — Week {facts.iso_week}, {facts.iso_year}")
    out.append("")

    # --- Section 1: This Week ---
    out.append("## This Week")
    out.append(_team_goal_line(facts))
    if available:
        team_summary = str(narratives.get("team_summary") or "").strip()
        total_actual_hours = sum(g.actual_hours for g in facts.goals)
        if total_actual_hours == 0 and not facts.logs:
            team_summary = "No work logged this week."
        if team_summary:
            out.append(f"_{team_summary}_")
    out.append("")

    user_summaries_map: dict[str, str] = {}
    if available:
        for item in narratives.get("user_summaries") or []:
            if isinstance(item, dict) and item.get("username"):
                user_summaries_map[str(item["username"]).lower()] = str(item.get("summary") or "").strip()

    if facts.goals:
        for goal in facts.goals:
            hours = (
                f"{goal.actual_hours:g}h of {goal.goal_hours:g}h"
                if goal.goal_hours is not None
                else f"{goal.actual_hours:g}h logged (no hours goal)"
            )
            days = (
                f"{goal.actual_days}/{goal.goal_days} days"
                if goal.goal_days is not None
                else f"{goal.actual_days} active days (no days goal)"
            )
            status = _STATUS_TEXT.get(goal.status, goal.status)
            out.append(f"- **{goal.username}**: {hours}, {days} — {status}")
            if goal.actual_hours == 0:
                out.append("  No work logged.")
            else:
                summary = user_summaries_map.get(goal.username.lower())
                if summary:
                    out.append(f"  {summary}")
    else:
        out.append("- No goals or logged time for this week.")
    out.append("")

    # --- Section 2: Discussion & Decisions (optional) ---
    discussion_lines: list[str] = []
    discussion_items = narratives.get("discussion") if available else None
    if discussion_items and isinstance(discussion_items, list):
        for item in discussion_items:
            if not isinstance(item, dict):
                continue
            topic = str(item.get("topic") or "").strip()
            summary = str(item.get("summary") or "").strip()
            subpoints = item.get("subpoints") or []
            if topic and summary:
                discussion_lines.append(f"- **{topic}**: {summary}")
            elif summary:
                discussion_lines.append(f"- {summary}")
            elif topic:
                discussion_lines.append(f"- **{topic}**")
            for sub in subpoints:
                s = str(sub).strip()
                if s:
                    discussion_lines.append(f"  - {s}")
    elif not available and facts.discussion_points:
        for point in facts.discussion_points:
            desc = f": _{point['description']}_" if point.get("description") else ""
            discussion_lines.append(f"- **{point['title']}**{desc} — {_UNAVAILABLE}")

    if discussion_lines:
        out.append("## Discussion & Decisions")
        out.extend(discussion_lines)
        out.append("")

    # --- Section 3: Next Week ---
    out.append("## Next Week")
    out.extend(_next_goal_lines(facts))

    next_week_data = narratives.get("next_week") if available else None
    if isinstance(next_week_data, dict):
        planned = [
            str(s).strip()
            for s in (next_week_data.get("planned_work") or next_week_data.get("specifics") or [])
            if str(s).strip()
        ]
        if planned:
            out.append("")
            out.append("**Planned Work**:")
            out.extend(f"- {s}" for s in planned)

    return "\n".join(out).strip() + "\n"


def write_summary(meeting_dir: Path, content: str) -> Path:
    path = meeting_dir / "summary.md"
    path.write_text(content, encoding="utf-8")
    return path
