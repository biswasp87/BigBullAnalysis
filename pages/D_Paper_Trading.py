import dash
# To create meta tag for each page, define the title, image, and description.
dash.register_page(
    __name__,
    # path='/',
    title='Paper Trading',
    name='Paper Trading'
)

from dash import Dash, dcc, html, Input, Output, callback
import dash_bootstrap_components as dbc
from dash import html
from dash import dash_table as dt
from datetime import date, timedelta, datetime

import pandas as pd
import dash_daq as daq
from fyers_api import fyersModel
from fyers_api import accessToken

client_id = "6N55AF8XZA-100"
access_token = pd.read_csv("gs://biswasp87/access_token.csv")
token = access_token["access_token"].iloc[0]
print(token)

# Declaring DataFrames
OI_exp_data = pd.DataFrame()
Master_Spot_Data = pd.DataFrame()

FPI_Investment_Equity_Exst = pd.read_csv('gs://bba_support_files/FPI_Investment_Equity.csv')
FPI_Investment_Debt_Exst = pd.read_csv('gs://bba_support_files/FPI_Investment_Debt.csv')
lot_size = pd.read_csv("gs://bba_support_files/Lot_Size.csv")
watchlist = pd.read_csv("gs://bba_support_files/WL_ALL.csv")
Expiry_Date_Monthly = pd.read_csv("gs://bba_support_files/Expiry_Date_Monthly.csv")

FPI_Investment_Equity_Exst.drop(FPI_Investment_Equity_Exst.columns[FPI_Investment_Equity_Exst.columns.str.contains('unnamed', case=False)], axis=1, inplace=True)
FPI_Investment_Debt_Exst.drop(FPI_Investment_Debt_Exst.columns[FPI_Investment_Debt_Exst.columns.str.contains('unnamed', case=False)], axis=1, inplace=True)

FPI_Data = FPI_Investment_Equity_Exst
FPI_Data.columns.astype(str)

# the style arguments for the main content page.
CONTENT_STYLE = {
    'margin-left': '2%',
    'margin-right': '5%',
    'padding': '20px 10p',
}

content_first_row = dbc.Row([
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
                                             for x in Expiry_Date_Monthly.Monthly],
                                    value=Expiry_Date_Monthly.Monthly[0],  # default value
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
                        html.Hr(),
                        dbc.Row([
                            dbc.Col([
                                html.H6("CONDITION")
                            ]),
                            dbc.Col([
                                daq.BooleanSwitch(
                                    id="expiry_selection",
                                    on=False,
                                ),
                            ]),
                        ], align="center"),
                        dcc.Dropdown(
                            id='condition',
                            options=[{'label': 'BREAKOUT', 'value': 'BREAKOUT'},
                                     {'label': 'BREAKDOWN', 'value': 'BREAKDOWN'}],
                            value='BREAKOUT',  # default value
                            multi=False,
                            disabled=False
                        ),
                        dcc.Input(id="bba_condition_price", type="number", placeholder="Level", step=0.05, disabled=False),
                        html.Hr(),
                        dbc.Row([
                            dbc.Col([
                                dcc.Input(
                                    id="bba_entry_value", type="number", placeholder="Entry", step=0.05, style={"width": "85px", "height": "30px"}, value=0,)
                            ]),
                            dbc.Col([
                                dcc.Input(
                                    id="bba_stop_loss", type="number", placeholder="Stop Loss", step=0.05, style={"width": "85px", "height": "30px"}, value=0,)
                            ]),
                            dbc.Col([
                                dcc.Input(
                                    id="bba_take_profit", type="number", placeholder="Target", step=0.05, style={"width": "85px", "height": "30px"},  value=0,)
                            ], style= {'display': 'block'}),
                        ], justify="evenly"),
                        dbc.Button(
                            id='place_order',
                            n_clicks=0,
                            children='Place Order',
                            color='success',
                            # className="ml-0",
                            size='Auto'
                        ),
                        html.P(className="card-text", id='order_status', children= "hi"),
                    ],
                    gap=1,
                ),
            ]
        )
    ], width=3),
    dbc.Col([

    ], width=6),
])

content = html.Div(
    [
        content_first_row,
        # content_second_row,
        # content_third_row,
    ],
    # style=CONTENT_STYLE
)

#app = dash.Dash(external_stylesheets=[dbc.themes.BOOTSTRAP])
layout = html.Div([content])


