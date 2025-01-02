import dash
from openpyxl.styles.builtins import output

# To create meta tag for each page, define the title, image, and description.
dash.register_page(
    __name__,
    # path='/',
    title='Settings',
    name='Settings'
)
import dash_bootstrap_components as dbc
from dash import Dash, dcc, html, Input, Output, callback, State
from google.cloud import storage
import pandas as pd
from datetime import date, timedelta, datetime
import requests

# PART I: Defining Google Storage Client & Bucket
# ___________________________________________________________________________________________________________
storage_client = storage.Client()
bucket = storage_client.bucket('biswasp87')

# PART II: Preparing the Layout
# ___________________________________________________________________________________________________________


settings_first_card = [
    dbc.CardHeader("FUNCTION REPORT"),
    dbc.CardBody(
        [
            dbc.Row([
                dbc.Col([
                    dbc.Row([html.H5("FUNCTION", className="card-title")]),
                    dbc.Row([html.P(className="card-text", id='A1_Col1_L1_Para')]),
                    dbc.Row([html.P(className="card-text", id='A1_Col1_L2_Para')]),
                    dbc.Row([html.P(className="card-text", id='A1_Col1_L3_Para')])
                ],lg=5,xs=3),
                dbc.Col([
                    dbc.Row([html.H5("STATUS", className="card-title")]),
                    dbc.Row([html.P(className="card-text", id='A1_Col2_L1_Para')]),
                    dbc.Row([html.P(className="card-text", id='A1_Col2_L2_Para')]),
                    dbc.Row([html.P(className="card-text", id='A1_Col2_L3_Para')])
                ],lg=2,xs=3),
                dbc.Col([
                    dbc.Row([html.H5("DATE", className="card-title")]),
                    dbc.Row([html.P(className="card-text", id='A1_Col3_L1_Para')]),
                    dbc.Row([html.P(className="card-text", id='A1_Col3_L2_Para')]),
                    dbc.Row([html.P(className="card-text", id='A1_Col3_L3_Para')])
                ],lg=2,xs=3),
                dbc.Col(
                    [
                    dbc.Button("Update Now", color="primary", id='A1_button', className="btn btn-primary btn-sm")
                ],lg=2,xs=3,
                ),
            ]),
            html.Hr(),
            dbc.Row([
                dbc.Col([
                    dbc.Row([html.P(className="card-text", id='B1_Col1_L1_Para')])
                ],lg=5,xs=3),
                dbc.Col([
                    dbc.Row([html.P(className="card-text", id='B1_Col2_L1_Para')])
                ],lg=2 ,xs=3),
                dbc.Col([
                    dbc.Row([html.P(className="card-text", id='B1_Col3_L1_Para')])
                ],lg=2,xs=3),
                dbc.Col([
                    dbc.Button("Update Now", color="primary", id='B1_button', className="btn btn-primary btn-sm")
                ],lg=2,xs=3,
                ),
            ]),
            html.Hr(),
            dbc.Row([
                dbc.Col([
                    dbc.Row([html.P(className="card-text", id='B3_Col1_L1_Para')])
                ], lg=5, xs=3),
                dbc.Col([
                    dbc.Row([html.P(className="card-text", id='B3_Col2_L1_Para')])
                ], lg=2, xs=3),
                dbc.Col([
                    dbc.Row([html.P(className="card-text", id='B3_Col3_L1_Para')])
                ], lg=2, xs=3),
                dbc.Col([
                    dbc.Button("Update Now", color="primary", id='B3_button', className="btn btn-primary btn-sm")
                ], lg=2, xs=3,
                ),
            ]),
            html.Hr(),
            dbc.Row([
                dbc.Col([
                    dbc.Row([html.P(className="card-text", id='C1_Col1_L1_Para')])
                ], lg=5, xs=3),
                dbc.Col([
                    dbc.Row([html.P(className="card-text", id='C1_Col2_L1_Para')])
                ], lg=2, xs=3),
                dbc.Col([
                    dbc.Row([html.P(className="card-text", id='C1_Col3_L1_Para')])
                ], lg=2, xs=3),
                dbc.Col([
                    dbc.Button("Update Now", color="primary", id='C1_button', className="btn btn-primary btn-sm")
                ], lg=2, xs=3,
                ),
            ]),
            html.Hr(),
            dbc.Row([
                dbc.Col([
                    dbc.Row([html.P(className="card-text", id='C2_Col1_L1_Para')])
                ], lg=5, xs=3),
                dbc.Col([
                    dbc.Row([html.P(className="card-text", id='C2_Col2_L1_Para')])
                ], lg=2, xs=3),
                dbc.Col([
                    dbc.Row([html.P(className="card-text", id='C2_Col3_L1_Para')])
                ], lg=2, xs=3),
                dbc.Col([
                    dbc.Button("Update Now", color="primary", id='C2_button', className="btn btn-primary btn-sm")
                ], lg=2, xs=3,
                ),
            ]),
            html.Hr(),
            dbc.Row([
                dbc.Col([
                    dbc.Row([html.P(className="card-text", id='D1_Col1_L1_Para')])
                ], lg=5, xs=3),
                dbc.Col([
                    dbc.Row([html.P(className="card-text", id='D1_Col2_L1_Para')])
                ], lg=2, xs=3),
                dbc.Col([
                    dbc.Row([html.P(className="card-text", id='D1_Col3_L1_Para')])
                ], lg=2, xs=3),
                dbc.Col([
                    dbc.Button("Update Now", color="primary", id='D1_button', className="btn btn-primary btn-sm")
                ], lg=2, xs=3,
                ),
            ]),
            html.Hr(),
            dbc.Row([
                dbc.Col([
                    dbc.Row([html.P(className="card-text", id='D3_Col1_L1_Para')])
                ], lg=5, xs=3),
                dbc.Col([
                    dbc.Row([html.P(className="card-text", id='D3_Col2_L1_Para')])
                ], lg=2, xs=3),
                dbc.Col([
                    dbc.Row([html.P(className="card-text", id='D3_Col3_L1_Para')])
                ], lg=2, xs=3),
                dbc.Col([
                    dbc.Button("Update Now", color="primary", id='D3_button', className="btn btn-primary btn-sm")
                ], lg=2, xs=3,
                ),
            ]),
            html.Hr(),
            dbc.Row([
                dbc.Col([
                    dbc.Row([html.P(className="card-text", id='D4_Col1_L1_Para')])
                ], lg=5, xs=3),
                dbc.Col([
                    dbc.Row([html.P(className="card-text", id='D4_Col2_L1_Para')])
                ], lg=2, xs=3),
                dbc.Col([
                    dbc.Row([html.P(className="card-text", id='D4_Col3_L1_Para')])
                ], lg=2, xs=3),
                dbc.Col([
                    dbc.Button("Update Now", color="primary", id='D4_button', className="btn btn-primary btn-sm"),
                    dbc.Modal(
                        [
                            dbc.ModalHeader(dbc.ModalTitle("Scanner NIFTY 500"), close_button=True),
                            dbc.ModalBody("This function takes about 30 Minute to proceed. Do you still want to proceed?"),
                            dbc.ModalFooter(
                                dbc.Row([
                                    dbc.Col([
                                        dbc.Button(
                                            "Cancel",
                                            id="D4_button_modal_cancel",
                                            className="ms-auto",
                                            n_clicks=0,
                                        ),
                                    ]),
                                    dbc.Col([
                                        dbc.Button(
                                            "OK",
                                            id="D4_button_modal_ok",
                                            className="ms-auto",
                                            n_clicks=0,
                                        ),
                                    ]),
                                ])
                            ),
                        ],
                        id="D4_modal",
                        centered=True,
                        is_open=False,
                    ),
                ], lg=2, xs=3,
                ),
            ])
        ]
    ),
]


