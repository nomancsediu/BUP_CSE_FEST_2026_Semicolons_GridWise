from models import DirectiveEntry, DirectiveType, StructuredAdjustment

VALID_DIRECTIVE_TYPES = {d.value for d in DirectiveType}


def validate_and_fix(raw_directives: list[dict], num_notes: int, battery_capacity_kwh: float) -> list[DirectiveEntry]:
    """
    Validate LLM output deterministically.
    Fix minor issues where possible, reject invalid ones safely (fallback to no_op).
    Never crash, never invent constraints.
    """
    result = []

    # build a lookup by note_index from LLM output
    by_index = {}
    for item in raw_directives:
        idx = item.get("note_index")
        if isinstance(idx, int) and 0 <= idx < num_notes:
            by_index[idx] = item

    for i in range(num_notes):
        item = by_index.get(i)
        if item is None:
            # LLM missed this note — safe fallback
            result.append(_make_no_op(i, "LLM did not return an entry for this note"))
            continue

        directive_type = item.get("directive_type", "no_op")
        if directive_type not in VALID_DIRECTIVE_TYPES:
            result.append(_make_no_op(i, f"Unsupported directive type: {directive_type}"))
            continue

        applies = item.get("applies", False)
        explanation = str(item.get("explanation", ""))
        raw_adj = item.get("structured_adjustment")

        if directive_type == "no_op":
            result.append(DirectiveEntry(
                note_index=i,
                applies=False,
                directive_type=DirectiveType.no_op,
                structured_adjustment=None,
                explanation=explanation or "This note does not affect today's energy schedule",
            ))
            continue

        # non-no_op must have applies=true and structured_adjustment
        if not applies:
            result.append(_make_no_op(i, "applies was false for a non-no_op directive"))
            continue

        if not isinstance(raw_adj, dict):
            result.append(_make_no_op(i, "structured_adjustment missing or not an object"))
            continue

        validated = _validate_adjustment(directive_type, raw_adj, battery_capacity_kwh)
        if validated is None:
            result.append(_make_no_op(i, f"Invalid structured_adjustment for {directive_type}"))
            continue

        result.append(DirectiveEntry(
            note_index=i,
            applies=True,
            directive_type=DirectiveType(directive_type),
            structured_adjustment=validated,
            explanation=explanation,
        ))

    return result


def _validate_adjustment(directive_type: str, raw_adj: dict, battery_capacity_kwh: float):
    hours = raw_adj.get("hours")
    if not isinstance(hours, list) or len(hours) == 0:
        return None

    # validate and sort hours
    clean_hours = []
    for h in hours:
        if isinstance(h, (int, float)) and 0 <= int(h) <= 23:
            clean_hours.append(int(h))
    clean_hours = sorted(set(clean_hours))
    if not clean_hours:
        return None

    if directive_type == "solar_reduction":
        factor = raw_adj.get("factor")
        if factor is None or not isinstance(factor, (int, float)):
            return None
        factor = float(factor)
        if not (0.0 <= factor <= 1.0):
            return None
        return StructuredAdjustment(hours=clean_hours, factor=factor)

    if directive_type == "minimum_battery_reserve":
        min_kwh = raw_adj.get("minimum_energy_kwh")
        if min_kwh is None or not isinstance(min_kwh, (int, float)):
            return None
        min_kwh = float(min_kwh)
        if min_kwh < 0 or min_kwh > battery_capacity_kwh:
            return None
        return StructuredAdjustment(hours=clean_hours, minimum_energy_kwh=min_kwh)

    if directive_type == "no_charge_window":
        return StructuredAdjustment(hours=clean_hours)

    if directive_type == "no_discharge_window":
        return StructuredAdjustment(hours=clean_hours)

    if directive_type == "max_grid_window":
        max_grid = raw_adj.get("max_grid_kwh")
        if max_grid is None or not isinstance(max_grid, (int, float)):
            return None
        max_grid = float(max_grid)
        if max_grid < 0:
            return None
        return StructuredAdjustment(hours=clean_hours, max_grid_kwh=max_grid)

    return None


def _make_no_op(note_index: int, reason: str) -> DirectiveEntry:
    return DirectiveEntry(
        note_index=note_index,
        applies=False,
        directive_type=DirectiveType.no_op,
        structured_adjustment=None,
        explanation=f"Treated as no_op: {reason}",
    )
