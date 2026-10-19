"""
NSE Performance Dashboard — optimized & redesigned.

Key improvements over the original:
  PERFORMANCE
  - Vectorized `calculate_returns` (pandas merge, not a Python loop per group).
  - Single reusable BigQuery client instead of a new client per query.
  - One consolidated Upstox LTP fetch per tick (indices + selected constituents
    combined) via a dcc.Store, instead of two independent API calls firing on
    every interval tick.
  - Theme switching is now a **clientside** callback that just flips a
    `data-theme` attribute + CSS variables — no server round-trip, no giant
    12-output callback re-shipping every style dict on every click.
  - In-memory TTL cache for the (large, network-fetched) Upstox instrument map
    and for BigQuery pulls, with a manual "Refresh Data" button instead of only
    ever loading once at process start.
  - Paginated constituents table so large indices don't render hundreds of
    rows at once.

  SECURITY
  - The Upstox access token is no longer hardcoded in source. It's read from
    the UPSTOX_ACCESS_TOKEN environment variable.

  LOOK & FEEL / NEW FUNCTIONALITY
  - KPI strip: advancers vs decliners, average move, best/worst performer —
    recomputed live off the same data already in the table.
  - Click an index to see its historical trend line (not just constituents).
  - Search box to filter constituents by symbol.
  - Click a constituent to overlay it on the index chart (secondary axis, same
    period selector and +/-2% Y-range rules); correlation-vs-index heat-map column.
  - Live "last updated" clock + status pill so it's obvious whether you're
    looking at fresh or stale data.
  - Design system driven by CSS custom properties (light/dark), Inter font,
    subtle hover/transition polish, custom scrollbars.

  LAYOUT / MOUNTING (this version)
  - The Indices Performance card and the Stock Performance (constituents) card sit side by side at 50:50. Compact fonts
    and tight cell padding keep both tables fully visible without horizontal scrolling.
  - The large page title is replaced by a slim toolbar (status, Start/Stop live, Refresh, theme).
  - Callbacks are registered on `app` (not the global `dash.callback`), so this page can be mounted next to other Dash
    apps inside Analysis_Master_Dashboard.py without callbacks leaking between apps.
  - If UPSTOX_ACCESS_TOKEN is not set, today's Upstox token is read from GCS (same file the Stock / Index dashboards use).
"""

import ast
import gzip
import json
import os
import time

import dash
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
from dash import Dash, Input, Output, State, dash_table, dcc, html, no_update
import dash_bootstrap_components as dbc

# ==============================================================================
# CONFIGURATION
# ==============================================================================
PROJECT_ID = os.environ.get("BQ_PROJECT_ID", "phrasal-fire-373510")
INDICES_TABLE = f"{PROJECT_ID}.Big_Bull_Analysis.Master_Data_Indices"
EQUITY_TABLE = f"{PROJECT_ID}.Big_Bull_Analysis.Master_Data_Equity"
MAPPING_PATH = os.environ.get("INDEX_MAPPING_PATH", "gs://bba_support_files/index_mapping.csv")

# NEVER hardcode credentials in source. Set this in your environment / secret
# manager before running: export UPSTOX_ACCESS_TOKEN="..."
UPSTOX_ACCESS_TOKEN = os.environ.get("UPSTOX_ACCESS_TOKEN", "")

# Fallback when the environment variable is empty: today's token saved in GCS by the token generator (the same file the
# Stock and Index dashboards read), so every page of the master shares one Upstox login.
TOKEN_BUCKET = os.environ.get("TOKEN_BUCKET", "bba_access_token")
_gcs_token = {"value": "", "checked_at": 0.0}


def get_upstox_token():
    """UPSTOX_ACCESS_TOKEN if set, else today's token from GCS (re-checked every 60 s until found, then every 10 min)."""
    if UPSTOX_ACCESS_TOKEN:
        return UPSTOX_ACCESS_TOKEN
    now = time.time()
    wait = 600 if _gcs_token["value"] else 60
    if now - _gcs_token["checked_at"] < wait:
        return _gcs_token["value"]
    _gcs_token["checked_at"] = now
    try:
        from datetime import date
        from google.cloud import storage
        blob = storage.Client().bucket(TOKEN_BUCKET).blob(f"upstox/{date.today()}.json")
        _gcs_token["value"] = json.loads(blob.download_as_text()).get("access_token") or ""
    except Exception as e:
        print(f"[upstox-token] GCS fallback unavailable: {e}")
    return _gcs_token["value"]

INTERVALS = [7, 14, 30, 45, 90, 365]
LIVE_REFRESH_MS = 5000
INSTRUMENT_MAP_TTL_SECS = 6 * 60 * 60  # instrument list changes rarely; 6h is plenty
BQ_DATA_TTL_SECS = 15 * 60             # manual "Refresh Data" also busts this early


# ==============================================================================
# LIGHTWEIGHT TTL CACHE (avoids re-fetching the ~large instrument file or
# re-querying BigQuery on every page load / callback)
# ==============================================================================
class TTLCache:
    def __init__(self):
        self._store = {}

    def get(self, key):
        entry = self._store.get(key)
        if not entry:
            return None
        value, expires_at = entry
        if time.time() > expires_at:
            return None
        return value

    def set(self, key, value, ttl_secs):
        self._store[key] = (value, time.time() + ttl_secs)

    def invalidate(self, key=None):
        if key is None:
            self._store.clear()
        else:
            self._store.pop(key, None)


_cache = TTLCache()


