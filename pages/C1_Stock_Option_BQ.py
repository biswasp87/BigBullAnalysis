import dash
from dash import dcc, html, Input, Output, callback, no_update
import dash_bootstrap_components as dbc
from plotly.subplots import make_subplots
import plotly.graph_objects as go
import pandas as pd
from google.cloud import bigquery
from datetime import datetime

# To create meta tag for each page, define the title, image, and description.
dash.register_page(
    __name__,
    # path='/',
    title='FNO Option Analysis',
    name='Option Analysis'
)

fno_watchlist = pd.read_csv("gs://bba_support_files/WL_FNO.csv")
Expiry_Date_Monthly = pd.read_csv("gs://bba_support_files/stock_expiry_dates.csv")
stock_option_bq_lot_size = pd.read_csv('gs://bba_support_files/Lot_Size.csv')

content_one_screen = html.Div([
    dbc.Row([
        dbc.Col([
            dbc.Card(
                dbc.CardBody([
                    html.Label("Option Chain Symbol", style={'color':'blue', 'padding':'8px'}),
                    dcc.Dropdown(
                        id='opt_symbol_left',
                        options=[{'label': x, 'value': x}
                                 for x in fno_watchlist.Symbol],
                        value='TATAMOTORS',  # default value
                        multi=False,
                    ),
                    html.Label("Option Chain Expiry", style={'color':'blue', 'padding':'8px'}),
                    dcc.Dropdown(
                        id='opt_expiry_left',
                        options=[{'label': x, 'value': x}
                                 for x in Expiry_Date_Monthly.NEAR_FUT_EXPIRY_DT],
                        value=Expiry_Date_Monthly.NEAR_FUT_EXPIRY_DT[1],  # default value
                        multi=False,
                        maxHeight=150,
                    ),
                    html.Hr(),
                    html.Label("Upper Graph Values", style={'color':'blue', 'padding':'8px'}),
                    dbc.Row([
                        dbc.Col([
                            dcc.Dropdown(
                                id='opt_type_left',
                                options=[{'label': 'CALL', 'value': 'CE'},
                                         {'label': 'PUT', 'value': 'PE'}],
                                value='CE',  # default value
                                multi=False,
                                disabled=False
                            ),
                        ]),
                        dbc.Col([
                            dcc.Dropdown(
                                id='opt_strike_left',
                                options=[],
                                # value='CE',  # default value
                                multi=False,
                                disabled=False
                            ),
                        ]),
                    ]),
                    # html.Br(),
                    # html.Hr(),
                    html.Label("Lower Graph Values", style={'color':'blue', 'padding':'8px'}),
                    dbc.Row([
                        dbc.Col([
                            dcc.Dropdown(
                                id='opt_type_right',
                                options=[{'label': 'CALL', 'value': 'CE'},
                                         {'label': 'PUT', 'value': 'PE'}],
                                value='PE',  # default value
                                multi=False,
                                disabled=False
                            ),
                        ]),
                        dbc.Col([
                            dcc.Input(id="opt_strike_right", type="number", placeholder="Strike Price", step=1,
                                      disabled=False, style={"width": "100px"}),
                        ]),
                    ]),
                    html.Hr(),
                    html.Label("Option Chain Date", style={'color':'blue', 'padding':'8px'}),
                    dbc.Row([
                        dbc.Col([
                            dcc.Dropdown(
                                id='oi_chain_date',
                                multi=False,
                                maxHeight=150,
                            ),
                        ]),dbc.Col([
                            dbc.ButtonGroup([
                                dbc.Button(
                                    id='prev_opt_chn_dt',
                                    n_clicks=0,
                                    children='Prev',
                                ),
                                dbc.Button(
                                    id='next_opt_chn_dt',
                                    n_clicks=0,
                                    children='Next',
                                ),
                            ], size='md'),
                        ])
                    ])
                ])
            )
        ], lg=2, xs=12, sm=12),
        dbc.Col([
            dbc.Card(
                dbc.CardBody([
                        dcc.Graph(id='graph_option_left', style={'height': '40vh'})
                ])
            ),
            html.Br(),
            dbc.Card(
                dbc.CardBody([
                    dcc.Graph(id='graph_option_right', style={'height': '40vh'})
                ])
            )
        ], lg=5, xs=12, sm=12),
        dbc.Col([
            dbc.Card(
                dbc.CardBody([
                        dcc.Graph(id='graph_option_chain_oi', style={'height': '25vh'})
                ])
            ),
            html.Br(),
            dbc.Card(
                dbc.CardBody([
                    dcc.Graph(id='graph_option_chain_chg_oi', style={'height': '25vh'})
                ])
            ),
            html.Br(),
            dbc.Card(
                dbc.CardBody([
                    dcc.Graph(id='graph_option_chain_volume', style={'height': '25vh'})
                ])
            )
        ], lg=5, xs=12, sm=12)
    ])
])