settings_first_row = dbc.Row([
    dbc.Col([
        dbc.Carousel(
            items=[
                {"key": "1", "src": "/assets/Image_1.jpg", "img_style": {"height": "650px"}},
                {"key": "2", "src": "/assets/Image_2.jpg", "img_style": {"height": "650px"}},
            ],
            controls=True,
            indicators=True,
        )
    ],lg=7,xs=12),
    dbc.Col([
        dbc.Card(settings_first_card, color="info", inverse=True),
    ], lg=5, xs=12)
])
content = html.Div(
    [
        html.Br(),
        settings_first_row,
    ],
)

layout = html.Div([content])
# # ___________________________________________________________________________________________________________
# # PART III: Defining the Callbacks to update the Card Details (Function A1)
# # ___________________________________________________________________________________________________________
@callback(
Output('A1_Col1_L1_Para','children'),
    Output('A1_Col1_L2_Para','children'),
    Output('A1_Col1_L3_Para','children'),
    Output('A1_Col2_L1_Para','children'),
    Output('A1_Col2_L2_Para','children'),
    Output('A1_Col2_L3_Para','children'),
    Output('A1_Col3_L1_Para','children'),
    Output('A1_Col3_L2_Para','children'),
    Output('A1_Col3_L3_Para','children'),
    Input('A1_button', 'n_clicks')
)
def update_function_a1(c1_clicks):
    function_A1_df = pd.read_csv("gs://bba_function_update_status/A1_EQ_FNO_Indices_Bhav_Copy_to_BQ.csv")
    A1_Col1_L1_Para_txt = function_A1_df["FUNCTION"].iloc[0]
    A1_Col1_L2_Para_txt = function_A1_df["FUNCTION"].iloc[1]
    A1_Col1_L3_Para_txt = function_A1_df["FUNCTION"].iloc[2]

    A1_Col2_L1_Para_txt = function_A1_df["STATUS"].iloc[0]
    A1_Col2_L2_Para_txt = function_A1_df["STATUS"].iloc[1]
    A1_Col2_L3_Para_txt = function_A1_df["STATUS"].iloc[2]

    A1_Col3_L1_Para_txt = function_A1_df["DATE"].iloc[0]
    A1_Col3_L2_Para_txt = function_A1_df["DATE"].iloc[1]
    A1_Col3_L3_Para_txt = function_A1_df["DATE"].iloc[2]

    if c1_clicks == 1:
        r = requests.post("https://asia-south1-phrasal-fire-373510.cloudfunctions.net/A1_EQ_FNO_Indices_Bhav_Copy_to_BQ")
        r_status = r.status_code

    return (A1_Col1_L1_Para_txt,A1_Col1_L2_Para_txt,A1_Col1_L3_Para_txt,
            A1_Col2_L1_Para_txt,A1_Col2_L2_Para_txt,A1_Col2_L3_Para_txt,
            A1_Col3_L1_Para_txt,A1_Col3_L2_Para_txt,A1_Col3_L3_Para_txt)