# ==============================================================================
# UPSTOX INSTRUMENT MAP + LIVE QUOTES
# ==============================================================================
def load_upstox_instrument_map():
    cached = _cache.get("instrument_map")
    if cached is not None:
        return cached

    url = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
    master_map = {}
    try:
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        with gzip.GzipFile(fileobj=response.raw) as gz:
            raw_data = json.loads(gz.read().decode("utf-8"))

        for item in raw_data:
            if item.get("exchange") != "NSE":
                continue
            segment = item.get("segment")
            inst_key = item.get("instrument_key")
            if not segment or not inst_key:
                continue

            if segment == "NSE_EQ":
                trading_sym = item.get("trading_symbol")
                if trading_sym:
                    master_map[trading_sym.upper()] = inst_key
            elif segment == "NSE_INDEX":
                name = item.get("name")
                if name:
                    master_map[name.upper()] = inst_key
                trading_sym = item.get("trading_symbol")
                if trading_sym:
                    master_map[trading_sym.upper()] = inst_key

        _cache.set("instrument_map", master_map, INSTRUMENT_MAP_TTL_SECS)
    except Exception as e:
        print(f"[instrument-map] fetch/parse failed: {e}")
    return master_map


def fetch_upstox_ltp(raw_identifiers):
    """Batched LTP lookup for a list of symbols/index names. Returns
    {ORIGINAL_SYMBOL: {"ltp":..., "close":...}}."""
    token = get_upstox_token()
    if not raw_identifiers or not token:
        return {}

    instrument_map = load_upstox_instrument_map()
    if not instrument_map:
        return {}

    mapped_keys = sorted({
        instrument_map[str(item).upper()]
        for item in raw_identifiers
        if str(item).upper() in instrument_map
    })
    if not mapped_keys:
        return {}

    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    url = "https://api.upstox.com/v3/market-quote/ltp?instrument_key=" + ",".join(mapped_keys)

    try:
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()
        res_data = response.json().get("data", {})
        live_data = {}
        for key, val in res_data.items():
            original_symbol = key.replace("NSE_EQ:", "").replace("NSE_INDEX:", "").upper()
            if original_symbol:
                live_data[original_symbol] = {"ltp": val.get("last_price"), "close": val.get("cp")}
        return live_data
    except Exception as e:
        print(f"[upstox-ltp] request failed: {e}")
        return {}


# ==============================================================================
# BIGQUERY DATA (single client, cached pulls, synthetic fallback for local/demo runs)
# ==============================================================================
_bq_client = None


def get_bq_client():
    global _bq_client
    if _bq_client is None:
        from google.cloud import bigquery
        _bq_client = bigquery.Client()
    return _bq_client


def fetch_bq_data(query):
    try:
        return get_bq_client().query(query).to_dataframe()
    except Exception as e:
        print(f"[bigquery] query failed, falling back to demo data: {e}")
        return None


def load_mapping_file():
    cached = _cache.get("mapping_file")
    if cached is not None:
        return cached
    try:
        df = pd.read_csv(MAPPING_PATH)
    except Exception as e:
        print(f"[mapping-file] could not read {MAPPING_PATH}, using demo mapping: {e}")
        df = pd.DataFrame({
            "Index Name": ["NIFTY 50", "NIFTY BANK", "NIFTY NEXT 50"],
            "Constituents": [
                str([f"STOCK{i}" for i in range(1, 21)]),
                str([f"BANK{i}" for i in range(1, 13)]),
                str([f"NEXT{i}" for i in range(1, 16)]),
            ],
        })
    df["Index Name"] = df["Index Name"].str.upper()
    _cache.set("mapping_file", df, ttl_secs=60 * 60 * 24)
    return df


def _synthetic_series(names, base_range, periods=400, vol=0.01):
    dates = pd.date_range(end=pd.Timestamp.now(), periods=periods)
    rows = []
    rng = np.random.default_rng(seed=42)
    for name in names:
        val = base_range()
        for d in dates:
            val *= (1 + rng.normal(0, vol))
            rows.append((name, d, val))
    return rows


def get_indices_data():
    query = f"SELECT UPPER(Index_Name) as Index_Name, TIMESTAMP, Closing_Index_Value FROM `{INDICES_TABLE}`"
    df = fetch_bq_data(query)
    if df is None:
        rows = _synthetic_series(
            ["NIFTY 50", "NIFTY BANK", "NIFTY NEXT 50"],
            base_range=lambda: 20000.0,
            vol=0.005,
        )
        df = pd.DataFrame(rows, columns=["Index_Name", "TIMESTAMP", "Closing_Index_Value"])
    df["TIMESTAMP"] = pd.to_datetime(df["TIMESTAMP"]).dt.tz_localize(None)
    return df


def get_equity_data():
    query = f"SELECT SYMBOL, TIMESTAMP, EQ_CLOSE_PRICE FROM `{EQUITY_TABLE}`"
    df = fetch_bq_data(query)
    if df is None:
        mapping = load_mapping_file()
        all_symbols = set()
        for const in mapping["Constituents"]:
            try:
                all_symbols.update(ast.literal_eval(const))
            except Exception:
                pass
        rows = _synthetic_series(list(all_symbols)[:100], base_range=lambda: 1000 + np.random.randint(0, 2000))
        df = pd.DataFrame(rows, columns=["SYMBOL", "TIMESTAMP", "EQ_CLOSE_PRICE"])
    df["TIMESTAMP"] = pd.to_datetime(df["TIMESTAMP"]).dt.tz_localize(None)
    return df


