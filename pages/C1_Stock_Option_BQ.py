import dash
# To create meta tag for each page, define the title, image, and description.
dash.register_page(
    __name__,
    # path='/',
    title='FNO Option Analysis',
    name='Option Analysis'
)

from dash import Dash, dcc, html, Input, Output, callback
import dash_bootstrap_components as dbc
from plotly.subplots import make_subplots
import plotly.graph_objects as go
import plotly.express as px
import numpy as np
import pandas as pd
from google.cloud import storage
from google.cloud import bigquery
from datetime import date, timedelta, datetime
import dash_daq as daq

fno_watchlist = pd.read_csv("gs://bba_support_files/WL_FNO.csv")
Expiry_Date_Monthly = pd.read_csv("gs://bba_support_files/Expiry_Date_Monthly.csv")
stock_option_bq_lot_size = pd.read_csv('gs://bba_support_files/Lot_Size.csv')


# the style arguments for the main content page.
CONTENT_STYLE = {
    # 'margin-left': '2%',
    # 'margin-right': '5%',
    'margin-top': '1%'
    # 'padding': '20px 10p'
}
content_upper_left_row = dbc.CardGroup(
    [
        dbc.Card(
            dbc.CardBody(
                [
                    dcc.Dropdown(
                        id='opt_symbol_left',
                        options=[{'label': x, 'value': x}
                                 for x in fno_watchlist.Symbol],
                        value='TATAMOTORS',  # default value
                        multi=False,
                    )
                ]
            )
        ),
        dbc.Card(
            dbc.CardBody(
                [
                    dcc.Dropdown(
                        id='opt_expiry_left',
                        options=[{'label': x, 'value': x}
                                 for x in Expiry_Date_Monthly.Monthly],
                        value=Expiry_Date_Monthly.Monthly[0],  # default value
                        multi=False,
                        maxHeight=150,
                    ),
                ]
            )
        ),
        dbc.Card(
            dbc.CardBody(
                [
                    dcc.Dropdown(
                        id='opt_type_left',
                        options=[{'label': 'CALL', 'value': 'CE'},
                                 {'label': 'PUT', 'value': 'PE'}],
                        value='CE',  # default value
                        multi=False,
                        disabled=False
                    ),
                ]
            )
        ),
        dbc.Card(
            dbc.CardBody(
                [
                    # dcc.Input(id="opt_strike_left", type="number", placeholder="Strike Price", step=1,
                    #           disabled=False, style={"width": "100px"})
                    dcc.Dropdown(
                        id='opt_strike_left',
                        options=[],
                        # value='CE',  # default value
                        multi=False,
                        disabled=False
                    ),
                ]
            )
        ),

    ]
)

content_upper_right_row = dbc.CardGroup(
    [
        dbc.Card(
            dbc.CardBody(
                [
                    dcc.Dropdown(
                        id='opt_symbol_right',
                        options=[{'label': x, 'value': x}
                                 for x in fno_watchlist.Symbol],
                        value='TATAMOTORS',  # default value
                        multi=False,
                    )
                ]
            )
        ),
        dbc.Card(
            dbc.CardBody(
                [
                    dcc.Dropdown(
                        id='opt_expiry_right',
                        options=[{'label': x, 'value': x}
                                 for x in Expiry_Date_Monthly.Monthly],
                        value=Expiry_Date_Monthly.Monthly[0],  # default value
                        multi=False,
                        maxHeight=150,
                    ),
                ]
            )
        ),
        dbc.Card(
            dbc.CardBody(
                [
                    dcc.Dropdown(
                        id='opt_type_right',
                        options=[{'label': 'CALL', 'value': 'CE'},
                                 {'label': 'PUT', 'value': 'PE'}],
                        value='CE',  # default value
                        multi=False,
                        disabled=False
                    ),
                ]
            )
        ),
        dbc.Card(
            dbc.CardBody(
                [
                    dcc.Input(id="opt_strike_right", type="number", placeholder="Strike Price", step=1,
                              disabled=False, style={"width": "100px"})
                ]
            )
        ),

    ]
)

content_lower_left_row = dbc.CardGroup(
    [
        dbc.Card(
            dbc.CardBody(
                [
                    dcc.Graph(id='graph_option_left')
                ]
            )
        ),
    ]
)

content_lower_right_row = dbc.CardGroup(
    [
        dbc.Card(
            dbc.CardBody(
                [
                    dcc.Graph(id='graph_option_right')
                ]
            )
        ),
    ]
)
content_first_row = dbc.Row([
    dbc.Col([
        dbc.Row([
            content_upper_left_row
        ]),
        dbc.Row([
            content_lower_left_row
        ],style=CONTENT_STYLE),
    ], width=6),
    dbc.Col([
        dbc.Row([
            content_upper_right_row
        ]),
        dbc.Row([
            content_lower_right_row
        ], style=CONTENT_STYLE)
    ], width=6)
])

