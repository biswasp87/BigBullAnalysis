"""
NSE Performance Dashboard — optimized & redesigned.

Key improvements over the original:
  PERFORMANCE
  - Vectorized `calculate_returns` (pandas merge, not a Python loop per group).
  - Upstox historical-candle API instead of BigQuery for index and stock closes (see DATA SOURCE below).
  - One consolidated Upstox LTP fetch per tick (indices + selected constituents
    combined) via a dcc.Store, instead of two independent API calls firing on
    every interval tick.
  - Theme switching is now a **clientside** callback that just flips a
    `data-theme` attribute + CSS variables — no server round-trip, no giant
    12-output callback re-shipping every style dict on every click.
  - In-memory TTL cache for the (large, network-fetched) Upstox instrument map
    and for the Upstox history pulls, with a manual "Refresh Data" button instead of only
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

  DATA SOURCE (this version)
  - Index and stock closing prices come from the Upstox historical-candle API (daily candles, HISTORY_DAYS calendar
    days) instead of BigQuery. Indices load at start-up (one request each, in parallel and rate-limited); a stock's
    history is fetched the first time an index containing it is selected, then cached. "Refresh Data" re-pulls it all.
  - Index constituents still come from the mapping file (index_mapping.csv).
"""

import ast
import gzip
import io
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from urllib.parse import quote

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
DATA_TTL_SECS = 15 * 60                # Upstox history cache; "Refresh Data" busts it early

# --- Upstox daily history (replaces BigQuery) ---
HISTORY_DAYS = int(os.environ.get("HISTORY_DAYS", "1825"))          # calendar days of daily closes per symbol (~5 years)
HIST_WORKERS = int(os.environ.get("UPSTOX_HIST_WORKERS", "8"))      # parallel requests
HIST_MAX_RPS = float(os.environ.get("UPSTOX_HIST_MAX_RPS", "20"))   # overall request-rate ceiling (keeps under Upstox limits)
UPSTOX_HIST_URL = "https://api.upstox.com/v3/historical-candle/{key}/days/1/{to_date}/{from_date}"
# Index names in the mapping file that Upstox spells differently (mapping-file name -> Upstox name, both upper-case).
# Names that cannot be resolved are listed in the console at start-up - add them here.
INDEX_NAME_ALIASES = {
    "NIFTY FINANCIAL SERVICES": "NIFTY FIN SERVICE",
    "NIFTY MIDCAP SELECT": "NIFTY MID SELECT",
}


# ==============================================================================
# LIGHTWEIGHT TTL CACHE (avoids re-fetching the ~large instrument file or
# re-pulling Upstox history on every page load / callback)
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
def _instrument_map_from_csv():
    """Fast path: the CSV instrument file parses in a few seconds with pandas (the JSON file is far heavier)."""
    url = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    wanted = {"instrument_key", "tradingsymbol", "trading_symbol", "name", "instrument_type", "exchange", "segment"}
    df = pd.read_csv(io.BytesIO(response.content), compression="gzip", dtype=str, low_memory=False,
                     usecols=lambda c: c in wanted)
    sym_col = "tradingsymbol" if "tradingsymbol" in df.columns else "trading_symbol"
    seg_col = "segment" if "segment" in df.columns else "exchange"
    if not {"instrument_key", sym_col, seg_col}.issubset(df.columns):
        raise ValueError(f"unexpected instrument columns: {list(df.columns)}")

    seg = df[seg_col].astype(str)
    master_map = {}
    eq = df[seg == "NSE_EQ"]
    if "instrument_type" in eq.columns:      # EQUITY rows last, so they win when a symbol appears more than once
        eq = eq.sort_values("instrument_type", key=lambda s: s.eq("EQUITY"), kind="stable")
    for sym, key in zip(eq[sym_col], eq["instrument_key"]):
        if isinstance(sym, str) and sym:
            master_map[sym.upper()] = key
    ix = df[seg == "NSE_INDEX"]
    for col in ("name", sym_col):
        if col in ix.columns:
            for val, key in zip(ix[col], ix["instrument_key"]):
                if isinstance(val, str) and val:
                    master_map[val.upper()] = key
    return master_map


def _instrument_map_from_json():
    """Fallback: the original (slow) JSON instrument file."""
    url = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
    master_map = {}
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
    return master_map