def load_all_data(force=False):
    """Pulls indices + equity history (cached, TTL-bound) and returns both the
    raw frames (needed for the trend chart) and the computed returns tables."""
    if force:
        _cache.invalidate("indices_hist")
        _cache.invalidate("equity_hist")

    idx_hist = _cache.get("indices_hist")
    if idx_hist is None:
        idx_hist = get_indices_data()
        _cache.set("indices_hist", idx_hist, BQ_DATA_TTL_SECS)

    eq_hist = _cache.get("equity_hist")
    if eq_hist is None:
        eq_hist = get_equity_data()
        _cache.set("equity_hist", eq_hist, BQ_DATA_TTL_SECS)

    indices_returns = calculate_returns(idx_hist, "Index_Name", "Closing_Index_Value")
    equity_returns = calculate_returns(eq_hist, "SYMBOL", "EQ_CLOSE_PRICE")

    indices_returns["Current LTP"] = indices_returns["Latest Price"]
    indices_returns["Today's Change %"] = 0.0
    indices_returns = indices_returns.drop(columns=["Latest Price"])  # superseded by "Current LTP"

    idx_cols = list(indices_returns.columns)
    ordered_idx_cols = ["Index_Name", "Current LTP", "Today's Change %"] + [
        c for c in idx_cols if c not in ("Index_Name", "Current LTP", "Today's Change %")
    ]
    indices_returns = indices_returns[ordered_idx_cols]

    return idx_hist, eq_hist, indices_returns, equity_returns


def calculate_returns(df, group_col, price_col):
    """Vectorized replacement for the original per-group Python loop.
    For each of the (few) INTERVALS we do one groupby + one merge across
    *all* groups at once, rather than looping per group for every interval.
    """
    df = df.sort_values("TIMESTAMP")
    latest_date = df["TIMESTAMP"].max()

    latest_slice = (
        df.groupby(group_col, as_index=False)
        .last()[[group_col, price_col]]
        .rename(columns={price_col: "Latest Price"})
    )
    result = latest_slice.copy()
    result["Latest Price"] = result["Latest Price"].round(2)

    for days in INTERVALS:
        col_name = f"{days}D %"
        target_date = latest_date - pd.Timedelta(days=days)
        past = df[df["TIMESTAMP"] <= target_date]
        if past.empty:
            result[col_name] = None
            continue
        past_slice = (
            past.groupby(group_col, as_index=False)
            .last()[[group_col, price_col]]
            .rename(columns={price_col: "_past_price"})
        )
        merged = result[[group_col, "Latest Price"]].merge(past_slice, on=group_col, how="left")
        pct = ((merged["Latest Price"] - merged["_past_price"]) / merged["_past_price"]) * 100
        result[col_name] = pct.round(2).values

    return result


def compute_kpis(indices_records):
    """Advancers/decliners/avg move/best/worst from whatever is currently in
    the indices table (updates automatically as live ticks land)."""
    if not indices_records:
        return {"advancers": 0, "decliners": 0, "avg_change": 0.0, "best": ("—", 0.0), "worst": ("—", 0.0)}

    df = pd.DataFrame(indices_records)
    change_col = "Today's Change %"
    if change_col not in df.columns:
        return {"advancers": 0, "decliners": 0, "avg_change": 0.0, "best": ("—", 0.0), "worst": ("—", 0.0)}

    changes = pd.to_numeric(df[change_col], errors="coerce").fillna(0.0)
    advancers = int((changes > 0).sum())
    decliners = int((changes < 0).sum())
    avg_change = round(float(changes.mean()), 2) if len(changes) else 0.0

    best_idx = changes.idxmax() if len(changes) else None
    worst_idx = changes.idxmin() if len(changes) else None
    best = (df.loc[best_idx, "Index_Name"], round(changes[best_idx], 2)) if best_idx is not None else ("—", 0.0)
    worst = (df.loc[worst_idx, "Index_Name"], round(changes[worst_idx], 2)) if worst_idx is not None else ("—", 0.0)

    return {"advancers": advancers, "decliners": decliners, "avg_change": avg_change, "best": best, "worst": worst}


# ==============================================================================
# STYLING HELPERS
# ==============================================================================
CORR_WINDOW_DAYS = 365  # correlation is computed on daily returns over the trailing year
CORR_MIN_OBS = 20
_corr_cache = {}        # {index_name: Series(symbol -> corr)}; cleared on "Refresh Data"


def get_constituents(idx_name):
    """Constituent symbols for an index (empty list if unmapped/unparseable)."""
    row = mapping_df[mapping_df["Index Name"] == idx_name]
    if row.empty:
        return []
    try:
        return list(ast.literal_eval(row.iloc[0]["Constituents"]))
    except Exception:
        return []


def compute_correlations(idx_name, symbols):
    """Pearson correlation of each stock's daily returns vs the index's daily
    returns over the trailing CORR_WINDOW_DAYS. (Returns, not raw prices — price
    levels trend and would give spuriously high correlations.) Cached per index."""
    if idx_name in _corr_cache:
        return _corr_cache[idx_name]

    idx = idx_hist_df[idx_hist_df["Index_Name"] == idx_name]
    if idx.empty or not symbols:
        return pd.Series(dtype=float)

    idx_px = idx.assign(d=idx["TIMESTAMP"].dt.normalize()).groupby("d")["Closing_Index_Value"].last()
    cutoff = idx_px.index.max() - pd.Timedelta(days=CORR_WINDOW_DAYS)
    idx_ret = idx_px[idx_px.index >= cutoff].pct_change(fill_method=None)

    eq = eq_hist_df[eq_hist_df["SYMBOL"].isin(symbols)]
    eq = eq.assign(d=eq["TIMESTAMP"].dt.normalize())
    eq = eq[eq["d"] >= cutoff]
    wide = eq.pivot_table(index="d", columns="SYMBOL", values="EQ_CLOSE_PRICE", aggfunc="last")
    rets = wide.pct_change(fill_method=None)

    corr = rets.apply(lambda s: idx_ret.corr(s, min_periods=CORR_MIN_OBS)).round(2)
    _corr_cache[idx_name] = corr
    return corr


