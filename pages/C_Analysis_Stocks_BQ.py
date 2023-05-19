import dash
# To create meta tag for each page, define the title, image, and description.
dash.register_page(
    __name__,
    # path='/',
    title='FNO Stock Analysis',
    name='Stock Analysis FNO'
)

from dash import Dash, dcc, html, Input, Output, callback
import dash_bootstrap_components as dbc
from dash.dependencies import Input, Output, State
from plotly.subplots import make_subplots
import plotly.graph_objects as go
import numpy as np
import pandas as pd
from google.cloud import storage
from google.cloud import bigquery
from datetime import date, timedelta, datetime


lot_size_list = pd.read_csv("gs://bba_support_files/Lot_Size.csv")
watchlist = pd.read_csv("gs://bba_support_files/WL_ALL.csv")
dropdown_opt_list = pd.read_csv("gs://bba_support_files/Dropdown_options.csv")
Expiry_Date_Monthly = pd.read_csv("gs://bba_support_files/Expiry_Date_Monthly.csv")

# the style arguments for the sidebar.
SIDEBAR_STYLE = {
    # 'position': 'fixed',
    # 'top': 0,
    # 'left': 0,
    # 'bottom': 0,
    # 'width': '20%',
    'height': '550px',
    'padding': '0px 10px',
    'background-color': '#f8f9fa'
}

# the style arguments for the main content page.
CONTENT_STYLE = {
    'margin-left': '2%',
    'margin-right': '5%',
    'padding': '20px 10p'
}

content_first_row = dbc.CardGroup(
    [
        dbc.Card(
            dbc.CardBody(
                [

                ]
            )
        ),
        dbc.Card(
            dbc.CardBody(
                [

                    # dcc.Graph(id='graph_1', config={'displayModeBar': False})
                ]
            )
        ),
        dbc.Card(
            dbc.CardBody(
                [

                ]
            )
        ),
        dbc.Card(
            dbc.CardBody(
                [
                    dbc.Row([
                        dbc.Col([

                        ]),
                        dbc.Col([

                        ])
                            ])
                ]
            )
        ),

    ]
)

content_second_row = dbc.Row(
    [
        dbc.Col(

        )
    ]
)
content_third_row = dbc.Row([
    dbc.Col([dbc.Card(
        dbc.CardBody([
            dcc.Graph(id='graph_31', config=dict({'scrollZoom': True}))
        ])
    )], width=10),
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
                    ),
                    html.H6("Select Stock"),
                    dcc.Dropdown(
                        id='dropdown',
                        options=[{'label': x, 'value': x}
                                 for x in watchlist.Symbol],
                        value='TATAMOTORS',  # default value
                        maxHeight=150,
                    ),
                    html.H6("Select Expiry"),
                    dcc.Dropdown(
                        id='dropdown_exp',
                        options=[{'label': x, 'value': x}
                                 for x in Expiry_Date_Monthly.Monthly],
                        value=Expiry_Date_Monthly.Monthly[0],  # default value
                        multi=False,
                        maxHeight=150,
                    ),
                    html.H6("Select No. of days"),
                    dcc.Dropdown(
                        id='dropdown_n_days',
                        options=[{'label': 7, 'value': 7},
                                 {'label': 15, 'value': 15},
                                 {'label': 30, 'value': 30},
                                 {'label': 60, 'value': 60},
                                 {'label': 90, 'value': 90}],
                        value=30,  # default value
                        multi=False,
                        maxHeight=150,
                    ),
                    html.H6("Pre / Next Stock"),
                    dbc.ButtonGroup([
                        dbc.Button(
                            id='submit_button',
                            n_clicks=0,
                            children='Prev',
                            # color='primary',
                            # className="ml-0",
                            # size='sm',
                        ),
                        dbc.Button(
                            id='submit_button_next',
                            n_clicks=0,
                            children='Next',
                            # color='primary',
                            # className="ml-0",
                            # size='sm'
                        ),
                    ], size='md'),
                    html.Hr(),
                    html.H6("Short Trend Period"),
                    dcc.Input(
                        id="input_short_SMA", type="number", placeholder="Short SMA", min=1, max=200, step=1, value=3, size="sm",
                        style={'width': '150px'}),
                    html.H6("Mid Trend Period"),
                    dcc.Input(
                        id="input_medium_SMA", type="number", placeholder="Medium SMA", min=1, max=200, step=1, value=7, size="sm",
                        style={'width': '150px'}),
                    html.H6("Long Trend Period"),
                    dcc.Input(
                        id="input_long_SMA", type="number", placeholder="Long SMA", min=1, max=200, step=1, value=21, size="sm",
                        style={'width': '150px'}),
                    html.H6("Select Resolution"),
                    dcc.RangeSlider(id='my-range-slider', min=400, max=800, step=50, value=[550], marks=None,),
                    html.H6("Bollinger Band Channel"),
                    dcc.Input(
                        id="b_band_limit", type="number", placeholder="Bollinger Band", min=1, max=200, step=0.1, value=1.5, size="sm",
                        style={'width': '150px'}),
                    html.H6("Keltler Channel"),
                    dcc.Input(
                        id="kc_limit", type="number", placeholder="Keltner Channel", min=1, max=200, step=0.1, value=1.2, size="sm",
                        style={'width': '150px'}),
                ]
            )
        ),
    ], width=2, style=SIDEBAR_STYLE)
])
content = html.Div(
    [
        content_third_row,
    ],
    style=CONTENT_STYLE
)


