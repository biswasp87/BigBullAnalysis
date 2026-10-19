"""360° Index Analysis dashboard (Dash + Upstox + Zerodha Kite + GCS).

Same layout and features as Analysis_Stock_Dashboard.py, adapted for NSE / BSE index options.

Differences from the stock dashboard (index data has no bhav-copy fundamentals):
    * price candles for EVERY timeframe (1/5/10/15/30 Min, 1 Hour, Day, Week, Month) come straight from Upstox
      (Day = history + today's live quote, merged without duplicates). No BigQuery / local CSV source.
    * no Q/T, delivery, COI, shareholding or company profile; the "Share Holding" cell is an "Index Snapshot"
    * the buy/sell "equity" triangle + table row use the nearest index FUTURE (an index has no order book)
    * symbols = index option underlyings (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX, BANKEX ...)

File layout
    1. Imports & logging       2. Configuration           3. Tokens / shortlist helpers (GCS)
    4. Broker API helpers      5. Start-up data           6. Chart helpers
    7. Dash app, pop-outs, layout                         8. Callbacks

Run:  python Analysis_Index_Dashboard.py        (PORT environment variable overrides 8080)
Prod: gunicorn Analysis_Index_Dashboard:server
"""

# =====================================================================================
# 1. IMPORTS & LOGGING
# =====================================================================================
import datetime
import functools
import gzip
import importlib.util
import io
import json
import logging
import os
import threading
import time
from datetime import timedelta

import dash
import dash_bootstrap_components as dbc
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.offline
import requests
from dash import Input, Output, State, dash_table, dcc, html
from flask import Response
from google.cloud import storage
from plotly.subplots import make_subplots

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("index360")


# =====================================================================================
# 2. CONFIGURATION
# =====================================================================================

# --- Google Cloud Storage -----------------------------------------------------------
TOKEN_BUCKET = "bba_access_token"                      # <bucket>/upstox/<date>.json and <bucket>/zerodha/<date>.json
SHORTLIST_BUCKET_NAME = "bba_support_files"
SHORTLIST_FILE_PATH = "shortlisted_index_symbols.csv"

# --- Upstox -----------------------------------------------------------------------------
UPSTOX_INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
HTTP_TIMEOUT = 20                                       # seconds, every outgoing API request

# --- Zerodha Kite Connect (alerts) --------------------------------------------------
KITE_API_KEY = os.environ.get("KITE_API_KEY", "njg16v6xxfb7iorf")      # set KITE_API_KEY in the environment for deployment
KITE_ALERTS_URL = "https://api.kite.trade/alerts"

# --- Token generator scripts (placed next to this file) -----------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
UPSTOX_TOKEN_SCRIPT = os.path.join(_HERE, "Upstox_Generate_Access_Token.py")
ZERODHA_TOKEN_SCRIPT = os.path.join(_HERE, "Zerodha_Generate_Access_Token.py")
_BROKER_LABEL = {"upstox": "Upstox", "zerodha": "Zerodha"}
_BROKER_NEEDED_FOR = {"upstox": "market data, option chain, streaming", "zerodha": "Create Alert / Active Alerts"}

_IST = "Asia/Kolkata"

# Option-underlying name (Upstox OPTIDX 'name') -> cash index instrument key
INDEX_KEY_MAP = {
    'NIFTY': 'NSE_INDEX|Nifty 50',
    'BANKNIFTY': 'NSE_INDEX|Nifty Bank',
    'FINNIFTY': 'NSE_INDEX|Nifty Fin Service',
    'MIDCPNIFTY': 'NSE_INDEX|NIFTY MID SELECT',
    'SENSEX': 'BSE_INDEX|SENSEX',
    'BANKEX': 'BSE_INDEX|BANKEX',
}
# Option-underlying name -> (Kite exchange, Kite trading symbol) used to pre-fill the alert dialog
INDEX_ALERT_MAP = {
    'NIFTY': ('INDICES', 'NIFTY 50'),
    'BANKNIFTY': ('INDICES', 'NIFTY BANK'),
    'FINNIFTY': ('INDICES', 'NIFTY FIN SERVICE'),
    'MIDCPNIFTY': ('INDICES', 'NIFTY MID SELECT'),
    'SENSEX': ('INDICES', 'SENSEX'),
    'BANKEX': ('INDICES', 'BANKEX'),
}


# =====================================================================================
# 3. ACCESS TOKENS AND SHORTLIST HELPERS
# =====================================================================================


def get_broker_token_from_gcs(broker, bucket_name=TOKEN_BUCKET, filename=None):
    """Access token of `broker` ('upstox' | 'zerodha') from <bucket>/<broker>/<filename>.
    Defaults to today's file (YYYY-MM-DD.json). Returns None when it is missing or unreadable."""
    filename = filename or f"{datetime.datetime.now().date()}.json"
    try:
        blob = storage.Client().bucket(bucket_name).blob(f"{broker}/{filename}")
        return json.loads(blob.download_as_text()).get("access_token")
    except Exception as e:
        logger.warning("Could not read %s token from GCS: %s", broker, e)
        return None


DEFAULT_TOKEN = get_broker_token_from_gcs("upstox")
KITE_ACCESS_TOKEN = get_broker_token_from_gcs("zerodha")
KITE_HEADERS = {
    'X-Kite-Version': '3',
    'Authorization': f'token {KITE_API_KEY}:{KITE_ACCESS_TOKEN}'
}


def load_shortlist_from_gcs():
    try:
        blob = storage.Client().bucket(SHORTLIST_BUCKET_NAME).blob(SHORTLIST_FILE_PATH)
        if blob.exists():
            df = pd.read_csv(io.StringIO(blob.download_as_text()))
            return df.to_dict('records')
    except Exception as e:
        logger.warning(f"Error loading shortlist from GCS: {e}")
    return []


def save_shortlist_to_gcs(records):
    try:
        df = pd.DataFrame(records) if records else pd.DataFrame(columns=['Symbol', 'Notes'])
        blob = storage.Client().bucket(SHORTLIST_BUCKET_NAME).blob(SHORTLIST_FILE_PATH)
        blob.upload_from_string(df.to_csv(index=False), 'text/csv')
        return True
    except Exception as e:
        logger.warning(f"Error saving shortlist to GCS: {e}")
        return False


# =====================================================================================
# 4. BROKER API HELPERS (Upstox / Zerodha)
# =====================================================================================


def fetch_zerodha_filtered_symbols():
    """All NSE / NFO-OPT trading symbols from Zerodha (used by the ATO basket symbol picker)."""
    try:
        response = requests.get("https://api.kite.trade/instruments", timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        df = pd.read_csv(io.StringIO(response.text))
        df = df[df['exchange'].isin(['NFO', 'NSE']) & df['segment'].isin(['NFO-OPT', 'NSE', 'INDICES'])]
        return sorted(df['tradingsymbol'].dropna().unique().tolist())
    except Exception as e:
        logger.warning(f"Error fetching Zerodha symbols: {e}")
        return []


def get_access_token():
    """Upstox access token (refreshed in place by the Access Tokens drawer)."""
    return DEFAULT_TOKEN


_ALL_INSTRUMENTS = None          # cache of the Upstox instrument master


def get_all_instruments():
    """Download and cache all instruments from Upstox."""
    global _ALL_INSTRUMENTS
    if _ALL_INSTRUMENTS is None:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                                 'Chrome/91.0.4472.124 Safari/537.36'}
        try:
            logger.info("Downloading all instruments...")
            response = requests.get(UPSTOX_INSTRUMENTS_URL, headers=headers, timeout=120)
            response.raise_for_status()
            with gzip.open(io.BytesIO(response.content), 'rt') as f:
                _ALL_INSTRUMENTS = pd.read_csv(f)
            logger.info("Instruments downloaded successfully.")
        except Exception as e:
            logger.warning(f"Error fetching Upstox instruments: {e}")
            return pd.DataFrame()
    return _ALL_INSTRUMENTS


def fetch_and_prepare_data():
    """Index derivatives (options + futures) from the Upstox instrument master."""
    upstox_df = get_all_instruments()
    if upstox_df.empty:
        return pd.DataFrame()
    return upstox_df[upstox_df['instrument_type'].isin(['OPTIDX', 'FUTIDX'])].copy()


def get_index_key(symbol):
    """Cash-index instrument key of an option underlying (NIFTY -> 'NSE_INDEX|Nifty 50')."""
    if symbol in INDEX_KEY_MAP:
        return INDEX_KEY_MAP[symbol]
    try:
        if 'underlying_key' in INSTRUMENTS_DF.columns:
            m = INSTRUMENTS_DF[(INSTRUMENTS_DF['name'] == symbol) & INSTRUMENTS_DF['underlying_key'].notna()]
            if not m.empty:
                return str(m.iloc[0]['underlying_key'])
        allx = get_all_instruments()
        m = allx[(allx['instrument_type'] == 'INDEX') & (allx['name'].str.upper() == str(symbol).upper())]
        if not m.empty:
            return m.iloc[0]['instrument_key']
    except Exception as e:
        logger.warning(f"Index key lookup failed for {symbol}: {e}")
    return None


def get_index_future_key(symbol):
    """Nearest (non-expired) index future of `symbol` - gives the index a buy/sell order-book for the streaming table."""
    try:
        f = INSTRUMENTS_DF[(INSTRUMENTS_DF['instrument_type'] == 'FUTIDX') & (INSTRUMENTS_DF['name'] == symbol)]
        if f.empty:
            return None
        f = f.assign(_e=f['expiry'].astype(str)).sort_values('_e')
        live = f[f['_e'] >= str(datetime.date.today())]
        return (live if not live.empty else f).iloc[0]['instrument_key']
    except Exception as e:
        logger.warning(f"Index future lookup failed for {symbol}: {e}")
        return None


def fetch_historical_v3(instrument_key, interval='days', interval_value=1, from_date=None, to_date=None):
    """Fetch Historical Candle Data V3."""
    if to_date is None:
        to_date = datetime.datetime.now().strftime('%Y-%m-%d')
    if from_date is None:
        from_date = (datetime.datetime.now() - timedelta(days=100)).strftime('%Y-%m-%d')
    url = f"https://api.upstox.com/v3/historical-candle/{instrument_key}/{interval}/{interval_value}/{to_date}/{from_date}"
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {get_access_token()}'}
    try:
        response = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
        if response.status_code == 200:
            data = response.json().get('data', {}).get('candles', [])
            cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi']
            df = pd.DataFrame(data, columns=cols)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            return df.sort_values('timestamp', ascending=True)
        logger.warning(f"Error fetching historical V3: {response.status_code} {response.text}")
    except Exception as e:
        logger.warning(f"Exception fetching historical V3: {e}")
    return pd.DataFrame()


def fetch_option_chain(underlying_key, expiry):
    """Fetch Option Chain from Upstox API."""
    url = f"https://api.upstox.com/v2/option/chain?instrument_key={underlying_key}&expiry_date={expiry}"
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {get_access_token()}'}
    try:
        response = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
        if response.status_code == 200:
            return response.json().get('data', [])
        logger.warning(f"Option chain error ({response.status_code}): {response.text[:200]}")
    except Exception as e:
        logger.warning(f"Exception fetching option chain: {e}")
    return []


def fetch_market_quotes(instrument_keys):
    """Fetch Full Market Quote for multiple instruments, chunked by 50 (API limit)."""
    if not instrument_keys:
        return {}
    all_data = {}
    for i in range(0, len(instrument_keys), 50):
        keys_str = ",".join(instrument_keys[i:i + 50])
        url = f"https://api.upstox.com/v2/market-quote/quotes?instrument_key={keys_str}"
        headers = {'Accept': 'application/json', 'Authorization': f'Bearer {get_access_token()}'}
        try:
            response = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
            if response.status_code == 200:
                all_data.update(response.json().get('data', {}))
            else:
                logger.warning(f"Error fetching market quotes chunk: {response.status_code} {response.text}")
        except Exception as e:
            logger.warning(f"Exception fetching market quotes chunk: {e}")
    return all_data


# _____________________________________________________________________________________
# LIVE DAILY-CANDLE HELPERS  (Upstox quote -> one daily row, merged WITHOUT duplicates)
# _____________________________________________________________________________________
_LIVE_CACHE = {}          # instrument_key -> (fetched_at_epoch, row-or-None)
_LIVE_CACHE_TTL = 8       # seconds


def _find_quote(quotes_data, instrument_key):
    q = quotes_data.get(instrument_key)
    if q:
        return q
    tail = instrument_key.split('|')[-1]
    for k, v in quotes_data.items():
        if v.get('instrument_token') == instrument_key or k.endswith(tail):
            return v
    return None


def _quote_trade_date(q):
    """Trading date (tz-naive, midnight) of a full-market-quote from its own time stamp, or None."""
    for field in ("last_trade_time", "timestamp"):
        raw = q.get(field)
        if raw in (None, "", 0, "0"):
            continue
        try:
            if isinstance(raw, (int, float)) or str(raw).isdigit():
                ts = pd.Timestamp(int(raw), unit="ms", tz="UTC").tz_convert(_IST)
            else:
                ts = pd.Timestamp(raw)
                ts = ts.tz_convert(_IST) if ts.tzinfo else ts.tz_localize(_IST)
            return ts.tz_localize(None).normalize()
        except Exception:
            continue
    return None


def get_live_daily_row(instrument_key):
    """Today's (or the latest session's) daily candle from the market quote, or None. Short TTL cache."""
    now = time.time()
    hit = _LIVE_CACHE.get(instrument_key)
    if hit and now - hit[0] < _LIVE_CACHE_TTL:
        return hit[1]
    row = None
    try:
        q = _find_quote(fetch_market_quotes([instrument_key]), instrument_key)
        if q:
            ohlc = q.get('ohlc') or {}
            close = q.get('last_price')
            trade_date = _quote_trade_date(q)
            if trade_date is None:
                n = pd.Timestamp.now(tz=_IST)
                if n.weekday() < 5 and (n.hour, n.minute) >= (9, 15):
                    trade_date = n.tz_localize(None).normalize()
            vals = [ohlc.get('open'), ohlc.get('high'), ohlc.get('low'), close]
            if trade_date is not None and all(v not in (None, 0) for v in vals):
                row = {'timestamp': trade_date, 'open': ohlc.get('open'), 'high': ohlc.get('high'),
                       'low': ohlc.get('low'), 'close': close, 'volume': q.get('volume'), 'oi': q.get('oi')}
    except Exception as e:
        logger.warning(f"Live row fetch failed for {instrument_key}: {e}")
    _LIVE_CACHE[instrument_key] = (now, row)
    return row


def merge_live_row(df, live_row):
    """Merge the live quote row into daily candles - exactly ONE row per calendar day (same day: live replaces it;
    newer day: appended; older day: ignored). Timestamps are normalised to tz-naive midnight first."""
    if df is None:
        df = pd.DataFrame()
    if not df.empty:
        df = df.copy()
        ts = pd.to_datetime(df['timestamp'], utc=False)
        if getattr(ts.dt, 'tz', None) is not None:
            ts = ts.dt.tz_localize(None)
        df['timestamp'] = ts.dt.normalize()
        df = df.drop_duplicates('timestamp', keep='last').sort_values('timestamp').reset_index(drop=True)
        for c in df.columns.drop('timestamp'):
            df[c] = pd.to_numeric(df[c], errors='coerce').astype(float)
    if live_row:
        day = pd.Timestamp(live_row['timestamp']).normalize()
        if df.empty:
            df = pd.DataFrame([{**live_row, 'timestamp': day}])
        elif day == df['timestamp'].iloc[-1]:
            for k, v in live_row.items():
                if k != 'timestamp' and v is not None:
                    df.loc[df.index[-1], k] = v
        elif day > df['timestamp'].iloc[-1]:
            df = pd.concat([df, pd.DataFrame([{**live_row, 'timestamp': day}])], ignore_index=True)
    return df


def fetch_daily_with_live(instrument_key):
    """Historical daily candles + the live quote, de-duplicated (used by the CE / PE option graphs)."""
    return merge_live_row(fetch_historical_v3(instrument_key), get_live_daily_row(instrument_key))


# _____________________________________________________________________________________
# TIMEFRAMES  (1 / 5 / 10 / 15 / 30 Min, 1 Hour, Day, Week, Month - all straight from Upstox)
# _____________________________________________________________________________________
TF_SPEC = {"1m": ("minutes", 1), "5m": ("minutes", 5), "10m": ("minutes", 10), "15m": ("minutes", 15),
           "30m": ("minutes", 30), "1h": ("hours", 1), "D": ("days", 1), "W": ("weeks", 1), "M": ("months", 1)}
TF_LABEL = {"1m": "1 Min", "5m": "5 Min", "10m": "10 Min", "15m": "15 Min", "30m": "30 Min", "1h": "1 Hour",
            "D": "Day", "W": "Week", "M": "Month"}
_TF_STEP_MIN = {"1m": 1, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60}
_CANDLE_CACHE = {}


def _candles_from_json(candles):
    """Upstox candle rows -> DataFrame [timestamp(naive IST), open, high, low, close, volume, oi]."""
    if not candles:
        return pd.DataFrame()
    df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_convert(_IST).dt.tz_localize(None)
    return df


def _get_json(url):
    try:
        r = requests.get(url, headers={'Accept': 'application/json', 'Authorization': f'Bearer {get_access_token()}'},
                         timeout=HTTP_TIMEOUT)
        if r.status_code == 200:
            return r.json().get('data', {}).get('candles', [])
        logger.warning(f"Candle request failed {r.status_code}: {r.text[:200]}  [{url}]")
    except Exception as e:
        logger.warning(f"Candle request error: {e}")
    return []


