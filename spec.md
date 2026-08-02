# The Idea

## Strategy: 
- Invest in QQQ3 (nasdaq 3x), IUFS (financials), IUHC (healthcare), IGLD (gold), and ASWC (defence). 
- Every trading day, check last closing price and its position against the 180 ma. 
- If QQQ3 dips below ma, then signal to move that money to SXRV (nasdaq 1x), if the rest of the ETF prices dips below ma, then signal to move their money to cash. If prices goes above ma, do the opposite. 
- There will be monthly income. 

## Balancing:
- The ideal proportion in order is: [0.36, 0.16, 0.16, 0.16, 0.16]. We start by splitting the money in this proportion, and any income will also be split in this proportion and allocated. 
- Add this proportion of income to an existing investment. If an ETF is not being actively invested due to price below ma, we add their proportion of money to a cash deposite, however, we still track the amount this ETF should have received, so that when the price goes over ma, we know how much to put back in. 
- No automatic rebalancing. User rebalance or refrain from rebalancing at their own risk.
- When price moves below ma, change the investment by the total amount, keep track of that amout, and when price goes above ma, we move that amount back in. 
- If investing for the first time or prompted to rebalance, just move out of all investments, calculate the prfoile based on user intended proportions and invest in each one. 

# The Code

## Persistence: 
- Will be deployed on a server that runs (almost) 24/7. Checks and can make changes everyday.

## API
- Use trading212's API to check positions and order history, use yfinance to check close price and ma, and use telegram bot to alert user on changes and receive commands.

## Interaction
- When prices cross ma, notify user. When user is done makihng a change in the account, notify the app to update its database. When user deposite funds or would like to rebalance, also notify the app through telegram.

# Step by step

Keep a data base containing an amount for each of the 5 stock fields. On start up...

- After closing price goes above ma, query data base for how much we reserve for that stock, then notify user to invest that much amount back in and prompt for confirmation, after user confirm putting money in, do nothing. 
- After notifying user closing price goes below ma and prompting confirmation, and user confirm pulling money out, update the amount for that stock field either by a pending order or by the execution price. 
- After user notifies depositing some amount of money, check price above / below ma status and calculate, by proportion, how much to put in each stock field and cash, then notify user and prompt for confirmation.
- After user confirm investing the deposites, update the stock fields. 

# Human loop: 
1. Start the server for first time, send message "/setup {amount}". If amount empty, the script splits the balance, if amount not empty, the scripts splits on the amount. 
2. Every trading day at 12:00 noon, the bot checks pama status. If a stock pama, user gets notified to invest. If a stock pbma, user gets notified to sell with a button, and must click the button after the sell order has filled.
3. When user deposites, send message "/depo {amount}". 
4. As a fall back, user can also send message "/setup {dict}". Where dict is literally a dict in the textual form showing what the script should set the amount of each field to. 