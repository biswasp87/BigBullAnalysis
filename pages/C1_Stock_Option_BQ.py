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
import numpy as np
import pandas as pd
from google.cloud import storage
from google.cloud import bigquery
from datetime import date, timedelta, datetime
import dash_daq as daq

fno_watchlist = pd.read_csv("gs://bba_support_files/WL_FNO.csv")
Expiry_Date_Monthly = pd.read_csv("gs://bba_support_files/Expiry_Date_Monthly.csv")


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
                    dcc.Input(id="opt_strike_left", type="number", placeholder="Strike Price", step=1,
                              disabled=False, style={"width": "100px"})
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


@callback(
    Output('graph_option_left', 'figure'),
    Input('memory', 'data'),
    Input('opt_type_left', 'value'),
    Input('opt_strike_left', 'value')
)
def update_left_graph(data, option_type, strike_price):
    option_df = pd.DataFrame(data)
    option_df = option_df[option_df.OPTION_TYP == option_type]
    option_df = option_df[option_df.STRIKE_PR == strike_price]
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
    # Add Volume as Subplot
    fig_left_graph.add_trace(
        go.Bar(x=option_df['TIMESTAMP'], y=option_df['VOLUME'], name='Volume'), row=2, col=1)
    # Add 20 SMA to Volume Subplot
    fig_left_graph.add_trace(go.Scatter(x=option_df['TIMESTAMP'], y=option_df.VOLUME.rolling(20).mean(), name='20SMA Vol'),
                  row=2, col=1)
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
