"""
Fetch option chains, dividends, spot price and treasury rates for AAPL.
Saves everything as CSV in data/.
Usage: python3 scripts/fetch_market_data.py [TICKER]
"""

import sys
import os
import datetime
import yfinance as yf
import pandas as pd

TICKER = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
MAX_DAYS_TO_EXPIRY = 365
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)

today = datetime.date.today()
print(f"Fetching data for {TICKER} on {today}")

# --- Spot price ---
tk = yf.Ticker(TICKER)
spot = tk.info.get("currentPrice") or tk.info.get("regularMarketPrice")
print(f"Spot: {spot}")

with open(os.path.join(DATA_DIR, f"{TICKER.lower()}_spot.csv"), "w") as f:
    f.write("date,spot\n")
    f.write(f"{today},{spot}\n")

# --- Dividends ---
divs = tk.dividends
if not divs.empty:
    divs.index = divs.index.tz_localize(None)
    divs = divs[divs.index >= pd.Timestamp(today - datetime.timedelta(days=730))]
    divs.to_csv(os.path.join(DATA_DIR, f"{TICKER.lower()}_dividends.csv"), header=["amount"])
    print(f"Dividends: {len(divs)} entries saved")
else:
    print("No dividends found")

# --- Option chains ---
expirations = tk.options
print(f"Available expirations: {len(expirations)}")

all_calls = []
all_puts = []

for exp_str in expirations:
    exp_date = datetime.date.fromisoformat(exp_str)
    days_to_exp = (exp_date - today).days
    if days_to_exp < 7 or days_to_exp > MAX_DAYS_TO_EXPIRY:
        continue

    try:
        chain = tk.option_chain(exp_str)
    except Exception as e:
        print(f"  Skipping {exp_str}: {e}")
        continue

    for df, side in [(chain.calls, "call"), (chain.puts, "put")]:
        df = df.copy()
        df["expiration"] = exp_str
        df["daysToExpiry"] = days_to_exp
        df["T"] = round(days_to_exp / 365.0, 6)
        df["type"] = side
        df["exerciseType"] = "american"

        # Filter: keep only options with volume > 0 and reasonable bid
        df = df[df["volume"].fillna(0) > 0]
        df = df[df["bid"] > 0.05]

        cols = [
            "expiration", "T", "daysToExpiry", "type", "exerciseType",
            "strike", "bid", "ask", "lastPrice",
            "volume", "openInterest", "impliedVolatility"
        ]
        cols = [c for c in cols if c in df.columns]
        df = df[cols]

        if side == "call":
            all_calls.append(df)
        else:
            all_puts.append(df)

    print(f"  {exp_str} ({days_to_exp}d): {len(chain.calls)} calls, {len(chain.puts)} puts")

if all_calls:
    calls_df = pd.concat(all_calls, ignore_index=True)
    calls_df.to_csv(os.path.join(DATA_DIR, f"{TICKER.lower()}_calls.csv"), index=False)
    print(f"\nTotal calls saved: {len(calls_df)}")

if all_puts:
    puts_df = pd.concat(all_puts, ignore_index=True)
    puts_df.to_csv(os.path.join(DATA_DIR, f"{TICKER.lower()}_puts.csv"), index=False)
    print(f"Total puts saved: {len(puts_df)}")

# --- Treasury rates (risk-free curve) ---
try:
    import pandas_datareader as pdr

    tenors = {
        "DGS1MO": 1/12, "DGS3MO": 0.25, "DGS6MO": 0.5,
        "DGS1": 1.0, "DGS2": 2.0, "DGS5": 5.0,
        "DGS10": 10.0, "DGS30": 30.0
    }
    rates = pdr.get_data_fred(list(tenors.keys()),
                              start=today - datetime.timedelta(days=7))
    last_rates = rates.dropna().iloc[-1]

    with open(os.path.join(DATA_DIR, "treasury_rates.csv"), "w") as f:
        f.write("tenor,rate\n")
        for code, tenor in tenors.items():
            if code in last_rates:
                f.write(f"{tenor},{last_rates[code] / 100.0:.6f}\n")

    print(f"Treasury rates saved")
except ImportError:
    print("pandas_datareader not installed, skipping treasury rates")
    print("  pip install pandas-datareader")
except Exception as e:
    print(f"Could not fetch treasury rates: {e}")

print("\nDone! Files saved in data/")
