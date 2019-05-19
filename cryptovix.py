# Crypto VIX

import requests
import json
import pandas as pd
import numpy as np
import re
from time import strptime
from datetime import datetime

#-----------#
# CONSTANTS #
#-----------#
DERIBIT_API_URL = 'https://www.deribit.com/api/v1/public/'
DERIBIT_V2_API_URL = 'https://www.deribit.com/api/v2/public/'
LEDGERX_API_URL = ''
MINS_IN_YEAR = 365 * 24 * 60

# This will give bids / asks for the selected instrument
# order_book = json.loads(s.get(DERIBIT_API_URL + 'getorderbook', params={'instrument': instrument_names[0]}).content)

#--------------------------#
# CVIX FUNCTION PARAMETERS #
#--------------------------#

# for R_1 / R_2, instead of stripping an entire curve, could at least try to find 2 near-term points to query via request and then linear interpolate
R_1 = 0.000305 # stolen from VIX whitepaper, do not use for real
R_2 = 0.000286 # stolen from VIX whitepaper, do not use for real
T_1 = None
T_2 = None
F_1 = None
F_2 = None
K0_1 = None
K0_2 = None

#-----------#
# FUNCTIONS #
#-----------#

def get_bid_ask_data(instrumentName):
    '''
    Queries Deribit API for order book data and parses out average bid / ask / number of each
    '''
    bidask = json.loads(s.get(DERIBIT_API_URL + 'getorderbook', params={'instrument': instrumentName}).content)
    bids = bidask['result']['bids']
    n_bids = len(bids)
    amt_bid = sum([i['amount'] for i in bids])
    if amt_bid == 0:
        wtd_avg_bid = np.nan # or 0 ? whatever ends up being useful here
    else:
        wtd_avg_bid = sum([i['amount'] * i['price'] for i in bids]) / amt_bid
    asks = bidask['result']['asks']
    n_asks = len(asks)
    amt_ask = sum([i['amount'] for i in asks])
    if amt_ask == 0:
        wtd_avg_ask = np.nan
    else:
        wtd_avg_ask = sum([i['amount'] * i['price'] for i in asks]) / amt_ask
    return ({'n_bids': n_bids,
             'amt_bid': amt_bid,
             'wtd_avg_bid': wtd_avg_bid,
             'n_asks': n_asks,
             'amt_ask': amt_ask,
             'wtd_avg_ask': wtd_avg_ask})

def get_pricecomp(puts, calls):
    '''
    Builds a price comparison table for (near- or next-term) puts and calls with common strikes.
    '''
    p = pd.DataFrame()
    shared_strikes = list(set(puts['strike']) & set(calls['strike']))
    shared_strikes.sort()
    for i in range(len(shared_strikes)):
        s = shared_strikes[i]
        p.loc[i, 'strike'] = s
        p.loc[i, 'call_price'] = calls[calls['strike'] == s]['call_price'].values[0]
        p.loc[i, 'put_price'] = puts[puts['strike'] == s]['put_price'].values[0]
    p['abs_diff'] = abs(p['call_price'] - p['put_price'])
    return p

def get_vixstrike(puts, calls):
    '''
    Generates the (near- or next-term) table of average put / call prices to determine VIX strike.
    '''
    p = get_pricecomp(puts, calls)
    row = p.where(p['abs_diff'] == min(p['abs_diff'].dropna())).dropna()
    return list(row.T.to_dict().values())[0]

def get_k0(strikes, f):
    '''
    Gets strike immediately below forward index level (f) for a set of strikes.
    '''
    strikes = list(strikes) # just in case it's an ndarray or something
    strikes.sort()
    if len(strikes) == 1:
        if strikes[0] < f:
            return strikes[0]
        else:
            raise ValueError('bad strike data')
    for i in range(len(strikes)-1):
        if strikes[i+1] < f:
            continue
        else:
            return strikes[i]

def select_options(opts, k0, direction):
    '''
    Filters options based on criteria related to moneyness and sequence of nonzero bids per VIX whitepaper.
    '''
    orig_opts = opts.copy()
    assert type(opts) == pd.DataFrame, 'opts must be a dataframe'
    assert direction in ('put', 'call'), 'direction must be one of: \'put\', \'call\''
    opts.sort_values('strike', ascending=(direction == 'call'), inplace=True)
    selected_strikes = [k0] # always include k0 regardless of direction / moneyness
    # filter out in-the-money strikes
    if direction == 'put':
        opts = opts[opts['strike'] < k0]
    else:
        opts = opts[opts['strike'] > k0]
    opts.index = range(len(opts)) # maybe unnecessary (?)
    # iterate through ordered rows, exclude if n_bids == 0, and break if n_bids == 0 twice in a row
    successive_zero_bids = 0
    for i in opts.index:
        if opts.loc[i, 'n_bids'] == 0:
            successive_zero_bids += 1
        else:
            successive_zero_bids = 0
            selected_strikes.append(opts.loc[i, 'strike'])
        if successive_zero_bids == 2:
            break
    # filter to only rows where the strike is in the selected set
    return orig_opts[orig_opts['strike'].isin(selected_strikes)].sort_values('strike')