layout = html.Div([html.Br(), dcc.Store(id='memory', data=[], storage_type='local'), content_one_screen])

# _____________________________________________________________________________________
# Set Option SYMBOL from the data of Stock Analysis Page
# _____________________________________________________________________________________
@callback(Output('opt_symbol_left', 'value'), Input('df_indicator', 'data'))
def update_symbol_strikeprice_value(data_value):
    option_df = pd.DataFrame(data_value)
    return option_df["SYMBOL"].iloc[0]
# _____________________________________________________________________________________
# Fetch Data from Biq Query and as per the SYMBOL set in previous callback
# and save local Memory for further usage
# _____________________________________________________________________________________
@callback(
    Output('memory', 'data'),
    Input('opt_symbol_left', 'value'),
    Input('opt_expiry_left', 'value')
)
def store_data(symbol, expiry):
    expiry = datetime.strptime(expiry, '%Y-%m-%d').date()
    table_cur_name = 'STO_' + str(expiry)
    table_id = "phrasal-fire-373510.Option_Data.{}".format(table_cur_name)
    client = bigquery.Client()
    sql = f"""
        SELECT *
        FROM `{table_id}`
        WHERE SYMBOL = "{symbol}"
        ORDER BY TIMESTAMP DESC
    """
    df_option = client.query(sql).to_dataframe()
    return df_option.to_dict('records')

# _____________________________________________________________________________________
# Update Strike Price Dropdown menu option of upper Graph and
# Strike Price Value of Left Graph from the fetched option data
# _____________________________________________________________________________________
@callback(
    Output('opt_strike_left', 'options'),
    Output('opt_strike_left', 'value'),
    Output('opt_strike_right', 'value'),
    Input('memory', 'data'),
    Input('df_indicator', 'data')
)
def update_strike_price_values(memory_data, data_value):
    option_df_full = pd.DataFrame(memory_data)
    option_df_full['STRIKE_PR'] = option_df_full['STRIKE_PR'].astype(float).astype(int)
    stk_pr_list = option_df_full.STRIKE_PR.unique()
    stk_pr_list.sort()

    option_df = pd.DataFrame(data_value)
    closing_price = option_df["EQ_CLOSE_PRICE"].iloc[-1]
    if option_df["SYMBOL"].iloc[0] == option_df_full["SYMBOL"].iloc[0]:
        strike_price_left = min(stk_pr_list, key=lambda x: abs(x - closing_price))
        strike_price_right = min(stk_pr_list, key=lambda x: abs(x - closing_price))
        return stk_pr_list, strike_price_left, strike_price_right
    else:
        stk_pr_list_len = len(stk_pr_list)
        stk_pr_position = int(stk_pr_list_len/2)
        strike_price_left = stk_pr_list[stk_pr_position]
        strike_price_right = stk_pr_list[stk_pr_position]
        return stk_pr_list, strike_price_left, strike_price_right