def load_upstox_instrument_map():
    """{UPPER SYMBOL / INDEX NAME: instrument_key} for NSE equities and indices (cached, never caches an empty map)."""
    cached = _cache.get("instrument_map")
    if cached is not None:
        return cached

    master_map = {}
    for loader in (_instrument_map_from_csv, _instrument_map_from_json):
        try:
            master_map = loader()
        except Exception as e:
            print(f"[instrument-map] {loader.__name__} failed: {e}")
            master_map = {}
        if master_map:
            break
    if master_map:
        _cache.set("instrument_map", master_map, INSTRUMENT_MAP_TTL_SECS)
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
# UPSTOX DAILY HISTORY (index + stock closes; replaces BigQuery)
# ==============================================================================
# Daily candles come from the Upstox historical-candle API, one request per instrument. Requests run in parallel
# (HIST_WORKERS threads) behind a shared rate limiter and retry on HTTP 429 / 5xx.
#   * indices: fetched at start-up / "Refresh Data" for every index in the mapping file
#   * stocks:  fetched on demand - the first time an index is selected, only its constituents - then cached
class _RateLimiter:
    """Spaces request starts at least 1/max_rps seconds apart; shared by all worker threads."""

    def __init__(self, max_rps):
        self._interval = 1.0 / max(max_rps, 0.1)
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self._interval
        if start > now:
            time.sleep(start - now)


_limiter = _RateLimiter(HIST_MAX_RPS)
_http = requests.Session()
_http.mount("https://", requests.adapters.HTTPAdapter(pool_connections=HIST_WORKERS * 2,
                                                      pool_maxsize=HIST_WORKERS * 2))


def _blank_closes():
    return pd.DataFrame({"TIMESTAMP": pd.Series(dtype="datetime64[ns]"), "Close": pd.Series(dtype=float)})


def fetch_daily_closes(instrument_key, days=None):
    """Daily closing prices of one instrument from Upstox -> DataFrame[TIMESTAMP, Close], oldest first.
    Returns an empty frame on any failure (no token, HTTP error, no candles)."""
    token = get_upstox_token()
    if not token:
        return _blank_closes()
    today = date.today()
    url = UPSTOX_HIST_URL.format(key=quote(instrument_key, safe=""), to_date=today.isoformat(),
                                 from_date=(today - timedelta(days=days or HISTORY_DAYS)).isoformat())
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    for attempt in range(4):
        _limiter.wait()
        try:
            response = _http.get(url, headers=headers, timeout=20)
        except requests.RequestException as e:
            print(f"[upstox-history] {instrument_key}: {e}")
            time.sleep(0.5 * (attempt + 1))
            continue
        if response.status_code == 429 or response.status_code >= 500:     # rate limited / server hiccup: back off
            time.sleep(1.0 * (attempt + 1))
            continue
        if response.status_code != 200:
            print(f"[upstox-history] {instrument_key}: HTTP {response.status_code} {response.text[:150]}")
            return _blank_closes()
        candles = response.json().get("data", {}).get("candles", [])
        if not candles:
            return _blank_closes()
        df = pd.DataFrame([[c[0], c[4]] for c in candles], columns=["TIMESTAMP", "Close"])    # [ts, o, h, l, c, v, oi]
        df["TIMESTAMP"] = (pd.to_datetime(df["TIMESTAMP"], utc=True).dt.tz_convert("Asia/Kolkata")
                           .dt.tz_localize(None).dt.normalize())
        df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
        return (df.dropna().drop_duplicates("TIMESTAMP", keep="last")
                .sort_values("TIMESTAMP").reset_index(drop=True))
    print(f"[upstox-history] {instrument_key}: gave up after retries")
    return _blank_closes()


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


def _empty_returns(group_col):
    return pd.DataFrame(columns=[group_col, "Latest Price"] + [f"{d}D %" for d in INTERVALS])


