import os
from datetime import datetime, timedelta
import pandas as pd
import requests
import io
import gzip
from google.cloud import storage
import dash
from dash import dcc, html, Input, Output, State, dash_table
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import numpy as np
import json
import datetime
from google.cloud.exceptions import NotFound
import time
from dash import dash_table as dt
from google.cloud import bigquery
import re

try:                                   # optional: load secrets from a .env file (local runs / Secret Manager mount)
    from dotenv import load_dotenv
    load_dotenv(os.environ.get("ENV_FILE", ".env"))
except ImportError:
    pass


# if os.path.exists(f"AccessToken/{datetime.datetime.now().date()}.json"):
#     with open(f"AccessToken/{datetime.datetime.now().date()}.json", "r") as f:
#         DEFAULT_TOKEN = json.load(f)
#         print(DEFAULT_TOKEN)
# else:
#     print("Generate Access Token")

def get_token_from_gcs(bucket_name, filename=None):
    """Retrieves the access token string from a JSON file in GCS.

    Defaults to today's date (YYYY-MM-DD.json) if no filename is provided.
    """
    if filename is None:
        filename = f"{datetime.datetime.now().date()}.json"

    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(f"upstox/{filename}")

        # Download file contents and parse JSON
        token_data = json.loads(blob.download_as_text())
        return token_data.get("access_token")

    except Exception as e:
        print(f"❌ Failed to retrieve token from GCS: {e}")
        return None

def get_zerodha_token_from_gcs(bucket_name, filename=None):
    """Retrieves the access token string from a JSON file in GCS.

    Defaults to today's date (YYYY-MM-DD.json) if no filename is provided.
    """
    if filename is None:
        filename = f"{datetime.datetime.now().date()}.json"

    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(f"zerodha/{filename}")

        # Download file contents and parse JSON
        token_data = json.loads(blob.download_as_text())
        return token_data.get("access_token")

    except Exception as e:
        print(f"❌ Failed to retrieve token from GCS: {e}")
        return None


DEFAULT_TOKEN = get_token_from_gcs("bba_access_token")

# _____________________________________________________________________________________
# Zerodha Kite Connect Alert Configurations
# _____________________________________________________________________________________
KITE_API_KEY = os.environ.get("KITE_API_KEY", "")
KITE_ACCESS_TOKEN = get_zerodha_token_from_gcs("bba_access_token")

KITE_ALERTS_URL = "https://api.kite.trade/alerts"
KITE_HEADERS = {
    'X-Kite-Version': '3',
    'Authorization': f'token {KITE_API_KEY}:{KITE_ACCESS_TOKEN}'
}

# --- SHORTLIST GCS HELPER FUNCTIONS ---
SHORTLIST_FILE_PATH = "shortlisted_symbols.csv"
SHORTLIST_BUCKET_NAME = "bba_support_files"


def load_shortlist_from_gcs():
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(SHORTLIST_BUCKET_NAME)
        blob = bucket.blob(SHORTLIST_FILE_PATH)
        if blob.exists():
            data = blob.download_as_text()
            df = pd.read_csv(io.StringIO(data))
            return df.to_dict('records')
    except Exception as e:
        print(f"Error loading shortlist from GCS: {e}")
    return []


def save_shortlist_to_gcs(records):
    try:
        # If records are empty, create an empty DataFrame with the correct columns
        df = pd.DataFrame(records) if records else pd.DataFrame(columns=['Symbol', 'Notes'])
        storage_client = storage.Client()
        bucket = storage_client.bucket(SHORTLIST_BUCKET_NAME)
        blob = bucket.blob(SHORTLIST_FILE_PATH)
        blob.upload_from_string(df.to_csv(index=False), 'text/csv')
        return True
    except Exception as e:
        print(f"Error saving shortlist to GCS: {e}")
        return False


# Constants
NSE_NIFTY_500_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
UPSTOX_INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"

# _____________________________________________________________________________________
# Pulling Data from Google Cloud Storage & Initializing Global Clients / Caches
# _____________________________________________________________________________________
watchlist = pd.read_csv("gs://bba_support_files/WL_ALL.csv")
dropdown_opt_list = pd.read_csv("gs://bba_support_files/Dropdown_options.csv")
Expiry_Date_Monthly = pd.read_csv("gs://bba_support_files/stock_expiry_dates.csv")

# --- Local data files (data source 'Local CSV'): picked from a dropdown while the app runs; NOTHING is read at start-up ---
LOCAL_DATA_DIR = os.getcwd()                  # folder that is scanned for data files - change it if your files live elsewhere
LOCAL_DATA_EXT = (".csv", ".xlsx", ".xls")
LOCAL_DATA_DEFAULT = "Master_Equity_Data.csv"   # pre-selected in the dropdown when it exists in the folder
_LOCAL_DATA_CACHE = {}                        # path -> (modified time, DataFrame); re-read automatically when the file changes


def list_local_data_files():
    """Dropdown options: every csv / excel file in LOCAL_DATA_DIR."""
    try:
        names = sorted((f for f in os.listdir(LOCAL_DATA_DIR)
                        if f.lower().endswith(LOCAL_DATA_EXT) and not f.startswith("~$")), key=str.lower)
    except Exception as e:
        print(f"Cannot list {LOCAL_DATA_DIR}: {e}")
        names = []
    return [{"label": n, "value": n} for n in names]


def default_local_file(options):
    names = [o["value"] for o in options]
    return LOCAL_DATA_DEFAULT if LOCAL_DATA_DEFAULT in names else None


def load_local_equity_data(file_name):
    """Read the chosen file on demand (cached; re-read when it changes on disk). Returns None if it cannot be read."""
    if not file_name:
        return None
    path = os.path.join(LOCAL_DATA_DIR, os.path.basename(file_name))      # basename: never leave the folder
    try:
        mtime = os.path.getmtime(path)
        cached = _LOCAL_DATA_CACHE.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        df = pd.read_excel(path) if path.lower().endswith((".xlsx", ".xls")) else pd.read_csv(path)
        if len(_LOCAL_DATA_CACHE) >= 3:
            _LOCAL_DATA_CACHE.pop(next(iter(_LOCAL_DATA_CACHE)))
        _LOCAL_DATA_CACHE[path] = (mtime, df)
        return df
    except Exception as e:
        print(f"❌ Could not load local data file {file_name}: {e}")
        return None


# Global instances to optimize execution speed
bq_client = bigquery.Client()
STOCK_DATA_CACHE = {}
WATCHLIST_CACHE = {}


# _____________________________________________________________________________________
# Fetch Zerodha Instruments
# _____________________________________________________________________________________
def fetch_zerodha_filtered_symbols():
    """Fetches all trading instruments from Zerodha and filters for NSE/NFO exchanges and segments."""
    url = "https://api.kite.trade/instruments"
    try:
        response = requests.get(url)
        response.raise_for_status()
        df = pd.read_csv(io.StringIO(response.text))
        # 1. In Exchange column filter NSE & NFO
        # 2. In segment filter NSE, NSE-OPT, NSE-FUT
        # filtered_df = df[
        #     (df['exchange'].isin(['NFO', 'NSE'])) &
        #     (df['segment'].isin(['NSE', 'NSE-OPT', 'NSE-FUT']))
        #     ]
        filtered_df = df[
            (df['exchange'].isin(['NFO', 'NSE']))
            ]
        filtered_df1 = filtered_df[
            (filtered_df['segment'].isin(['NFO-OPT', 'NSE']))
            ]
        # Extract unique trading symbols
        valid_symbols = filtered_df1['tradingsymbol'].dropna().unique()
        return sorted(valid_symbols.tolist())

    except Exception as e:
        print(f"Error fetching Zerodha symbols: {e}")
        return []


# Initialize once on app startup to prevent slowing down page loads
ZERODHA_FILTERED_SYMBOLS = fetch_zerodha_filtered_symbols()
ZERODHA_SYMBOL_OPTIONS = [{'label': s, 'value': s} for s in ZERODHA_FILTERED_SYMBOLS]


def get_access_token():
    """Fetch Upstox Access Token from GCS or use default."""
    try:
        bucket_name = os.environ.get('UPSTOX_TOKEN_BUCKET')
        if bucket_name:
            client = storage.Client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob('access_token.txt')
            return blob.download_as_text().strip()
    except Exception as e:
        print(f"Error fetching token from GCS: {e}")

    return DEFAULT_TOKEN


# Global cache for instruments
_ALL_INSTRUMENTS = None


def get_all_instruments():
    """Download and cache all instruments from Upstox."""
    global _ALL_INSTRUMENTS
    if _ALL_INSTRUMENTS is None:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        try:
            print("Downloading all instruments...")
            response = requests.get(UPSTOX_INSTRUMENTS_URL, headers=headers)
            response.raise_for_status()
            with gzip.open(io.BytesIO(response.content), 'rt') as f:
                _ALL_INSTRUMENTS = pd.read_csv(f)
            print("Instruments downloaded successfully.")
        except Exception as e:
            print(f"Error fetching Upstox instruments: {e}")
            return pd.DataFrame()
    return _ALL_INSTRUMENTS


