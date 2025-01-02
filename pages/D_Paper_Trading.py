import dash
from google.cloud import bigquery

# To create meta tag for each page, define the title, image, and description.
dash.register_page(
    __name__,
    # path='/',
    title='Alert',
    name='All Alert'
)

from dash import Dash, dcc, Input, Output, callback, State
import dash_bootstrap_components as dbc
from dash import html
from dash import dash_table as dt
from google.cloud import storage
import pandas as pd
from dash.exceptions import PreventUpdate
import upstox_client
from upstox_client.rest import ApiException

alert = pd.DataFrame()
url = "https://api.upstox.com/v2/login/authorization/dialog?response_type=code&client_id=1c51a1bc-de3e-4b6f-9ec7-a392943d0de7&redirect_uri=https://bigbullanalysis-ctckij6ibq-uc.a.run.app/"
# ___________________________________________________________________________________________________
# Layout for Alert Page
# ___________________________________________________________________________________________________

content_first_row = dbc.CardGroup(
    [
        dbc.Card(
            dbc.CardBody(
                [
                    dbc.Button(
                        id='delete_alert_button',
                        n_clicks=0,
                        children='Update Alert List',
                    ),
                ]
            )
        ),
    ]
)

content_second_row = dbc.Row(
    [
        dbc.Col(
                html.Br(),
        )
    ]
)

content_third_row = dt.DataTable(id="alert_table",
                                    editable=True,
                                    row_deletable=True,
                                    page_action="native",
                                    style_header={
                                     'backgroundColor': 'grey',
                                     'fontWeight': 'bold'
                                    },
                                    style_cell_conditional=[
                                            {
                                                'if': {'column_id': i},
                                                'textAlign': 'left'
                                            } for i in ['Sector']
                                        ],
                                    style_as_list_view=True,
                                    style_data_conditional=
                                     # Colour red when values are negative
                                    [
                                         {
                                             'if': {'column_id': field_name,
                                                    'filter_query': '{' + field_name + '}' + ' == W'},
                                             'backgroundColor': '#FF4136',
                                             'color': 'white'
                                         } for field_name in alert.columns
                                    ]
                                    +
                                    [
                                         # Colour green when values are positive
                                         {
                                             'if': {'column_id': field_name,
                                                    'filter_query': '{' + field_name + '}' + ' > 0'},
                                             'backgroundColor': '#318500',
                                             'color': 'white'
                                         } for field_name in alert.columns
                                    ]
                                )

second_column_content = html.Div(
    [
        html.Br(),
        content_first_row,
        content_second_row,
        content_third_row,
        html.Div(id='table_status'),
    ],
)

content_navbar = html.Div([
    dbc.Row([
        dbc.Col([
            dbc.Row([
                dbc.Button(
                    "Upstock Login",
                    href=url,
                    external_link=True,
                    color="primary",
                    target='_blank'
                ),
            ]),
            html.Hr(),
            dbc.Row([
                dcc.Input(id="auth_code_from_url", type="text", placeholder="Enter Code", style={'display': 'block'}),
            ]),
            html.Hr(),
            dbc.Row([
                dbc.Button(
                    "Generate Access Token",
                    color="primary",
                    id="generate_access_token"
                ),
            ]),
            html.Hr(),
            dbc.Row([
                html.P(className="card-text", id='access_token_status', children="hi"),
            ])
        ])
    ])
])
main_layout = dbc.Row([
    dbc.Col([
        html.Br(),
        content_navbar
    ], lg=2, xs=12),
    dbc.Col([
        second_column_content
    ], lg=10, xs=12)
])

layout = html.Div([main_layout])

# ___________________________________________________________________________________________________
# Import Alert Data From Big Querry and display on Table
# ___________________________________________________________________________________________________
@callback(
    Output('alert_table', "columns"),
    Output('alert_table', "data"),
    Input('delete_alert_button', 'n_clicks'))
def fetch_scanner_data_from_bq(n_clicks):
    print(n_clicks)
    client = bigquery.Client()
    sql_alert = f""" SELECT * FROM `phrasal-fire-373510.alert_order.alert` """
    cur_alert_df = client.query(sql_alert).to_dataframe()
    cur_alert_df.drop(cur_alert_df.columns[cur_alert_df.columns.str.contains('unnamed', case=False)], axis=1, inplace=True)
    cur_alert_df.columns.astype(str)
    return [{'name': i, 'id': i, 'deletable': True} for i in cur_alert_df.columns if i != 'id'], cur_alert_df.to_dict('records')

# _____________________________________________________________________________________
# Function to upload modified table to Big Query
# _____________________________________________________________________________________
@callback(
        Output("table_status","children"),
        [Input("delete_alert_button","n_clicks")],
        [State("alert_table","data")]
        )

def selected_data_to_csv(nclicks,table1):
    if nclicks == 0:
        raise PreventUpdate
    else:
        # pd.DataFrame(table1).to_csv('D:/solar.csv',index=False)
        print(table1)
        modified_alert_df = pd.DataFrame(table1)
        print(pd.DataFrame(table1))
        if modified_alert_df.empty is False:
            client = bigquery.Client()
            table_id = 'phrasal-fire-373510.alert_order.alert'
            project = "WRITE_TRUNCATE"
            job_config = bigquery.LoadJobConfig(write_disposition=project)
            job = client.load_table_from_dataframe(modified_alert_df, table_id,
                                                   job_config=job_config)  # Make an API request.
            job.result()  # Wait for the job to complete.
            return "Data Submitted"
        else:
            client = bigquery.Client()
            blank_alert__sql = f""" DELETE FROM `phrasal-fire-373510.alert_order.alert` WHERE true;"""
            query_job = client.query(blank_alert__sql)  # API request
            query_job.result()  # Waits for statement to finish
            return "All records deleted"

@callback(Output('access_token_status','children'),
          Input('generate_access_token','n_clicks'),
          Input('auth_code_from_url','value'))
def access_token(nclick, auth_code):
    if nclick == 1:
        api_instance = upstox_client.LoginApi()
        api_version = '2.0'
        code = auth_code
        client_id = '1c51a1bc-de3e-4b6f-9ec7-a392943d0de7'
        client_secret = 'syr0qzt2qw'
        redirect_uri = 'https://bigbullanalysis-ctckij6ibq-uc.a.run.app/'
        grant_type = 'authorization_code'

        try:
            # Get token API
            api_response = api_instance.token(api_version, code=code, client_id=client_id, client_secret=client_secret,
                                              redirect_uri=redirect_uri, grant_type=grant_type)
            # print(api_response)
            new_access_token = api_response.access_token
            try:
                client_storage = storage.Client()
                bucket = client_storage.bucket('biswasp87')
                blob = bucket.blob('access_token.txt')
                blob.upload_from_string(str(new_access_token))
            except Exception:
                pass
            return str("New Access Token Generated")

        except ApiException as e:
            print("Exception when calling LoginApi->token: %s\n" % e)
            return "Error While fetching Access Token"