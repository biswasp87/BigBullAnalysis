import dash
import requests

# To create meta tag for each page, define the title, image, and description.
dash.register_page(
    __name__,
    path='/',
    title='Home',
    name='App Details'
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


master_data = pd.read_csv("gs://biswasp87/last_Master_Data_updated_on.csv")
watchlist = pd.read_csv("gs://biswasp87/last_watchlist_updated_on.csv")
scanner = pd.read_csv("gs://biswasp87/last_scanner_updated_on.csv")


first_card_content = [
    dbc.CardHeader("Master Data"),
    dbc.CardBody(
        [
            html.H5(className="card-title", id='master_data_heading'),
            html.P(className="card-text", id='master_data'),
            dbc.Button("Update Now", color="primary", id='master_data_button'),
        ]
    ),
]
second_card_content = [
    dbc.CardHeader("Watchlist"),
    dbc.CardBody(
        [
            html.H5("Last Updated on", className="card-title"),
            html.P(
                "This is some card content that we'll reuse",
                className="card-text",
            ),
            dbc.Button("Update Now", color="primary", href="https://asia-south1-phrasal-fire-373510.cloudfunctions.net/BBA_Update_Scanner_10m_opt_vol"),
        ]
    ),
]
third_card_content = [
    dbc.CardHeader("Scanner"),
    dbc.CardBody(
        [
            html.H5("Last Updated on", className="card-title"),
            html.P(
                "This is some card content that we'll reuse",
                className="card-text",
            ),
            dbc.Button("Update Now", color="primary"),
        ]
    ),
]
content_first_row = dbc.Row(
    [
        dbc.Col(dbc.Card(first_card_content, color="info", inverse=True)),
        dbc.Col(dbc.Card(second_card_content, color="info", inverse=True)),
        dbc.Col(dbc.Card(third_card_content, color="info", inverse=True)),
    ],
    className="mb-4"
)

content = html.Div(
    [
        content_first_row,
    ],
)

layout = html.Div([content])

@callback(
    Output(component_id='master_data_heading', component_property='children'),
    Output(component_id='master_data', component_property='children'),
    Input(component_id='master_data_button', component_property='n_clicks')
)
def update_output_div(n):
    master_data_last_updated_on = master_data['last_data_day'].iloc[-1]
    if n is None:
        text_one = "Last Updated on " + master_data_last_updated_on
        text_two = "Click on the Button for Manual Update"
        return text_one, text_two
    elif n == 1:
        r = requests.post("https://asia-south1-phrasal-fire-373510.cloudfunctions.net/BBA_Update_Scanner_10m_opt_vol")
        r_status = r.status_code
        if r_status == 200:
            text_one = 'Updated Successfully at ' + str(datetime.now())
            text_two = ''
            return text_one, text_two
        else:
            text_one = 'Update failed with HTTP error ' + str(r_status)
            text_two = 'Try Later'
            return text_one, text_two
    else:
        text_one = ""
        text_two = 'Avoid Updating Multiple Times'
        return text_one, text_two

