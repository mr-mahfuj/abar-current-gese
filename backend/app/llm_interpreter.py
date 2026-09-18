from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)
DEFAULT_MODEL = "gemini-3.8-flash"

SYSTEM_PROMPT = """You interpret energy operator notes into exactly one structured directive per note.
Use only: solar_reduction {hours: integer list, factor: remaining usable fraction 0..1},
minimum_battery_reserve {hours: integer list, minimum_energy_kwh: number},
no_charge_window {hours: integer list}, no_discharge_window {hours: integer list},
max_grid_window {hours: integer list, max_grid_kwh: number}, and no_op null.
Hours are start-inclusive and end-exclusive: 1 PM to 3 PM means [13, 14].
Hours must be unique, ascending, and 0..23. An 80 percent reduction means factor 0.2.
Every input note gets one entry in order. Ambiguous, irrelevant, or unsupported notes are no_op.
For no_op use applies=false and null. For all other directives use applies=true.
Return only JSON matching the supplied schema."""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "directive_interpretation": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "note_index": {"type": "integer"},
                    "applies": {"type": "boolean"},
                    "directive_type": {
                        "type": "string",
                        "enum": [
                            "solar_reduction", "minimum_battery_reserve", "no_charge_window",
                            "no_discharge_window", "max_grid_window", "no_op",
                        ],
                    },
                    "structured_adjustment": {"type": ["object", "null"]},
                    "explanation": {"type": "string"},
                },
                "required": ["note_index", "applies", "directive_type", "structured_adjustment", "explanation"],
            },
        }
    },
    "required": ["directive_interpretation"],
}


def _fallback(note: str, index: int, battery_capacity: float | None = None) -> dict:
    text = note.lower()
    noop = {"note_index": index, "applies": False, "directive_type": "no_op", "structured_adjustment": None,
            "explanation": "No supported schedule change was identified."}
    if not any(token in text for token in ("solar", "pv", "panel", "cloud", "battery", "charge", "charging", "discharge", "grid", "reserve", "feeder", "transformer", "substation")):
        return noop
    hours = _extract_hours(text)
    if not hours:
        return noop
    if (
        ("charging circuit" in text or "charging-circuit" in text) and "unavailable" in text
    ) or re.search(r"(?:do not|don't|avoid|stop|cannot|can't|must not)\s+(?:charge|charging)\b", text):
        return _directive(index, "no_charge_window", {"hours": hours}, "Charging is unavailable in the stated window.")
    if any(token in text for token in ("solar", "pv", "panel", "cloud")):
        factor = _extract_remaining_factor(text)
        if factor is None and "half" in text:
            factor = 0.5
        if factor is not None:
            return _directive(index, "solar_reduction", {"hours": hours, "factor": round(factor, 6)}, "Solar availability is reduced in the stated window.")
    if (
        ("no charge" in text or "not charge" in text or "charging" in text or "charger" in text)
        and any(token in text for token in ("stop", "unavailable", "without", "avoid", "disabled", "isolated", "maintenance", "inspection"))
    ):
        return _directive(index, "no_charge_window", {"hours": hours}, "Charging is unavailable in the stated window.")
    if (
        "no discharge" in text
        or "not discharge" in text
        or re.search(r"(?:do not|don't|avoid|stop|cannot|can't|must not)\s+(?:discharge|discharging)\b", text)
        or ("discharge" in text and any(token in text for token in ("unavailable", "disabled", "isolated", "maintenance")))
    ):
        return _directive(index, "no_discharge_window", {"hours": hours}, "Discharging is unavailable in the stated window.")
    if "reserve" in text or (any(token in text for token in ("battery", "stored energy")) and any(token in text for token in ("at least", "above", "minimum", "maintain", "keep", "hold"))):
        value = _number_before_kwh(text)
        if value is None and battery_capacity is not None:
            percentage = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
            if percentage:
                value = battery_capacity * float(percentage.group(1)) / 100
        if value is not None:
            return _directive(index, "minimum_battery_reserve", {"hours": hours, "minimum_energy_kwh": value}, "Battery reserve is raised in the stated window.")
    if any(token in text for token in ("grid", "intake", "feeder", "transformer", "substation")) and any(token in text for token in ("maximum", "max", "limit", "cap", "not exceed", "at or below", "must stay", "under", "below")):
        value = _number_before_kwh(text)
        if value is not None:
            return _directive(index, "max_grid_window", {"hours": hours, "max_grid_kwh": value}, "Grid import is capped in the stated window.")
    return noop


def _directive(index: int, dtype: str, adjustment: dict, explanation: str) -> dict:
    return {"note_index": index, "applies": True, "directive_type": dtype,
            "structured_adjustment": adjustment, "explanation": explanation}


def _extract_hours(text: str) -> list[int] | None:
    text = re.sub(r"\bmidnight\b", "12 am", text)
    text = re.sub(r"\bnoon\b", "12 pm", text)
    match = re.search(r"(?:from|between)\s+(.{1,80})", text)
    if not match:
        match = re.search(r"(?:during|over|until)\s+(.{1,80})", text)
    window = match.group(1) if match else text
    times = re.findall(r"(?<!\d)(\d{1,2})(?::\d{2})?\s*(am|pm)?", window)
    if len(times) < 2:
        times = re.findall(r"(?<!\d)(\d{1,2})(?::\d{2})?\s*(am|pm)?", text)
    if len(times) < 2:
        return None
    parsed = []
    for number, meridiem in times[:2]:
        hour = int(number)
        if meridiem.lower() == "pm" and hour != 12:
            hour += 12
        if meridiem.lower() == "am" and hour == 12:
            hour = 0
        parsed.append(hour)
    start, end = parsed
    if end <= start:
        return None
    return list(range(start, end))


def _extract_remaining_factor(text: str) -> float | None:
    percent = re.search(r"(?:about|roughly|approximately|to|at|around)\s*(\d+(?:\.\d+)?)\s*(?:%|percent)(?!\w)", text)
    if percent:
        return float(percent.group(1)) / 100
    reduction = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent)(?!\w)\s*(?:reduction|drop|decrease)", text)
    if reduction:
        return 1 - float(reduction.group(1)) / 100
    if "one-fifth" in text or "one fifth" in text:
        return 0.2
    if "one-quarter" in text or "one quarter" in text:
        return 0.25
    return None


def _number_before_kwh(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*kwh", text)
    return float(match.group(1)) if match else None


def _fallback_all(notes: list[str], battery_capacity: float | None = None) -> list[dict]:
    return [_fallback(note, index, battery_capacity) for index, note in enumerate(notes)]


def interpret_notes(operator_notes: list[str], battery_capacity: float | None = None) -> list[dict]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return _fallback_all(operator_notes, battery_capacity)
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        model = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
        contents = "Operator notes:\n" + "\n".join(f"{i}: {note}" for i, note in enumerate(operator_notes))
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        response_schema=RESPONSE_SCHEMA,
                        temperature=0,
                    ),
                )
                parsed: Any = getattr(response, "parsed", None)
                if parsed is None:
                    parsed = json.loads(response.text)
                if isinstance(parsed, dict):
                    parsed = parsed.get("directive_interpretation", [])
                return parsed
            except Exception:
                if attempt == 1:
                    logger.warning("Gemini interpretation failed; using safe fallback", exc_info=False)
    except Exception:
        logger.warning("Gemini client unavailable; using safe fallback", exc_info=False)
    return _fallback_all(operator_notes, battery_capacity)
