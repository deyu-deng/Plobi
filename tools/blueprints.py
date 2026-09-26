"""Blueprints: shareable plain-language automations layered on skills + cron.

This module is the single home for BOTH blueprint flavors:

1. Skill blueprints — an ordinary skill (a SKILL.md the agent loads) whose
   frontmatter additionally declares an automation schedule:

       metadata:
         plobi:
           blueprint:
             schedule: "0 9 * * *"     # presence of `blueprint:` marks it runnable
             deliver: origin            # optional (default "origin")
             prompt: "..."              # optional task instruction for the run
             no_agent: false            # optional

   Because a blueprint is just a skill, it flows through the ENTIRE existing
   skills-hub pipeline for free — search, inspect, quarantine, security scan,
   install, lock-file provenance, audit log, taps, the centralized index, and
   `plobi skills publish` for sharing. No new source type, no new store, no
   new transport.

2. Parameterized blueprint catalog (merged from the former
   `cron/blueprint_catalog.py`) — a curated in-repo catalog
   (``AutomationBlueprint`` / ``CATALOG``) where a blueprint carries typed
   slots (time / enum / text / weekdays) and a ``schedule_template`` with
   ``{slot}`` placeholders. ``fill_blueprint`` validates user-supplied values
   and turns a blueprint into ``cron.jobs.create_job`` kwargs; renderers emit
   the dashboard form schema, the one-line ``/blueprint`` slash command, and a
   ``plobi://`` deep-link. Both flavors translate into the SAME existing cron
   ``create_job()`` API — there is no second job engine.

The dev guide's "Extend, Don't Duplicate" rule is the whole design: the blueprint
is a skill, the schedule is a cron job, sharing is the existing publish/tap/
index path.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    # skill-frontmatter blueprints (Plobi native)
    "BlueprintSpec",
    "parse_blueprint",
    "blueprint_spec_for_installed",
    "blueprint_to_job_spec",
    "create_blueprint_job",
    "register_blueprint_suggestion",
    "export_blueprint",
    "BlueprintError",
    # parameterized blueprint catalog (merged from cron/blueprint_catalog.py)
    "BlueprintSlot",
    "AutomationBlueprint",
    "CATALOG",
    "get_blueprint",
    "blueprint_form_schema",
    "blueprint_slash_command",
    "blueprint_deeplink",
    "blueprint_catalog_entry",
    "fill_blueprint",
    "BlueprintFillError",
    "WEEKDAY_PRESETS",
]


class BlueprintError(ValueError):
    """Raised when a blueprint block is present but malformed."""


@dataclass
class BlueprintSpec:
    """Parsed ``metadata.plobi.blueprint`` automation spec for a skill."""

    skill_name: str
    schedule: str
    deliver: str = "origin"
    prompt: Optional[str] = None
    no_agent: bool = False
    model: Optional[str] = None
    provider: Optional[str] = None
    enabled_toolsets: Optional[List[str]] = None
    raw: Dict[str, Any] = field(default_factory=dict)


def _split_frontmatter(text: str) -> Optional[Dict[str, Any]]:
    """Return the parsed YAML frontmatter mapping, or None if absent/invalid."""
    if not isinstance(text, str):
        return None
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return None
    # Find the closing fence after the opening one.
    after_open = stripped[3:]
    end = after_open.find("\n---")
    if end == -1:
        return None
    fm_text = after_open[:end]
    try:
        import yaml

        data = yaml.safe_load(fm_text)
    except Exception as e:  # pragma: no cover - malformed YAML
        logger.debug("blueprint: frontmatter YAML parse failed: %s", e)
        return None
    return data if isinstance(data, dict) else None


def parse_blueprint(skill_md_text: str) -> Optional[BlueprintSpec]:
    """Extract a BlueprintSpec from a SKILL.md string, or None if not a blueprint.

    A skill is a blueprint iff ``metadata.plobi.blueprint`` is a mapping containing
    a non-empty ``schedule``. Raises BlueprintError if the block exists but is
    structurally invalid (so a typo surfaces instead of silently no-op'ing).
    """
    fm = _split_frontmatter(skill_md_text)
    if not fm:
        return None

    name = str(fm.get("name", "")).strip()

    meta = fm.get("metadata")
    plobi = meta.get("plobi") if isinstance(meta, dict) else None
    blueprint = plobi.get("blueprint") if isinstance(plobi, dict) else None
    if blueprint is None:
        return None
    if not isinstance(blueprint, dict):
        raise BlueprintError("metadata.plobi.blueprint must be a mapping")

    schedule = str(blueprint.get("schedule", "")).strip()
    if not schedule:
        raise BlueprintError("blueprint.schedule is required and must be non-empty")

    deliver = str(blueprint.get("deliver", "origin")).strip() or "origin"
    prompt = blueprint.get("prompt")
    if prompt is not None:
        prompt = str(prompt)
    no_agent = bool(blueprint.get("no_agent", False))
    model = blueprint.get("model")
    provider = blueprint.get("provider")
    toolsets = blueprint.get("enabled_toolsets")
    if toolsets is not None and not isinstance(toolsets, list):
        raise BlueprintError("blueprint.enabled_toolsets must be a list when present")

    return BlueprintSpec(
        skill_name=name,
        schedule=schedule,
        deliver=deliver,
        prompt=prompt,
        no_agent=no_agent,
        model=str(model).strip() if model else None,
        provider=str(provider).strip() if provider else None,
        enabled_toolsets=[str(t) for t in toolsets] if toolsets else None,
        raw=blueprint,
    )


def blueprint_spec_for_installed(skill_name: str) -> Optional[BlueprintSpec]:
    """Locate an installed skill's SKILL.md and parse its blueprint block.

    Searches the standard skills tree for ``<skill_name>/SKILL.md``. Returns
    None if the skill isn't found or isn't a blueprint.
    """
    try:
        from tools.skills_hub import SKILLS_DIR
    except Exception:  # pragma: no cover - import guard
        return None

    base = Path(SKILLS_DIR)
    # Skills live at skills/<category>/<name>/SKILL.md or skills/<name>/SKILL.md.
    candidates = list(base.glob(f"**/{skill_name}/SKILL.md"))
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        spec = parse_blueprint(text)
        if spec is not None:
            # Prefer the frontmatter name, fall back to the directory name.
            if not spec.skill_name:
                spec.skill_name = skill_name
            return spec
    return None


def blueprint_to_job_spec(
    spec: BlueprintSpec,
    *,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the ``cron.jobs.create_job`` kwargs dict for a BlueprintSpec.

    This is the single source of truth for translating a blueprint into a job.
    Both the direct ``create_blueprint_job`` path and the suggestion path
    (``register_blueprint_suggestion``) build on it, so a blueprint scheduled now and
    a blueprint accepted from a suggestion produce an identical job.
    """
    return {
        "prompt": spec.prompt,
        "schedule": spec.schedule,
        "name": name or f"blueprint:{spec.skill_name}",
        "deliver": spec.deliver,
        "skills": [spec.skill_name] if spec.skill_name else None,
        "model": spec.model,
        "provider": spec.provider,
        "enabled_toolsets": spec.enabled_toolsets,
        "no_agent": spec.no_agent,
    }


def create_blueprint_job(
    spec: BlueprintSpec,
    *,
    origin: Optional[Dict[str, Any]] = None,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """Create the cron job described by a BlueprintSpec via the existing cron API.

    The blueprint's skill is loaded before the run (cron ``skills=[name]``); the
    optional ``prompt`` becomes the task instruction. Delivery, model, and
    toolsets carry through. Returns the created job dict.
    """
    from cron.jobs import create_job

    job_spec = blueprint_to_job_spec(spec, name=name)
    if origin is not None:
        job_spec["origin"] = origin
    return create_job(**job_spec)


def register_blueprint_suggestion(spec: BlueprintSpec) -> Optional[Dict[str, Any]]:
    """Turn an installed blueprint into a pending Suggested Cron Job.

    Blueprints are source ``blueprint`` of the unified suggestion surface: installing
    a skill that carries a ``blueprint:`` block does NOT auto-schedule it — it
    registers a suggestion the user accepts (or dismisses) like any other.
    Returns the suggestion record, or None if it was skipped (already
    seen/dismissed, backlog full, etc.).
    """
    if not spec.skill_name:
        return None
    try:
        from cron.suggestions import add_suggestion
    except Exception:  # pragma: no cover - import guard
        return None

    return add_suggestion(
        title=f"Schedule '{spec.skill_name}'",
        description=(
            f"The '{spec.skill_name}' blueprint runs on schedule {spec.schedule}"
            + (f", delivering to {spec.deliver}" if spec.deliver and spec.deliver != "origin" else "")
            + "."
        ),
        source="blueprint",
        job_spec=blueprint_to_job_spec(spec),
        dedup_key=f"blueprint:{spec.skill_name}:{spec.schedule}",
    )


def export_blueprint(job: Dict[str, Any], body: str, *, blueprint_name: Optional[str] = None) -> str:
    """Render a shareable blueprint SKILL.md from an existing cron job dict.

    The inverse of ``create_blueprint_job``: take a cron job a user already built
    and emit a SKILL.md (with a ``metadata.plobi.blueprint`` block) they can hand
    to ``plobi skills publish`` to share. ``body`` is the plain-language
    description / instructions that become the SKILL.md body.
    """
    import yaml

    name = blueprint_name or job.get("name") or "shared-blueprint"
    # Sanitize to a valid skill identifier.
    name = "".join(c if (c.isalnum() or c in "-_") else "-" for c in str(name).lower())
    name = name.strip("-_") or "shared-blueprint"

    schedule = job.get("schedule_display") or _schedule_to_string(job.get("schedule"))
    skills = job.get("skills") or ([job["skill"]] if job.get("skill") else [])

    blueprint_block: Dict[str, Any] = {"schedule": schedule}
    deliver = job.get("deliver")
    if deliver and deliver != "origin":
        blueprint_block["deliver"] = deliver
    if job.get("prompt"):
        blueprint_block["prompt"] = job["prompt"]
    if job.get("no_agent"):
        blueprint_block["no_agent"] = True
    if job.get("model"):
        blueprint_block["model"] = job["model"]
    if job.get("provider"):
        blueprint_block["provider"] = job["provider"]
    if job.get("enabled_toolsets"):
        blueprint_block["enabled_toolsets"] = job["enabled_toolsets"]

    description = (
        (body.strip().splitlines() or ["Shared automation blueprint."])[0][:200]
        if body.strip()
        else "Shared automation blueprint."
    )

    frontmatter = {
        "name": name,
        "description": description,
        "version": "1.0.0",
        "license": "MIT",
        "metadata": {
            "plobi": {
                "tags": ["blueprint", "automation"],
                "blueprint": blueprint_block,
            }
        },
    }
    fm_yaml = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    body_text = body.strip() or f"# {name}\n\nShared automation blueprint."
    return f"---\n{fm_yaml}\n---\n\n{body_text}\n"


def _schedule_to_string(schedule: Any) -> str:
    """Best-effort render of a parsed schedule dict back to a string."""
    if isinstance(schedule, str):
        return schedule
    if isinstance(schedule, dict):
        kind = schedule.get("kind")
        if kind == "cron" and schedule.get("expr"):
            return str(schedule["expr"])
        if kind == "interval":
            # parse_schedule stores interval periods as "minutes"; tolerate a
            # legacy/foreign "seconds" form too.
            if schedule.get("minutes"):
                mins = int(schedule["minutes"])
                if mins % 60 == 0:
                    return f"every {mins // 60}h"
                return f"every {mins}m"
            if schedule.get("seconds"):
                secs = int(schedule["seconds"])
                if secs % 3600 == 0:
                    return f"every {secs // 3600}h"
                if secs % 60 == 0:
                    return f"every {secs // 60}m"
                return f"every {secs}s"
    return "0 9 * * *"  # safe daily fallback


# ---------------------------------------------------------------------------
# Parameterized automation blueprints (slot catalog + fill)
# Merged from cron/blueprint_catalog.py into the single blueprint module.
# ---------------------------------------------------------------------------

class BlueprintFillError(ValueError):
    """Raised when supplied slot values fail validation."""


# Slot types the renderers understand.
_SLOT_TYPES = frozenset({"time", "enum", "text", "weekdays"})

# Named weekday recurrences -> cron day-of-week field.
WEEKDAY_PRESETS: Dict[str, str] = {
    "everyday": "*",
    "weekdays": "1-5",
    "weekends": "0,6",
}


@dataclass(frozen=True)
class BlueprintSlot:
    """A single fillable field on a blueprint."""

    name: str
    type: str
    label: str
    default: Any = None
    options: tuple = ()       # for type="enum": allowed values
    optional: bool = False
    help: str = ""
    # When False, ``options`` are suggestions rather than a closed set —
    # any value is accepted (e.g. the deliver slot, where the real set of
    # valid platforms depends on the user's configured gateways and is
    # validated downstream by the cron scheduler).
    strict: bool = True

    def __post_init__(self) -> None:
        if self.type not in _SLOT_TYPES:
            raise ValueError(f"unknown slot type {self.type!r} (slot {self.name})")


@dataclass(frozen=True)
class AutomationBlueprint:
    """A parameterized automation blueprint."""

    key: str
    title: str
    description: str
    category: str
    # Cron expression with ``{slot}`` placeholders, e.g. "{minute} {hour} * * {dow}".
    # Placeholders are filled from resolved slot values (time -> minute/hour,
    # weekdays -> dow). A literal cron string with no placeholders = fixed schedule.
    schedule_template: str
    # Seed instruction for the agent / the cron job prompt; may contain {slot}s.
    prompt_template: str
    slots: List[BlueprintSlot] = field(default_factory=list)
    deliver_default: str = "origin"
    skills: tuple = ()        # skills the job loads before running
    tags: tuple = ()


# ---------------------------------------------------------------------------
# Curated in-repo catalog
# ---------------------------------------------------------------------------

_TIME = lambda default="08:00": BlueprintSlot(  # noqa: E731 - concise factory
    name="time", type="time", label="What time?", default=default,
    help="24h local time, e.g. 08:00",
)
_DELIVER = BlueprintSlot(
    name="deliver", type="enum", label="Where to deliver?",
    default="origin", options=("origin", "local", "telegram", "discord", "email"),
    optional=False, strict=False,
    help="origin = the chat you set this up from (or your configured home "
    "channel when created from the dashboard); local = save only, no message; "
    "or any connected platform name",
)


CATALOG: List[AutomationBlueprint] = [
    AutomationBlueprint(
        key="morning-brief",
        title="Morning briefing",
        description="A short daily briefing: today's calendar, weather, and "
        "anything urgent waiting on you.",
        category="daily",
        schedule_template="{minute} {hour} * * *",
        prompt_template=(
            "Produce a concise morning briefing for the user: today's calendar "
            "events, the local weather, and any urgent items. Keep it short and "
            "scannable. If no data sources are connected, give a brief "
            "good-morning with the date and offer to connect calendar/email."
        ),
        slots=[_TIME("08:00"), _DELIVER],
        tags=("daily", "briefing"),
    ),
    AutomationBlueprint(
        key="important-mail",
        title="Important-mail monitor",
        description="Check your inbox periodically and ping you ONLY about mail "
        "that actually needs attention.",
        category="email",
        schedule_template="*/{interval_min} * * * *",
        prompt_template=(
            "Check the user's inbox for new messages since the last run. Surface "
            "ONLY mail matching: {criteria}. Score candidates with the urgency "
            "classifier and deliver only what clears the bar; if nothing does, "
            "respond with [SILENT]. Requires a connected mail source; if none is "
            "configured, explain how to connect one and stop."
        ),
        slots=[
            BlueprintSlot(
                name="interval_min", type="enum", label="How often?",
                default="30", options=("15", "30", "60"),
                help="minutes between checks",
            ),
            BlueprintSlot(
                name="criteria", type="text",
                label="Only notify me if the mail…",
                default="needs a reply today, is from my manager or family, "
                "or mentions a deadline",
            ),
            _DELIVER,
        ],
        tags=("email", "monitor"),
    ),
    AutomationBlueprint(
        key="weekly-review",
        title="Weekly review",
        description="A weekly recap: what got done, what's still open, and "
        "what's coming up.",
        category="weekly",
        schedule_template="{minute} {hour} * * {dow}",
        prompt_template=(
            "Produce a weekly review for the user: what was accomplished this "
            "week, still-open items, and next week's calendar. Pull from "
            "connected sources. Keep it tight."
        ),
        slots=[
            _TIME("18:00"),
            BlueprintSlot(
                name="day", type="enum", label="Which day?",
                default="sunday",
                options=("sunday", "monday", "friday", "saturday"),
            ),
            _DELIVER,
        ],
        tags=("weekly", "review"),
    ),
    AutomationBlueprint(
        key="workday-start",
        title="Workday start reminder",
        description="A weekday nudge with your agenda and top priorities.",
        category="daily",
        schedule_template="{minute} {hour} * * 1-5",
        prompt_template=(
            "Give the user a brief weekday start-of-day nudge: today's calendar "
            "and the 1-3 highest-priority things to focus on, inferred from "
            "recent context and any task tools. Encouraging, short, one message."
        ),
        slots=[_TIME("09:00"), _DELIVER],
        tags=("daily", "focus"),
    ),
    AutomationBlueprint(
        key="custom-reminder",
        title="Custom reminder",
        description="A recurring reminder in your own words, on your schedule.",
        category="general",
        schedule_template="{minute} {hour} * * {dow}",
        prompt_template="Remind the user: {what}",
        slots=[
            BlueprintSlot(name="what", type="text", label="Remind me to…",
                       default="take a break and stretch"),
            _TIME("14:00"),
            BlueprintSlot(
                name="recurrence", type="weekdays", label="Repeat on",
                default="everyday",
                options=tuple(WEEKDAY_PRESETS.keys()),
            ),
            _DELIVER,
        ],
        tags=("reminder",),
    ),
    AutomationBlueprint(
        key="evening-winddown",
        title="Evening wind-down",
        description="An end-of-day check-in: tomorrow's calendar at a glance "
        "and anything you should prep tonight.",
        category="daily",
        schedule_template="{minute} {hour} * * *",
        prompt_template=(
            "Give the user a short evening wind-down: tomorrow's calendar, any "
            "early commitments to prep for, and one gentle nudge to wrap up "
            "loose ends from today. Keep it calm and brief — one message. If no "
            "calendar is connected, just offer a friendly sign-off and the "
            "weather for tomorrow."
        ),
        slots=[_TIME("21:00"), _DELIVER],
        tags=("daily", "evening"),
    ),
    AutomationBlueprint(
        key="news-digest",
        title="Topic news digest",
        description="A recurring digest on a topic you care about — deduped "
        "against what was already sent, so only genuinely new items land.",
        category="general",
        schedule_template="{minute} {hour} * * {dow}",
        prompt_template=(
            "Search the web for new and noteworthy items about: {topic}. "
            "Dedupe against what you sent in previous runs — only include "
            "genuinely new developments. Deliver a tight digest of at most "
            "{count} bullets, each one line with a link. If nothing new since "
            "last run, respond with [SILENT]."
        ),
        slots=[
            BlueprintSlot(
                name="topic", type="text", label="What topic?",
                default="AI and technology",
                help="a subject, product, person, or search phrase",
            ),
            _TIME("18:00"),
            BlueprintSlot(
                name="recurrence", type="weekdays", label="Repeat on",
                default="weekdays",
                options=tuple(WEEKDAY_PRESETS.keys()),
            ),
            BlueprintSlot(
                name="count", type="enum", label="How many bullets?",
                default="5", options=("3", "5", "8"),
            ),
            _DELIVER,
        ],
        tags=("digest", "research"),
    ),
    AutomationBlueprint(
        key="bill-renewal-watch",
        title="Bills & renewals reminder",
        description="A heads-up before a recurring payment, subscription "
        "renewal, or due date — so nothing auto-charges by surprise.",
        category="general",
        schedule_template="{minute} {hour} * * {dow}",
        prompt_template=(
            "Remind the user about an upcoming payment or renewal: {what}. "
            "Phrase it as an actionable heads-up (e.g. 'review or cancel before "
            "it renews'), not just a notification. One short message."
        ),
        slots=[
            BlueprintSlot(
                name="what", type="text", label="What's due?",
                default="my streaming subscription renews soon",
            ),
            _TIME("10:00"),
            BlueprintSlot(
                name="recurrence", type="weekdays", label="Repeat on",
                default="everyday",
                options=tuple(WEEKDAY_PRESETS.keys()),
            ),
            _DELIVER,
        ],
        tags=("reminder", "finance"),
    ),
    AutomationBlueprint(
        key="habit-checkin",
        title="Habit check-in",
        description="A recurring nudge to keep a habit on track and reflect "
        "on whether you did it.",
        category="general",
        schedule_template="{minute} {hour} * * {dow}",
        prompt_template=(
            "Nudge the user about their habit: {habit}. Ask whether they did it "
            "today, keep it warm and non-judgmental, and offer a one-line word "
            "of encouragement. One short message."
        ),
        slots=[
            BlueprintSlot(
                name="habit", type="text", label="Which habit?",
                default="20 minutes of reading",
            ),
            _TIME("20:00"),
            BlueprintSlot(
                name="recurrence", type="weekdays", label="Repeat on",
                default="everyday",
                options=tuple(WEEKDAY_PRESETS.keys()),
            ),
            _DELIVER,
        ],
        tags=("habit", "wellbeing"),
    ),
    AutomationBlueprint(
        key="hydration-move",
        title="Hydration & movement nudge",
        description="A periodic nudge during the day to drink water, stand up, "
        "and stretch.",
        category="general",
        # NOTE: cron minute-field steps (*/90) wrap per hour — */90 and */120
        # both degrade to hourly. Use an hour-field step instead so the chosen
        # cadence is what actually fires.
        schedule_template="0 {start_hour}-{end_hour}/{interval_hours} * * 1-5",
        prompt_template=(
            "Send the user a brief, friendly nudge to drink some water, stand "
            "up, and stretch for a moment. Vary the wording each time so it "
            "doesn't feel robotic. One short line."
        ),
        slots=[
            BlueprintSlot(
                name="interval_hours", type="enum", label="How often?",
                default="1", options=("1", "2", "3"),
                help="hours between nudges",
            ),
            BlueprintSlot(
                name="start_hour", type="enum", label="Start hour",
                default="9", options=("7", "8", "9", "10"),
                help="first hour of the active window (24h)",
            ),
            BlueprintSlot(
                name="end_hour", type="enum", label="End hour",
                default="17", options=("16", "17", "18", "19"),
                help="last hour of the active window (24h)",
            ),
            _DELIVER,
        ],
        tags=("wellbeing", "focus"),
    ),
    AutomationBlueprint(
        key="meal-plan",
        title="Weekly meal plan",
        description="A weekly meal plan plus a consolidated grocery list, "
        "tuned to your diet and how much time you have to cook.",
        category="weekly",
        schedule_template="{minute} {hour} * * {dow}",
        prompt_template=(
            "Build the user a meal plan for the coming week: {meals} per day, "
            "suited to a {diet} diet and roughly {effort} cooking effort. "
            "Include a consolidated grocery list grouped by aisle. Keep blueprints "
            "simple and skimmable."
        ),
        slots=[
            BlueprintSlot(
                name="diet", type="enum", label="Diet?",
                default="no restrictions",
                options=("no restrictions", "vegetarian", "vegan",
                         "high-protein", "low-carb"),
            ),
            BlueprintSlot(
                name="meals", type="enum", label="Meals per day?",
                default="dinner only",
                options=("dinner only", "lunch and dinner", "all three"),
            ),
            BlueprintSlot(
                name="effort", type="enum", label="Cooking effort?",
                default="quick", options=("quick", "medium", "ambitious"),
            ),
            _TIME("17:00"),
            BlueprintSlot(
                name="day", type="enum", label="Which day?",
                default="sunday",
                options=("sunday", "monday", "friday", "saturday"),
            ),
            _DELIVER,
        ],
        tags=("weekly", "food"),
    ),
    AutomationBlueprint(
        key="learn-daily",
        title="Daily learning drip",
        description="One bite-sized lesson a day on a topic you want to learn, "
        "building progressively over time.",
        category="daily",
        schedule_template="{minute} {hour} * * {dow}",
        prompt_template=(
            "Teach the user one bite-sized lesson about: {topic}. Build on "
            "earlier lessons so it progresses rather than repeating. Keep it to "
            "a couple of short paragraphs with one concrete example, and end "
            "with a single question to check understanding."
        ),
        slots=[
            BlueprintSlot(
                name="topic", type="text", label="Learn about…",
                default="Spanish vocabulary",
            ),
            _TIME("08:30"),
            BlueprintSlot(
                name="recurrence", type="weekdays", label="Repeat on",
                default="weekdays",
                options=tuple(WEEKDAY_PRESETS.keys()),
            ),
            _DELIVER,
        ],
        tags=("learning", "daily"),
    ),
    AutomationBlueprint(
        key="gratitude-journal",
        title="Gratitude & reflection prompt",
        description="A gentle evening prompt to reflect on the day and note "
        "what went well.",
        category="general",
        schedule_template="{minute} {hour} * * {dow}",
        prompt_template=(
            "Send the user a short, warm reflection prompt for the end of the "
            "day — invite them to note one thing that went well, one thing they "
            "are grateful for, and one small win. If they reply, acknowledge it "
            "kindly. One message."
        ),
        slots=[
            _TIME("21:30"),
            BlueprintSlot(
                name="recurrence", type="weekdays", label="Repeat on",
                default="everyday",
                options=tuple(WEEKDAY_PRESETS.keys()),
            ),
            _DELIVER,
        ],
        tags=("wellbeing", "reflection"),
    ),
    AutomationBlueprint(
        key="on-this-day",
        title="On-this-day discovery",
        description="A daily dose of curiosity: a notable historical event, "
        "fact, or word for the day.",
        category="daily",
        schedule_template="{minute} {hour} * * *",
        prompt_template=(
            "Give the user one interesting '{flavor}' item for today — keep it "
            "short, surprising, and genuinely interesting. One or two sentences, "
            "no filler."
        ),
        slots=[
            BlueprintSlot(
                name="flavor", type="enum", label="What kind?",
                default="on this day in history",
                options=("on this day in history", "word of the day",
                         "science fact", "quote of the day"),
            ),
            _TIME("07:30"),
            _DELIVER,
        ],
        tags=("daily", "curiosity"),
    ),
]

_CATALOG_BY_KEY = {r.key: r for r in CATALOG}


def get_blueprint(key: str) -> Optional[AutomationBlueprint]:
    return _CATALOG_BY_KEY.get(key)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def blueprint_form_schema(blueprint: AutomationBlueprint) -> Dict[str, Any]:
    """Emit the JSON a form renderer (dashboard / GUI) needs for this blueprint."""
    return {
        "key": blueprint.key,
        "title": blueprint.title,
        "description": blueprint.description,
        "category": blueprint.category,
        "tags": list(blueprint.tags),
        "fields": [
            {
                "name": s.name,
                "type": s.type,
                "label": s.label,
                "default": s.default,
                "options": list(s.options),
                "optional": s.optional,
                "strict": s.strict,
                "help": s.help,
            }
            for s in blueprint.slots
        ],
    }


def blueprint_slash_command(blueprint: AutomationBlueprint, values: Optional[Dict[str, Any]] = None) -> str:
    """Build the flattened ``/blueprint <key> slot=val …`` command string.

    Uses each slot's default when ``values`` is omitted, so the docs/dashboard
    can show a ready-to-paste command. Free-text slots are quoted.
    """
    values = values or {}
    parts = [f"/blueprint {blueprint.key}"]
    for s in blueprint.slots:
        val = values.get(s.name, s.default)
        if val is None or val == "":
            if s.optional:
                continue
            val = ""
        sval = str(val)
        if s.type == "text" or " " in sval:
            sval = '"' + sval.replace('"', '\\"') + '"'
        parts.append(f"{s.name}={sval}")
    return " ".join(parts)


def blueprint_deeplink(blueprint: AutomationBlueprint, values: Optional[Dict[str, Any]] = None) -> str:
    """Build the ``plobi://blueprint/<key>?slot=val`` deep-link URL."""
    from urllib.parse import quote, urlencode

    values = values or {}
    query = {}
    for s in blueprint.slots:
        val = values.get(s.name, s.default)
        if val not in (None, ""):
            query[s.name] = str(val)
    qs = ("?" + urlencode(query)) if query else ""
    return f"plobi://blueprint/{quote(blueprint.key)}{qs}"


def _humanize_schedule(blueprint: AutomationBlueprint) -> str:
    """A short human-readable description of when a blueprint runs (defaults)."""
    sched = blueprint.schedule_template
    if sched.startswith("*/"):
        iv = next((s for s in blueprint.slots if s.name == "interval_min"), None)
        every = (iv.default if iv else None) or sched.split("/")[1].split()[0]
        return f"every {every} minutes"
    if "{interval_hours}" in sched:
        iv = next((s for s in blueprint.slots if s.name == "interval_hours"), None)
        every = str((iv.default if iv else None) or "1")
        scope = "weekdays, " if "* * 1-5" in sched else ""
        return f"{scope}every hour" if every == "1" else f"{scope}every {every} hours"
    time_slot = next((s for s in blueprint.slots if s.type == "time"), None)
    when = time_slot.default if time_slot else None
    if "* * 1-5" in sched:
        return f"weekdays at {when}" if when else "every weekday"
    if "{dow}" in sched:
        day_slot = next((s for s in blueprint.slots if s.name in ("day", "recurrence")), None)
        scope = (day_slot.default if day_slot else "") or ""
        if scope and when:
            return f"{scope} at {when}"
        return f"at {when}" if when else "on a schedule"
    if when:
        return f"daily at {when}"
    return "on a schedule"


def blueprint_catalog_entry(blueprint: AutomationBlueprint) -> Dict[str, Any]:
    """Unified serializable shape for a blueprint — used by the docs generator
    and the dashboard API. Combines the form schema, the ready-to-paste slash
    command, the deep-link URL, and a human-readable schedule.
    """
    return {
        **blueprint_form_schema(blueprint),
        "schedule": blueprint.schedule_template,
        "scheduleHuman": _humanize_schedule(blueprint),
        "command": blueprint_slash_command(blueprint),
        "appUrl": blueprint_deeplink(blueprint),
    }


# ---------------------------------------------------------------------------
# Fill + validate + translate to a create_job spec
# ---------------------------------------------------------------------------

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_DAY_TO_DOW = {
    "sunday": "0", "monday": "1", "tuesday": "2", "wednesday": "3",
    "thursday": "4", "friday": "5", "saturday": "6",
}


def _resolve_schedule(blueprint: AutomationBlueprint, values: Dict[str, Any]) -> str:
    """Fill the schedule_template placeholders from resolved slot values."""
    sched = blueprint.schedule_template

    # A free-text `schedule` slot passes through verbatim (full flexibility).
    if "schedule" in values and values["schedule"]:
        return str(values["schedule"])

    repl: Dict[str, str] = {}

    # time -> minute/hour
    time_val = values.get("time")
    if "{minute}" in sched or "{hour}" in sched:
        if not time_val:
            raise BlueprintFillError("a time is required")
        m = _TIME_RE.match(str(time_val).strip())
        if not m:
            raise BlueprintFillError(f"invalid time {time_val!r} — use HH:MM (24h)")
        repl["hour"] = str(int(m.group(1)))
        repl["minute"] = str(int(m.group(2)))

    # weekday set -> dow
    if "{dow}" in sched:
        if "recurrence" in values:
            preset = str(values.get("recurrence", "everyday")).lower()
            if preset not in WEEKDAY_PRESETS:
                raise BlueprintFillError(
                    f"unknown recurrence {preset!r} — one of {', '.join(WEEKDAY_PRESETS)}"
                )
            repl["dow"] = WEEKDAY_PRESETS[preset]
        elif "day" in values:
            day = str(values.get("day", "")).lower()
            if day not in _DAY_TO_DOW:
                raise BlueprintFillError(f"unknown day {day!r}")
            repl["dow"] = _DAY_TO_DOW[day]
        else:
            repl["dow"] = "*"

    # interval (minutes) for */N schedules
    if "{interval_min}" in sched:
        iv = str(values.get("interval_min", "")).strip()
        if not iv.isdigit() or int(iv) <= 0:
            raise BlueprintFillError(f"invalid interval {iv!r} — minutes as a positive integer")
        repl["interval_min"] = iv

    # Any remaining {slot} placeholders are filled verbatim from validated
    # enum/text slot values (e.g. an hour-range window). Enum options have
    # already been checked in fill_blueprint, so these are safe to interpolate.
    for name in re.findall(r"\{(\w+)\}", sched):
        if name not in repl and name in values:
            repl[name] = str(values[name])

    try:
        return sched.format(**repl)
    except KeyError as e:  # pragma: no cover - template/slot mismatch is a dev error
        raise BlueprintFillError(f"schedule template missing value for {e}") from e


def fill_blueprint(
    blueprint: AutomationBlueprint,
    values: Dict[str, Any],
    *,
    origin: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate ``values`` and return ``cron.jobs.create_job`` kwargs.

    Missing required (non-optional) slots raise BlueprintFillError naming the
    slot, so a form can show field errors and the agent knows what to ask.
    Unknown slot names are rejected (a typo'd ``tiem=07:15`` must not silently
    create a job with the default time). Enum values are checked against their
    options. The result is passed straight to ``create_job`` — no second schema.
    """
    known = {s.name for s in blueprint.slots}
    unknown = sorted(set(values) - known)
    if unknown:
        raise BlueprintFillError(
            f"unknown slot{'s' if len(unknown) > 1 else ''}: "
            f"{', '.join(unknown)} — valid: {', '.join(s.name for s in blueprint.slots)}"
        )
    resolved: Dict[str, Any] = {}
    for s in blueprint.slots:
        raw = values.get(s.name, s.default)
        if raw in (None, ""):
            if s.optional:
                continue
            raise BlueprintFillError(f"missing required value: {s.name} ({s.label})")
        if s.type == "enum" and s.strict and s.options and str(raw) not in {str(o) for o in s.options}:
            raise BlueprintFillError(
                f"{s.name}={raw!r} not allowed — one of {', '.join(map(str, s.options))}"
            )
        resolved[s.name] = raw

    schedule = _resolve_schedule(blueprint, resolved)

    # Render the prompt with whatever slots it references.
    try:
        prompt = blueprint.prompt_template.format(**resolved)
    except KeyError as e:
        raise BlueprintFillError(f"blueprint prompt missing value for {e}") from e

    spec: Dict[str, Any] = {
        "prompt": prompt,
        "schedule": schedule,
        "name": blueprint.title,
        "deliver": resolved.get("deliver", blueprint.deliver_default),
    }
    if blueprint.skills:
        spec["skills"] = list(blueprint.skills)
    if origin is not None:
        spec["origin"] = origin
    return spec
