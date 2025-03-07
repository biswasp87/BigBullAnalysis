from logging import exception

import dash
# To create meta tag for each page, define the title, image, and description.
dash.register_page(
    __name__,
    # path='/',
    title='FNO Stock Analysis',
    name='Stock Analysis FNO'
)

from dash import Dash, dcc, html, Input, Output, callback, State
import dash_bootstrap_components as dbc
from plotly.subplots import make_subplots
import plotly.graph_objects as go
import numpy as np
import pandas as pd
from google.cloud import storage
from google.cloud import bigquery
from datetime import date, timedelta, datetime
from dash import dash_table as dt
from google.cloud import bigquery
from google.cloud.exceptions import NotFound
import asyncio
import telegram

# _____________________________________________________________________________________
# Telegram Bot Credentials
# _____________________________________________________________________________________
API_KEY = "6815271407:AAGGMeMYtsDBLSaZPySktp-aU1fmO5A3y5Y"
CHANNEL_ID = "@BigBullAnalysis360"
bot = telegram.Bot(token=API_KEY)

# _____________________________________________________________________________________
# Pulling Data from Google Cloud Storage
# _____________________________________________________________________________________
watchlist = pd.read_csv("gs://bba_support_files/WL_ALL.csv")
dropdown_opt_list = pd.read_csv("gs://bba_support_files/Dropdown_options.csv")
Expiry_Date_Monthly = pd.read_csv("gs://bba_support_files/stock_expiry_dates.csv")

# _____________________________________________________________________________________
# Layout for Order Placement inside Modal Layout
# _____________________________________________________________________________________

trade_layout = dbc.Row([
    dbc.Col([
        html.Div(
            [
                dbc.Stack(
                    [
                        dcc.Dropdown(
                            id='bba_symbol',
                            options=[{'label': x, 'value': x}
                                     for x in watchlist.Symbol],
                            value='TATAMOTORS',  # default value
                            maxHeight=150,
                        ),
                        dcc.Dropdown(
                            id='bba_instrument',
                            options=[{'label': 'Equity', 'value': 'EQ'},
                                     {'label': 'Future', 'value': 'FUTSTK'},
                                     {'label': 'Option', 'value': 'OPTSTK'}],
                            value='EQ',  # default value
                            multi=False
                        ),
                        dcc.Input(id="bba_qty", type="number", placeholder="Quantity", step=1, value=1, style={'display': 'block'}),
                        dcc.Dropdown(
                            id='bba_ordertype',
                            options=[{'label': 'LIMIT ORDER', 'value': '1'},
                                     {'label': 'MARKET ORDER', 'value': '2'},
                                     {'label': 'STOP ORDER (SL-M)', 'value': '3'},
                                     {'label': 'STOPLIMIT ORDER (SL-L)', 'value': '4'}],
                            value='2',  # default value
                            multi=False
                        ),
                        dcc.Dropdown(
                            id='bba_side',
                            options=[{'label': 'BUY', 'value': '1'},
                                     {'label': 'SELL', 'value': '-1'}],
                            value='1',  # default value
                            multi=False
                        ),
                        dcc.Dropdown(
                            id='bba_producttype',
                            options=[],
                            value='CNC',  # default value
                            multi=False
                        ),
                        dbc.Row([
                            dbc.Col([
                                dcc.Input(
                                    id="bba_limit_price", type="number", placeholder="Limit", step=0.05, value=0,
                                    style={"width": "85px", "height": "30px"})
                            ]),
                            dbc.Col([
                                dcc.Input(
                                    id="bba_stop_price", type="number", placeholder="Stop Price", step=0.05, value=0,
                                    style={"width": "85px", "height": "30px"})
                            ]),
                            dbc.Col([
                                dcc.Input(
                                    id="bba_disclose_qty", type="number", placeholder="Disc Qty", step=1, value=0,
                                    style={"width": "85px", "height": "30px"})
                            ]),
                        ], justify="evenly"),
                        dbc.Row([
                            dbc.Col([
                                dcc.Dropdown(
                                    id='bba_expiry',
                                    options=[{'label': x, 'value': x}
                                             for x in Expiry_Date_Monthly.NEAR_FUT_EXPIRY_DT],
                                    value=Expiry_Date_Monthly.NEAR_FUT_EXPIRY_DT[0],  # default value
                                    multi=False,
                                    maxHeight=150,
                                    disabled=False, style={"width": "85px"}
                                ),
                            ]),
                            dbc.Col([
                                dcc.Dropdown(
                                    id='bba_option_type',
                                    options=[{'label': 'CALL', 'value': 'CE'},
                                             {'label': 'PUT', 'value': 'PE'}],
                                    value='CE',  # default value
                                    multi=False,
                                    disabled=False, style={"width": "85px"}
                                ),
                            ]),
                            dbc.Col([
                                dcc.Input(id="bba_strike_price", type="number", placeholder="Strike Price", step=1,
                                          disabled=False, style={"width": "85px"})
                            ]),
                        ], justify="evenly"),
                    ],
                    gap=1,
                ),
            ]
        )
    ])
])

# _____________________________________________________________________________________
# Layout for Alert
# _____________________________________________________________________________________