def fetch_candles(instrument_key, tf, days):
    """Candles for any timeframe: history (chunked to stay inside Upstox's per-request range limits) + today's running
    candles from the intraday endpoint, de-duplicated on timestamp (the newer copy wins)."""
    ck = (instrument_key, tf, days)
    hit = _CANDLE_CACHE.get(ck)
    ttl = 8 if tf in _TF_STEP_MIN else 60
    if hit and time.time() - hit[0] < ttl:
        return hit[1].copy()
    unit, interval = TF_SPEC[tf]
    today = pd.Timestamp.now(tz=_IST).tz_localize(None).normalize()
    start = today - pd.Timedelta(days=days)
    # assumed Upstox limits: minutes<=15 -> 1 month, other minutes / hours -> 1 quarter, days / weeks / months -> 10 years
    chunk = 28 if (unit == "minutes" and interval <= 15) else (85 if unit in ("minutes", "hours") else 3000)
    frames, end = [], today
    while end >= start:
        s_ = max(start, end - pd.Timedelta(days=chunk - 1))
        url = (f"https://api.upstox.com/v3/historical-candle/{instrument_key}/{unit}/{interval}/"
               f"{end:%Y-%m-%d}/{s_:%Y-%m-%d}")
        frames.append(_candles_from_json(_get_json(url)))
        end = s_ - pd.Timedelta(days=1)
    if tf in _TF_STEP_MIN:                                   # today's (running) candles
        frames.append(_candles_from_json(
            _get_json(f"https://api.upstox.com/v3/historical-candle/intraday/{instrument_key}/{unit}/{interval}")))
    frames = [f for f in frames if not f.empty]
    df = (pd.concat(frames, ignore_index=True).drop_duplicates('timestamp', keep='last')
          .sort_values('timestamp').reset_index(drop=True)) if frames else pd.DataFrame()
    _CANDLE_CACHE[ck] = (time.time(), df.copy())
    if len(_CANDLE_CACHE) > 40:
        _CANDLE_CACHE.pop(next(iter(_CANDLE_CACHE)))
    return df


def _future_slot(last_ts, step_min, n):
    """Timestamp of the n-th trading slot after `last_ts` (NSE 09:15-15:30, Mon-Fri) - leaves room for the OI profile
    on an axis whose nights / weekends are collapsed (rangebreaks)."""
    t, open_, close_ = pd.Timestamp(last_ts), (9, 15), (15, 30)
    for _ in range(n):
        t = t + pd.Timedelta(minutes=step_min)
        if (t.hour, t.minute) >= close_ or (t.hour, t.minute) < open_:
            t = t.normalize() + pd.Timedelta(days=0 if (t.hour, t.minute) < open_ else 1, hours=9, minutes=15)
        while t.weekday() >= 5:
            t = t.normalize() + pd.Timedelta(days=1, hours=9, minutes=15)
    return t


def _to_eq_columns(df):
    return df.rename(columns={'timestamp': 'TIMESTAMP', 'open': 'EQ_OPEN_PRICE', 'high': 'EQ_HIGH_PRICE',
                              'low': 'EQ_LOW_PRICE', 'close': 'EQ_CLOSE_PRICE', 'volume': 'EQ_TTL_TRD_QNTY'})


# =====================================================================================
# 5. START-UP DATA
# =====================================================================================

ZERODHA_FILTERED_SYMBOLS = fetch_zerodha_filtered_symbols()
ZERODHA_SYMBOL_OPTIONS = [{'label': s, 'value': s} for s in ZERODHA_FILTERED_SYMBOLS]

INSTRUMENTS_DF = fetch_and_prepare_data()
UNIQUE_SYMBOLS = (sorted(INSTRUMENTS_DF[INSTRUMENTS_DF['instrument_type'] == 'OPTIDX']['name'].dropna().unique().tolist())
                  if not INSTRUMENTS_DF.empty else [])


# =====================================================================================
# 6. CHART HELPERS
# =====================================================================================


def _compact_fig(fig, right=10, title=None, legend=True):
    """Make a Plotly figure readable inside a small dashboard panel."""
    if fig is None:
        return fig
    upd = dict(
        font=dict(size=9),
        margin=dict(l=40, r=right, t=22, b=26),
        title=dict(text=title if title is not None else (fig.layout.title.text if fig.layout.title else None),
                   font=dict(size=10), x=0.01, xanchor="left", y=0.98, yanchor="top"),
        legend=dict(orientation="h", x=1, xanchor="right", y=1.02, yanchor="bottom", font=dict(size=9),
                    bgcolor="rgba(0,0,0,0)"),
        showlegend=legend if legend is False else fig.layout.showlegend,
        autosize=True, height=None,
    )
    fig.update_layout(**upd)
    fig.update_xaxes(title_font=dict(size=9), tickfont=dict(size=8), automargin=True)
    fig.update_yaxes(title_font=dict(size=9), tickfont=dict(size=8), automargin=True)
    return fig


def _empty_fig(message):
    fig = go.Figure()
    fig.add_annotation(text=message, showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
                       font=dict(size=14, color="#888"))
    fig.update_layout(xaxis=dict(visible=False), yaxis=dict(visible=False), plot_bgcolor="white",
                      paper_bgcolor="white", margin=dict(l=10, r=10, t=10, b=10))
    return fig


# _____________________________________________________________________________________
# OPTION CHAIN ON THE PRICE CHART
#   right-edge OI profile (butterfly) on the candlestick's own price axis, with
#   * change-in-OI whiskers, * strike buy/sell markers, * index-future buy/sell ratio triangle
# _____________________________________________________________________________________
OI_PROFILE_ENABLED = True   # False -> plain chart, no live option chain on the price graph
OI_PROFILE_WIDTH = 0.17     # share of the chart width reserved (right edge) for the OI profile
OI_WALLS_TOP_N = 2          # how many biggest CE / PE OI strikes get a horizontal "wall" band
_OI_X_AXIS = 'x20'          # private overlay x-axis; it SHARES the candlestick y (price) axis
_EQ_Y_AXIS = 'y30'          # private overlay y-axis (fixed 0..1) that pins the buy/sell triangle to the top/bottom
_PAD = 1.30
_MARKER_X = 1.14
_EQ_TOP, _EQ_BOTTOM = 0.92, 0.08


def _num(v):
    try:
        f = float(v)
        return None if np.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _ratio_text(a, b):
    """Weaker side as a fraction of the stronger side, e.g. 0.5x, 0.66x."""
    hi, lo = max(a, b), min(a, b)
    return f"{round(lo / hi if hi else 0, 2):g}x"


def _equity_qty(stream_rows):
    """(buy, sell) total quantity of the index future from the streaming rows, or None."""
    for r in stream_rows or []:
        if str(r.get('Strike', '')).startswith('EQ:'):
            b, s_ = _num(r.get('Call Buy Qty')), _num(r.get('Call Sell Qty'))
            if b is not None and s_ is not None:
                return b, s_
    return None


def _chain_to_frame(chain_data, stream_rows):
    """Option-chain rows (+ streaming buy/sell qty) -> one DataFrame, one row per strike."""
    rows = []
    for item in chain_data:
        ce = item.get('call_options', {}) or {}
        pe = item.get('put_options', {}) or {}
        ce_md, pe_md = ce.get('market_data', {}) or {}, pe.get('market_data', {}) or {}
        ce_g, pe_g = ce.get('option_greeks', {}) or {}, pe.get('option_greeks', {}) or {}

        def chg(md):
            prev = md.get('prev_oi', 0) or 0
            return round((((md.get('oi', 0) or 0) - prev) / prev) * 100, 1) if prev else 0.0

        rows.append({'strike': item['strike_price'],
                     'ce_oi': ce_md.get('oi', 0) or 0, 'pe_oi': pe_md.get('oi', 0) or 0,
                     'ce_prev': ce_md.get('prev_oi', 0) or 0, 'pe_prev': pe_md.get('prev_oi', 0) or 0,
                     'ce_chg': chg(ce_md), 'pe_chg': chg(pe_md),
                     'ce_ltp': _num(ce_md.get('ltp')), 'pe_ltp': _num(pe_md.get('ltp')),
                     'ce_delta': _num(ce_g.get('delta')), 'pe_delta': _num(pe_g.get('delta')),
                     'ce_pop': _num(ce_g.get('pop')), 'pe_pop': _num(pe_g.get('pop'))})
    df = pd.DataFrame(rows).sort_values('strike').reset_index(drop=True)
    for c in ('ce_buy', 'ce_sell', 'pe_buy', 'pe_sell'):
        df[c] = np.nan
    if stream_rows:
        sdf = pd.DataFrame(stream_rows)
        if 'Strike' in sdf.columns:
            sdf['_k'] = pd.to_numeric(sdf['Strike'], errors='coerce')
            sdf = sdf[sdf['_k'].notna()].drop_duplicates('_k').set_index('_k')
            for dst, src in (('ce_buy', 'Call Buy Qty'), ('ce_sell', 'Call Sell Qty'),
                             ('pe_buy', 'Put Buy Qty'), ('pe_sell', 'Put Sell Qty')):
                if src in sdf.columns:
                    df[dst] = df['strike'].map(pd.to_numeric(sdf[src], errors='coerce'))
    return df


def _max_pain_strike(df):
    k, ce, pe = df['strike'].values, df['ce_oi'].values, df['pe_oi'].values
    pain = [(np.maximum(s - k, 0) * ce).sum() + (np.maximum(k - s, 0) * pe).sum() for s in k]
    return float(k[int(np.argmin(pain))])


def _oi_rgba(is_call, chg):
    """Colour = side (CE red / PE green). Opacity = OI build-up strength; unwinding is pale."""
    r, g, b = (214, 39, 40) if is_call else (44, 160, 44)
    alpha = 0.40 + 0.60 * min(max(chg, 0) / 25.0, 1.0) if chg > 0 else 0.15
    return f"rgba({r},{g},{b},{alpha:.2f})", f"rgb({r},{g},{b})"


def _add_option_chain_overlay(fig, df_stock, chain_data, underlying_info, stream_rows, x_end=None):
    """Draw today's option chain ON the index chart (row 1), sharing its price axis.

    * butterfly OI profile at the right edge: CE OI to the left (red), PE OI to the right (green)
        - bar length = OI, bar opacity = OI change %
        - whisker = PREVIOUS OI: dark segment INSIDE the bar = OI added; black segment OUTSIDE the bar = OI lost
    * strike buy/sell markers (outer columns): CE = up-triangle, PE = down-triangle, only when Buy qty > Sell qty
    * index-future buy/sell triangle: green up at the TOP if buyers dominate, red down at the BOTTOM if sellers
      dominate; the number inside = weaker side / stronger side
    * horizontal wall bands for the biggest CE / PE OI strikes, max-pain line, spot line
    """
    if not OI_PROFILE_ENABLED or not chain_data:
        return
    try:
        full = _chain_to_frame(chain_data, stream_rows)
        if len(full) < 2:
            return
        step = float(np.median(np.diff(full['strike'].values))) or 1.0
        ltp = float(df_stock['EQ_CLOSE_PRICE'].iloc[-1])
        lo, hi = float(df_stock['EQ_LOW_PRICE'].min()), float(df_stock['EQ_HIGH_PRICE'].max())
        pad = 0.05 * ltp
        # only strikes inside the price range being shown (keeps the shared price axis from being stretched;
        # also hides a stale chain left over from the previously selected index)
        df = full[(full['strike'] >= lo - pad) & (full['strike'] <= hi + pad)].copy()
        if df.empty:
            return
        m = float(df[['ce_oi', 'pe_oi', 'ce_prev', 'pe_prev']].to_numpy(dtype=float).max())
        if m <= 0:
            return

        x0, x1 = df_stock['TIMESTAMP'].min(), df_stock['TIMESTAMP'].max()
        extra = (x1 - x0) * (OI_PROFILE_WIDTH / (1 - OI_PROFILE_WIDTH)) * 1.08
        fig.update_xaxes(range=[x0 - (x1 - x0) * 0.01, x_end if x_end is not None else x1 + extra])

        centre = 1 - OI_PROFILE_WIDTH / 2
        ax_hi = m * _PAD
        ax_lo = -ax_hi * (centre / (1 - centre))
        fig.update_layout(**{
            'xaxis' + _OI_X_AXIS[1:]: dict(overlaying='x', anchor='y', visible=False,
                                           range=[ax_lo, ax_hi], fixedrange=True),
            'yaxis' + _EQ_Y_AXIS[1:]: dict(overlaying='y', anchor='x', visible=False, range=[0, 1],
                                           fixedrange=True, domain=list(fig.layout.yaxis.domain)),
            'margin': dict(t=34)})

        strikes = df['strike'].to_numpy(dtype=float)

        def side(prefix, is_call):
            sign = -1 if is_call else 1
            name = 'CE' if is_call else 'PE'
            oi = df[prefix + '_oi'].to_numpy(dtype=float)
            prev = df[prefix + '_prev'].to_numpy(dtype=float)
            fills, lines = zip(*[_oi_rgba(is_call, c) for c in df[prefix + '_chg']])
            cd = np.column_stack([oi, df[prefix + '_chg'], df[prefix + '_ltp'], df[prefix + '_delta'],
                                  df[prefix + '_pop'], df[prefix + '_buy'], df[prefix + '_sell'], prev]).astype(float)
            ht = ("<b>Strike %{y:g}</b> (" + name + ")<br>"
                  "OI: %{customdata[0]:,.0f} (prev %{customdata[7]:,.0f}, %{customdata[1]:+.1f}%)<br>"
                  "LTP: %{customdata[2]:.2f} | Delta: %{customdata[3]:.2f} | POP: %{customdata[4]:.1f}<br>"
                  "Buy / Sell qty: %{customdata[5]:,.0f} / %{customdata[6]:,.0f}<extra></extra>")
            fig.add_trace(go.Bar(x=sign * oi, y=strikes, orientation='h', width=step * 0.82,
                                 marker=dict(color=list(fills), line=dict(color=list(lines), width=0.6)),
                                 customdata=cd, hovertemplate=ht, showlegend=False, name=name + ' OI profile',
                                 xaxis=_OI_X_AXIS, yaxis='y', offsetgroup='oi_profile'))

            dark = '#8b0000' if is_call else '#006400'
            for amount, colour, inward in ((np.clip(oi - prev, 0, None), dark, True),
                                           (np.clip(prev - oi, 0, None), '#111111', False)):
                mask = amount > 0
                if not mask.any():
                    continue
                zero = np.zeros(int(mask.sum()))
                toward_plus = (inward == is_call)
                fig.add_trace(go.Scatter(
                    x=(sign * oi)[mask], y=strikes[mask], mode='markers', showlegend=False, hoverinfo='skip',
                    marker=dict(size=2, color=colour),
                    error_x=dict(type='data', symmetric=False,
                                 array=amount[mask] if toward_plus else zero,
                                 arrayminus=zero if toward_plus else amount[mask],
                                 color=colour, thickness=2.2, width=5),
                    xaxis=_OI_X_AXIS, yaxis='y'))

            b, sl = df[prefix + '_buy'], df[prefix + '_sell']
            d = df[b.notna() & sl.notna() & (b > sl)]
            if not d.empty:
                fig.add_trace(go.Scatter(
                    x=[sign * _MARKER_X * m] * len(d), y=d['strike'].to_numpy(dtype=float), mode='markers',
                    marker=dict(symbol='triangle-up' if is_call else 'triangle-down', size=10,
                                color='#d62728' if is_call else '#2ca02c', line=dict(color='black', width=1)),
                    hovertext=[f"<b>{name} {k:g}</b><br>Buy {bq:,.0f} &gt; Sell {sq:,.0f}<br>Sell/Buy = {_ratio_text(bq, sq)}"
                               for k, bq, sq in zip(d['strike'], d[prefix + '_buy'], d[prefix + '_sell'])],
                    hoverinfo='text', showlegend=False, name=name + ' buy>sell',
                    xaxis=_OI_X_AXIS, yaxis='y'))

        side('ce', True)
        side('pe', False)

        eq = _equity_qty(stream_rows)
        if eq and eq[0] != eq[1]:
            eb, es = eq
            buy_dom = eb > es
            sym = (underlying_info or {}).get('symbol', 'INDEX')
            fig.add_trace(go.Scatter(
                x=[0], y=[_EQ_TOP if buy_dom else _EQ_BOTTOM], mode='markers+text',
                marker=dict(symbol='triangle-up' if buy_dom else 'triangle-down', size=46,
                            color='#1a9850' if buy_dom else '#d73027', line=dict(color='black', width=1.2),
                            opacity=0.95),
                text=[f"<b>{_ratio_text(eb, es)}</b>"], textposition='middle center',
                textfont=dict(color='white', size=10),
                hovertext=[f"<b>{sym} (index future)</b><br>Buy qty {eb:,.0f}<br>Sell qty {es:,.0f}<br>"
                           f"{'Sell/Buy' if buy_dom else 'Buy/Sell'} = {_ratio_text(eb, es)}"],
                hoverinfo='text', showlegend=False, name='Index buy/sell',
                xaxis=_OI_X_AXIS, yaxis=_EQ_Y_AXIS))

        for prefix, colour in (('ce', 'red'), ('pe', 'green')):
            for rank, (_, r) in enumerate(df.nlargest(OI_WALLS_TOP_N, prefix + '_oi').iterrows()):
                kw = {}
                if rank == 0:
                    kw = dict(annotation_text=f"{prefix.upper()} wall {r['strike']:g} ({r[prefix + '_oi'] / 1e5:.1f}L)",
                              annotation_position='top left', annotation_font_size=9,
                              annotation_font_color=colour)
                fig.add_hrect(y0=r['strike'] - step / 2, y1=r['strike'] + step / 2, row=1, col=1,
                              fillcolor=colour, opacity=0.16 if rank == 0 else 0.07, line_width=0,
                              layer='below', **kw)

        mp = _max_pain_strike(full)
        fig.add_hline(y=mp, row=1, col=1, line_dash='dot', line_color='purple', line_width=1.5,
                      annotation_text=f"Max Pain {mp:g}", annotation_position='bottom left',
                      annotation_font_size=9, annotation_font_color='purple')
        spot = _num((underlying_info or {}).get('price')) or ltp
        fig.add_hline(y=spot, row=1, col=1, line_dash='dash', line_color='black', line_width=1)

        fig.add_annotation(x=centre, y=1.0, xref='paper', yref='paper', yanchor='bottom', yshift=13,
                           showarrow=False, font=dict(size=9),
                           text="<span style='color:#d62728'><b>◄ CE OI</b></span> | "
                                "<span style='color:#2ca02c'><b>PE OI ►</b></span>")
        fig.add_annotation(x=centre, y=1.0, xref='paper', yref='paper', yanchor='bottom', showarrow=False,
                           font=dict(size=8, color='#666'),
                           text="whisker = prev OI · ▲▼ = buy &gt; sell · big ▲▼ = index future (weak/strong)")
    except Exception as e:  # never let the overlay break the price chart
        logger.warning(f"Option-chain overlay skipped: {e}")


