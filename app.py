"""
app.py  -  Portfolio Rebalancing Web App
==========================================
Supports all clients in the database.
Home page shows a client selector; all other screens are per-client.

Schema notes (from the actual model_portfolio.db):
  clients         → client_id TEXT ('C001'), client_name TEXT, total_invested REAL
  model_funds     → fund_id TEXT, fund_name TEXT, asset_class TEXT, allocation_pct REAL
  client_holdings → holding_id INT, client_id TEXT, fund_id TEXT, fund_name TEXT, current_value REAL
  rebalance_sessions → session_id INT autoincrement, client_id TEXT, created_at TEXT,
                       portfolio_value, total_to_buy, total_to_sell, net_cash_needed, status TEXT
  rebalance_items    → item_id INT autoincrement, session_id INT, fund_id TEXT, fund_name TEXT,
                       action TEXT, amount REAL (NOT NULL), current_pct REAL (NOT NULL),
                       target_pct REAL, post_rebalance_pct REAL, is_model_fund INT (NOT NULL)
"""

import sqlite3
import os
from datetime import datetime
from flask import Flask, render_template, redirect, url_for, request, flash, abort

app = Flask(__name__)
app.secret_key = "rebalance-2024-multiuser"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "model_portfolio.db")


# ─────────────────────────────────────────────────────────────────────────────
#  DB HELPER
# ─────────────────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def get_all_clients(conn):
    return conn.execute(
        "SELECT client_id, client_name, total_invested FROM clients ORDER BY client_name"
    ).fetchall()


def get_client(conn, client_id):
    """Fetch one client row; returns None if not found."""
    return conn.execute(
        "SELECT client_id, client_name, total_invested FROM clients WHERE client_id = ?",
        (client_id,)
    ).fetchone()


def calculate_rebalance(conn, client_id):
    """
    Core rebalancing logic — works for any client_id.

    Returns dict:
      rows, total_portfolio, total_to_buy, total_to_sell, fresh_money_needed
    """
    # 1. Model funds
    model_rows = conn.execute(
        "SELECT fund_id, fund_name, asset_class, allocation_pct FROM model_funds"
    ).fetchall()
    model_map = {r["fund_id"]: dict(r) for r in model_rows}

    # 2. This client's holdings
    holding_rows = conn.execute(
        "SELECT fund_id, fund_name, current_value FROM client_holdings WHERE client_id = ?",
        (client_id,)
    ).fetchall()
    holding_map = {r["fund_id"]: dict(r) for r in holding_rows}

    # 3. Total portfolio = ALL holdings including non-model funds
    total_portfolio = sum(r["current_value"] for r in holding_rows)

    rows = []

    # 4a. Model funds — BUY / SELL / HOLD
    for fund_id, mf in model_map.items():
        h = holding_map.get(fund_id)
        current_value = h["current_value"] if h else 0.0
        target_pct    = mf["allocation_pct"]
        current_pct   = (current_value / total_portfolio * 100) if total_portfolio else 0.0

        # Use unrounded drift for the amount calculation (matches spec numbers exactly),
        # then round for display.
        drift         = target_pct - current_pct
        action        = "BUY" if drift > 0 else ("SELL" if drift < 0 else "HOLD")
        amount        = round(abs(drift) / 100 * total_portfolio) if action != "HOLD" else 0

        rows.append({
            "fund_id":            fund_id,
            "fund_name":          mf["fund_name"],
            "asset_class":        mf["asset_class"],
            "current_value":      current_value,
            "current_pct":        round(current_pct, 1),
            "target_pct":         target_pct,
            "drift":              round(drift, 1),
            "action":             action,
            "amount":             amount,
            "is_model_fund":      1,
            "post_rebalance_pct": target_pct,
        })

    # 4b. Non-model holdings → REVIEW
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
                "target_pct":         None,
                "drift":              None,
                "action":             "REVIEW",
                "amount":             0,      # NOT NULL in DB; 0 for REVIEW
                "is_model_fund":      0,
                "post_rebalance_pct": None,
            })

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


# ─────────────────────────────────────────────────────────────────────────────
#  ROUTES
# ─────────────────────────────────────────────────────────────────────────────

# ── Home — client selector ────────────────────────────────────────────────────
@app.route("/")
def home():
    conn    = get_db()
    clients = get_all_clients(conn)
    conn.close()
    return render_template("home.html", clients=clients)


# ── Rebalance screen for one client ──────────────────────────────────────────
@app.route("/client/<client_id>")
def index(client_id):
    conn   = get_db()
    client = get_client(conn, client_id)
    if client is None:
        conn.close()
        abort(404)
    data = calculate_rebalance(conn, client_id)
    conn.close()
    return render_template("index.html", client=client, **data)