def get_correlation_styles(col="Correlation"):
    """Heat-map: green for positive, red for negative, intensity scales with |corr|."""
    styles = []
    for i in range(1, 11):
        t = i / 10
        alpha = round(0.10 + i * 0.075, 3)
        text = "var(--text-main)" if i < 6 else "white"
        styles.append({"if": {"filter_query": f"{{{col}}} >= {t}", "column_id": col},
                       "backgroundColor": f"rgba(22, 163, 74, {alpha})", "color": text})
    for i in range(1, 11):
        t = i / 10
        alpha = round(0.10 + i * 0.075, 3)
        text = "var(--text-main)" if i < 6 else "white"
        styles.append({"if": {"filter_query": f"{{{col}}} <= {-t}", "column_id": col},
                       "backgroundColor": f"rgba(220, 38, 38, {alpha})", "color": text})
    return styles


def get_conditional_styles(columns):
    styles = []
    for col in columns:
        if " %" in col or "Change" in col:
            for i in range(1, 6):
                styles.append({
                    "if": {"filter_query": f"{{{col}}} >= {i * 2}", "column_id": col},
                    "backgroundColor": f"rgba(34, 197, 94, {0.08 + i * 0.14})", "color": "#052e16" if i < 4 else "white",
                })
            for i in range(1, 6):
                styles.append({
                    "if": {"filter_query": f"{{{col}}} <= {-i * 2}", "column_id": col},
                    "backgroundColor": f"rgba(239, 68, 68, {0.08 + i * 0.14})", "color": "#450a0a" if i < 4 else "white",
                })
    styles.append({"if": {"state": "selected"}, "backgroundColor": "var(--select-bg)", "border": "1px solid var(--select-border)"})
    return styles


TABLE_CELL_STYLE = {
    "textAlign": "left", "padding": "12px 16px", "backgroundColor": "var(--card-bg)",
    "color": "var(--text-main)", "fontFamily": "Inter, sans-serif",
    "border": "1px solid var(--border)", "fontSize": "0.875rem",
}
TABLE_HEADER_STYLE = {
    "fontWeight": "600", "backgroundColor": "var(--header-bg)", "color": "var(--text-muted)",
    "textTransform": "uppercase", "fontSize": "0.75rem", "border": "1px solid var(--border)",
}
CARD_STYLE = {
    "backgroundColor": "var(--card-bg)", "padding": "2rem", "borderRadius": "12px",
    "border": "1px solid var(--border)", "boxShadow": "var(--shadow)",
}

# Both tables (~9-10 columns each) share the page 50:50 (see layout). To avoid a horizontal scrollbar the cells use a
# small font and tight padding, numbers are centered, and headers / long names wrap instead of forcing the column wide.
INDICES_CELL_STYLE = {
    **TABLE_CELL_STYLE, "textAlign": "center", "padding": "6px 4px", "fontSize": "0.72rem",
    "whiteSpace": "normal", "height": "auto", "minWidth": "0px",
}
INDICES_HEADER_STYLE = {
    **TABLE_HEADER_STYLE, "textAlign": "center", "whiteSpace": "normal", "height": "auto", "lineHeight": "1.2",
    "fontSize": "0.62rem", "padding": "6px 4px",
}
INDICES_CELL_CONDITIONAL = [
    {"if": {"column_id": "Index_Name"}, "textAlign": "left", "minWidth": "110px"},
]
# Constituents (stock performance) table: same compact look
CONSTITUENT_CELL_STYLE = INDICES_CELL_STYLE
CONSTITUENT_HEADER_STYLE = INDICES_HEADER_STYLE
CONSTITUENT_CELL_CONDITIONAL = [
    {"if": {"column_id": "SYMBOL"}, "textAlign": "left", "minWidth": "80px"},
]
# Cards that sit side by side (50:50): tighter padding than the full-width CARD_STYLE
HALF_CARD_STYLE = {**CARD_STYLE, "padding": "1rem 1.1rem", "height": "100%"}

