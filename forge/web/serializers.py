"""Преобразование результатов core/planner/recommend в JSON-совместимые словари."""

from __future__ import annotations

from ..core import BuildEstimate
from ..core.sourcing import MaterialLine, NodeResult
from ..planner import Plan
from ..planner.schedule import Schedule
from ..recommend.engine import BuyDeal, Recommendation
from ..recommend.stock import StockRecommendation


def material_line_to_dict(ln: MaterialLine) -> dict:
    return {
        "type_id": ln.type_id,
        "name": ln.name,
        "quantity": ln.quantity,
        "source": ln.source,
        "unit_cost": ln.unit_cost,
        "subtotal": ln.subtotal,
        "buy_hub": ln.buy_hub,
        "buy_shortage": ln.buy_shortage,
        "child": node_to_dict(ln.child) if ln.child else None,
    }


def node_to_dict(n: NodeResult) -> dict:
    return {
        "product_type_id": n.product_type_id,
        "name": n.name,
        "activity_id": n.activity_id,
        "blueprint_type_id": n.blueprint_type_id,
        "runs": n.runs,
        "streams": n.streams,
        "produced": n.produced,
        "material_cost": n.material_cost,
        "job_cost": n.job_cost,
        "total_cost": n.total_cost,
        "unit_cost": n.unit_cost,
        "blueprint_cost": n.blueprint_cost,
        "blueprint_source": n.blueprint_source,
        "decryptor": n.decryptor,
        "decryptor_manual": n.decryptor_manual,
        "decryptor_fallback": n.decryptor_fallback,
        "resulting_te": n.resulting_te,
        "reaction_bp_owned": n.reaction_bp_owned,
        "reaction_bp_needed": n.reaction_bp_needed,
        "bpc_runs_owned": n.bpc_runs_owned,
        "bpc_runs_needed": n.bpc_runs_needed,
        "bpc_shortfall_invention_attempts": n.bpc_shortfall_invention_attempts,
        "bpc_shortfall_invention_fee_per_attempt": n.bpc_shortfall_invention_fee_per_attempt,
        "bpc_shortfall_decryptor": n.bpc_shortfall_decryptor,
        "bpc_shortfall_probability": n.bpc_shortfall_probability,
        "reprocess_source_id": n.reprocess_source_id,
        "reprocess_source_name": n.reprocess_source_name,
        "reprocess_byproduct_credit": n.reprocess_byproduct_credit,
        "reprocess_byproducts": [
            {"type_id": t, "name": nm, "quantity": q} for t, nm, q in n.reprocess_byproducts
        ],
        "invention_source_id": n.invention_source_id,
        "invention_source_name": n.invention_source_name,
        "invention_source_owned": n.invention_source_owned,
        "invention_t1_kind": n.invention_t1_kind,
        "invention_t1_bpos": n.invention_t1_bpos,
        "invention_t1_copy": n.invention_t1_copy,
        # копия: сервис дописывает в неё подписи (имена систем) — не трогать объект ядра
        "invention_breakdown": dict(n.invention_breakdown) if n.invention_breakdown else None,
        "eiv": n.eiv,
        "cost_index": n.cost_index,
        "cost_mult": n.cost_mult,
        "facility_tax": n.facility_tax,
        "scc_surcharge": n.scc_surcharge,
        "cost_system_id": n.cost_system_id,
        "missing_prices": sorted(set(n.missing_prices)),
        "lines": [material_line_to_dict(l) for l in n.lines],
    }


def estimate_to_dict(est: BuildEstimate) -> dict:
    return {
        "node": node_to_dict(est.node),
        "profit": {
            "sell_unit_price": est.profit.sell_unit_price,
            "revenue": est.profit.revenue,
            "profit": est.profit.profit,
            "roi": est.profit.roi,
            "export_freight": est.profit.export_freight,
            "export_is_jump": est.profit.export_is_jump,
        },
    }


def schedule_to_dict(s: Schedule) -> dict:
    return {
        "makespan": s.makespan,
        "warnings": list(dict.fromkeys(s.warnings)),
        "transfer_jobs": s.transfer_jobs,
        "items": [
            {
                "job_id": it.job_id,
                "product_type_id": it.product_type_id,
                "name": it.name,
                "activity_id": it.activity_id,
                "runs": it.runs,
                "character_id": it.character_id,
                "character_name": it.character_name,
                "pool": it.pool,
                "slot": it.slot,
                "start": it.start,
                "end": it.end,
                "te": it.te,
                "owner_status": it.owner_status,
                "owners": list(it.owners),
                "copy_runs": it.copy_runs,
            }
            for it in s.items
        ],
    }


def plan_to_dict(p: Plan) -> dict:
    return {"estimate": estimate_to_dict(p.estimate), "schedule": schedule_to_dict(p.schedule)}


def buy_deal_to_dict(d: BuyDeal) -> dict:
    return {
        "product_type_id": d.product_type_id,
        "name": d.name,
        "build_unit": d.build_unit,
        "buy_unit": d.buy_unit,
        "buy_hub": d.buy_hub,
        "savings_unit": d.savings_unit,
        "savings_pct": d.savings_pct,
        "daily_volume": d.daily_volume,
    }


def recommendation_to_dict(r: Recommendation) -> dict:
    return {
        "product_type_id": r.product_type_id,
        "name": r.name,
        "runs": r.runs,
        "capital": r.capital,
        "unit_cost": r.unit_cost,
        "unit_profit": r.unit_profit,
        "roi": r.roi,
        "isk_per_hour": r.isk_per_hour,
        "daily_volume": r.daily_volume,
        "score": r.score,
    }


def stock_recommendation_to_dict(r: StockRecommendation) -> dict:
    return {
        "product_type_id": r.product_type_id,
        "name": r.name,
        "runs": r.runs,
        "capital": r.capital,
        "real_capital": r.real_capital,
        "stock_value": r.stock_value,
        "stock_utilization": r.stock_utilization,
        "unit_cost": r.unit_cost,
        "real_profit": r.real_profit,
        "real_roi": r.real_roi,
        "isk_per_hour": r.isk_per_hour,
        "daily_volume": r.daily_volume,
        "score": r.score,
    }