alert_layout = dbc.Row([
    dbc.Col([
        html.Div(
            [
                dbc.Stack(
                    [
                        dcc.Dropdown(
                            id='condition',
                            options=[{'label': 'BREAKOUT', 'value': 'BREAKOUT'},
                                     {'label': 'BREAKDOWN', 'value': 'BREAKDOWN'}],
                            value='BREAKOUT',  # default value
                            multi=False,
                            disabled=False
                        ),
                        html.Hr(),
                        dbc.Row([
                            dbc.Col([
                                html.H6("TRIGER PRICE")
                            ]),
                            dbc.Col([
                                dcc.Input(
                                    id="bba_condition_price", type="number", placeholder="Entry", step=0.05,
                                    style={"width": "85px", "height": "30px"}, value=0, )
                            ]),
                        ], align="center"),
                        # dbc.Row([
                        #     dbc.Col([
                        #         html.H6("STOP LOSS")
                        #     ]),
                        #     dbc.Col([
                        #         dcc.Input(
                        #             id="bba_stop_loss", type="number", placeholder="Stop Loss", step=0.05,
                        #             style={"width": "85px", "height": "30px"}, value=0,)
                        #     ]),
                        # ], align="center"),
                        # dbc.Row([
                        #     dbc.Col([
                        #         html.H6("BOOK PROFIT")
                        #     ]),
                        #     dbc.Col([
                        #         dcc.Input(
                        #             id="bba_take_profit", type="number", placeholder="Target", step=0.05,
                        #             style={"width": "85px", "height": "30px"},  value=0,)
                        #     ]),
                        # ], align="center"),
                        dbc.Button(
                            id='place_alert',
                            n_clicks=0,
                            children='Place Alert',
                            color='warning',
                            # className="ml-0",
                            size='Auto'
                        ),
                        dbc.Button(
                            id='place_order',
                            n_clicks=0,
                            children='Place Order',
                            color='success',
                            # className="ml-0",
                            size='Auto'
                        ),
                        html.P(className="card-text", id='alert_status', children="Click Alert Button to generate Alert")
                    ],
                    gap=1,
                ),
            ]
        )
    ])
])
# _____________________________________________________________________________________
# Modal layout for Order Placement
# _____________________________________________________________________________________
modal_layout = html.Div(
    [
        dbc.Modal(
            [
                dbc.ModalHeader(dbc.ModalTitle("Fyers Order Placement")),
                dbc.ModalBody([
                    trade_layout,
                    html.Hr(),
                    # alert_layout
                ]),
                dbc.ModalFooter(
                    dbc.Button(
                        "Close", id="close", className="ms-auto", n_clicks=0
                    )
                ),
            ],
            id="modal",
            is_open=False,
        ),
    ]
)

# _____________________________________________________________________________________
# Main Page Layout with Graph, Modal, and Alert Layout
# _____________________________________________________________________________________
content_third_row = dbc.Row([
    dbc.Col([dbc.Card(
        dbc.CardBody([
            dcc.Graph(id='graph_31', config=dict({'scrollZoom': True}))
        ])
    )], lg=10, xs=12, sm=12),
    dbc.Col([
        dbc.Card(
            dbc.CardBody(
                [
                    html.H6("Select Watchlist"),
                    dcc.Dropdown(
                        id='dropdown_opt',
                        options=[{'label': x, 'value': x}
                                 for x in dropdown_opt_list.DRP_OPT],
                        value=dropdown_opt_list.DRP_OPT[0],  # default value
                        multi=False,
                        optionHeight=25,
                    ),
                    html.H6("Select Stock"),
                    dbc.Row([
                        dbc.Col(
                            dcc.Dropdown(
                                id='dropdown',
                                options=[{'label': x, 'value': x}
                                         for x in watchlist.Symbol],
                                value='TATAMOTORS',  # default value
                                maxHeight=150,
                            ),
                        ),
                        dbc.Col(
                            dbc.ButtonGroup([
                                dbc.Button(
                                    id='submit_button',
                                    n_clicks=0,
                                    children='Prev',
                                ),
                                dbc.Button(
                                    id='submit_button_next',
                                    n_clicks=0,
                                    children='Next',
                                ),
                            ], size='md'),
                        )
                    ]),
                    dbc.Row([
                        dbc.Col(
                            dcc.Dropdown(
                                id='dropdown_exp',
                                options=[{'label': x, 'value': x}
                                         for x in Expiry_Date_Monthly.NEAR_FUT_EXPIRY_DT],
                                value=Expiry_Date_Monthly.NEAR_FUT_EXPIRY_DT[1],  # default value
                                multi=False,
                                maxHeight=150,
                            ),
                        ),
                        dbc.Col(
                            # html.H6("Select No. of days"),
                            dcc.Dropdown(
                                id='dropdown_n_days',
                                options=[{'label': 7, 'value': 7},
                                         {'label': 15, 'value': 15},
                                         {'label': 30, 'value': 30},
                                         {'label': 60, 'value': 60},
                                         {'label': 90, 'value': 90},
                                         {'label': 120, 'value': 120},
                                         {'label': 150, 'value': 150}],
                                value=60,  # default value
                                multi=False,
                                # maxHeight=150,
                            ),
                        ),
                    ]),
                    html.Hr(),
                    dbc.Accordion([
                        dbc.AccordionItem([
                            dbc.Row([
                                dbc.Col([
                                    html.H6("Short"),
                                    dcc.Input(
                                        id="input_short_SMA", type="number", placeholder="Short SMA", min=1, max=200,
                                        step=1,
                                        value=2, size="sm",
                                        style={'width': '50px'}
                                    ),
                                ]),
                                dbc.Col([
                                    html.H6("Midium"),
                                    dcc.Input(
                                        id="input_medium_SMA", type="number", placeholder="Medium SMA", min=1, max=200,
                                        step=1,
                                        value=7, size="sm",
                                        style={'width': '50px'}
                                    ),
                                ]),
                                dbc.Col([
                                    html.H6("Long"),
                                    dcc.Input(
                                        id="input_long_SMA", type="number", placeholder="Long SMA", min=1, max=200,
                                        step=1,
                                        value=21, size="sm",
                                        style={'width': '50px'}
                                    ),
                                ]),
                            ]),
                            html.H6("Select Resolution"),
                            dcc.RangeSlider(id='my-range-slider', min=400, max=800, step=50, value=[800], marks=None, ),
                            dbc.Row([
                                dbc.Col([
                                    html.H6("BB Channel"),
                                    dcc.Input(
                                        id="b_band_limit", type="number", placeholder="Bollinger Band", min=1, max=200,
                                        step=0.1, value=1.5, size="sm",
                                        style={'width': '75px'}),
                                ]),
                                dbc.Col([
                                    html.H6("K Channel"),
                                    dcc.Input(
                                        id="kc_limit", type="number", placeholder="Keltner Channel", min=1, max=200,
                                        step=0.1,
                                        value=1.2, size="sm",
                                        style={'width': '75px'}),
                                ]),
                            ]),
                            dbc.Row([
                            ]),
                        ], title="Chart Settings"),
                        dbc.AccordionItem([
                            dbc.Row([
                                dbc.ButtonGroup([
                                    dbc.Button(
                                        id="Add_Button",
                                        n_clicks=0,
                                        children="ADD"
                                    ),
                                    dbc.Button(
                                        id="Clear_Button",
                                        n_clicks=0,
                                        children="CLEAR"
                                    ),
                                    dbc.Button(
                                        id="Save_Button",
                                        n_clicks=0,
                                        children="SAVE"
                                    )
                                ])
                            ]),
                            dbc.Row([
                                dt.DataTable(
                                    id="shortlisted_table",
                                    row_deletable=True,
                                    selected_columns=[],
                                    selected_rows=[],
                                    page_action="native",
                                    style_as_list_view=True,
                                    style_cell={'padding': '5px'},
                                    style_header={
                                        'backgroundColor': 'white',
                                        'fontWeight': 'bold'
                                    },
                                    style_data={
                                                    "textAlign": "left",
                                                },
                                ),
                                html.Label(id='Shortlisted_BQ_Status'),
                                html.Label(id='watch_stock_from_table')
                            ])
                        ], title="Shortlisted"),
                        dbc.AccordionItem([
                            html.Div([
                                alert_layout,   # The Layout is defined above seperately
                                modal_layout,
                            ])
                        ], title="Trade Book Entry"),
                    ], start_collapsed=True)
                ]
            )
        ),
    ], lg=2, xs=12)
])