# _____________________________________________________________________________________
# SUPPORT / RESISTANCE LINES  = classic pivot points (P, R1, S1) of the previous Day / Week / Month
# _____________________________________________________________________________________
PIVOT_LEVELS = ("R1", "P", "S1")
SR_STYLES = {
    "D": ("dot",   1.3, {"R": "#ef9a9a", "P": "#90a4ae", "S": "#81c784"}),
    "W": ("dash",  1.7, {"R": "#e53935", "P": "#1e88e5", "S": "#43a047"}),
    "M": ("solid", 2.3, {"R": "#8e0000", "P": "#0d47a1", "S": "#0b5d1e"}),
}
_SR_FREQ = {"D": "D", "W": "W", "M": "M"}


def _sr_levels(df_full, tf):
    """Classic pivot points of the previous COMPLETED day / week / month for every candle plus one row for the NEXT
    session:  P = (H+L+C)/3,  R1 = 2P-L,  S1 = 2P-H,  R2 = P+(H-L),  S2 = P-(H-L),  R3 = H+2(P-L),  S3 = L-2(H-P)."""
    d = df_full[["TIMESTAMP", "EQ_HIGH_PRICE", "EQ_LOW_PRICE", "EQ_CLOSE_PRICE"]].dropna()
    if d.empty:
        return None
    freq = _SR_FREQ[tf]
    nxt = d["TIMESTAMP"].iloc[-1] + pd.offsets.BDay(1)
    dates = pd.concat([d["TIMESTAMP"], pd.Series([nxt])], ignore_index=True)
    agg = d.groupby(d["TIMESTAMP"].dt.to_period(freq)).agg(
        H=("EQ_HIGH_PRICE", "max"), L=("EQ_LOW_PRICE", "min"), C=("EQ_CLOSE_PRICE", "last"))
    h, l, c = (agg[k].to_numpy(dtype=float) for k in ("H", "L", "C"))
    p = (h + l + c) / 3.0
    by_period = {"P": p, "R1": 2 * p - l, "S1": 2 * p - h, "R2": p + (h - l), "S2": p - (h - l),
                 "R3": h + 2 * (p - l), "S3": l - 2 * (h - p)}
    pos = agg.index.searchsorted(pd.PeriodIndex(dates, freq=freq)) - 1
    ok = pos >= 0
    take = np.clip(pos, 0, None)
    out = {"TIMESTAMP": dates}
    for name, arr in by_period.items():
        out[name] = np.where(ok, arr[take], np.nan)
    return pd.DataFrame(out)


def _draw_sr_levels(fig, df_stock, sr_full, chain_data, x_end=None, label_x=None):
    """Pivot levels as step lines on the price chart (row 1), labelled with the level in force for the next session."""
    if not sr_full:
        return
    x0, x1 = df_stock['TIMESTAMP'].iloc[0], df_stock['TIMESTAMP'].iloc[-1]
    if x_end is None and OI_PROFILE_ENABLED and chain_data:
        x_end = x1 + (x1 - x0) * (OI_PROFILE_WIDTH / (1 - OI_PROFILE_WIDTH)) * 1.08
    for tf in ("D", "W", "M"):
        lv = sr_full.get(tf)
        if lv is None or lv.empty:
            continue
        lv = lv[lv["TIMESTAMP"] >= pd.Timestamp(x0).normalize()].reset_index(drop=True)
        if lv.empty:
            continue
        nxt = lv["TIMESTAMP"].iloc[-1]
        dash_, width, colours = SR_STYLES[tf]
        line_df = lv
        if x_end is not None and x_end > nxt:
            line_df = pd.concat([lv, lv.iloc[[-1]].assign(TIMESTAMP=x_end)], ignore_index=True)
        for level in PIVOT_LEVELS:
            if level not in lv.columns:
                continue
            name, colour = f"{tf}-{level}", colours[level[0]]
            fig.add_trace(go.Scatter(x=line_df["TIMESTAMP"], y=line_df[level], mode="lines", name=name,
                                     line=dict(color=colour, width=width, dash=dash_, shape="hv"), showlegend=False,
                                     hovertemplate=name + " %{y:,.2f}<extra></extra>"), row=1, col=1)
            last = lv[level].iloc[-1]
            if pd.notna(last):
                fig.add_trace(go.Scatter(x=[label_x if label_x is not None else nxt], y=[last], mode="text",
                                         text=[f"{name} {last:,.1f}"], textposition="top left",
                                         textfont=dict(size=9, color=colour), showlegend=False,
                                         hoverinfo="skip"), row=1, col=1)


# _____________________________________________________________________________________
# PRICE CHART  (any timeframe, straight from Upstox)
# _____________________________________________________________________________________

def build_timeframe_figure(symbol, key, tf, n_days, ma_lines, ma_periods, sr_levels, chain_data, stream_rows,
                           underlying_info, expiry_value):
    """Index price (+ volume) chart for any timeframe from Upstox, with moving averages, pivot S/R and the live
    option-chain overlay. 'Day' = history + today's live quote merged into ONE candle per day."""
    intraday = tf in _TF_STEP_MIN
    unit, interval = TF_SPEC[tf]
    n_days = int(n_days or 60)
    if tf == "D":
        days = int(n_days * 1.6) + max(ma_periods.values()) * 2 + 10     # extra history so the MAs start on candle 1
    elif tf == "W":
        days = 3 * 365
    elif tf == "M":
        days = 8 * 365
    elif unit == "minutes" and interval <= 15:
        days = min(n_days, 30)                      # Upstox keeps 1-15 min history requests to about a month
    else:
        days = n_days
    raw = fetch_candles(key, tf, days)
    if tf == "D":
        raw = merge_live_row(raw, get_live_daily_row(key))
    if raw.empty:
        return _empty_fig(f"No {TF_LABEL[tf]} candles from Upstox for {symbol} (access token / market data?)")
    df_stock = _to_eq_columns(raw)
    for k, p in ma_periods.items():
        df_stock[f"MA_{k}"] = df_stock["EQ_CLOSE_PRICE"].rolling(p).mean()

    sr_full = {}
    if sr_levels:
        daily = merge_live_row(fetch_candles(key, "D", 400), get_live_daily_row(key))
        if not daily.empty:
            dd = _to_eq_columns(daily)
            sr_full = {t_: _sr_levels(dd, t_) for t_ in sr_levels if t_ in SR_STYLES}

    if tf == "D":
        df_stock = df_stock.tail(n_days).reset_index(drop=True)

    # right-hand room for the OI profile
    x_end = label_x = None
    if intraday:
        n_extra = int(np.ceil(len(df_stock) * OI_PROFILE_WIDTH / (1 - OI_PROFILE_WIDTH) * 1.08)) + 1
        x_end = _future_slot(df_stock['TIMESTAMP'].iloc[-1], _TF_STEP_MIN[tf], n_extra)
        label_x = df_stock['TIMESTAMP'].iloc[-1]
    elif tf == "D":
        n_extra = int(np.ceil(len(df_stock) * OI_PROFILE_WIDTH / (1 - OI_PROFILE_WIDTH) * 1.08)) + 1
        x_end = pd.Timestamp(df_stock['TIMESTAMP'].iloc[-1]) + pd.offsets.BDay(n_extra)
        label_x = df_stock['TIMESTAMP'].iloc[-1]

    fig = make_subplots(rows=2, cols=1, row_heights=[0.80, 0.20], shared_xaxes=True, vertical_spacing=0,
                        print_grid=False)
    fig.update_layout(paper_bgcolor='rgb(255,255,255)', plot_bgcolor='rgb(255,255,255)', autosize=True, height=None,
                      margin=dict(r=2, t=2, b=2, l=2), showlegend=False, dragmode='drawline',
                      newshape_line_color='cyan')
    fig.update_xaxes(showline=True, linewidth=2, linecolor='black', rangeslider_visible=False)
    fig.update_yaxes(mirror="ticks", side='right')
    if intraday or tf == "D":        # hide nights (intraday), weekends and exchange holidays (weekdays with no candle)
        have = set(df_stock['TIMESTAMP'].dt.strftime('%Y-%m-%d'))
        span = pd.date_range(df_stock['TIMESTAMP'].min().normalize(), df_stock['TIMESTAMP'].max().normalize())
        holidays = [d.strftime('%Y-%m-%d') for d in span if d.weekday() < 5 and d.strftime('%Y-%m-%d') not in have]
        breaks = [dict(bounds=["sat", "mon"])]
        if intraday:
            breaks.append(dict(bounds=[15.5, 9.25], pattern="hour"))
        if holidays:
            breaks.append(dict(values=holidays))
        fig.update_xaxes(rangebreaks=breaks)

    fig.add_trace(go.Candlestick(x=df_stock['TIMESTAMP'], open=df_stock['EQ_OPEN_PRICE'],
                                 high=df_stock['EQ_HIGH_PRICE'], low=df_stock['EQ_LOW_PRICE'],
                                 close=df_stock['EQ_CLOSE_PRICE'], name='Price'), row=1, col=1)
    for k, colour, w in (("short", '#1f77b4', 1.2), ("medium", '#ff7f0e', 1.6), ("long", '#9467bd', 2.0)):
        if k in (ma_lines or []):
            fig.add_trace(go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock[f"MA_{k}"], mode='lines',
                                     line=dict(color=colour, width=w), name=f"{ma_periods[k]} SMA Close",
                                     hovertemplate=f"{ma_periods[k]} SMA %{{y:,.2f}}<extra></extra>"), row=1, col=1)
    _draw_sr_levels(fig, df_stock, sr_full, chain_data, x_end=x_end, label_x=label_x)
    up = df_stock['EQ_CLOSE_PRICE'] >= df_stock['EQ_OPEN_PRICE']
    fig.add_trace(go.Bar(x=df_stock['TIMESTAMP'], y=df_stock['EQ_TTL_TRD_QNTY'], name='Volume',
                         marker_color=np.where(up, '#26a69a', '#ef5350')), row=2, col=1)
    fig.update_layout(yaxis2=dict(showticklabels=False))
    fig['layout']['yaxis']['title'] = 'Index OHLC'
    fig['layout']['yaxis2']['title'] = 'Volume'

    _add_option_chain_overlay(fig, df_stock, chain_data, underlying_info, stream_rows, x_end=x_end)

    ltp = df_stock['EQ_CLOSE_PRICE'].iloc[-1]
    prev = df_stock['EQ_CLOSE_PRICE'].iloc[-2] if len(df_stock) > 1 else ltp
    live_note = "  |  ● latest candle live from Upstox" if tf == "D" else ""
    fig.update_layout(
        xaxis_title=f"{symbol} | {TF_LABEL[tf]} | LTP: {ltp:g} | vs prev candle: {(ltp - prev) / prev * 100:+.2f}% | "
                    f"{len(df_stock)} candles (Upstox){live_note}",
        uirevision=f"{symbol}|{expiry_value}|{tf}|{n_days}")
    return fig


# =====================================================================================
# 7. DASH APP, POP-OUT WINDOWS AND LAYOUT
# =====================================================================================


ROW_DEFAULTS = {"r1": 100, "r2": 40, "r3": 26}   # row heights in vh; Row 1 = a full screen, rows 2-3 are below the fold


def _rh_group(label, key):
    return html.Div([
        html.Span(label, className="text-white small me-1"),
        dbc.ButtonGroup([
            dbc.Button("−", id=f"{key}-dec", size="sm", color="light", outline=True, className="py-0 px-2",
                       title=f"Decrease {label} height"),
            dbc.Button("+", id=f"{key}-inc", size="sm", color="light", outline=True, className="py-0 px-2",
                       title=f"Increase {label} height"),
        ])
    ], className="d-flex align-items-center me-3")


app = dash.Dash(__name__, external_stylesheets=[dbc.themes.FLATLY])
server = app.server          # WSGI entry point


# =====================================================================
# POP-OUT WINDOWS for the Trend Analytics graphs (drag the window to a second monitor).
# =====================================================================
POPOUT_FIGS = {}                      # name -> {"rev": int, "json": str}
_POPOUT_LOCK = threading.Lock()
POPOUT_TITLES = {"oi-trend": "OI Trend", "buysell-trend": "Buy/Sell Qty Trend"}
POPOUT_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>__TITLE__</title>
<style>
  html, body { height: 100%; margin: 0; font: 12px system-ui, sans-serif; background: #fff; }
  #bar { height: 26px; line-height: 26px; padding: 0 10px; background: #0d6efd; color: #fff;
         display: flex; justify-content: space-between; }
  #g { position: absolute; top: 26px; left: 0; right: 0; bottom: 0; }
  #msg { position: absolute; top: 45%; left: 0; right: 0; text-align: center; color: #888; font-size: 14px; }
</style></head>
<body>
<div id="bar"><b>__TITLE__</b><span id="st">connecting...</span></div>
<div id="g"></div><div id="msg"></div>
<script src="/popout-plotly.js"></script>
<script>
let rev = -1;
const st = document.getElementById('st'), msg = document.getElementById('msg');
async function tick() {
  try {
    const r = await fetch('/popout-data/__NAME__', {cache: 'no-store'});
    const j = await r.json();
    if (!j.figure) {
      msg.textContent = 'Waiting for data from the main window (start Record and pick strikes)...';
      st.textContent = 'no data yet';
      return;
    }
    msg.textContent = '';
    if (j.rev !== rev) {
      rev = j.rev;
      const f = j.figure, lay = Object.assign({}, f.layout, {autosize: true, uirevision: 'keep'});
      delete lay.height; delete lay.width;
      Plotly.react('g', f.data, lay, {responsive: true, scrollZoom: true, displaylogo: false});
    }
    st.textContent = 'updated ' + new Date().toLocaleTimeString();
  } catch (e) {
    st.textContent = 'main app not reachable';
  }
}
tick();
setInterval(tick, 2000);
</script></body></html>"""


def _popout_stash(name):
    """Decorator for a graph callback: keep the latest figure so the pop-out window can fetch it."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            fig = fn(*args, **kwargs)
            try:
                with _POPOUT_LOCK:
                    POPOUT_FIGS[name] = {"rev": POPOUT_FIGS.get(name, {}).get("rev", 0) + 1, "json": fig.to_json()}
            except Exception as e:
                logger.warning(f"pop-out stash skipped ({name}): {e}")
            return fig
        return wrapper
    return deco


@app.server.route("/popout-plotly.js")
def _popout_plotlyjs():
    resp = Response(plotly.offline.get_plotlyjs(), mimetype="application/javascript")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.server.route("/popout-data/<name>")
def _popout_data(name):
    with _POPOUT_LOCK:
        item = POPOUT_FIGS.get(name)
    body = '{"rev": %d, "figure": %s}' % (item["rev"], item["json"]) if item else '{"rev": 0, "figure": null}'
    resp = Response(body, mimetype="application/json")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.server.route("/popout/<name>")
def _popout_page(name):
    if name not in POPOUT_TITLES:
        return Response("Unknown graph", status=404)
    return Response(POPOUT_HTML.replace("__NAME__", name).replace("__TITLE__", POPOUT_TITLES[name]),
                    mimetype="text/html")


