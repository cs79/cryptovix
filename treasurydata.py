# Treasury data fetch and parse / simple curve fit

#---------#
# IMPORTS #
#---------#

import requests
import xml.etree.ElementTree as ET
import xmlschema
import numpy as np

#-----------#
# CONSTANTS #
#-----------#

TREASURY_URL = 'https://data.treasury.gov/feed.svc/DailyTreasuryYieldCurveRateData?$filter=month(NEW_DATE)%20eq%205%20and%20year(NEW_DATE)%20eq%202019'
PATH_TO_SCHEMA = './DailyTreasuryYieldCurveRateData.xsd/DailyTreasuryYieldCurveRateData.xsd'

#-----------#
# FUNCTIONS #
#-----------#

# Function to return polynomial coefficents for estimating R_1 / R_2
def get_treasuries_coefs(deg=3):
    '''
    Fetches treasury data from XML API and fits a polynomial of degree deg.
    '''
    s = requests.Session()
    xmldata = s.get(TREASURY_URL).content
    tree = ET.fromstring(xmldata)

    xs = xmlschema.XMLSchema(PATH_TO_SCHEMA)
    treedict = xs.to_dict(tree)

    # get the data points of interest
    entries = treedict['{http://www.w3.org/2005/Atom}entry'][-1]['{http://www.w3.org/2005/Atom}content']['{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}properties']
    ordered_vals = [
        float(entries['{http://schemas.microsoft.com/ado/2007/08/dataservices}BC_1MONTH']['$']),
        float(entries['{http://schemas.microsoft.com/ado/2007/08/dataservices}BC_2MONTH']['$']),
        float(entries['{http://schemas.microsoft.com/ado/2007/08/dataservices}BC_3MONTH']['$']),
        float(entries['{http://schemas.microsoft.com/ado/2007/08/dataservices}BC_6MONTH']['$']),
        float(entries['{http://schemas.microsoft.com/ado/2007/08/dataservices}BC_1YEAR']['$']),
        float(entries['{http://schemas.microsoft.com/ado/2007/08/dataservices}BC_2YEAR']['$']),
        float(entries['{http://schemas.microsoft.com/ado/2007/08/dataservices}BC_3YEAR']['$']),
        float(entries['{http://schemas.microsoft.com/ado/2007/08/dataservices}BC_5YEAR']['$']),
        float(entries['{http://schemas.microsoft.com/ado/2007/08/dataservices}BC_7YEAR']['$']),
        float(entries['{http://schemas.microsoft.com/ado/2007/08/dataservices}BC_10YEAR']['$']),
        float(entries['{http://schemas.microsoft.com/ado/2007/08/dataservices}BC_20YEAR']['$']),
        float(entries['{http://schemas.microsoft.com/ado/2007/08/dataservices}BC_30YEAR']['$'])
    ]
    ordered_yearfracs = [30/360, 60/360, 90/360, 0.5, 1, 2, 3, 5, 7, 10, 20, 30]

    # not necessarily justifiable but probably ok as a start
    return np.polyfit(ordered_yearfracs, ordered_vals, deg)