def get_combined_table(puts, calls, k0):
    '''
    Filters both puts and calls per VIX criteria and combines results into a single table.
    Mid-quote prices are calculated for each strike; shared k0 strike mid-quote prices are averaged across puts / calls.
    '''
    to_return = pd.DataFrame()
    sel_p = select_options(puts, k0, 'put')
    sel_c = select_options(calls, k0, 'call')
    # this is just recalculating the put_price / call_price columns, it seems
    sel_p['mid_quote_price'] = (sel_p['wtd_avg_bid'] + sel_p['wtd_avg_ask']) / 2
    sel_c['mid_quote_price'] = (sel_c['wtd_avg_bid'] + sel_c['wtd_avg_ask']) / 2
    # for non-strike values, reformat values and combine into to_return
    ri = 0 # row indexer for to_return
    si = 0 # selected indexer
    for sel in (sel_p, sel_c):
        for i in range(len(sel)):
            strike = sel.iloc[i,:]['strike']
            if strike == k0:
                continue
            to_return.loc[ri, 'strike'] = strike
            to_return.loc[ri, 'option_type'] = 'put' if si == 0 else 'call'
            to_return.loc[ri, 'mid_quote_price'] = sel.iloc[i,:]['mid_quote_price']
            ri += 1
        si += 1
    # average the mid-quote prices for puts and calls and insert into to_return
    to_return.loc[ri, 'strike'] = k0
    to_return.loc[ri, 'option_type'] = 'put_call_avg'
    to_return.loc[ri, 'mid_quote_price'] = (sel_p[sel_p['strike'] == k0]['mid_quote_price'].values[0] + sel_c[sel_c['strike'] == k0]['mid_quote_price'].values[0]) / 2
    # sort and return
    return to_return.sort_values('strike')


#------------------------#
# MAIN PROGRAM EXECUTION #
#------------------------#
s = requests.Session()

# current BTC price to use for determining moneyness of options
CURRENT_BTC = json.loads(s.get(DERIBIT_V2_API_URL + 'get_index', params={'currency': 'BTC'}).content)['result']['BTC']
# also need to find "risk-free rates" for T1/T2 (once those are known)
# ideally find an API for this, less-ideally strip a curve based on APIs for appropriate instruments; worst case just make something up w/ caveat

# get instruments from Deribit
instruments = json.loads(s.get(DERIBIT_API_URL + 'getinstruments').content)['result']
instrument_names = [i['instrumentName'] for i in instruments]

# parse out useful metadata into a dataframe for quick calculations
btc_pat = r'BTC-\d+[A-Z]+\d+-\d+-[PC]'
# btc_names = [n for n in instrument_names if re.match(btc_pat, n) is not None]
# metadata = pd.DataFrame(columns=['instrumentName', 'created', 'expiration', 'strike', 'optionType', 'isActive', 'settlement', 'daysUntilExpiry', 'n_bids', 'amt_bid', 'wtd_avg_bid', 'n_asks', 'amt_ask', 'wtd_avg_ask'])
metadata = pd.DataFrame()
row = 0
for i in instruments:
    if re.match(btc_pat, i['instrumentName']) is not None and i['settlement'] != 'perpetual' and i['isActive']:
        exp = pd.Timestamp(i['expiration'])
        # this is slow; I don't know a workaround for it at the moment though
        bidask = get_bid_ask_data(i['instrumentName'])
        metadata.loc[row, 'instrumentName'] = i['instrumentName']
        metadata.loc[row, 'created'] = pd.Timestamp(i['created'])
        metadata.loc[row, 'expiration'] = exp
        metadata.loc[row, 'strike'] = i['strike']
        metadata.loc[row, 'optionType'] = i['optionType']
        # metadata.loc[row, 'isActive'] = i['isActive']
        # metadata.loc[row, 'settlement'] = i['settlement'] # NEED TO FILTER ON THIS ONE - should use 'month' as it is most frequent, probably
        metadata.loc[row, 'daysUntilExpiry'] = (exp.date() - datetime.utcnow().date()).days # for filtering "near / next-term"
        # row+=1
        metadata.loc[row, 'n_bids'] = bidask['n_bids']
        metadata.loc[row, 'amt_bid'] = bidask['amt_bid']
        metadata.loc[row, 'wtd_avg_bid'] = bidask['wtd_avg_bid']
        metadata.loc[row, 'n_asks'] = bidask['n_asks']
        metadata.loc[row, 'amt_ask'] = bidask['amt_ask']
        metadata.loc[row, 'wtd_avg_ask'] = bidask['wtd_avg_ask']
        row += 1