# --- App layout ---
app.layout = dbc.Container([
    # --- HEADER (compact, with row-height controls) ---
    dbc.Row([
        dbc.Col(
            html.Div([
                html.Span("360° INDEX ANALYSIS", className="text-white fw-bold fs-6 me-3"),
                html.Span("Real-time Index Analytics & Strike Tracking",
                          className="text-white-50 small d-none d-xxl-inline"),
                html.Div([
                    html.Span("Row height:", className="text-white-50 small me-2"),
                    _rh_group("Row 1", "r1"), _rh_group("Row 2", "r2"),
                    dbc.Button("Reset", id="rh-reset", size="sm", color="light", outline=True, className="py-0 px-2"),
                ], className="d-flex align-items-center ms-auto me-4"),
                dbc.Button("⚙ Settings", id="btn-open-settings", size="sm", color="light", outline=True,
                           className="py-0 px-2 me-3 fw-bold", title="Settings, Access Tokens, Shortlist, Alerts"),
                dbc.Checklist(
                    options=[{"label": "📱 Portrait Mode", "value": True}],
                    value=[], id="layout-toggle-switch", inline=True, switch=True,
                    className="text-white fw-bold small mb-0"
                ),
            ], className="bg-primary px-3 py-1 rounded shadow-sm mb-1 d-flex align-items-center"),
            width=12)
    ]),

    # --- ACCESS TOKEN EXPIRY BANNER ---
    dbc.Alert([html.Span(id="token-expiry-text"),
               dbc.Button("Generate now", id="btn-banner-open-tokens", size="sm", color="danger", outline=True,
                          className="py-0 px-2 ms-3 fw-bold")],
              id="token-expiry-banner", color="danger", is_open=False,
              className="py-1 px-2 mb-1 small d-flex align-items-center"),

    # --- DYNAMIC LAYOUT HOLDER ---
    html.Div(id="dynamic-content-layout"),

    # --- INVISIBLE STORES ---
    dcc.Store(id='option-chain-store'),
    dcc.Store(id='underlying-info-store'),
    dcc.Store(id='recording-data-store', data=[]),
    dcc.Store(id='recording-active-store', data=False),
    dcc.Store(id='stream-qty-store', data=[]),
    dcc.Store(id='row-heights-store-v3', storage_type='local', data=ROW_DEFAULTS),
    dcc.Store(id='rh-dummy'),
    dcc.Store(id='popout-dummy'),
    dcc.Store(id='token-gen-trigger'),
    dcc.Interval(id='token-check-interval', interval=5 * 60 * 1000, n_intervals=0),

    # --- SHORTLIST MODAL ---
    dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle("Add to Shortlist", className="fw-bold")),
        dbc.ModalBody([
            html.Label("Selected Index:", className="fw-bold text-muted small"),
            html.Div(id="modal-symbol-display", className="fw-bold fs-5 text-primary mb-3"),
            html.Label("Notes:", className="fw-bold text-muted small"),
            dbc.Textarea(id="modal-notes-input", placeholder="Type your observation/thesis here...",
                         style={'height': 120})
        ]),
        dbc.ModalFooter([
            dbc.Button("Save to Table", id="btn-save-shortlist-modal", color="primary", className="fw-bold"),
            dbc.Button("Cancel", id="btn-close-shortlist-modal", color="secondary", outline=True)
        ])
    ], id="shortlist-modal", is_open=False, centered=True),

    # --- ZERODHA ALERTS MODAL ---
    dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle("Create Zerodha Alert", className="fw-bold")),
        dbc.ModalBody(
            dbc.Row([
                dbc.Col([
                    dbc.Label("Alert Name", className="small fw-bold"),
                    dbc.Input(id="alert-name", type="text", placeholder="E.g., NIFTY Target"),
                    dbc.Row([
                        dbc.Col([
                            dbc.Label("Exchange", className="small fw-bold mt-2"),
                            dcc.Dropdown(id="alert-exchange", options=[
                                {'label': 'NSE', 'value': 'NSE'}, {'label': 'NFO', 'value': 'NFO'},
                                {'label': 'BSE', 'value': 'BSE'}, {'label': 'INDICES', 'value': 'INDICES'}
                            ], value='INDICES')
                        ], width=4),
                        dbc.Col([
                            dbc.Label("Trading Symbol", className="small fw-bold mt-2"),
                            dbc.Input(id="alert-symbol", type="text", placeholder="E.g., NIFTY 50"),
                        ], width=8)
                    ]),
                    dbc.Label("Property to Monitor", className="small fw-bold mt-2"),
                    dcc.Dropdown(id="alert-attribute", options=[
                        {'label': 'Last Traded Price', 'value': 'LastTradedPrice'},
                        {'label': 'Open', 'value': 'Open'}, {'label': 'High', 'value': 'High'},
                        {'label': 'Low', 'value': 'Low'}, {'label': 'Close', 'value': 'Close'}
                    ], value='LastTradedPrice'),
                    dbc.Row([
                        dbc.Col([
                            dbc.Label("Operator", className="small fw-bold mt-2"),
                            dcc.Dropdown(id="alert-operator", options=[
                                {'label': '>= (Greater/Equal)', 'value': '>='},
                                {'label': '<= (Less/Equal)', 'value': '<='},
                                {'label': '== (Equal)', 'value': '=='}
                            ], value='>=')
                        ], width=6),
                        dbc.Col([
                            dbc.Label("Target Value", className="small fw-bold mt-2"),
                            dbc.Input(id="alert-value", type="number", placeholder="E.g., 25000")
                        ], width=6)
                    ]),
                    dbc.Label("Alert Type", className="small fw-bold mt-3"),
                    dbc.RadioItems(id="alert-type", options=[
                        {'label': ' Simple Alert', 'value': 'simple'},
                        {'label': ' ATO (Alert Triggers Order)', 'value': 'ato'}
                    ], value='simple', inline=True, className="mb-2"),
                ], md=12, id="alert-left-col"),
                dbc.Col([
                    html.H6("ATO Basket Configuration", className="fw-bold text-primary border-bottom pb-1"),
                    dbc.Label("Trading Symbol", className="small fw-bold mt-2"),
                    dcc.Dropdown(id="ato-tradingsymbol", options=ZERODHA_SYMBOL_OPTIONS,
                                 placeholder="Search & Select Symbol...", searchable=True),
                    dbc.Row([
                        dbc.Col([
                            dbc.Label("Trans. Type", className="small fw-bold mt-2"),
                            dcc.Dropdown(id="ato-transaction-type",
                                         options=[{'label': 'BUY', 'value': 'BUY'}, {'label': 'SELL', 'value': 'SELL'}],
                                         value='BUY')
                        ], width=6),
                        dbc.Col([
                            dbc.Label("Product", className="small fw-bold mt-2"),
                            dcc.Dropdown(id="ato-product",
                                         options=[{'label': 'CNC', 'value': 'CNC'}, {'label': 'MIS', 'value': 'MIS'},
                                                  {'label': 'NRML', 'value': 'NRML'}], value='NRML')
                        ], width=6)
                    ]),
                    dbc.Label("Quantity", className="small fw-bold mt-2"),
                    dbc.Input(id="ato-quantity", type="number", min=1, step=1, value=1),
                    dbc.Button("⚙ Create ATO", id="btn-create-ato-basket", size="sm", color="info",
                               className="mt-3 w-100 fw-bold text-white"),
                    dbc.Label("Generated ATO JSON", className="small fw-bold mt-3"),
                    dbc.Textarea(id="ato-basket-input", placeholder='JSON payload will generate here...',
                                 style={"height": "120px", "fontSize": "11px"})
                ], md=6, id="ato-basket-div", style={"display": "none"})
            ])
        ),
        dbc.ModalFooter([
            html.Div(id="alert-status-msg", className="text-success fw-bold small me-auto"),
            dbc.Button("Place Alert", id="btn-submit-alert", color="primary", className="fw-bold"),
            dbc.Button("Cancel", id="btn-cancel-alert", color="secondary", outline=True)
        ])
    ], id="create-alert-modal", size="lg", is_open=False, centered=True),
], fluid=True, className="bg-light pt-1 pb-1 px-2")


# --- Layout building blocks (shared by the portrait and compact layouts) ---

def build_sr_control():
    """Timeframe selector + Support / Resistance (pivot points of the previous Month)."""
    return html.Div([
        html.Div(dcc.Dropdown(id="tf-select", value="D", clearable=False, searchable=False,
                              options=[{"label": TF_LABEL[v], "value": v} for v in TF_SPEC],
                              style={"fontSize": "0.78rem"}),
                 style={"width": "92px"}, className="me-3",
                 title="Candle timeframe (Upstox). Day = history + today's live quote."),
        html.Span("S/R", className="fw-bold small me-2", style={"cursor": "help"},
                  title="Support & Resistance = pivot points (P, R1, S1) of the previous Month"),
        dbc.Checklist(id="sr-levels-select", inline=True, switch=True, value=[], className="mb-0",
                      style={"fontSize": "0.72rem"},
                      options=[{"label": "Monthly", "value": "M"}]),
    ], className="d-flex align-items-center")


def _tab_label_styles():
    return dict(label_style={"border": "none", "background": "transparent", "color": "#6c757d"},
                active_label_style={"border": "none", "border-bottom": "3px solid #007bff", "color": "#007bff",
                                    "background": "transparent", "font-weight": "bold"})


_TL = _tab_label_styles()

equity_hub_card = dbc.Card([
    dbc.CardHeader(
        dbc.Row([
            dbc.Col([html.H5("Index & Options Analysis", className="m-0 fw-bold text-primary text-center text-md-start")],
                    xs=12, md=2, className="mb-2 mb-md-0"),
            dbc.Col([
                dbc.Label("Scanner", className="fw-bold text-muted mb-1 small"),
                dcc.Dropdown(id='scanner-dropdown',
                             options=[{'label': 'None', 'value': 'None'},
                                      {'label': 'Shortlisted', 'value': 'Shortlisted'}],
                             value='None', clearable=False)
            ], xs=6, md=2, className="mb-2 mb-md-0"),
            dbc.Col([
                dbc.Label("Index", className="fw-bold text-muted mb-1 small"),
                dbc.InputGroup([
                    dbc.Button("★ Shortlist", id="btn-open-shortlist-modal", color="warning", size="sm", outline=True,
                               className="fw-bold text-dark"),
                    dbc.Button("◄", id="prev-btn", n_clicks=0, color="secondary", outline=True),
                    dcc.Dropdown(id='symbol-dropdown', options=[{'label': s, 'value': s} for s in UNIQUE_SYMBOLS],
                                 value=UNIQUE_SYMBOLS[0] if UNIQUE_SYMBOLS else None,
                                 style={'min-width': '100px', 'flex': '1 1 auto'}),
                    dbc.Button("►", id="next-btn", n_clicks=0, color="secondary", outline=True),
                ], size="sm")
            ], xs=12, md=3, className="mb-2 mb-md-0"),
            dbc.Col([
                dbc.Label("Expiry", className="fw-bold text-muted mb-1 small"),
                dcc.Dropdown(id='expiry-dropdown', placeholder="Select Expiry", style={'fontSize': '0.9rem'})
            ], xs=6, md=2, className="mb-2 mb-md-0"),
            dbc.Col([
                dbc.Label("Days", className="fw-bold text-muted mb-1 small"),
                dcc.Dropdown(id='dropdown_n_days',
                             options=[{'label': f"{d} Days", 'value': d} for d in [7, 15, 30, 60, 90, 120, 150]],
                             value=60, multi=False, clearable=False),
            ], xs=4, md=1, className="mb-2 mb-md-0"),
            dbc.Col([
                html.H6("Max Pain", className="text-muted text-uppercase mb-0", style={'fontSize': '0.7rem'}),
                html.Div(id="max-pain-display", className="text-primary fw-bold", style={'fontSize': '1.1rem'}),
                html.Div(id="underlying-price-display", className="text-secondary small",
                         style={'fontSize': '0.75rem'}),
                dbc.Button("🔔 Create Alert", id="btn-create-alert", size="sm", color="primary", outline=True,
                           className="mt-1 fw-bold")
            ], xs=8, md=2, className="text-center text-md-end align-self-center")
        ], className="g-2 align-items-center")
    ),
    dbc.CardBody([
        dbc.Tabs([
            dbc.Tab(label="Index Analysis", tab_id="tab-stock-analysis", children=[
                html.Div(build_sr_control(), className="px-2 pt-1"),
                dcc.Graph(id='graph_31', config=dict({'scrollZoom': True}),
                          style={"height": "60vh", "minHeight": "400px"})], **_TL),
            dbc.Tab(label="Options Hub", tab_id="tab-ce-hub", children=[
                dbc.Row([
                    dbc.Col([html.Div([html.Div([html.Label("CE Strike:", className="fw-bold small text-muted me-2"),
                                                 dcc.RadioItems(id='ce-strike-radio', inline=True,
                                                                labelStyle={'margin-right': '8px', 'cursor': 'pointer',
                                                                            'fontSize': '0.8rem'})],
                                                className="mb-2 p-2 bg-light rounded border-bottom"),
                                       dcc.Graph(id='ce-candle-graph', style={'height': '40vh', 'minHeight': '350px'})],
                                      className="pt-2")], xs=12, lg=6),
                    dbc.Col([html.Div([html.Div([html.Label("PE Strike:", className="fw-bold small text-muted me-2"),
                                                 dcc.RadioItems(id='pe-strike-radio', inline=True,
                                                                labelStyle={'margin-right': '8px', 'cursor': 'pointer',
                                                                            'fontSize': '0.8rem'})],
                                                className="mb-2 p-2 bg-light rounded border-bottom"),
                                       dcc.Graph(id='pe-candle-graph', style={'height': '40vh', 'minHeight': '350px'})],
                                      className="pt-2")], xs=12, lg=6)
                ])
            ], **_TL),
            dbc.Tab(label="PCR Trend", tab_id="tab-pcr", children=[
                html.Div(dcc.Graph(id='pcr-graph', style={"height": "50vh", "minHeight": "350px"}),
                         className="pt-2")], **_TL),
            dbc.Tab(label="Total OI", tab_id="tab-oi", children=[
                html.Div(dcc.Graph(id='oi-graph', style={"height": "50vh", "minHeight": "350px"}),
                         className="pt-2")], **_TL),
            dbc.Tab(label="OI Change", tab_id="tab-oi-change", children=[
                html.Div(dcc.Graph(id='oi-change-graph', style={"height": "50vh", "minHeight": "350px"}),
                         className="pt-2")], **_TL),
            dbc.Tab(label="Volume", tab_id="tab-volume", children=[
                html.Div(dcc.Graph(id='volume-graph', style={"height": "50vh", "minHeight": "350px"}),
                         className="pt-2")], **_TL),
        ], id="options-hub-tabs", active_tab="tab-ce-hub",
            style={"border-bottom": "1px solid #dee2e6", "overflowX": "auto", "whiteSpace": "nowrap"})
    ])
], className="shadow-sm border-0 h-100")

iv_graph_element = html.Div([
    dbc.Checklist(options=[{"label": "Exclude ITM Options", "value": True}], value=[True], id="exclude-itm-toggle",
                  switch=True, inline=True, className="mt-2 mb-1 text-muted small d-flex justify-content-end"),
    dcc.Graph(id='iv-graph', style={'height': '43vh', 'minHeight': '350px'})
])

otm_prices_element = dcc.Graph(id='otm-prices-graph', style={'height': '43vh', 'minHeight': '350px'})


# _____________________________________________________________________________________
# ACCESS TOKENS  (generate with the user's own scripts, check today's token files in GCS)
# _____________________________________________________________________________________


def _token_exists(broker):
    """True / False = today's <broker>/<date>.json exists in the bucket; None = could not check (GCS unreachable)."""
    try:
        blob = storage.Client().bucket(TOKEN_BUCKET).blob(f"{broker}/{datetime.datetime.now().date()}.json")
        return bool(blob.exists())
    except Exception as e:
        logger.warning(f"❌ Token check failed for {broker}: {e}")
        return None


def _apply_token(broker, token):
    """Make a (new) token live in the running app - no restart needed."""
    global DEFAULT_TOKEN, KITE_ACCESS_TOKEN
    if broker == "upstox":
        DEFAULT_TOKEN = token
    else:
        KITE_ACCESS_TOKEN = token
        KITE_HEADERS['Authorization'] = f'token {KITE_API_KEY}:{token}'