# _____________________________________________________________________________________
# Upper Graph
# _____________________________________________________________________________________
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
    option_df.style.format({"TIMESTAMP": lambda t: t.strftime("%Y-%m-%d")})
    option_df["TIMESTAMP"] = pd.to_datetime(option_df.TIMESTAMP, dayfirst=True,format='%Y-%m-%d')
    option_df = option_df[option_df.OPTION_TYP == option_type]
    option_df = option_df[option_df.STRIKE_PR == strike_price].sort_values(by='TIMESTAMP', ascending=True)

    try:
        if option_df["SYMBOL"].iloc[0] == analysis_stock_df_store["SYMBOL"].iloc[0]:
            option_level_df = option_df[option_df.TIMESTAMP == analysis_stock_df_store['10M_VOL_TIMESTAMP'].iloc[0]]
            if option_type == 'CE':
                entry_left_graph = round(float(option_level_df['HIGH'].iloc[0]), 2)
                sl_left_graph = round(float(option_level_df['LOW'].iloc[0]), 2)
            else:
                entry_left_graph = round(float(option_level_df['LOW'].iloc[0]), 2)
                sl_left_graph = round(float(option_level_df['HIGH'].iloc[0]), 2)

            present_value_left_graph = round(option_df['CLOSE'].iloc[-1], 2)
            lot_size_left_graph_df = stock_option_bq_lot_size[stock_option_bq_lot_size.Symbol == analysis_stock_df_store['SYMBOL'].iloc[0]]
            lot_size_left_graph = int(lot_size_left_graph_df['LOT_SIZE'].iloc[0])
            entry_value_lg = round((lot_size_left_graph * entry_left_graph), 2)
            exit_value_lg = round(lot_size_left_graph * sl_left_graph, 2)
            present_value_lg = round(lot_size_left_graph*present_value_left_graph, 2)
            if option_type == 'CE' and present_value_lg > entry_value_lg:
                line_color = 'green'
            elif option_type == 'CE' and present_value_lg < entry_value_lg:
                line_color = 'red'
            elif option_type == 'PE' and present_value_lg < entry_value_lg:
                line_color = 'green'
            else:
                line_color = 'red'
            text_left_graph = "Lot Size:{}\nExit Value:{}\nEntry Value:{}\nPresent Value:{}".format(
                lot_size_left_graph, exit_value_lg, entry_value_lg, present_value_lg)
    except Exception:
        pass
        # print(text_left_graph)

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
    try:
        if option_df["SYMBOL"].iloc[0] == analysis_stock_df_store["SYMBOL"].iloc[0]:
            fig_left_graph.add_hline(y=entry_left_graph, line_dash="dot", row=1, col=1, annotation_text="Entry",
                                     annotation_position="top right")
            # # Add SL Lines
            fig_left_graph.add_hline(y=sl_left_graph, line_dash="dot", row=1, col=1, annotation_text="Stop Loss",
                                     annotation_position="bottom right", line_color='red')
            # # Add Annonation
            fig_left_graph.add_hline(y=present_value_left_graph, line_dash="dot", row=1, col=1, annotation_text=text_left_graph,
                                     annotation_position="bottom left", line_color=line_color)
    except Exception:
        pass
    # Add Volume as Subplots
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
# _____________________________________________________________________________________
# Lower Graph
# _____________________________________________________________________________________
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
# # _____________________________________________________________________________________
# # Process unique dates available and update the 'oi chain date' option and values
# # _____________________________________________________________________________________
# @callback(
#     Output('oi_chain_date', 'options'),
#     Output('oi_chain_date', 'value'),
#     Input('memory', 'data')
# )
# def option_chain_dates(oi_data):
#     oi_date = pd.DataFrame(oi_data)
#     oi_date_options = [{'label': x, 'value': x}
#                for x in oi_date.TIMESTAMP.unique()]
#     oi_date_value = oi_date.TIMESTAMP[0]  # default value
#     return oi_date_options, oi_date_value
# _____________________________________________________________________________________
# open interest Graph
# _____________________________________________________________________________________
@callback(
    Output('graph_option_chain_oi', 'figure'),
    Input('memory', 'data'),
    Input('oi_chain_date', 'value')
)
def update_option_chain_graph(data, oi_chain_date):
    df_option_chain = pd.DataFrame(data)
    df_option_chain = df_option_chain[df_option_chain.TIMESTAMP == oi_chain_date]
    df_option_chain_pe = df_option_chain[df_option_chain.OPTION_TYP == "PE"]
    df_option_chain_ce = df_option_chain[df_option_chain.OPTION_TYP == "CE"]

    df_option_chain_ce = df_option_chain_ce.sort_values(by=['STRIKE_PR'])
    df_option_chain_ce.reset_index(drop=True, inplace=True)
    df_option_chain_pe = df_option_chain_pe.sort_values(by=['STRIKE_PR'])
    df_option_chain_pe.reset_index(drop=True, inplace=True)


    fig_option_chain = go.Figure(data=[
        go.Bar(name='CALL', x=df_option_chain_ce["STRIKE_PR"].astype(str), y=df_option_chain_ce["OPEN_INT"], marker=dict(color='rgb(255, 0, 0)')),
        go.Bar(name='PUT', x=df_option_chain_pe["STRIKE_PR"].astype(str), y=df_option_chain_pe["OPEN_INT"], marker=dict(color='rgb(0, 204, 0)'))
    ])
    # Change the bar mode
    fig_option_chain.update_layout(barmode='group')
    fig_option_chain.update_layout(paper_bgcolor='rgb(255,255,255)', plot_bgcolor='rgb(255,255,255)')
    fig_option_chain.update_layout(margin=dict(r=2, t=2, b=2, l=2))
    fig_option_chain.update_layout(showlegend=False)
    fig_option_chain.update_layout(xaxis_title='Open Interest')
    return fig_option_chain
