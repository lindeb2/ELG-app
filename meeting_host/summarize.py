"""Fixed-format meeting summary.

Factual sections (attendance, goal outcomes, next-week goals) are rendered
from data. The model only writes the prose under those headings, and if it is
unavailable the file is still produced with the facts intact.
"""
from __future__ import annotations

import json
from pathlib import Path

from meeting_host.facts import MeetingFacts
from meeting_host.llama_server import LlmUnavailable

_STATUS_TEXT = {
    "reached": "reached",
    "missed": "missed",
    "none": "no goal set",
}

_NOT_DISCUSSED = "Not discussed."
_UNAVAILABLE = "_Local model unavailable; transcript saved without narrative._"

_SYSTEM_PROMPT = (
    "You write the narrative parts of a weekly status meeting summary for a small "
    "game studio. You are given a speaker-attributed transcript and the verified "
    "facts for the week. Rules: "
    "1) Use only what the transcript supports. Never invent decisions, numbers or names. "
    "2) Never contradict the given facts; whether a goal was reached is already decided. "
    "3) If a topic was not discussed, output exactly \"Not discussed.\" for it. "
    "4) Be concise and concrete. No greetings, no sign-offs, no headings, no markdown tables."
)


def _summary_schema(point_count: int) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["goals_narrative", "logs_narrative", "points", "next_week_narrative", "actions"],
        "properties": {
            "goals_narrative": {"type": "string"},
            "logs_narrative": {"type": "string"},
            "points": {
                "type": "array",
                "maxItems": max(1, point_count),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["index", "notes"],
                    "properties": {
                        "index": {"type": "integer"},
                        "notes": {"type": "string"},
                    },
                },
            },
            "next_week_narrative": {"type": "string"},
            "actions": {"type": "array", "items": {"type": "string"}},
        },
    }


def _format_seconds(seconds: int) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def _attendance_lines(attendance: dict) -> list[str]:
    participants = attendance.get("participants") or []
    if not participants:
        return ["- No participants were recorded."]

    lines = []
    for participant in participants:
        name = (
            participant.get("elg_username")
            or participant.get("discord_nick")
            or participant.get("discord_global_name")
            or participant.get("discord_username")
            or participant.get("discord_user_id")
        )
        suffix = ""
        if participant.get("joined_late"):
            suffix = " (joined late)"
        if not participant.get("elg_username"):
            suffix += " (unlinked Discord account)"
        lines.append(f"- {name}{suffix}")
    return lines


def _goal_lines(facts: MeetingFacts) -> list[str]:
    if not facts.goals:
        return ["- No goals or logged time for this week."]

    lines = []
    for goal in facts.goals:
        hours = (
            f"{goal.actual_hours:g}h of {goal.goal_hours:g}h"
            if goal.goal_hours
            else f"{goal.actual_hours:g}h logged (no hours goal)"
        )
        days = (
            f"{goal.actual_days}/{goal.goal_days} days"
            if goal.goal_days
            else f"{goal.actual_days} active days (no days goal)"
        )
        status = _STATUS_TEXT.get(goal.status, goal.status)
        lines.append(f"- **{goal.username}**: {hours}, {days} — {status}")
    return lines


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


def _log_lines(facts: MeetingFacts) -> list[str]:
    if not facts.logs:
        return []
    lines = []
    for row in facts.logs[:40]:
        label = row["name"] or "(no title)"
        lines.append(f"- {row['user']} · {row['date']} · {_format_seconds(row['seconds'])} · {label}")
    if len(facts.logs) > 40:
        lines.append(f"- ...and {len(facts.logs) - 40} more entries")
    return lines


