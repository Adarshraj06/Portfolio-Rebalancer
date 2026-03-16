# Portfolio Rebalancer – Amit Sharma
### Wealth Builder 2025 | Match The Model Challenge

Flask web app that compares Amit Sharma's current holdings against the
advisor's model portfolio and computes exact BUY / SELL / REVIEW actions.

---

## Quickstart

```bash
# 1. Install the only dependency
pip install flask

# 2. Run the app  (model_portfolio.db must be in the same folder)
python app.py
```

Open **http://127.0.0.1:5000** in your browser.

> `create_db.py` is included for reference only — the provided
> `model_portfolio.db` already has all data pre-loaded. Do NOT run it
> against the real database.

---

## Project Structure

```
portfolio_app/
├── app.py                   # All Flask routes + calculation logic
├── model_portfolio.db       # Provided SQLite database (pre-loaded)
├── requirements.txt         # pip install flask
├── templates/
│   ├── base.html            # Navbar, flash messages, Bootstrap layout
│   ├── index.html           # / — Main rebalance comparison screen
│   ├── holdings.html        # /holdings — Current investments
│   ├── history.html         # /history — Past saved sessions
│   ├── session_detail.html  # /history/<id> — Fund breakdown of one session
│   └── edit_plan.html       # /edit-plan — Edit target allocations
└── static/
    └── style.css            # Row colouring + summary card styles
```

---

## Routes

| Route           | Method   | What it does                                    |
|-----------------|----------|-------------------------------------------------|
| `/`             | GET      | Show fund comparison table + summary cards      |
| `/save`         | POST     | Write to `rebalance_sessions` + `rebalance_items` |
| `/holdings`     | GET      | Show all of Amit's current holdings             |
| `/history`      | GET      | List all saved rebalance sessions               |
| `/history/<id>` | GET      | Fund-by-fund breakdown for one past session     |
| `/edit-plan`    | GET/POST | Edit model allocation %, validate total = 100%  |

---

## Calculation Logic

```
Total portfolio value  =  sum of ALL holdings (including non-model F006)
                       =  90,000 + 1,55,000 + 0 + 1,10,000 + 1,45,000 + 80,000
                       =  ₹5,80,000

For each model fund:
  current_pct  = current_value / total_portfolio × 100
  drift        = target_pct − current_pct
  action       = BUY  if drift > 0
                 SELL if drift < 0
                 HOLD if drift = 0
  amount       = round( |drift| / 100 × total_portfolio )

Non-model funds (F006 Axis Bluechip) → REVIEW, no amount.
```

### Expected output (matches challenge spec exactly)

| Fund | Today % | Plan % | Drift | Action | Amount |
|------|---------|--------|-------|--------|--------|
| F001 Mirae Asset Large Cap | 15.5% | 30% | +14.5% | **BUY** | ₹84,000 |
| F002 Parag Parikh Flexi Cap | 26.7% | 25% | −1.7% | **SELL** | ₹10,000 |
| F003 HDFC Mid Cap (₹0 holding) | 0.0% | 20% | +20.0% | **BUY** | ₹1,16,000 |
| F004 ICICI Prudential Bond | 19.0% | 15% | −4.0% | **SELL** | ₹23,000 |
| F005 Nippon India Gold ETF | 25.0% | 10% | −15.0% | **SELL** | ₹87,000 |
| F006 Axis Bluechip (not in plan) | 13.8% | — | — | **REVIEW** | — |

**Total BUY: ₹2,00,000 · Total SELL: ₹1,20,000 · Fresh money needed: ₹80,000**

---

## Database Schema Used

| Table | Read / Write | Key columns used |
|-------|-------------|-----------------|
| `clients` | READ | `client_id` (TEXT, e.g. `C001`), `client_name` |
| `model_funds` | READ + UPDATE | `fund_id`, `fund_name`, `asset_class`, `allocation_pct` |
| `client_holdings` | READ | `client_id`, `fund_id`, `fund_name`, `current_value` |
| `rebalance_sessions` | WRITE | `client_id`, `created_at`, `portfolio_value`, `total_to_buy`, `total_to_sell`, `net_cash_needed`, `status` |
| `rebalance_items` | WRITE | `session_id`, `fund_id`, `fund_name`, `action`, `amount`, `current_pct`, `target_pct`, `post_rebalance_pct`, `is_model_fund` |
