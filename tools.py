"""
Blooms Agent Tools — queries Venture Studio Supabase for Blooms business data.
Same pattern as Harvest's agents.py but scoped to Blooms only.
"""
import httpx
import logging
from datetime import datetime

import config

log = logging.getLogger("blooms.tools")

# Cache for Blooms entity ID
_blooms_entity_id: str | None = None


async def _supabase_get(table: str, params: dict = None) -> dict:
    """Query Venture Studio Supabase."""
    if not config.VS_SUPABASE_URL or not config.VS_SUPABASE_KEY:
        return {"error": "Venture Studio Supabase not configured"}

    url = f"{config.VS_SUPABASE_URL.rstrip('/')}/rest/v1/{table}"
    headers = {
        "apikey": config.VS_SUPABASE_KEY,
        "Authorization": f"Bearer {config.VS_SUPABASE_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers, params=params or {})
            if resp.status_code != 200:
                return {"error": f"Supabase returned {resp.status_code}: {resp.text[:200]}"}
            return {"data": resp.json()}
    except Exception as e:
        log.error(f"Supabase query error ({table}): {e}")
        return {"error": str(e)}


async def _get_entity_id() -> str | None:
    """Look up and cache the Blooms entity UUID."""
    global _blooms_entity_id
    if _blooms_entity_id:
        return _blooms_entity_id

    result = await _supabase_get(
        "entities",
        {"name": "eq.Blooms", "select": "id", "limit": "1"},
    )
    rows = result.get("data", [])
    if rows:
        _blooms_entity_id = rows[0]["id"]
        log.info(f"Blooms entity ID: {_blooms_entity_id}")
    return _blooms_entity_id


async def get_financial_health(params: dict = None) -> dict:
    """Get Blooms financial overview — revenue, COGS, profit, EBITDA."""
    params = params or {}
    entity_id = await _get_entity_id()
    if not entity_id:
        return {"error": "Blooms entity not found in Venture Studio"}

    entity_result = await _supabase_get(
        "entities",
        {
            "id": f"eq.{entity_id}",
            "select": "name,revenue_ttm,ebitda,annual_expenses,budget,spent,"
                      "add_backs,status,employees,owner_involvement",
        },
    )

    fin_params = {
        "entity_id": f"eq.{entity_id}",
        "select": "year,revenue,net_profit,owner_salary,one_time_expenses,"
                  "non_recurring_costs,interest,taxes,depreciation,amortization",
        "order": "year.desc",
    }
    if params.get("year"):
        fin_params["year"] = f"eq.{params['year']}"

    financials_result = await _supabase_get("entity_financials", fin_params)

    return {
        "entity": (entity_result.get("data") or [None])[0],
        "financials": financials_result.get("data", []),
        "baselines": {
            "revenue_target": 842000,
            "cogs_actual_pct": 42.9,
            "cogs_target_pct": 34.4,
            "profit_gap": 96214,
            "profit_actual": 41700,
            "profit_target": 137914,
        },
        "error": entity_result.get("error") or financials_result.get("error"),
    }


async def get_tasks(params: dict = None) -> dict:
    """Get Blooms initiative tracker — tasks, overdue items, completion rate."""
    params = params or {}
    entity_id = await _get_entity_id()
    if not entity_id:
        return {"error": "Blooms entity not found in Venture Studio"}

    # Check for projects first
    proj_result = await _supabase_get(
        "projects",
        {
            "entity_id": f"eq.{entity_id}",
            "select": "id,name,status,phase",
        },
    )
    project_ids = [p["id"] for p in proj_result.get("data", [])]

    if project_ids:
        pid_list = ",".join(project_ids)
        task_params = {
            "project_id": f"in.({pid_list})",
            "select": "id,title,status,priority,due_date,category,"
                      "completed,completed_date,project_id",
            "order": "due_date.asc.nullslast",
            "limit": "50",
        }
    else:
        task_params = {
            "entity_id": f"eq.{entity_id}",
            "select": "id,title,status,priority,due_date,category,"
                      "completed,completed_date",
            "order": "due_date.asc.nullslast",
            "limit": "50",
        }

    if params.get("status"):
        task_params["status"] = f"eq.{params['status']}"
    if params.get("priority"):
        task_params["priority"] = f"eq.{params['priority']}"

    tasks_result = await _supabase_get("tasks", task_params)
    tasks = tasks_result.get("data", [])

    # Calculate stats
    today = datetime.utcnow().strftime("%Y-%m-%d")
    overdue = [t for t in tasks if t.get("due_date") and t["due_date"] < today
               and t.get("status") != "done"]
    done = [t for t in tasks if t.get("status") == "done"]
    in_progress = [t for t in tasks if t.get("status") == "in_progress"]

    return {
        "projects": proj_result.get("data", []),
        "tasks": tasks,
        "stats": {
            "total": len(tasks),
            "done": len(done),
            "in_progress": len(in_progress),
            "overdue": len(overdue),
            "completion_pct": round(len(done) / max(len(tasks), 1) * 100, 1),
        },
        "overdue_items": overdue[:10],
        "error": tasks_result.get("error"),
    }


async def get_expenses(params: dict = None) -> dict:
    """Get Blooms expenses — recurring costs, vendor payments."""
    params = params or {}
    entity_id = await _get_entity_id()
    if not entity_id:
        return {"error": "Blooms entity not found in Venture Studio"}

    exp_params = {
        "entity_id": f"eq.{entity_id}",
        "select": "id,description,amount,category,expense_date,"
                  "is_recurring,recurring_frequency,notes",
        "order": "expense_date.desc",
        "limit": "50",
    }
    if params.get("category"):
        exp_params["category"] = f"eq.{params['category']}"

    return await _supabase_get("entity_expenses", exp_params)


async def suggest_order(params: dict = None) -> dict:
    """Suggest what to order based on day of week, season, and patterns.
    Uses embedded knowledge until live inventory data is available."""
    params = params or {}
    today = datetime.now()
    day_name = today.strftime("%A")
    month = today.month

    # Seasonal priorities
    seasonal = []
    if month == 2:
        seasonal = ["Valentine's Day prep — HEAVY ordering: red roses, mixed bouquets, heart arrangements"]
    elif month == 5:
        seasonal = ["Mother's Day prep — HEAVY ordering: pastels, spring arrangements, mixed bouquets"]
    elif month in (3, 4):
        seasonal = ["Spring season — tulips, daffodils, hyacinths, Easter lilies if near Easter"]
    elif month in (6, 7, 8):
        seasonal = ["Summer — sunflowers, dahlias, zinnias, lighter arrangements"]
    elif month in (9, 10):
        seasonal = ["Fall — mums, fall colors, Thanksgiving prep in November"]
    elif month in (11, 12):
        seasonal = ["Holiday season — poinsettias, wreaths, red/green arrangements, centerpieces"]
    elif month == 1:
        seasonal = ["Post-holiday lull — keep inventory lean, focus on sympathy and everyday"]

    # Day-of-week guidance
    if day_name in ("Monday", "Tuesday"):
        day_advice = "Heavier ordering day — stock for weekend events + Friday deliveries."
    elif day_name == "Wednesday":
        day_advice = "Mid-week restock — check what moved Mon-Tue, fill gaps."
    elif day_name in ("Thursday", "Friday"):
        day_advice = "Light ordering — use existing inventory. Only order for specific weekend events."
    elif day_name == "Saturday":
        day_advice = "No ordering today — focus on arrangements and walk-in sales."
    else:
        day_advice = "Sunday — shop closed. Plan Monday's order if needed."

    # Always-stock items
    staples = [
        "Red roses (always in demand)",
        "White roses + lilies (sympathy — unpredictable but steady)",
        "Carnations (filler, long-lasting, low cost)",
        "Greenery (eucalyptus, ruscus, leather leaf)",
        "Baby's breath (high margin filler)",
    ]

    # COGS reminder
    cogs_note = (
        "COGS is at 42.9% — target is 34.4%. "
        "Before ordering, ask: Can I substitute a lower-cost flower? "
        "Can I reduce quantity and sell what I have? "
        "Are there items that aren't selling and going to waste?"
    )

    return {
        "date": today.strftime("%A, %B %d, %Y"),
        "day_advice": day_advice,
        "seasonal": seasonal,
        "always_stock": staples,
        "cogs_reminder": cogs_note,
        "house_accounts_check": "Check standing orders for Hofstra, Adelphi, N.F. Walker this week.",
        "note": "This is based on general patterns. Live inventory data is not connected yet.",
    }


async def get_overview(params: dict = None) -> dict:
    """Get the full Blooms entity snapshot from Venture Studio."""
    entity_id = await _get_entity_id()
    if not entity_id:
        return {"error": "Blooms entity not found in Venture Studio"}

    return await _supabase_get(
        "entities",
        {
            "id": f"eq.{entity_id}",
            "select": "*",
        },
    )


# Dispatch map
TOOL_DISPATCH = {
    "get_financial_health": get_financial_health,
    "get_tasks": get_tasks,
    "get_expenses": get_expenses,
    "suggest_order": suggest_order,
    "get_overview": get_overview,
}

# Tool definitions for Claude
TOOL_DEFINITIONS = [
    {
        "name": "get_financial_health",
        "description": "Get Blooms financial overview: revenue, COGS, profit margins, "
                       "EBITDA. Includes baselines from the 2025 financial analysis "
                       "(revenue $842K, COGS 42.9% vs 34.4% target, $96K profit gap). "
                       "Optional param: year (e.g. 2025, 2026).",
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {
                    "type": "integer",
                    "description": "Filter to a specific year"
                }
            },
        },
    },
    {
        "name": "get_tasks",
        "description": "Get Blooms initiative tracker: all tasks with status, priority, "
                       "due dates. Shows overdue items and completion rate. "
                       "Filters: status (todo/in_progress/done), priority (high/medium/low).",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["todo", "in_progress", "done"],
                    "description": "Filter by task status"
                },
                "priority": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Filter by priority"
                },
            },
        },
    },
    {
        "name": "get_expenses",
        "description": "Get Blooms expenses: recurring costs, vendor payments, "
                       "categorized spending. Filter by category.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Filter by expense category"
                },
            },
        },
    },
    {
        "name": "suggest_order",
        "description": "Get ordering suggestions for today based on day of week, "
                       "season, and patterns. Includes COGS reminder and house account "
                       "check. Call this when Bileysi asks 'what should I order today?' "
                       "or anything about ordering/inventory.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_overview",
        "description": "Get the full Blooms business snapshot: revenue, expenses, "
                       "budget, employees, status. Use for general 'how is the business' questions.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]