@callback(
    Output('bba_expiry', 'disabled'), Output('bba_option_type', 'disabled'), Output('bba_strike_price', 'disabled'),
    Output('bba_producttype', 'options'), Output('bba_producttype', 'value'),
    Output('bba_qty', 'value'), Output('bba_qty', 'step'),
    Input('bba_instrument', 'value'), Input('bba_symbol', 'value')
)
def input_field_ability(instrument_value, symbol_value):
    lot_size_value = lot_size[lot_size['Symbol'] == symbol_value]['LOT_SIZE'].iloc[0]
    if instrument_value == 'EQ':
        expiry_disability = True
        option_type_disability = True
        strike_price_disability = True
        product_type_options = [
                    {'label': 'CNC', 'value': 'CNC'},
                    {'label': 'INTRADAY', 'value': 'INTRADAY'},
                    {'label': 'CO', 'value': 'CO'},
                    {'label': 'BO', 'value': 'BO'}]
        bba_product_type_value = 'CNC'
        bba_qty_value = 1
        bba_qty_step = 1
    elif instrument_value == 'FUTSTK':
        expiry_disability = False
        option_type_disability = True
        strike_price_disability = True
        product_type_options = [
                   {'label': 'INTRADAY', 'value': 'INTRADAY'},
                   {'label': 'MARGIN', 'value': 'MARGIN'},
                   {'label': 'CO', 'value': 'CO'},
                   {'label': 'BO', 'value': 'BO'}]
        bba_product_type_value = 'MARGIN'
        bba_qty_value = lot_size_value
        bba_qty_step = int(lot_size_value)
    else:
        expiry_disability = False
        option_type_disability = False
        strike_price_disability = False
        product_type_options = [
                    {'label': 'INTRADAY', 'value': 'INTRADAY'},
                    {'label': 'MARGIN', 'value': 'MARGIN'},
                    {'label': 'CO', 'value': 'CO'},
                    {'label': 'BO', 'value': 'BO'}]
        bba_product_type_value = 'MARGIN'
        bba_qty_value = lot_size_value
        bba_qty_step = int(lot_size_value)
    return expiry_disability, option_type_disability, strike_price_disability, \
        product_type_options, bba_product_type_value, \
        bba_qty_value, bba_qty_step


@callback(
    Output('place_order','color'),
    Input('bba_side', 'value')
)
def order_button_colour(position_value):
    if position_value == '-1':
        colour_value = 'danger'
    else:
        colour_value = 'success'
    return colour_value

@callback(
    Output('order_status','children'),
    Input('bba_symbol', 'value'), Input('bba_instrument', 'value'), Input('bba_qty', 'value'),
    Input('bba_ordertype', 'value'), Input('bba_side', 'value'), Input('bba_producttype', 'value'),
    Input('bba_limit_price', 'value'), Input('bba_stop_price', 'value'), Input('bba_disclose_qty', 'value'),
    Input('bba_expiry', 'value'), Input('bba_option_type', 'value'), Input('bba_strike_price', 'value'), Input('bba_stop_loss', 'value'),
    Input('bba_take_profit', 'value'), Input('place_order', 'n_clicks')
)
def place_order(bba_symbol_val, bba_instrument_val, bba_qty_val, bba_ordertype_val, bba_side_val, bba_producttype_val,
                bba_limit_price_val, bba_stop_price_val, bba_disclose_qty_val, bba_expiry_val, bba_option_type_val, bba_strike_price_val,
                bba_stop_loss_val, bba_take_profit_val, place_order_click):  # Place Order Function

    if bba_instrument_val == "EQ":
        symbol_format = "NSE:" + str(bba_symbol_val) + "-EQ"
    elif bba_instrument_val == 'FUTSTK':
        symbol_format = "NSE:" + str(bba_symbol_val) + bba_expiry_val[9:11] + str(bba_expiry_val[3:6]).upper() + 'FUT'
    else:
        symbol_format = "NSE:" + str(bba_symbol_val) + bba_expiry_val[9:11] + str(bba_expiry_val[3:6]).upper() + str(bba_strike_price_val) + str(bba_option_type_val)

    if place_order_click > 1:
        data = {
            "symbol": '{}'.format(symbol_format),
            "qty": int(bba_qty_val),
            "type": int(bba_ordertype_val),
            "side": int(bba_side_val),
            "productType": '{}'.format(bba_producttype_val),
            "limitPrice": bba_limit_price_val,
            "stopPrice": bba_stop_price_val,
            "validity": "DAY",
            "disclosedQty": bba_disclose_qty_val,
            "offlineOrder": "False",
            "stopLoss": bba_stop_loss_val,
            "takeProfit": bba_take_profit_val
        }
        print(data)

        fyers = fyersModel.FyersModel(client_id=client_id, token=token)
        print(fyers.get_profile())
        # print(fyers.place_order(data)['message'])
        return fyers.place_order(data)['message']