def fetch_and_prepare_data():
    """Fetch NIFTY 500 and Upstox instruments, then merge."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    try:
        response = requests.get(NSE_NIFTY_500_URL, headers=headers)
        response.raise_for_status()
        nifty500_df = pd.read_csv(io.StringIO(response.text))
    except Exception as e:
        print(f"Error fetching NIFTY 500: {e}")
        return pd.DataFrame()

    upstox_df = get_all_instruments()
    if upstox_df.empty:
        return pd.DataFrame()

    # Keep both OPTSTK and FUTSTK to follow specific instruction for expiries
    instruments_filtered = upstox_df[upstox_df['instrument_type'].isin(['OPTSTK', 'FUTSTK'])].copy()

    # 1. Get the list of Nifty 500 symbols and sort them by length (descending)
    # Sorting by length is crucial so that longer symbols are matched first
    # (e.g., it ensures 'M&MFIN' is matched before 'M&M').
    nifty_symbols = nifty500_df['Symbol'].dropna().tolist()
    nifty_symbols.sort(key=len, reverse=True)

    # 2. Create a regex pattern to match the symbols at the START of the tradingsymbol
    # re.escape handles any special characters in stock tickers (like '&' or '-')
    # The '^' ensures we only match the beginning of the string.
    pattern = f"^({'|'.join([re.escape(s) for s in nifty_symbols])})"

    # 3. Extract the matching Nifty 500 symbol into a temporary column
    instruments_filtered['Matched_Symbol'] = instruments_filtered['tradingsymbol'].str.extract(pattern, expand=False)

    # 4. Merge the two DataFrames
    # Using how='inner' will only keep the rows that found a match in the Nifty 500 list.
    # (Change to how='left' if you want to keep all instruments, even unmatched ones).
    merged_df = pd.merge(
        instruments_filtered,
        nifty500_df,
        left_on='Matched_Symbol',
        right_on='Symbol',
        how='inner'
    )

    # 5. Clean up by dropping the temporary column
    merged_df = merged_df.drop(columns=['Matched_Symbol'])
    return merged_df


def get_underlying_instrument_info(symbol):
    """Find the NSE_EQ instrument info for a symbol."""
    try:
        upstox_df = get_all_instruments()
        if upstox_df.empty:
            return None

        match = upstox_df[
            (upstox_df['instrument_type'] == 'EQUITY') &
            (upstox_df['tradingsymbol'] == symbol) &
            (upstox_df['exchange'] == 'NSE_EQ')
            ]
        if not match.empty:
            info = match.iloc[0].to_dict()
            key = info['instrument_key']
            info['isin'] = key.split('|')[1] if '|' in key else None
            return info
    except Exception as e:
        print(f"Error finding underlying info: {e}")
    return None


def fetch_company_profile(isin):
    """Fetch Company Profile from Upstox API."""
    url = f"https://api.upstox.com/v2/fundamentals/{isin}/profile"
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {get_access_token()}'}
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json().get('data', {})
    except Exception as e:
        print(f"Error fetching profile: {e}")
    return {}


def fetch_shareholdings(isin):
    """Fetch Shareholding data from Upstox API."""
    url = f"https://api.upstox.com/v2/fundamentals/{isin}/share-holdings"
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {get_access_token()}'}
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json().get('data', [])
    except Exception as e:
        print(f"Error fetching shareholdings: {e}")
    return []


def fetch_corporate_actions(isin):
    """Fetch Corporate actions from Upstox API."""
    url = f"https://api.upstox.com/v2/fundamentals/{isin}/corporate-actions"
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {get_access_token()}'}
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json().get('data', [])
    except Exception as e:
        print(f"Error fetching corporate actions: {e}")
    return []


def fetch_historical_v3(instrument_key, interval='days', interval_value=1, from_date=None, to_date=None):
    """Fetch Historical Candle Data V3."""
    if to_date is None:
        to_date = datetime.datetime.now().strftime('%Y-%m-%d')
    if from_date is None:
        from_date = (datetime.datetime.now() - timedelta(days=100)).strftime('%Y-%m-%d')

    url = f"https://api.upstox.com/v3/historical-candle/{instrument_key}/{interval}/{interval_value}/{to_date}/{from_date}"
    headers = {
        'Accept': 'application/json',
        'Authorization': f'Bearer {get_access_token()}'
    }
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json().get('data', {}).get('candles', [])
            cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi']
            df = pd.DataFrame(data, columns=cols)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df.sort_values('timestamp', ascending=True)
            return df
        else:
            print(f"Error fetching historical V3: {response.status_code} {response.text}")
    except Exception as e:
        print(f"Exception fetching historical V3: {e}")
    return pd.DataFrame()


def fetch_option_chain(underlying_key, expiry):
    """Fetch Option Chain from Upstox API."""
    url = f"https://api.upstox.com/v2/option/chain?instrument_key={underlying_key}&expiry_date={expiry}"
    headers = {
        'Accept': 'application/json',
        'Authorization': f'Bearer {get_access_token()}'
    }
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json().get('data', [])
    except Exception as e:
        print(f"Exception fetching option chain: {e}")
    return []


def fetch_market_quotes(instrument_keys):
    """Fetch Full Market Quote for multiple instruments, chunked by 50 (API limit)."""
    if not instrument_keys:
        return {}

    all_data = {}
    for i in range(0, len(instrument_keys), 50):
        chunk = instrument_keys[i:i + 50]
        keys_str = ",".join(chunk)
        url = f"https://api.upstox.com/v2/market-quote/quotes?instrument_key={keys_str}"
        headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {get_access_token()}'
        }
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                all_data.update(response.json().get('data', {}))
            else:
                print(f"Error fetching market quotes chunk: {response.status_code} {response.text}")
        except Exception as e:
            print(f"Exception fetching market quotes chunk: {e}")

    return all_data


# Initialize Data
INSTRUMENTS_DF = fetch_and_prepare_data()
UNIQUE_SYMBOLS = sorted(INSTRUMENTS_DF['Symbol'].unique()) if not INSTRUMENTS_DF.empty else []

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
server = app.server                           # gunicorn entry point:  gunicorn app:server

# =====================================================================
# POP-OUT WINDOWS for the Trend Analytics graphs (drag the window to a second monitor).
# The trend callbacks remember their latest figure on the server (@_popout_stash); the pop-out page
# (/popout/<name>) polls /popout-data/<name> every 2 s and redraws, keeping its own zoom/pan.
# Plotly.js is served locally from the installed plotly package, so no internet/CDN is needed.
# =====================================================================
import functools
import threading
from flask import Response

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
                print(f"pop-out stash skipped ({name}): {e}")
            return fig
        return wrapper
    return deco


@app.server.route("/popout-plotly.js")
def _popout_plotlyjs():
    import plotly.offline
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


# --- UPDATE THE APP LAYOUT ---
app.layout = dbc.Container([
    # --- HEADER (compact, with row-height controls) ---
    dbc.Row([
        dbc.Col(
            html.Div([
                html.Span("360° STOCK ANALYSIS", className="text-white fw-bold fs-6 me-3"),
                html.Span("Real-time Analytics & Strike Tracking", className="text-white-50 small d-none d-xxl-inline"),
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

    # --- ACCESS TOKEN EXPIRY BANNER (shown on load / every 5 min when today's token file is missing in GCS) ---
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
            html.Label("Selected Symbol:", className="fw-bold text-muted small"),
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
        # ... keep original modal components as is ...
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
                            ], value='NSE')
                        ], width=4),
                        dbc.Col([
                            dbc.Label("Trading Symbol", className="small fw-bold mt-2"),
                            dbc.Input(id="alert-symbol", type="text", placeholder="E.g., RELIANCE"),
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
                            dbc.Input(id="alert-value", type="number", placeholder="E.g., 2500")
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
                                                  {'label': 'NRML', 'value': 'NRML'}], value='CNC')
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

# --- DYNAMIC INTERFACE ELEMENT DECLARATIONS FOR USE IN CALLBACK ---
# Separate the core functional chunks so they can be easily moved around based on state

def build_sr_control():
    """Support / Resistance lines = pivot points (P, R1, S1) of the previous completed Day / Week / Month.
    Multi-select; drawn on the price chart by update_graph_31."""
    return html.Div([
        html.Span("S/R", className="fw-bold small me-2", style={"cursor": "help"},
                  title="Support & Resistance = pivot points (P, R1, S1) of the previous Day / Week / Month"),
        dbc.Checklist(id="sr-levels-select", inline=True, switch=True, value=[], className="mb-0",
                      style={"fontSize": "0.72rem"},
                      options=[{"label": "Daily", "value": "D"}, {"label": "Weekly", "value": "W"},
                               {"label": "Monthly", "value": "M"}]),
    ], className="d-flex align-items-center")


equity_hub_card = dbc.Card([
    dbc.CardHeader(
        dbc.Row([
            dbc.Col(
                [html.H5("Equity & Options Analysis", className="m-0 fw-bold text-primary text-center text-md-start")],
                xs=12, md=2, className="mb-2 mb-md-0"),

            # --- NEW: Scanner Dropdown ---
            dbc.Col([
                dbc.Label("Scanner", className="fw-bold text-muted mb-1 small"),
                dcc.Dropdown(id='scanner-dropdown',
                             options=[
                                 {'label': 'None', 'value': 'None'},
                                 {'label': 'Shortlisted', 'value': 'Shortlisted'},
                                 {'label': '10M Vol Scanner', 'value': '10M Vol Scanner'}
                             ],
                             value='None',  # Set 'None' as default
                             clearable=False)
            ], xs=6, md=2, className="mb-2 mb-md-0"),

            # --- UPDATED: Reduced column width (md=5 to md=3) and removed toggle switch ---
            dbc.Col([
                dbc.Label("Symbol", className="fw-bold text-muted mb-1 small"),
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
            dbc.Tab(label="Stock Analysis", tab_id="tab-stock-analysis", children=[
                html.Div(build_sr_control(), className="px-2 pt-1"),
                dcc.Graph(id='graph_31', config=dict({'scrollZoom': True}),
                          style={"height": "60vh", "minHeight": "400px"})],
                    label_style={"border": "none", "background": "transparent", "color": "#6c757d"},
                    active_label_style={"border": "none", "border-bottom": "3px solid #007bff", "color": "#007bff",
                                        "background": "transparent", "font-weight": "bold"}),
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
            ], label_style={"border": "none", "background": "transparent", "color": "#6c757d"},
                    active_label_style={"border": "none", "border-bottom": "3px solid #007bff", "color": "#007bff",
                                        "background": "transparent", "font-weight": "bold"}),
            dbc.Tab(label="PCR Trend", tab_id="tab-pcr", children=[
                html.Div(dcc.Graph(id='pcr-graph', style={"height": "50vh", "minHeight": "350px"}), className="pt-2")],
                    label_style={"border": "none", "background": "transparent", "color": "#6c757d"},
                    active_label_style={"border": "none", "border-bottom": "3px solid #007bff", "color": "#007bff",
                                        "background": "transparent", "font-weight": "bold"}),
            dbc.Tab(label="Total OI", tab_id="tab-oi", children=[
                html.Div(dcc.Graph(id='oi-graph', style={"height": "50vh", "minHeight": "350px"}), className="pt-2")],
                    label_style={"border": "none", "background": "transparent", "color": "#6c757d"},
                    active_label_style={"border": "none", "border-bottom": "3px solid #007bff", "color": "#007bff",
                                        "background": "transparent", "font-weight": "bold"}),
            dbc.Tab(label="OI Change", tab_id="tab-oi-change", children=[
                html.Div(dcc.Graph(id='oi-change-graph', style={"height": "50vh", "minHeight": "350px"}),
                         className="pt-2")],
                    label_style={"border": "none", "background": "transparent", "color": "#6c757d"},
                    active_label_style={"border": "none", "border-bottom": "3px solid #007bff", "color": "#007bff",
                                        "background": "transparent", "font-weight": "bold"}),
            dbc.Tab(label="Volume", tab_id="tab-volume", children=[
                html.Div(dcc.Graph(id='volume-graph', style={"height": "50vh", "minHeight": "350px"}),
                         className="pt-2")],
                    label_style={"border": "none", "background": "transparent", "color": "#6c757d"},
                    active_label_style={"border": "none", "border-bottom": "3px solid #007bff", "color": "#007bff",
                                        "background": "transparent", "font-weight": "bold"})
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
TOKEN_BUCKET = "bba_access_token"
_HERE = os.path.dirname(os.path.abspath(__file__))
# The two ready-made scripts are loaded from these paths (put them next to this dashboard, or change the paths).
UPSTOX_TOKEN_SCRIPT = os.path.join(_HERE, "Upstox_Generate_Access_Token.py")
ZERODHA_TOKEN_SCRIPT = os.path.join(_HERE, "Zerodha_Generate_Access_Token.py")
_BROKER_LABEL = {"upstox": "Upstox", "zerodha": "Zerodha"}
_BROKER_NEEDED_FOR = {"upstox": "market data, option chain, streaming", "zerodha": "Create Alert / Active Alerts"}


def _token_exists(broker):
    """True / False = today's <broker>/<date>.json exists in the bucket; None = could not check (GCS unreachable)."""
    try:
        blob = storage.Client().bucket(TOKEN_BUCKET).blob(f"{broker}/{datetime.datetime.now().date()}.json")
        return bool(blob.exists())
    except Exception as e:
        print(f"❌ Token check failed for {broker}: {e}")
        return None


def _apply_token(broker, token):
    """Make a (new) token live in the running app - no restart needed."""
    global DEFAULT_TOKEN, KITE_ACCESS_TOKEN
    if broker == "upstox":
        DEFAULT_TOKEN = token
    else:
        KITE_ACCESS_TOKEN = token
        KITE_HEADERS['Authorization'] = f'token {KITE_API_KEY}:{token}'   # dict is shared by the alert callbacks


def _load_script_module(path, module_name):
    """Import one of the token scripts from a file path (their `if __name__ == "__main__"` block does not run)."""
    import importlib.util
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
            dbc.Checklist(id="ma-lines-select", inline=True, switch=True, className="mb-1", style={"fontSize": "0.85rem"},
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
            html.H6("Select Resolution", className="small text-muted"),
            dcc.RangeSlider(id='my-range-slider', min=400, max=800, step=50, value=[800], marks=None, className="mb-3"),
            html.H6("Data source", className="small text-muted"),
            dbc.Row([dcc.RadioItems(id="data_source_select", options=[{"label": " BigQuery ", "value": "bq"},
                                                                      {"label": " Local CSV ", "value": "local"}],
                                    value="bq", inline=True, labelStyle={'marginRight': '15px'})]),
            html.Div([
                dcc.Dropdown(id="local-data-file", options=list_local_data_files(),
                             value=default_local_file(list_local_data_files()), clearable=False, disabled=True,
                             placeholder="Select a local data file (csv / xlsx)",
                             style={"fontSize": "0.8rem", "flex": "1 1 auto", "minWidth": 0}),
                dbc.Button("↻", id="btn-local-refresh", size="sm", color="secondary", outline=True, className="ms-1",
                           title="Rescan the folder and re-read the selected file"),
            ], className="d-flex align-items-center mt-2"),
            html.Div(f"Folder: {LOCAL_DATA_DIR}", id="local-data-status", className="small text-muted mt-1",
                     style={"wordBreak": "break-all"}),
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
        dbc.AccordionItem([html.Div(id="shareholding-display", className="fw-semibold text-dark small")],
                          title="Shareholding Data"),
        dbc.AccordionItem([
            dbc.Row([
                dbc.Col(dbc.Button("↻ Refresh", id="btn-refresh-alerts", size="sm", color="info", outline=True,
                                   className="w-100 fw-bold"), width=6, className="pe-1"),
                dbc.Col(dbc.Button("✎ Modify Selected", id="btn-modify-alert", size="sm", color="warning", outline=True,
                                   className="w-100 fw-bold"), width=6, className="ps-1"),
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


def build_settings_fundamentals(include_shareholding=True):
    items = _settings_items()
    if not include_shareholding:
        items = [i for i in items if getattr(i, "title", None) != "Shareholding Data"]
    return html.Div([dbc.Accordion(items, id="settings-accordion", start_collapsed=True, flush=True)],
                    className="py-2")


settings_fundamentals_element = html.Div("Settings & Fundamentals are in the ⚙ Settings drawer (top bar).",
                                         className="small text-muted p-2")

# Right-side drawer (non-modal: the dashboard stays usable while it is open). Added to the root layout here because
# build_settings_fundamentals() is defined after app.layout.
settings_offcanvas = dbc.Offcanvas(
    build_settings_fundamentals(include_shareholding=False),
    id="settings-offcanvas", title="⚙ Settings & Fundamentals", placement="end", is_open=False,
    scrollable=True, backdrop=False, style={"width": "480px", "maxWidth": "95vw"})
app.layout.children.append(settings_offcanvas)

workspace_panel_card = dbc.Card([
    dbc.CardHeader(
        dbc.Row([
            dbc.Col(
                [html.H6("Profile & Sector", className="text-muted text-uppercase mb-0", style={'fontSize': '0.7rem'})],
                xs=4, md=3),
            dbc.Col([html.Div(id="profile-display", className="fw-semibold text-dark small")], xs=8, md=9)
        ], className="align-items-center")
    ),
    dbc.CardBody(dbc.Tabs([
        dbc.Tab(label="IV Graph", tab_id="tab-iv", className="m-0 fw-bold", children=[
            iv_graph_element,
            html.Hr(),
            html.Div(dcc.Graph(id='otm-prices-graph', style={'height': '25vh'}))
        ], label_style={"border": "none", "background": "transparent", "color": "#6c757d"},
                active_label_style={"border": "none", "border-bottom": "3px solid #007bff", "color": "#007bff",
                                    "background": "transparent", "font-weight": "bold"}),
        dbc.Tab(label="Settings & Fundamentals", tab_id="tab-fundamental", className="m-0 fw-bold",
                children=[settings_fundamentals_element],
                label_style={"border": "none", "background": "transparent", "color": "#6c757d"},
                active_label_style={"border": "none", "border-bottom": "3px solid #007bff", "color": "#007bff",
                                    "background": "transparent", "font-weight": "bold"}),
    ], style={"overflowX": "auto", "whiteSpace": "nowrap"}))
], className="shadow-sm border-0 h-100")

option_chain_card = dbc.Card([
    dbc.CardHeader(html.H5("Option Chain Data", className="m-0 fw-bold")),
    dbc.CardBody(html.Div(id='option-chain-table-container', className="table-responsive"))
], className="shadow-sm border-0 h-100")

streaming_panel_card = dbc.Card([
    dbc.CardHeader(html.H6("Streaming Buy/Sell Qty", className="m-0 fw-bold")),
    dbc.CardBody([
        dbc.Row([
            dbc.Col([
                dbc.ButtonGroup([dbc.Button("▶ Stream", id="start-stream-btn", size="sm", color="success"),
                                 dbc.Button("■ Stop", id="stop-stream-btn", size="sm", color="danger")]),
                dbc.Button("● Record", id="start-recording-btn", size="sm", color="info", outline=True,
                           className="ms-2"),
            ], xs=12, sm=7, className="d-flex align-items-center mb-2 mb-sm-0"),
            dbc.Col([
                dbc.InputGroup([
                    dbc.InputGroupText("Interval"),
                    dbc.Button("-", id="interval-dec-btn", size="sm", color="secondary", outline=True),
                    dbc.Input(id="interval-input", type="number", value=3, min=1, step=1,
                              style={'textAlign': 'center'}),
                    dbc.Button("+", id="interval-inc-btn", size="sm", color="secondary", outline=True),
                ], size="sm")
            ], xs=12, sm=5, className="d-flex align-items-center")
        ], className="mb-2 g-2"),
        html.Hr(className="my-2"),
        html.Div(id='streaming-table-container', className="table-responsive"),
        dcc.Interval(id='stream-interval', interval=3000, n_intervals=0, disabled=True)
    ])
], className="shadow-sm border-0 h-100")

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
                    label_style={"border": "none", "background": "transparent", "color": "#6c757d"},
                    active_label_style={"border": "none", "border-bottom": "3px solid #007bff", "color": "#007bff",
                                        "background": "transparent", "font-weight": "bold"}),
            dbc.Tab(label="Buy/Sell Qty Trend", tab_id="tab-trend-buysell", children=[
                html.Div(dcc.Graph(id='buysell-trend-graph', style={'height': '400px'}), className="pt-2")],
                    label_style={"border": "none", "background": "transparent", "color": "#6c757d"},
                    active_label_style={"border": "none", "border-bottom": "3px solid #007bff", "color": "#007bff",
                                        "background": "transparent", "font-weight": "bold"})
        ], id="trend-workspace-tabs", active_tab="tab-trend-oi", style={"border-bottom": "1px solid #dee2e6"})
    ])
], className="shadow-sm border-0 h-100")


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
                                       {'label': 'Shortlisted', 'value': 'Shortlisted'},
                                       {'label': '10M Vol Scanner', 'value': '10M Vol Scanner'}],
                              value='None', clearable=False)], width=1),
        dbc.Col([_lbl("Symbol"),
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
                width=1),
        dbc.Col([_lbl("Days"),
                 dcc.Dropdown(id='dropdown_n_days',
                              options=[{'label': f"{d} Days", 'value': d} for d in [7, 15, 30, 60, 90, 120, 150]],
                              value=60, multi=False, clearable=False)], width=1),
        dbc.Col([_lbl("MAX PAIN"),
                 html.Div(id="max-pain-display", className="text-primary fw-bold", style={'fontSize': '1.0rem'})],
                width=1, className="text-center"),
        dbc.Col(html.Div(id="underlying-price-display", className="text-secondary",
                         style={'fontSize': '0.75rem'}), width=2),
        # Profile & Sector sits immediately to the LEFT of the Create Alert button
        dbc.Col(html.Div(id="profile-display", className="text-dark text-end",
                         style={"fontSize": "0.7rem", "lineHeight": "1.1", "maxHeight": "40px",
                                "overflow": "hidden"}), width=2),
        dbc.Col(dbc.Button("🔔 Create Alert", id="btn-create-alert", size="sm", color="primary", outline=True,
                           className="fw-bold"), width=1, className="text-end"),
    ], className="g-2 align-items-center"), className="p-1"), className="shadow-sm border-0 mb-1")