# # ___________________________________________________________________________________________________________
# # PART III: Defining the Callbacks to update the Card Details (Function B1)
# # ___________________________________________________________________________________________________________
@callback(
Output('B1_Col1_L1_Para','children'),
    Output('B1_Col2_L1_Para','children'),
    Output('B1_Col3_L1_Para','children'),
    Input('B1_button', 'n_clicks')
)
def update_function_b1(B1_clicks):
    function_B1_df = pd.read_csv("gs://bba_function_update_status/B1_EQ_FNO_OPT_Master_Data.csv")
    B1_Col1_L1_Para_txt = function_B1_df["FUNCTION"].iloc[0]
    B1_Col2_L1_Para_txt = function_B1_df["STATUS"].iloc[0]
    B1_Col3_L1_Para_txt = function_B1_df["DATE"].iloc[0]

    if B1_clicks == 1:
        r = requests.post("https://asia-south1-phrasal-fire-373510.cloudfunctions.net/B1_EQ_FNO_OPT_Master_Data")
        r_status = r.status_code

    return (B1_Col1_L1_Para_txt,B1_Col2_L1_Para_txt,B1_Col3_L1_Para_txt)

# # ___________________________________________________________________________________________________________
# # PART III: Defining the Callbacks to update the Card Details (Function B3)
# # ___________________________________________________________________________________________________________
@callback(
Output('B3_Col1_L1_Para','children'),
    Output('B3_Col2_L1_Para','children'),
    Output('B3_Col3_L1_Para','children'),
    Input('B3_button', 'n_clicks')
)
def update_function_b3(B3_clicks):
    function_B3_df = pd.read_csv("gs://bba_function_update_status/B3_Expiry_Wise_OPT_to_BQ.csv")
    B3_Col1_L1_Para_txt = function_B3_df["FUNCTION"].iloc[0]
    B3_Col2_L1_Para_txt = function_B3_df["STATUS"].iloc[0]
    B3_Col3_L1_Para_txt = function_B3_df["DATE"].iloc[0]

    if B3_clicks == 1:
        r = requests.post("https://asia-south1-phrasal-fire-373510.cloudfunctions.net/B3_Expiry_Wise_OPT_to_BQ")
        r_status = r.status_code

    return (B3_Col1_L1_Para_txt, B3_Col2_L1_Para_txt, B3_Col3_L1_Para_txt)

