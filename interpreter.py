import os
import json
from groq import Groq

_client = None

def _get_client():
    global _client
    if _client is None:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY environment variable is not set")
        _client = Groq(api_key=api_key)
    return _client

SYSTEM_PROMPT = """You are an energy management system operator note interpreter.

Your job is to read natural-language operator notes and convert each one into a structured directive JSON object.

## SUPPORTED DIRECTIVE TYPES

1. solar_reduction
   - Meaning: Reduce usable solar to a fraction during specific hours
   - structured_adjustment: {"hours": [...], "factor": <remaining fraction>}
   - IMPORTANT: factor = the REMAINING usable fraction, NOT the reduction amount
   - Example: "80% reduction" → factor = 0.2 (only 20% remains)
   - Example: "solar drops to 25%" → factor = 0.25
   - Example: "half of forecast solar" → factor = 0.5

2. minimum_battery_reserve
   - Meaning: Battery must stay at or above a minimum kWh level during specific hours
   - structured_adjustment: {"hours": [...], "minimum_energy_kwh": <value>}
   - If given as percentage: calculate from battery capacity provided
   - Example: "keep at least 50% of 200kWh battery" → minimum_energy_kwh = 100

3. no_charge_window
   - Meaning: Battery charging is completely unavailable during specific hours
   - structured_adjustment: {"hours": [...]}
   - Example: "charger isolated from 2 AM to 5 AM" → hours [2,3,4]

4. no_discharge_window
   - Meaning: Battery discharging is completely unavailable during specific hours
   - structured_adjustment: {"hours": [...]}
   - Example: "battery must not discharge from 6 PM to 8 PM" → hours [18,19]

5. max_grid_window
   - Meaning: Grid import must not exceed a stated kWh amount during specific hours
   - structured_adjustment: {"hours": [...], "max_grid_kwh": <value>}
   - Example: "grid import must not exceed 155 kWh from 6 PM to 9 PM" → hours [18,19,20], max_grid_kwh=155

6. no_op
   - Meaning: The note is irrelevant to the current 24-hour energy schedule
   - applies: false
   - structured_adjustment: null
   - Use this for notes about future events, administrative matters, unrelated topics

## TIME WINDOW RULES (CRITICAL)
- Time windows are START-INCLUSIVE and END-EXCLUSIVE
- "1 PM to 3 PM" → hours [13, 14]  (NOT 15)
- "6 PM until 9 PM" → hours [18, 19, 20]
- "2 AM until 5 AM" → hours [2, 3, 4]
- "noon until 2 PM" → hours [12, 13]
- "11 AM to 1 PM" → hours [11, 12]
- "5 PM until 7 PM" → hours [17, 18]
- Hours must be unique integers 0-23 in ascending order

## RULES
- Return exactly one entry per operator note, in note_index order (0, 1, 2...)
- For no_op: applies=false, structured_adjustment=null
- For all other directives: applies=true, structured_adjustment must be present
- Do NOT invent directive types not listed above
- Do NOT change demand, tariff, or battery parameters
- Distractors (cafeteria menus, registration deadlines, club notices, seminar bookings, etc.) are always no_op

## OUTPUT FORMAT
Return a JSON array only. No explanation text outside the JSON.
[
  {
    "note_index": 0,
    "applies": true,
    "directive_type": "solar_reduction",
    "structured_adjustment": {"hours": [12, 13], "factor": 0.25},
    "explanation": "Solar reduced to 25% during panel cleaning window noon to 2 PM"
  },
  {
    "note_index": 1,
    "applies": false,
    "directive_type": "no_op",
    "structured_adjustment": null,
    "explanation": "This note does not affect today's energy schedule"
  }
]"""


def interpret_notes(operator_notes: list[str], battery_capacity_kwh: float) -> list[dict]:
    notes_text = "\n".join(
        f"{i}: \"{note}\"" for i, note in enumerate(operator_notes)
    )

    user_message = f"""Battery capacity: {battery_capacity_kwh} kWh

Operator notes to interpret:
{notes_text}

Return a JSON array with exactly {len(operator_notes)} entries, one per note in order."""

    response = _get_client().chat.completions.create(
        model="qwen/qwen3.8-27b",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=0.0,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content.strip()

    parsed = json.loads(raw)

    # handle all possible shapes the LLM might return
    if isinstance(parsed, list):
        return parsed

    if isinstance(parsed, dict):
        # check common wrapper keys
        for key in ("directives", "interpretations", "result", "notes", "items", "data"):
            if key in parsed and isinstance(parsed[key], list):
                return parsed[key]
        # if it looks like a single directive object, wrap it
        if "note_index" in parsed:
            return [parsed]
        # take first list value found
        for val in parsed.values():
            if isinstance(val, list):
                return val
        raise ValueError(f"Unexpected LLM JSON shape: {list(parsed.keys())}")

    raise ValueError("LLM did not return a JSON array or object with array")
