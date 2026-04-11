"""
Blooms MCP Server — Remote MCP tools for Claude app.

Exposes all Blooms operations tools via Streamable HTTP so
Bileysi and Manny can use them from Claude mobile/desktop.

Deploy on Railway alongside the Flask chat server.
"""
from fastmcp import FastMCP
import os

# Initialize with metadata
mcp = FastMCP(
    "Blooms in Bunches",
    description="Flower shop operations — inventory, orders, vendors, morning brief",
)

# ============================================================
# Import the existing tool functions from tools.py
# ============================================================
from tools import (
    get_morning_brief,
    get_inventory,
    get_orders,
    get_products_list,
    get_vendors_list,
    suggest_order,
    get_financial_health,
    get_overview,
)

# ============================================================
# MCP Tool Definitions — wrap existing async functions
# ============================================================

@mcp.tool()
async def morning_brief() -> str:
    """Get the full morning operations brief.

    Shows: today's orders, tomorrow's orders, flowers needed vs in stock,
    inventory gaps, which vendors to call, estimated costs, revenue totals.

    Use for: "morning brief", "what do I need today", "what should I order",
    "who do I call", or any daily operations question.
    """
    result = await get_morning_brief()
    return _format(result)


@mcp.tool()
async def inventory(flower_type: str = "") -> str:
    """Get current cooler inventory — real-time flower stem counts.

    Shows what's in the cooler, what's out of stock, what's running low.

    Args:
        flower_type: Optional filter by flower name (partial match, e.g. "rose")
    """
    params = {"flower_type": flower_type} if flower_type else {}
    result = await get_inventory(params)
    return _format(result)


@mcp.tool()
async def orders(status: str = "", date: str = "") -> str:
    """Get current daily customer orders with product names and totals.

    Args:
        status: Filter by status — "pending", "in_production", or "fulfilled"
        date: Filter by delivery date (YYYY-MM-DD)
    """
    params = {}
    if status:
        params["status"] = status
    if date:
        params["date"] = date
    result = await get_orders(params)
    return _format(result)


@mcp.tool()
async def products() -> str:
    """Get all products/arrangements with their flower recipes.

    Shows each product's name, category, price, and which flowers
    (with quantities) go into making it.
    """
    result = await get_products_list()
    return _format(result)


@mcp.tool()
async def vendors() -> str:
    """Get all vendors with contact info, delivery schedules, and flower pricing.

    Shows each vendor's phone, email, delivery days, order cutoff,
    reliability rating, specialties, and per-flower pricing.
    """
    result = await get_vendors_list()
    return _format(result)


@mcp.tool()
async def ordering_suggestions() -> str:
    """Get ordering suggestions based on day of week, season, inventory levels.

    Includes: day-of-week advice, seasonal priorities, always-stock staples,
    COGS reminder, current out-of-stock and low-stock items.
    """
    result = await suggest_order()
    return _format(result)


@mcp.tool()
async def financials(year: int = 0) -> str:
    """Get Blooms financial overview — revenue, COGS, profit margins.

    Includes baselines from 2025 analysis: revenue $842K, COGS 42.9%
    vs 34.4% target, $96K profit gap.

    Args:
        year: Optional year to filter (e.g. 2025, 2026)
    """
    params = {"year": year} if year else {}
    result = await get_financial_health(params)
    return _format(result)


@mcp.tool()
async def business_overview() -> str:
    """Get the full Blooms business snapshot from Venture Studio.

    Revenue, expenses, budget, employees, status — the big picture.
    """
    result = await get_overview()
    return _format(result)


# ============================================================
# Helper
# ============================================================
import json

def _format(data: dict) -> str:
    """Convert tool result dict to readable string for Claude."""
    # Remove None values and format nicely
    clean = {k: v for k, v in data.items() if v is not None}
    return json.dumps(clean, indent=2, default=str)


# ============================================================
# Run
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("MCP_PORT", os.environ.get("PORT", 8080)))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
