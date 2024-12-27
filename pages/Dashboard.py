import dash
# To create meta tag for each page, define the title, image, and description.
dash.register_page(
    __name__,
    path='/',
    title='Home',
    name='Home'
)
import dash_bootstrap_components as dbc
from dash import Dash, dcc, html, Input, Output, callback
from google.cloud import storage
from datetime import date, timedelta, datetime
import requests

# PART I: Defining Google Storage Client & Bucket
# ___________________________________________________________________________________________________________
storage_client = storage.Client()
bucket = storage_client.bucket('biswasp87')

# PART II: Preparing the Layout
# ___________________________________________________________________________________________________________

content_first_row = dbc.Carousel(
    items=[
        {"key": "1", "src": "/assets/Image_1.jpg", "img_style": {"height": "650px"}},
        {"key": "2", "src": "/assets/Image_2.jpg", "img_style": {"height": "650px"}},
    ],
    controls=True,
    indicators=True,
)

content_second_row = dbc.Row([
    dbc.Card(
        [
            dbc.CardHeader(""),
            dbc.CardBody(
                [
                    dbc.Row([
                        dbc.Col([
                            html.H5(className="card-title", id='scanner_data_heading'),
                            html.P(className="card-text", id='scanner_data_para'),
                        ]),
                        dbc.Col(
                            # dbc.Button("Update Now", color="primary", id='scanner_data_button'),
                        ),
                    ]),
                ]
            ),
        ], color="info", inverse=True,
    )
])

content = html.Div(
    [
        html.Br(),
        content_first_row,
        html.Br(),
        content_second_row
    ],
)

layout = html.Div([content])