def get_indices_data():
    """Daily closes of every index listed in the mapping file, from Upstox (parallel, rate-limited)
    -> long DataFrame [Index_Name, TIMESTAMP, Closing_Index_Value]."""
    cols = ["Index_Name", "TIMESTAMP", "Closing_Index_Value"]
    imap = load_upstox_instrument_map()
    if not imap:
        print("[upstox-history] Upstox instrument list unavailable (network?) - no index data")
        return pd.DataFrame(columns=cols)
    jobs, unresolved = [], []
    for name in dict.fromkeys(mapping_df["Index Name"]):
        key = imap.get(INDEX_NAME_ALIASES.get(name, name))
        if key:
            jobs.append((name, key))
        else:
            unresolved.append(name)
    if unresolved:
        print(f"[upstox-history] {len(unresolved)} index name(s) not found in the Upstox instrument list "
              f"(add them to INDEX_NAME_ALIASES): {', '.join(unresolved)}")
    if not jobs:
        return pd.DataFrame(columns=cols)
    if not get_upstox_token():
        print("[upstox-history] no Upstox access token (set UPSTOX_ACCESS_TOKEN or generate today's token) - no index data")
        return pd.DataFrame(columns=cols)

    def work(job):
        name, key = job
        return fetch_daily_closes(key).assign(Index_Name=name).rename(columns={"Close": "Closing_Index_Value"})

    with ThreadPoolExecutor(max_workers=HIST_WORKERS) as pool:
        frames = [f for f in pool.map(work, jobs) if not f.empty]
    if not frames:
        return pd.DataFrame(columns=cols)
    return pd.concat(frames, ignore_index=True)[cols]


# Stock history: fetched lazily per index, cached per symbol.
_equity_hist = {}            # SYMBOL -> (expires_at, DataFrame[TIMESTAMP, EQ_CLOSE_PRICE])
_equity_lock = threading.Lock()      # guards _equity_hist
_ensure_lock = threading.Lock()      # one fetch round at a time (live ticks must not start duplicate fetches)
_const_returns_cache = {}    # index name -> (expires_at, have_key, returns DataFrame); cleared on "Refresh Data"


def ensure_equity_history(symbols):
    """Fetch (in parallel, rate-limited) the Upstox daily closes of every symbol that is not cached yet / has expired.
    Failures and unknown symbols are cached for 60 s only, so a transient error is retried soon but not every tick."""
    with _ensure_lock:
        now = time.time()
        with _equity_lock:
            todo = [s for s in dict.fromkeys(symbols) if s not in _equity_hist or now > _equity_hist[s][0]]
        if not todo:
            return
        imap = load_upstox_instrument_map()

        def work(sym):
            key = imap.get(str(sym).upper())
            if not key:
                return sym, _blank_closes().rename(columns={"Close": "EQ_CLOSE_PRICE"})
            return sym, fetch_daily_closes(key).rename(columns={"Close": "EQ_CLOSE_PRICE"})

        with ThreadPoolExecutor(max_workers=HIST_WORKERS) as pool:
            results = list(pool.map(work, todo))
        with _equity_lock:
            for sym, d in results:
                _equity_hist[sym] = (time.time() + (DATA_TTL_SECS if not d.empty else 60), d)


def _have_key(symbols):
    """Which of `symbols` currently have cached history (derived caches are rebuilt when this changes)."""
    with _equity_lock:
        return tuple(sorted(s for s in symbols if _equity_hist.get(s) is not None and not _equity_hist[s][1].empty))


def equity_history_frame(symbols):
    """Long DataFrame [SYMBOL, TIMESTAMP, EQ_CLOSE_PRICE] of the cached history of `symbols`."""
    with _equity_lock:
        entries = [(s, _equity_hist.get(s)) for s in symbols]
    frames = [e[1].assign(SYMBOL=s) for s, e in entries if e is not None and not e[1].empty]
    if not frames:
        return pd.DataFrame(columns=["SYMBOL", "TIMESTAMP", "EQ_CLOSE_PRICE"])
    return pd.concat(frames, ignore_index=True)


def get_constituent_returns(idx_name, symbols):
    """Returns table (SYMBOL, Latest Price, 7D % ... 365D %) for an index's constituents, built from their Upstox history
    (fetched on first use, then cached)."""
    ensure_equity_history(symbols)
    have = _have_key(symbols)
    hit = _const_returns_cache.get(idx_name)
    if hit and time.time() < hit[0] and hit[1] == have:
        return hit[2]
    hist = equity_history_frame(symbols)
    ret = calculate_returns(hist, "SYMBOL", "EQ_CLOSE_PRICE") if not hist.empty else _empty_returns("SYMBOL")
    _const_returns_cache[idx_name] = (time.time() + DATA_TTL_SECS, have, ret)
    return ret