def _load_script_module(path, module_name):
    """Import one of the token scripts from a file path (their `if __name__ == "__main__"` block does not run)."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"{os.path.basename(path)} not found at {path}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _access_tokens_item():
    return dbc.AccordionItem([
        html.Div(f"Tokens are saved to gs://{TOKEN_BUCKET}/upstox/ and /zerodha/ as <date>.json and used "
                 f"immediately - no restart needed.", className="small text-muted mb-2"),
        dbc.Row([
            dbc.Col(dbc.Button("Generate Upstox Token", id="btn-gen-upstox-token", color="primary", size="sm",
                               className="w-100 fw-bold"), width=6, className="pe-1"),
            dbc.Col(dbc.Button("Generate Zerodha Token", id="btn-gen-zerodha-token", color="warning", size="sm",
                               className="w-100 fw-bold"), width=6, className="ps-1"),
        ], className="mb-2 g-0"),
        dcc.Loading(html.Div(id="token-gen-status", className="small"), type="dot"),
        html.Hr(className="my-2"),
        html.Div(id="token-status-detail", className="small"),
    ], title="Access Tokens", item_id="tokens")


def _settings_items():
    return [
        _access_tokens_item(),
        dbc.AccordionItem([
            html.H6("Moving average lines (on the price chart)", className="small text-muted"),
            dbc.Checklist(id="ma-lines-select", inline=True, switch=True, className="mb-1",
                          style={"fontSize": "0.85rem"},
                          options=[{"label": "MA 1", "value": "short"}, {"label": "MA 2", "value": "medium"},
                                   {"label": "MA 3", "value": "long"}],
                          value=["short", "medium"]),
            dbc.Row([
                dbc.Col([html.Label("MA 1 period", className="small text-muted mb-1"),
                         dcc.Input(id="input_short_SMA", type="number", min=1, max=400, step=1, value=5,
                                   className="form-control form-control-sm")], xs=4),
                dbc.Col([html.Label("MA 2 period", className="small text-muted mb-1"),
                         dcc.Input(id="input_medium_SMA", type="number", min=1, max=400, step=1, value=20,
                                   className="form-control form-control-sm")], xs=4),
                dbc.Col([html.Label("MA 3 period", className="small text-muted mb-1"),
                         dcc.Input(id="input_long_SMA", type="number", min=1, max=400, step=1, value=50,
                                   className="form-control form-control-sm")], xs=4),
            ], className="mb-3 g-2"),
        ], title="Chart Settings"),
        dbc.AccordionItem([
            dbc.Button("Save Table to GCS", id="btn-save-shortlist-gcs", color="success", size="sm",
                       className="mb-2 w-100 fw-bold"),
            html.Div(id="save-gcs-status", className="text-center small fw-bold mb-2 text-success"),
            dash_table.DataTable(id='shortlist-table',
                                 columns=[{'name': 'Symbol', 'id': 'Symbol'}, {'name': 'Notes', 'id': 'Notes'}],
                                 data=load_shortlist_from_gcs(), row_deletable=True,
                                 style_cell={'textAlign': 'left', 'fontSize': '12px', 'whiteSpace': 'normal',
                                             'height': 'auto'},
                                 style_header={'fontWeight': 'bold', 'backgroundColor': '#f8f9fa'},
                                 style_table={'overflowX': 'auto', 'maxHeight': '300px', 'overflowY': 'auto'},
                                 page_size=10)
        ], title="Shortlisted"),
        dbc.AccordionItem([
            dbc.Row([
                dbc.Col(dbc.Button("↻ Refresh", id="btn-refresh-alerts", size="sm", color="info", outline=True,
                                   className="w-100 fw-bold"), width=6, className="pe-1"),
                dbc.Col(dbc.Button("✎ Modify Selected", id="btn-modify-alert", size="sm", color="warning",
                                   outline=True, className="w-100 fw-bold"), width=6, className="ps-1"),
            ], className="mb-2 g-0"),
            dash_table.DataTable(id='alerts-table',
                                 columns=[{"name": "Name", "id": "name"}, {"name": "Symbol", "id": "symbol"},
                                          {"name": "Cond.", "id": "condition"}, {"name": "Type", "id": "type"}],
                                 data=[], row_deletable=True, row_selectable="single",
                                 style_cell={'textAlign': 'left', 'fontSize': '11px', 'padding': '4px'},
                                 style_header={'fontWeight': 'bold', 'backgroundColor': '#f8f9fa'},
                                 style_table={'overflowX': 'auto', 'maxHeight': '200px'}),
            html.Div(id="alerts-error-msg", className="text-danger small mt-2"),
            dcc.Store(id="alerts-raw-data-store", data=[]), dcc.Store(id="modify-alert-uuid", data=None)
        ], title="Active Alerts"),
    ]


def build_settings_fundamentals():
    return html.Div([dbc.Accordion(_settings_items(), id="settings-accordion", start_collapsed=True, flush=True)],
                    className="py-2")


# Right-side drawer (non-modal: the dashboard stays usable while it is open).
settings_offcanvas = dbc.Offcanvas(
    build_settings_fundamentals(),
    id="settings-offcanvas", title="⚙ Settings", placement="end", is_open=False,
    scrollable=True, backdrop=False, style={"width": "480px", "maxWidth": "95vw"})
app.layout.children.append(settings_offcanvas)


trend_analytics_card = dbc.Card([
    dbc.CardHeader(
        dbc.Row([
            dbc.Col(html.H6("Trend Analytics Workspace", className="m-0 fw-bold text-info d-flex align-items-center"),
                    xs=12, sm=6, className="text-center text-sm-start mb-2 mb-sm-0"),
            dbc.Col(html.Div([
                html.Label("Height:", className="small text-muted me-2 fw-semibold mb-0",
                           style={"whiteSpace": "nowrap"}),
                html.Div(dcc.Slider(id='trend-height-slider', min=200, max=1000, value=400, step=50,
                                    marks={200: '200px', 600: '600px', 1000: '1000px'},
                                    tooltip={"placement": "bottom", "always_visible": False}),
                         style={"flex": "1", "paddingTop": "5px"})
            ], className="d-flex align-items-center justify-content-end"), xs=12, sm=6)
        ], className="align-items-center")
    ),
    dbc.CardBody([
        html.Div([
            dbc.Row([
                dbc.Col([html.Label("CE Strikes", className="fw-bold text-success small mb-1"),
                         dcc.Checklist(id='trend-ce-strikes', inline=True,
                                       labelStyle={'margin-right': '10px', 'cursor': 'pointer',
                                                   'fontSize': '0.75rem'})], xs=12, sm=6,
                        className="border-sm-end mb-2 mb-sm-0"),
                dbc.Col([html.Label("PE Strikes", className="fw-bold text-danger small mb-1"),
                         dcc.Checklist(id='trend-pe-strikes', inline=True,
                                       labelStyle={'margin-right': '10px', 'cursor': 'pointer',
                                                   'fontSize': '0.75rem'})], xs=12, sm=6)
            ])
        ], className="p-2 mb-3 bg-light rounded border"),
        dbc.Tabs([
            dbc.Tab(label="OI Trend", tab_id="tab-trend-oi",
                    children=[html.Div(dcc.Graph(id='oi-trend-graph', style={'height': '400px'}), className="pt-2")],
                    **_TL),
            dbc.Tab(label="Buy/Sell Qty Trend", tab_id="tab-trend-buysell", children=[
                html.Div(dcc.Graph(id='buysell-trend-graph', style={'height': '400px'}), className="pt-2")], **_TL)
        ], id="trend-workspace-tabs", active_tab="tab-trend-oi", style={"border-bottom": "1px solid #dee2e6"})
    ])
], className="shadow-sm border-0 h-100")


# =====================================================================
# COMPACT ONE-SCREEN LAYOUT (24" monitor, landscape)
# Row heights are CSS variables (--r1-h, --r2-h, --r3-h) so the header +/- buttons can change them live.
# =====================================================================
PANEL_CHROME_PX = 42  # card header + body padding
TOP_CHROME_PX = 100   # page header + controls card that sit above Row 1 (Row 1 + this = one screen). Tune if needed.


def _hv(row):
    base = f"var(--{row}-h, {ROW_DEFAULTS[row]}vh)"
    return f"calc({base} - {TOP_CHROME_PX}px)" if row == 'r1' else base


def _h(row, extra_px=0):
    """CSS height for content inside a panel of row `row` ('r1' | 'r2' | 'r3')."""
    return f"calc({_hv(row)} - {PANEL_CHROME_PX + extra_px}px)"


def _panel(title, body, row, title_cls="text-primary", right=None, scroll=False):
    header = html.Div([html.Span(title, className=f"fw-bold small {title_cls}"),
                       right if right is not None else ""],
                      className="d-flex justify-content-between align-items-center")
    return dbc.Card([
        dbc.CardHeader(header, className="py-1 px-2"),
        dbc.CardBody(body, className="p-1", style={"overflow": "auto" if scroll else "hidden"}),
    ], className="shadow-sm border-0", style={"height": _hv(row)})


def _col(child, pct):
    return dbc.Col(child, className="px-1", style={"flex": f"0 0 {pct}%", "maxWidth": f"{pct}%"})


def _tab_styles():
    return dict(
        label_style={"border": "none", "background": "transparent", "color": "#6c757d", "padding": "2px 8px",
                     "fontSize": "0.8rem"},
        active_label_style={"border": "none", "border-bottom": "3px solid #007bff", "color": "#007bff",
                            "background": "transparent", "font-weight": "bold", "padding": "2px 8px",
                            "fontSize": "0.8rem"})


def _lbl(text):
    return html.Div(text, className="text-muted fw-bold", style={"fontSize": "0.65rem", "lineHeight": "1"})


def build_compact_controls():
    return dbc.Card(dbc.CardBody(dbc.Row([
        dbc.Col([_lbl("Scanner"),
                 dcc.Dropdown(id='scanner-dropdown',
                              options=[{'label': 'None', 'value': 'None'},
                                       {'label': 'Shortlisted', 'value': 'Shortlisted'}],
                              value='None', clearable=False)], width=1),
        dbc.Col([_lbl("Index"),
                 dbc.InputGroup([
                     dbc.Button("★ Shortlist", id="btn-open-shortlist-modal", color="warning", size="sm",
                                outline=True, className="fw-bold text-dark"),
                     dbc.Button("◄", id="prev-btn", n_clicks=0, color="secondary", outline=True),
                     dcc.Dropdown(id='symbol-dropdown',
                                  options=[{'label': s_, 'value': s_} for s_ in UNIQUE_SYMBOLS],
                                  value=UNIQUE_SYMBOLS[0] if UNIQUE_SYMBOLS else None,
                                  style={'min-width': '100px', 'flex': '1 1 auto'}),
                     dbc.Button("►", id="next-btn", n_clicks=0, color="secondary", outline=True),
                 ], size="sm")], width=3),
        dbc.Col([_lbl("Expiry"),
                 dcc.Dropdown(id='expiry-dropdown', placeholder="Select Expiry", style={'fontSize': '0.9rem'})],
                width=2),
        dbc.Col([_lbl("Days"),
                 dcc.Dropdown(id='dropdown_n_days',
                              options=[{'label': f"{d} Days", 'value': d} for d in [7, 15, 30, 60, 90, 120, 150]],
                              value=60, multi=False, clearable=False)], width=1),
        dbc.Col([_lbl("MAX PAIN"),
                 html.Div(id="max-pain-display", className="text-primary fw-bold", style={'fontSize': '1.0rem'})],
                width=1, className="text-center"),
        dbc.Col(html.Div(id="underlying-price-display", className="text-secondary",
                         style={'fontSize': '0.75rem'}), width=3),
        dbc.Col(dbc.Button("🔔 Create Alert", id="btn-create-alert", size="sm", color="primary", outline=True,
                           className="fw-bold"), width=1, className="text-end"),
    ], className="g-2 align-items-center"), className="p-1"), className="shadow-sm border-0 mb-1")


def build_stream_controls():
    """Stream / Stop / Record / Interval buttons."""
    return html.Div([
        dbc.ButtonGroup([dbc.Button("▶ Stream", id="start-stream-btn", size="sm", color="success"),
                         dbc.Button("■ Stop", id="stop-stream-btn", size="sm", color="danger")]),
        dbc.Button("● Record", id="start-recording-btn", size="sm", color="info", outline=True, className="ms-1"),
        dbc.InputGroup([
            dbc.InputGroupText("Interval"),
            dbc.Button("-", id="interval-dec-btn", size="sm", color="secondary", outline=True),
            dbc.Input(id="interval-input", type="number", value=3, min=1, step=1,
                      style={'textAlign': 'center', 'maxWidth': '52px', 'padding': '0'}),
            dbc.Button("+", id="interval-inc-btn", size="sm", color="secondary", outline=True),
        ], size="sm", className="ms-2", style={"width": "auto", "flexWrap": "nowrap"}),
    ], className="d-flex align-items-center")


def build_merged_panel(row="r1", with_controls=True):
    """Option chain + streaming buy/sell quantities in ONE table (rendered by update_merged_table)."""
    return _panel("Option Chain + Streaming Buy/Sell Qty", [
        html.Div(id='merged-table-container'),
        dcc.Interval(id='stream-interval', interval=3000, n_intervals=0, disabled=True),
    ], row, right=build_stream_controls() if with_controls else None)


def _hrow(frac, extra_px=0):
    """Height of a support-graph row = `frac` of the Row-1 right-panel body (the two 4px grid gaps removed first)."""
    return f"calc(({_h('r1', 8)} - 8px) * {frac} - {extra_px}px)"


CHAIN_TABLE_FRAC = 0.56   # 'Show Option Chain' view: share of the card used by the table (rest: OI Change | Snapshot)
STRIKE_BOX_PX = 80        # Trend Analytics: height of the CE / PE strike check-box area


def _cell(title, body, height, title_cls="text-primary", right=None, scroll=False):
    """Mini card for the right panel. `height` = the cell's total CSS height."""
    head = html.Div([html.Span(title, className=f"fw-bold {title_cls}", style={"fontSize": "0.72rem"}),
                     right if right is not None else ""],
                    className="d-flex justify-content-between align-items-center px-1",
                    style={"height": "22px", "background": "#f8f9fa", "borderBottom": "1px solid #dee2e6"})
    return html.Div([head, html.Div(body, style={"height": f"calc({height} - 24px)",
                                                 "overflow": "auto" if scroll else "hidden"})],
                    className="border rounded bg-white", style={"height": height, "overflow": "hidden", "minWidth": 0})


def build_right_panel():
    """Row-1 right column (40 %). Header switch: 'Show Option Chain' | 'Show Support Graphs' (default).
    Support Graphs = 3 rows of 40 % / 40 % / 20 %: CE Graph | IV Graph, PE Graph | Put/Call price, blank news row.
    Option Chain = the merged table on top, OI Change (tabs) | Index Snapshot underneath."""
    ts = _tab_styles()
    strike_row = {"whiteSpace": "nowrap", "overflowX": "auto", "overflowY": "hidden", "height": "44px",
                  "paddingTop": "4px"}
    lbl = {'margin-right': '10px', 'cursor': 'pointer', 'fontSize': '0.75rem'}
    h_top, h_news = _hrow(0.4), _hrow(0.2)
    g_strike = _hrow(0.4, 24 + 44)
    g_plain = _hrow(0.4, 24)

    ce = _cell("CE Graph", [
        html.Div(dcc.RadioItems(id='ce-strike-radio', inline=True, labelStyle=lbl), style=strike_row),
        dcc.Graph(id='ce-candle-graph', style={'height': g_strike})], h_top, title_cls="text-success")
    iv = _cell("IV Graph", dcc.Graph(id='iv-graph', style={'height': g_plain}), h_top,
               right=dbc.Checklist(options=[{"label": "Excl. ITM", "value": True}], value=[True],
                                   id="exclude-itm-toggle", switch=True, inline=True,
                                   className="text-muted mb-0", style={"fontSize": "0.65rem"}))
    pe = _cell("PE Graph", [
        html.Div(dcc.RadioItems(id='pe-strike-radio', inline=True, labelStyle=lbl), style=strike_row),
        dcc.Graph(id='pe-candle-graph', style={'height': g_strike})], h_top, title_cls="text-danger")
    putcall = _cell("Put / Call Price (10 OTM)", dcc.Graph(id='otm-prices-graph', style={'height': g_plain}), h_top)
    news = html.Div(id="news-panel", className="border rounded",      # intentionally blank: reserved for a news feed
                    style={"gridColumn": "1 / span 2", "height": h_news, "borderStyle": "dashed",
                           "borderColor": "#ced4da", "overflow": "auto"})

    toggle = dbc.RadioItems(
        id="right-panel-mode", value="graphs",
        options=[{"label": "Show Option Chain", "value": "chain"},
                 {"label": "Show Support Graphs", "value": "graphs"}],
        className="btn-group", inputClassName="btn-check",
        labelClassName="btn btn-outline-primary btn-sm py-0 px-2", labelCheckedClassName="active")

    graphs_view = html.Div([ce, iv, pe, putcall, news], id="support-graphs-view",
                           style={"display": "grid", "gridTemplateColumns": "minmax(0, 7fr) minmax(0, 3fr)",
                                  "gridTemplateRows": f"{h_top} {h_top} {h_news}", "gap": "4px",
                                  "height": _h('r1', 8)})

    h_tbl = _hrow(CHAIN_TABLE_FRAC)
    h_low = _hrow(1 - CHAIN_TABLE_FRAC)
    g_tab = f"calc({h_low} - 24px - 34px)"
    oi_tabs = dbc.Tabs([
        dbc.Tab(label="OI Chg", tab_id="c-oichg", children=[dcc.Graph(id='oi-change-graph', style={'height': g_tab})], **ts),
        dbc.Tab(label="PCR", tab_id="c-pcr", children=[dcc.Graph(id='pcr-graph', style={'height': g_tab})], **ts),
        dbc.Tab(label="OI", tab_id="c-oi", children=[dcc.Graph(id='oi-graph', style={'height': g_tab})], **ts),
        dbc.Tab(label="Vol", tab_id="c-vol", children=[dcc.Graph(id='volume-graph', style={'height': g_tab})], **ts),
    ], id="compact-oi-tabs", active_tab="c-oichg", style={"borderBottom": "1px solid #dee2e6"})
    oi = _cell("OI Change", oi_tabs, h_low)
    snap = _cell("Index Snapshot", html.Div(id="snapshot-display", className="small p-1"), h_low, scroll=True)
    chain_view = html.Div([
        html.Div(id='merged-table-container', style={"height": h_tbl, "overflow": "hidden"}),
        html.Div([oi, snap], style={"display": "grid", "gridTemplateColumns": "minmax(0, 11fr) minmax(0, 9fr)",
                                    "gap": "4px", "height": h_low, "marginTop": "4px"}),
    ], id="option-chain-view", className="d-none")
    return _panel("Options", [graphs_view, chain_view,
                              dcc.Interval(id='stream-interval', interval=3000, n_intervals=0, disabled=True)],
                  'r1', right=toggle)


def build_compact_layout():
    ts = _tab_styles()

    # ---------- ROW 1 : Index Analysis 60% (full screen height) | Options card 40% ----------
    stock = _panel("Index Analysis",
                   dcc.Graph(id='graph_31', config={'scrollZoom': True}, style={'height': _h('r1', 12)}), 'r1',
                   right=html.Div([build_sr_control(), build_stream_controls()],
                                  className="d-flex align-items-center gap-3"))
    row1 = dbc.Row([_col(stock, 60), _col(build_right_panel(), 40)], className="g-0 mb-1 flex-nowrap")

    # ---------- ROW 2 (below the fold) : Trend Analytics Workspace, full width ----------
    g3 = _h('r2', 4 + STRIKE_BOX_PX + 34)
    chk = {'margin-right': '12px', 'cursor': 'pointer', 'fontSize': '0.78rem'}
    strike_box = html.Div([
        html.Div([html.Span("CE", className="fw-bold text-success small me-2", style={"minWidth": "24px"}),
                  html.Div(dcc.Checklist(id='trend-ce-strikes', inline=True, labelStyle=chk),
                           style={"flex": "1 1 auto"})], className="d-flex align-items-start"),
        html.Div([html.Span("PE", className="fw-bold text-danger small me-2", style={"minWidth": "24px"}),
                  html.Div(dcc.Checklist(id='trend-pe-strikes', inline=True, labelStyle=chk),
                           style={"flex": "1 1 auto"})], className="d-flex align-items-start mt-1"),
    ], className="bg-light rounded border px-2 py-1 mb-1", style={"height": f"{STRIKE_BOX_PX}px", "overflowY": "auto"})
    trend_body = [
        strike_box,
        dbc.Tabs([
            dbc.Tab(label="OI Trend", tab_id="tab-trend-oi",
                    children=[dcc.Graph(id='oi-trend-graph', style={'height': g3})], **ts),
            dbc.Tab(label="Buy/Sell Qty Trend", tab_id="tab-trend-buysell",
                    children=[dcc.Graph(id='buysell-trend-graph', style={'height': g3})], **ts),
        ], id="trend-workspace-tabs", active_tab="tab-trend-oi", style={"borderBottom": "1px solid #dee2e6"}),
    ]
    popout = dbc.Button("⧉ Pop out", id="btn-popout-trend", size="sm", color="info", outline=True,
                        className="py-0 px-2",
                        title="Open the graph of the active tab in its own window (drag it to your second monitor)")
    trend = _panel("Trend Analytics Workspace", trend_body, 'r2', title_cls="text-info", right=popout)
    row2 = dbc.Row([_col(trend, 100)], className="g-0 mb-1")

    return [build_compact_controls(), row1, row2]


# =====================================================================================
# 8. CALLBACKS
# =====================================================================================


# ---- Header +/- row-height buttons (client-side, no server round trip) ----
app.clientside_callback(
    """
    function(r1d, r1i, r2d, r2i, reset, data) {
        const ctx = window.dash_clientside.callback_context;
        const trig = (ctx.triggered && ctx.triggered.length) ? ctx.triggered[0].prop_id.split('.')[0] : '';
        const defaults = {r1: 100, r2: 40, r3: 26};
        let h = Object.assign({}, defaults, data || {});
        const step = 2, lo = 12, hi = 70, hi1 = 150;
        if (trig === 'rh-reset') { h = Object.assign({}, defaults); }
        else if (/^r[123]-(inc|dec)$/.test(trig)) {
            const key = trig.slice(0, 2), dir = trig.slice(3);
            if (h[key] !== undefined) { h[key] = Math.max(lo, Math.min(key === 'r1' ? hi1 : hi, h[key] + (dir === 'inc' ? step : -step))); }
        }
        return h;
    }
    """,
    Output('row-heights-store-v3', 'data'),
    [Input('r1-dec', 'n_clicks'), Input('r1-inc', 'n_clicks'),
     Input('r2-dec', 'n_clicks'), Input('r2-inc', 'n_clicks'),
     Input('rh-reset', 'n_clicks')],
    [State('row-heights-store-v3', 'data')],
    prevent_initial_call=True
)

# ---- Apply stored heights to the page + make Plotly graphs re-fit ----
app.clientside_callback(
    """
    function(data) {
        const h = Object.assign({r1: 100, r2: 40, r3: 26}, data || {});
        const root = document.documentElement;
        ['r1', 'r2', 'r3'].forEach(k => root.style.setProperty('--' + k + '-h', h[k] + 'vh'));
        setTimeout(() => window.dispatchEvent(new Event('resize')), 100);
        return window.dash_clientside.no_update;
    }
    """,
    Output('rh-dummy', 'data'),
    Input('row-heights-store-v3', 'data')
)

# ---- Row 1 right panel: 'Show Option Chain' / 'Show Support Graphs' (client-side) ----
app.clientside_callback(
    """
    function(mode) {
        setTimeout(() => window.dispatchEvent(new Event('resize')), 120);
        return mode === 'chain' ? ['d-none', ''] : ['', 'd-none'];
    }
    """,
    [Output('support-graphs-view', 'className'), Output('option-chain-view', 'className')],
    Input('right-panel-mode', 'value')
)

