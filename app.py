"""360° Master dashboard - ONE page, ONE port, a web-app style top bar to switch between
Stock Analysis, Index Analysis and Index Performance.

    [360] 360° Analysis |  Stock Analysis   Index Analysis   Performance        ● Market hours  Mon 11:42:07 IST
    +--------------------------------------------------------------------------------------------------------+
    |  the selected page (an iframe filling the window)                                                      |
    +--------------------------------------------------------------------------------------------------------+

Keep these files in the same folder (they are used UNCHANGED apart from the performance page edits delivered with this):
    Analysis_Stock_Dashboard.py             -> /stock/
    Analysis_Index_Dashboard.py             -> /index/
    B2_2_Index_Performance_Dashboard_V2.py  -> /perf/

How it works:
    * every page is imported into this one process and mounted under its own URL prefix (the Dash path prefix is set
      through environment variables just before each import, then restored)
    * each page keeps its own layout, callbacks, tokens, shortlist, streaming and recording
    * a page is loaded into its iframe the first time you select it, then kept alive, so switching back and forth keeps
      its selections, streaming and recorded data
    * the Trend Analytics pop-out windows use root URLs (/popout/...); this file forwards them to whichever page is
      currently selected (remembered in a cookie)
    * if one page fails to start (token, BigQuery, network ...), the others still work and the failed one shows the
      error inside its iframe

Run:   python Analysis_Master_Dashboard.py        (PORT environment variable overrides 8080)
Prod:  gunicorn Analysis_Master_Dashboard:server
"""

# =====================================================================================
# 1. IMPORTS & CONFIGURATION
# =====================================================================================
import importlib
import json
import logging
import os
import sys
import time
import traceback
from html import escape as _esc

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, dcc, html
from flask import Response, request
from werkzeug.middleware.dispatcher import DispatcherMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("master360")

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# key -> python module (file name without .py) and nav label. The key is also the URL prefix (/<key>/).
DASHBOARDS = {
    "stock": {"module": "Analysis_Stock_Dashboard", "label": "📈  Stock Analysis"},
    "index": {"module": "Analysis_Index_Dashboard", "label": "📊  Index Analysis"},
    "perf": {"module": "B2_2_Index_Performance_Dashboard_V2", "label": "🏆  Performance"},
}
DEFAULT_VIEW = "stock"      # shown the very first time; afterwards the last selection is remembered by the browser
HEADER_PX = 48              # height of the top bar
VIEW_COOKIE = "dash_app"    # which page is on screen (read by the pop-out forwarding routes)


# =====================================================================================
# 2. LOAD THE PAGES (each mounted under /<key>/)
# =====================================================================================
_DASH_ENV_KEYS = ("DASH_URL_BASE_PATHNAME", "DASH_REQUESTS_PATHNAME_PREFIX", "DASH_ROUTES_PATHNAME_PREFIX")


def _failed_app(label, error_text):
    """Tiny WSGI app shown in the iframe when a page could not be imported."""
    body = (f"<html><body style='font-family:system-ui,sans-serif;margin:24px'>"
            f"<h3 style='color:#b02a37'>{_esc(label.strip())} failed to start</h3>"
            f"<p>The other pages are not affected. Fix the error below and restart this app.</p>"
            f"<pre style='background:#f8f9fa;border:1px solid #dee2e6;padding:12px;white-space:pre-wrap'>"
            f"{_esc(error_text)}</pre></body></html>").encode("utf-8")

    def app(environ, start_response):
        start_response("503 Service Unavailable", [("Content-Type", "text/html; charset=utf-8"),
                                                   ("Content-Length", str(len(body)))])
        return [body]
    return app


def _load_dashboard(key):
    """Import one page with its Dash URL prefix set to /<key>/ and return (module | None, wsgi_app).

    The dispatcher strips the prefix before the request reaches the page, so its ROUTES stay at '/' while the URLs the
    browser uses (REQUESTS prefix) carry /<key>/."""
    cfg = DASHBOARDS[key]
    prefix = f"/{key}/"
    saved = {k: os.environ.get(k) for k in _DASH_ENV_KEYS}
    os.environ.pop("DASH_URL_BASE_PATHNAME", None)
    os.environ["DASH_REQUESTS_PATHNAME_PREFIX"] = prefix
    os.environ["DASH_ROUTES_PATHNAME_PREFIX"] = "/"
    started = time.time()
    try:
        module = importlib.import_module(cfg["module"])
        sub_app = module.app
        if sub_app.config.requests_pathname_prefix != prefix:      # safety net; normally already set by the env vars
            sub_app.config.requests_pathname_prefix = prefix
        logger.info("Loaded %s as %s in %.1fs", cfg["module"], prefix, time.time() - started)
        return module, sub_app.server
    except Exception:
        err = traceback.format_exc()
        logger.error("Could not load %s:\n%s", cfg["module"], err)
        return None, _failed_app(cfg["label"], err)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