def load_all_data(force=False):
    """Index daily closes from Upstox (cached, TTL-bound) plus the returns table built from them.
    Stock history is NOT loaded here - it is fetched per index on demand (see get_constituent_returns)."""
    if force:
        _cache.invalidate("indices_hist")

    idx_hist = _cache.get("indices_hist")
    if idx_hist is None:
        started = time.time()
        idx_hist = get_indices_data()
        if not idx_hist.empty:      # never cache a failed / empty pull
            _cache.set("indices_hist", idx_hist, DATA_TTL_SECS)
            print(f"[upstox-history] {idx_hist['Index_Name'].nunique()} indices loaded in {time.time() - started:.1f}s")

    indices_returns = (calculate_returns(idx_hist, "Index_Name", "Closing_Index_Value")
                       if not idx_hist.empty else _empty_returns("Index_Name"))
    indices_returns["Current LTP"] = indices_returns["Latest Price"]
    indices_returns["Today's Change %"] = 0.0
    indices_returns = indices_returns.drop(columns=["Latest Price"])  # superseded by "Current LTP"

    idx_cols = list(indices_returns.columns)
    ordered_idx_cols = ["Index_Name", "Current LTP", "Today's Change %"] + [
        c for c in idx_cols if c not in ("Index_Name", "Current LTP", "Today's Change %")
    ]
    indices_returns = indices_returns[ordered_idx_cols]

    return idx_hist, indices_returns


def data_status(idx_hist):
    """One-line status under the 'Indices Performance' title."""
    if idx_hist is None or idx_hist.empty:
        return html.Span("⚠ No index data from Upstox - check the access token, then click Refresh Data.",
                         style={"color": "var(--down)"})
    return (f"Upstox daily closes · {idx_hist['Index_Name'].nunique()} indices · "
            f"as of {idx_hist['TIMESTAMP'].max():%d %b %Y}")


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
_corr_cache = {}        # {index_name: (have_key, Series(symbol -> corr))}; cleared on "Refresh Data"


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
    levels trend and would give spuriously high correlations.) Cached per index;
    rebuilt if more of the stocks' history has arrived since."""
    ensure_equity_history(symbols)
    have = _have_key(symbols)
    hit = _corr_cache.get(idx_name)
    if hit and hit[0] == have:
        return hit[1]

    idx = idx_hist_df[idx_hist_df["Index_Name"] == idx_name]
    eq = equity_history_frame(symbols)
    if idx.empty or eq.empty:
        return pd.Series(dtype=float)

    idx_px = idx.assign(d=idx["TIMESTAMP"].dt.normalize()).groupby("d")["Closing_Index_Value"].last()
    cutoff = idx_px.index.max() - pd.Timedelta(days=CORR_WINDOW_DAYS)
    idx_ret = idx_px[idx_px.index >= cutoff].pct_change(fill_method=None)

    eq = eq.assign(d=eq["TIMESTAMP"].dt.normalize())
    eq = eq[eq["d"] >= cutoff]
    wide = eq.pivot_table(index="d", columns="SYMBOL", values="EQ_CLOSE_PRICE", aggfunc="last")
    rets = wide.pct_change(fill_method=None)

    corr = rets.apply(lambda s: idx_ret.corr(s, min_periods=CORR_MIN_OBS)).round(2)
    _corr_cache[idx_name] = (have, corr)
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
idx_hist_df, indices_returns = load_all_data()

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
                        html.Div(id="data-status", children=data_status(idx_hist_df), className="mb-2",
                                 style={"color": "var(--text-muted)", "fontSize": "0.7rem"}),
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
    Output("data-status", "children"),
    Input("btn-refresh-data", "n_clicks"),
    prevent_initial_call=True,
)
def refresh_historical_data(n_clicks):
    """Re-pulls the index history from Upstox (and forgets the cached stock history) without restarting the process."""
    global idx_hist_df, indices_returns
    idx_hist_df, indices_returns = load_all_data(force=True)
    _corr_cache.clear()  # correlations depend on the history we just reloaded
    _const_returns_cache.clear()
    with _equity_lock:
        _equity_hist.clear()
    columns = [{"name": i, "id": i} for i in indices_returns.columns]
    return indices_returns.to_dict("records"), columns, data_status(idx_hist_df)


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
        stock = equity_history_frame([stock_symbol]).sort_values("TIMESTAMP").dropna(subset=["EQ_CLOSE_PRICE"])
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

    df_const = get_constituent_returns(idx_name, constituents).copy()
    if df_const.empty:
        return empty(f"No Upstox price history for the constituents of {idx_name}. "
                     f"Check the access token, then click Refresh Data.")
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