# ── Save recommendation ───────────────────────────────────────────────────────
@app.route("/client/<client_id>/save", methods=["POST"])
def save(client_id):
    conn   = get_db()
    client = get_client(conn, client_id)
    if client is None:
        conn.close()
        abort(404)
    try:
        data = calculate_rebalance(conn, client_id)
        now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor = conn.execute(
            """INSERT INTO rebalance_sessions
               (client_id, created_at, portfolio_value, total_to_buy,
                total_to_sell, net_cash_needed, status)
               VALUES (?, ?, ?, ?, ?, ?, 'PENDING')""",
            (client_id, now, data["total_portfolio"],
             data["total_to_buy"], data["total_to_sell"], data["fresh_money_needed"])
        )
        session_id = cursor.lastrowid

        for row in data["rows"]:
            conn.execute(
                """INSERT INTO rebalance_items
                   (session_id, fund_id, fund_name, action, amount,
                    current_pct, target_pct, post_rebalance_pct, is_model_fund)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, row["fund_id"], row["fund_name"], row["action"],
                 row["amount"], row["current_pct"], row["target_pct"],
                 row["post_rebalance_pct"], row["is_model_fund"])
            )

        conn.commit()
        flash(f"✅ Recommendation saved! Session #{session_id}", "success")

    except Exception as e:
        conn.rollback()
        flash(f"❌ Error: {e}", "danger")
    finally:
        conn.close()

    return redirect(url_for("index", client_id=client_id))


# ── Current holdings ──────────────────────────────────────────────────────────
@app.route("/client/<client_id>/holdings")
def holdings(client_id):
    conn   = get_db()
    client = get_client(conn, client_id)
    if client is None:
        conn.close()
        abort(404)

    rows = conn.execute(
        "SELECT fund_id, fund_name, current_value FROM client_holdings "
        "WHERE client_id = ? ORDER BY current_value DESC",
        (client_id,)
    ).fetchall()

    total_value = sum(r["current_value"] for r in rows)
    conn.close()
    return render_template("holdings.html", client=client, rows=rows, total_value=total_value)


# ── Rebalance history ─────────────────────────────────────────────────────────
@app.route("/client/<client_id>/history")
def history(client_id):
    conn   = get_db()
    client = get_client(conn, client_id)
    if client is None:
        conn.close()
        abort(404)

    sessions = conn.execute(
        """SELECT session_id, created_at, portfolio_value, total_to_buy,
                  total_to_sell, net_cash_needed, status
           FROM rebalance_sessions
           WHERE client_id = ? ORDER BY created_at DESC""",
        (client_id,)
    ).fetchall()

    conn.close()
    return render_template("history.html", client=client, sessions=sessions)


# ── Session detail ────────────────────────────────────────────────────────────
@app.route("/client/<client_id>/history/<int:session_id>")
def session_detail(client_id, session_id):
    conn   = get_db()
    client = get_client(conn, client_id)
    if client is None:
        conn.close()
        abort(404)

    session = conn.execute(
        "SELECT * FROM rebalance_sessions WHERE session_id = ? AND client_id = ?",
        (session_id, client_id)
    ).fetchone()

    if session is None:
        conn.close()
        abort(404)

    items = conn.execute(
        "SELECT * FROM rebalance_items WHERE session_id = ?",
        (session_id,)
    ).fetchall()

    conn.close()
    return render_template("session_detail.html", client=client, session=session, items=items)


# ── Edit model portfolio ──────────────────────────────────────────────────────
@app.route("/edit-plan", methods=["GET", "POST"])
def edit_plan():
    conn = get_db()

    if request.method == "POST":
        funds       = conn.execute("SELECT fund_id, fund_name FROM model_funds").fetchall()
        updates     = []
        total_alloc = 0.0
        error       = None

        try:
            for fund in funds:
                fid = fund["fund_id"]
                pct = float(request.form.get(f"alloc_{fid}", "").strip())
                if pct < 0:
                    error = f"Allocation for {fid} cannot be negative."
                    break
                total_alloc += pct
                updates.append((pct, fid))
        except (ValueError, TypeError):
            error = "All fields must be valid numbers."

        if error is None and abs(total_alloc - 100.0) > 0.001:
            error = (f"Allocations must total exactly 100%. "
                     f"Yours total {round(total_alloc, 2)}%.")

        if error:
            model_funds = conn.execute(
                "SELECT fund_id, fund_name, asset_class, allocation_pct FROM model_funds"
            ).fetchall()
            conn.close()
            return render_template("edit_plan.html", model_funds=model_funds, error=error)

        for (pct, fid) in updates:
            conn.execute("UPDATE model_funds SET allocation_pct = ? WHERE fund_id = ?", (pct, fid))
        conn.commit()
        conn.close()

        flash("✅ Model portfolio updated! All client views now use the new allocations.", "success")
        return redirect(url_for("home"))

    model_funds = conn.execute(
        "SELECT fund_id, fund_name, asset_class, allocation_pct FROM model_funds"
    ).fetchall()
    conn.close()
    return render_template("edit_plan.html", model_funds=model_funds, error=None)


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True, port=5000)