def build_stream_controls():
    """Stream / Stop / Record / Interval buttons (shown in the Stock Analysis header in the compact layout)."""
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
    """Option chain + streaming buy/sell quantities in ONE table (rendered by update_merged_table).
    with_controls=False -> the stream buttons are placed elsewhere (compact layout: Stock Analysis header)."""
    return _panel("Option Chain + Streaming Buy/Sell Qty", [
        html.Div(id='merged-table-container'),
        dcc.Interval(id='stream-interval', interval=3000, n_intervals=0, disabled=True),
    ], row, right=build_stream_controls() if with_controls else None)


def _hrow(frac, extra_px=0):
    """Height of a support-graph row = `frac` of the Row-1 right-panel body (the two 4px grid gaps removed first)."""
    return f"calc(({_h('r1', 8)} - 8px) * {frac} - {extra_px}px)"


CHAIN_TABLE_FRAC = 0.56   # 'Show Option Chain' view: share of the card used by the table (the rest: OI Change | Share Holding)
STRIKE_BOX_PX = 80        # Trend Analytics: height of the CE / PE strike check-box area (wraps onto several lines)


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
    Support Graphs = 3 rows of 40 % / 40 % / 20 %; rows 1-2 are split 70 : 30:
        row 1 = CE Graph | IV Graph      row 2 = PE Graph | Put/Call price      row 3 = blank (news feed, id 'news-panel').
    Option Chain = the merged table on top, OI Change (tabs) | Share Holding underneath."""
    ts = _tab_styles()
    strike_row = {"whiteSpace": "nowrap", "overflowX": "auto", "overflowY": "hidden", "height": "44px",
                  "paddingTop": "4px"}
    lbl = {'margin-right': '10px', 'cursor': 'pointer', 'fontSize': '0.75rem'}
    h_top, h_news = _hrow(0.4), _hrow(0.2)
    g_strike = _hrow(0.4, 24 + 44)          # header 22 + borders 2, strike-button row 44
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
    news = html.Div(id="news-panel", className="border rounded",      # intentionally blank: reserved for the stock news feed
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

    # --- Option Chain view: table, then OI Change | Share Holding below it ---
    h_tbl = _hrow(CHAIN_TABLE_FRAC)
    h_low = _hrow(1 - CHAIN_TABLE_FRAC)
    g_tab = f"calc({h_low} - 24px - 34px)"          # cell header + tab row
    oi_tabs = dbc.Tabs([
        dbc.Tab(label="OI Chg", tab_id="c-oichg", children=[dcc.Graph(id='oi-change-graph', style={'height': g_tab})], **ts),
        dbc.Tab(label="PCR", tab_id="c-pcr", children=[dcc.Graph(id='pcr-graph', style={'height': g_tab})], **ts),
        dbc.Tab(label="OI", tab_id="c-oi", children=[dcc.Graph(id='oi-graph', style={'height': g_tab})], **ts),
        dbc.Tab(label="Vol", tab_id="c-vol", children=[dcc.Graph(id='volume-graph', style={'height': g_tab})], **ts),
    ], id="compact-oi-tabs", active_tab="c-oichg", style={"borderBottom": "1px solid #dee2e6"})
    oi = _cell("OI Change", oi_tabs, h_low)
    share = _cell("Share Holding", html.Div(id="shareholding-display", className="fw-semibold text-dark small p-1"),
                  h_low, scroll=True)
    chain_view = html.Div([
        html.Div(id='merged-table-container', style={"height": h_tbl, "overflow": "hidden"}),
        html.Div([oi, share], style={"display": "grid", "gridTemplateColumns": "minmax(0, 11fr) minmax(0, 9fr)",
                                     "gap": "4px", "height": h_low, "marginTop": "4px"}),
    ], id="option-chain-view", className="d-none")
    return _panel("Options", [graphs_view, chain_view,
                              dcc.Interval(id='stream-interval', interval=3000, n_intervals=0, disabled=True)],
                  'r1', right=toggle)


def build_compact_layout():
    ts = _tab_styles()

    # ---------- ROW 1 : Stock Analysis 60% (full screen height) | Options card 40% ----------
    stock = _panel("Stock Analysis",
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


# ---- Header +/- row-height buttons (client-side, no server round trip) ----
app.clientside_callback(
    """
    function(r1d, r1i, r2d, r2i, reset, data) {
        const ctx = window.dash_clientside.callback_context;
        const trig = (ctx.triggered && ctx.triggered.length) ? ctx.triggered[0].prop_id.split('.')[0] : '';
        const defaults = {r1: 100, r2: 40, r3: 26};
        let h = Object.assign({}, defaults, data || {});
        const step = 2, lo = 12, hi = 70, hi1 = 150;
        const clamp = v => Math.max(lo, Math.min(hi, v));
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


# ---- Row 1 right panel: 'Show Option Chain' / 'Show Support Graphs' (client-side, no server round trip) ----
app.clientside_callback(
    """
    function(mode) {
        setTimeout(() => window.dispatchEvent(new Event('resize')), 120);   // let Plotly re-fit after un-hiding
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
    trig = dash.callback_context.triggered[0]["prop_id"].split(".")[0]
    if trig == "btn-banner-open-tokens":
        return True, "tokens"
    return (not is_open), dash.no_update


# ---- Access tokens: check today's token files in GCS on load, every 5 min, and after each generation ----
@app.callback(
    [Output("token-expiry-banner", "is_open"), Output("token-expiry-text", "children"),
     Output("token-expiry-banner", "color"), Output("token-status-detail", "children")],
    [Input("token-check-interval", "n_intervals"), Input("token-gen-trigger", "data")]
)
def check_access_tokens(_n, _generated):
    today = datetime.datetime.now().date()
    status = {b: _token_exists(b) for b in ("upstox", "zerodha")}

    # a token generated elsewhere (scheduled script, another session) is picked up without a restart
    for broker, loader in (("upstox", get_token_from_gcs), ("zerodha", get_zerodha_token_from_gcs)):
        if status[broker]:
            fresh = loader(TOKEN_BUCKET)
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
        print(f"❌ {label} token generation failed: {e}")
        return dbc.Alert(f"✖ {label} token failed: {str(e)[:220]}", color="danger", className="py-1 px-2 mb-0"), time.time()
    if not token:
        return dbc.Alert(f"✖ {label} login did not return a token - see the console for the reason.",
                         color="danger", className="py-1 px-2 mb-0"), time.time()
    _apply_token(broker, token)
    return dbc.Alert(f"✔ {label} token generated, saved to GCS and active ({time.time() - started:.0f}s).",
                     color="success", className="py-1 px-2 mb-0"), time.time()


# ---- Local data dropdown: enabled only for the 'Local CSV' source; the refresh button rescans the folder ----
@app.callback(Output("local-data-file", "disabled"), Input("data_source_select", "value"))
def toggle_local_file_dropdown(source):
    return source != "local"


@app.callback(
    [Output("local-data-file", "options"), Output("local-data-status", "children")],
    [Input("btn-local-refresh", "n_clicks")],
    prevent_initial_call=True
)
def refresh_local_files(n_clicks):
    if not n_clicks:
        raise dash.exceptions.PreventUpdate
    options = list_local_data_files()
    return options, f"Folder: {LOCAL_DATA_DIR}  ({len(options)} file(s) found)"


# --- NEW CALLBACK TO DYNAMICALLY CHANGE LAYOUT FACTORING LANDSCAPE VS PORTRAIT MODE ---
@app.callback(
    Output("dynamic-content-layout", "children"),
    [Input("layout-toggle-switch", "value")]
)
def render_dynamic_layout(portrait_toggle):
    if portrait_toggle and True in portrait_toggle:
        # --- PORTRAIT MODE: 4 ROWS ---
        return [
            # Row 1: Equity & Option analysis
            dbc.Row([
                dbc.Col(equity_hub_card, width=12, className="mb-4")
            ]),

            # Row 2: IV Graph (Col 1), 10 OTM Price Graph (Col 2), Settings and Fundamentals (Col 3) - Equal Width
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
                    dbc.CardHeader(html.H6("Share Holding", className="m-0 fw-bold text-primary")),
                    dbc.CardBody([html.Div(id="shareholding-display", className="fw-semibold text-dark small"),
                                  html.Div("Settings, tokens and alerts: ⚙ Settings (top bar).",
                                           className="small text-muted mt-2")],
                                 style={"overflow": "auto"})
                ], className="shadow-sm border-0 h-100"), width=4, className="mb-4")
            ], className="align-items-stretch"),

            # Row 3: merged Option Chain + Streaming table
            dbc.Row([
                dbc.Col(build_merged_panel('r1'), width=12, className="mb-4")
            ]),

            # Row 4: Trend analysis
            dbc.Row([
                dbc.Col(trend_analytics_card, width=12, className="mb-4")
            ], className="align-items-stretch")
        ]
    else:
        # --- COMPACT LANDSCAPE LAYOUT (DEFAULT, one screen on a 24" monitor) ---
        return build_compact_layout()


# --- NEW CALLBACK FOR MANUALLY ADJUSTING GRAPH VERTICAL HEIGHT ---
@app.callback(
    [Output('oi-trend-graph', 'style'),
     Output('buysell-trend-graph', 'style')],
    [Input('trend-height-slider', 'value')]
)
def update_card_graph_heights(slider_value):
    style = {'height': f'{slider_value}px'}
    return style, style


# Callbacks
@app.callback(
    [Output('symbol-dropdown', 'options'),
     Output('symbol-dropdown', 'value')],
    [Input('scanner-dropdown', 'value'),  # --- CHANGED INPUT ---
     Input('shortlist-table', 'data'),
     Input('prev-btn', 'n_clicks'),
     Input('next-btn', 'n_clicks')],
    [State('symbol-dropdown', 'value')]
)
def update_and_navigate_symbols(scanner_val, shortlist_data, prev_clicks, next_clicks, current_symbol):
    ctx = dash.callback_context
    triggered_id = ctx.triggered[0]['prop_id'].split('.')[0] if ctx.triggered else None

    # 1. Determine which list of symbols to use based on the Scanner Dropdown
    if scanner_val == 'Shortlisted':
        valid_symbols = []
        if shortlist_data:
            valid_symbols = list(set([row['Symbol'] for row in shortlist_data]))
        valid_symbols = sorted(valid_symbols)
        # Fallback if toggle is checked but table is empty
        if not valid_symbols:
            valid_symbols = UNIQUE_SYMBOLS

    elif scanner_val == '10M Vol Scanner':
        try:
            # Fetch from GCS
            vol_scanner_df = pd.read_csv("gs://bba_support_files/WL_10MVOL.csv")
            # Account for potential case variations in CSV column name ('symbol' or 'Symbol')
            col_name = 'symbol' if 'symbol' in vol_scanner_df.columns else 'Symbol'
            valid_symbols = sorted(vol_scanner_df[col_name].dropna().unique().tolist())

            # Fallback if list is empty
            if not valid_symbols:
                valid_symbols = UNIQUE_SYMBOLS
        except Exception as e:
            print(f"Error loading 10M Vol Scanner file: {e}")
            valid_symbols = UNIQUE_SYMBOLS

    else:
        # Default 'None'
        valid_symbols = UNIQUE_SYMBOLS

    options = [{'label': s, 'value': s} for s in valid_symbols]

    # 2. Handle symbol navigation and persistence
    if not valid_symbols:
        return [], None

    # If the current symbol is no longer in the valid list, default to the first available
    new_symbol = current_symbol if current_symbol in valid_symbols else valid_symbols[0]

    # Handle Next/Prev button clicks
    if (triggered_id == 'prev-btn' and prev_clicks) or (triggered_id == 'next-btn' and next_clicks):
        idx = valid_symbols.index(new_symbol)
        if triggered_id == 'next-btn':
            new_symbol = valid_symbols[(idx + 1) % len(valid_symbols)]
        elif triggered_id == 'prev-btn':
            new_symbol = valid_symbols[(idx - 1) % len(valid_symbols)]

    return options, new_symbol


@app.callback(
    [Output('expiry-dropdown', 'options'), Output('expiry-dropdown', 'value'), Output('underlying-info-store', 'data')],
    [Input('symbol-dropdown', 'value')]
)
def update_symbol_expiries(symbol):
    if not symbol: return [], None, None
    underlying_info = get_underlying_instrument_info(symbol)
    if not underlying_info: return [], None, None
    expiries = sorted(
        INSTRUMENTS_DF[(INSTRUMENTS_DF['Symbol'] == symbol) & (INSTRUMENTS_DF['instrument_type'] == 'FUTSTK')][
            'expiry'].dropna().unique())
    if not expiries:
        expiries = sorted(
            INSTRUMENTS_DF[(INSTRUMENTS_DF['Symbol'] == symbol) & (INSTRUMENTS_DF['instrument_type'] == 'OPTSTK')][
                'expiry'].dropna().unique())

    options = [{'label': e, 'value': e} for e in expiries]
    default_expiry = expiries[0] if expiries else None
    underlying_info['symbol'] = symbol
    return options, default_expiry, underlying_info


@app.callback(
    [Output('profile-display', 'children'), Output('shareholding-display', 'children')],
    [Input('underlying-info-store', 'data')]
)
def update_fundamentals(info):
    if not info or not info.get('isin'): return "N/A", "N/A"
    isin = info['isin']
    profile = fetch_company_profile(isin)
    shares = fetch_shareholdings(isin)

    # 1. Company Profile Display
    profile_html = html.Div([
        html.P(f"Sector: {profile.get('sector', 'N/A')}", style={'fontWeight': 'bold', 'margin': 0}),
        html.P(profile.get('company_profile', 'N/A')[:100] + "...", style={'fontSize': '10px', 'margin': 0, 'lineHeight': '1.1'})
    ])

    # 2. Shareholdings Table (Columns = Period, Rows = Category)
    if shares:
        # Extract unique period headers maintaining original order (usually newest first)
        periods = []
        for cat_data in shares:
            for h in cat_data.get('history', []):
                p = h.get('period')
                if p and p not in periods:
                    periods.append(p)

        # Reverse to make the latest quarter appear at the last column
        periods.reverse()

        # Construct row data and maintain raw values for numerical comparison
        table_rows = []
        raw_values = {}

        for cat_data in shares:
            category_name = cat_data.get('category', 'Unknown').replace('_', ' ').upper()
            row_dict = {'Category': category_name}
            raw_values[category_name] = {}

            for h in cat_data.get('history', []):
                period = h.get('period')
                val = h.get('value', 0)
                row_dict[period] = f"{val:.2f}%"
                raw_values[category_name][period] = val

            table_rows.append(row_dict)

        df_shares = pd.DataFrame(table_rows)
        columns = [{'name': 'Category', 'id': 'Category'}] + [{'name': p, 'id': p} for p in periods]

        # Build dynamic conditional formatting for Q-o-Q growth
        style_cond = []
        for cat, vals in raw_values.items():
            for i in range(1, len(periods)):
                curr_p = periods[i]
                prev_p = periods[i - 1]

                # Check if data exists for both the current and previous period
                if curr_p in vals and prev_p in vals:
                    if vals[curr_p] > vals[prev_p]:
                        style_cond.append({
                            'if': {
                                'filter_query': f'{{Category}} = "{cat}"',
                                'column_id': curr_p
                            },
                            'backgroundColor': '#e6f4ea',  # Light green background
                            'color': '#137333',  # Dark green text
                            'fontWeight': 'bold'
                        })

        shares_html = dash_table.DataTable(
            data=df_shares.to_dict('records'),
            columns=columns,
            style_cell={'textAlign': 'center', 'padding': '2px 4px', 'fontSize': '11px'},
            style_header={'fontWeight': 'bold', 'backgroundColor': '#f8f9fa', 'border': '1px solid #dee2e6'},
            style_data={'border': '1px solid #dee2e6'},
            style_data_conditional=style_cond,
            style_table={'overflowX': 'auto'}
        )
    else:
        shares_html = html.Div("No shareholding data available", className="text-muted small")

    return profile_html, shares_html


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

    if not expiry or not underlying_info:
        return [], None, [], None, None, underlying_info, [], []

    underlying_key = underlying_info['instrument_key']
    chain_data = fetch_option_chain(underlying_key, expiry)
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


@app.callback(
    Output('ce-candle-graph', 'figure'),
    [Input('ce-strike-radio', 'value'), Input('stream-interval', 'n_intervals')],
    [State('underlying-info-store', 'data'), State('stream-interval', 'disabled')]
)
def update_ce_graph(strike, n, underlying_info, stream_disabled):
    ctx = dash.callback_context
    triggered_id = ctx.triggered[0]['prop_id'].split('.')[0] if ctx.triggered else None

    if triggered_id == 'stream-interval' and stream_disabled:
        raise dash.exceptions.PreventUpdate

    if not strike or not underlying_info: return go.Figure()
    symbol = underlying_info['symbol']
    expiry = underlying_info['expiry']
    match = INSTRUMENTS_DF[(INSTRUMENTS_DF['Symbol'] == symbol) & (INSTRUMENTS_DF['strike'] == strike) & (
            INSTRUMENTS_DF['option_type'] == 'CE') & (INSTRUMENTS_DF['expiry'] == expiry)]
    if match.empty: return go.Figure()

    # --- NEW: Extract Lot Size ---
    try:
        lot_size = int(match.iloc[0].get('lot_size', 1))
    except (ValueError, TypeError):
        lot_size = 1

    instrument_key = match.iloc[0]['instrument_key']
    df = fetch_historical_v3(instrument_key)

    quotes_data = fetch_market_quotes([instrument_key])

    q = quotes_data.get(instrument_key)
    if not q:
        for k, v in quotes_data.items():
            if v.get('instrument_token') == instrument_key or k.endswith(instrument_key.split('|')[-1]):
                q = v
                break

    if q:
        ohlc = q.get('ohlc', {})
        new_row = {
            'timestamp': datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0),
            'open': ohlc.get('open'),
            'high': ohlc.get('high'),
            'low': ohlc.get('low'),
            'close': q.get('last_price'),
            'volume': q.get('volume'),
            'oi': q.get('oi')
        }
        today_ts = pd.to_datetime(new_row['timestamp'])
        if not df.empty:
            last_ts = pd.to_datetime(df.iloc[-1]['timestamp'])
            if last_ts.date() == today_ts.date():
                for k, v in new_row.items():
                    df.iloc[-1, df.columns.get_loc(k)] = v
            else:
                df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        else:
            df = pd.DataFrame([new_row])

    if df.empty: return go.Figure()

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0, row_heights=[0.5, 0.25, 0.25])
    fig.add_trace(go.Candlestick(x=df['timestamp'], open=df['open'], high=df['high'], low=df['low'], close=df['close']),
                  row=1, col=1)
    fig.add_trace(go.Bar(x=df['timestamp'], y=df['volume']), row=2, col=1)
    fig.add_trace(go.Scatter(x=df['timestamp'], y=df['oi']), row=3, col=1)

    # --- NEW: Update Title with Lot Size ---
    fig.update_layout(title=f"CE - {symbol} {strike} {expiry} | Lot Size: {lot_size}", plot_bgcolor='white',
                      paper_bgcolor='white',
                      xaxis_rangeslider_visible=False, showlegend=False)
    fig.update_xaxes(showline=True, linewidth=1, linecolor='black', mirror=True)
    fig.update_yaxes(showline=True, linewidth=1, linecolor='black', mirror=True)

    # --- NEW: Display 1 Lot Value on the Right Axis ---
    latest_price = df['close'].iloc[-1]
    lot_value = latest_price * lot_size
    fig.add_hline(
        y=latest_price,
        line_dash="dot",
        line_color="royalblue",
        annotation_text=f"₹{lot_value:,.2f}",
        annotation_position="right",
        row=1, col=1
    )

    return _compact_fig(fig, right=62, title=f"CE {symbol} {strike} | Lot {lot_size}", legend=False)


@app.callback(
    Output('pe-candle-graph', 'figure'),
    [Input('pe-strike-radio', 'value'), Input('stream-interval', 'n_intervals')],
    [State('underlying-info-store', 'data'), State('stream-interval', 'disabled')]
)
def update_pe_graph(strike, n, underlying_info, stream_disabled):
    ctx = dash.callback_context
    triggered_id = ctx.triggered[0]['prop_id'].split('.')[0] if ctx.triggered else None

    if triggered_id == 'stream-interval' and stream_disabled:
        raise dash.exceptions.PreventUpdate

    if not strike or not underlying_info: return go.Figure()
    symbol = underlying_info['symbol']
    expiry = underlying_info['expiry']
    match = INSTRUMENTS_DF[(INSTRUMENTS_DF['Symbol'] == symbol) & (INSTRUMENTS_DF['strike'] == strike) & (
            INSTRUMENTS_DF['option_type'] == 'PE') & (INSTRUMENTS_DF['expiry'] == expiry)]
    if match.empty: return go.Figure()

    # --- NEW: Extract Lot Size ---
    try:
        lot_size = int(match.iloc[0].get('lot_size', 1))
    except (ValueError, TypeError):
        lot_size = 1

    instrument_key = match.iloc[0]['instrument_key']
    df = fetch_historical_v3(instrument_key)

    quotes_data = fetch_market_quotes([instrument_key])
    q = quotes_data.get(instrument_key)
    if not q:
        for k, v in quotes_data.items():
            if v.get('instrument_token') == instrument_key or k.endswith(instrument_key.split('|')[-1]):
                q = v
                break

    if q:
        ohlc = q.get('ohlc', {})
        new_row = {
            'timestamp': datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0),
            'open': ohlc.get('open'),
            'high': ohlc.get('high'),
            'low': ohlc.get('low'),
            'close': q.get('last_price'),
            'volume': q.get('volume'),
            'oi': q.get('oi')
        }
        today_ts = pd.to_datetime(new_row['timestamp'])
        if not df.empty:
            last_ts = pd.to_datetime(df.iloc[-1]['timestamp'])
            if last_ts.date() == today_ts.date():
                for k, v in new_row.items():
                    df.iloc[-1, df.columns.get_loc(k)] = v
            else:
                df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        else:
            df = pd.DataFrame([new_row])

    if df.empty: return go.Figure()

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0, row_heights=[0.5, 0.25, 0.25])
    fig.add_trace(go.Candlestick(x=df['timestamp'], open=df['open'], high=df['high'], low=df['low'], close=df['close']),
                  row=1, col=1)
    fig.add_trace(go.Bar(x=df['timestamp'], y=df['volume']), row=2, col=1)
    fig.add_trace(go.Scatter(x=df['timestamp'], y=df['oi']), row=3, col=1)

    # --- NEW: Update Title with Lot Size ---
    fig.update_layout(title=f"PE - {symbol} {strike} {expiry} | Lot Size: {lot_size}", plot_bgcolor='white',
                      paper_bgcolor='white',
                      xaxis_rangeslider_visible=False, showlegend=False)
    fig.update_xaxes(showline=True, linewidth=1, linecolor='black', mirror=True)
    fig.update_yaxes(showline=True, linewidth=1, linecolor='black', mirror=True)

    # --- NEW: Display 1 Lot Value on the Right Axis ---
    latest_price = df['close'].iloc[-1]
    lot_value = latest_price * lot_size
    fig.add_hline(
        y=latest_price,
        line_dash="dot",
        line_color="royalblue",
        annotation_text=f"₹{lot_value:,.2f}",
        annotation_position="right",
        row=1, col=1
    )

    return _compact_fig(fig, right=62, title=f"PE {symbol} {strike} | Lot {lot_size}", legend=False)


@app.callback(
    Output('iv-graph', 'figure'),
    [Input('option-chain-store', 'data'),
     Input('exclude-itm-toggle', 'value')],
    [State('underlying-info-store', 'data')]
)
def update_iv_graph(chain_data, exclude_itm_val, underlying_info):
    if not chain_data or not underlying_info: return go.Figure()
    price = underlying_info.get('price', 0)

    # Check if the exclude ITM toggle is active
    exclude_itm = True if exclude_itm_val and True in exclude_itm_val else False

    iv_data = []
    for item in chain_data:
        strike = item['strike_price']

        # Skip deep OTM/ITM for cleaner graphing
        if price > 0 and (strike < price * 0.5 or strike > price * 1.5): continue

        call_greeks = item.get('call_options', {}).get('option_greeks')
        put_greeks = item.get('put_options', {}).get('option_greeks')

        call_iv = call_greeks.get('iv') if call_greeks else 0
        put_iv = put_greeks.get('iv') if put_greeks else 0

        # Filter out ITM options if the toggle is checked
        if exclude_itm and price > 0:
            if strike < price:  # Call is ITM
                call_iv = None
            if strike > price:  # Put is ITM
                put_iv = None

        # Only append data if at least one valid IV exists for the strike
        if (call_iv and call_iv > 0) or (put_iv and put_iv > 0):
            iv_data.append({
                'strike': strike,
                'call_iv': call_iv if (call_iv and call_iv > 0) else None,
                'put_iv': put_iv if (put_iv and put_iv > 0) else None
            })

    if not iv_data: return go.Figure(layout={'title': 'No IV data available'})

    df = pd.DataFrame(iv_data).sort_values('strike')
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(x=df['strike'], y=df['call_iv'], mode='lines+markers', name='Call IV', line=dict(color='red'),
                   connectgaps=True))
    fig.add_trace(
        go.Scatter(x=df['strike'], y=df['put_iv'], mode='lines+markers', name='Put IV', line=dict(color='green'),
                   connectgaps=True))
    fig.update_layout(title="IV of Put and Call", plot_bgcolor='white', paper_bgcolor='white',
                      margin=dict(t=30, b=20, l=20, r=20))

    # Optional: Add a vertical line for the underlying spot price
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
    if not chain_data or not underlying_info: return [go.Figure()] * 4
    price = underlying_info['price']
    metrics = []

    for item in chain_data:
        strike = item['strike_price']
        ce_md = item.get('call_options', {}).get('market_data', {})
        pe_md = item.get('put_options', {}).get('market_data', {})
        ce_oi, pe_oi = ce_md.get('oi', 0), pe_md.get('oi', 0)

        metrics.append({
            'strike': strike,
            'pcr': pe_oi / ce_oi if ce_oi > 0 else 0,
            'ce_oi': ce_oi,
            'pe_oi': pe_oi,
            'ce_oi_change': ce_oi - ce_md.get('prev_oi', 0),
            'pe_oi_change': pe_oi - pe_md.get('prev_oi', 0),
            'ce_vol': ce_md.get('volume', 0),  # Added Call Volume
            'pe_vol': pe_md.get('volume', 0)  # Added Put Volume
        })

    df = pd.DataFrame(metrics).sort_values('strike')

    fig_pcr = go.Figure(go.Scatter(x=df['strike'], y=df['pcr'], mode='lines+markers'))

    fig_oi = go.Figure([go.Bar(x=df['strike'], y=df['ce_oi'], name='CE OI', marker_color='red'),
                        go.Bar(x=df['strike'], y=df['pe_oi'], name='PE OI', marker_color='green')])

    fig_oi_chg = go.Figure([go.Bar(x=df['strike'], y=df['ce_oi_change'], name='CE OI Chg', marker_color='red'),
                            go.Bar(x=df['strike'], y=df['pe_oi_change'], name='PE OI Chg', marker_color='green')])

    # 1 & 2. Modified Volume graph to show separated Call (Red) and Put (Green) bars
    fig_vol = go.Figure([go.Bar(x=df['strike'], y=df['ce_vol'], name='CE Volume', marker_color='red'),
                         go.Bar(x=df['strike'], y=df['pe_vol'], name='PE Volume', marker_color='green')])

    for f, t in zip([fig_pcr, fig_oi, fig_oi_chg, fig_vol], ["PCR", "OI", "OI Change", "Volume"]):
        f.add_vline(x=price, line_dash="dash", line_color="black")

        # 3. Added 'Volume' to the barmode group list so bars don't overlap
        f.update_layout(title=t, plot_bgcolor='white', paper_bgcolor='white',
                        barmode='group' if t in ["OI", "OI Change", "Volume"] else None)
        f.update_xaxes(showline=True, linewidth=1, linecolor='black', gridcolor='lightgrey')
        f.update_yaxes(showline=True, linewidth=1, linecolor='black', gridcolor='lightgrey')

        # 4. Insert horizontal line at 10M mark strictly for the Volume graph
        if t == "Volume":
            f.add_hline(y=10000000, line_dash="dash", line_color="gray", annotation_text="10M",
                        annotation_position="top right")

    return tuple(_compact_fig(f_) for f_ in (fig_pcr, fig_oi, fig_oi_chg, fig_vol))


@app.callback(
    [Output('max-pain-display', 'children'), Output('underlying-price-display', 'children')],
    [Input('option-chain-store', 'data')],
    [State('underlying-info-store', 'data')]
)
def update_max_pain(chain_data, underlying_info):
    if not chain_data or not underlying_info: return "N/A", "Underlying: N/A"
    strikes = sorted([d['strike_price'] for d in chain_data])
    min_pain, max_pain_strike = float('inf'), strikes[0]
    for s in strikes:
        tp = sum(
            item.get('call_options', {}).get('market_data', {}).get('oi', 0) * (s - item['strike_price']) for item in
            chain_data if item['strike_price'] < s) + \
             sum(item.get('put_options', {}).get('market_data', {}).get('oi', 0) * (item['strike_price'] - s) for item
                 in chain_data if item['strike_price'] > s)
        if tp < min_pain: min_pain, max_pain_strike = tp, s
    price = underlying_info['price']
    diff = price - max_pain_strike
    arrow = "▲" if diff >= 0 else "▼"
    return html.Span(f"{max_pain_strike}"), html.Div([html.Span(f"Underlying: {price:.2f} "),
                                                      html.Span(f"({arrow} {abs(diff):.2f})",
                                                                style={'color': "green" if diff >= 0 else "red"})])


@app.callback(
    Output('merged-table-container', 'children'),
    [Input('option-chain-store', 'data'), Input('stream-qty-store', 'data')],
    [State('underlying-info-store', 'data'), State('layout-toggle-switch', 'value')]
)
def update_merged_table(chain_data, stream_rows, underlying_info, portrait_toggle=None):
    """One table: option chain columns + streaming Buy/Sell quantities, joined on strike."""
    if not chain_data: return "No data"
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

    # ---- ATM border (computed on option rows only) ----
    atm_idx = None
    if price > 0:
        for i in range(len(df) - 1):
            if df.iloc[i]['Strike'] >= price and df.iloc[i + 1]['Strike'] < price:
                atm_idx = i
                break

    # ---- join streaming buy/sell quantities ----
    qty_cols = ['CE_Buy', 'CE_Sell', 'PE_Buy', 'PE_Sell']
    eq_row = None
    for c in qty_cols: df[c] = None
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
        return current_val, current_val * 1000

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
    # act only on a REAL click (n_clicks > 0); a freshly created button reports n_clicks=None and must not start anything
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
    """App load / stock change / expiry change while NOT streaming: fetch the buy-sell quantities once, so the table,
    strike markers and equity triangle are filled. While streaming, the interval callback below does the fetching."""
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

    keys_to_fetch = [underlying_info['instrument_key']]
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

    quotes = {}
    for k, v in quotes_data.items():
        token = v.get('instrument_token')
        if token:
            quotes[token] = v
        quotes[k] = v

    stream_rows = []

    eq_key = underlying_info['instrument_key']
    q = quotes.get(eq_key)
    if not q:
        q = next((v for k, v in quotes_data.items() if v.get('instrument_token') == eq_key), None)

    if q:
        stream_rows.append({
            'Call Buy Qty': q.get('total_buy_quantity'),
            'Call Sell Qty': q.get('total_sell_quantity'),
            'Strike': f"EQ: {underlying_info['symbol']}",
            'Put Buy Qty': 0,
            'Put Sell Qty': 0
        })

    for s in strikes:
        row = {'Strike': s}
        ckey = next((k for k, v in strike_map.items() if v[0] == s and v[1] == 'CE'), None)
        pkey = next((k for k, v in strike_map.items() if v[0] == s and v[1] == 'PE'), None)

        cq = quotes.get(ckey)
        if not cq and ckey:
            cq = next((v for k, v in quotes_data.items() if v.get('instrument_token') == ckey), None)

        if cq:
            row['Call Buy Qty'] = cq.get('total_buy_quantity', 0)
            row['Call Sell Qty'] = cq.get('total_sell_quantity', 0)
        else:
            row['Call Buy Qty'] = 0
            row['Call Sell Qty'] = 0

        pq = quotes.get(pkey)
        if not pq and pkey:
            pq = next((v for k, v in quotes_data.items() if v.get('instrument_token') == pkey), None)

        if pq:
            row['Put Buy Qty'] = pq.get('total_buy_quantity', 0)
            row['Put Sell Qty'] = pq.get('total_sell_quantity', 0)
        else:
            row['Put Buy Qty'] = 0
            row['Put Sell Qty'] = 0

        stream_rows.append(row)

    options_rows = [r for r in stream_rows if not str(r['Strike']).startswith('EQ:')]
    eq_rows = [r for r in stream_rows if str(r['Strike']).startswith('EQ:')]

    df_options = pd.DataFrame(options_rows).sort_values('Strike', ascending=False)
    df_eq = pd.DataFrame(eq_rows)

    df_stream = pd.concat([df_eq, df_options], ignore_index=True)

    new_history = recorded_history
    if recording_active:
        timestamp = datetime.datetime.now().strftime('%H:%M:%S')
        snapshot = []
        for row in stream_rows:
            snapshot.append({
                'timestamp': timestamp,
                'Strike': row['Strike'],
                'Call Buy Qty': row.get('Call Buy Qty', 0),
                'Call Sell Qty': row.get('Call Sell Qty', 0),
                'Put Buy Qty': row.get('Put Buy Qty', 0),
                'Put Sell Qty': row.get('Put Sell Qty', 0),
                'Call OI': next(
                    (item.get('call_options', {}).get('market_data', {}).get('oi', 0) for item in chain_data if
                     item['strike_price'] == row['Strike']), 0) if isinstance(row['Strike'], (int, float)) else 0,
                'Put OI': next(
                    (item.get('put_options', {}).get('market_data', {}).get('oi', 0) for item in chain_data if
                     item['strike_price'] == row['Strike']), 0) if isinstance(row['Strike'], (int, float)) else 0,
            })
        new_history.append(snapshot)

    return df_stream.to_dict('records'), new_history


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

    data_list = []
    for snapshot in history:
        for row in snapshot:
            data_list.append(row)
    df = pd.DataFrame(data_list)

    fig = go.Figure()
    if ce_strikes:
        for s in ce_strikes:
            df_s = df[df['Strike'] == s]
            if not df_s.empty:
                fig.add_trace(
                    go.Scatter(x=df_s['timestamp'], y=df_s['Call OI'], mode='lines+markers', name=f'CE {s} OI'))

    if pe_strikes:
        for s in pe_strikes:
            df_s = df[df['Strike'] == s]
            if not df_s.empty:
                fig.add_trace(
                    go.Scatter(x=df_s['timestamp'], y=df_s['Put OI'], mode='lines+markers', name=f'PE {s} OI'))

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

    data_list = []
    for snapshot in history:
        for row in snapshot:
            data_list.append(row)
    df = pd.DataFrame(data_list)

    fig = go.Figure()
    if ce_strikes:
        for s in ce_strikes:
            df_s = df[df['Strike'] == s]
            if not df_s.empty:
                fig.add_trace(
                    go.Scatter(x=df_s['timestamp'], y=df_s['Call Buy Qty'], mode='lines', name=f'CE {s} Buy Qty',
                               line=dict(dash='solid')))
                fig.add_trace(
                    go.Scatter(x=df_s['timestamp'], y=df_s['Call Sell Qty'], mode='lines', name=f'CE {s} Sell Qty',
                               line=dict(dash='dot')))

    if pe_strikes:
        for s in pe_strikes:
            df_s = df[df['Strike'] == s]
            if not df_s.empty:
                fig.add_trace(
                    go.Scatter(x=df_s['timestamp'], y=df_s['Put Buy Qty'], mode='lines', name=f'PE {s} Buy Qty',
                               line=dict(dash='solid')))
                fig.add_trace(
                    go.Scatter(x=df_s['timestamp'], y=df_s['Put Sell Qty'], mode='lines', name=f'PE {s} Sell Qty',
                               line=dict(dash='dot')))

    fig.update_layout(title="Buy/Sell Qty Trend (Recorded)", plot_bgcolor='white', paper_bgcolor='white',
                      xaxis_title="Time", yaxis_title="Qty")
    fig.update_xaxes(showline=True, linewidth=1, linecolor='black', gridcolor='lightgrey')
    fig.update_yaxes(showline=True, linewidth=1, linecolor='black', gridcolor='lightgrey')
    return _compact_fig(fig)


# _____________________________________________________________________________________
# OPTION CHAIN ON THE PRICE CHART
#   right-edge OI profile (butterfly) on the candlestick's own price axis, with
#   * change-in-OI whiskers (CI style), * strike buy/sell markers, * equity buy/sell ratio triangle
# _____________________________________________________________________________________
OI_PROFILE_ENABLED = True   # False -> original chart, no live option chain on the price graph
OI_PROFILE_WIDTH = 0.17     # share of the chart width reserved (right edge) for the OI profile
OI_WALLS_TOP_N = 2          # how many biggest CE / PE OI strikes get a horizontal "wall" band
_OI_X_AXIS = 'x20'          # private overlay x-axis; it SHARES the candlestick y (price) axis
_EQ_Y_AXIS = 'y30'          # private overlay y-axis (fixed 0..1) that pins the equity triangle to the top/bottom
_PAD = 1.30                 # profile axis headroom (x longest bar) for whiskers + marker column
_MARKER_X = 1.14            # strike buy/sell marker column, in units of the longest bar
_EQ_TOP, _EQ_BOTTOM = 0.92, 0.08   # equity triangle position inside row 1 (fraction of its height)


def _num(v):
    try:
        f = float(v)
        return None if np.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _ratio_text(a, b):
    """Weaker side as a fraction of the stronger side, e.g. 0.5x, 0.66x (smaller number = bigger imbalance)."""
    hi, lo = max(a, b), min(a, b)
    return f"{round(lo / hi if hi else 0, 2):g}x"


def _equity_qty(stream_rows):
    """(buy, sell) total quantity of the underlying equity from the streaming rows, or None."""
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


def _add_option_chain_overlay(fig, df_stock, chain_data, underlying_info, stream_rows):
    """Draw today's option chain ON the stock-analysis chart (row 1), sharing its price axis.

    * butterfly OI profile at the right edge: CE OI to the left (red), PE OI to the right (green)
        - bar length = OI, bar opacity = OI change %
        - whisker (like a confidence-interval bar) = PREVIOUS OI: it starts at the bar tip and runs to the
          previous level. Dark segment INSIDE the bar = OI added; black segment OUTSIDE the bar = OI lost.
        - hover = OI, prev OI, LTP, Delta, POP, buy/sell qty
    * strike buy/sell markers (outer columns): CE = up-triangle, PE = down-triangle, shown only when Buy qty > Sell qty
    * equity buy/sell triangle: green up-triangle at the TOP if buyers dominate, red down-triangle at the BOTTOM if
      sellers dominate; the number inside = weaker side / stronger side (e.g. 0.66x)
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
        # also hides a stale chain left over from the previously selected symbol)
        df = full[(full['strike'] >= lo - pad) & (full['strike'] <= hi + pad)].copy()
        if df.empty:
            return
        # longest bar INCLUDING previous OI, so outward (unwinding) whiskers always fit
        m = float(df[['ce_oi', 'pe_oi', 'ce_prev', 'pe_prev']].to_numpy(dtype=float).max())
        if m <= 0:
            return

        # -- leave empty room on the right of the candles for the profile (all rows share the x-range)
        x0, x1 = df_stock['TIMESTAMP'].min(), df_stock['TIMESTAMP'].max()
        extra = (x1 - x0) * (OI_PROFILE_WIDTH / (1 - OI_PROFILE_WIDTH)) * 1.08
        fig.update_xaxes(range=[x0 - (x1 - x0) * 0.01, x1 + extra])   # BEFORE the overlay axis is defined

        # -- private overlay axes. x = 0 sits at the centre of the profile zone; the zone spans OI_PROFILE_WIDTH.
        #    y30 is a fixed 0..1 axis over row 1 (domain copied from the price axis) for the equity triangle.
        centre = 1 - OI_PROFILE_WIDTH / 2
        ax_hi = m * _PAD
        ax_lo = -ax_hi * (centre / (1 - centre))   # puts x = 0 at `centre` of the width; +ax_hi is the right edge
        fig.update_layout(**{
            'xaxis' + _OI_X_AXIS[1:]: dict(overlaying='x', anchor='y', visible=False,
                                           range=[ax_lo, ax_hi], fixedrange=True),
            'yaxis' + _EQ_Y_AXIS[1:]: dict(overlaying='y', anchor='x', visible=False, range=[0, 1],
                                           fixedrange=True, domain=list(fig.layout.yaxis.domain)),
            'margin': dict(t=34)})   # room for the caption above the profile

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

            # change-in-OI whiskers: from the bar tip to the previous OI level, cap at the previous level
            dark = '#8b0000' if is_call else '#006400'
            for amount, colour, inward in ((np.clip(oi - prev, 0, None), dark, True),
                                           (np.clip(prev - oi, 0, None), '#111111', False)):
                mask = amount > 0
                if not mask.any():
                    continue
                zero = np.zeros(int(mask.sum()))
                toward_plus = (inward == is_call)   # CE inward / PE outward point to +x; the other two to -x
                fig.add_trace(go.Scatter(
                    x=(sign * oi)[mask], y=strikes[mask], mode='markers', showlegend=False, hoverinfo='skip',
                    marker=dict(size=2, color=colour),
                    error_x=dict(type='data', symmetric=False,
                                 array=amount[mask] if toward_plus else zero,
                                 arrayminus=zero if toward_plus else amount[mask],
                                 color=colour, thickness=2.2, width=5),
                    xaxis=_OI_X_AXIS, yaxis='y'))

            # strike buy/sell marker: only where Buy qty > Sell qty (needs the stream to be running)
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

        # -- equity buy/sell triangle with the ratio inside (top = buyers dominate, bottom = sellers dominate)
        eq = _equity_qty(stream_rows)
        if eq and eq[0] != eq[1]:
            eb, es = eq
            buy_dom = eb > es
            sym = (underlying_info or {}).get('symbol', 'EQ')
            fig.add_trace(go.Scatter(
                x=[0], y=[_EQ_TOP if buy_dom else _EQ_BOTTOM], mode='markers+text',
                marker=dict(symbol='triangle-up' if buy_dom else 'triangle-down', size=46,
                            color='#1a9850' if buy_dom else '#d73027', line=dict(color='black', width=1.2),
                            opacity=0.95),
                text=[f"<b>{_ratio_text(eb, es)}</b>"], textposition='middle center',
                textfont=dict(color='white', size=10),
                hovertext=[f"<b>{sym} (equity)</b><br>Buy qty {eb:,.0f}<br>Sell qty {es:,.0f}<br>"
                           f"{'Sell/Buy' if buy_dom else 'Buy/Sell'} = {_ratio_text(eb, es)}"],
                hoverinfo='text', showlegend=False, name='EQ buy/sell',
                xaxis=_OI_X_AXIS, yaxis=_EQ_Y_AXIS))

        # -- walls: biggest OI strikes drawn across the whole price chart (behind the candles)
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

        # -- max pain + spot
        mp = _max_pain_strike(full)
        fig.add_hline(y=mp, row=1, col=1, line_dash='dot', line_color='purple', line_width=1.5,
                      annotation_text=f"Max Pain {mp:g}", annotation_position='bottom left',
                      annotation_font_size=9, annotation_font_color='purple')
        spot = _num((underlying_info or {}).get('price')) or ltp
        fig.add_hline(y=spot, row=1, col=1, line_dash='dash', line_color='black', line_width=1)

        # -- caption above the profile
        fig.add_annotation(x=centre, y=1.0, xref='paper', yref='paper', yanchor='bottom', yshift=13,
                           showarrow=False, font=dict(size=9),
                           text="<span style='color:#d62728'><b>◄ CE OI</b></span> | "
                                "<span style='color:#2ca02c'><b>PE OI ►</b></span>")
        fig.add_annotation(x=centre, y=1.0, xref='paper', yref='paper', yanchor='bottom', showarrow=False,
                           font=dict(size=8, color='#666'),
                           text="whisker = prev OI · ▲▼ = buy &gt; sell · big ▲▼ = equity (weak/strong)")
    except Exception as e:  # never let the overlay break the stock chart
        print(f"Option-chain overlay skipped: {e}")


# _____________________________________________________________________________________
# SUPPORT / RESISTANCE LINES  = classic pivot points (P, R1, S1) of the previous Day / Week / Month
# _____________________________________________________________________________________
PIVOT_LEVELS = ("R1", "P", "S1")      # levels drawn; any of "R3", "R2", "R1", "P", "S1", "S2", "S3" can be added here
# per timeframe: (line dash, width, {level kind: colour}) - colour = kind (R red / P blue / S green), weight = timeframe
SR_STYLES = {
    "D": ("dot",   1.3, {"R": "#ef9a9a", "P": "#90a4ae", "S": "#81c784"}),
    "W": ("dash",  1.7, {"R": "#e53935", "P": "#1e88e5", "S": "#43a047"}),
    "M": ("solid", 2.3, {"R": "#8e0000", "P": "#0d47a1", "S": "#0b5d1e"}),
}
_SR_FREQ = {"D": "D", "W": "W", "M": "M"}


def _sr_levels(df_full, tf):
    """Classic (floor-trader) pivot points of the previous COMPLETED day / week (Mon-Sun) / calendar month, for every
    candle plus one extra row for the NEXT session:
        P = (H + L + C) / 3      R1 = 2P - L      S1 = 2P - H
        R2 = P + (H - L)         S2 = P - (H - L)
        R3 = H + 2(P - L)        S3 = L - 2(H - P)
    H / L / C = high / low / close of that previous period. Computed on the full history, so the first visible candle
    already has its levels. Returns a DataFrame [TIMESTAMP, P, R1, S1, R2, S2, R3, S3] (last row = level now in force)."""
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
    pos = agg.index.searchsorted(pd.PeriodIndex(dates, freq=freq)) - 1      # last period strictly before each date
    ok = pos >= 0
    take = np.clip(pos, 0, None)
    out = {"TIMESTAMP": dates}
    for name, arr in by_period.items():
        out[name] = np.where(ok, arr[take], np.nan)
    return pd.DataFrame(out)


def _draw_sr_levels(fig, df_stock, sr_full, chain_data):
    """Pivot levels as step lines on the price chart (row 1): a level is held through its period; each line is labelled
    with the level in force for the next session, e.g. 'W-R1 1,234.5' (D = daily, W = weekly, M = monthly)."""
    if not sr_full:
        return
    x0, x1 = df_stock['TIMESTAMP'].iloc[0], df_stock['TIMESTAMP'].iloc[-1]
    x_end = None
    if OI_PROFILE_ENABLED and chain_data:        # run the lines on into the strip reserved for the OI profile
        x_end = x1 + (x1 - x0) * (OI_PROFILE_WIDTH / (1 - OI_PROFILE_WIDTH)) * 1.08
    for tf in ("D", "W", "M"):
        lv = sr_full.get(tf)
        if lv is None or lv.empty:
            continue
        lv = lv[lv["TIMESTAMP"] >= x0].reset_index(drop=True)
        if lv.empty:
            continue
        nxt = lv["TIMESTAMP"].iloc[-1]
        dash, width, colours = SR_STYLES[tf]
        line_df = lv
        if x_end is not None and x_end > nxt:
            line_df = pd.concat([lv, lv.iloc[[-1]].assign(TIMESTAMP=x_end)], ignore_index=True)
        for level in PIVOT_LEVELS:
            if level not in lv.columns:
                continue
            name, colour = f"{tf}-{level}", colours[level[0]]
            fig.add_trace(go.Scatter(x=line_df["TIMESTAMP"], y=line_df[level], mode="lines", name=name,
                                     line=dict(color=colour, width=width, dash=dash, shape="hv"), showlegend=False,
                                     hovertemplate=name + " %{y:,.2f}<extra></extra>"), row=1, col=1)
            last = lv[level].iloc[-1]
            if pd.notna(last):
                fig.add_trace(go.Scatter(x=[nxt], y=[last], mode="text", text=[f"{name} {last:,.1f}"],
                                         textposition="top left", textfont=dict(size=9, color=colour),
                                         showlegend=False, hoverinfo="skip"), row=1, col=1)


def _empty_fig(message):
    fig = go.Figure()
    fig.add_annotation(text=message, showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
                       font=dict(size=14, color="#888"))
    fig.update_layout(xaxis=dict(visible=False), yaxis=dict(visible=False), plot_bgcolor="white",
                      paper_bgcolor="white", margin=dict(l=10, r=10, t=10, b=10))
    return fig


# _____________________________________________________________________________________
# DISPLAY Graph
# _____________________________________________________________________________________
@app.callback(
    Output('graph_31', 'figure'),
    [Input("data_source_select", "value"),
     Input('expiry-dropdown', 'value'),
     Input('symbol-dropdown', 'value'),
     Input('dropdown_n_days', 'value'),
     Input('input_short_SMA', 'value'),
     Input('input_medium_SMA', 'value'),
     Input('input_long_SMA', 'value'),
     Input('my-range-slider', 'value'),
     Input('option-chain-store', 'data'),            # live chain -> OI profile on the price chart
     Input('stream-qty-store', 'data'),              # live buy/sell quantities -> markers
     Input('ma-lines-select', 'value'),              # which moving-average lines are shown
     Input('sr-levels-select', 'value'),             # support / resistance: D / W / M (multi-select)
     Input('local-data-file', 'value'),              # file used when the data source is 'Local CSV'
     Input('btn-local-refresh', 'n_clicks'),         # re-read that file now
     ],
    [State('underlying-info-store', 'data')])
def update_graph_31(data_source, dropdown_exp_value, dropdown_value, dropdown_n_days_value,
                    short_sma, medium_sma, long_sma, graph_height, chain_data=None, stream_rows=None,
                    ma_lines=None, sr_levels=None, local_file=None, local_refresh=None, underlying_info=None):
    if not dropdown_exp_value or not dropdown_value:
        raise dash.exceptions.PreventUpdate
    expiry_date = datetime.datetime.strptime(dropdown_exp_value, "%Y-%m-%d").date()
    if data_source == "bq":
        if dropdown_value in STOCK_DATA_CACHE:
            # Load instantly from RAM if the stock was already queried
            df_stock = STOCK_DATA_CACHE[dropdown_value].copy()
        else:
            # Import Data from Big Query with a fixed max history (150 days) to optimize parameter tweaks
            # _______________________________________________________________________________________________________________
            sql_stock = f"""
                SELECT TIMESTAMP, CUR_FUT_EXPIRY_DT,NEAR_FUT_EXPIRY_DT,
                                    SYMBOL, EQ_OPEN_PRICE, EQ_HIGH_PRICE, EQ_LOW_PRICE, EQ_CLOSE_PRICE,
                                    EQ_TTL_TRD_QNTY, EQ_DELIV_QTY, EQ_DELIV_PER, EQ_QT,
                                    CUR_PE_STRIKE_PR_OIMAX, CUR_PE_STRIKE_PR_10MVOL,
                                    CUR_CE_STRIKE_PR_OIMAX, CUR_CE_STRIKE_PR_10MVOL,
                                    NEAR_CE_STRIKE_PR_OIMAX, NEAR_CE_STRIKE_PR_10MVOL,
                                    NEAR_PE_STRIKE_PR_OIMAX, NEAR_PE_STRIKE_PR_10MVOL,
                                    CUR_PE_OI_SUM, CUR_CE_OI_SUM,
                                    EQ_CHG_PER, FUT_COI, FUT_BUILD_UP,FUT_PRICE_COL, FUT_COI_EXPLOSION_COL,
                                    CUR_PCR, NEAR_PCR, BAR, QTCO0321, QTCO0321COL
                FROM `phrasal-fire-373510.Big_Bull_Analysis.Master_Data_Equity`
                WHERE SYMBOL = '{dropdown_value}'
                ORDER BY TIMESTAMP DESC LIMIT 150
            """
            df_stock = bq_client.query(sql_stock).to_dataframe()
            STOCK_DATA_CACHE[dropdown_value] = df_stock.copy()
    else:
        # Import Data from Local Machine
        # _______________________________________________________________________________________________________________
        if 'btn-local-refresh' in str(dash.callback_context.triggered):
            _LOCAL_DATA_CACHE.clear()              # refresh button: force a re-read from disk
        local_df = load_local_equity_data(local_file)
        if local_df is None or 'SYMBOL' not in local_df.columns:
            return _empty_fig("Local CSV: choose a data file in  ⚙ Settings ▸ Chart Settings ▸ Data source")
        df_stock = local_df[local_df['SYMBOL'] == dropdown_value].copy()
        if df_stock.empty:
            return _empty_fig(f"{dropdown_value} is not in {local_file}")

    df_stock.style.format({"TIMESTAMP": lambda t: t.strftime("%Y-%m-%d")})
    df_stock['TIMESTAMP'] = pd.to_datetime(df_stock['TIMESTAMP'], dayfirst=True, format='mixed')
    df_stock = df_stock.sort_values(by='TIMESTAMP', ascending=True)

    # Moving averages and support / resistance levels use the FULL history (before the 'Days' slice), so the lines
    # already start at the first visible candle.
    ma_periods = {k: max(1, int(p or d_)) for k, p, d_ in
                  (("short", short_sma, 5), ("medium", medium_sma, 20), ("long", long_sma, 50))}
    for _k, _p in ma_periods.items():
        df_stock[f"MA_{_k}"] = df_stock["EQ_CLOSE_PRICE"].rolling(_p).mean()
    sr_full = {_tf: _sr_levels(df_stock, _tf) for _tf in (sr_levels or []) if _tf in SR_STYLES}

    # In-memory slice based on user choice (extremely fast)
    df_stock = df_stock.tail(dropdown_n_days_value)
    df_stock["BAR"] = df_stock["BAR"].astype(int)
    df_stock = df_stock.reset_index()

    # (Price / Volume / Q/T trend indicator rows were removed from the chart)

    # try:
    df_10M_VOL = df_stock[["TIMESTAMP", "CUR_FUT_EXPIRY_DT", "NEAR_FUT_EXPIRY_DT",
                           "EQ_HIGH_PRICE", "EQ_LOW_PRICE",
                           "CUR_PE_STRIKE_PR_10MVOL", "CUR_CE_STRIKE_PR_10MVOL",
                           "NEAR_PE_STRIKE_PR_10MVOL", "NEAR_CE_STRIKE_PR_10MVOL"]]
    columns = ["CUR_PE_STRIKE_PR_10MVOL", "CUR_CE_STRIKE_PR_10MVOL", "NEAR_PE_STRIKE_PR_10MVOL",
               "NEAR_CE_STRIKE_PR_10MVOL"]
    exploded = [
        df_10M_VOL[col].str.strip("[]").str.split(",", expand=True).stack().rename(col)
        for col in columns
    ]
    exploded = pd.concat(exploded, axis=1).droplevel(-1)
    df_10M_VOL = df_10M_VOL.drop(columns=columns)
    df_10M_VOL = df_10M_VOL.join(exploded)

    if data_source == "bq":
        df_10M_VOL = df_10M_VOL[df_10M_VOL.CUR_FUT_EXPIRY_DT == expiry_date]
    else:
        df_10M_VOL['CUR_FUT_EXPIRY_DT'] = pd.to_datetime(df_10M_VOL['CUR_FUT_EXPIRY_DT']).dt.date
        df_10M_VOL = df_10M_VOL[df_10M_VOL.CUR_FUT_EXPIRY_DT == expiry_date]

    df_10M_VOL = df_10M_VOL.astype(str).replace('nan', 'None')
    df_10M_VOL = df_10M_VOL[
        (df_10M_VOL["CUR_PE_STRIKE_PR_10MVOL"] != 'None') | (df_10M_VOL["CUR_CE_STRIKE_PR_10MVOL"] != 'None')]

    if not df_10M_VOL.empty:
        df_10M_VOL["ENTRY_BO"] = float(df_10M_VOL['EQ_HIGH_PRICE'].iloc[0])
        df_10M_VOL["ENTRY_BD"] = float(df_10M_VOL['EQ_LOW_PRICE'].iloc[0])
    else:
        df_10M_VOL["ENTRY_BO"] = 0
        df_10M_VOL["ENTRY_BD"] = 0

    if graph_height[0] == 800:
        row_height_values = [0.62, 0.10, 0.09, 0.09, 0.03, 0.07]
    else:
        row_height_values = [0.58, 0.11, 0.10, 0.09, 0.03, 0.09]

    fig = make_subplots(
        rows=6, cols=1,
        row_heights=row_height_values,
        specs=[[{}], [{"secondary_y": True}], [{}], [{}], [{}], [{"secondary_y": True}]],
        print_grid=False, shared_xaxes=True, horizontal_spacing=0.05, vertical_spacing=0)

    config = dict({'scrollZoom': True})
    fig.update_layout(paper_bgcolor='rgb(255,255,255)', plot_bgcolor='rgb(255,255,255)', autosize=True, height=None)
    fig.update_layout(margin=dict(r=2, t=2, b=2, l=2))
    fig.update_xaxes(showline=True, linewidth=2, linecolor='black')
    fig.update_yaxes(mirror="ticks", side='right')
    fig.update_yaxes(showticklabels=False, row=5, col=1)
    fig.update_layout(yaxis3=dict(showticklabels=False), yaxis6=dict(showticklabels=False),
                      yaxis8=dict(showticklabels=False))
    fig.update_layout(dragmode='drawline', newshape_line_color='cyan')
    fig.update_layout(showlegend=False)
    fig.update_layout(xaxis1=dict(rangeslider_visible=False))
    # include Equity candlestick without rangeselector
    fig.add_trace(go.Candlestick(x=df_stock['TIMESTAMP'],
                                 open=df_stock['EQ_OPEN_PRICE'], high=df_stock['EQ_HIGH_PRICE'],
                                 low=df_stock['EQ_LOW_PRICE'], close=df_stock['EQ_CLOSE_PRICE'], name='Price'),
                  row=1, col=1)
    fig.add_trace(
        go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock['CUR_CE_STRIKE_PR_OIMAX'], mode='lines+markers',
                   name='Resistance'),
        row=1, col=1)
    fig.add_trace(
        go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock['CUR_PE_STRIKE_PR_OIMAX'], mode='lines+markers',
                   name='Support'),
        row=1, col=1)
    opt_vol_pb_mode_status = "True"
    if opt_vol_pb_mode_status == 'True':
        fig.add_trace(
            go.Scatter(x=df_10M_VOL['TIMESTAMP'], y=df_10M_VOL['CUR_CE_STRIKE_PR_10MVOL'], mode='markers',
                       marker=dict(size=10, symbol=5, color='green'), name='CE Buildup'),
            row=1, col=1)
        fig.add_trace(
            go.Scatter(x=df_10M_VOL['TIMESTAMP'], y=df_10M_VOL['CUR_PE_STRIKE_PR_10MVOL'], mode='markers',
                       marker=dict(size=10, symbol=6, color='red'), name='PE Buildup'),
            row=1, col=1)
    # Moving-average lines (shown / hidden and sized in Chart Settings)
    for _k, _colour, _w in (("short", '#1f77b4', 1.2), ("medium", '#ff7f0e', 1.6), ("long", '#9467bd', 2.0)):
        if _k in (ma_lines or []):
            fig.add_trace(go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock[f"MA_{_k}"], mode='lines',
                                     line=dict(color=_colour, width=_w), name=f"{ma_periods[_k]} SMA Close",
                                     hovertemplate=f"{ma_periods[_k]} SMA %{{y:,.2f}}<extra></extra>"), row=1, col=1)
    # Add ENTRY Lines.....................
    fig.add_trace(
        go.Scatter(x=df_10M_VOL['TIMESTAMP'], y=df_10M_VOL['ENTRY_BO'], mode='lines', name='Entry',
                   line=dict(color='black')),
        row=1, col=1)
    # Add SL Lines
    fig.add_trace(
        go.Scatter(x=df_10M_VOL['TIMESTAMP'], y=df_10M_VOL['ENTRY_BD'], mode='lines', name='SL',
                   line=dict(color='black')),
        row=1, col=1)
    # Support / Resistance = pivot points (P, R1, S1) of the previous Day / Week / Month, selected in the chart header
    _draw_sr_levels(fig, df_stock, sr_full, chain_data)
    # Add Volume as Subplot
    fig.add_trace(
        go.Bar(x=df_stock['TIMESTAMP'], y=df_stock['EQ_TTL_TRD_QNTY'], name='Volume', offsetgroup=1),
        row=2, col=1)
    # Add 20 SMA to Volume Subplot
    fig.add_trace(go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock.EQ_TTL_TRD_QNTY.rolling(20).mean(), name='20SMA Vol'),
                  row=2, col=1)
    fig.add_trace(
        go.Bar(x=df_stock['TIMESTAMP'], y=df_stock['EQ_DELIV_QTY'], name='Del Qty', offsetgroup=1),
        row=2, col=1)
    # Add Delivery% as Subplot
    fig.add_trace(
        go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock['EQ_DELIV_PER'],
                   line=dict(color='firebrick', width=2, dash='dot'),
                   name='Del%'),
        row=2, col=1, secondary_y=True)
    # Add QT as Subplot
    fig.add_trace(go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock['EQ_QT'], name='Q/T'),
                  row=3, col=1)
    # Add 1SD Mark to Qt Subplot
    fig.add_trace(
        go.Scatter(x=df_stock['TIMESTAMP'], y=(df_stock.EQ_QT.rolling(20).mean() + df_stock.EQ_QT.rolling(20).std()),
                   name='1SD Q/T'),
        row=3, col=1)
    # QT 3-21 Crossover Indicator
    fig.add_trace(
        go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock['QTCO0321'], mode='markers',
                   marker=dict(size=10, symbol=5, color=df_stock['QTCO0321COL']), name='QT Ind'),
        row=3, col=1)
    # Add COI to Subplot
    fig.add_trace(
        go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock['FUT_COI'], name='COI', mode='lines+markers',
                   marker=dict(size=10, color=df_stock['FUT_PRICE_COL'])),
        row=4, col=1)
    fig.add_trace(
        go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock['BAR'], mode='markers',
                   marker=dict(size=10, symbol=1, color=df_stock['FUT_COI_EXPLOSION_COL'])),
        row=5, col=1)
    # Add Delivery Quantity Change Subplot
    fig.add_trace(
        go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock['CUR_PCR'], name='PCR'),
        row=6, col=1)
    # Add Current Expiry PUT Total
    fig.add_trace(
        go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock['CUR_PE_OI_SUM'],
                   line=dict(color='green', width=2, dash='dot'),
                   name='Cur PE'),
        row=6, col=1, secondary_y=True)
    # Add Current Expiry CALL Total
    fig.add_trace(
        go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock['CUR_CE_OI_SUM'],
                   line=dict(color='red', width=2, dash='dot'),
                   name='Cur CE'),
        row=6, col=1, secondary_y=True)
    # # Add Annonation
    cur_position = Expiry_Date_Monthly[Expiry_Date_Monthly['NEAR_FUT_EXPIRY_DT'] == dropdown_exp_value].index[0]
    cur_position = int(cur_position)
    cur_expiry_date = Expiry_Date_Monthly['NEAR_FUT_EXPIRY_DT'].iloc[cur_position]
    prev_expiry_date = Expiry_Date_Monthly['NEAR_FUT_EXPIRY_DT'].iloc[cur_position + 1]
    fig.add_vline(x=cur_expiry_date)
    fig.add_vline(x=prev_expiry_date)

    # option chain on the price chart (OI profile, walls, max pain, spot)
    _add_option_chain_overlay(fig, df_stock, chain_data, underlying_info, stream_rows)

    # edit axis labels
    fig['layout']['yaxis']['title'] = 'Equity OHCL'
    fig['layout']['yaxis2']['title'] = 'Volume'
    fig['layout']['yaxis4']['title'] = 'Q/T'
    fig['layout']['yaxis5']['title'] = 'COI'
    fig['layout']['yaxis7']['title'] = 'PCR'
    LTP = df_stock['EQ_CLOSE_PRICE'].iloc[-1]
    LTP_PREV = df_stock['EQ_CLOSE_PRICE'].iloc[-2]
    PER_CHNG = ((LTP - LTP_PREV) / LTP * 100).round(1)
    fig.update_layout(
        xaxis_title=dropdown_value + str(" LTP: ") + str(LTP) + str(" Change: ") + str(PER_CHNG) + str("%"),
        uirevision=f"{dropdown_value}|{dropdown_exp_value}|{dropdown_n_days_value}")  # chain ticks keep zoom/drawings

    return fig