def _build_prompt(facts: MeetingFacts, attendance: dict, transcript: str) -> str:
    payload = {
        "week": f"{facts.iso_week}/{facts.iso_year}",
        "verified_goal_facts": [
            {
                "user": goal.username,
                "goal_hours": goal.goal_hours,
                "goal_days": goal.goal_days,
                "actual_hours": goal.actual_hours,
                "actual_days": goal.actual_days,
                "outcome": goal.status,
            }
            for goal in facts.goals
        ],
        "next_week_goals": facts.next_goals,
        "discussion_points": [
            {"index": index, "title": point["title"], "description": point["description"]}
            for index, point in enumerate(facts.discussion_points)
        ],
        "attendance": [
            {
                "name": participant.get("elg_username")
                or participant.get("discord_global_name")
                or participant.get("discord_username"),
                "joined_late": bool(participant.get("joined_late")),
            }
            for participant in (attendance.get("participants") or [])
        ],
    }

    return (
        "FACTS (authoritative):\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\nTRANSCRIPT:\n"
        + (transcript or "(no speech was transcribed)")
        + "\n\nWrite: goals_narrative (2-6 sentences on what was said about goals), "
        "logs_narrative (only if logged work was discussed, else \"Not discussed.\"), "
        "points (notes per discussion point index, \"Not discussed.\" when absent), "
        "next_week_narrative (1-4 sentences, else \"Not discussed.\"), "
        "actions (decisions and action items as short bullets, empty list if none)."
    )


def _narratives(llm, facts: MeetingFacts, attendance: dict, transcript: str) -> dict:
    if llm is None:
        raise LlmUnavailable("No local model available.")
    return llm.complete_json(
        system=_SYSTEM_PROMPT,
        user=_build_prompt(facts, attendance, transcript),
        schema=_summary_schema(len(facts.discussion_points)),
        max_tokens=2048,
    )


def build_summary(
    facts: MeetingFacts,
    attendance: dict,
    transcript: str,
    *,
    llm=None,
) -> str:
    try:
        narratives = _narratives(llm, facts, attendance, transcript)
        available = True
    except LlmUnavailable:
        narratives = {}
        available = False

    def narrative(key: str, fallback: str) -> str:
        if not available:
            return _UNAVAILABLE
        value = str(narratives.get(key) or "").strip()
        return value or fallback

    point_notes: dict[int, str] = {}
    if available:
        for entry in narratives.get("points") or []:
            if not isinstance(entry, dict):
                continue
            try:
                index = int(entry.get("index"))
            except (TypeError, ValueError):
                continue
            notes = str(entry.get("notes") or "").strip()
            if notes:
                point_notes[index] = notes

    out: list[str] = []
    out.append(f"# Weekly Status Meeting — Week {facts.iso_week}, {facts.iso_year}")
    out.append("")

    out.append("## Attendance")
    out.extend(_attendance_lines(attendance))
    out.append("")

    out.append("## This week's goals")
    out.extend(_goal_lines(facts))
    out.append("")
    out.append(narrative("goals_narrative", _NOT_DISCUSSED))
    out.append("")

    out.append("## Logs")
    log_lines = _log_lines(facts)
    if log_lines:
        out.extend(log_lines)
        out.append("")
    out.append(narrative("logs_narrative", _NOT_DISCUSSED))
    out.append("")

    out.append("## Discussion points")
    if facts.discussion_points:
        for index, point in enumerate(facts.discussion_points):
            out.append(f"### {point['title']}")
            if point["description"]:
                out.append(f"_{point['description']}_")
            out.append(point_notes.get(index, _UNAVAILABLE if not available else _NOT_DISCUSSED))
            out.append("")
    else:
        out.append("No discussion points were registered for this week.")
        out.append("")

    out.append("## Next week's goals")
    out.extend(_next_goal_lines(facts))
    out.append("")
    out.append(narrative("next_week_narrative", _NOT_DISCUSSED))
    out.append("")

    out.append("## Decisions and action items")
    actions = [str(item).strip() for item in (narratives.get("actions") or []) if str(item).strip()]
    if not available:
        out.append(_UNAVAILABLE)
    elif actions:
        out.extend(f"- {action}" for action in actions)
    else:
        out.append("None.")
    out.append("")

    return "\n".join(out)


def write_summary(meeting_dir: Path, content: str) -> Path:
    path = meeting_dir / "summary.md"
    path.write_text(content, encoding="utf-8")
    return path