# # ___________________________________________________________________________________________________________
# # PART III: Defining the Callbacks to update the Card Details (Function C1)
# # ___________________________________________________________________________________________________________
@callback(
Output('C1_Col1_L1_Para','children'),
    Output('C1_Col2_L1_Para','children'),
    Output('C1_Col3_L1_Para','children'),
    Input('C1_button', 'n_clicks')
)
def update_function_c1(C1_clicks):
    function_C1_df = pd.read_csv("gs://bba_function_update_status/C1_BBA_Update_Watchlist.csv")
    C1_Col1_L1_Para_txt = function_C1_df["FUNCTION"].iloc[0]
    C1_Col2_L1_Para_txt = function_C1_df["STATUS"].iloc[0]
    C1_Col3_L1_Para_txt = function_C1_df["DATE"].iloc[0]

    if C1_clicks == 1:
        r = requests.post("https://asia-south1-phrasal-fire-373510.cloudfunctions.net/C1_BBA_Update_Watchlist")
        r_status = r.status_code

    return (C1_Col1_L1_Para_txt, C1_Col2_L1_Para_txt, C1_Col3_L1_Para_txt)

# # ___________________________________________________________________________________________________________
# # PART III: Defining the Callbacks to update the Card Details (Function C2)
# # ___________________________________________________________________________________________________________
@callback(
Output('C2_Col1_L1_Para','children'),
    Output('C2_Col2_L1_Para','children'),
    Output('C2_Col3_L1_Para','children'),
    Input('C2_button', 'n_clicks')
)
def update_function_c2(C2_clicks):
    function_C2_df = pd.read_csv("gs://bba_function_update_status/C2_BBA_Update_Expiry_Dates_Monthly.csv")
    C2_Col1_L1_Para_txt = function_C2_df["FUNCTION"].iloc[0]
    C2_Col2_L1_Para_txt = function_C2_df["STATUS"].iloc[0]
    C2_Col3_L1_Para_txt = function_C2_df["DATE"].iloc[0]

    if C2_clicks == 1:
        r = requests.post("https://asia-south1-phrasal-fire-373510.cloudfunctions.net/C2_BBA_Update_Expiry_Dates_Monthly")
        r_status = r.status_code

    return (C2_Col1_L1_Para_txt, C2_Col2_L1_Para_txt, C2_Col3_L1_Para_txt)