# metadata = metadata[metadata['settlement'] == 'month']
metadata.index = range(len(metadata))
# metadata = metadata[metadata['isActive'] == True]
# run some additional metadata calculations
metadata['term'] = ['near' if metadata.loc[i, 'daysUntilExpiry'] <= 7 else 'next' if (metadata.loc[i, 'daysUntilExpiry'] > 7 and metadata.loc[i, 'daysUntilExpiry'] < 32) else np.nan for i in metadata.index]
metadata['moneyness'] = ['at' if metadata.loc[i, 'strike'] == CURRENT_BTC else 'in' if (metadata.loc[i, 'strike'] > CURRENT_BTC and metadata.loc[i, 'optionType'] == 'put') or (metadata.loc[i, 'strike'] < CURRENT_BTC and metadata.loc[i, 'optionType'] == 'call') else 'out' for i in metadata.index]

# drop rows where n_bids or n_asks is 0
# actually don't do this yet - wait until running select_options()
# metadata = metadata[(metadata['n_bids'] > 0) & (metadata['n_asks'] >0)]

'''
need to make a decision here about defining T1 / T2, at least for now
it looks like there ought to be
- 1 week
- upcoming month end
- quarter end (next 3 quarters)
so "near term" would be 7 days or less until expiry
and "next term" would be 7-31 days until expiry (i guess)
'''

# calculate T_1 and T_2 based on expiry dates of selected near / next terms
neardates = list(set(metadata[metadata['term'] == 'near']['expiration']))
nextdates = list(set(metadata[metadata['term'] == 'next']['expiration']))
assert len(neardates) == len(nextdates) == 1, 'unexpected set of expiration dates'
nearexp = neardates[0]
nextexp = nextdates[0]
tnow = pd.Timestamp.now(tz='UTC') # for consistency in next calculations
T_1 = ((nearexp.tz_convert('UTC') - tnow).total_seconds() / 60) / MINS_IN_YEAR
T_2 = ((nextexp.tz_convert('UTC') - tnow).total_seconds() / 60) / MINS_IN_YEAR

# given T_1, T_2 and assuming we got R_1, R_2 from somewhere, calculate F_1, F_2

# replicating tables from pg. 6 of VIX whitepaper
nearputs = metadata[(metadata['optionType'] == 'put') & (metadata['term'] == 'near')].sort_values('strike')
nextputs = metadata[(metadata['optionType'] == 'put') & (metadata['term'] == 'next')].sort_values('strike')
nearcalls = metadata[(metadata['optionType'] == 'call') & (metadata['term'] == 'near')].sort_values('strike')
nextcalls = metadata[(metadata['optionType'] == 'call') & (metadata['term'] == 'next')].sort_values('strike')
nearputs['put_price'] = (nearputs['wtd_avg_bid'] + nearputs['wtd_avg_ask']) / 2
nextputs['put_price'] = (nextputs['wtd_avg_bid'] + nextputs['wtd_avg_ask']) / 2
nearcalls['call_price'] = (nearcalls['wtd_avg_bid'] + nearcalls['wtd_avg_ask']) / 2
nextcalls['call_price'] = (nextcalls['wtd_avg_bid'] + nextcalls['wtd_avg_ask']) / 2

# not entirely clear what the failure mode is here if there is no liquidity to support finding the appropriate strikes...
# though I guess in that case you're just not really going to get much value out of a VIX-esque value, period
near_vixstrike = get_vixstrike(nearputs, nearcalls)
next_vixstrike = get_vixstrike(nextputs, nextcalls)
# build next-term table and find appropriate row (obviously if similar enough, factor this out as a function)
# near_vixstrike can be used to calculate F_1
F_1 = near_vixstrike['strike'] + np.exp(R_1 * T_1) * (near_vixstrike['call_price'] - near_vixstrike['put_price'])
F_2 = next_vixstrike['strike'] + np.exp(R_2 * T_2) * (next_vixstrike['call_price'] - next_vixstrike['put_price'])
# also get K0_1 and K0_2
near_strikes = get_pricecomp(nearputs, nearcalls)['strike'].values
next_strikes = get_pricecomp(nextputs, nextcalls)['strike'].values
K0_1 = get_k0(near_strikes, F_1)
K0_2 = get_k0(near_strikes, F_2)

# select out-of-the-money puts and calls at each term per pg. 6-7 of VIX whitepaper
near_selected = get_combined_table(nearputs, nearcalls, K0_1)
next_selected = get_combined_table(nextputs, nextcalls, K0_2)

# next step is "Step 2" of the VIX whitepaper to calculate volatility