# ---- Trend Analytics Workspace: pop the active graph out into its own window ----
app.clientside_callback(
    """
    function(n, tab) {
        if (!n) { return window.dash_clientside.no_update; }
        const name = (tab === 'tab-trend-buysell') ? 'buysell-trend' : 'oi-trend';
        window.open('/popout/' + name, 'popout_' + name, 'popup=yes,width=1200,height=760,left=60,top=60');
        return window.dash_clientside.no_update;
    }
    """,
    Output('popout-dummy', 'data'),
    Input('btn-popout-trend', 'n_clicks'),
    State('trend-workspace-tabs', 'active_tab'),
    prevent_initial_call=True
)


# ---- Settings drawer: header button toggles it; the banner button opens it on the 'Access Tokens' accordion ----
@app.callback(
    [Output("settings-offcanvas", "is_open"), Output("settings-accordion", "active_item")],
    [Input("btn-open-settings", "n_clicks"), Input("btn-banner-open-tokens", "n_clicks")],
    [State("settings-offcanvas", "is_open")],
    prevent_initial_call=True
)
def toggle_settings_drawer(n_open, n_banner, is_open):
    # Act only on a real click (a freshly created button reports n_clicks=None and must not toggle the drawer).
    trig = dash.callback_context.triggered_id
    if trig == "btn-banner-open-tokens" and n_banner:
        return True, "tokens"
    if trig == "btn-open-settings" and n_open:
        return (not is_open), dash.no_update
    raise dash.exceptions.PreventUpdate


# ---- Access tokens: check today's token files in GCS on load, every 5 min, and after each generation ----
@app.callback(
    [Output("token-expiry-banner", "is_open"), Output("token-expiry-text", "children"),
     Output("token-expiry-banner", "color"), Output("token-status-detail", "children")],
    [Input("token-check-interval", "n_intervals"), Input("token-gen-trigger", "data")]
)
def check_access_tokens(_n, _generated):
    today = datetime.datetime.now().date()
    status = {b: _token_exists(b) for b in ("upstox", "zerodha")}

    for broker in ("upstox", "zerodha"):          # a token generated elsewhere is picked up without a restart
        if status[broker]:
            fresh = get_broker_token_from_gcs(broker)
            current = DEFAULT_TOKEN if broker == "upstox" else KITE_ACCESS_TOKEN
            if fresh and fresh != current:
                _apply_token(broker, fresh)

    detail = []
    for broker in ("upstox", "zerodha"):
        icon, colour, note = {True: ("✔", "text-success", f"today's token found ({broker}/{today}.json)"),
                              False: ("✖", "text-danger", f"no token for {today} - expired / not generated"),
                              None: ("?", "text-warning", "could not check (GCS not reachable)")}[status[broker]]
        detail.append(html.Div([html.Span(f"{icon} {_BROKER_LABEL[broker]}: ", className=f"fw-bold {colour}"),
                                html.Span(note),
                                html.Span(f"  [{_BROKER_NEEDED_FOR[broker]}]", className="text-muted")]))

    missing = [_BROKER_LABEL[b] for b in status if status[b] is False]
    unknown = [_BROKER_LABEL[b] for b in status if status[b] is None]
    if missing:
        text = (f"⚠ Access token expired: no {' / '.join(missing)} token for {today} in gs://{TOKEN_BUCKET}. "
                f"Generate a new one before using " + "; ".join(_BROKER_NEEDED_FOR[b.lower()] for b in missing) + ".")
        return True, text, "danger", detail
    if unknown:
        return True, f"Could not check the {' / '.join(unknown)} access token in GCS.", "warning", detail
    return False, "", "danger", detail


@app.callback(
    [Output("token-gen-status", "children"), Output("token-gen-trigger", "data")],
    [Input("btn-gen-upstox-token", "n_clicks"), Input("btn-gen-zerodha-token", "n_clicks")],
    prevent_initial_call=True,
    running=[(Output("btn-gen-upstox-token", "disabled"), True, False),
             (Output("btn-gen-zerodha-token", "disabled"), True, False)]
)
def generate_access_token(n_upstox, n_zerodha):
    trig = dash.callback_context.triggered[0]["prop_id"].split(".")[0]
    broker = "upstox" if trig == "btn-gen-upstox-token" else "zerodha"
    label = _BROKER_LABEL[broker]
    started = time.time()
    try:
        if broker == "upstox":
            module = _load_script_module(UPSTOX_TOKEN_SCRIPT, "upstox_token_script")
            token = module.auto_login(storage_mode="both", gcs_bucket_name=TOKEN_BUCKET)
        else:
            module = _load_script_module(ZERODHA_TOKEN_SCRIPT, "zerodha_token_script")
            kite = module.broker_login()
            token = getattr(kite, "access_token", None) if kite is not None else None
    except Exception as e:
        logger.warning(f"❌ {label} token generation failed: {e}")
        return dbc.Alert(f"✖ {label} token failed: {str(e)[:220]}", color="danger", className="py-1 px-2 mb-0"), time.time()
    if not token:
        return dbc.Alert(f"✖ {label} login did not return a token - see the console for the reason.",
                         color="danger", className="py-1 px-2 mb-0"), time.time()
    _apply_token(broker, token)
    return dbc.Alert(f"✔ {label} token generated, saved to GCS and active ({time.time() - started:.0f}s).",
                     color="success", className="py-1 px-2 mb-0"), time.time()


# ---- Landscape (compact) / portrait layout switch ----
@app.callback(
    Output("dynamic-content-layout", "children"),
    [Input("layout-toggle-switch", "value")]
)
def render_dynamic_layout(portrait_toggle):
    if portrait_toggle and True in portrait_toggle:
        # --- PORTRAIT MODE: 4 ROWS ---
        return [
            dbc.Row([dbc.Col(equity_hub_card, width=12, className="mb-4")]),
            dbc.Row([
                dbc.Col(dbc.Card([
                    dbc.CardHeader(html.H6("IV Graph", className="m-0 fw-bold text-primary")),
                    dbc.CardBody(iv_graph_element)
                ], className="shadow-sm border-0 h-100"), width=4, className="mb-4"),
                dbc.Col(dbc.Card([
                    dbc.CardHeader(html.H6("10 OTM Prices Graph", className="m-0 fw-bold text-primary")),
                    dbc.CardBody(otm_prices_element)
                ], className="shadow-sm border-0 h-100"), width=4, className="mb-4"),
                dbc.Col(dbc.Card([
                    dbc.CardHeader(html.H6("Index Snapshot", className="m-0 fw-bold text-primary")),
                    dbc.CardBody([html.Div(id="snapshot-display", className="small"),
                                  html.Div("Settings, tokens and alerts: ⚙ Settings (top bar).",
                                           className="small text-muted mt-2")],
                                 style={"overflow": "auto"})
                ], className="shadow-sm border-0 h-100"), width=4, className="mb-4")
            ], className="align-items-stretch"),
            dbc.Row([dbc.Col(build_merged_panel('r1'), width=12, className="mb-4")]),
            dbc.Row([dbc.Col(trend_analytics_card, width=12, className="mb-4")], className="align-items-stretch")
        ]
    # --- COMPACT LANDSCAPE LAYOUT (DEFAULT, one screen on a 24" monitor) ---
    return build_compact_layout()


# ---- Trend graphs: height slider (portrait layout) ----
@app.callback(
    [Output('oi-trend-graph', 'style'), Output('buysell-trend-graph', 'style')],
    [Input('trend-height-slider', 'value')]
)
def update_card_graph_heights(slider_value):
    style = {'height': f'{slider_value}px'}
    return style, style


@app.callback(
    [Output('symbol-dropdown', 'options'), Output('symbol-dropdown', 'value')],
    [Input('scanner-dropdown', 'value'),
     Input('shortlist-table', 'data'),
     Input('prev-btn', 'n_clicks'),
     Input('next-btn', 'n_clicks')],
    [State('symbol-dropdown', 'value')]
)
def update_and_navigate_symbols(scanner_val, shortlist_data, prev_clicks, next_clicks, current_symbol):
    ctx = dash.callback_context
    triggered_id = ctx.triggered[0]['prop_id'].split('.')[0] if ctx.triggered else None

    if scanner_val == 'Shortlisted':
        valid_symbols = sorted({row['Symbol'] for row in (shortlist_data or []) if row.get('Symbol') in UNIQUE_SYMBOLS})
        if not valid_symbols:
            valid_symbols = UNIQUE_SYMBOLS
    else:
        valid_symbols = UNIQUE_SYMBOLS

    options = [{'label': s, 'value': s} for s in valid_symbols]
    if not valid_symbols:
        return [], None

    new_symbol = current_symbol if current_symbol in valid_symbols else valid_symbols[0]
    if (triggered_id == 'prev-btn' and prev_clicks) or (triggered_id == 'next-btn' and next_clicks):
        idx = valid_symbols.index(new_symbol)
        if triggered_id == 'next-btn':
            new_symbol = valid_symbols[(idx + 1) % len(valid_symbols)]
        else:
            new_symbol = valid_symbols[(idx - 1) % len(valid_symbols)]
    return options, new_symbol


@app.callback(
    [Output('expiry-dropdown', 'options'), Output('expiry-dropdown', 'value'), Output('underlying-info-store', 'data')],
    [Input('symbol-dropdown', 'value')]
)
def update_symbol_expiries(symbol):
    if not symbol or INSTRUMENTS_DF.empty:
        return [], None, None
    derivs = INSTRUMENTS_DF[(INSTRUMENTS_DF['instrument_type'] == 'OPTIDX') & (INSTRUMENTS_DF['name'] == symbol)]
    if derivs.empty:
        return [], None, None
    all_exp = sorted({str(e) for e in derivs['expiry'].dropna().unique()})
    today = str(datetime.date.today())
    expiries = [e for e in all_exp if e >= today] or all_exp         # nearest upcoming expiry first
    options = [{'label': e, 'value': e} for e in expiries]
    default_expiry = expiries[0] if expiries else None
    return options, default_expiry, {'symbol': symbol, 'instrument_key': get_index_key(symbol)}


@app.callback(
    [Output('ce-strike-radio', 'options'), Output('ce-strike-radio', 'value'), Output('pe-strike-radio', 'options'),
     Output('pe-strike-radio', 'value'), Output('option-chain-store', 'data'),
     Output('underlying-info-store', 'data', allow_duplicate=True),
     Output('trend-ce-strikes', 'options'), Output('trend-pe-strikes', 'options')],
    [Input('expiry-dropdown', 'value'), Input('stream-interval', 'n_intervals'), Input('start-stream-btn', 'n_clicks'),
     Input('start-recording-btn', 'n_clicks')],
    [State('underlying-info-store', 'data'), State('stream-interval', 'disabled')],
    prevent_initial_call=True
)
def update_option_chain_data(expiry, n, start_stream, start_rec, underlying_info, stream_disabled):
    ctx = dash.callback_context
    triggered_id = ctx.triggered[0]['prop_id'].split('.')[0] if ctx.triggered else None

    if triggered_id == 'stream-interval' and stream_disabled:
        raise dash.exceptions.PreventUpdate

    if not expiry or not underlying_info or not underlying_info.get('instrument_key'):
        return [], None, [], None, None, underlying_info, [], []

    chain_data = fetch_option_chain(underlying_info['instrument_key'], expiry)
    strikes = sorted(list(set([d['strike_price'] for d in chain_data])))
    ce_options = [{'label': str(s), 'value': s} for s in strikes]
    pe_options = [{'label': str(s), 'value': s} for s in strikes]
    underlying_price = chain_data[0].get('underlying_spot_price', 0) if chain_data else 0

    if triggered_id == 'expiry-dropdown':
        atm_strike = min(strikes, key=lambda x: abs(x - underlying_price)) if strikes else None
    else:
        atm_strike = dash.no_update

    updated_info = underlying_info.copy()
    updated_info.update({'price': underlying_price, 'expiry': expiry})
    return ce_options, atm_strike, pe_options, atm_strike, chain_data, updated_info, ce_options, pe_options


def _option_candle_figure(option_type, strike, underlying_info, chain_data):
    """Daily candles + volume + OI of one option contract (option_type 'CE' | 'PE'); history and the live quote are
    merged into ONE row per day by fetch_daily_with_live()."""
    if not strike or not underlying_info or not chain_data:
        return go.Figure()
    opt_key = 'call_options' if option_type == 'CE' else 'put_options'
    key = next((item.get(opt_key, {}).get('instrument_key') for item in chain_data
                if item['strike_price'] == strike), None)
    if not key:
        return go.Figure()
    symbol = underlying_info['symbol']
    lot_size = 1
    try:
        m = INSTRUMENTS_DF[INSTRUMENTS_DF['instrument_key'] == key]
        if not m.empty:
            lot_size = int(m.iloc[0].get('lot_size', 1))
    except (ValueError, TypeError):
        lot_size = 1

    df = fetch_daily_with_live(key)
    if df.empty:
        return go.Figure()

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0, row_heights=[0.5, 0.25, 0.25])
    fig.add_trace(go.Candlestick(x=df['timestamp'], open=df['open'], high=df['high'], low=df['low'], close=df['close']),
                  row=1, col=1)
    fig.add_trace(go.Bar(x=df['timestamp'], y=df['volume']), row=2, col=1)
    fig.add_trace(go.Scatter(x=df['timestamp'], y=df['oi']), row=3, col=1)
    fig.update_layout(plot_bgcolor='white', paper_bgcolor='white', xaxis_rangeslider_visible=False, showlegend=False)
    fig.update_xaxes(showline=True, linewidth=1, linecolor='black', mirror=True, rangebreaks=[dict(bounds=["sat", "mon"])])
    fig.update_yaxes(showline=True, linewidth=1, linecolor='black', mirror=True)

    latest_price = df['close'].iloc[-1]
    fig.add_hline(y=latest_price, line_dash="dot", line_color="royalblue",
                  annotation_text=f"₹{latest_price * lot_size:,.2f}", annotation_position="right", row=1, col=1)
    return _compact_fig(fig, right=62, title=f"{option_type} {symbol} {strike:g} | Lot {lot_size}", legend=False)


def _skip_idle_stream_tick(stream_disabled):
    """Interval ticks are ignored while streaming is off."""
    if dash.callback_context.triggered_id == 'stream-interval' and stream_disabled:
        raise dash.exceptions.PreventUpdate


@app.callback(
    Output('ce-candle-graph', 'figure'),
    [Input('ce-strike-radio', 'value'), Input('stream-interval', 'n_intervals')],
    [State('underlying-info-store', 'data'), State('option-chain-store', 'data'), State('stream-interval', 'disabled')]
)
def update_ce_graph(strike, n, underlying_info, chain_data, stream_disabled):
    _skip_idle_stream_tick(stream_disabled)
    return _option_candle_figure('CE', strike, underlying_info, chain_data)


@app.callback(
    Output('pe-candle-graph', 'figure'),
    [Input('pe-strike-radio', 'value'), Input('stream-interval', 'n_intervals')],
    [State('underlying-info-store', 'data'), State('option-chain-store', 'data'), State('stream-interval', 'disabled')]
)
def update_pe_graph(strike, n, underlying_info, chain_data, stream_disabled):
    _skip_idle_stream_tick(stream_disabled)
    return _option_candle_figure('PE', strike, underlying_info, chain_data)


@app.callback(
    Output('iv-graph', 'figure'),
    [Input('option-chain-store', 'data'), Input('exclude-itm-toggle', 'value')],
    [State('underlying-info-store', 'data')]
)
def update_iv_graph(chain_data, exclude_itm_val, underlying_info):
    if not chain_data or not underlying_info:
        return go.Figure()
    price = underlying_info.get('price', 0)
    exclude_itm = True if exclude_itm_val and True in exclude_itm_val else False

    iv_data = []
    for item in chain_data:
        strike = item['strike_price']
        if price > 0 and (strike < price * 0.5 or strike > price * 1.5):
            continue
        call_greeks = item.get('call_options', {}).get('option_greeks')
        put_greeks = item.get('put_options', {}).get('option_greeks')
        call_iv = call_greeks.get('iv') if call_greeks else 0
        put_iv = put_greeks.get('iv') if put_greeks else 0
        if exclude_itm and price > 0:
            if strike < price:      # Call is ITM
                call_iv = None
            if strike > price:      # Put is ITM
                put_iv = None
        if (call_iv and call_iv > 0) or (put_iv and put_iv > 0):
            iv_data.append({'strike': strike,
                            'call_iv': call_iv if (call_iv and call_iv > 0) else None,
                            'put_iv': put_iv if (put_iv and put_iv > 0) else None})

    if not iv_data:
        return go.Figure(layout={'title': 'No IV data available'})

    df = pd.DataFrame(iv_data).sort_values('strike')
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df['strike'], y=df['call_iv'], mode='lines+markers', name='Call IV',
                             line=dict(color='red'), connectgaps=True))
    fig.add_trace(go.Scatter(x=df['strike'], y=df['put_iv'], mode='lines+markers', name='Put IV',
                             line=dict(color='green'), connectgaps=True))
    fig.update_layout(title="IV of Put and Call", plot_bgcolor='white', paper_bgcolor='white',
                      margin=dict(t=30, b=20, l=20, r=20))
    if price > 0:
        fig.add_vline(x=price, line_dash="dot", line_color="black", annotation_text="Spot",
                      annotation_position="top left")
    fig.update_xaxes(showline=True, linewidth=1, linecolor='black', gridcolor='lightgrey')
    fig.update_yaxes(showline=True, linewidth=1, linecolor='black', gridcolor='lightgrey')
    return _compact_fig(fig, title="IV – Put & Call")