@app.callback(
    Output('otm-prices-graph', 'figure'),
    [Input('option-chain-store', 'data')],
    [State('underlying-info-store', 'data')]
)
def update_otm_prices_graph(chain_data, underlying_info):
    if not chain_data or not underlying_info:
        return go.Figure()

    price = underlying_info.get('price', 0)
    otm_calls = []
    otm_puts = []

    # --- Calculate Max Pain ---
    strikes = sorted([d['strike_price'] for d in chain_data])
    min_pain, max_pain_strike = float('inf'), strikes[0]

    for s in strikes:
        # Calculate pain for expiry at price 's'
        tp = sum(
            item.get('call_options', {}).get('market_data', {}).get('oi', 0) * (s - item['strike_price'])
            for item in chain_data if item['strike_price'] < s
        ) + sum(
            item.get('put_options', {}).get('market_data', {}).get('oi', 0) * (item['strike_price'] - s)
            for item in chain_data if item['strike_price'] > s
        )
        if tp < min_pain:
            min_pain, max_pain_strike = tp, s

    for item in chain_data:
        strike = item['strike_price']
        ce_price = item.get('call_options', {}).get('market_data', {}).get('ltp', 0)
        pe_price = item.get('put_options', {}).get('market_data', {}).get('ltp', 0)

        # OTM Logic
        if strike > price:  # OTM Call
            otm_calls.append({'strike': strike, 'price': ce_price, 'diff': strike - price})
        elif strike < price:  # OTM Put
            otm_puts.append({'strike': strike, 'price': pe_price, 'diff': price - strike})

    # Sort by proximity to ATM and take top 10
    df_calls = pd.DataFrame(otm_calls).sort_values('diff').head(10).sort_values('strike')
    df_puts = pd.DataFrame(otm_puts).sort_values('diff').head(10).sort_values('strike')

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df_calls['strike'], y=df_calls['price'], mode='lines+markers', name='OTM Calls'))
    fig.add_trace(go.Scatter(x=df_puts['strike'], y=df_puts['price'], mode='lines+markers', name='OTM Puts'))

    # --- Add Vertical Lines ---
    if price > 0:
        fig.add_vline(x=price, line_dash="dash", line_color="green", annotation_text="LTP",
                      annotation_position="top left")

    fig.add_vline(x=max_pain_strike, line_dash="dash", line_color="red", annotation_text="Max Pain",
                  annotation_position="top right")

    fig.update_layout(
        title="10 OTM Call & Put Prices",
        xaxis_title="Strike",
        yaxis_title="LTP",
        template="plotly_white",
        margin=dict(l=20, r=20, t=40, b=20)
    )
    return _compact_fig(fig, title="10 OTM Call & Put LTP")