# # ___________________________________________________________________________________________________________
# # PART III: Defining the Callbacks to update the Card Details (Function D1)
# # ___________________________________________________________________________________________________________
@callback(
Output('D1_Col1_L1_Para','children'),
    Output('D1_Col2_L1_Para','children'),
    Output('D1_Col3_L1_Para','children'),
    Input('D1_button', 'n_clicks')
)
def update_function_d1(D1_clicks):
    function_D1_df = pd.read_csv("gs://bba_function_update_status/D1_BBA_Scanner_10m_opt_vol.csv")
    D1_Col1_L1_Para_txt = function_D1_df["FUNCTION"].iloc[0]
    D1_Col2_L1_Para_txt = function_D1_df["STATUS"].iloc[0]
    D1_Col3_L1_Para_txt = function_D1_df["DATE"].iloc[0]

    if D1_clicks == 1:
        r = requests.post("https://asia-south1-phrasal-fire-373510.cloudfunctions.net/D1_BBA_Scanner_10m_opt_vol")
        r_status = r.status_code

    return (D1_Col1_L1_Para_txt, D1_Col2_L1_Para_txt, D1_Col3_L1_Para_txt)

# # ___________________________________________________________________________________________________________
# # PART III: Defining the Callbacks to update the Card Details (Function D3)
# # ___________________________________________________________________________________________________________
@callback(
Output('D3_Col1_L1_Para','children'),
    Output('D3_Col2_L1_Para','children'),
    Output('D3_Col3_L1_Para','children'),
    Input('D3_button', 'n_clicks')
)
def update_function_d3(D3_clicks):
    function_D3_df = pd.read_csv("gs://bba_function_update_status/D3_Scanner_FNO.csv")
    D3_Col1_L1_Para_txt = function_D3_df["FUNCTION"].iloc[0]
    D3_Col2_L1_Para_txt = function_D3_df["STATUS"].iloc[0]
    D3_Col3_L1_Para_txt = function_D3_df["DATE"].iloc[0]

    if D3_clicks == 1:
        r = requests.post("https://asia-south1-phrasal-fire-373510.cloudfunctions.net/D3_Scanner_FNO_WL")
        r_status = r.status_code

    return (D3_Col1_L1_Para_txt, D3_Col2_L1_Para_txt, D3_Col3_L1_Para_txt)

# # ___________________________________________________________________________________________________________
# # PART III: Defining the Callbacks to update the Card Details (Function D4)
# # ___________________________________________________________________________________________________________
@callback(
Output('D4_Col1_L1_Para','children'),
    Output('D4_Col2_L1_Para','children'),
    Output('D4_Col3_L1_Para','children'),
    Input('D4_button', 'n_clicks')
)
def update_function_d4(D4_clicks):
    function_D4_df = pd.read_csv("gs://bba_function_update_status/D4_Scanner_WL_All.csv")
    D4_Col1_L1_Para_txt = function_D4_df["FUNCTION"].iloc[0]
    D4_Col2_L1_Para_txt = function_D4_df["STATUS"].iloc[0]
    D4_Col3_L1_Para_txt = function_D4_df["DATE"].iloc[0]

    # if D4_clicks == 1:


    return (D4_Col1_L1_Para_txt, D4_Col2_L1_Para_txt, D4_Col3_L1_Para_txt)


@callback(
    Output("D4_modal", "is_open"),
    [Input("D4_button_modal_cancel", "n_clicks"),
     Input("D4_button_modal_ok", "n_clicks"),
     Input("D4_button", "n_clicks")],
    [State("D4_modal", "is_open")],
)
def toggle_modal(n1, n2, n3, is_open):
    if n1 or n3:
        return not is_open
    if n2 == 1:
        r = requests.post("https://asia-east1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/phrasal-fire-373510/jobs/main:run")
        r_status = r.status_code
    return is_open