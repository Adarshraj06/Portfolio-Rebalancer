"""
app.py  -  Portfolio Rebalancing Web App for Amit Sharma
=========================================================
Tech stack : Python · Flask · SQLite (raw SQL) · Jinja2 templates
Database   : model_portfolio.db  (provided, pre-loaded — do NOT recreate)

Key schema facts (from the actual DB):
  clients        → client_id TEXT ('C001'), client_name TEXT
  model_funds    → fund_id, fund_name, asset_class, allocation_pct
  client_holdings→ holding_id, client_id TEXT, fund_id, fund_name, current_value
  rebalance_sessions → session_id INTEGER autoincrement, client_id TEXT, ...
  rebalance_items    → item_id INTEGER autoincrement, session_id, ...
"""

import sqlite3
import os
from datetime import datetime
from flask import Flask, render_template, redirect, url_for, request, flash

app = Flask(__name__)
app.secret_key = "rebalance-amit-2024"

# ── Database path ─────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "model_portfolio.db")


# ═══════════════════════════════════════════════════════════════════════════════
#  DATABASE HELPER
# ═══════════════════════════════════════════════════════════════════════════════

def get_db():
    """
    Open a SQLite connection with row_factory=sqlite3.Row so every row
    can be accessed both by column name (row['fund_id']) and by index.
    Caller must call conn.close() when done.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ═══════════════════════════════════════════════════════════════════════════════
#  BUSINESS-LOGIC HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def get_amit(conn):
    """
    Return Amit Sharma's row from the clients table.
    client_id is a TEXT value like 'C001'.
    """
    row = conn.execute(
        "SELECT client_id, client_name FROM clients WHERE client_name = 'Amit Sharma'"
    ).fetchone()
    if row is None:
        raise ValueError("Amit Sharma not found in the clients table.")
    return row          # row['client_id'] == 'C001'


def calculate_rebalance(conn, client_id):
    """
    Core rebalancing calculation for a given client_id.

    Algorithm:
      1. Fetch all model funds (the advisor's recommended plan).
      2. Fetch all client holdings.
      3. total_portfolio = sum of ALL current holdings (including non-model funds).
      4. For each model fund:
           current_value  = holding value (0 if not held)
           current_pct    = current_value / total_portfolio * 100
           target_pct     = allocation_pct from model_funds
           drift          = target_pct - current_pct
           action         = BUY if drift>0, SELL if drift<0, HOLD if drift==0
           amount         = round(|drift| / 100 * total_portfolio)
      5. Non-model holdings → REVIEW, no BUY/SELL amount.

    Returns a dict with keys:
      rows, total_portfolio, total_to_buy, total_to_sell, fresh_money_needed
    """

    # ── 1. Model funds ──────────────────────────────────────────────────
    model_rows = conn.execute(
        "SELECT fund_id, fund_name, asset_class, allocation_pct FROM model_funds"
    ).fetchall()

    # Lookup dict: fund_id -> model fund info
    model_map = {
        r["fund_id"]: {
            "fund_name":      r["fund_name"],
            "asset_class":    r["asset_class"],
            "allocation_pct": r["allocation_pct"],
        }
        for r in model_rows
    }

    # ── 2. Client holdings ──────────────────────────────────────────────
    holding_rows = conn.execute(
        "SELECT fund_id, fund_name, current_value "
        "FROM client_holdings WHERE client_id = ?",
        (client_id,)
    ).fetchall()

    # Lookup dict: fund_id -> holding row
    holding_map = {r["fund_id"]: r for r in holding_rows}

    # ── 3. Total portfolio value (ALL holdings, including non-model) ─────
    total_portfolio = sum(r["current_value"] for r in holding_rows)

    # ── 4. Build comparison rows ────────────────────────────────────────
    rows = []

    # 4a. Every model fund (whether or not Amit holds it)
    for fund_id, mf in model_map.items():
        h = holding_map.get(fund_id)
        current_value = h["current_value"] if h else 0.0
        target_pct    = mf["allocation_pct"]

        # current percentage of total portfolio
        current_pct   = (current_value / total_portfolio * 100) if total_portfolio else 0.0
        current_pct_r = round(current_pct, 1)

        # drift = how far we are from the target
        drift   = target_pct - current_pct
        drift_r = round(drift, 1)

        # decide action
        if drift > 0:
            action = "BUY"
        elif drift < 0:
            action = "SELL"
        else:
            action = "HOLD"

        # rupee amount to transact
        amount = round(abs(drift) / 100 * total_portfolio) if action != "HOLD" else 0

        rows.append({
            "fund_id":            fund_id,
            "fund_name":          mf["fund_name"],
            "asset_class":        mf["asset_class"],
            "current_value":      current_value,
            "current_pct":        current_pct_r,
            "target_pct":         target_pct,
            "drift":              drift_r,
            "action":             action,
            "amount":             amount,
            "is_model_fund":      1,
            # after rebalancing this fund will be exactly at target
            "post_rebalance_pct": target_pct,
        })

    # 4b. Non-model fund holdings → REVIEW
    for fund_id, h in holding_map.items():
        if fund_id not in model_map:
            cv          = h["current_value"]
            current_pct = (cv / total_portfolio * 100) if total_portfolio else 0.0
            rows.append({
                "fund_id":            fund_id,
                "fund_name":          h["fund_name"],
                "asset_class":        "—",
                "current_value":      cv,
                "current_pct":        round(current_pct, 1),
                "target_pct":         None,   # no target for non-model (nullable in DB)
                "drift":              None,   # display only — not saved
                "action":             "REVIEW",
                "amount":             0,      # DB has NOT NULL on amount — use 0 for REVIEW
                "is_model_fund":      0,
                "post_rebalance_pct": None,   # nullable in DB
            })

    # ── 5. Summary totals ────────────────────────────────────────────────
    total_to_buy       = sum(r["amount"] for r in rows if r["action"] == "BUY")
    total_to_sell      = sum(r["amount"] for r in rows if r["action"] == "SELL")
    fresh_money_needed = total_to_buy - total_to_sell

    return {
        "rows":               rows,
        "total_portfolio":    total_portfolio,
        "total_to_buy":       total_to_buy,
        "total_to_sell":      total_to_sell,
        "fresh_money_needed": fresh_money_needed,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

# ── 1. Main comparison screen ─────────────────────────────────────────────────
@app.route("/")
def index():
    conn = get_db()
    try:
        amit        = get_amit(conn)
        client_id   = amit["client_id"]     # 'C001'
        client_name = amit["client_name"]
        data        = calculate_rebalance(conn, client_id)
    except Exception as e:
        conn.close()
        return f"<h2 style='color:red'>Error: {e}</h2>", 500
    conn.close()
    return render_template("index.html", client_name=client_name, **data)


# ── 2. Save recommendation (POST only) ───────────────────────────────────────
@app.route("/save", methods=["POST"])
def save():
    conn = get_db()
    try:
        amit      = get_amit(conn)
        client_id = amit["client_id"]
        data      = calculate_rebalance(conn, client_id)

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Insert one summary row into rebalance_sessions
        cursor = conn.execute(
            """
            INSERT INTO rebalance_sessions
                (client_id, created_at, portfolio_value,
                 total_to_buy, total_to_sell, net_cash_needed, status)
            VALUES (?, ?, ?, ?, ?, ?, 'PENDING')
            """,
            (
                client_id,
                now,
                data["total_portfolio"],
                data["total_to_buy"],
                data["total_to_sell"],
                data["fresh_money_needed"],
            )
        )
        session_id = cursor.lastrowid   # SQLite auto-generated INTEGER

        # Insert one detail row per fund into rebalance_items
        for row in data["rows"]:
            conn.execute(
                """
                INSERT INTO rebalance_items
                    (session_id, fund_id, fund_name, action, amount,
                     current_pct, target_pct, post_rebalance_pct, is_model_fund)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    row["fund_id"],
                    row["fund_name"],
                    row["action"],
                    row["amount"],           # None for REVIEW rows
                    row["current_pct"],
                    row["target_pct"],       # None for REVIEW rows
                    row["post_rebalance_pct"],  # None for REVIEW rows
                    row["is_model_fund"],
                )
            )

        conn.commit()
        flash(f"✅ Recommendation saved! Session ID: {session_id}", "success")

    except Exception as e:
        conn.rollback()
        flash(f"❌ Error saving recommendation: {e}", "danger")
    finally:
        conn.close()

    return redirect(url_for("index"))