MODULES = {}     # key -> imported module (None if it failed to load)
MOUNTS = {}      # '/stock' -> WSGI app ...
for _key in DASHBOARDS:
    MODULES[_key], MOUNTS[f"/{_key}"] = _load_dashboard(_key)


# =====================================================================================
# 3. THE MASTER APP (top bar + iframes)  - created AFTER the prefix variables were restored, so it lives at '/'
# =====================================================================================
master = dash.Dash(__name__, external_stylesheets=[dbc.themes.FLATLY], title="360° Analysis")

master.index_string = """<!DOCTYPE html>
<html>
<head>
{%metas%}
<title>{%title%}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
{%favicon%}
{%css%}
<style>
    html, body { margin: 0; height: 100%; overflow: hidden; background: #f1f5f9;
                 font-family: 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif; }

    /* ---- top bar ---- */
    .topbar { box-sizing: border-box; height: __BAR_H__px; display: flex; align-items: center; gap: 28px; padding: 0 16px;
              background: linear-gradient(180deg, #111c33 0%, #0b1426 100%);
              border-bottom: 1px solid rgba(148,163,184,.18); box-shadow: 0 2px 10px rgba(2,6,23,.35);
              position: relative; z-index: 5; }
    .brand { display: flex; align-items: center; gap: 10px; user-select: none; }
    .brand-mark { width: 30px; height: 30px; border-radius: 8px; display: grid; place-items: center; color: #fff;
                  font-weight: 800; font-size: 11px; letter-spacing: -.02em;
                  background: linear-gradient(135deg, #38bdf8 0%, #6366f1 100%); box-shadow: 0 2px 8px rgba(56,189,248,.35); }
    .brand-name { color: #f8fafc; font-weight: 700; font-size: 15px; line-height: 1.1; letter-spacing: -.01em; }
    .brand-sub { color: #64748b; font-size: 10px; line-height: 1.1; margin-top: 3px; letter-spacing: .08em; text-transform: uppercase; }
    .brand-sep { width: 1px; height: 24px; background: rgba(148,163,184,.25); margin-left: -6px; }

    /* ---- navigation (radio buttons styled as app tabs) ---- */
    .app-nav { display: flex; align-items: stretch; height: __BAR_H__px; gap: 2px; }
    .app-nav .form-check { padding: 0; margin: 0; min-height: 0; display: flex; }
    .app-nav-item { display: flex; align-items: center; gap: 8px; margin: 0; padding: 0 16px; cursor: pointer;
                    color: #94a3b8; font-size: 13px; font-weight: 600; white-space: nowrap;
                    border-bottom: 2px solid transparent;
                    transition: color .15s ease, background-color .15s ease, border-color .15s ease; }
    .app-nav-item:hover { color: #e2e8f0; background: rgba(148,163,184,.08); }
    .app-nav-item.active { color: #fff; border-bottom-color: #38bdf8; background: rgba(56,189,248,.10); }
    .app-nav .btn-check:focus-visible + .app-nav-item { outline: 2px solid #38bdf8; outline-offset: -2px; }

    /* ---- right side: market-hours pill + IST clock ---- */
    .topbar-right { margin-left: auto; display: flex; align-items: center; gap: 12px; }
    .mkt-pill { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; border-radius: 999px;
                font-size: 11px; font-weight: 600; white-space: nowrap; }
    .mkt-pill::before { content: ''; width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
    .mkt-open { color: #4ade80; background: rgba(34,197,94,.14); }
    .mkt-open::before { animation: mkt-pulse 1.6s infinite; }
    .mkt-closed { color: #94a3b8; background: rgba(148,163,184,.14); }
    @keyframes mkt-pulse { 50% { opacity: .3; } }
    .clock { color: #cbd5e1; font-variant-numeric: tabular-nums; font-size: 12px; font-weight: 600; white-space: nowrap; }

    @media (max-width: 900px) { .brand-text, .brand-sep, .clock { display: none; } .topbar { gap: 12px; } }
    @media (max-width: 600px) { .app-nav-item { padding: 0 10px; font-size: 12px; } .mkt-pill { display: none; } }
</style>
</head>
<body>
{%app_entry%}
<footer>{%config%}{%scripts%}{%renderer%}</footer>
</body>
</html>""".replace("__BAR_H__", str(HEADER_PX))


# --- Pop-out windows: the pages open '/popout/<name>' (root URLs). Forward to the page that is on screen. ---
def _popout_owner():
    module = MODULES.get(request.cookies.get(VIEW_COOKIE, DEFAULT_VIEW))
    return module if module is not None and hasattr(module, "_popout_page") else None


@master.server.route("/popout-plotly.js")
def _master_popout_plotlyjs():
    owner = _popout_owner()
    return owner._popout_plotlyjs() if owner else Response("Page not loaded", status=404)


@master.server.route("/popout-data/<name>")
def _master_popout_data(name):
    owner = _popout_owner()
    return owner._popout_data(name) if owner else Response("Page not loaded", status=404)


@master.server.route("/popout/<name>")
def _master_popout_page(name):
    owner = _popout_owner()
    return owner._popout_page(name) if owner else Response("Page not loaded", status=404)