INDEX_STRING = """
<!DOCTYPE html>
<html data-theme="light">
    <head>
        {%metas%}
        <title>{%title%}</title>
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
        {%favicon%}
        {%css%}
        <style>
            :root {
                --bg: #f8fafc; --card-bg: #ffffff; --text-main: #0f172a; --text-muted: #64748b;
                --border: #e2e8f0; --header-bg: #f1f5f9; --select-bg: #f0f9ff; --select-border: #0284c7;
                --accent: #0284c7; --shadow: 0 1px 3px 0 rgb(0 0 0 / 0.06);
                --up: #16a34a; --down: #dc2626;
            }
            html[data-theme="dark"] {
                --bg: #0b1220; --card-bg: #161f31; --text-main: #f8fafc; --text-muted: #94a3b8;
                --border: #253148; --header-bg: #0f172a; --select-bg: rgba(56,189,248,0.12); --select-border: #38bdf8;
                --accent: #38bdf8; --shadow: 0 4px 12px -2px rgb(0 0 0 / 0.5);
                --up: #4ade80; --down: #f87171;
            }
            body {
                background-color: var(--bg) !important;
                transition: background-color 0.25s ease, color 0.25s ease;
                font-family: 'Inter', sans-serif;
            }
            ::-webkit-scrollbar { height: 8px; width: 8px; }
            ::-webkit-scrollbar-track { background: transparent; }
            ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 8px; }
            .kpi-card {
                background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px;
                padding: 1.1rem 1.3rem; box-shadow: var(--shadow); transition: transform .15s ease, box-shadow .15s ease;
            }
            .kpi-card:hover { transform: translateY(-2px); box-shadow: 0 8px 20px -6px rgb(0 0 0 / 0.15); }
            .kpi-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem 2rem; }
            @media (max-width: 480px) { .kpi-grid { grid-template-columns: 1fr; } }
            .kpi-label { color: var(--text-muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: .04em; font-weight: 600; }
            .kpi-value { color: var(--text-main); font-size: 1.5rem; font-weight: 700; margin-top: 2px; }
            .pill { border-radius: 999px; padding: 3px 10px; font-size: 0.72rem; font-weight: 600; display: inline-flex; align-items: center; gap: 6px; }
            .pill-live { background: rgba(34,197,94,0.15); color: var(--up); }
            .pill-paused { background: rgba(148,163,184,0.2); color: var(--text-muted); }
            .dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
            .dot-live { animation: pulse 1.4s infinite; }
            @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.35; } 100% { opacity: 1; } }
            .fade-card { animation: fadein .4s ease; }
            @keyframes fadein { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
            .app-toolbar { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap;
                           gap: .5rem 1rem; padding: 0 .25rem .75rem; margin-bottom: 1rem; border-bottom: 1px solid var(--border); }
            .toolbar-title { color: var(--text-main); font-size: 1.15rem; font-weight: 700; letter-spacing: -.01em; }
            .toolbar-sub { color: var(--text-muted); font-size: .8rem; margin-left: .75rem; }
        </style>
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>
"""

# ==============================================================================
# INITIAL DATA LOAD
# ==============================================================================
mapping_df = load_mapping_file()
idx_hist_df, eq_hist_df, indices_returns, equity_returns = load_all_data()

# ==============================================================================
# APP
# ==============================================================================
app = Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP], title="NSE Performance Dashboard")
app.index_string = INDEX_STRING
server = app.server

app.layout = html.Div(
    id="page-wrapper",
    children=[
        dcc.Interval(id="live-interval", interval=LIVE_REFRESH_MS, disabled=True),
        dcc.Store(id="live-quotes-store"),
        dcc.Store(id="last-updated-store"),
        dcc.Store(id="selected-stock-store"),

        dbc.Container([
            # Slim app toolbar (replaces the large page title + divider)
            html.Div(className="app-toolbar", children=[
                html.Div([
                    html.Span("NSE Performance", className="toolbar-title"),
                    html.Span("Real-time index analysis and constituent tracking",
                              className="toolbar-sub d-none d-lg-inline"),
                ]),
                html.Div(className="d-flex align-items-center flex-wrap gap-2", children=[
                    html.Span(id="status-pill", className="pill pill-paused me-2",
                              children=[html.Span(className="dot"), "Paused"]),
                    html.Small(id="last-updated-label", children="Not yet updated",
                               style={"color": "var(--text-muted)"}, className="me-2"),
                    dbc.ButtonGroup([
                        dbc.Button("▶ Start Live", id="btn-start-refresh", color="success", size="sm"),
                        dbc.Button("⏸ Stop", id="btn-stop-refresh", color="danger", size="sm", disabled=True),
                    ], className="shadow-sm"),
                    dbc.Button("⟳ Refresh Data", id="btn-refresh-data", outline=True, color="primary",
                               size="sm", className="shadow-sm"),
                    dbc.Button("🌙 Dark Mode", id="theme-toggle", n_clicks=0, outline=True, color="secondary",
                               size="sm", className="shadow-sm"),
                ]),
            ]),

            # Market snapshot (left 50%) + trend chart (right 50%)
            dbc.Row([
                dbc.Col(html.Div(id="kpi-row"), width=12, lg=6, className="mb-4 mb-lg-0"),
                dbc.Col(
                    html.Div(id="trend-chart-card", className="fade-card", style={**CARD_STYLE, "minHeight": "100%"}, children=[
                        html.Div(className="d-flex justify-content-between align-items-center flex-wrap gap-2 mb-2", children=[
                            html.Div(id="trend-title", children=[
                                html.P("Select an index below to see its historical trend.", style={"color": "var(--text-muted)"}),
                            ]),
                            dbc.RadioItems(
                                id="trend-period-toggle",
                                options=[
                                    {"label": "Full History", "value": "full"},
                                    {"label": "1Y", "value": "1y"},
                                    {"label": "6M", "value": "6m"},
                                    {"label": "3M", "value": "3m"},
                                    {"label": "1M", "value": "1m"},
                                ],
                                value="full", inline=True,
                                style={"display": "none", "color": "var(--text-muted)", "fontSize": "0.8rem"},
                            ),
                        ]),
                        html.Div(id="trend-graph-container"),
                    ]),
                    width=12, lg=6,
                ),
            ], className="mb-4"),

            # Indices performance (left 50%) | Stock performance / constituents (right 50%)
            dbc.Row([
                dbc.Col(
                    html.Div(id="table-card", className="fade-card", style=HALF_CARD_STYLE, children=[
                        html.H4("Indices Performance", className="fw-semibold mb-2",
                                style={"color": "var(--text-main)", "fontSize": "1rem"}),
                        dash_table.DataTable(
                            id="indices-table",
                            columns=[{"name": i, "id": i} for i in indices_returns.columns],
                            data=indices_returns.to_dict("records"),
                            sort_action="native", row_selectable="single",
                            style_table={"width": "100%", "minWidth": "100%", "overflowX": "auto"},
                            style_cell=INDICES_CELL_STYLE,
                            style_header=INDICES_HEADER_STYLE,
                            style_cell_conditional=INDICES_CELL_CONDITIONAL,
                            style_data_conditional=get_conditional_styles(indices_returns.columns),
                        ),
                    ]),
                    width=12, lg=6, className="mb-4 mb-lg-0",
                ),
                dbc.Col(
                    html.Div(id="constituent-section", className="fade-card", style=HALF_CARD_STYLE, children=[
                        html.Div(id="constituent-header",
                                 children=[html.P("Select an index to see its constituents.", style={"color": "var(--text-muted)"})]),
                        html.Div(className="d-flex align-items-center justify-content-between flex-wrap gap-2 mb-2", children=[
                            dbc.Input(id="constituent-search", type="text", placeholder="Filter by symbol…", value="",
                                       size="sm", style={"maxWidth": "260px", "display": "none"}),
                            dbc.RadioItems(
                                id="constituent-view-toggle",
                                options=[{"label": "Paginated", "value": "paginated"}, {"label": "View all", "value": "all"}],
                                value="paginated", inline=True, style={"display": "none", "color": "var(--text-muted)", "fontSize": "0.85rem"},
                            ),
                        ]),
                        html.Div(id="constituent-table-container", style={"display": "none"}, children=[
                            html.P("Click a row to overlay that stock on the index chart (right axis).",
                                   style={"color": "var(--text-muted)", "fontSize": "0.75rem"}),
                            dash_table.DataTable(
                                id="constituent-table",
                                columns=[], data=[], selected_rows=[],
                                row_selectable="single", sort_action="native",
                                page_action="native", page_size=15,
                                style_table={"overflowX": "auto"},
                                style_cell=CONSTITUENT_CELL_STYLE,
                                style_header=CONSTITUENT_HEADER_STYLE,
                                style_cell_conditional=CONSTITUENT_CELL_CONDITIONAL,
                            ),
                        ]),
                    ]),
                    width=12, lg=6,
                ),
            ], className="mb-4"),
        ], fluid=True, style={"padding": "1rem 1.25rem 2rem"}),
    ],
)


