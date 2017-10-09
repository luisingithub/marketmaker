from os.path import join
import logging

########################################################################################################################
# Connection/Auth
########################################################################################################################

# API URL.
BASE_URL = "https://testnet.bitmex.com/api/v1/"
#BASE_URL = "https://www.bitmex.com/api/v1/"
REAL_BASE_URL = "https://www.bitmex.com/api/v1/" # Once you're ready, uncomment this.

# The BitMEX API requires permanent API keys. Go to https://testnet.bitmex.com/api/apiKeys to fill these out.
API_KEY = "zHgnTX-8mdBba8pEbKLQY7qj"
API_SECRET = "6knPb6z1eOL-eFlo_SInS2zCwDWd-5R11p1adnTT_CJdtPHs"

REAL_API_KEY = "bTS72bah7Ij7H96m_O72wjGG"
REAL_API_SECRET = "D6GOFHmG0xR-y8x0ARNig4y3U1kJEWve-ovAirdgU45-O2Lc"



########################################################################################################################
# Target
########################################################################################################################

# Instrument to market make on BitMEX.
SYMBOL = "XBTUSD"


########################################################################################################################
# Order Size & Spread
########################################################################################################################

# How many pairs of buy/sell orders to keep open
ORDER_PAIRS = 6

# ORDER_START_SIZE will be the number of contracts submitted on level 1
# Number of contracts from level 1 to ORDER_PAIRS - 1 will follow the function
# [ORDER_START_SIZE + ORDER_STEP_SIZE (Level -1)]
ORDER_START_SIZE = 100
ORDER_STEP_SIZE = 100

# Distance between successive orders, as a percentage (example: 0.005 for 0.5%)
INTERVAL = 0.005

# Minimum spread to maintain, in percent, between asks & bids
MIN_SPREAD = 0.01

# If True, market-maker will place orders just inside the existing spread and work the interval % outwards,
# rather than starting in the middle and killing potentially profitable spreads.
MAINTAIN_SPREADS = True

# This number defines far much the price of an existing order can be from a desired order before it is amended.
# This is useful for avoiding unnecessary calls and maintaining your ratelimits.
#
# Further information:
# Each order is designed to be (INTERVAL*n)% away from the spread.
# If the spread changes and the order has moved outside its bound defined as
# abs((desired_order['price'] / order['price']) - 1) > settings.RELIST_INTERVAL)
# it will be resubmitted.
#
# 0.01 == 1%
RELIST_INTERVAL = 0.01


########################################################################################################################
# Trading Behavior
########################################################################################################################

# Position limits - set to True to activate. Values are in contracts.
# If you exceed a position limit, the bot will log and stop quoting that side.
CHECK_POSITION_LIMITS = False
MIN_POSITION = -10000
MAX_POSITION = 10000


########################################################################################################################
# Misc Behavior, Technicals
########################################################################################################################

# If true, don't set up any orders, just say what we would do
# DRY_RUN = True
DRY_RUN = False

# How often to re-check and replace orders.
# Generally, it's safe to make this short because we're fetching from websockets. But if too many
# order amend/replaces are done, you may hit a ratelimit. If so, email BitMEX if you feel you need a higher limit.
LOOP_INTERVAL = 300 # 300 seconds, 5mins

# Wait times between orders / errors
API_REST_INTERVAL = 1
API_ERROR_INTERVAL = 10

# If we're doing a dry run, use these numbers for BTC balances
DRY_BTC = 50

# Available levels: logging.(DEBUG|INFO|WARN|ERROR)
LOG_LEVEL = logging.INFO

# To uniquely identify orders placed by this bot, the bot sends a ClOrdID (Client order ID) that is attached
# to each order so its source can be identified. This keeps the market maker from cancelling orders that are
# manually placed, or orders placed by another bot.
#
# If you are running multiple bots on the same symbol, give them unique ORDERID_PREFIXes - otherwise they will
# cancel each others' orders.
# Max length is 13 characters.
ORDERID_PREFIX = "mm_bitmex_"
REAL_ORDERID_PREFIX = "real_bitmex_"

# If any of these files (and this file) changes, reload the bot.
WATCHED_FILES = [join("market_maker", f) for f in ["market_maker.py", "bitmex.py", __file__]]


########################################################################################################################
# BitMEX Portfolio
########################################################################################################################

# Specify the contracts that you hold. These will be used in portfolio calculations.
CONTRACTS = ['XBTUSD']

RESONABLE_PRICE_GAP = 20.0
ZHISHUN_PROZENT = 0.1
RESONABLE_PRICE_STEP = 200.0

# parameter for R_Breaker
PRE_HIGH_PRICE = 4642.0
PRE_LOW_PRICE = 4500.2
POSITION_SIZE = 5

#Strategy name #Turtle, #R_Breaker, #MovingAverage
STRATEGY = "MovingAverage"

#backtesting 
#period unit is "min": 1/5/60/1440
IS_BACKTESTING = False
BACKTEST_PERIOD = 5
START_BTCOIN = 0.5
BACKTESTFILE = "backtestingdata.csv"
R_BREAKER_F1 = 0.35
R_BREAKER_F2 = 0.07
R_BREAKER_F3 = 0.25

#data record and backtest
START_DATE = "2017-08-01"
END_DATE = "2017-09-19"

# Turle 
DonchianN = 5 #number of backtime
ATRN = 5
ADDTIME = 10
ZHIYINGUSD = 1000.0

#MovingAverage
AVERGAGEDAY = 20
UPPERLIMIT = 2400
UNTERLIMIT = -8000
AVERAGENUMPERIORD = 20 # 20 * 5min = 100mins