# ── 3. Current holdings screen ────────────────────────────────────────────────
@app.route("/holdings")
def holdings():
    conn = get_db()
    try:
        amit        = get_amit(conn)
        client_id   = amit["client_id"]
        client_name = amit["client_name"]

        rows = conn.execute(
            """
            SELECT fund_id, fund_name, current_value
            FROM client_holdings
            WHERE client_id = ?
            ORDER BY current_value DESC
            """,
            (client_id,)
        ).fetchall()

        total_value = sum(r["current_value"] for r in rows)

    except Exception as e:
        conn.close()
        return f"<h2 style='color:red'>Error: {e}</h2>", 500

    conn.close()
    return render_template("holdings.html",
                           rows=rows,
                           total_value=total_value,
                           client_name=client_name)


# ── 4. Rebalance history screen ───────────────────────────────────────────────
@app.route("/history")
def history():
    conn = get_db()
    try:
        amit        = get_amit(conn)
        client_id   = amit["client_id"]
        client_name = amit["client_name"]

        sessions = conn.execute(
            """
            SELECT session_id, created_at, portfolio_value,
                   total_to_buy, total_to_sell, net_cash_needed, status
            FROM rebalance_sessions
            WHERE client_id = ?
            ORDER BY created_at DESC
            """,
            (client_id,)
        ).fetchall()

    except Exception as e:
        conn.close()
        return f"<h2 style='color:red'>Error: {e}</h2>", 500

    conn.close()
    return render_template("history.html",
                           sessions=sessions,
                           client_name=client_name)