# ==============================================================================
# CALLBACKS
# ==============================================================================

# --- Instant, server-free theme toggle -------------------------------------
app.clientside_callback(
    """
    function(n_clicks) {
        const root = document.documentElement;
        const isDark = root.getAttribute('data-theme') === 'dark';
        const next = isDark ? 'light' : 'dark';
        root.setAttribute('data-theme', next);
        return next === 'dark' ? '☀️ Light Mode' : '🌙 Dark Mode';
    }
    """,
    Output("theme-toggle", "children"),
    Input("theme-toggle", "n_clicks"),
    prevent_initial_call=True,
)


@app.callback(
    Output("live-interval", "disabled"),
    Output("btn-start-refresh", "disabled"),
    Output("btn-stop-refresh", "disabled"),
    Output("status-pill", "children"),
    Output("status-pill", "className"),
    Input("btn-start-refresh", "n_clicks"),
    Input("btn-stop-refresh", "n_clicks"),
    prevent_initial_call=True,
)
def control_refresh(start_clicks, stop_clicks):
    ctx = dash.callback_context
    button_id = ctx.triggered[0]["prop_id"].split(".")[0] if ctx.triggered else None
    live = button_id == "btn-start-refresh"
    status_children = [html.Span(className="dot dot-live" if live else "dot"), "Live" if live else "Paused"]
    status_class = "pill pill-live me-3" if live else "pill pill-paused me-3"
    return (not live, live, not live, status_children, status_class)


@app.callback(
    Output("indices-table", "data", allow_duplicate=True),
    Output("indices-table", "columns"),
    Input("btn-refresh-data", "n_clicks"),
    prevent_initial_call=True,
)
def refresh_historical_data(n_clicks):
    """Recomputes returns from BigQuery (or demo data) without restarting the process."""
    global idx_hist_df, eq_hist_df, indices_returns, equity_returns
    idx_hist_df, eq_hist_df, indices_returns, equity_returns = load_all_data(force=True)
    _corr_cache.clear()  # correlations depend on the history we just reloaded
    columns = [{"name": i, "id": i} for i in indices_returns.columns]
    return indices_returns.to_dict("records"), columns


@app.callback(
    Output("live-quotes-store", "data"),
    Output("last-updated-store", "data"),
    Input("live-interval", "n_intervals"),
    State("indices-table", "data"),
    State("indices-table", "selected_rows"),
    prevent_initial_call=False,
)
def fetch_consolidated_quotes(n_intervals, indices_data, selected_rows):
    """Single Upstox call per tick covering indices + the currently selected
    index's constituents, instead of two independent calls per tick."""
    if not indices_data:
        return no_update, no_update

    symbols = {row["Index_Name"] for row in indices_data}

    if selected_rows:
        idx_name = indices_data[selected_rows[0]]["Index_Name"]
        row = mapping_df[mapping_df["Index Name"] == idx_name]
        if not row.empty:
            try:
                symbols.update(ast.literal_eval(row.iloc[0]["Constituents"]))
            except Exception:
                pass

    quotes = fetch_upstox_ltp(sorted(symbols))
    timestamp = pd.Timestamp.now().strftime("%H:%M:%S")
    return quotes, timestamp