# _____________________________________________________________________________________
# Change in open interest Graph
# _____________________________________________________________________________________
@callback(
    Output('graph_option_chain_chg_oi', 'figure'),
    Input('memory', 'data'),
    Input('oi_chain_date', 'value')
)
def update_option_chain_graph(data, oi_chain_date):
    df_option_chain_ch_oi = pd.DataFrame(data)
    df_option_chain_ch_oi = df_option_chain_ch_oi[df_option_chain_ch_oi.TIMESTAMP == oi_chain_date]
    df_option_chain_pe_chg_oi = df_option_chain_ch_oi[df_option_chain_ch_oi.OPTION_TYP == "PE"]
    df_option_chain_ce_chg_oi = df_option_chain_ch_oi[df_option_chain_ch_oi.OPTION_TYP == "CE"]

    df_option_chain_ce_chg_oi = df_option_chain_ce_chg_oi.sort_values(by=['STRIKE_PR'])
    df_option_chain_ce_chg_oi.reset_index(drop=True, inplace=True)
    df_option_chain_pe_chg_oi = df_option_chain_pe_chg_oi.sort_values(by=['STRIKE_PR'])
    df_option_chain_pe_chg_oi.reset_index(drop=True, inplace=True)

    fig_option_chain_chg_oi = go.Figure(data=[
        go.Bar(name='CALL', x=df_option_chain_ce_chg_oi["STRIKE_PR"].astype(str), y=df_option_chain_ce_chg_oi["CHG_IN_OI"], marker=dict(color='rgb(255, 0, 0)')),
        go.Bar(name='PUT', x=df_option_chain_pe_chg_oi["STRIKE_PR"].astype(str), y=df_option_chain_pe_chg_oi["CHG_IN_OI"], marker=dict(color='rgb(0, 204, 0)'))
    ])
    # Change the bar mode
    fig_option_chain_chg_oi.update_layout(barmode='group')
    fig_option_chain_chg_oi.update_layout(paper_bgcolor='rgb(255,255,255)', plot_bgcolor='rgb(255,255,255)')
    fig_option_chain_chg_oi.update_layout(margin=dict(r=2, t=2, b=2, l=2))
    fig_option_chain_chg_oi.update_layout(showlegend=False)
    fig_option_chain_chg_oi.update_layout(xaxis_title='Change in Open Interest')
    return fig_option_chain_chg_oi
