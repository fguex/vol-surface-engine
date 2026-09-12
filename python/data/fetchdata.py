from openbb import obb 
obb.user.preferences.output_type = 'dataframe'

data = obb.equity.price.historical('SPY', provider = 'cboe')
print(data)

chains = obb.derivatives.options.chains('SPY', provider = 'cboe')
chains.info()


# Test des différentes manipulations de données avec pandas 

from openbb import obb 

obb.user.preferences.output_type = 'dataframe'

chains = obb.derivatives.options.chains('SPY', provider = 'cboe')
# créer un multiindex 
print('Test du multiindex')
df_chains = chains.set_index(['expiration', 'strike', 'option_type'])
print(df_chains.head(10))
print(df_chains.index)

# Testons quelques manipulations de données avec pandas 

import numpy as np 
import pandas as pd
from openbb import obb 
obb.user.preferences.output_type = 'dataframe'

# Loadons un dataframe avec les données des options de SPY
asset = obb.equity.price.historical('AAPL', provider = 'yfinance')
benchmark = obb.equity.price.historical('SPY', provider = 'yfinance')

columns = ['open', 'high', 'low', 'close', 'volume', 'dividends']
asset.columns = columns
benchmark.columns = columns

asset['price_diff'] = asset.close.diff()
benchmark['price_diff'] = benchmark.close.diff()

asset['gain'] = asset.price_diff > 0
benchmark['gain'] = benchmark.price_diff > 0

asset['symbol'] = 'AAPL'
benchmark['symbol'] = 'SPY'

asset2 = asset.copy()
asset2.at[asset2.index[10], 'volume'] = asset2.volume[asset2.index[5:10]].mean().astype(int)
asset2.iat[10, 4]  
# Concatenations des deux dataframes 
print('Test de la concaténation des deux dataframes')
print(pd.concat([asset, asset2]).drop_duplicates().head(5))

# Test de pivoting such as excel 

asset['returns'] = asset.close.pct_change()
asset['returns'] = asset['returns'].fillna(0)

print(pd.pivot_table(data = asset, values='returns', columns = 'gain', aggfunc = ['sum', 'mean', 'std']).head(5))

# Exercise try to create the straddle prices : 

chains = obb.derivatives.options.chains('SPY', provider = 'cboe')

# Filter out the call and put options for certain expiration dates 

expirations = chains.expiration.unique()
calls = chains[(chains.option_type == 'call') & (chains.expiration == expirations[10])]
puts = chains[(chains.option_type == 'put') & (chains.expiration == expirations[10])]

calls_strikes = calls.set_index('strike')
puts_strikes = puts.set_index('strike') 
    
joined = calls_strikes.join(puts_strikes, lsuffix='_call', rsuffix='_put')

prices = joined[['last_trade_price_call', 'last_trade_price_put']].copy()
prices['straddle_price'] = prices['last_trade_price_call'] + prices['last_trade_price_put']
print(prices.head(10))