@app.callback(
    Output("last-updated-label", "children"),
    Input("last-updated-store", "data"),
    prevent_initial_call=True,
)
def show_last_updated(ts):
    return f"Last updated: {ts}" if ts else no_update


@app.callback(
    Output("indices-table", "data", allow_duplicate=True),
    Input("live-quotes-store", "data"),
    State("indices-table", "data"),
    prevent_initial_call=True,
)
def apply_quotes_to_indices(quotes, current_data):
    if not quotes or not current_data:
        return no_update

    df_idx = pd.DataFrame(current_data)
    for i, row in df_idx.iterrows():
        q = quotes.get(row["Index_Name"])
        if q and q.get("ltp") is not None and q.get("close"):
            df_idx.at[i, "Current LTP"] = round(q["ltp"], 2)
            df_idx.at[i, "Today's Change %"] = round(((q["ltp"] - q["close"]) / q["close"]) * 100, 2)
    return df_idx.to_dict("records")


@app.callback(
    Output("kpi-row", "children"),
    Input("indices-table", "data"),
)
def render_kpis(indices_data):
    k = compute_kpis(indices_data)
    best_name, best_val = k["best"]
    worst_name, worst_val = k["worst"]

    def stat_block(label, value, sub=None, color=None):
        return html.Div(children=[
            html.Div(label, className="kpi-label"),
            html.Div(value, className="kpi-value", style={"color": color} if color else {}),
            html.Div(sub, style={"color": "var(--text-muted)", "fontSize": "0.75rem", "marginTop": "2px"}) if sub else None,
        ])

    return html.Div(className="kpi-card fade-card", style={"height": "100%"}, children=[
        html.H4("Market Snapshot", className="fw-semibold mb-3",
                style={"color": "var(--text-main)", "fontSize": "1.2rem"}),
        html.Div(className="kpi-grid", children=[
            stat_block("Advancers vs Decliners", f"{k['advancers']} / {k['decliners']}"),
            stat_block("Avg. Change Today", f"{k['avg_change']:+.2f}%",
                       color="var(--up)" if k["avg_change"] >= 0 else "var(--down)"),
            stat_block("Best Mover", f"{best_val:+.2f}%", sub=best_name, color="var(--up)"),
            stat_block("Worst Mover", f"{worst_val:+.2f}%", sub=worst_name, color="var(--down)"),
        ]),
    ])


@app.callback(
    Output("trend-title", "children"),
    Output("trend-period-toggle", "style"),
    Output("trend-graph-container", "children"),
    Input("indices-table", "selected_rows"),
    Input("trend-period-toggle", "value"),
    Input("selected-stock-store", "data"),
    State("indices-table", "data"),
)
def update_trend_chart(selected_rows, period, stock_symbol, data):
    hidden_toggle = {"display": "none"}
    visible_toggle = {"display": "block", "color": "var(--text-muted)", "fontSize": "0.8rem"}
    placeholder = html.P("Select an index below to see its historical trend.", style={"color": "var(--text-muted)"})

    if not selected_rows or not data:
        return placeholder, hidden_toggle, None

    idx_name = data[selected_rows[0]]["Index_Name"]
    hist = idx_hist_df[idx_hist_df["Index_Name"] == idx_name].sort_values("TIMESTAMP").dropna(subset=["Closing_Index_Value"])
    if hist.empty:
        return placeholder, hidden_toggle, None

    period_days = {"full": None, "1y": 365, "6m": 182, "3m": 91, "1m": 30}.get(period)
    latest_date = hist["TIMESTAMP"].max()
    cutoff = latest_date - pd.Timedelta(days=period_days) if period_days else None

    def window(df):
        """Same viewing-period rule for index and stock; fall back to all data if the window is empty."""
        if cutoff is None:
            return df
        view = df[df["TIMESTAMP"] >= cutoff]
        return view if not view.empty else df

    def padded_range(series, pad=0.02):  # +/-2% around the viewing period's low/high
        return [float(series.min()) * (1 - pad), float(series.max()) * (1 + pad)]

    view = window(hist)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=view["TIMESTAMP"], y=view["Closing_Index_Value"], mode="lines", name=idx_name,
        line=dict(color="#0284c7", width=2), fill="tozeroy", fillcolor="rgba(2,132,199,0.08)",
        hovertemplate="%{x|%d %b %Y}<br>" + idx_name + ": %{y:.2f}<extra></extra>",
    ))
    layout = dict(
        margin=dict(l=10, r=10, t=10, b=10), height=320,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, sans-serif", color="#64748b"),
        hovermode="x unified", showlegend=False,
        xaxis=dict(showgrid=False),
        yaxis=dict(showgrid=True, gridcolor="rgba(148,163,184,0.15)", range=padded_range(view["Closing_Index_Value"]),
                   automargin=True),
    )

    title_text = idx_name
    if stock_symbol and stock_symbol in get_constituents(idx_name):
        stock = eq_hist_df[eq_hist_df["SYMBOL"] == stock_symbol].sort_values("TIMESTAMP").dropna(subset=["EQ_CLOSE_PRICE"])
        if not stock.empty:
            sview = window(stock)
            fig.add_trace(go.Scatter(
                x=sview["TIMESTAMP"], y=sview["EQ_CLOSE_PRICE"], mode="lines", name=stock_symbol, yaxis="y2",
                line=dict(color="#f59e0b", width=2),
                hovertemplate=stock_symbol + ": %{y:.2f}<extra></extra>",
            ))
            layout["yaxis2"] = dict(overlaying="y", side="right", showgrid=False, automargin=True,
                                    range=padded_range(sview["EQ_CLOSE_PRICE"]))
            layout["showlegend"] = True
            layout["legend"] = dict(orientation="h", x=0, y=1.12)
            title_text = f"{idx_name} vs {stock_symbol}"
    fig.update_layout(**layout)

    period_labels = {"full": "full history", "1y": "1 year", "6m": "6 months", "3m": "3 months", "1m": "1 month"}
    title = html.H4(f"{title_text} — {period_labels.get(period, 'full history')}", className="fw-semibold mb-0",
                     style={"color": "var(--text-main)", "fontSize": "1.1rem"})
    return title, visible_toggle, dcc.Graph(figure=fig, config={"displayModeBar": False})