# _____________________________________________________________________________________
# Option Volume Graph
# _____________________________________________________________________________________
@callback(
    Output('graph_option_chain_volume', 'figure'),
    Input('memory', 'data'),
    Input('oi_chain_date', 'value')
)
def update_option_chain_graph(data, oi_chain_date):
    df_option_chain_volume = pd.DataFrame(data)
    df_option_chain_volume = df_option_chain_volume[df_option_chain_volume.TIMESTAMP == oi_chain_date]
    df_option_chain_volume_pe = df_option_chain_volume[df_option_chain_volume.OPTION_TYP == "PE"]
    df_option_chain_volume_ce = df_option_chain_volume[df_option_chain_volume.OPTION_TYP == "CE"]

    df_option_chain_volume_ce = df_option_chain_volume_ce.sort_values(by=['STRIKE_PR'])
    df_option_chain_volume_ce.reset_index(drop=True, inplace=True)
    df_option_chain_volume_pe = df_option_chain_volume_pe.sort_values(by=['STRIKE_PR'])
    df_option_chain_volume_pe.reset_index(drop=True, inplace=True)

    fig_option_chain_volume = go.Figure(data=[
        go.Bar(name='CALL', x=df_option_chain_volume_ce["STRIKE_PR"].astype(str), y=df_option_chain_volume_ce["VOLUME"], marker=dict(color='rgb(255, 0, 0)')),
        go.Bar(name='PUT', x=df_option_chain_volume_pe["STRIKE_PR"].astype(str), y=df_option_chain_volume_pe["VOLUME"], marker=dict(color='rgb(0, 204, 0)'))
    ])
    # Change the bar mode
    fig_option_chain_volume.update_layout(barmode='group')
    fig_option_chain_volume.update_layout(paper_bgcolor='rgb(255,255,255)', plot_bgcolor='rgb(255,255,255)')
    fig_option_chain_volume.update_layout(margin=dict(r=2, t=2, b=2, l=2))
    fig_option_chain_volume.update_layout(showlegend=False)
    # Add 10M Vol Horizontal Line
    fig_option_chain_volume.add_hline(y=10000000, line_dash="dot", annotation_text="Volume Benchmark",
                             annotation_position="bottom right")
    fig_option_chain_volume.update_layout(xaxis_title='Option Volume')
    return fig_option_chain_volume

# _____________________________________________________________________________________
# Process unique dates available and update the 'oi chain date' option and values
# _____________________________________________________________________________________
@callback(
    Output('oi_chain_date', 'options'),
    Output('oi_chain_date', 'value'),
    Input('memory', 'data'),
    Input('prev_opt_chn_dt', 'n_clicks'),
    Input('next_opt_chn_dt', 'n_clicks'),
    Input('oi_chain_date', 'value'),
)
def option_chain_dates(oi_data, nclick_prev, n_click_next, oi_date_dropdown):
    oi_date = pd.DataFrame(oi_data)
    ctx = dash.callback_context
    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
    print(trigger_id)

    if trigger_id == 'prev_opt_chn_dt':
        cur_position = oi_date[oi_date['TIMESTAMP'] == oi_date_dropdown].index[0]
        cur_position = int(cur_position)
        oi_date_value = oi_date['TIMESTAMP'].iloc[cur_position + 1]
        oi_date_options = [{'label': x, 'value': x}
                   for x in oi_date.TIMESTAMP.unique()]
        return oi_date_options, oi_date_value
    elif trigger_id == 'next_opt_chn_dt':
        cur_position = oi_date[oi_date['TIMESTAMP'] == oi_date_dropdown].index[0]
        cur_position = int(cur_position)
        oi_date_value = oi_date['TIMESTAMP'].iloc[cur_position - 1]
        oi_date_options = [{'label': x, 'value': x}
                   for x in oi_date.TIMESTAMP.unique()]
        return oi_date_options, oi_date_value
    else:
        oi_date_value = oi_date.TIMESTAMP[0]  # default value
        oi_date_options = [{'label': x, 'value': x}
                   for x in oi_date.TIMESTAMP.unique()]
        return oi_date_options, oi_date_value
