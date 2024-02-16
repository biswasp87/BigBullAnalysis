import dash
# To create meta tag for each page, define the title, image, and description.
dash.register_page(
    __name__,
    path='/',
    title='Settings',
    name='Settings'
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

first_card_content = [
    dbc.CardHeader("Master Data"),
    dbc.CardBody(
        [
            dbc.Row([
                dbc.Col([
                    html.H5(className="card-title", id='master_data_heading'),
                    html.P(className="card-text", id='master_data_para'),
                ]),
                dbc.Col(
                    dbc.Button("Update Now", color="primary", id='master_data_button'),
                ),
            ])
        ]
    ),
]
second_card_content = [
    dbc.CardHeader("Watchlist"),
    dbc.CardBody(
        [
            dbc.Row([
                dbc.Col([
                    html.H5(className="card-title", id='watchlist_data_heading'),
                    html.P(className="card-text", id='watchlist_data_para'),
                ]),
                dbc.Col(
                    dbc.Button("Update Now", color="primary", id='watchlist_data_button'),
                )
            ])
        ]
    ),
]
third_card_content = [
    dbc.CardHeader("Scanner"),
    dbc.CardBody(
        [
            dbc.Row([
                dbc.Col([
                    html.H5(className="card-title", id='scanner_data_heading'),
                    html.P(className="card-text", id='scanner_data_para'),
                ]),
                dbc.Col(
                    dbc.Button("Update Now", color="primary", id='scanner_data_button'),
                ),
            ]),
        ]
    ),
]
content_first_row = dbc.Carousel(
    items=[
        {"key": "1", "src": "/assets/Image_1.jpg", "img_style": {"height": "650px"}},
        {"key": "2", "src": "/assets/Image_2.jpg", "img_style": {"height": "650px"}},
    ],
    controls=True,
    indicators=True,
)

content_second_row = dbc.Row(
    [
        dbc.Col(dbc.Card(first_card_content, color="info", inverse=True)),
        dbc.Col(dbc.Card(second_card_content, color="info", inverse=True)),
        dbc.Col(dbc.Card(third_card_content, color="info", inverse=True)),
    ],
    className="mb-4"
)

content = html.Div(
    [
        html.Br(),
        content_first_row,
        html.Br(),
        content_second_row,
    ],
)

layout = html.Div([content])

# PART III: Defining the Callbacks to update the Card Details
# ___________________________________________________________________________________________________________
@callback(
    Output(component_id='master_data_heading', component_property='children'),
    Output(component_id='master_data_para', component_property='children'),
    Input(component_id='master_data_button', component_property='n_clicks'),
)
def update_master_data_card(n):
    blob_watchlist = bucket.get_blob('last_Master_Data_updated_on.csv')
    if n is None:
        text_one = "Last Updated on " + str(datetime.date(blob_watchlist.updated))
        text_two = "Click on the Button for Manual Update"
        return text_one, text_two
    elif n == 1:
        r = requests.post("https://asia-south1-phrasal-fire-373510.cloudfunctions.net/Fetch_Master_Data_To_BQ")
        r_status = r.status_code
        if r_status == 200:
            text_one = 'Updated Successfully at ' + str(datetime.date(blob_watchlist.updated))
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
@callback(
    Output(component_id='watchlist_data_heading', component_property='children'),
    Output(component_id='watchlist_data_para', component_property='children'),
    Input(component_id='watchlist_data_button', component_property='n_clicks'),
)
def update_watchlist_data_card(n):
    blob_watchlist = bucket.get_blob('last_watchlist_updated_on.csv')
    if n is None:
        text_one = "Last Updated on " + str(datetime.date(blob_watchlist.updated))
        text_two = "Click on the Button for Manual Update"
        return text_one, text_two
    elif n == 1:
        r = requests.post("https://asia-south1-phrasal-fire-373510.cloudfunctions.net/BBA_Update_Watchlist")
        r_status = r.status_code
        if r_status == 200:
            text_one = 'Updated Successfully at ' + str(datetime.date(blob_watchlist.updated))
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

@callback(
    Output(component_id='scanner_data_heading', component_property='children'),
    Output(component_id='scanner_data_para', component_property='children'),
    Input(component_id='scanner_data_button', component_property='n_clicks'),
)
def update_scanner_data_card(n):
    blob_scanner = bucket.get_blob('last_scanner_updated_on.csv')
    if n is None:
        text_one = "Last Updated on " + str(datetime.date(blob_scanner.updated))
        text_two = "Click on the Button for Manual Update"
        return text_one, text_two
    elif n == 1:
        r = requests.post("https://asia-south1-phrasal-fire-373510.cloudfunctions.net/FNO_Scanner")
        r_status = r.status_code
        if r_status == 200:
            text_one = 'Updated Successfully at ' + str(datetime.date(blob_scanner.updated))
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