content = html.Div(
    [
        content_third_row,
    ],
)

layout = html.Div([html.Br(), content, dcc.Store(id="df_shortlisted", data=[], storage_type='session')])

# _____________________________________________________________________________________
# Function to prepare Alert database and upload to Big Query
# _____________________________________________________________________________________
@callback(
        Output('alert_status','children'),
        Input('dropdown', 'value'),
        Input('condition', 'value'),
        Input('bba_condition_price', 'value'),
        Input('place_alert', 'n_clicks')
)
def place_alert(bba_symbol_val,
                bba_condition_value, bba_condition_price,
                place_alert_click):
    alert_status = ""

    ctx = dash.callback_context
    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
    alert_date = str(date.today())

    if trigger_id == 'place_alert':
        index_symbol = [[f'{alert_date}', f'{bba_condition_value}', f'{bba_symbol_val}', f'{bba_condition_price}', "",'ACTIVE']]
        index_symbol_df = pd.DataFrame(index_symbol,
                                           columns=['Alert_Date', 'Alert_Condition', 'Alert_Symbol', 'Alert_Price', 'Trigger_Date', 'Alert_Status'])
        telegram_message = f"""{bba_condition_value} TRADE\n{bba_symbol_val}\nEntry Price:{bba_condition_price}"""

    client = bigquery.Client()
    table_id = 'phrasal-fire-373510.alert_order.alert'
    project = "WRITE_APPEND"
    job_config = bigquery.LoadJobConfig(
        schema=[
            bigquery.SchemaField("Alert_Date", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("Alert_Condition", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("Alert_Symbol", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("Alert_Price", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("Trigger_Date", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("Alert_Status", "STRING", mode="REQUIRED"),
            # bigquery.SchemaField("EXPIRY_DT", "DATE", mode="REQUIRED"),
            ],
        write_disposition=project)
    try:
        try:
            client.get_table(table_id)  # Make an API request.
            print("Table {} already exists.".format(table_id))
            # Upload Current Dataframe
            job = client.load_table_from_dataframe(index_symbol_df, table_id,
                                                   job_config=job_config)  # Make an API request.
            job.result()  # Wait for the job to complete.
            table = client.get_table(table_id)  # Make an API request.
            print("Loaded {} rows and {} columns to {}".format(table.num_rows, len(table.schema), table_id))

            bot.sendMessage(chat_id=CHANNEL_ID,
                            text=telegram_message)  # Telegram Bot for pushing message in telegram
            alert_status = f'Alert Created for {bba_symbol_val}'
        except NotFound:
            print("Table {} is not found. Proceed to create table".format(table_id))
            client.create_table(table_id)  # API request
            print(f"Created {table_id}.")
            # Upload Current Dataframe
            job = client.load_table_from_dataframe(index_symbol_df, table_id,
                                                   job_config=job_config)  # Make an API request.
            job.result()  # Wait for the job to complete.

            table = client.get_table(table_id)  # Make an API request.
            print("Loaded {} rows and {} columns to {}".format(table.num_rows, len(table.schema), table_id))
            alert_status = f'Alert Created for {bba_symbol_val}'
    except exception as e:
        print(e)
        alert_status = f'ERROR in Creating Alert'

    return alert_status

# _____________________________________________________________________________________
# Function for Modal
# _____________________________________________________________________________________
@callback(
    Output("order_status", "children"),
    [Input("place_order", "n_clicks"), Input("close", "n_clicks")],
    [State("modal", "is_open")],
)
def toggle_modal(n1, n2, is_open):
    if n1 or n2:
        return not is_open
    return is_open
# ____________________________________________________________________________________________
# Update Watchlist Values as per the selected watchlist
# ____________________________________________________________________________________________
@callback(
    Output('dropdown', 'options'),
    [Input('dropdown_opt', 'value')])
def update_dropdown_list(dropdown_item_value):
    dir_wl = "gs://bba_support_files/" + dropdown_item_value + '.csv'
    wl = pd.read_csv(dir_wl)
    options = [{'label': x, 'value': x} for x in wl['Symbol']]
    return options
# ____________________________________________________________________________________________
# Update Expiry Date Values
# ____________________________________________________________________________________________
@callback(
    Output('dropdown_exp', 'options'),
    [Input('dropdown_opt', 'value')])
def update_dropdown_list(dropdown_item_value):
    exp_dates = pd.read_csv("gs://bba_support_files/stock_expiry_dates.csv")
    options = [{'label': x, 'value': x} for x in exp_dates['NEAR_FUT_EXPIRY_DT']]
    return options

# ____________________________________________________________________________________________
# Callback function to fetch Previous and Next Symbol from the selected Watchlist
# Select Symbol from Favourite list (if Selected)
# ____________________________________________________________________________________________
@callback(
    Output('dropdown', 'value'),
    Input('submit_button', 'n_clicks'),
    Input('submit_button_next', 'n_clicks'),
    Input('dropdown', 'value'),
    Input('dropdown_opt', 'value'),
    Input('shortlisted_table', 'data'),
    Input('shortlisted_table', 'active_cell')
)
def update_dropdown(n_clicks, n_clicks_next, dropdown_value, dropdown_opt_val, shortlisted_table, active_cell):
    shortlisted_table = pd.DataFrame(shortlisted_table)

    ctx = dash.callback_context
    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
    dropdown_opt_val = "gs://bba_support_files/" + dropdown_opt_val + str('.csv')
    wl = pd.read_csv(dropdown_opt_val)

    if trigger_id == 'submit_button':
        cur_position = wl[wl['Symbol'] == dropdown_value].index[0]
        cur_position = int(cur_position)
        value_analysis = wl['Symbol'].iloc[cur_position - 1]

    elif trigger_id == 'submit_button_next':
        cur_position = wl[wl['Symbol'] == dropdown_value].index[0]
        cur_position = int(cur_position)
        value_analysis = wl['Symbol'].iloc[cur_position + 1]
    elif active_cell:
        data_row = active_cell['row']
        data_col_id = active_cell['column_id']
        value_analysis = shortlisted_table.loc[data_row, data_col_id]
    else:
        value_analysis = dropdown_value
    return value_analysis

# _____________________________________________________________________________________
# FAVOURITE Stocks Selection and Management
# _____________________________________________________________________________________

@callback(Output('shortlisted_table', "columns"),
          Output('shortlisted_table', "data"),
          Input('df_shortlisted', 'data')
          )
def update_shortlisted_stock_table(shortlisted_table_data):
    latest_table_df = pd.DataFrame(shortlisted_table_data)
    return [{'name': i, 'id': i, 'deletable': True} for i in latest_table_df.columns if i != 'id'], \
        latest_table_df.to_dict('records')

@callback(
    Output('df_shortlisted', 'data'),
    State('df_shortlisted', 'data'),
    Input('Add_Button', 'n_clicks'),
    Input('Clear_Button', 'n_clicks'),
    Input('Save_Button', 'n_clicks'),
    Input('dropdown', 'value'),
)
def shortlisted_stock_data(df_shortlisted, Add_clicks, Clear_clicks, Save_Clicks, Stock_name):
    existing_shortlisted_df = pd.DataFrame(df_shortlisted)
    if existing_shortlisted_df.empty:
        client = bigquery.Client()
        sql_shortlisted = f"""
            SELECT *
            FROM `phrasal-fire-373510.Watchlist.Shortlisted`
        """
        existing_shortlisted_df = client.query(sql_shortlisted).to_dataframe()
        if existing_shortlisted_df.empty:
            existing_shortlisted_df = pd.DataFrame(columns=['FAVOURITE'])
            print(existing_shortlisted_df)

    ctx = dash.callback_context
    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

    if trigger_id == 'Add_Button':
        data = [['{}'.format(Stock_name)]]
        new_shortlisted_df = pd.DataFrame(data, columns=['FAVOURITE'])
        print(new_shortlisted_df)

        vertical_concat = pd.concat([existing_shortlisted_df, new_shortlisted_df], axis=0)
        print(vertical_concat)
        return vertical_concat.to_dict('records')
    elif trigger_id == 'Clear_Button':
        existing_shortlisted_df = pd.DataFrame(columns=['FAVOURITE'])
        client = bigquery.Client()
        clear_dml_statement = ("TRUNCATE TABLE phrasal-fire-373510.Watchlist.Shortlisted")
        clear_job = client.query(clear_dml_statement)
        clear_job.result()
        return existing_shortlisted_df.to_dict('records')
    else:
        return existing_shortlisted_df.to_dict('records')

@callback(
    Output('Shortlisted_BQ_Status', 'children'),
    Input('df_shortlisted', 'data'),
    Input('Save_Button', 'n_clicks'),
)
def shortlisted_BQ_save(shortlisted_saved_df, n_click_save):
    shortlisted_saved_df = pd.DataFrame(shortlisted_saved_df)
    ctx = dash.callback_context
    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

    if trigger_id == 'Save_Button':
        client = bigquery.Client()
        try:
            table_id = "phrasal-fire-373510.Watchlist.Shortlisted"
            # project = "WRITE_APPEND"
            project = "WRITE_TRUNCATE"
            job_config = bigquery.LoadJobConfig(
                schema=[
                    bigquery.SchemaField("FAVOURITE", "STRING", mode="NULLABLE")
                ],
                write_disposition=project, )
            try:
                client.get_table(table_id)  # Make an API request.
                # Upload Current Dataframe
                job = client.load_table_from_dataframe(shortlisted_saved_df, table_id,
                                                       job_config=job_config)  # Make an API request.
                job.result()  # Wait for the job to complete.
                table = client.get_table(table_id)  # Make an API request.
                message = "List Saved"

            except NotFound:
                client.create_table(table_id)  # API request
                # Upload Current Dataframe
                job = client.load_table_from_dataframe(shortlisted_saved_df, table_id,
                                                       job_config=job_config)  # Make an API request.
                job.result()  # Wait for the job to complete.
                table = client.get_table(table_id)  # Make an API request.
                message = "List Saved"
        except Exception as e:
            print(f"***** ERROR at fetching : {e} ****")  # Print the ERROR Message
            message = f"***** ERROR at fetching : {e} ****"
    else:
        message = 'Click on SAVE'
    return message

# _____________________________________________________________________________________
# DISPLAY Graph
# _____________________________________________________________________________________
@callback(
    Output('graph_31', 'figure'),
    Output('df_indicator', 'data'),
    [Input('dropdown_exp', 'value'),
     Input('dropdown', 'value'),
     Input('dropdown_opt', 'value'),
     Input('dropdown_n_days', 'value'),
     Input('input_short_SMA', 'value'),
     Input('input_medium_SMA', 'value'),
     Input('input_long_SMA', 'value'),
     Input('my-range-slider', 'value'),
     Input('b_band_limit', 'value'),
     Input('kc_limit', 'value')])
def update_graph_31(dropdown_exp_value, dropdown_value, dropdown_opt_value, dropdown_n_days_value,
                    short_sma, medium_sma, long_sma, graph_height,b_band,kc):
    expiry_date = datetime.strptime(dropdown_exp_value, "%Y-%m-%d").date()

    client = bigquery.Client()
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
        ORDER BY TIMESTAMP DESC LIMIT {dropdown_n_days_value}
    """
    df_stock = client.query(sql_stock).to_dataframe()
    df_stock = df_stock.sort_values(by='TIMESTAMP', ascending=True)
    df_stock["BAR"] = df_stock["BAR"].astype(int)
    df_stock = df_stock.reset_index()

    # PRICE STRENGTH INDICATOR___________________________________________________________
    df_stock["CLOSE_MA_S"] = df_stock["EQ_CLOSE_PRICE"].rolling(short_sma).mean()
    df_stock["CLOSE_MA_M"] = df_stock["EQ_CLOSE_PRICE"].rolling(medium_sma).mean()
    df_stock["CLOSE_MA_COL"] = np.where((df_stock["CLOSE_MA_S"] > df_stock["CLOSE_MA_M"].shift(short_sma)), 'green', 'white')

    # VOLUME STRENGTH INDICATOR___________________________________________________________
    df_stock["VOLUME_MA_S"] = df_stock["EQ_TTL_TRD_QNTY"].rolling(short_sma).mean()
    df_stock["VOLUME_MA_M"] = df_stock["EQ_TTL_TRD_QNTY"].rolling(medium_sma).mean()
    df_stock["VOLUME_MA_COL"] = np.where((df_stock["VOLUME_MA_S"] > df_stock["VOLUME_MA_M"].shift(short_sma)), 'green', 'white')

    # DELIVERY QUANTITY STRENGTH INDICATOR___________________________________________________________
    df_stock["DEL_MA_S"] = df_stock["EQ_DELIV_QTY"].rolling(short_sma).mean()
    df_stock["DEL_MA_M"] = df_stock["EQ_DELIV_QTY"].rolling(medium_sma).mean()
    df_stock["DEL_MA_COL"] = np.where((df_stock["DEL_MA_S"] > df_stock["DEL_MA_M"].shift(short_sma)), 'green', 'white')

    # DELIVERY PERCENTAGE STRENGTH INDICATOR___________________________________________________________
    df_stock["DEL_PER_MA_S"] = ((df_stock["EQ_TTL_TRD_QNTY"].rolling(short_sma).sum() /
                                df_stock["EQ_DELIV_QTY"].rolling(short_sma).sum())*100).round(1)
    df_stock["DEL_PER_MA_M"] = ((df_stock["EQ_TTL_TRD_QNTY"].rolling(medium_sma).sum() /
                                df_stock["EQ_DELIV_QTY"].rolling(medium_sma).sum())*100).round(1)
    df_stock["DEL_PER_MA_COL"] = np.where((df_stock["DEL_PER_MA_S"] < df_stock["DEL_PER_MA_M"].shift(short_sma)), 'red', 'white')

    # Q/T STRENGTH INDICATOR___________________________________________________________
    df_stock["QT_MA_S"] = df_stock["EQ_QT"].rolling(short_sma).mean()
    df_stock["QT_MA_M"] = df_stock["EQ_QT"].rolling(medium_sma).mean()
    df_stock["QT_MA_COL"] = np.where((df_stock["QT_MA_S"] > df_stock["QT_MA_M"].shift(short_sma)), 'green', 'white')

    # COI BUILDUP ___________________________________________________________
    df_stock["FUT_BUILD_UP_COL"] = np.where(((df_stock["FUT_BUILD_UP"] == "LB") | (df_stock["FUT_BUILD_UP"] == "SC")), 'green', 'red')

    # PCR VALUE STRENGTH INDICATOR___________________________________________________________
    df_stock["PCR_MA_COL"] = np.where(df_stock["CUR_PCR"] >= 100, 'green',
                                       np.where((df_stock["CUR_PCR"] < 100) & (df_stock["CUR_PCR"] > 80), 'yellow', 'red'))
    # Consolidation Phase
    df_stock['SMA'] = df_stock['EQ_CLOSE_PRICE'].rolling(window=long_sma).mean()     #Simple Moving Average calculation (period = 20)
    df_stock['stdev'] = df_stock['EQ_CLOSE_PRICE'].rolling(window=long_sma).std()    #Standard Deviation calculation
    df_stock['Lower_Bollinger'] = df_stock['SMA'] - (b_band * df_stock['stdev'])   #Calculation of the lower curve of the Bollinger Bands
    df_stock['Upper_Bollinger'] = df_stock['SMA'] + (b_band * df_stock['stdev'])   #Upper curve

    df_stock['TR'] = abs(df_stock['EQ_HIGH_PRICE'] - df_stock['EQ_LOW_PRICE'])      #True Range calculation
    df_stock['ATR'] = df_stock['TR'].rolling(window = long_sma).mean()    #Average True Range

    df_stock['Upper_KC'] = df_stock['SMA'] + (kc * df_stock['ATR'])      #Upper curve of the Keltner Channel
    df_stock['Lower_KC'] = df_stock['SMA'] - (kc * df_stock['ATR'])      #Lower curve

    df_stock['consolidation'] = np.where((df_stock['Lower_Bollinger'] > df_stock['Lower_KC']) & (df_stock['Upper_Bollinger'] < df_stock['Upper_KC']),"yellow","white")

    try:
        df_10M_VOL = df_stock[["TIMESTAMP", "CUR_FUT_EXPIRY_DT", "NEAR_FUT_EXPIRY_DT",
                               "EQ_HIGH_PRICE", "EQ_LOW_PRICE",
                               "CUR_PE_STRIKE_PR_10MVOL", "CUR_CE_STRIKE_PR_10MVOL",
                               "NEAR_PE_STRIKE_PR_10MVOL", "NEAR_CE_STRIKE_PR_10MVOL"]]
        columns = ["CUR_PE_STRIKE_PR_10MVOL", "CUR_CE_STRIKE_PR_10MVOL", "NEAR_PE_STRIKE_PR_10MVOL", "NEAR_CE_STRIKE_PR_10MVOL"]
        exploded = [
            df_10M_VOL[col].str.strip("[]").str.split(",", expand=True).stack().rename(col)
            for col in columns
        ]
        exploded = pd.concat(exploded, axis=1).droplevel(-1)
        df_10M_VOL = df_10M_VOL.drop(columns=columns)
        df_10M_VOL = df_10M_VOL.join(exploded)

        if dropdown_exp_value != "ALL":
            df_10M_VOL = df_10M_VOL[df_10M_VOL.CUR_FUT_EXPIRY_DT == expiry_date]
            df_10M_VOL = df_10M_VOL.astype(str).replace('nan', 'None')
            df_10M_VOL = df_10M_VOL[(df_10M_VOL["CUR_PE_STRIKE_PR_10MVOL"] != 'None') | (df_10M_VOL["CUR_CE_STRIKE_PR_10MVOL"] != 'None')]
            df_10M_VOL["ENTRY_BO"] = float(df_10M_VOL['EQ_HIGH_PRICE'].iloc[0])
            df_10M_VOL["ENTRY_BD"] = float(df_10M_VOL['EQ_LOW_PRICE'].iloc[0])
    except Exception:
        print(Exception)
        df_10M_VOL = pd.DataFrame(columns=['TIMESTAMP',
                                           'CUR_FUT_EXPIRY_DT', 'NEAR_FUT_EXPIRY_DT',
                                           'EQ_HIGH_PRICE', 'EQ_LOW_PRICE',
                                           'CUR_PE_STRIKE_PR_10MVOL', 'CUR_CE_STRIKE_PR_10MVOL',
                                           'NEAR_PE_STRIKE_PR_10MVOL', 'NEAR_CE_STRIKE_PR_10MVOL',
                                           'ENTRY_BO', 'ENTRY_BD'])
    df_store = df_stock.iloc[[-1]]
    if not df_10M_VOL.empty:
        df_store["10M_VOL_TIMESTAMP"] = df_10M_VOL['TIMESTAMP'].iloc[0]
    else:
        df_store["10M_VOL_TIMESTAMP"] = 'NA'

    if graph_height[0] == 800:
        row_height_values = [0.505, 0.075, 0.075, 0.075, 0.02, 0.09, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02,
                             0.02]
    else:
        row_height_values = [0.38, 0.09, 0.085, 0.085, 0.03, 0.09, 0.03, 0.03, 0.03, 0.03, 0.03, 0.03, 0.03, 0.03]

    fig = make_subplots(
        rows=14, cols=1,
        row_heights=row_height_values,
        specs=[[{}], [{"secondary_y": True}], [{}], [{}], [{}], [{"secondary_y": True}], [{}], [{}], [{}], [{}], [{}], [{}], [{}], [{}]],
        print_grid=True, shared_xaxes=True, horizontal_spacing=0.05, vertical_spacing=0)

    config = dict({'scrollZoom': True})
    fig.update_layout(paper_bgcolor='rgb(255,255,255)', plot_bgcolor='rgb(255,255,255)', height=graph_height[0])
    fig.update_layout(margin=dict(r=2, t=2, b=2, l=2))
    fig.update_xaxes(showline=True, linewidth=2, linecolor='black')
    # fig.update_yaxes(range=[0, 100], row=2, col=1)
    # fig.update_yaxes(mirror=True, row=1, col=1)
    fig.update_yaxes(mirror="ticks", side='right')
    fig.update_yaxes(showticklabels=False, row=5, col=1)
    fig.update_layout(yaxis3=dict(showticklabels=False), yaxis6=dict(showticklabels=False),
                      yaxis8=dict(showticklabels=False), yaxis9=dict(showticklabels=False),
                      yaxis10=dict(showticklabels=False), yaxis11=dict(showticklabels=False),
                      yaxis12=dict(showticklabels=False), yaxis13=dict(showticklabels=False),
                      yaxis14=dict(showticklabels=False), yaxis15=dict(showticklabels=False),
                      yaxis16=dict(showticklabels=False), yaxis17=dict(showticklabels=False))
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
    # Add 5 SMA to Closing Price in OHCL Plot
    fig.add_trace(go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock.EQ_CLOSE_PRICE.rolling(5).mean(), name='5SMA Close'),
                  row=1, col=1)
    # Add 20 SMA to Closing Price in OHCL Plot
    fig.add_trace(go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock.EQ_CLOSE_PRICE.rolling(20).mean(), name='20SMA Close'),
                  row=1, col=1)
    # Add ENTRY Lines.....................
    fig.add_trace(
        go.Scatter(x=df_10M_VOL['TIMESTAMP'], y=df_10M_VOL['ENTRY_BO'], mode='lines', name='Entry', line=dict(color='black')),
        row=1, col=1)
    # Add SL Lines
    fig.add_trace(
        go.Scatter(x=df_10M_VOL['TIMESTAMP'], y=df_10M_VOL['ENTRY_BD'], mode='lines', name='SL', line=dict(color='black')),
        row=1, col=1)
    # Add Volume as Subplot
    fig.add_trace(
        go.Bar(x=df_stock['TIMESTAMP'], y=df_stock['EQ_TTL_TRD_QNTY'], name='Volume', offsetgroup=1),
        row=2, col=1)
    # Add 20 SMA to Volume Subplot
    fig.add_trace(go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock.EQ_TTL_TRD_QNTY.rolling(20).mean(), name='20SMA Vol'),
                  row=2, col=1)
    fig.add_trace(
        go.Bar(x=df_stock['TIMESTAMP'], y=df_stock['EQ_DELIV_QTY'], name='Del Qty',offsetgroup=1),
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
        go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock['FUT_COI'], name='COI',mode='lines+markers', marker=dict(size=10, color=df_stock['FUT_PRICE_COL'])),
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
    # Add Price Trend
    fig.add_trace(
        go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock['BAR'], mode='markers', name="Price Trend",
                   marker=dict(size=10, symbol=1, color=df_stock['CLOSE_MA_COL'])),
        row=7, col=1)
    # Add Volume Trend
    fig.add_trace(
        go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock['BAR'], mode='markers', name="Vol Trend",
                   marker=dict(size=10, symbol=1, color=df_stock['VOLUME_MA_COL'])),
        row=8, col=1)
    # Add Del Trend
    fig.add_trace(
        go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock['BAR'], mode='markers', name="Del Qnt Trend",
                   marker=dict(size=10, symbol=1, color=df_stock['DEL_MA_COL'])),
        row=9, col=1)
    # Add Del% Trend
    fig.add_trace(
        go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock['BAR'], mode='markers', name="Del% Trend",
                   marker=dict(size=10, symbol=1, color=df_stock['DEL_PER_MA_COL'])),
        row=10, col=1)
    # Add QT Trend
    fig.add_trace(
        go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock['BAR'], mode='markers', name="QT Trend",
                   marker=dict(size=10, symbol=1, color=df_stock['QT_MA_COL'])),
        row=11, col=1)
    # Add Future Buildup
    fig.add_trace(
        go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock['BAR'], mode='markers', name="Fut Buildup",
                   marker=dict(size=10, symbol=1, color=df_stock['FUT_BUILD_UP_COL'])),
        row=12, col=1)
    # Add Future Buildup
    fig.add_trace(
        go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock['BAR'], mode='markers', name="PCR",
                   marker=dict(size=10, symbol=1, color=df_stock['PCR_MA_COL'])),
        row=13, col=1)
    # Add consolidation
    fig.add_trace(
        go.Scatter(x=df_stock['TIMESTAMP'], y=df_stock['BAR'], mode='markers', name="Consolidation",
                   marker=dict(size=10, symbol=1, color=df_stock['consolidation'])),
        row=14, col=1)
    # # Add Annonation
    cur_position = Expiry_Date_Monthly[Expiry_Date_Monthly['NEAR_FUT_EXPIRY_DT'] == dropdown_exp_value].index[0]
    cur_position = int(cur_position)
    cur_expiry_date = Expiry_Date_Monthly['NEAR_FUT_EXPIRY_DT'].iloc[cur_position]
    prev_expiry_date = Expiry_Date_Monthly['NEAR_FUT_EXPIRY_DT'].iloc[cur_position + 1]
    fig.add_vline(x=cur_expiry_date)
    fig.add_vline(x=prev_expiry_date)

    # edit axis labels
    fig['layout']['yaxis']['title'] = 'Equity OHCL'
    fig['layout']['yaxis2']['title'] = 'Volume'
    fig['layout']['yaxis4']['title'] = 'Q/T'
    fig['layout']['yaxis5']['title'] = 'COI'
    fig['layout']['yaxis7']['title'] = 'PCR'
    LTP=df_stock['EQ_CLOSE_PRICE'].iloc[-1]
    LTP_PREV=df_stock['EQ_CLOSE_PRICE'].iloc[-2]
    PER_CHNG=((LTP-LTP_PREV)/LTP*100).round(1)
    fig.update_layout(xaxis_title=dropdown_value+str(" LTP: ")+str(LTP)+str(" Change: ")+str(PER_CHNG)+str("%"))

    return fig, df_store.to_dict('records')

# @callback(
#     Output('bba_expiry', 'disabled'), Output('bba_option_type', 'disabled'), Output('bba_strike_price', 'disabled'),
#     Output('bba_producttype', 'options'), Output('bba_producttype', 'value'),
#     Output('bba_qty', 'value'), Output('bba_qty', 'step'),
#     Input('bba_instrument', 'value'), Input('bba_symbol', 'value')
# )
# def input_field_ability(instrument_value, symbol_value):
#     lot_size_value = lot_size[lot_size['Symbol'] == symbol_value]['LOT_SIZE'].iloc[0]
#     if instrument_value == 'EQ':
#         expiry_disability = True
#         option_type_disability = True
#         strike_price_disability = True
#         product_type_options = [
#                     {'label': 'CNC', 'value': 'CNC'},
#                     {'label': 'INTRADAY', 'value': 'INTRADAY'},
#                     {'label': 'CO', 'value': 'CO'},
#                     {'label': 'BO', 'value': 'BO'}]
#         bba_product_type_value = 'CNC'
#         bba_qty_value = 1
#         bba_qty_step = 1
#     elif instrument_value == 'FUTSTK':
#         expiry_disability = False
#         option_type_disability = True
#         strike_price_disability = True
#         product_type_options = [
#                    {'label': 'INTRADAY', 'value': 'INTRADAY'},
#                    {'label': 'MARGIN', 'value': 'MARGIN'},
#                    {'label': 'CO', 'value': 'CO'},
#                    {'label': 'BO', 'value': 'BO'}]
#         bba_product_type_value = 'MARGIN'
#         bba_qty_value = lot_size_value
#         bba_qty_step = int(lot_size_value)
#     else:
#         expiry_disability = False
#         option_type_disability = False
#         strike_price_disability = False
#         product_type_options = [
#                     {'label': 'INTRADAY', 'value': 'INTRADAY'},
#                     {'label': 'MARGIN', 'value': 'MARGIN'},
#                     {'label': 'CO', 'value': 'CO'},
#                     {'label': 'BO', 'value': 'BO'}]
#         bba_product_type_value = 'MARGIN'
#         bba_qty_value = lot_size_value
#         bba_qty_step = int(lot_size_value)
#     return expiry_disability, option_type_disability, strike_price_disability, \
#         product_type_options, bba_product_type_value, \
#         bba_qty_value, bba_qty_step
#
#
# @callback(
#     Output('place_order','color'),
#     Input('bba_side', 'value')
# )
# def order_button_colour(position_value):
#     if position_value == '-1':
#         colour_value = 'danger'
#     else:
#         colour_value = 'success'
#     return colour_value

# @callback(
#     Output('order_status','children'),
#     Input('bba_symbol', 'value'), Input('bba_instrument', 'value'), Input('bba_qty', 'value'),
#     Input('bba_ordertype', 'value'), Input('bba_side', 'value'), Input('bba_producttype', 'value'),
#     Input('bba_limit_price', 'value'), Input('bba_stop_price', 'value'), Input('bba_disclose_qty', 'value'),
#     Input('bba_expiry', 'value'), Input('bba_option_type', 'value'), Input('bba_strike_price', 'value'), Input('bba_stop_loss', 'value'),
#     Input('bba_take_profit', 'value'), Input('place_order', 'n_clicks')
# )
# def place_order(bba_symbol_val, bba_instrument_val, bba_qty_val, bba_ordertype_val, bba_side_val, bba_producttype_val,
#                 bba_limit_price_val, bba_stop_price_val, bba_disclose_qty_val, bba_expiry_val, bba_option_type_val, bba_strike_price_val,
#                 bba_stop_loss_val, bba_take_profit_val, place_order_click):  # Place Order Function
#
#     if bba_instrument_val == "EQ":
#         symbol_format = "NSE:" + str(bba_symbol_val) + "-EQ"
#     elif bba_instrument_val == 'FUTSTK':
#         symbol_format = "NSE:" + str(bba_symbol_val) + bba_expiry_val[9:11] + str(bba_expiry_val[3:6]).upper() + 'FUT'
#     else:
#         symbol_format = "NSE:" + str(bba_symbol_val) + bba_expiry_val[9:11] + str(bba_expiry_val[3:6]).upper() + str(bba_strike_price_val) + str(bba_option_type_val)
#
#     if place_order_click > 1:
#         data = {
#             "symbol": '{}'.format(symbol_format),
#             "qty": int(bba_qty_val),
#             "type": int(bba_ordertype_val),
#             "side": int(bba_side_val),
#             "productType": '{}'.format(bba_producttype_val),
#             "limitPrice": bba_limit_price_val,
#             "stopPrice": bba_stop_price_val,
#             "validity": "DAY",
#             "disclosedQty": bba_disclose_qty_val,
#             "offlineOrder": "False",
#             "stopLoss": bba_stop_loss_val,
#             "takeProfit": bba_take_profit_val
#         }
#         print(data)
#
#         fyers = fyersModel.FyersModel(client_id=client_id, token=token)
#         print(fyers.get_profile())
#         # print(fyers.place_order(data)['message'])
#         return fyers.place_order(data)['message']