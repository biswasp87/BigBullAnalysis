import dash
from google.cloud import bigquery

# To create meta tag for each page, define the title, image, and description.
dash.register_page(
    __name__,
    # path='/',
    title='Scanner',
    name='All Scanner'
)

from dash import Dash, dcc, Input, Output, callback
import dash_bootstrap_components as dbc
from dash import html
from dash import dash_table as dt
from google.cloud import storage

# scanner = pd.read_csv('gs://biswasp87/Scanner.csv')
# ___________________________________________________________________________________________________
# Import Data From Big Querry
# ___________________________________________________________________________________________________
client = bigquery.Client()
sql_stock = f""" SELECT * FROM `phrasal-fire-373510.Scanner_Data.FNO_Scanner` """
scanner = client.query(sql_stock).to_dataframe()
scanner.drop(scanner.columns[scanner.columns.str.contains('unnamed', case=False)], axis=1, inplace=True)
scanner.columns.astype(str)
scanner['id'] = scanner['SYMBOL'] # For selecting Symbols to pust it to watchlist
scanner.set_index('id', inplace=True, drop=False)

# ___________________________________________________________________________________________________
# Defining Layout of FNO Scanner Page and displaying the Data Table
# ___________________________________________________________________________________________________
content_first_row = dbc.CardGroup(
    [
        dbc.Card(
            dbc.CardBody(
                [
                    dbc.Button(
                        id='scanner_button',
                        n_clicks=0,
                        children='Send to Watchlist',
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

content_third_row = dt.DataTable(id="scanner_table",
                                    columns=[{'name': i, 'id': i, 'deletable': True} for i in scanner.columns if i != 'id'],
                                    data=scanner.to_dict('records'),
                                    editable=True,
                                    filter_action="native",
                                    sort_action="native",
                                    sort_mode="multi",
                                    row_selectable="multi",
                                    row_deletable=True,
                                    selected_columns=[],
                                    selected_rows=[],
                                    page_action="native",
                                    # page_current=0,
                                    # page_size=10,
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
                                                    'filter_query': '{' + field_name + '}' + ' < 0'},
                                             'backgroundColor': '#FF4136',
                                             'color': 'white'
                                         } for field_name in scanner.columns
                                    ]
                                    +
                                    [
                                         # Colour green when values are positive
                                         {
                                             'if': {'column_id': field_name,
                                                    'filter_query': '{' + field_name + '}' + ' > 0'},
                                             'backgroundColor': '#318500',
                                             'color': 'white'
                                         } for field_name in scanner.columns
                                    ]
                                )

content = html.Div(
    [
        html.Br(),
        content_first_row,
        content_second_row,
        content_third_row,
    ],
)

layout = html.Div([dcc.Store(id="filtered_scanner_df", data=[], storage_type='local'), content])
# ___________________________________________________________________________________________________
# Button Function to Push the Selected Symbol to Scanner Watchlist
# ___________________________________________________________________________________________________
@callback(
    # Output('scanner_table_ag_grid', "rowData"),
    Output('filtered_scanner_df', "data"),
    Input('scanner_table', "derived_virtual_row_ids"),
    Input('scanner_table', "selected_row_ids"),
    Input('scanner_button', 'n_clicks'))

def update_graphs(row_ids, selected_row_ids,n_clicks):
    print(row_ids)
    print(selected_row_ids)

    if selected_row_ids is None:
        dff = scanner
    else:
        dff = scanner.loc[selected_row_ids]

    dff.rename(columns={'SYMBOL': 'Symbol'}, inplace=True)
    # print(dff)

    ctx = dash.callback_context
    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

    if trigger_id == 'scanner_button':
        try:
            client_storage = storage.Client()
            bucket = client_storage.bucket('bba_support_files')
            blob = bucket.blob('SCANNER.csv')
            blob.upload_from_string(dff.to_csv(), 'text/csv')
        except Exception:
            pass
    dff.rename(columns={'Symbol': 'SYMBOL'}, inplace=True)
    return dff.to_dict('records')

# ___________________________________________________________________________________________________
# Callback to Fetch Last Updated Time
# ___________________________________________________________________________________________________
# @callback(Output("Table_Last_Updated", "children"))
# def update_statusBar():
#     table_id = "phrasal-fire-373510.Scanner_Data.FNO_Scanner"
#     table = client.get_table(table_id)  # Make an API request.
#     print("Last Updated on: {}".format(table.modified))