@app.callback(
    Output("selected-stock-store", "data"),
    Input("constituent-table", "selected_rows"),
    State("constituent-table", "data"),
)
def store_selected_stock(selected_rows, table_data):
    """Row selection -> symbol. Kept in a Store so the chart doesn't depend on row indices."""
    if selected_rows and table_data and selected_rows[0] < len(table_data):
        return table_data[selected_rows[0]]["SYMBOL"]
    return None


@app.callback(
    Output("constituent-header", "children"),
    Output("constituent-search", "style"),
    Output("constituent-view-toggle", "style"),
    Output("constituent-table-container", "style"),
    Output("constituent-table", "data"),
    Output("constituent-table", "columns"),
    Output("constituent-table", "style_data_conditional"),
    Output("constituent-table", "page_action"),
    Output("constituent-table", "page_size"),
    Output("constituent-table", "style_table"),
    Output("constituent-table", "selected_rows"),
    Input("indices-table", "selected_rows"),
    Input("live-quotes-store", "data"),
    Input("constituent-search", "value"),
    Input("constituent-view-toggle", "value"),
    State("indices-table", "data"),
    State("selected-stock-store", "data"),
)
def update_constituents(selected_rows, quotes, search_term, view_mode, data, selected_symbol):
    hidden = {"display": "none"}
    visible_search = {"maxWidth": "260px", "display": "block"}
    visible_toggle = {"display": "block", "color": "var(--text-muted)", "fontSize": "0.85rem"}

    def empty(msg):
        note = html.P(msg, style={"color": "var(--text-muted)"})
        return (note, hidden, hidden, hidden, [], [], [], "native", 15, {"overflowX": "auto"}, [])

    if not selected_rows or not data:
        return empty("Select an index to see its constituents.")

    idx_name = data[selected_rows[0]]["Index_Name"]
    constituents = get_constituents(idx_name)
    if not constituents:
        return empty(f"No constituent mapping found for {idx_name}.")

    df_const = equity_returns[equity_returns["SYMBOL"].isin(constituents)].copy()
    df_const["Current LTP"] = df_const["Latest Price"]
    df_const["Today's Change %"] = 0.0
    df_const["Correlation"] = df_const["SYMBOL"].map(compute_correlations(idx_name, constituents))

    if quotes:
        for i, r in df_const.iterrows():
            q = quotes.get(r["SYMBOL"])
            if q and q.get("ltp") is not None and q.get("close"):
                df_const.at[i, "Current LTP"] = round(q["ltp"], 2)
                df_const.at[i, "Today's Change %"] = round(((q["ltp"] - q["close"]) / q["close"]) * 100, 2)

    if search_term:
        df_const = df_const[df_const["SYMBOL"].str.contains(search_term.strip(), case=False, na=False)]

    df_const = df_const.sort_values("SYMBOL").reset_index(drop=True)  # stable order keeps row selection valid across ticks

    ordered_cols = ["SYMBOL", "Correlation", "Current LTP", "Today's Change %"] + [
        c for c in df_const.columns if c not in ("SYMBOL", "Correlation", "Current LTP", "Today's Change %", "Latest Price")
    ]
    df_const = df_const[ordered_cols]

    header = html.Div([
        html.H4(f"Constituents of {idx_name}", className="fw-semibold mb-0",
                style={"color": "var(--text-main)", "fontSize": "1rem"}),
        html.Span(f"{len(df_const)} shown", style={"color": "var(--text-muted)", "fontSize": "0.8rem"}),
    ], className="d-flex justify-content-between align-items-center mb-2")

    # Heat-map rules go before the trailing "selected" rule so row selection still wins visually.
    base_styles = get_conditional_styles(df_const.columns)
    cond_styles = base_styles[:-1] + get_correlation_styles("Correlation") + base_styles[-1:]

    view_all = view_mode == "all"
    style_table = {"overflowX": "auto", **({"maxHeight": "560px", "overflowY": "auto"} if view_all else {})}

    # Keep the chosen stock selected across live ticks / sorting / filtering (if it's still listed).
    selected = df_const.index[df_const["SYMBOL"] == selected_symbol].tolist()[:1] if selected_symbol else []

    return (
        header, visible_search, visible_toggle, {"display": "block"},
        df_const.to_dict("records"), [{"name": c, "id": c} for c in df_const.columns], cond_styles,
        "none" if view_all else "native", max(len(df_const), 1) if view_all else 15, style_table, selected,
    )


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))