_FRAME_BASE = {"position": "absolute", "top": f"{HEADER_PX}px", "left": 0, "width": "100%",
               "height": f"calc(100vh - {HEADER_PX}px)", "border": 0, "background": "#fff"}
_FRAME_HIDDEN = {**_FRAME_BASE, "visibility": "hidden", "zIndex": 1}


def _frame(key):
    # src is filled in by the callback below the first time the page is selected (lazy load)
    return html.Iframe(id=f"frame-{key}", title=DASHBOARDS[key]["label"].strip(), style=_FRAME_HIDDEN)


master.layout = html.Div([
    html.Header([
        html.Div([
            html.Div("360", className="brand-mark"),
            html.Div([html.Div("360° Analysis", className="brand-name"),
                      html.Div("Markets workspace", className="brand-sub")], className="brand-text"),
        ], className="brand"),
        html.Div(className="brand-sep"),
        dbc.RadioItems(
            id="view-select",
            options=[{"label": cfg["label"], "value": key} for key, cfg in DASHBOARDS.items()],
            value=DEFAULT_VIEW,
            persistence=True, persistence_type="local",           # the browser remembers the last selection
            className="app-nav", inputClassName="btn-check",
            labelClassName="app-nav-item", labelCheckedClassName="active"),
        html.Div([
            html.Span("Off hours", id="mkt-pill", className="mkt-pill mkt-closed"),
            html.Span("", id="clock", className="clock"),
        ], className="topbar-right"),
        dcc.Interval(id="clock-tick", interval=1000),
    ], className="topbar"),
    *[_frame(key) for key in DASHBOARDS],
])


# Show the selected page's iframe (hidden ones keep their size, so their charts stay correctly sized), load it on first
# use, and remember the selection in a cookie for the pop-out forwarding routes above.
master.clientside_callback(
    """
    function(view) {
        const keys = __KEYS__;
        const nu = window.dash_clientside.no_update;
        if (keys.indexOf(view) < 0) { view = '__DEFAULT__'; }
        try { document.cookie = '__COOKIE__=' + view + '; path=/; SameSite=Lax'; } catch (e) {}
        const base = {position: 'absolute', top: '__H__px', left: 0, width: '100%',
                      height: 'calc(100vh - __H__px)', border: 0, background: '#fff'};
        const styles = keys.map(k => Object.assign({}, base,
            k === view ? {visibility: 'visible', zIndex: 2} : {visibility: 'hidden', zIndex: 1}));
        const srcs = keys.map((k, i) => (k === view && !arguments[1 + i]) ? k + '/' : nu);
        return styles.concat(srcs);
    }
    """.replace("__KEYS__", json.dumps(list(DASHBOARDS))).replace("__DEFAULT__", DEFAULT_VIEW)
       .replace("__COOKIE__", VIEW_COOKIE).replace("__H__", str(HEADER_PX)),
    [Output(f"frame-{k}", "style") for k in DASHBOARDS] + [Output(f"frame-{k}", "src") for k in DASHBOARDS],
    Input("view-select", "value"),
    [State(f"frame-{k}", "src") for k in DASHBOARDS]
)

# IST clock + market-hours pill (09:15-15:30, Mon-Fri; exchange holidays are not known here, so it only says hours)
master.clientside_callback(
    """
    function(n) {
        const parts = new Intl.DateTimeFormat('en-GB', {timeZone: 'Asia/Kolkata', weekday: 'short', hour: '2-digit',
                          minute: '2-digit', second: '2-digit', hour12: false}).formatToParts(new Date());
        const get = t => (parts.find(p => p.type === t) || {}).value || '';
        const wd = get('weekday');
        const hh = get('hour') === '24' ? '00' : get('hour');
        const mins = parseInt(hh, 10) * 60 + parseInt(get('minute'), 10);
        const weekend = (wd === 'Sat' || wd === 'Sun');
        const open = !weekend && mins >= 555 && mins < 930;
        return [wd + ' ' + hh + ':' + get('minute') + ':' + get('second') + ' IST',
                weekend ? 'Weekend' : (open ? 'Market hours' : 'Off hours'),
                'mkt-pill ' + (open ? 'mkt-open' : 'mkt-closed')];
    }
    """,
    [Output("clock", "children"), Output("mkt-pill", "children"), Output("mkt-pill", "className")],
    Input("clock-tick", "n_intervals")
)


# =====================================================================================
# 4. WSGI ENTRY POINT / RUN
# =====================================================================================
server = DispatcherMiddleware(master.server, MOUNTS)      # gunicorn Analysis_Master_Dashboard:server

if __name__ == "__main__":
    from werkzeug.serving import run_simple
    port = int(os.environ.get("PORT", 8080))
    logger.info("360° Master dashboard on http://localhost:%d  (%s)", port,
                "  ".join(f"{cfg['label'].strip()}: /{k}/" for k, cfg in DASHBOARDS.items()))
    run_simple("0.0.0.0", port, server, threaded=True, use_reloader=False)