content = html.Div(
    [
        content_first_row,
    ]
)


layout = html.Div([dcc.Store(id='memory', data=[], storage_type='memory'), content])

@callback(
    Output('memory', 'data'),
    Input('opt_symbol_left', 'value'),
    Input('opt_expiry_left', 'value')
)
def store_data(symbol, expiry):
    expiry = datetime.strptime(expiry, '%d-%b-%Y').date()
    table_cur_name = 'FUTSTK_' + str(expiry)
    table_id = "phrasal-fire-373510.FNO_Data.{}".format(table_cur_name)
    client = bigquery.Client()
    sql = f"""
        SELECT *
        FROM `{table_id}`
        WHERE SYMBOL = "{symbol}"
        ORDER BY TIMESTAMP DESC
    """
    df_option = client.query(sql).to_dataframe()
    return df_option.to_dict('records')


@callback(Output('opt_symbol_left', 'value'), Input('df_indicator', 'data'))
def update_symbol_strikeprice_value(data_value):
    option_df = pd.DataFrame(data_value)
    symbol = option_df["SYMBOL"].iloc[0]
    return symbol


@callback(
    Output('opt_strike_left', 'options'),
    Output('opt_strike_left', 'value'),
    Output('opt_type_left', 'value'),
    Output('opt_strike_right', 'value'),
    Input('memory', 'data'),
    Input('df_indicator', 'data')
)
def update_strike_price_values(memory_data, data_value):
    option_df_full = pd.DataFrame(memory_data)
    option_df = pd.DataFrame(data_value)
    option_df['CUR_CE_STRIKE_PR_10MVOL'] = option_df['CUR_CE_STRIKE_PR_10MVOL'].astype(float).astype(int)
    stk_pr_list_ce = option_df.CUR_CE_STRIKE_PR_10MVOL.unique()
    stk_pr_list_pe = option_df.CUR_PE_STRIKE_PR_10MVOL.unique()
    option_df_full['STRIKE_PR'] = option_df_full['STRIKE_PR'].astype(float).astype(int)
    stk_pr_list = option_df_full.STRIKE_PR.unique()

    strike_price_left = option_df["CUR_CE_STRIKE_PR_10MVOL"].iloc[0]
    strike_price_right = option_df["CUR_CE_STRIKE_PR_10MVOL"].iloc[0]
    if stk_pr_list_ce[0] == 'None':
        opt_type = 'PE'
        return stk_pr_list, strike_price_left, opt_type, strike_price_right
    else:
        opt_type = 'CE'
        return stk_pr_list, strike_price_left, opt_type, strike_price_right