layout = html.Div([content])


@callback(
    Output('dropdown', 'options'),
    [Input('dropdown_opt', 'value')])
def update_dropdown_list(dropdown_item_value):
    dir_wl = "gs://bba_support_files/" + dropdown_item_value + '.csv'
    wl = pd.read_csv(dir_wl)
    options = [{'label': x, 'value': x}
               for x in wl['Symbol']]
    return options

@callback(
    Output('dropdown', 'value'),
    Input('submit_button', 'n_clicks'),
    Input('submit_button_next', 'n_clicks'),
    Input('dropdown', 'value'),
    Input('dropdown_opt', 'value')
)
def update_dropdown(n_clicks, n_clicks_next, dropdown_value, dropdown_opt_val):

    ctx = dash.callback_context
    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
    dropdown_opt_val = "gs://bba_support_files/" + dropdown_opt_val + str('.csv')
    wl = pd.read_csv(dropdown_opt_val)

    if trigger_id == 'submit_button':
        cur_position = wl[wl['Symbol'] == dropdown_value].index[0]
        cur_position = int(cur_position)
        print(cur_position)
        value_analysis = wl['Symbol'].iloc[cur_position - 1]

    elif trigger_id == 'submit_button_next':
        cur_position = wl[wl['Symbol'] == dropdown_value].index[0]
        cur_position = int(cur_position)
        print(cur_position)
        value_analysis = wl['Symbol'].iloc[cur_position + 1]
    else:
        value_analysis = dropdown_value
    return value_analysis



# @callback(
#     Output('graph_1', 'figure'),
#     [Input('dropdown_exp', 'value'),
#      Input('dropdown', 'value')])
# def update_graph_1(dropdown_exp_value, dropdown_value):
#     print(dropdown_value)
#     print(dropdown_exp_value)
#
#     df_eq_g1 = pd.read_csv(os.path.join(equity_folder_path, (dropdown_value + ".csv")))  # Fetch Equity master Data and filter by Stock Name
#     spot_price = df_eq_g1['CLOSE'].iloc[-1]  # Find the Spot price as per last Closing
#     prev_price = df_eq_g1['CLOSE'].iloc[-2]  # Find the Previous Day price as per Closing
#
#     fig_g1 = go.Figure()
#     fig_g1.add_trace(go.Indicator(
#         mode="number+delta",
#         value=spot_price,
#         delta={'reference': prev_price, 'relative': True},
#     ))
#     fig_g1.update_traces(delta_font={'size': 15})
#     fig_g1.update_traces(number_font_size=15, selector=dict(type='indicator'))
#     fig_g1.update_traces(delta_position='left', selector=dict(type='indicator'))
#     fig_g1.update_traces(number_prefix='Close ', selector=dict(type='indicator'))
#     fig_g1.update_traces(align='left', selector=dict(type='indicator'))
#     fig_g1.update_layout(height=20, width=150)
#     fig_g1.update_layout(margin_l=0)
#     return fig_g1