# --- SHORTLIST CALLBACKS ---

# 1. Handle Modal Open/Close & Reset Notes
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

    # Only open the modal if the open button was really clicked (not merely created by the layout)
    if trigger_id == "btn-open-shortlist-modal":
        if open_clicks:
            return True, current_symbol, ""
        return is_open, dash.no_update, dash.no_update

    # Close it only when Save or Cancel was really clicked
    if (trigger_id == "btn-close-shortlist-modal" and close_clicks) or \
            (trigger_id == "btn-save-shortlist-modal" and save_clicks):
        return False, dash.no_update, ""
    return is_open, dash.no_update, dash.no_update


# 2. Append New Symbol to the Table
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
        new_row = {"Symbol": symbol, "Notes": notes or ""}
        if current_data is None:
            current_data = []
        current_data.append(new_row)
        return current_data
    raise dash.exceptions.PreventUpdate


# 3. Save Table Data to GCS
@app.callback(
    Output("save-gcs-status", "children"),
    [Input("btn-save-shortlist-gcs", "n_clicks")],
    [State("shortlist-table", "data")],
    prevent_initial_call=True
)
def push_table_to_gcs(n_clicks, table_data):
    if n_clicks:
        success = save_shortlist_to_gcs(table_data)
        if success:
            # Timestamp to show it actually updated on multiple clicks
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            return f"✔ Saved to GCS at {ts}"
        else:
            return "❌ Failed to save."
    raise dash.exceptions.PreventUpdate