@callback(
    Output('graph_option_left', 'figure'),
    Input('memory', 'data'),
    Input('df_indicator', 'data'),
    Input('opt_type_left', 'value'),
    Input('opt_strike_left', 'value')
)
def update_left_graph(data, indicator_data, option_type, strike_price):
    analysis_stock_df_store = pd.DataFrame(indicator_data)
    option_df = pd.DataFrame(data)
    option_df = option_df[option_df.OPTION_TYP == option_type]
    option_df = option_df[option_df.STRIKE_PR == strike_price].sort_values(by='TIMESTAMP', ascending=True)
    option_level_df = option_df[option_df.TIMESTAMP == analysis_stock_df_store['TIMESTAMP'].iloc[0]]
    entry_left_graph = float(option_level_df['HIGH'].iloc[0])
    sl_left_graph = float(option_level_df['LOW'].iloc[0])
    present_value_left_graph = option_df['CLOSE'].iloc[-1]

    lot_size_left_graph_df = stock_option_bq_lot_size[stock_option_bq_lot_size.Symbol == analysis_stock_df_store['SYMBOL'].iloc[0]]
    lot_size_left_graph = int(lot_size_left_graph_df['LOT_SIZE'].iloc[0])
    entry_value_lg = (lot_size_left_graph * entry_left_graph)
    exit_value_lg = lot_size_left_graph * sl_left_graph
    present_value_lg = lot_size_left_graph*present_value_left_graph
    text_left_graph = "Lot Size:{}\nExit Value:{}\nEntry Value:{}\nPresent Value:{}".format(
        lot_size_left_graph, exit_value_lg, entry_value_lg, present_value_lg)
    print(text_left_graph)
    fig_left_graph = make_subplots(
        rows=3, cols=1,
        row_heights=[0.6, 0.2, 0.2],
        specs=[[{}], [{}], [{}]],
        print_grid=False, shared_xaxes=True, horizontal_spacing=0.05, vertical_spacing=0)

    fig_left_graph.update_layout(paper_bgcolor='rgb(255,255,255)', plot_bgcolor='rgb(255,255,255)')  #  height=graph_height[0]
    fig_left_graph.update_layout(margin=dict(r=2, t=2, b=2, l=2))
    fig_left_graph.update_xaxes(showline=True, linewidth=2, linecolor='black')
    fig_left_graph.update_yaxes(mirror="ticks", side='right')
    fig_left_graph.update_layout(dragmode='drawline', newshape_line_color='cyan')
    fig_left_graph.update_layout(showlegend=False)
    fig_left_graph.update_layout(xaxis1=dict(rangeslider_visible=False))

    # include Equity candlestick without rangeselector
    fig_left_graph.add_trace(go.Candlestick(x=option_df['TIMESTAMP'],
                                 open=option_df['OPEN'], high=option_df['HIGH'],
                                 low=option_df['LOW'], close=option_df['CLOSE'], name='Price'), row=1, col=1)
    # # Add ENTRY Lines.....................
    fig_left_graph.add_hline(y=entry_left_graph, line_dash="dot", row=1, col=1, annotation_text="Entry",
                             annotation_position="top right")
    # # Add SL Lines
    fig_left_graph.add_hline(y=sl_left_graph, line_dash="dot", row=1, col=1, annotation_text="Stop Loss",
                             annotation_position="bottom right")
    # # Add Annonation
    fig_left_graph.add_hline(y=present_value_left_graph, line_dash="dot", row=1, col=1, annotation_text=text_left_graph,
                             annotation_position="bottom left")
    # Add Volume as Subplot
    fig_left_graph.add_trace(
        go.Bar(x=option_df['TIMESTAMP'], y=option_df['VOLUME'], name='Volume'), row=2, col=1)
    # Add 20 SMA to Volume Subplot
    fig_left_graph.add_hline(y=10000000, line_dash="dot", row=2, col=1, annotation_text="Volume Benchmark",
                             annotation_position="bottom right")
    # Add Open Interest as Subplot
    fig_left_graph.add_trace(go.Scatter(x=option_df['TIMESTAMP'], y=option_df['OPEN_INT'], name='OI'),
                  row=3, col=1)
    fig_left_graph['layout']['yaxis1']['title'] = 'Option OHCL'
    fig_left_graph['layout']['yaxis2']['title'] = 'Volume'
    fig_left_graph['layout']['yaxis3']['title'] = 'OI'
    return fig_left_graph

@callback(
    Output('graph_option_right', 'figure'),
    Input('memory', 'data'),
    Input('opt_type_right', 'value'),
    Input('opt_strike_right', 'value')
)
def update_right_graph(data, option_type, strike_price):
    option_df = pd.DataFrame(data)
    option_df = option_df[option_df.OPTION_TYP == option_type]
    option_df = option_df[option_df.STRIKE_PR == strike_price]
    fig_right_graph = make_subplots(
        rows=3, cols=1,
        row_heights=[0.6, 0.2, 0.2],
        specs=[[{}], [{}], [{}]],
        print_grid=True, shared_xaxes=True, horizontal_spacing=0.05, vertical_spacing=0)

    fig_right_graph.update_layout(paper_bgcolor='rgb(255,255,255)', plot_bgcolor='rgb(255,255,255)')  #  height=graph_height[0]
    fig_right_graph.update_layout(margin=dict(r=2, t=2, b=2, l=2))
    fig_right_graph.update_xaxes(showline=True, linewidth=2, linecolor='black')
    fig_right_graph.update_yaxes(mirror="ticks", side='right')
    fig_right_graph.update_layout(dragmode='drawline', newshape_line_color='cyan')
    fig_right_graph.update_layout(showlegend=False)
    fig_right_graph.update_layout(xaxis1=dict(rangeslider_visible=False))
    # include Equity candlestick without rangeselector
    fig_right_graph.add_trace(go.Candlestick(x=option_df['TIMESTAMP'],
                                 open=option_df['OPEN'], high=option_df['HIGH'],
                                 low=option_df['LOW'], close=option_df['CLOSE'], name='Price'), row=1, col=1)
    # Add Volume as Subplot
    fig_right_graph.add_trace(
        go.Bar(x=option_df['TIMESTAMP'], y=option_df['VOLUME'], name='Volume', offsetgroup=1), row=2, col=1)
    # Add 20 SMA to Volume Subplot
    fig_right_graph.add_trace(go.Scatter(x=option_df['TIMESTAMP'], y=option_df.VOLUME.rolling(20).mean(), name='20SMA Vol'),
                  row=2, col=1)
    # Add Open Interest as Subplot
    fig_right_graph.add_trace(go.Scatter(x=option_df['TIMESTAMP'], y=option_df['OPEN_INT'], name='OI'),
                  row=3, col=1)
    fig_right_graph['layout']['yaxis1']['title'] = 'Option OHCL'
    fig_right_graph['layout']['yaxis2']['title'] = 'Volume'
    fig_right_graph['layout']['yaxis3']['title'] = 'OI'
    return fig_right_graph