@app.callback(
    [Output('pcr-graph', 'figure'), Output('oi-graph', 'figure'), Output('oi-change-graph', 'figure'),
     Output('volume-graph', 'figure')],
    [Input('option-chain-store', 'data')],
    [State('underlying-info-store', 'data')]
)
def update_metric_graphs(chain_data, underlying_info):
    if not chain_data or not underlying_info:
        return [go.Figure()] * 4
    price = underlying_info['price']
    metrics = []
    for item in chain_data:
        ce_md = item.get('call_options', {}).get('market_data', {})
        pe_md = item.get('put_options', {}).get('market_data', {})
        ce_oi, pe_oi = ce_md.get('oi', 0), pe_md.get('oi', 0)
        metrics.append({
            'strike': item['strike_price'],
            'pcr': pe_oi / ce_oi if ce_oi > 0 else 0,
            'ce_oi': ce_oi, 'pe_oi': pe_oi,
            'ce_oi_change': ce_oi - ce_md.get('prev_oi', 0),
            'pe_oi_change': pe_oi - pe_md.get('prev_oi', 0),
            'ce_vol': ce_md.get('volume', 0), 'pe_vol': pe_md.get('volume', 0)
        })
    df = pd.DataFrame(metrics).sort_values('strike')

    fig_pcr = go.Figure(go.Scatter(x=df['strike'], y=df['pcr'], mode='lines+markers'))
    fig_oi = go.Figure([go.Bar(x=df['strike'], y=df['ce_oi'], name='CE OI', marker_color='red'),
                        go.Bar(x=df['strike'], y=df['pe_oi'], name='PE OI', marker_color='green')])
    fig_oi_chg = go.Figure([go.Bar(x=df['strike'], y=df['ce_oi_change'], name='CE OI Chg', marker_color='red'),
                            go.Bar(x=df['strike'], y=df['pe_oi_change'], name='PE OI Chg', marker_color='green')])
    fig_vol = go.Figure([go.Bar(x=df['strike'], y=df['ce_vol'], name='CE Volume', marker_color='red'),
                         go.Bar(x=df['strike'], y=df['pe_vol'], name='PE Volume', marker_color='green')])

    for f, t in zip([fig_pcr, fig_oi, fig_oi_chg, fig_vol], ["PCR", "OI", "OI Change", "Volume"]):
        f.add_vline(x=price, line_dash="dash", line_color="black")
        f.update_layout(title=t, plot_bgcolor='white', paper_bgcolor='white',
                        barmode='group' if t in ["OI", "OI Change", "Volume"] else None)
        f.update_xaxes(showline=True, linewidth=1, linecolor='black', gridcolor='lightgrey')
        f.update_yaxes(showline=True, linewidth=1, linecolor='black', gridcolor='lightgrey')
    return tuple(_compact_fig(f_) for f_ in (fig_pcr, fig_oi, fig_oi_chg, fig_vol))


@app.callback(
    Output('snapshot-display', 'children'),
    [Input('option-chain-store', 'data')],
    [State('underlying-info-store', 'data')]
)
def update_snapshot(chain_data, underlying_info):
    """Index Snapshot (replaces the stock dashboard's Share Holding cell): chain-wide totals and key strikes."""
    if not chain_data or not underlying_info:
        return "N/A"
    full = _chain_to_frame(chain_data, None)
    ce_tot, pe_tot = full['ce_oi'].sum(), full['pe_oi'].sum()
    ce_chg, pe_chg = (full['ce_oi'] - full['ce_prev']).sum(), (full['pe_oi'] - full['pe_prev']).sum()
    price = underlying_info.get('price', 0)
    mp = _max_pain_strike(full)
    ce_wall = full.loc[full['ce_oi'].idxmax(), 'strike']
    pe_wall = full.loc[full['pe_oi'].idxmax(), 'strike']

    def sgn(v):
        return html.Span(f"{v:+,.0f}", style={'color': 'green' if v >= 0 else 'red'})

    rows = [
        ("Index / Expiry", f"{underlying_info.get('symbol')} | {underlying_info.get('expiry')}"),
        ("Spot", f"{price:,.2f}"),
        ("Max Pain", f"{mp:g}  ({price - mp:+,.1f})"),
        ("Total PCR", f"{pe_tot / ce_tot:.2f}" if ce_tot else "N/A"),
        ("Total CE OI", f"{ce_tot:,.0f}"),
        ("Total PE OI", f"{pe_tot:,.0f}"),
        ("CE OI change", sgn(ce_chg)),
        ("PE OI change", sgn(pe_chg)),
        ("Resistance (max CE OI)", f"{ce_wall:g}"),
        ("Support (max PE OI)", f"{pe_wall:g}"),
    ]
    return dbc.Table(html.Tbody([html.Tr([html.Td(k, className="text-muted"), html.Td(v, className="fw-bold")])
                                 for k, v in rows]),
                     size="sm", borderless=True, className="mb-0", style={"fontSize": "0.72rem"})


@app.callback(
    [Output('max-pain-display', 'children'), Output('underlying-price-display', 'children')],
    [Input('option-chain-store', 'data')],
    [State('underlying-info-store', 'data')]
)
def update_max_pain(chain_data, underlying_info):
    if not chain_data or not underlying_info:
        return "N/A", "Underlying: N/A"
    max_pain_strike = _max_pain_strike(_chain_to_frame(chain_data, None))
    price = underlying_info['price']
    diff = price - max_pain_strike
    arrow = "▲" if diff >= 0 else "▼"
    return html.Span(f"{max_pain_strike:g}"), html.Div([html.Span(f"Underlying: {price:.2f} "),
                                                        html.Span(f"({arrow} {abs(diff):.2f})",
                                                                  style={'color': "green" if diff >= 0 else "red"})])


@app.callback(
    Output('merged-table-container', 'children'),
    [Input('option-chain-store', 'data'), Input('stream-qty-store', 'data')],
    [State('underlying-info-store', 'data'), State('layout-toggle-switch', 'value')]
)
def update_merged_table(chain_data, stream_rows, underlying_info, portrait_toggle=None):
    """One table: option chain columns + streaming Buy/Sell quantities, joined on strike."""
    if not chain_data:
        return "No data"
    price = underlying_info.get('price', 0) if underlying_info else 0
    rows = []
    for item in chain_data:
        ce_md = item.get('call_options', {}).get('market_data', {})
        ce_g = item.get('call_options', {}).get('option_greeks', {})
        pe_md = item.get('put_options', {}).get('market_data', {})
        pe_g = item.get('put_options', {}).get('option_greeks', {})

        def oi_chg(md):
            return round(((md.get('oi', 0) - md.get('prev_oi', 0)) / md.get('prev_oi', 1)) * 100, 2) \
                if md.get('prev_oi', 0) else 0

        rows.append({'CE_OI': ce_md.get('oi'), 'CE_OI_Chg%': oi_chg(ce_md), 'CE_Delta': ce_g.get('delta'),
                     'CE_POP': ce_g.get('pop'), 'CE_LTP': ce_md.get('ltp'), 'Strike': item['strike_price'],
                     'PE_LTP': pe_md.get('ltp'), 'PE_POP': pe_g.get('pop'), 'PE_Delta': pe_g.get('delta'),
                     'PE_OI_Chg%': oi_chg(pe_md), 'PE_OI': pe_md.get('oi')})
    df = pd.DataFrame(rows).sort_values('Strike', ascending=False).reset_index(drop=True)
    max_ce_oi, max_pe_oi = df['CE_OI'].max(), df['PE_OI'].max()

    atm_idx = None
    if price > 0:
        for i in range(len(df) - 1):
            if df.iloc[i]['Strike'] >= price and df.iloc[i + 1]['Strike'] < price:
                atm_idx = i
                break

    qty_cols = ['CE_Buy', 'CE_Sell', 'PE_Buy', 'PE_Sell']
    eq_row = None
    for c in qty_cols:
        df[c] = None
    if stream_rows:
        sdf = pd.DataFrame(stream_rows)
        sdf['_k'] = pd.to_numeric(sdf['Strike'], errors='coerce')
        opt = sdf[sdf['_k'].notna()].rename(columns={'Call Buy Qty': 'CE_Buy', 'Call Sell Qty': 'CE_Sell',
                                                      'Put Buy Qty': 'PE_Buy', 'Put Sell Qty': 'PE_Sell'})
        opt = opt[['_k'] + qty_cols].drop_duplicates('_k')
        df = df.drop(columns=qty_cols).merge(opt, left_on='Strike', right_on='_k', how='left').drop(columns='_k')
        eq = sdf[sdf['_k'].isna()]
        if not eq.empty:
            e = eq.iloc[0]
            eq_row = {'Strike': e['Strike'], 'CE_Buy': e.get('Call Buy Qty'), 'CE_Sell': e.get('Call Sell Qty')}

    col_order = ['CE_OI', 'CE_OI_Chg%', 'CE_Delta', 'CE_POP', 'CE_LTP', 'CE_Buy', 'CE_Sell', 'Strike',
                 'PE_Buy', 'PE_Sell', 'PE_LTP', 'PE_POP', 'PE_Delta', 'PE_OI_Chg%', 'PE_OI']
    df = df[col_order]
    if eq_row:
        df = pd.concat([pd.DataFrame([eq_row]), df], ignore_index=True)[col_order]
    df = df.astype(object).where(pd.notnull(df), None)

    atm_style = []
    if atm_idx is not None:
        off = 1 if eq_row else 0
        atm_style = [{'if': {'row_index': atm_idx + off}, 'borderBottom': '5px solid black'},
                     {'if': {'row_index': atm_idx + off + 1}, 'borderTop': '5px solid black'}]

    ce_cols = ['CE_OI', 'CE_OI_Chg%', 'CE_Delta', 'CE_POP', 'CE_LTP']
    pe_cols = ['PE_OI', 'PE_OI_Chg%', 'PE_Delta', 'PE_POP', 'PE_LTP']
    names = {'CE_OI': 'CE OI', 'CE_OI_Chg%': 'CE OI Chg%', 'CE_Delta': 'CE Delta', 'CE_POP': 'CE POP',
             'CE_LTP': 'CE LTP', 'CE_Buy': 'CE Buy Qty', 'CE_Sell': 'CE Sell Qty', 'Strike': 'Strike',
             'PE_Buy': 'PE Buy Qty', 'PE_Sell': 'PE Sell Qty', 'PE_LTP': 'PE LTP', 'PE_POP': 'PE POP',
             'PE_Delta': 'PE Delta', 'PE_OI_Chg%': 'PE OI Chg%', 'PE_OI': 'PE OI'}
    return dash_table.DataTable(
        data=df.to_dict('records'),
        columns=[{'name': names[c], 'id': c} for c in col_order],
        style_cell={'textAlign': 'center', 'border': '1px solid grey', 'fontSize': '10px',
                    'padding': '1px 2px', 'minWidth': 0},
        style_header={'fontWeight': 'bold', 'backgroundColor': 'lightgrey', 'fontSize': '9px',
                      'whiteSpace': 'normal', 'height': 'auto'},
        fixed_rows={'headers': True},
        style_table={'height': (_h('r1', 8) if (portrait_toggle and True in portrait_toggle)
                                else _hrow(CHAIN_TABLE_FRAC)), 'overflowY': 'auto', 'overflowX': 'auto'},
        style_data_conditional=[
            {'if': {'column_id': 'Strike'}, 'fontWeight': 'bold', 'backgroundColor': '#eee'},
            {'if': {'filter_query': f'{{CE_OI}} = {max_ce_oi}', 'column_id': ce_cols}, 'backgroundColor': '#FFCCCB'},
            {'if': {'filter_query': f'{{PE_OI}} = {max_pe_oi}', 'column_id': pe_cols}, 'backgroundColor': '#90EE90'},
            {'if': {'filter_query': '{CE_OI_Chg%} > 0', 'column_id': 'CE_OI_Chg%'}, 'color': 'green'},
            {'if': {'filter_query': '{CE_OI_Chg%} < 0', 'column_id': 'CE_OI_Chg%'}, 'color': 'red'},
            {'if': {'filter_query': '{PE_OI_Chg%} > 0', 'column_id': 'PE_OI_Chg%'}, 'color': 'green'},
            {'if': {'filter_query': '{PE_OI_Chg%} < 0', 'column_id': 'PE_OI_Chg%'}, 'color': 'red'},
            {'if': {'filter_query': '{CE_Buy} > {CE_Sell}', 'column_id': 'CE_Buy'}, 'backgroundColor': '#90EE90'},
            {'if': {'filter_query': '{PE_Buy} > {PE_Sell}', 'column_id': 'PE_Buy'}, 'backgroundColor': '#90EE90'},
        ] + atm_style,
        page_size=200)


@app.callback(
    [Output('interval-input', 'value'), Output('stream-interval', 'interval')],
    [Input('interval-inc-btn', 'n_clicks'), Input('interval-dec-btn', 'n_clicks'), Input('interval-input', 'value')],
    prevent_initial_call=True
)
def update_interval_settings(inc, dec, current_val):
    ctx = dash.callback_context
    if not ctx.triggered:
        return current_val, (current_val or 3) * 1000
    triggered_id = ctx.triggered[0]['prop_id'].split('.')[0]
    new_val = current_val if current_val is not None else 3
    if triggered_id == 'interval-inc-btn' and inc:
        new_val += 1
    elif triggered_id == 'interval-dec-btn' and dec:
        new_val = max(1, new_val - 1)
    return new_val, new_val * 1000


@app.callback(
    [Output('stream-interval', 'disabled'), Output('recording-active-store', 'data')],
    [Input('start-stream-btn', 'n_clicks'),
     Input('start-recording-btn', 'n_clicks'),
     Input('stop-stream-btn', 'n_clicks')],
    [State('stream-interval', 'disabled'), State('recording-active-store', 'data')],
    prevent_initial_call=True
)
def toggle_streaming(start_clicks, start_rec_clicks, stop_clicks, currently_disabled, currently_recording):
    ctx = dash.callback_context
    if not ctx.triggered:
        return currently_disabled, currently_recording
    button_id = ctx.triggered[0]['prop_id'].split('.')[0]
    # act only on a REAL click (n_clicks > 0); a freshly created button reports n_clicks=None
    if button_id == 'start-stream-btn' and start_clicks:
        return False, False
    elif button_id == 'start-recording-btn' and start_rec_clicks:
        return False, True
    elif button_id == 'stop-stream-btn' and stop_clicks:
        return True, False
    return currently_disabled, currently_recording


@app.callback(
    Output('stream-qty-store', 'data', allow_duplicate=True),
    [Input('option-chain-store', 'data')],
    [State('stream-interval', 'disabled'), State('underlying-info-store', 'data')],
    prevent_initial_call=True
)
def snapshot_quotes_when_not_streaming(chain_data, stream_disabled, underlying_info):
    """App load / index change / expiry change while NOT streaming: fetch the buy-sell quantities once, so the table,
    strike markers and index triangle are filled. While streaming, the interval callback below does the fetching."""
    if not stream_disabled or not chain_data or not underlying_info:
        raise dash.exceptions.PreventUpdate
    rows, _ = update_streaming_data(None, None, None, None, chain_data, underlying_info, False, [])
    if rows is dash.no_update:
        raise dash.exceptions.PreventUpdate
    return rows


@app.callback(
    [Output('stream-qty-store', 'data'), Output('recording-data-store', 'data')],
    [Input('stream-interval', 'n_intervals'), Input('symbol-dropdown', 'value'), Input('start-stream-btn', 'n_clicks'),
     Input('start-recording-btn', 'n_clicks')],
    [State('option-chain-store', 'data'),
     State('underlying-info-store', 'data'),
     State('recording-active-store', 'data'),
     State('recording-data-store', 'data')],
    prevent_initial_call=True
)
def update_streaming_data(n, symbol_val, start_stream, start_rec, chain_data, underlying_info, recording_active,
                          recorded_history):
    ctx = dash.callback_context
    triggered_id = ctx.triggered[0]['prop_id'].split('.')[0] if ctx.triggered else None

    if triggered_id == 'symbol-dropdown':
        return [], []
    if not chain_data or not underlying_info:
        return dash.no_update, recorded_history

    # An index has no order book of its own -> use the nearest index FUTURE for the buy/sell "EQ" row.
    fut_key = get_index_future_key(underlying_info['symbol'])
    keys_to_fetch = []
    if fut_key:
        keys_to_fetch.append(fut_key)
    strike_map = {}
    strikes = sorted(list(set([d['strike_price'] for d in chain_data])))
    for item in chain_data:
        ckey = item.get('call_options', {}).get('instrument_key')
        pkey = item.get('put_options', {}).get('instrument_key')
        if ckey:
            keys_to_fetch.append(ckey)
            strike_map[ckey] = (item['strike_price'], 'CE')
        if pkey:
            keys_to_fetch.append(pkey)
            strike_map[pkey] = (item['strike_price'], 'PE')

    quotes_data = fetch_market_quotes(keys_to_fetch)
    stream_rows = []

    q = _find_quote(quotes_data, fut_key) if fut_key else None
    if q:
        stream_rows.append({
            'Call Buy Qty': q.get('total_buy_quantity'),
            'Call Sell Qty': q.get('total_sell_quantity'),
            'Strike': f"EQ: {underlying_info['symbol']} FUT",
            'Put Buy Qty': 0, 'Put Sell Qty': 0
        })

    ckey_by_strike = {v[0]: k for k, v in strike_map.items() if v[1] == 'CE'}
    pkey_by_strike = {v[0]: k for k, v in strike_map.items() if v[1] == 'PE'}
    for s in strikes:
        row = {'Strike': s}
        ckey, pkey = ckey_by_strike.get(s), pkey_by_strike.get(s)
        cq = _find_quote(quotes_data, ckey) if ckey else None
        pq = _find_quote(quotes_data, pkey) if pkey else None
        row['Call Buy Qty'] = cq.get('total_buy_quantity', 0) if cq else 0
        row['Call Sell Qty'] = cq.get('total_sell_quantity', 0) if cq else 0
        row['Put Buy Qty'] = pq.get('total_buy_quantity', 0) if pq else 0
        row['Put Sell Qty'] = pq.get('total_sell_quantity', 0) if pq else 0
        stream_rows.append(row)

    options_rows = [r for r in stream_rows if not str(r['Strike']).startswith('EQ:')]
    eq_rows = [r for r in stream_rows if str(r['Strike']).startswith('EQ:')]
    df_options = pd.DataFrame(options_rows).sort_values('Strike', ascending=False)
    df_stream = pd.concat([pd.DataFrame(eq_rows), df_options], ignore_index=True)

    new_history = recorded_history
    if recording_active:
        timestamp = datetime.datetime.now().strftime('%H:%M:%S')
        oi_by_strike = {item['strike_price']: (item.get('call_options', {}).get('market_data', {}).get('oi', 0),
                                               item.get('put_options', {}).get('market_data', {}).get('oi', 0))
                        for item in chain_data}
        snapshot = []
        for row in stream_rows:
            is_strike = isinstance(row['Strike'], (int, float))
            coi, poi = oi_by_strike.get(row['Strike'], (0, 0)) if is_strike else (0, 0)
            snapshot.append({
                'timestamp': timestamp, 'Strike': row['Strike'],
                'Call Buy Qty': row.get('Call Buy Qty', 0), 'Call Sell Qty': row.get('Call Sell Qty', 0),
                'Put Buy Qty': row.get('Put Buy Qty', 0), 'Put Sell Qty': row.get('Put Sell Qty', 0),
                'Call OI': coi, 'Put OI': poi,
            })
        new_history.append(snapshot)

    return df_stream.to_dict('records'), new_history


