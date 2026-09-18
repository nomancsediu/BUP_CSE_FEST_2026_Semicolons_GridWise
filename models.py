from pydantic import BaseModel, field_validator, model_validator
from typing import Optional, Literal
from enum import Enum


class HourEntry(BaseModel):
    hour: int
    demand_kwh: float
    solar_kwh: float
    tariff_bdt_per_kwh: float


class BatterySpec(BaseModel):
    capacity_kwh: float
    initial_energy_kwh: float
    minimum_energy_kwh: float
    max_charge_kwh_per_hour: float
    max_discharge_kwh_per_hour: float


class OptimizeRequest(BaseModel):
    scenario_id: str
    operator_notes: list[str]
    hours: list[HourEntry]
    battery: BatterySpec

    @field_validator("operator_notes")
    @classmethod
    def validate_notes(cls, v):
        if not (1 <= len(v) <= 3):
            raise ValueError("operator_notes must have 1 to 3 entries")
        for note in v:
            if not note.strip():
                raise ValueError("operator_notes entries must be non-empty")
        return v

    @field_validator("hours")
    @classmethod
    def validate_hours(cls, v):
        if len(v) != 24:
            raise ValueError("hours must contain exactly 24 entries")
        hour_vals = [h.hour for h in v]
        if sorted(hour_vals) != list(range(24)):
            raise ValueError("hours must contain unique integers 0 through 23")
        return sorted(v, key=lambda h: h.hour)


class DirectiveType(str, Enum):
    solar_reduction = "solar_reduction"
    minimum_battery_reserve = "minimum_battery_reserve"
    no_charge_window = "no_charge_window"
    no_discharge_window = "no_discharge_window"
    max_grid_window = "max_grid_window"
    no_op = "no_op"


class StructuredAdjustment(BaseModel):
    hours: Optional[list[int]] = None
    factor: Optional[float] = None
    minimum_energy_kwh: Optional[float] = None
    max_grid_kwh: Optional[float] = None


class DirectiveEntry(BaseModel):
    note_index: int
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: Optional[StructuredAdjustment]
    explanation: str


class HourlyPlanEntry(BaseModel):
    hour: int
    grid_kwh: float
    solar_used_kwh: float
    battery_action: Literal["charge", "discharge", "idle"]
    battery_kwh: float
    battery_energy_after_kwh: float


class OptimizeResponse(BaseModel):
    scenario_id: str
    directive_interpretation: list[DirectiveEntry]
    hourly_plan: list[HourlyPlanEntry]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str