# ── 5. Session detail screen ──────────────────────────────────────────────────
@app.route("/history/<int:session_id>")
def session_detail(session_id):
    conn = get_db()
    try:
        amit      = get_amit(conn)
        client_id = amit["client_id"]

        # Make sure this session belongs to Amit
        session = conn.execute(
            "SELECT * FROM rebalance_sessions "
            "WHERE session_id = ? AND client_id = ?",
            (session_id, client_id)
        ).fetchone()

        if session is None:
            conn.close()
            return "<h2>Session not found or access denied.</h2>", 404

        items = conn.execute(
            "SELECT * FROM rebalance_items WHERE session_id = ?",
            (session_id,)
        ).fetchall()

    except Exception as e:
        conn.close()
        return f"<h2 style='color:red'>Error: {e}</h2>", 500

    conn.close()
    return render_template("session_detail.html",
                           session=session,
                           items=items,
                           client_name=amit["client_name"])


# ── 6. Edit model portfolio ───────────────────────────────────────────────────
@app.route("/edit-plan", methods=["GET", "POST"])
def edit_plan():
    conn = get_db()

    if request.method == "POST":
        funds = conn.execute(
            "SELECT fund_id, fund_name FROM model_funds"
        ).fetchall()

        updates     = []
        total_alloc = 0.0
        error       = None

        try:
            for fund in funds:
                fid   = fund["fund_id"]
                value = request.form.get(f"alloc_{fid}", "").strip()
                pct   = float(value)
                if pct < 0:
                    error = f"Allocation for {fid} cannot be negative."
                    break
                total_alloc += pct
                updates.append((pct, fid))
        except (ValueError, TypeError):
            error = "All fields must be valid numbers."

        # Enforce that allocations sum to exactly 100%
        if error is None and abs(total_alloc - 100.0) > 0.001:
            error = (
                f"Allocations must add up to exactly 100%. "
                f"Your total is currently {round(total_alloc, 2)}%."
            )

        if error:
            model_funds = conn.execute(
                "SELECT fund_id, fund_name, asset_class, allocation_pct FROM model_funds"
            ).fetchall()
            conn.close()
            return render_template("edit_plan.html",
                                   model_funds=model_funds,
                                   error=error)

        # Save new allocations
        for (pct, fid) in updates:
            conn.execute(
                "UPDATE model_funds SET allocation_pct = ? WHERE fund_id = ?",
                (pct, fid)
            )
        conn.commit()
        conn.close()

        flash("✅ Model portfolio updated! Rebalancing recalculated below.", "success")
        return redirect(url_for("index"))

    # GET – show current allocations
    model_funds = conn.execute(
        "SELECT fund_id, fund_name, asset_class, allocation_pct FROM model_funds"
    ).fetchall()
    conn.close()
    return render_template("edit_plan.html", model_funds=model_funds, error=None)


# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app.run(debug=True, port=5000)