def _history_frame(history):
    return pd.DataFrame([row for snapshot in history for row in snapshot])


@app.callback(
    Output('oi-trend-graph', 'figure'),
    [Input('recording-data-store', 'data'),
     Input('trend-ce-strikes', 'value'),
     Input('trend-pe-strikes', 'value')]
)
@_popout_stash("oi-trend")
def update_oi_trend(history, ce_strikes, pe_strikes):
    if not history or (not ce_strikes and not pe_strikes):
        return go.Figure(layout={'title': 'Waiting for recorded data and strike selection...'})
    df = _history_frame(history)
    fig = go.Figure()
    for s in ce_strikes or []:
        df_s = df[df['Strike'] == s]
        if not df_s.empty:
            fig.add_trace(go.Scatter(x=df_s['timestamp'], y=df_s['Call OI'], mode='lines+markers', name=f'CE {s} OI'))
    for s in pe_strikes or []:
        df_s = df[df['Strike'] == s]
        if not df_s.empty:
            fig.add_trace(go.Scatter(x=df_s['timestamp'], y=df_s['Put OI'], mode='lines+markers', name=f'PE {s} OI'))
    fig.update_layout(title="OI Trend (Recorded)", plot_bgcolor='white', paper_bgcolor='white', xaxis_title="Time",
                      yaxis_title="OI")
    fig.update_xaxes(showline=True, linewidth=1, linecolor='black', gridcolor='lightgrey')
    fig.update_yaxes(showline=True, linewidth=1, linecolor='black', gridcolor='lightgrey')
    return _compact_fig(fig)


@app.callback(
    Output('buysell-trend-graph', 'figure'),
    [Input('recording-data-store', 'data'),
     Input('trend-ce-strikes', 'value'),
     Input('trend-pe-strikes', 'value')]
)
@_popout_stash("buysell-trend")
def update_buysell_trend(history, ce_strikes, pe_strikes):
    if not history or (not ce_strikes and not pe_strikes):
        return go.Figure(layout={'title': 'Waiting for recorded data and strike selection...'})
    df = _history_frame(history)
    fig = go.Figure()
    for s in ce_strikes or []:
        df_s = df[df['Strike'] == s]
        if not df_s.empty:
            fig.add_trace(go.Scatter(x=df_s['timestamp'], y=df_s['Call Buy Qty'], mode='lines',
                                     name=f'CE {s} Buy Qty', line=dict(dash='solid')))
            fig.add_trace(go.Scatter(x=df_s['timestamp'], y=df_s['Call Sell Qty'], mode='lines',
                                     name=f'CE {s} Sell Qty', line=dict(dash='dot')))
    for s in pe_strikes or []:
        df_s = df[df['Strike'] == s]
        if not df_s.empty:
            fig.add_trace(go.Scatter(x=df_s['timestamp'], y=df_s['Put Buy Qty'], mode='lines',
                                     name=f'PE {s} Buy Qty', line=dict(dash='solid')))
            fig.add_trace(go.Scatter(x=df_s['timestamp'], y=df_s['Put Sell Qty'], mode='lines',
                                     name=f'PE {s} Sell Qty', line=dict(dash='dot')))
    fig.update_layout(title="Buy/Sell Qty Trend (Recorded)", plot_bgcolor='white', paper_bgcolor='white',
                      xaxis_title="Time", yaxis_title="Qty")
    fig.update_xaxes(showline=True, linewidth=1, linecolor='black', gridcolor='lightgrey')
    fig.update_yaxes(showline=True, linewidth=1, linecolor='black', gridcolor='lightgrey')
    return _compact_fig(fig)


# _____________________________________________________________________________________
# DISPLAY Graph
# _____________________________________________________________________________________
@app.callback(
    Output('graph_31', 'figure'),
    [Input('expiry-dropdown', 'value'),
     Input('symbol-dropdown', 'value'),
     Input('dropdown_n_days', 'value'),
     Input('input_short_SMA', 'value'),
     Input('input_medium_SMA', 'value'),
     Input('input_long_SMA', 'value'),
     Input('option-chain-store', 'data'),            # live chain -> OI profile on the price chart
     Input('stream-qty-store', 'data'),              # live buy/sell quantities -> markers
     Input('ma-lines-select', 'value'),              # which moving-average lines are shown
     Input('sr-levels-select', 'value'),             # support / resistance: D / W / M (multi-select)
     Input('tf-select', 'value')],                   # candle timeframe
    [State('underlying-info-store', 'data')])
def update_graph_31(dropdown_exp_value, dropdown_value, dropdown_n_days_value, short_sma, medium_sma, long_sma,
                    chain_data=None, stream_rows=None, ma_lines=None, sr_levels=None, tf='D', underlying_info=None):
    if not dropdown_value or not underlying_info or underlying_info.get('symbol') != dropdown_value:
        raise dash.exceptions.PreventUpdate
    key = underlying_info.get('instrument_key')
    if not key:
        return _empty_fig(f"{dropdown_value}: cash-index instrument key not found (add it to INDEX_KEY_MAP)")
    ma_periods = {k: max(1, int(p or d_)) for k, p, d_ in
                  (("short", short_sma, 5), ("medium", medium_sma, 20), ("long", long_sma, 50))}
    return build_timeframe_figure(dropdown_value, key, tf or "D", dropdown_n_days_value, ma_lines, ma_periods,
                                  sr_levels, chain_data, stream_rows, underlying_info, dropdown_exp_value)


@app.callback(
    Output('otm-prices-graph', 'figure'),
    [Input('option-chain-store', 'data')],
    [State('underlying-info-store', 'data')]
)
def update_otm_prices_graph(chain_data, underlying_info):
    if not chain_data or not underlying_info:
        return go.Figure()
    price = underlying_info.get('price', 0)
    otm_calls, otm_puts = [], []
    max_pain_strike = _max_pain_strike(_chain_to_frame(chain_data, None))

    for item in chain_data:
        strike = item['strike_price']
        ce_price = item.get('call_options', {}).get('market_data', {}).get('ltp', 0)
        pe_price = item.get('put_options', {}).get('market_data', {}).get('ltp', 0)
        if strike > price:      # OTM Call
            otm_calls.append({'strike': strike, 'price': ce_price, 'diff': strike - price})
        elif strike < price:    # OTM Put
            otm_puts.append({'strike': strike, 'price': pe_price, 'diff': price - strike})

    df_calls = pd.DataFrame(otm_calls, columns=['strike', 'price', 'diff']).sort_values('diff').head(10).sort_values('strike')
    df_puts = pd.DataFrame(otm_puts, columns=['strike', 'price', 'diff']).sort_values('diff').head(10).sort_values('strike')

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df_calls['strike'], y=df_calls['price'], mode='lines+markers', name='OTM Calls'))
    fig.add_trace(go.Scatter(x=df_puts['strike'], y=df_puts['price'], mode='lines+markers', name='OTM Puts'))
    if price > 0:
        fig.add_vline(x=price, line_dash="dash", line_color="green", annotation_text="LTP",
                      annotation_position="top left")
    fig.add_vline(x=max_pain_strike, line_dash="dash", line_color="red", annotation_text="Max Pain",
                  annotation_position="top right")
    fig.update_layout(title="10 OTM Call & Put Prices", xaxis_title="Strike", yaxis_title="LTP",
                      template="plotly_white", margin=dict(l=20, r=20, t=40, b=20))
    return _compact_fig(fig, title="10 OTM Call & Put LTP")


# --- SHORTLIST CALLBACKS ---

@app.callback(
    [Output("shortlist-modal", "is_open"),
     Output("modal-symbol-display", "children"),
     Output("modal-notes-input", "value")],
    [Input("btn-open-shortlist-modal", "n_clicks"),
     Input("btn-close-shortlist-modal", "n_clicks"),
     Input("btn-save-shortlist-modal", "n_clicks")],
    [State("shortlist-modal", "is_open"),
     State("symbol-dropdown", "value")],
    prevent_initial_call=True
)
def toggle_shortlist_modal(open_clicks, close_clicks, save_clicks, is_open, current_symbol):
    ctx = dash.callback_context
    if not ctx.triggered:
        return is_open, current_symbol, ""
    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
    if trigger_id == "btn-open-shortlist-modal":
        if open_clicks:
            return True, current_symbol, ""
        return is_open, dash.no_update, dash.no_update
    if (trigger_id == "btn-close-shortlist-modal" and close_clicks) or \
            (trigger_id == "btn-save-shortlist-modal" and save_clicks):
        return False, dash.no_update, ""
    return is_open, dash.no_update, dash.no_update


@app.callback(
    Output("shortlist-table", "data"),
    [Input("btn-save-shortlist-modal", "n_clicks")],
    [State("modal-symbol-display", "children"),
     State("modal-notes-input", "value"),
     State("shortlist-table", "data")],
    prevent_initial_call=True
)
def append_to_shortlist_table(n_clicks, symbol, notes, current_data):
    if n_clicks and symbol:
        current_data = current_data or []
        current_data.append({"Symbol": symbol, "Notes": notes or ""})
        return current_data
    raise dash.exceptions.PreventUpdate


@app.callback(
    Output("save-gcs-status", "children"),
    [Input("btn-save-shortlist-gcs", "n_clicks")],
    [State("shortlist-table", "data")],
    prevent_initial_call=True
)
def push_table_to_gcs(n_clicks, table_data):
    if n_clicks:
        if save_shortlist_to_gcs(table_data):
            return f"✔ Saved to GCS at {datetime.datetime.now().strftime('%H:%M:%S')}"
        return "❌ Failed to save."
    raise dash.exceptions.PreventUpdate


# --- ZERODHA ALERTS CALLBACKS ---

@app.callback(
    [Output("alert-left-col", "md"), Output("ato-basket-div", "style")],
    Input("alert-type", "value")
)
def toggle_ato_basket(alert_type):
    if alert_type == 'ato':
        return 6, {"display": "block"}
    return 12, {"display": "none"}


@app.callback(
    Output("ato-basket-input", "value", allow_duplicate=True),
    Input("btn-create-ato-basket", "n_clicks"),
    [State("ato-tradingsymbol", "value"),
     State("ato-transaction-type", "value"),
     State("ato-product", "value"),
     State("ato-quantity", "value"),
     State("alert-exchange", "value")],
    prevent_initial_call=True
)
def generate_ato_basket(n_clicks, t_symbol, t_type, t_product, t_qty, t_exchange):
    if not n_clicks or not t_symbol:
        return dash.no_update
    basket = {
        "name": "alerts-basket", "type": "alert", "tags": [],
        "items": [{
            "type": "insert",
            "tradingsymbol": str(t_symbol).strip().upper(),
            "exchange": t_exchange or "NSE",
            "weight": 10000,
            "params": {
                "transaction_type": t_type, "product": t_product, "order_type": "MARKET", "validity": "DAY",
                "validity_ttl": 1, "quantity": int(t_qty) if t_qty else 1, "price": 0, "trigger_price": 0,
                "disclosed_quantity": 0, "last_price": 0, "variety": "regular", "tags": [], "squareoff": 0,
                "stoploss": 0, "trailing_stoploss": 0, "iceberg_legs": 0, "market_protection": -1
            }
        }]
    }
    return json.dumps(basket, indent=2)


@app.callback(
    [Output("create-alert-modal", "is_open"),
     Output("alert-status-msg", "children"),
     Output("modify-alert-uuid", "data"),
     Output("alert-name", "value"),
     Output("alert-exchange", "value"),
     Output("alert-symbol", "value"),
     Output("alert-attribute", "value"),
     Output("alert-operator", "value"),
     Output("alert-value", "value"),
     Output("alert-type", "value"),
     Output("ato-basket-input", "value"),
     Output("alerts-table", "selected_rows")],
    [Input("btn-create-alert", "n_clicks"),
     Input("btn-modify-alert", "n_clicks"),
     Input("btn-cancel-alert", "n_clicks"),
     Input("btn-submit-alert", "n_clicks")],
    [State("alerts-table", "selected_rows"),
     State("alerts-raw-data-store", "data"),
     State("modify-alert-uuid", "data"),
     State("alert-name", "value"),
     State("alert-exchange", "value"),
     State("alert-symbol", "value"),
     State("alert-attribute", "value"),
     State("alert-operator", "value"),
     State("alert-value", "value"),
     State("alert-type", "value"),
     State("ato-basket-input", "value"),
     State("create-alert-modal", "is_open"),
     State("underlying-info-store", "data")],
    prevent_initial_call=True
)
def handle_alert_modal_actions(create_clicks, modify_clicks, cancel_clicks, submit_clicks,
                               selected_rows, raw_alerts, edit_uuid,
                               name, exchange, symbol, attribute, operator,
                               val, alert_type, ato_basket, is_open, underlying_info):
    ctx = dash.callback_context
    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

    ret = [is_open, "", edit_uuid, name, exchange, symbol, attribute, operator, val, alert_type, ato_basket,
           selected_rows]

    _clicks = {"btn-create-alert": create_clicks, "btn-modify-alert": modify_clicks,
               "btn-cancel-alert": cancel_clicks, "btn-submit-alert": submit_clicks}
    if not _clicks.get(trigger_id):
        return ret

    if trigger_id == "btn-create-alert":
        idx_name = underlying_info.get("symbol", "") if underlying_info else ""
        current_price = underlying_info.get("price", "") if underlying_info else ""
        k_exchange, k_symbol = INDEX_ALERT_MAP.get(idx_name, ('INDICES', idx_name))
        return [True, "", None, f"{idx_name} alert", k_exchange, k_symbol, "LastTradedPrice", ">=", current_price,
                "simple", "", selected_rows]

    elif trigger_id == "btn-modify-alert":
        if not selected_rows or not raw_alerts:
            ret[1] = "Please select an alert to modify."
            return ret
        alert_data = raw_alerts[selected_rows[0]]
        m_basket = alert_data.get("basket", "")
        if isinstance(m_basket, dict):
            m_basket = json.dumps(m_basket)
        return [True, "", alert_data.get("uuid"), alert_data.get("name", ""),
                alert_data.get("lhs_exchange", "INDICES"), alert_data.get("lhs_tradingsymbol", ""),
                alert_data.get("lhs_attribute", "LastTradedPrice"), alert_data.get("operator", ">="),
                alert_data.get("rhs_constant", ""), alert_data.get("type", "simple"), m_basket, selected_rows]

    elif trigger_id == "btn-submit-alert":
        if not name or len(str(name).strip()) == 0:
            ret[1] = "Error: Alert Name is required."
            return ret
        if not symbol or len(str(symbol).strip()) == 0:
            ret[1] = "Error: Trading Symbol is required."
            return ret
        if val is None or str(val).strip() == "":
            ret[1] = "Error: Target Value is required."
            return ret

        payload = {
            "name": name,
            "lhs_exchange": exchange,
            "lhs_tradingsymbol": str(symbol).strip().upper(),
            "lhs_attribute": attribute,
            "operator": operator,
            "rhs_type": "constant",
            "rhs_constant": float(val),
            "type": alert_type
        }
        if alert_type == "ato" and ato_basket:
            try:
                json.loads(ato_basket)
                payload["basket"] = ato_basket
            except json.JSONDecodeError:
                ret[1] = "Error: Invalid JSON format in ATO basket."
                return ret
        try:
            if edit_uuid:
                res = requests.put(f"{KITE_ALERTS_URL}/{edit_uuid}", headers=KITE_HEADERS, data=payload,
                                   timeout=HTTP_TIMEOUT)
            else:
                res = requests.post(KITE_ALERTS_URL, headers=KITE_HEADERS, data=payload, timeout=HTTP_TIMEOUT)
            if res.status_code == 200:
                return [False, "Alert Placed!", None, name, exchange, symbol, attribute, operator, val, alert_type,
                        ato_basket, []]
            ret[1] = f"Error: {res.json().get('message', 'Failed')}"
            return ret
        except Exception as e:
            ret[1] = f"Exception: {str(e)}"
            return ret

    elif trigger_id == "btn-cancel-alert":
        ret[0] = False
        ret[1] = ""
        ret[11] = []
        return ret

    return ret


@app.callback(
    [Output("alerts-table", "data"),
     Output("alerts-raw-data-store", "data"),
     Output("alerts-error-msg", "children")],
    [Input("btn-refresh-alerts", "n_clicks"),
     Input("alerts-table", "data_timestamp"),
     Input("create-alert-modal", "is_open")],
    [State("alerts-table", "data"),
     State("alerts-table", "data_previous"),
     State("alerts-raw-data-store", "data")]
)
def fetch_and_manage_alerts(refresh_clicks, data_timestamp, modal_is_open, current_data, previous_data, current_raw):
    ctx = dash.callback_context
    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0] if ctx.triggered else None

    if trigger_id == "alerts-table" and previous_data is not None:       # row deleted in the table -> delete at Kite
        current_ids = {row.get('uuid') for row in current_data}
        for row in previous_data:
            uuid = row.get('uuid')
            if uuid and uuid not in current_ids:
                requests.delete(f"{KITE_ALERTS_URL}/{uuid}", headers=KITE_HEADERS, timeout=HTTP_TIMEOUT)

    if modal_is_open:           # do not auto-refresh while the user is working in the modal
        raise dash.exceptions.PreventUpdate

    try:
        res = requests.get(KITE_ALERTS_URL, headers=KITE_HEADERS, timeout=HTTP_TIMEOUT)
        if res.status_code == 200:
            alerts = res.json().get('data', [])
            table_data = [{"uuid": a.get("uuid"), "name": a.get("name"), "symbol": a.get("lhs_tradingsymbol"),
                           "condition": f"{a.get('operator')} {a.get('rhs_constant')}", "type": a.get("type")}
                          for a in alerts]
            return table_data, alerts, ""
        return current_data, current_raw, f"API Error: {res.status_code}"
    except Exception as e:
        return current_data, current_raw, f"Request failed: {e}"


@app.callback(Output("ato-tradingsymbol", "options"), [Input("alert-symbol", "value")])
def filter_trading_symbol_options(search_value):
    if not search_value:
        return ZERODHA_SYMBOL_OPTIONS
    search_term = str(search_value).strip().upper()
    return [opt for opt in ZERODHA_SYMBOL_OPTIONS if str(opt['value']).upper().startswith(search_term)]


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
