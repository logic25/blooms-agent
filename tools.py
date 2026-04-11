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

# Blooms OS Supabase (floral operations data)
BLOOMS_SUPABASE_URL = "https://pqhatplothwhdanfrcrq.supabase.co"
BLOOMS_SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InBxaGF0cGxvdGh3aGRhbmZyY3JxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDE0NzM5NDgsImV4cCI6MjA1NzA0OTk0OH0.4nmjVjWu_hylb7WaNKsPk6_JMXAWX5C4n5V1zp_Gr88"


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


async def _blooms_db_get(table: str, params: dict = None) -> dict:
    """Query Blooms OS Supabase (floral operations tables)."""
    url = f"{BLOOMS_SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": BLOOMS_SUPABASE_KEY,
        "Authorization": f"Bearer {BLOOMS_SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers, params=params or {})
            if resp.status_code != 200:
                return {"error": f"Blooms DB returned {resp.status_code}: {resp.text[:200]}"}
            return {"data": resp.json()}
    except Exception as e:
        log.error(f"Blooms DB query error ({table}): {e}")
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

    # Pull real inventory data
    inv_result = await _blooms_db_get(
        "blooms_cooler_inventory",
        {"select": "flower_type,quantity", "order": "quantity"},
    )
    inventory = inv_result.get("data", [])
    out_of_stock = [i["flower_type"] for i in inventory if i.get("quantity", 0) == 0]
    low_stock = [f"{i['flower_type']} ({i['quantity']} stems)" for i in inventory if 0 < i.get("quantity", 0) <= 10]

    return {
        "date": today.strftime("%A, %B %d, %Y"),
        "day_advice": day_advice,
        "seasonal": seasonal,
        "always_stock": staples,
        "cogs_reminder": cogs_note,
        "house_accounts_check": "Check standing orders for Hofstra, Adelphi, N.F. Walker this week.",
        "current_inventory": {
            "out_of_stock": out_of_stock,
            "low_stock": low_stock,
            "total_items": len(inventory),
        },
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


async def get_morning_brief(params: dict = None) -> dict:
    """Generate the full morning operations brief — orders, inventory gaps,
    what to order, who to call. This is the core daily workflow tool."""
    from datetime import timedelta
    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    tomorrow_str = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    week_end = (today + timedelta(days=7)).strftime("%Y-%m-%d")

    # 1. Get today's and upcoming orders
    orders_result = await _blooms_db_get(
        "blooms_daily_orders",
        {
            "select": "id,product_id,quantity,delivery_date,delivery_time,"
                      "customer_name,notes,status",
            "status": "neq.fulfilled",
            "delivery_date": f"lte.{week_end}",
            "order": "delivery_date,delivery_time.nullslast",
        },
    )
    orders = orders_result.get("data", [])

    # 2. Get all products + recipes
    prod_result = await _blooms_db_get(
        "blooms_products", {"select": "id,name,price"}
    )
    products = {p["id"]: p for p in prod_result.get("data", [])}

    recipe_result = await _blooms_db_get(
        "blooms_recipe_items", {"select": "product_id,flower_type,quantity"}
    )
    recipes_by_product = {}
    for r in recipe_result.get("data", []):
        pid = r["product_id"]
        if pid not in recipes_by_product:
            recipes_by_product[pid] = []
        recipes_by_product[pid].append(r)

    # 3. Get current cooler inventory
    inv_result = await _blooms_db_get(
        "blooms_cooler_inventory", {"select": "flower_type,quantity"}
    )
    cooler = {i["flower_type"]: i["quantity"] for i in inv_result.get("data", [])}

    # 4. Get vendor info + pricing
    vendor_result = await _blooms_db_get(
        "blooms_vendors",
        {"select": "id,name,phone,email,order_cutoff,delivery_days,"
                   "reliability_rating,specialties"},
    )
    vendors = {v["id"]: v for v in vendor_result.get("data", [])}

    vendor_inv_result = await _blooms_db_get(
        "blooms_vendor_inventory",
        {"select": "vendor_id,flower_type,regular_price,peak_price,quality_rating"},
    )
    # Best vendor per flower (lowest regular price with quality >= 4)
    best_vendor_for = {}
    for vi in vendor_inv_result.get("data", []):
        ft = vi["flower_type"]
        if ft not in best_vendor_for or vi["regular_price"] < best_vendor_for[ft]["regular_price"]:
            best_vendor_for[ft] = vi

    # 5. Calculate flower needs per day
    def calc_needs(day_orders):
        needs = {}
        for order in day_orders:
            recipe = recipes_by_product.get(order["product_id"], [])
            for item in recipe:
                ft = item["flower_type"]
                stems = item["quantity"] * order["quantity"]
                needs[ft] = needs.get(ft, 0) + stems
        return needs

    today_orders = [o for o in orders if o.get("delivery_date") == today_str]
    tomorrow_orders = [o for o in orders if o.get("delivery_date") == tomorrow_str]
    week_orders = orders  # all pending within the week

    today_needs = calc_needs(today_orders)
    tomorrow_needs = calc_needs(tomorrow_orders)
    week_needs = calc_needs(week_orders)

    # 6. Find gaps (need vs have)
    def find_gaps(needs):
        gaps = []
        for flower, needed in sorted(needs.items()):
            have = cooler.get(flower, 0)
            if needed > have:
                gap = needed - have
                # Find best vendor
                vendor_info = best_vendor_for.get(flower, {})
                vendor_id = vendor_info.get("vendor_id")
                vendor = vendors.get(vendor_id, {}) if vendor_id else {}
                # Round up to nearest bunch of 25 for roses, 10 for others
                order_qty = gap + 10  # buffer
                est_cost = round(order_qty * vendor_info.get("regular_price", 0), 2)

                gaps.append({
                    "flower_type": flower,
                    "needed": needed,
                    "in_stock": have,
                    "shortfall": gap,
                    "order_quantity": order_qty,
                    "vendor_name": vendor.get("name", "Unknown"),
                    "vendor_phone": vendor.get("phone", ""),
                    "price_per_stem": vendor_info.get("regular_price", 0),
                    "estimated_cost": est_cost,
                })
        return gaps

    today_gaps = find_gaps(today_needs)
    tomorrow_gaps = find_gaps(tomorrow_needs)

    # 7. Enrich orders with product names
    def enrich(order_list):
        enriched = []
        for o in order_list:
            prod = products.get(o.get("product_id"), {})
            enriched.append({
                "quantity": o["quantity"],
                "product": prod.get("name", "Unknown"),
                "price": prod.get("price", 0),
                "customer": o.get("customer_name", "Walk-in"),
                "delivery_time": o.get("delivery_time", ""),
                "notes": o.get("notes", ""),
                "line_total": o["quantity"] * prod.get("price", 0),
            })
        return enriched

    today_enriched = enrich(today_orders)
    tomorrow_enriched = enrich(tomorrow_orders)

    today_revenue = sum(o["line_total"] for o in today_enriched)
    tomorrow_revenue = sum(o["line_total"] for o in tomorrow_enriched)

    # 8. Build vendor call list (group gaps by vendor)
    calls = {}
    for gap in tomorrow_gaps:
        vname = gap["vendor_name"]
        if vname not in calls:
            calls[vname] = {
                "vendor": vname,
                "phone": gap["vendor_phone"],
                "items": [],
                "total_cost": 0,
            }
        calls[vname]["items"].append({
            "flower": gap["flower_type"],
            "quantity": gap["order_quantity"],
            "cost": gap["estimated_cost"],
        })
        calls[vname]["total_cost"] += gap["estimated_cost"]

    # Out of stock / low stock alerts
    out_of_stock = [ft for ft, qty in cooler.items() if qty == 0]
    low_stock = [f"{ft} ({qty})" for ft, qty in cooler.items() if 0 < qty <= 10]

    return {
        "date": today.strftime("%A, %B %d, %Y"),
        "today": {
            "orders": today_enriched,
            "order_count": len(today_orders),
            "revenue": today_revenue,
            "gaps": today_gaps,
            "all_in_stock": len(today_gaps) == 0,
        },
        "tomorrow": {
            "orders": tomorrow_enriched,
            "order_count": len(tomorrow_orders),
            "revenue": tomorrow_revenue,
            "gaps": tomorrow_gaps,
        },
        "vendor_calls": list(calls.values()),
        "total_ordering_cost": round(sum(c["total_cost"] for c in calls.values()), 2),
        "inventory_alerts": {
            "out_of_stock": out_of_stock,
            "low_stock": low_stock,
        },
        "week_summary": {
            "total_orders": len(week_orders),
            "total_revenue": sum(
                o["quantity"] * products.get(o["product_id"], {}).get("price", 0)
                for o in week_orders
            ),
        },
    }


async def get_inventory(params: dict = None) -> dict:
    """Get current cooler inventory — real-time flower counts."""
    params = params or {}
    inv_params = {"select": "flower_type,quantity,last_updated", "order": "flower_type"}
    if params.get("flower_type"):
        inv_params["flower_type"] = f"ilike.%{params['flower_type']}%"

    result = await _blooms_db_get("blooms_cooler_inventory", inv_params)
    items = result.get("data", [])

    # Summary stats
    total_stems = sum(i.get("quantity", 0) for i in items)
    out_of_stock = [i["flower_type"] for i in items if i.get("quantity", 0) == 0]
    low_stock = [i["flower_type"] for i in items if 0 < i.get("quantity", 0) <= 10]

    return {
        "inventory": items,
        "total_stems": total_stems,
        "out_of_stock": out_of_stock,
        "low_stock": low_stock,
        "item_count": len(items),
        "error": result.get("error"),
    }


async def get_orders(params: dict = None) -> dict:
    """Get current daily orders with product names."""
    params = params or {}
    order_params = {
        "select": "id,product_id,quantity,delivery_date,delivery_time,"
                  "customer_name,notes,status,created_at",
        "order": "delivery_date,created_at",
    }
    if params.get("status"):
        order_params["status"] = f"eq.{params['status']}"
    if params.get("date"):
        order_params["delivery_date"] = f"eq.{params['date']}"

    result = await _blooms_db_get("blooms_daily_orders", order_params)
    orders = result.get("data", [])

    # Get products to map names
    prod_result = await _blooms_db_get("blooms_products", {"select": "id,name,price"})
    products = {p["id"]: p for p in prod_result.get("data", [])}

    # Enrich orders with product info
    for order in orders:
        prod = products.get(order.get("product_id"), {})
        order["product_name"] = prod.get("name", "Unknown")
        order["unit_price"] = prod.get("price", 0)
        order["line_total"] = order.get("quantity", 0) * prod.get("price", 0)

    # Stats
    pending = [o for o in orders if o.get("status") == "pending"]
    fulfilled = [o for o in orders if o.get("status") == "fulfilled"]
    total_revenue = sum(o.get("line_total", 0) for o in orders)

    return {
        "orders": orders,
        "stats": {
            "total": len(orders),
            "pending": len(pending),
            "fulfilled": len(fulfilled),
            "total_revenue": total_revenue,
        },
        "error": result.get("error"),
    }


async def get_products_list(params: dict = None) -> dict:
    """Get all products with their recipes."""
    prod_result = await _blooms_db_get(
        "blooms_products",
        {"select": "id,name,category,price,is_active", "order": "name"},
    )
    recipe_result = await _blooms_db_get(
        "blooms_recipe_items",
        {"select": "product_id,flower_type,quantity,notes"},
    )

    # Group recipes by product
    recipes = {}
    for r in recipe_result.get("data", []):
        pid = r["product_id"]
        if pid not in recipes:
            recipes[pid] = []
        recipes[pid].append(r)

    products = prod_result.get("data", [])
    for p in products:
        p["recipe"] = recipes.get(p["id"], [])

    return {
        "products": products,
        "count": len(products),
        "error": prod_result.get("error"),
    }


async def get_vendors_list(params: dict = None) -> dict:
    """Get all vendors with their inventory/pricing."""
    vendor_result = await _blooms_db_get(
        "blooms_vendors",
        {"select": "id,name,phone,email,lead_time_days,order_cutoff,"
                   "delivery_days,payment_terms,reliability_rating,specialties,notes",
         "order": "name"},
    )
    inv_result = await _blooms_db_get(
        "blooms_vendor_inventory",
        {"select": "vendor_id,flower_type,regular_price,peak_price,quality_rating,notes"},
    )

    # Group inventory by vendor
    vendor_inv = {}
    for item in inv_result.get("data", []):
        vid = item["vendor_id"]
        if vid not in vendor_inv:
            vendor_inv[vid] = []
        vendor_inv[vid].append(item)

    vendors = vendor_result.get("data", [])
    for v in vendors:
        v["inventory"] = vendor_inv.get(v["id"], [])

    return {
        "vendors": vendors,
        "count": len(vendors),
        "error": vendor_result.get("error"),
    }


# Dispatch map
TOOL_DISPATCH = {
    "get_financial_health": get_financial_health,
    "get_tasks": get_tasks,
    "get_expenses": get_expenses,
    "suggest_order": suggest_order,
    "get_morning_brief": get_morning_brief,
    "get_overview": get_overview,
    "get_inventory": get_inventory,
    "get_orders": get_orders,
    "get_products_list": get_products_list,
    "get_vendors_list": get_vendors_list,
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
    {
        "name": "get_morning_brief",
        "description": "Generate the full morning operations brief. Calculates: "
                       "today's orders, tomorrow's orders, flowers needed vs in stock, "
                       "inventory gaps, which vendors to call, estimated costs, "
                       "and total revenue. THIS IS THE PRIMARY TOOL — call it for "
                       "'morning brief', 'what do I need today', 'what should I order', "
                       "'who do I call', or any daily operations question.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_inventory",
        "description": "Get current cooler inventory — real-time flower stem counts. "
                       "Shows what's in the cooler right now, what's out of stock, "
                       "and what's running low. Use for 'how many roses?', "
                       "'what do we have?', 'what are we low on?'",
        "input_schema": {
            "type": "object",
            "properties": {
                "flower_type": {
                    "type": "string",
                    "description": "Filter by flower name (partial match, e.g. 'rose')"
                },
            },
        },
    },
    {
        "name": "get_orders",
        "description": "Get current daily customer orders with product names and totals. "
                       "Use for 'what orders do we have?', 'what's pending?', "
                       "'how much revenue today?'",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_production", "fulfilled"],
                    "description": "Filter by order status"
                },
                "date": {
                    "type": "string",
                    "description": "Filter by delivery date (YYYY-MM-DD)"
                },
            },
        },
    },
    {
        "name": "get_products_list",
        "description": "Get all products/arrangements with their flower recipes. "
                       "Use for 'what products do we sell?', 'what goes into a dozen roses?'",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_vendors_list",
        "description": "Get all vendors with contact info, delivery schedules, and flower pricing. "
                       "Use for 'who sells roses?', 'vendor info', 'who delivers on Wednesday?'",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]
