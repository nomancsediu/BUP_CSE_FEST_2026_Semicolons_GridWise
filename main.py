import os
import traceback
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from models import OptimizeRequest, OptimizeResponse
from interpreter import interpret_notes
from guardrails import validate_and_fix
from optimizer import optimize

app = FastAPI(title="GridWise LLM Energy Optimizer")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/optimize-energy", response_model=OptimizeResponse)
async def optimize_energy(request: OptimizeRequest):
    # ── Step 1: LLM interpretation ──────────────────────────────────────────
    try:
        raw_directives = interpret_notes(
            request.operator_notes,
            request.battery.capacity_kwh,
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={
            "error": "LLM interpretation failed",
            "detail": str(e),
        })

    # ── Step 2: Deterministic guardrail validation ──────────────────────────
    try:
        validated_directives = validate_and_fix(
            raw_directives,
            len(request.operator_notes),
            request.battery.capacity_kwh,
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={
            "error": "Guardrail validation failed",
            "detail": str(e),
        })

    # ── Step 3: Optimize 24-hour schedule ───────────────────────────────────
    try:
        hourly_plan = optimize(
            request.hours,
            request.battery,
            validated_directives,
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={
            "error": "Optimization failed",
            "detail": str(e),
        })

    # ── Step 4: Compute totals from hourly_plan ─────────────────────────────
    tariff_map = {h.hour: h.tariff_bdt_per_kwh for h in request.hours}

    total_grid_kwh = round(sum(e.grid_kwh for e in hourly_plan), 4)
    total_cost_bdt = round(
        sum(e.grid_kwh * tariff_map[e.hour] for e in hourly_plan), 4
    )
    peak_grid_kwh = round(max(e.grid_kwh for e in hourly_plan), 4)

    # ── Step 5: Build plan summary ──────────────────────────────────────────
    applied = [d for d in validated_directives if d.applies]
    if applied:
        directive_names = ", ".join(d.directive_type.value for d in applied)
        summary = (
            f"Applied directives: {directive_names}. "
            f"Optimized 24-hour schedule minimizing grid cost. "
            f"Total grid: {total_grid_kwh} kWh, "
            f"Total cost: {total_cost_bdt} BDT, "
            f"Peak grid: {peak_grid_kwh} kWh."
        )
    else:
        summary = (
            f"No applicable directives. "
            f"Optimized 24-hour schedule minimizing grid cost. "
            f"Total grid: {total_grid_kwh} kWh, "
            f"Total cost: {total_cost_bdt} BDT, "
            f"Peak grid: {peak_grid_kwh} kWh."
        )

    return OptimizeResponse(
        scenario_id=request.scenario_id,
        directive_interpretation=validated_directives,
        hourly_plan=hourly_plan,
        total_grid_kwh=total_grid_kwh,
        total_cost_bdt=total_cost_bdt,
        peak_grid_kwh=peak_grid_kwh,
        plan_summary=summary,
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={
        "error": "Internal server error",
        "detail": "An unexpected error occurred",
    })
