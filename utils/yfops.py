import yfinance as yf


def price_above_ma(ticker, window=180):
    """
    Fetch a stock's X-day moving average and its last closing price.

    Args:
        ticker: stock symbol
        window: number of days for moving average

    Returns:
        last_close (float): last closing price of the stock
        price_above_ma (bool): whether last stock close is above ma
        
    """

    # Pull enough history to cover the MA window (buffer for weekends/holidays).
    data = yf.download(
        ticker, 
        period=f"{window * 2 + 10}d",
        interval="1d", 
        auto_adjust=True,
        progress=False
    )

    if data.empty:
        raise ValueError(f"No data returned for '{ticker}'. Check the symbol.")

    close = data["Close"].squeeze()  # ensure 1-D Series

    if len(close) < window:
        raise ValueError(f"Only {len(close)} days available; need {window}.")

    last_close = float(close.iloc[-1])
    moving_average = float(close.tail(window).mean())
    price_above_ma = last_close >= moving_average

    return last_close, price_above_ma


# MAIN
if __name__ == "__main__":
    close, pama = price_above_ma("AAPL", 180)
    print(close, pama)