@callback(
    Output('graph_31', 'figure'),
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

    expiry_date = datetime.strptime(dropdown_exp_value, "%d-%b-%Y").date()
    print(expiry_date)

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
        FROM `phrasal-fire-373510.Big_Bull_Analysis.Master_Data`
        WHERE SYMBOL = '{dropdown_value}'
        ORDER BY TIMESTAMP DESC LIMIT {dropdown_n_days_value}
    """
    df_stock = client.query(sql_stock).to_dataframe()
    df_stock = df_stock.sort_values(by='TIMESTAMP', ascending=True)
    df_stock["BAR"] = df_stock["BAR"].astype(int)
    df_stock = df_stock.reset_index()
    print(df_stock)

    df_stock["CLOSE_MA_S"] = df_stock["EQ_CLOSE_PRICE"].rolling(short_sma).mean()
    df_stock["CLOSE_MA_M"] = df_stock["EQ_CLOSE_PRICE"].rolling(medium_sma).mean()
    df_stock["CLOSE_MA_L"] = df_stock["EQ_CLOSE_PRICE"].rolling(long_sma).mean()
    df_stock["CLOSE_MA_COL"] = np.where((df_stock["CLOSE_MA_S"] > df_stock["CLOSE_MA_M"]) & (df_stock["CLOSE_MA_M"] > df_stock["CLOSE_MA_L"]), 'green',
                                        np.where((df_stock["CLOSE_MA_S"] < df_stock["CLOSE_MA_M"]) & (df_stock["CLOSE_MA_M"] < df_stock["CLOSE_MA_L"]),'red', 'yellow'))

    df_stock["VOLUME_MA_S"] = df_stock["EQ_TTL_TRD_QNTY"].rolling(short_sma).mean()
    df_stock["VOLUME_MA_M"] = df_stock["EQ_TTL_TRD_QNTY"].rolling(medium_sma).mean()
    df_stock["VOLUME_MA_L"] = df_stock["EQ_TTL_TRD_QNTY"].rolling(long_sma).mean()
    df_stock["VOLUME_MA_COL"] = np.where((df_stock["VOLUME_MA_S"] > df_stock["VOLUME_MA_M"]) & (df_stock["VOLUME_MA_M"] > df_stock["VOLUME_MA_L"]), 'green',
                                        np.where((df_stock["VOLUME_MA_M"] > df_stock["VOLUME_MA_S"]) & (df_stock["VOLUME_MA_M"] > df_stock["VOLUME_MA_L"]),'yellow', 'red'))

    df_stock["DEL_MA_S"] = df_stock["EQ_DELIV_QTY"].rolling(short_sma).mean()
    df_stock["DEL_MA_M"] = df_stock["EQ_DELIV_QTY"].rolling(medium_sma).mean()
    df_stock["DEL_MA_L"] = df_stock["EQ_DELIV_QTY"].rolling(long_sma).mean()
    df_stock["DEL_MA_COL"] = np.where((df_stock["DEL_MA_S"] > df_stock["DEL_MA_M"]) & (df_stock["DEL_MA_M"] > df_stock["DEL_MA_L"]), 'green',
                                        np.where((df_stock["DEL_MA_M"] > df_stock["DEL_MA_S"]) & (df_stock["DEL_MA_M"] > df_stock["DEL_MA_L"]),'yellow', 'red'))

    df_stock["DEL_PER_MA_S"] = ((df_stock["EQ_TTL_TRD_QNTY"].rolling(short_sma).sum() /
                                df_stock["EQ_DELIV_QTY"].rolling(short_sma).sum())*100).round(1)
    df_stock["DEL_PER_MA_M"] = ((df_stock["EQ_TTL_TRD_QNTY"].rolling(medium_sma).sum() /
                                df_stock["EQ_DELIV_QTY"].rolling(medium_sma).sum())*100).round(1)
    df_stock["DEL_PER_MA_L"] = ((df_stock["EQ_TTL_TRD_QNTY"].rolling(long_sma).sum() /
                                df_stock["EQ_DELIV_QTY"].rolling(long_sma).sum())*100).round(1)
    df_stock["DEL_PER_MA_COL"] = np.where((df_stock["DEL_PER_MA_S"] > df_stock["DEL_PER_MA_M"]) & (df_stock["DEL_PER_MA_M"] > df_stock["DEL_PER_MA_L"]), 'green',
                                        np.where((df_stock["DEL_PER_MA_M"] > df_stock["DEL_PER_MA_S"]) & (df_stock["DEL_PER_MA_M"] > df_stock["DEL_PER_MA_L"]),'yellow', 'red'))

    df_stock["QT_MA_S"] = df_stock["EQ_QT"].rolling(short_sma).mean()
    df_stock["QT_MA_M"] = df_stock["EQ_QT"].rolling(medium_sma).mean()
    df_stock["QT_MA_L"] = df_stock["EQ_QT"].rolling(long_sma).mean()
    df_stock["QT_MA_COL"] = np.where((df_stock["QT_MA_S"] > df_stock["QT_MA_M"]) & (df_stock["QT_MA_M"] > df_stock["QT_MA_L"]), 'green',
                                        np.where((df_stock["QT_MA_M"] > df_stock["QT_MA_S"]) & (df_stock["QT_MA_M"] > df_stock["QT_MA_L"]),'yellow', 'red'))

    df_stock["FUT_BUILD_UP_COL"] = np.where(((df_stock["FUT_BUILD_UP"] == "LB") | (df_stock["FUT_BUILD_UP"] == "SC")), 'green', 'red')
    df_stock["PCR_MA_COL"] = np.where(df_stock["CUR_PCR"] >= 110, 'green',
                                       np.where((df_stock["CUR_PCR"] < 110) & (df_stock["CUR_PCR"] > 80), 'yellow', 'red'))
    # Consolidation Phase
    df_stock['SMA'] = df_stock['EQ_CLOSE_PRICE'].rolling(window = long_sma).mean()     #Simple Moving Average calculation (period = 20)
    df_stock['stdev'] = df_stock['EQ_CLOSE_PRICE'].rolling(window = long_sma).std()    #Standard Deviation calculation
    df_stock['Lower_Bollinger'] = df_stock['SMA'] - (b_band * df_stock['stdev'])   #Calculation of the lower curve of the Bollinger Bands
    df_stock['Upper_Bollinger'] = df_stock['SMA'] + (b_band * df_stock['stdev'])   #Upper curve

    df_stock['TR'] = abs(df_stock['EQ_HIGH_PRICE'] - df_stock['EQ_LOW_PRICE'])      #True Range calculation
    df_stock['ATR'] = df_stock['TR'].rolling(window = long_sma).mean()    #Average True Range

    df_stock['Upper_KC'] = df_stock['SMA'] + (kc * df_stock['ATR'])      #Upper curve of the Keltner Channel
    df_stock['Lower_KC'] = df_stock['SMA'] - (kc * df_stock['ATR'])      #Lower curve

    # def in_consolidation(df_stock):       #function testing if a symbol is consolidating (Bollinger Bands in Keltner Channel)
    #     return df_stock['Lower_Bollinger'] > df_stock['Lower_KC'] and df_stock['Upper_Bollinger'] < df_stock['Upper_KC']

    df_stock['consolidation'] = np.where((df_stock['Lower_Bollinger'] > df_stock['Lower_KC']) & (df_stock['Upper_Bollinger'] < df_stock['Upper_KC']),"yellow","white")
    df_stock.info(verbose=True)

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
            df_10M_VOL["ENTRY_BO"] = np.where(df_10M_VOL['TIMESTAMP'] >= df_10M_VOL['TIMESTAMP'].iloc[0],
                                      float(df_10M_VOL['EQ_HIGH_PRICE'].iloc[0]), '')
            df_10M_VOL["ENTRY_BD"] = np.where(df_10M_VOL['TIMESTAMP'] >= df_10M_VOL['TIMESTAMP'].iloc[0],
                                      float(df_10M_VOL['EQ_LOW_PRICE'].iloc[0]), '')
    except Exception:
        df_10M_VOL = pd.DataFrame(columns=['TIMESTAMP',
                                           'CUR_FUT_EXPIRY_DT', 'NEAR_FUT_EXPIRY_DT',
                                           'EQ_HIGH_PRICE', 'EQ_LOW_PRICE',
                                           'CUR_PE_STRIKE_PR_10MVOL', 'CUR_CE_STRIKE_PR_10MVOL',
                                           'NEAR_PE_STRIKE_PR_10MVOL', 'NEAR_CE_STRIKE_PR_10MVOL',
                                           'ENTRY_BO', 'ENTRY_BD'])
    # ____________________________________________________________

    fig = make_subplots(
        rows=14, cols=1,
        # row_heights=[0.50, 0.15, 0.1, 0.1, 0.03, 0.12],
        row_heights=[0.38, 0.09, 0.085, 0.085, 0.03, 0.09, 0.03, 0.03, 0.03, 0.03, 0.03, 0.03, 0.03, 0.03],
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
    # edit axis labels
    fig['layout']['yaxis']['title'] = 'Equity OHCL'
    fig['layout']['yaxis2']['title'] = 'Volume'
    fig['layout']['yaxis4']['title'] = 'Q/T'
    fig['layout']['yaxis5']['title'] = 'COI'
    fig['layout']['yaxis7']['title'] = 'PCR'

    return fig
