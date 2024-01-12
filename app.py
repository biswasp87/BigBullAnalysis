import dash  # pip install dash
import dash_bootstrap_components as dbc # pip install dash-bootstrap-components
# Code from: https://github.com/plotly/dash-labs/tree/main/docs/demos/multi_page_example1
import dash_auth
from dash import dcc, html
from dash.dependencies import Input, Output, State

app = dash.Dash(__name__, external_stylesheets=[dbc.themes.CERULEAN], use_pages=True)
auth = dash_auth.BasicAuth(
    app,
    {'biswasp87': 'hello1234',
     'pajaroloco': 'unsecreto',
     'test': 'test'}
)

navbar = dbc.Navbar(
    dbc.Container(
        [
            dbc.Row([
                dbc.Col([
                    # html.Img(src=dash.get_asset_url('logo2.png'), height="40px"),
                    dbc.NavbarBrand("Big Bull Analysis", className="ms-2")
                ],
                    width={"size": "auto"})
            ],
                align="center",
                className="g-0"),

            dbc.Row([
                dbc.Col([
                    dbc.Nav([
                        dbc.NavItem(dbc.NavLink("Home", href="/")),
                        dbc.NavItem(dbc.NavLink("FPI Investment", href="/a-fpi-investment")),
                        dbc.NavItem(dbc.NavLink("Scanner", href="/b-scanner")),
                        dbc.NavItem(dbc.DropdownMenu(
                            children=[
                                dbc.DropdownMenuItem("Nifty", href="/hownnlearns"),
                                dbc.DropdownMenuItem("Bank Nifty", href="/hownnlearns"),
                                dbc.DropdownMenuItem("Fin Nifty", href="/hownnlearns")
                            ],
                            nav=True,
                            in_navbar=True,
                            label="Index Analysis",
                        )),
                        # dbc.NavItem(dbc.NavLink("Settings", href="/dashboard")),
                        dbc.NavItem(dbc.DropdownMenu(
                            children=[
                                # dbc.DropdownMenuItem("FNO", header=True),
                                dbc.DropdownMenuItem("FNO Stock", href="/c-analysis-stocks-bq"),
                                dbc.DropdownMenuItem("FNO Option", href="/c1-stock-option-bq")
                            ],
                            nav=True,
                            in_navbar=True,
                            label="Stock Analysis",
                        )),
                        dbc.NavItem(dbc.NavLink("Paper Trading", href="/d-paper-trading")),
                    ],
                        navbar=True
                    )
                ],
                    width={"size": "auto"})
            ],
                align="center"),
            dbc.Col(dbc.NavbarToggler(id="navbar-toggler", n_clicks=0)),

            dbc.Row([
                dbc.Col(
                    dbc.Collapse(
                        dbc.Nav([
                            dbc.NavItem(dbc.NavLink(html.I(className="bi bi-github"),
                                                    href="https://github.com/siddharthajuprod07/algorithms/tree/master/plotly_deep_learning_app",
                                                    external_link=True)),
                            dbc.NavItem(
                                dbc.NavLink(html.I(className="bi bi bi-twitter"), href="https://twitter.com/splunk_ml",
                                            external_link=True)),
                            dbc.NavItem(dbc.NavLink(html.I(className="bi bi-youtube"),
                                                    href="https://www.youtube.com/channel/UC7J8myLv3tPabjeocxKQQKw",
                                                    external_link=True)),
                            dbc.Input(type="search", placeholder="Search"),
                            dbc.Button("Search", color="primary", className="ms-2", n_clicks=0),
                        ]
                        ),
                        id="navbar-collapse",
                        is_open=False,
                        navbar=True
                    )
                )
            ],
                align="center")
        ],
        fluid=True
    ),
    color="primary",
    dark=True
)


@dash.callback(
    Output("navbar-collapse", "is_open"),
    [Input("navbar-toggler", "n_clicks")],
    [State("navbar-collapse", "is_open")],
)
def toggle_navbar_collapse(n, is_open):
    if n:
        return not is_open
    return is_open

app.layout = dbc.Container([dcc.Store(id="df_indicator", data=[], storage_type='local'),
                            navbar, dash.page_container], fluid=True)

if __name__ == '__main__':
    app.run_server(debug=False, host="0.0.0.0", port=8080)