# --- ZERODHA ALERTS CALLBACKS ---

# UPDATED: Dynamic modal width & component visibility based on alert type
@app.callback(
    [Output("alert-left-col", "md"),
     Output("ato-basket-div", "style")],
    Input("alert-type", "value")
)
def toggle_ato_basket(alert_type):
    if alert_type == 'ato':
        return 6, {"display": "block"}
    return 12, {"display": "none"}


# NEW: Callback to generate the JSON Basket Structure
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
        "name": "alerts-basket",
        "type": "alert",
        "tags": [],
        "items": [{
            "type": "insert",
            "tradingsymbol": str(t_symbol).strip().upper(),
            "exchange": t_exchange or "NSE",
            "weight": 10000,
            "params": {
                "transaction_type": t_type,
                "product": t_product,
                "order_type": "MARKET",
                "validity": "DAY",
                "validity_ttl": 1,
                "quantity": int(t_qty) if t_qty else 1,
                "price": 0,
                "trigger_price": 0,
                "disclosed_quantity": 0,
                "last_price": 0,
                "variety": "regular",
                "tags": [],
                "squareoff": 0,
                "stoploss": 0,
                "trailing_stoploss": 0,
                "iceberg_legs": 0,
                "market_protection": -1
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
     State("underlying-info-store", "data")], # <-- NEW STATE ADDED
    prevent_initial_call=True
)
def handle_alert_modal_actions(create_clicks, modify_clicks, cancel_clicks, submit_clicks,
                               selected_rows, raw_alerts, edit_uuid,
                               name, exchange, symbol, attribute, operator,
                               val, alert_type, ato_basket, is_open, underlying_info): # <-- NEW ARGUMENT ADDED
    ctx = dash.callback_context
    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

    # Default return state (keeps UI exactly as it currently is)
    ret = [is_open, "", edit_uuid, name, exchange, symbol, attribute, operator, val, alert_type, ato_basket,
           selected_rows]

    # ignore "triggers" that are not real clicks (a button re-created by the layout has n_clicks=None)
    _clicks = {"btn-create-alert": create_clicks, "btn-modify-alert": modify_clicks,
               "btn-cancel-alert": cancel_clicks, "btn-submit-alert": submit_clicks}
    if not _clicks.get(trigger_id):
        return ret

    if trigger_id == "btn-create-alert":
        # Extract symbol and underlying price from the store
        current_sym = underlying_info.get("symbol", "") if underlying_info else ""
        current_price = underlying_info.get("price", "") if underlying_info else ""

        # Open for creating - prefill the name, symbol, and target value fields
        return [True, "", None, current_sym, "NSE", current_sym, "LastTradedPrice", ">=", current_price, "simple", "", selected_rows]

    elif trigger_id == "btn-modify-alert":
        if not selected_rows or not raw_alerts:
            ret[1] = "Please select an alert to modify."
            return ret

        # Pull the specific alert data using the selected row index
        idx = selected_rows[0]
        alert_data = raw_alerts[idx]

        # Populate the modal fields
        m_uuid = alert_data.get("uuid")
        m_name = alert_data.get("name", "")
        m_exchange = alert_data.get("lhs_exchange", "NSE")
        m_symbol = alert_data.get("lhs_tradingsymbol", "")
        m_attribute = alert_data.get("lhs_attribute", "LastTradedPrice")
        m_operator = alert_data.get("operator", ">=")
        m_val = alert_data.get("rhs_constant", "")
        m_type = alert_data.get("type", "simple")

        # Kite returns baskets as dicts, we need to cast to string for the textarea
        m_basket = alert_data.get("basket", "")
        if isinstance(m_basket, dict):
            m_basket = json.dumps(m_basket)

        return [True, "", m_uuid, m_name, m_exchange, m_symbol, m_attribute, m_operator, m_val, m_type, m_basket,
                selected_rows]

    elif trigger_id == "btn-submit-alert":
        # --- Form Validation Checks ---
        if not name or len(str(name).strip()) == 0:
            ret[1] = "Error: Alert Name is required."
            return ret

        if not symbol or len(str(symbol).strip()) == 0:
            ret[1] = "Error: Trading Symbol is required."
            return ret

        if val is None or str(val).strip() == "":
            ret[1] = "Error: Target Value is required."
            return ret
        # -----------------------------------

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
                # Validating JSON
                json.loads(ato_basket)
                # Adding stringified JSON directly for Zerodha's form URL-encoded payload expectation
                payload["basket"] = ato_basket
            except json.JSONDecodeError:
                ret[1] = "Error: Invalid JSON format in ATO basket."
                return ret

        try:
            if edit_uuid:
                # Use data=payload instead of json=payload for Form-Encoded
                res = requests.put(f"{KITE_ALERTS_URL}/{edit_uuid}", headers=KITE_HEADERS, data=payload)
            else:
                # Use data=payload instead of json=payload for Form-Encoded
                res = requests.post(KITE_ALERTS_URL, headers=KITE_HEADERS, data=payload)

            if res.status_code == 200:
                return [False, "Alert Placed!", None, name, exchange, symbol, attribute, operator, val, alert_type,
                        ato_basket, []]
            else:
                ret[1] = f"Error: {res.json().get('message', 'Failed')}"
                return ret

        except Exception as e:
            ret[1] = f"Exception: {str(e)}"
            return ret

    elif trigger_id == "btn-cancel-alert":
        # Cancel hides modal and clears selection
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
     Input("create-alert-modal", "is_open")],  # Refresh table automatically when modal closes
    [State("alerts-table", "data"),
     State("alerts-table", "data_previous"),
     State("alerts-raw-data-store", "data")]
)
def fetch_and_manage_alerts(refresh_clicks, data_timestamp, modal_is_open, current_data, previous_data, current_raw):
    ctx = dash.callback_context
    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0] if ctx.triggered else None

    # 1. Handle Delete Request
    if trigger_id == "alerts-table" and previous_data is not None:
        current_ids = {row.get('uuid') for row in current_data}
        for row in previous_data:
            uuid = row.get('uuid')
            if uuid and uuid not in current_ids:
                requests.delete(f"{KITE_ALERTS_URL}/{uuid}", headers=KITE_HEADERS)

    # Do not auto-refresh while the user is actively working in the modal
    if modal_is_open:
        raise dash.exceptions.PreventUpdate

    # 2. Fetch Latest State
    try:
        res = requests.get(KITE_ALERTS_URL, headers=KITE_HEADERS)
        if res.status_code == 200:
            alerts = res.json().get('data', [])
            table_data = []

            for a in alerts:
                table_data.append({
                    "uuid": a.get("uuid"),
                    "name": a.get("name"),
                    "symbol": a.get("lhs_tradingsymbol"),
                    "condition": f"{a.get('operator')} {a.get('rhs_constant')}",
                    "type": a.get("type")
                })
            return table_data, alerts, ""
        else:
            return current_data, current_raw, f"API Error: {res.status_code}"
    except Exception as e:
        return current_data, current_raw, f"Request failed: {e}"


# --- NEW CALLBACK TO DYNAMICALLY FILTER TRADING SYMBOL OPTIONS ---
@app.callback(
    Output("ato-tradingsymbol", "options"),
    [Input("alert-symbol", "value")]
)
def filter_trading_symbol_options(search_value):
    # If the text input is empty, return the entire default set of options
    if not search_value:
        return ZERODHA_SYMBOL_OPTIONS

    # Clean the search term and turn it into uppercase for case-insensitive matching
    search_term = str(search_value).strip().upper()

    # Filter choices that START WITH the entered value
    filtered_options = [
        opt for opt in ZERODHA_SYMBOL_OPTIONS
        if str(opt['value']).upper().startswith(search_term)
    ]

    return filtered_options


if __name__ == "__main__":
    app.run_server(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))