from __future__ import absolute_import
from time import sleep
import sys
from datetime import datetime
from os.path import getmtime
import random
import requests
import atexit
import signal
import decimal
import collections

import numpy as np

from market_maker import bitmex
from market_maker.settings import settings
from market_maker.utils import log, constants, errors
from market_maker import getTradeHis 



# Used for reloading the bot - saves modified times of key files
import os
watched_files_mtimes = [(f, getmtime(f)) for f in settings.WATCHED_FILES]


#
# Helpers
#
logger = log.setup_custom_logger('root')


class ExchangeInterface:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        if len(sys.argv) > 1:
            self.symbol = sys.argv[1]
        else:
            self.symbol = settings.SYMBOL
        self.bitmex = bitmex.BitMEX(base_url=settings.BASE_URL, symbol=self.symbol, login=settings.LOGIN,
                                    password=settings.PASSWORD, otpToken=settings.OTPTOKEN, apiKey=settings.API_KEY,
                                    apiSecret=settings.API_SECRET, orderIDPrefix=settings.ORDERID_PREFIX)

    def cancel_order(self, order):
        logger.info("Cancelling: %s %d @ %.2f" % (order['side'], order['orderQty'], "@", order['price']))
        while True:
            try:
                self.bitmex.cancel(order['orderID'])
                sleep(settings.API_REST_INTERVAL)
            except ValueError as e:
                logger.info(e)
                sleep(settings.API_ERROR_INTERVAL)
            else:
                break

    def cancel_all_orders(self):
        if self.dry_run:
            return

        logger.info("Resetting current position. Cancelling all existing orders.")

        # In certain cases, a WS update might not make it through before we call this.
        # For that reason, we grab via HTTP to ensure we grab them all.
        orders = self.bitmex.http_open_orders()

        for order in orders:
            logger.info("Cancelling: %s %d @ %.2f" % (order['side'], order['orderQty'], order['price']))

        if len(orders):
            self.bitmex.cancel([order['orderID'] for order in orders])

        sleep(settings.API_REST_INTERVAL)

    def get_portfolio(self):
        contracts = settings.CONTRACTS
        portfolio = {}
        for symbol in contracts:
            position = self.bitmex.position(symbol=symbol)
            instrument = self.bitmex.instrument(symbol=symbol)

            if instrument['isQuanto']:
                future_type = "Quanto"
            elif instrument['isInverse']:
                future_type = "Inverse"
            elif not instrument['isQuanto'] and not instrument['isInverse']:
                future_type = "Linear"
            else:
                raise NotImplementedError("Unknown future type; not quanto or inverse: %s" % instrument['symbol'])

            if instrument['underlyingToSettleMultiplier'] is None:
                multiplier = float(instrument['multiplier']) / float(instrument['quoteToSettleMultiplier'])
            else:
                multiplier = float(instrument['multiplier']) / float(instrument['underlyingToSettleMultiplier'])

            portfolio[symbol] = {
                "currentQty": float(position['currentQty']),
                "futureType": future_type,
                "multiplier": multiplier,
                "markPrice": float(instrument['markPrice']),
                "spot": float(instrument['indicativeSettlePrice'])
            }

        return portfolio

    def calc_delta(self):
        """Calculate currency delta for portfolio"""
        portfolio = self.get_portfolio()
        spot_delta = 0
        mark_delta = 0
        for symbol in portfolio:
            item = portfolio[symbol]
            if item['futureType'] == "Quanto":
                spot_delta += item['currentQty'] * item['multiplier'] * item['spot']
                mark_delta += item['currentQty'] * item['multiplier'] * item['markPrice']
            elif item['futureType'] == "Inverse":
                spot_delta += (item['multiplier'] / item['spot']) * item['currentQty']
                mark_delta += (item['multiplier'] / item['markPrice']) * item['currentQty']
            elif item['futureType'] == "Linear":
                spot_delta += item['multiplier'] * item['currentQty']
                mark_delta += item['multiplier'] * item['currentQty']
        basis_delta = mark_delta - spot_delta
        delta = {
            "spot": spot_delta,
            "mark_price": mark_delta,
            "basis": basis_delta
        }
        return delta

    def get_delta(self, symbol=None):
        if symbol is None:
            symbol = self.symbol
        return self.get_position(symbol)['currentQty']

    def get_instrument(self, symbol=None):
        if symbol is None:
            symbol = self.symbol
        return self.bitmex.instrument(symbol)

    def get_margin(self):
        if self.dry_run:
            return {'marginBalance': float(settings.DRY_BTC), 'availableFunds': float(settings.DRY_BTC)}
        return self.bitmex.funds()

    def get_orders(self):
        if self.dry_run:
            return []
        return self.bitmex.open_orders()

    def get_highest_buy(self):
        buys = [o for o in self.get_orders() if o['side'] == 'Buy']
        if not len(buys):
            return {'price': -2**32}
        highest_buy = max(buys or [], key=lambda o: o['price'])
        return highest_buy if highest_buy else {'price': -2**32}

    def get_lowest_sell(self):
        sells = [o for o in self.get_orders() if o['side'] == 'Sell']
        if not len(sells):
            return {'price': 2**32}
        lowest_sell = min(sells or [], key=lambda o: o['price'])
        return lowest_sell if lowest_sell else {'price': 2**32}  # ought to be enough for anyone

    def get_position(self, symbol=None):
        if symbol is None:
            symbol = self.symbol
        return self.bitmex.position(symbol)

    def get_ticker(self, symbol=None):
        if symbol is None:
            symbol = self.symbol
        return self.bitmex.ticker_data(symbol)

    def is_open(self):
        """Check that websockets are still open."""
        return not self.bitmex.ws.exited

    def check_market_open(self):
        instrument = self.get_instrument()
        if instrument["state"] != "Open" and instrument["state"] != "Closed":
            raise errors.MarketClosedError("The instrument %s is not open. State: %s" %
                                           (self.symbol, instrument["state"]))
            sys.exit()

    def check_if_orderbook_empty(self):
        """This function checks whether the order book is empty"""
        instrument = self.get_instrument()
        if instrument['midPrice'] is None:
            raise errors.MarketEmptyError("Orderbook is empty, cannot quote")
            sys.exit()

    def amend_bulk_orders(self, orders):
        if self.dry_run:
            return orders
        return self.bitmex.amend_bulk_orders(orders)

    def create_bulk_orders(self, orders):
        if self.dry_run:
            return orders
        return self.bitmex.create_bulk_orders(orders)

    def cancel_bulk_orders(self, orders):
        if self.dry_run:
            return orders
        return self.bitmex.cancel([order['orderID'] for order in orders])


class OrderManager:
    def __init__(self):
        if not settings.IS_BACKTESTING:
            self.exchange = ExchangeInterface(settings.DRY_RUN)
            atexit.register(self.exit)
            signal.signal(signal.SIGTERM, self.exit)
            logger.info("Using symbol %s. " % (self.exchange.symbol))
        # Once exchange is created, register exit handler that will always cancel orders
        # on any error.


    def init(self):
        if settings.DRY_RUN:
            logger.info("Initializing dry run. Orders printed below represent what would be posted to BitMEX.")
        elif settings.IS_BACKTESTING:
            logger.info("Initializing Back testing...")
        else:
            logger.info("Order Manager initializing, connecting to BitMEX. Live run: executing real trades.")
            
        self.start_time = datetime.now()
        self.todayDate = datetime.today().day
        if not settings.IS_BACKTESTING:
            self.instrument = self.exchange.get_instrument()
            self.starting_qty = self.exchange.get_delta()
            self.running_qty = self.starting_qty
        
        # choose the strategy
        if settings.STRATEGY ==  "R_Breaker":
            self.handle_trade = self.handle_trade_R_Breaker   
            self.init_R_Break()  
        elif settings.STRATEGY ==  "Turtle":
            self.init_Turtle()
        elif settings.STRATEGY == "MovingAverage":
            self.init_MovingAverage()
        else:  
            self.handle_trade = self.place_orders
            
        # profit calculation
        self.totalprofit = 0.0
        self.startPrice_profit = 0.0
        self.endPrice_profit = 0.0
        self.dynamic_position = 0
        self.initBitcoinPrice = 0
        self.finalUSDBenifit = 0.0
        self.baseBenifit = 0.0
        self.bankrupt = False
        self.unrealisedBitcoinBenifit = 0.0
        self.unrealisedbenifit = 0.0
        self.totalUSDbenifit = 0.0
        
        # prices variables
        self.prevClosePrice = 0.0
        self.currentPrice = 0.0
        self.preCurrentPrice = 0.0
        self.lastAskPrice = 0.0
        self.lastBidPrice = 0.0
        self.lastAskSize = 0.0
        self.lastBidSize = 0.0
        
        # indicates if this is the first day of trading
        self.firstDay = True
        self.firstTime = True
        self.simulateDayNumbers = 0
        self.prevDayBacktest = settings.START_DATE
        self.positionSize = settings.POSITION_SIZE
        # 最大回撤， 成功交易次数， 失败交易次数
        self.maxReturnedLoss = 0.0
        self.numberPostiveTrade = 0
        self.numberNegativTrade = 0
        
        #最大仓位
        self.UPPERLIMITPOS = 0.0
        self.UNTERLIMITPOS = 0.0
        
        if not settings.IS_BACKTESTING:   
            self.reset()
            

    def init_R_Break(self):
        logger.info("using strategy: R_Breaker")
        self.f1 = settings.R_BREAKER_F1
        self.f2 = settings.R_BREAKER_F2
        self.f3 = settings.R_BREAKER_F3
        
    def init_Turtle(self):
        self.maxPreNhighPrice = 0.0
        self.minPreNlowPrice = 10000
        self.highPriceQueue = collections.deque(settings.DonchianN * [0], settings.DonchianN)
        self.lowPriceQueue = collections.deque(settings.DonchianN * [0], settings.DonchianN)
        self.ATR = 0
        self.UnitPosition = 0
        self.TurtlePos = 0 #could be -5 ~ +5
        self.StopPrice = []
        self.AddPrice = [0] * settings.ADDTIME
        self.unitPositions = []
        
    def init_MovingAverage(self):
        self.maxPreNhighPrice = 0.0
        self.minPreNlowPrice = 10000
        
        self.highPriceQueue = collections.deque(settings.DonchianN * [0], settings.DonchianN)
        self.lowPriceQueue = collections.deque(settings.DonchianN * [0], settings.DonchianN)
        self.ATR = 0
        self.UnitPosition = 0
        self.TurtlePos = 0 #could be -5 ~ +5
        self.AddPrice = [0] * settings.ADDTIME
        self.movingAvergePrices = collections.deque(settings.AVERGAGEDAY * [0], settings.AVERGAGEDAY)
        self.movingAveragePrice = 0.0
        
        self.traderest = 0.0
    
    def updatePositionLimit(self):
        nowbitcoin = settings.START_BTCOIN + self.totalprofit + self.unrealisedBitcoinBenifit
        if nowbitcoin > 0:
            self.UPPERLIMITPOS = nowbitcoin * self.prevClosePrice * 1
            self.UNTERLIMITPOS = nowbitcoin * self.prevClosePrice * (-2)
            print("UPPERLIMITPOS = %d, UNTERLIMITPOS = %d" % (self.UPPERLIMITPOS, self.UNTERLIMITPOS))
    
    def benifitCaculate(self):
        pricealpha = (self.endPrice_profit - self.startPrice_profit) / self.startPrice_profit
        eachtimebenifit = pricealpha / (1+pricealpha) * self.dynamic_position / self.startPrice_profit
        self.totalprofit += eachtimebenifit
        initBitcoin = settings.START_BTCOIN
        nowBitcoin = initBitcoin + self.totalprofit
        USDBenifit = nowBitcoin * self.endPrice_profit - initBitcoin * self.initBitcoinPrice
        BenifitinUSD = USDBenifit / (initBitcoin * self.initBitcoinPrice) * 100
        #self.baseBenifit = (self.endPrice_profit - self.initBitcoinPrice) / self.initBitcoinPrice * 100
        self.finalUSDBenifit = BenifitinUSD
        print("%d XBTUSD settled, benifit: %.6f BT, total benifit: %.6f BT, Basebenifit: %.2f%% USDBenifit: %.2f%% %.2f USD" % (self.dynamic_position, eachtimebenifit, self.totalprofit, self.baseBenifit, BenifitinUSD, USDBenifit))
        print(self.prevDayBacktest)
        
    def benifitCaculatePos(self, pos, price):
        #print("benifitCaculatePos is called, startPrice_profit = %.2f" % self.startPrice_profit)
        if self.startPrice_profit == 0:
            return 0
        
        self.endPrice_profit = price
        pricealpha = (self.endPrice_profit - self.startPrice_profit) / self.startPrice_profit
        eachtimebenifit = pricealpha / (1+pricealpha) * pos / self.startPrice_profit
        
        if eachtimebenifit > 0:
            self.numberPostiveTrade += 1
        if eachtimebenifit < 0:
            self.numberNegativTrade += 1
        #self.maxReturnedLoss = 0.0
        
        self.totalprofit += eachtimebenifit
        initBitcoin = settings.START_BTCOIN
        nowBitcoin = initBitcoin + self.totalprofit
        if nowBitcoin < 0:
            self.bankrupt = True
        USDBenifit = nowBitcoin * self.endPrice_profit - initBitcoin * self.initBitcoinPrice
        BenifitinUSD = USDBenifit / (initBitcoin * self.initBitcoinPrice) * 100
        #self.baseBenifit = (self.endPrice_profit - self.initBitcoinPrice) / self.initBitcoinPrice * 100
        self.finalUSDBenifit = BenifitinUSD
        #print("%d XBTUSD settled, benifit: %.6f BT, total benifit: %.6f BT, Basebenifit: %.2f%% USDBenifit: %.2f%% %.2f USD" % (pos, eachtimebenifit, self.totalprofit, self.baseBenifit, BenifitinUSD, USDBenifit))
        #print(self.prevDayBacktest)
        print("平仓 %d, 本次利润%.4fBTC" %(pos,eachtimebenifit))
        
    def updateStartPriceProfit(self, newPrice, AddedPos):
        #print("newPrice = %.2f, Addedpos = %.2d, startPrice = %.2f, totalposition = %.2d, profit = " % (newPrice,AddedPos,self.startPrice_profit, self.dynamic_position))
        if self.dynamic_position != 0:
            self.startPrice_profit = (self.startPrice_profit * (self.dynamic_position - AddedPos) + newPrice * AddedPos) / (self.dynamic_position)
        #print("in update startPrice_profit = %.2f" % self.startPrice_profit)
        
        
    def unrealisedBenifit(self):
        if abs(self.dynamic_position) > 0: 
            pricealpha = (self.currentPrice - self.startPrice_profit) / self.startPrice_profit
            self.unrealisedBitcoinBenifit = pricealpha / (1+pricealpha) * self.dynamic_position / self.startPrice_profit
            nowBitcoin = self.unrealisedBitcoinBenifit + settings.START_BTCOIN + self.totalprofit
            self.totalUSDbenifit = (nowBitcoin * self.currentPrice) / (settings.START_BTCOIN * self.initBitcoinPrice) * 100 - 100.0
            self.unrealisedbenifit = self.totalUSDbenifit - self.finalUSDBenifit

            if nowBitcoin < 0:
                self.bankrupt = True
        elif self.dynamic_position == 0:
            nowBitcoin = settings.START_BTCOIN + self.totalprofit
            self.totalUSDbenifit = (nowBitcoin * self.currentPrice) / (settings.START_BTCOIN * self.initBitcoinPrice) * 100 - 100.0
            self.unrealisedbenifit = 0.0
            #print("unrealisedbenifit = %.2f, finalUSDBenifit = %.2f" %(self.unrealisedbenifit,self.finalUSDBenifit))
        
    def lastDaysettlement(self, tradeline = " "): # on last day of backtest to make position to 0
        if self.dynamic_position > 0:
            self.endPrice_profit = getTradeHis.getaskPriceFromLine(tradeline)
            self.benifitCaculatePos(self.dynamic_position,self.endPrice_profit)
            self.dynamic_position = 0
        if self.dynamic_position < 0:
            self.endPrice_profit = getTradeHis.getbidPriceFromLine(tradeline)
            self.benifitCaculatePos(self.dynamic_position,self.endPrice_profit)
            self.dynamic_position = 0
            
    
    def getPreNMaxMinPrice(self):
        N = settings.DonchianN
        maxP = 0
        minP = 10000
        for i in range(0,N):
            if self.highPriceQueue[i]>maxP:
                maxP = self.highPriceQueue[i]
            if self.lowPriceQueue[i]<minP:
                minP = self.lowPriceQueue[i]
        #print("price in queue: %.2f, %.2f, %.2f, %.2f, %.2f" % (self.lowPriceQueue[0], self.lowPriceQueue[1], self.lowPriceQueue[2], self.lowPriceQueue[3], self.lowPriceQueue[4]))
        self.maxPreNhighPrice = maxP
        self.minPreNlowPrice = minP
        
    def is_newDay(self, tradeline = " "): 
        if settings.IS_BACKTESTING:
            nowDay = getTradeHis.getDateFromLine(tradeline)
            if nowDay != self.prevDayBacktest:
                #print("new day: " + nowDay)
                self.firstDay = False
                self.prevDayBacktest = nowDay

                return True
            else:
                return False
        
        nowDay = datetime.today().day
        if nowDay != self.todayDate:
            self.todayDate = nowDay
            self.firstDay = False
            logger.info("Debug by Lu: new day is coming: %d" % nowDay)
            return True
        else:
            return False
        
    def reset(self):
        self.exchange.cancel_all_orders()
        self.sanity_check()
        self.print_status()
        # Create orders and converge.
        #self.place_orders()
        #self.handle_trade()
        if settings.DRY_RUN:
            sys.exit()

    def print_status(self):
        """Print the current MM status."""
        
        margin = self.exchange.get_margin()
        position = self.exchange.get_position()
        self.running_qty = self.exchange.get_delta()
        self.start_XBt = margin["marginBalance"]
        
        logger.debug('Debug by Lu: position_currentQty: %.2f, running_qty: %d, marginBalance = start_XBt: %.6f' %(position['currentQty'], self.running_qty,self.start_XBt))

        logger.info("Current XBT Balance: %.6f" % XBt_to_XBT(self.start_XBt))
        logger.info("Current Contract Position: %d" % self.running_qty)
        if settings.CHECK_POSITION_LIMITS:
            logger.info("Position limits: %d/%d" % (settings.MIN_POSITION, settings.MAX_POSITION))
        if position['currentQty'] != 0:
            logger.info("Avg Cost Price: %.2f" % float(position['avgCostPrice']))
            logger.info("Avg Entry Price: %.2f" % float(position['avgEntryPrice']))
        logger.info("Contracts Traded This Run: %d" % (self.running_qty - self.starting_qty))
        logger.info("Total Contract Delta: %.4f XBT" % self.exchange.calc_delta()['spot'])

    def get_ticker(self):
        ticker = self.exchange.get_ticker()

        # Set up our buy & sell positions as the smallest possible unit above and below the current spread
        # and we'll work out from there. That way we always have the best price but we don't kill wide
        # and potentially profitable spreads.
        self.start_position_buy = ticker["buy"] + self.instrument['tickSize']
        self.start_position_sell = ticker["sell"] - self.instrument['tickSize']
        
        logger.debug('Debug by Lu: ticker: Buy: %.2f, Sell: %.2f, self.instrument[tickSize]: %.2f' %
                    (ticker["buy"], ticker["sell"], self.instrument['tickSize']))
        logger.debug('Debug by Lu: highest buy: %.2f, lowest sell: %.2f' %
                    (self.exchange.get_highest_buy()['price'], self.exchange.get_lowest_sell()['price']))
        # If we're maintaining spreads and we already have orders in place,
        # make sure they're not ours. If they are, we need to adjust, otherwise we'll
        # just work the orders inward until they collide.
        if settings.MAINTAIN_SPREADS:
            if ticker['buy'] == self.exchange.get_highest_buy()['price']:
                self.start_position_buy = ticker["buy"]
            if ticker['sell'] == self.exchange.get_lowest_sell()['price']:
                self.start_position_sell = ticker["sell"]
            #logger.debug('Debug by Lu: maintain spreads ticker: Buy: %.2f, Sell: %.2f, self.instrument[tickSize]: %.2f' %
                    #(ticker["buy"], ticker["sell"], self.instrument['tickSize']))
        # Back off if our spread is too small.
        if self.start_position_buy * (1.00 + settings.MIN_SPREAD) > self.start_position_sell:
            self.start_position_buy *= (1.00 - (settings.MIN_SPREAD / 2))
            self.start_position_sell *= (1.00 + (settings.MIN_SPREAD / 2))

        # Midpoint, used for simpler order placement.
        self.start_position_mid = ticker["mid"]
        logger.info(
            "%s Ticker: Buy: %.2f, Sell: %.2f" %
            (self.instrument['symbol'], ticker["buy"], ticker["sell"])
        )
        logger.info('Start Positions: Buy: %.2f, Sell: %.2f, Mid: %.2f' %
                    (self.start_position_buy, self.start_position_sell, self.start_position_mid))
        return ticker

    def get_price_offset(self, index):
        """Given an index (1, -1, 2, -2, etc.) return the price for that side of the book.
           Negative is a buy, positive is a sell."""
        # Maintain existing spreads for max profit
        if settings.MAINTAIN_SPREADS:
            start_position = self.start_position_buy if index < 0 else self.start_position_sell
            # First positions (index 1, -1) should start right at start_position, others should branch from there
            index = index + 1 if index < 0 else index - 1
        else:
            # Offset mode: ticker comes from a reference exchange and we define an offset.
            start_position = self.start_position_buy if index < 0 else self.start_position_sell

            # If we're attempting to sell, but our sell price is actually lower than the buy,
            # move over to the sell side.
            if index > 0 and start_position < self.start_position_buy:
                start_position = self.start_position_sell
            # Same for buys.
            if index < 0 and start_position > self.start_position_sell:
                start_position = self.start_position_buy

        return round(start_position * (1 + settings.INTERVAL) ** index, self.instrument['tickLog'])

    ###
    # Orders
    ###
    
    def handle_trade_R_Breaker(self):
        #logger.info('Debug by Lu: handle_trade_R_Breaker is called')   
        buy_orders = []
        sell_orders = []
        # xxxx
     
        instrument = self.exchange.get_instrument()  
        if self.is_newDay(): #a new day start
            self.prevClosePrice = instrument["prevClosePrice"]
            self.prevHighPrice = self.todayHighPrice
            self.prevLowPrice = self.todayLowPrice
        
            self.Pivot = (self.prevClosePrice + self.prevHighPrice + self.prevLowPrice) / 3
        
            self.buy_break = self.prevHighPrice + 2 * (self.Pivot - self.prevLowPrice)
            self.sell_setup = self.Pivot + (self.prevHighPrice - self.prevLowPrice)
            self.sell_enter = 2 * self.Pivot - self.prevLowPrice
            self.buy_enter = 2 * self.Pivot - self.prevHighPrice
            self.buy_setup = self.Pivot - (self.prevHighPrice - self.prevLowPrice)
            self.sell_break = self.prevLowPrice - 2 * (self.prevHighPrice - self.Pivot)
            
        self.todayHighPrice = instrument["highPrice"]
        self.todayLowPrice = instrument["lowPrice"]
        lastPrice = instrument["lastPrice"]
        
        position = self.exchange.get_delta()

        logger.info('Debug by Lu: prevClosePrice: %.2f, prevHighPrice: %.2f, prevLowPrice: %.2f' %(self.prevClosePrice, self.prevHighPrice,self.prevLowPrice))
        logger.info('Debug by Lu: buy_break: %.2f, sell_setup: %.2f, sell_enter: %.2f, buy_enter: %.2f, buy_setup: %.2f, sell_break: %.2f' %(self.buy_break, self.sell_setup, self.sell_enter, self.buy_enter, self.buy_setup, self.sell_break))
        logger.info('Debug by Lu: position = %d' % position)
        
        if position == 0: 
            if lastPrice > self.buy_break:
                buyOrder = self.create_order(-self.positionSize,instrument)
                buy_orders.append(buyOrder)
                logger.info('Debug by Lu: lastPrice > buy_break, buy triggered: price = %.2f, nummer = %d' % (buyOrder["price"], buyOrder["orderQty"]))
            if lastPrice < self.sell_break:
                sellOrder = self.create_order(self.positionSize,instrument)
                sell_orders.append(sellOrder)
                logger.info('Debug by Lu: lastPrice < sell_break, sell triggered: price = %.2f, nummer = %d' % (sellOrder["price"], sellOrder["orderQty"]))
        elif position > 0:
            if self.todayHighPrice > self.sell_setup and lastPrice < self.sell_enter:
                sellOrder = self.create_order(self.positionSize * 2,instrument)
                sell_orders.append(sellOrder)
                logger.info('Debug by Lu: todayHighPrice > sell_setup and lastPrice < sell_enter, sell triggered: price = %.2f, nummer = %d' % (sellOrder["price"], sellOrder["orderQty"]))
        elif position < 0:
            if self.todayLowPrice < self.buy_setup and lastPrice > self.buy_enter:
                buyOrder = self.create_order(-self.positionSize * 2,instrument)
                buy_orders.append(buyOrder)
                logger.info('Debug by Lu: todayLowPrice < buy_setup and lastPrice > buy_enter, buy triggered: price = %.2f, nummer = %d' % (buyOrder["price"], buyOrder["orderQty"]))
                
        return self.converge_orders(buy_orders, sell_orders)
    
    def settlement(self, plastBidSize, plastBidPrice, plastAskPrice, plastAskSize): #平仓
        if self.dynamic_position > 0:
            if plastBidSize >= self.dynamic_position:
                self.endPrice_profit = plastBidPrice
                #print('lastPrice < sell_break, sell triggered: price = %.2f, nummer = %d' % (lastBidPrice, operateposition))
                self.benifitCaculate()
                self.dynamic_position =0
        if self.dynamic_position < 0:
            if plastAskSize >= abs(self.dynamic_position):
                self.endPrice_profit = plastAskPrice
                self.benifitCaculate()
                self.dynamic_position = 0

    def Zhishun(self, plastPrice, plastBidSize, plastBidPrice, plastAskPrice, plastAskSize):
        if self.dynamic_position > 0:
            pricealpha = (plastPrice - self.startPrice_profit) / self.startPrice_profit
            if pricealpha < -settings.ZHISHUN_PROZENT:
                self.settlement(plastBidSize, plastBidPrice, plastAskPrice, plastAskSize)
                if self.dynamic_position == 0:
                    print("price has decreased more than 5%, sell all")
        if self.dynamic_position < 0:
            pricealpha = (plastPrice - self.startPrice_profit) / self.startPrice_profit
            if pricealpha > settings.ZHISHUN_PROZENT:
                self.settlement(plastBidSize, plastBidPrice, plastAskPrice, plastAskSize)
                if self.dynamic_position == 0:
                    print("price has increased more than 5%, buy all")
                    
    def calcATR(self):
        N = settings.ATRN
        TR_List = []
        #TR_List_Str = ""
        for i in range(0,N):
            TR = self.highPriceQueue[i] - self.lowPriceQueue[i]
            TR_List.append(TR)
            #TR_List_Str += (str(round(TR,2))+" ")
        ATR = np.array(TR_List).mean()
        self.ATR = ATR
        #TR_List_Str += ("ATR = " + str(self.ATR))
        #print("ATR = %.2f" % self.ATR)
        #print(TR_List_Str)
    
    
    def CalcUnit(self, nowPrice):  #   计算一个ATR单位的仓位
        X = settings.START_BTCOIN + self.totalprofit
        if self.ATR != 0:
            self.UnitPosition = int(abs(0.1 * X * nowPrice * (nowPrice + self.ATR) / self.ATR))
        print("bitcoin = %.4f today UnitPosition = %d, nowPrice = %.2f, ATR = %d, dynamicpostion = %d" % (X, self.UnitPosition, nowPrice, self.ATR, self.dynamic_position))
        
    def tradeTultle(self):
        sell_break = 0.0
        buy_break = 0.0
        if self.TurtlePos == 0:
            if self.currentPrice > self.maxPreNhighPrice:
                traderesult = self.backtest_trade(self.UnitPosition, "buy")
                if traderesult:
                    self.AddPrice[0] = self.lastAskPrice + 0.5 * self.ATR                
                    self.TurtlePos += 1
                    self.startPrice_profit = self.lastAskPrice
                    print(self.prevDayBacktest + (" 价格向上突破%.2f,建仓:%d, 建仓价为%.2f" %(self.maxPreNhighPrice, self.UnitPosition,self.startPrice_profit)))
            elif self.currentPrice < self.minPreNlowPrice:
                traderesult = self.backtest_trade(self.UnitPosition, "sell")
                if traderesult:
                    self.AddPrice[0] = self.lastBidPrice - 0.5 * self.ATR
                    sellbreak = self.lastBidPrice - 0.5 * self.ATR
                    self.TurtlePos -= 1
                    self.startPrice_profit = self.lastAskPrice
                    print(self.prevDayBacktest + (" 价格向下跌破%.2f,建仓:-%d, 建仓价为%.2f" %(self.minPreNlowPrice, self.UnitPosition, self.startPrice_profit)))
        elif abs(self.TurtlePos) > 0:
            sell_break = self.AddPrice[abs(self.TurtlePos) - 1] - 2 * self.ATR
            buy_break = self.AddPrice[abs(self.TurtlePos) - 1] + 2 * self.ATR
            if self.TurtlePos > 0:
                if self.currentPrice < self.minPreNlowPrice or self.unrealisedbenifit > settings.ZHIYINGUSD:
                    pos = self.dynamic_position
                    traderesult = self.backtest_trade(abs(self.dynamic_position), "sell")
                    if traderesult:
                        self.TurtlePos = 0
                        print(self.prevDayBacktest + (" 价格向下跌破N日最低价格触发止盈, 平仓价为%.2f" %(self.lastBidPrice)))
                        self.benifitCaculatePos(pos, self.lastBidPrice)
                        return 0
                if self.currentPrice < sell_break:
                    pos = self.dynamic_position
                    traderesult = self.backtest_trade(abs(self.dynamic_position), "sell")
                    if traderesult:
                        self.TurtlePos = 0
                        print(self.prevDayBacktest + (" 价格向下跌破2ATR触发平仓, 平仓价为%.2f" %(self.lastBidPrice)))
                        self.benifitCaculatePos(pos, self.lastBidPrice)
                        return 0
                elif abs(self.TurtlePos) >= settings.ADDTIME:
                    return 0
                elif self.currentPrice > self.AddPrice[abs(self.TurtlePos) - 1]:
                    traderesult = self.backtest_trade(self.UnitPosition, "buy")
                    if traderesult:
                        self.AddPrice[abs(self.TurtlePos)] = self.AddPrice[abs(self.TurtlePos) - 1] + 0.5 * self.ATR
                        self.TurtlePos += 1
                        self.updateStartPriceProfit(self.lastAskPrice, self.UnitPosition)
                        print(self.prevDayBacktest + (" 价格向上突破%.2f, 加仓 %d, 现仓位为%.d, 仓位均价%.2f" %(self.AddPrice[abs(self.TurtlePos) - 2],self.UnitPosition, self.dynamic_position, self.startPrice_profit)))
            elif self.TurtlePos < 0:
                if self.currentPrice > self.maxPreNhighPrice  or self.unrealisedbenifit > settings.ZHIYINGUSD:
                    pos = self.dynamic_position
                    traderesult = self.backtest_trade(abs(self.dynamic_position), "buy")
                    if traderesult:
                        self.TurtlePos = 0
                        print(self.prevDayBacktest + (" 价格向上涨破N日最高价格触发平仓, 平仓价为%.2f" %(self.lastAskPrice)))
                        self.benifitCaculatePos(pos, self.lastAskPrice)
                        return 0
                if self.currentPrice > buy_break:
                    pos = self.dynamic_position
                    traderesult = self.backtest_trade(abs(self.dynamic_position), "buy")
                    if traderesult:
                        self.TurtlePos = 0
                        print(self.prevDayBacktest + (" 价格向上涨破2ATR触发平仓, 平仓价为%.2f" %(self.lastAskPrice)))
                        self.benifitCaculatePos(pos, self.lastAskPrice)
                        return 0
                if abs(self.TurtlePos) >= settings.ADDTIME:
                    return 0
                if self.currentPrice < self.AddPrice[abs(self.TurtlePos) - 1]:
                    traderesult = self.backtest_trade(self.UnitPosition, "sell")
                    if traderesult:
                        self.AddPrice[abs(self.TurtlePos)] = self.AddPrice[abs(self.TurtlePos) - 1] - 0.5 * self.ATR   
                        self.TurtlePos -= 1   
                        self.updateStartPriceProfit(self.lastBidPrice, (self.UnitPosition * (-1)))   
                        print(self.prevDayBacktest + (" 价格向下突破%.2f, 卖出加仓 -%d, 现仓位为%.d, 仓位均价%.2f" %(self.AddPrice[abs(self.TurtlePos) - 2],self.UnitPosition,self.dynamic_position, self.startPrice_profit)))
   
    def tradeMovingAverage(self):
        sell_break = 0.0
        buy_break = 0.0
        traderest = 0.0
        successTrade = 0.0
        if self.TurtlePos == 0:
            if self.currentPrice > self.movingAveragePrice:
                traderest = self.tradeTheRest(self.UnitPosition)
                successTrade = self.UnitPosition - traderest
                if abs(successTrade)>0:
                    self.AddPrice[0] = self.lastAskPrice + 0.5 * self.ATR                
                    self.TurtlePos += 1
                    print(self.prevDayBacktest + (" 价格向上突破均线%.2f,建仓:%d, 建仓价为%.2f" %(self.movingAveragePrice, self.UnitPosition,self.startPrice_profit)))
            elif self.currentPrice < self.movingAveragePrice:
                traderest = self.tradeTheRest(self.UnitPosition * (-1))
                successTrade = self.UnitPosition * (-1) - traderest
                if abs(successTrade)>0:
                    self.AddPrice[0] = self.lastBidPrice - 0.5 * self.ATR
                    self.TurtlePos -= 1
                    print(self.prevDayBacktest + (" 价格向下跌破均线%.2f,建仓:-%d, 建仓价为%.2f" %(self.movingAveragePrice, self.UnitPosition, self.startPrice_profit)))
        elif abs(self.TurtlePos) > 0:
            sell_break = self.AddPrice[abs(self.TurtlePos) - 1] - 2 * self.ATR
            buy_break = self.AddPrice[abs(self.TurtlePos) - 1] + 2 * self.ATR
            if self.TurtlePos > 0:
                if self.currentPrice < self.movingAveragePrice or self.currentPrice < sell_break or self.unrealisedbenifit >= settings.ZHIYINGUSD:
                    pos = self.dynamic_position
                    traderest = self.tradeTheRest(pos * (-1)) #sell all the positions
                    successTrade = pos * (-1) - traderest
                    if abs(successTrade) > 0:
                        self.TurtlePos = 0
                        print(self.prevDayBacktest + (" 价格向下跌破均线/2ATR触发止盈/平仓, 平仓价为%.2f" %(self.lastBidPrice)))
                        return traderest
                elif abs(self.TurtlePos) >= settings.ADDTIME:
                    return traderest
                elif self.currentPrice > self.AddPrice[abs(self.TurtlePos) - 1]:
                    if self.dynamic_position >= self.UPPERLIMITPOS:
                        return traderest
                    elif (self.dynamic_position + self.UnitPosition) > self.UPPERLIMITPOS:
                        self.UnitPosition = self.UPPERLIMITPOS - self.dynamic_position
                    traderest = self.tradeTheRest(self.UnitPosition)
                    successTrade = self.UnitPosition - traderest
                    if abs(successTrade) > 0.00:
                        self.AddPrice[abs(self.TurtlePos)] = self.AddPrice[abs(self.TurtlePos) - 1] + 0.5 * self.ATR
                        self.TurtlePos += 1
                        print(self.prevDayBacktest + (" 价格向上突破%.2f, 加仓 %d, 现仓位为%.d, 仓位均价%.2f" %(self.AddPrice[abs(self.TurtlePos) - 2],self.UnitPosition, self.dynamic_position, self.startPrice_profit)))
            elif self.TurtlePos < 0:
                if self.currentPrice > self.movingAveragePrice  or self.currentPrice > buy_break or self.unrealisedbenifit >= settings.ZHIYINGUSD:
                    pos = self.dynamic_position
                    traderest = self.tradeTheRest(abs(pos)) #平仓
                    successTrade = abs(pos) - traderest
                    if abs(successTrade) > 0.0:
                        self.TurtlePos = 0
                        print(self.prevDayBacktest + (" 价格向上涨破均价/2ATR触发平仓, 平仓价为%.2f" %(self.lastAskPrice)))
                        return traderest
                if abs(self.TurtlePos) >= settings.ADDTIME:
                    return traderest
                if self.currentPrice < self.AddPrice[abs(self.TurtlePos) - 1]:
                    if self.dynamic_position <= self.UNTERLIMITPOS:
                        return traderest
                    elif (self.dynamic_position - self.UnitPosition) < self.UNTERLIMITPOS:
                        self.UnitPosition = self.dynamic_position - self.UNTERLIMITPOS
                    traderest = self.tradeTheRest(self.UnitPosition*(-1))
                    successTrade = self.UnitPosition * (-1) - traderest
                    if abs(successTrade) > 0:
                        self.AddPrice[abs(self.TurtlePos)] = self.AddPrice[abs(self.TurtlePos) - 1] - 0.5 * self.ATR   
                        self.TurtlePos -= 1   
                        print(self.prevDayBacktest + (" 价格向下突破%.2f, 卖出加仓 -%d, 现仓位为%.d, 仓位均价%.2f" %(self.AddPrice[abs(self.TurtlePos) - 2],self.UnitPosition,self.dynamic_position, self.startPrice_profit)))   
        return traderest    
        
    def backtest_trade_rest(self, pos,dir = "buy"):   #trade in market price
        if pos > 0:
            if self.lastAskSize >= pos:
                #self.startPrice_profit = self.lastAskPrice
                self.dynamic_position +=pos
                return pos
            else:
                self.dynamic_position += self.lastAskSize
                return self.lastAskSize
        elif pos < 0:
            if self.lastBidSize >= abs(pos):
                #self.startPrice_profit = self.lastBidPrice
                self.dynamic_position +=pos
                return pos
            else:
                self.dynamic_position -= self.lastBidSize
                return (self.lastBidSize) * (-1)
            
    def backtest_trade(self, pos, dir = "buy"):   #trade in market price
        if dir == "buy":
            if self.lastAskSize >= pos:
                #self.startPrice_profit = self.lastAskPrice
                self.dynamic_position +=pos
                return True
            else:
                return False
        elif dir == "sell":
            if self.lastBidSize >= pos:
                #self.startPrice_profit = self.lastBidPrice
                self.dynamic_position -=pos
                return True
            else:
                return False
    
    def tradeTheRest(self, pos):   #trade the rest positions
        if pos > 0:
            tradePrice = self.lastAskPrice
        else:
            tradePrice = self.lastBidPrice
        
        posBeforeTrade = self.dynamic_position
        successTradeNum = self.backtest_trade_rest(pos)
        
        if posBeforeTrade * successTradeNum > 0.1 or posBeforeTrade == 0:
            self.updateStartPriceProfit(tradePrice, successTradeNum)
        elif posBeforeTrade * successTradeNum < -0.1:
            self.benifitCaculatePos(successTradeNum * (-1), tradePrice)
            
        restTrade = pos - successTradeNum
        return restTrade
           
    def handle_trade_Turtle_backtest(self, tradeline = " "):
        #logger.info('Debug by Lu: handle_trade_Turtle_backtest is called')   
        if self.firstTime:
            self.todayHighPrice = 0
            self.todayLowPrice = 10000
            self.initBitcoinPrice = getTradeHis.getPrevClosePriceFromLine(tradeline)
            #self.firstTime = False
        
        if self.is_newDay(tradeline):
            self.highPriceQueue.append(self.todayHighPrice)
            self.lowPriceQueue.append(self.todayLowPrice)
            self.prevClosePrice = getTradeHis.getPrevClosePriceFromLine(tradeline)
            self.prevHighPrice = self.todayHighPrice
            self.prevLowPrice = self.todayLowPrice
            self.todayHighPrice = self.prevClosePrice
            self.todayLowPrice = self.prevClosePrice
            self.getPreNMaxMinPrice()
            self.simulateDayNumbers += 1
            self.calcATR()
            self.CalcUnit(self.prevClosePrice)
            
     
        lastAskPrice = getTradeHis.getbidPriceFromLine(tradeline)
        lastBidPrice = getTradeHis.getaskPriceFromLine(tradeline)
        lastAskSize = getTradeHis.getaskSizeFromLine(tradeline)
        lastBidSize = getTradeHis.getbidSizeFromLine(tradeline)
        
        if lastBidPrice < 0 or lastAskPrice < 0: # both of the prices are none
            return 0
        
        if abs(lastAskPrice - lastBidPrice) > settings.RESONABLE_PRICE_GAP:
            #数据无效,买价与卖价差别太大
            return 0
        
        self.lastAskPrice = lastAskPrice
        self.lastBidPrice = lastBidPrice
        self.lastAskSize = lastAskSize
        self.lastBidSize = lastBidSize
        
        lastPrice = (lastAskPrice + lastBidPrice) / 2
        
        if self.firstTime:
            self.preCurrentPrice = lastPrice
            self.currentPrice = lastPrice
            self.firstTime = False
            
        #print("lastPrice = %.2f, preCurrentPrice = %.2f" %(lastPrice, self.preCurrentPrice))
        if abs(lastPrice - self.preCurrentPrice) > settings.RESONABLE_PRICE_STEP:
            #数据无效,filter the unresonable pricegap
            print(self.prevDayBacktest + "价格异常变动，跳过此次交易，请查看！！！！！！！！！")
            return 0
        
        self.preCurrentPrice = self.currentPrice
        self.currentPrice = lastPrice
        
        if lastPrice > self.todayHighPrice:
            self.todayHighPrice = lastPrice
        if lastPrice < self.todayLowPrice:
            self.todayLowPrice = lastPrice
        self.baseBenifit = (lastPrice - self.initBitcoinPrice) / self.initBitcoinPrice * 100
        self.unrealisedBenifit()
        
        if self.simulateDayNumbers > settings.DonchianN:
            #self.Zhishun(lastPrice, lastBidSize, lastBidPrice, lastAskPrice, lastAskSize)
            self.tradeTultle()

    def handle_movingaverage_backtest(self, tradeline = " "):
        #logger.info('Debug by Lu: handle_trade_Turtle_backtest is called')   
        if self.firstTime:
            self.todayHighPrice = 0
            self.todayLowPrice = 10000
            self.initBitcoinPrice = getTradeHis.getPrevClosePriceFromLine(tradeline)
            self.prevClosePrice = self.initBitcoinPrice
            for i in range(0,settings.AVERGAGEDAY):
                self.movingAvergePrices.append(self.initBitcoinPrice)
            
            #self.firstTime = False
        Is_newDay = self.is_newDay(tradeline)
        if Is_newDay:
            self.highPriceQueue.append(self.todayHighPrice)
            self.lowPriceQueue.append(self.todayLowPrice)
            self.prevClosePrice = getTradeHis.getPrevClosePriceFromLine(tradeline)
            self.movingAvergePrices.append(self.prevClosePrice)
            self.movingAveragePrice = np.mean(self.movingAvergePrices)
            self.prevHighPrice = self.todayHighPrice
            self.prevLowPrice = self.todayLowPrice
            self.todayHighPrice = self.prevClosePrice
            self.todayLowPrice = self.prevClosePrice
            self.getPreNMaxMinPrice()
            self.simulateDayNumbers += 1
            self.calcATR()
            self.CalcUnit(self.prevClosePrice)
            self.updatePositionLimit()
            self.traderest = 0.0
            
     
        lastAskPrice = getTradeHis.getbidPriceFromLine(tradeline)
        lastBidPrice = getTradeHis.getaskPriceFromLine(tradeline)
        lastAskSize = getTradeHis.getaskSizeFromLine(tradeline)
        lastBidSize = getTradeHis.getbidSizeFromLine(tradeline)
        
        if getTradeHis.IsThereANone(tradeline):
            return 0
        
        if abs(lastAskPrice - lastBidPrice) > settings.RESONABLE_PRICE_GAP:
            #数据无效,买价与卖价差别太大
            return 0
        
        self.lastAskPrice = lastAskPrice
        self.lastBidPrice = lastBidPrice
        self.lastAskSize = lastAskSize
        self.lastBidSize = lastBidSize
        
        lastPrice = (lastAskPrice + lastBidPrice) / 2
        
        if self.firstTime:
            self.preCurrentPrice = lastPrice
            self.currentPrice = lastPrice
            self.firstTime = False
            
        #print("lastPrice = %.2f, preCurrentPrice = %.2f" %(lastPrice, self.preCurrentPrice))
        if abs(lastPrice - self.preCurrentPrice) > settings.RESONABLE_PRICE_STEP:
            #数据无效,filter the unresonable pricegap
            print(self.prevDayBacktest + "价格异常变动，跳过此次交易，请查看！！！！！！！！！")
            return 0
        
        self.preCurrentPrice = self.currentPrice
        self.currentPrice = lastPrice
        
        if lastPrice > self.todayHighPrice:
            self.todayHighPrice = lastPrice
        if lastPrice < self.todayLowPrice:
            self.todayLowPrice = lastPrice
        self.baseBenifit = (lastPrice - self.initBitcoinPrice) / self.initBitcoinPrice * 100
        self.unrealisedBenifit()
        
        #self.UnitPosition = settings.START_BTCOIN * self.initBitcoinPrice / 10
        
        if self.simulateDayNumbers > settings.AVERGAGEDAY and Is_newDay:
            #self.Zhishun(lastPrice, lastBidSize, lastBidPrice, lastAskPrice, lastAskSize)
            #self.UnitPosition = 800
            self.traderest = self.tradeMovingAverage()
        elif abs(self.traderest) > 0.01:
            self.traderest = self.tradeTheRest(self.traderest)
    
    def handle_trade_R_Breaker_backtest(self, tradeline = " "):
        #logger.info('Debug by Lu: handle_trade_R_Breaker is called')   
        buy_orders = []
        sell_orders = []
        # xxxx
        
        
        if self.firstTime:
            self.todayHighPrice = 0
            self.todayLowPrice = 10000
            self.initBitcoinPrice = getTradeHis.getPrevClosePriceFromLine(tradeline)
            self.firstTime = False
        
        if self.is_newDay(tradeline):
            self.prevClosePrice = getTradeHis.getPrevClosePriceFromLine(tradeline)
            self.prevHighPrice = self.todayHighPrice
            self.prevLowPrice = self.todayLowPrice
            self.buy_setup = self.prevLowPrice - self.f1 * (self.prevHighPrice - self.prevClosePrice)
            self.sell_setup = self.prevHighPrice + self.f1 * (self.prevClosePrice - self.prevLowPrice)
            self.buy_enter = (1 + self.f2)/2 * (self.prevHighPrice + self.prevLowPrice) - self.f2 * self.prevHighPrice
            self.sell_enter = (1 + self.f2)/2 * (self.prevHighPrice + self.prevLowPrice) - self.f2 * self.prevLowPrice
            self.buy_break = self.sell_setup + self.f3 * (self.sell_setup - self.buy_setup)
            self.sell_break = self.buy_setup - self.f3 * (self.sell_setup - self.buy_setup)
            print('prevClosePrice: %.2f, prevHighPrice: %.2f, prevLowPrice: %.2f' %(self.prevClosePrice, self.prevHighPrice,self.prevLowPrice))
            print('buy_break: %.2f, sell_setup: %.2f, sell_enter: %.2f, buy_enter: %.2f, buy_setup: %.2f, sell_break: %.2f' %(self.buy_break, self.sell_setup, self.sell_enter, self.buy_enter, self.buy_setup, self.sell_break))
            
            self.todayHighPrice = self.prevClosePrice
            self.todayLowPrice = self.prevClosePrice
            #self.Pivot = (self.prevClosePrice + self.prevHighPrice + self.prevLowPrice) / 3
            #self.buy_break = self.prevHighPrice + 2 * (self.Pivot - self.prevLowPrice)
            #self.sell_setup = self.Pivot + (self.prevHighPrice - self.prevLowPrice)
            #self.sell_enter = 2 * self.Pivot - self.prevLowPrice
            #self.buy_enter = 2 * self.Pivot - self.prevHighPrice
            #self.buy_setup = self.Pivot - (self.prevHighPrice - self.prevLowPrice)
            #self.sell_break = self.prevLowPrice - 2 * (self.prevHighPrice - self.Pivot)
            #logger.info('Debug_by_Lu: buy_break: %.2f, sell_setup: %.2f, sell_enter: %.2f, buy_enter: %.2f, buy_setup: %.2f, sell_break: %.2f' %(self.buy_break, self.sell_setup, self.sell_enter, self.buy_enter, self.buy_setup, self.sell_break))
                
        lastAskPrice = getTradeHis.getbidPriceFromLine(tradeline)
        lastBidPrice = getTradeHis.getaskPriceFromLine(tradeline)
        lastAskSize = getTradeHis.getaskSizeFromLine(tradeline)
        lastBidSize = getTradeHis.getbidSizeFromLine(tradeline)
        if abs(lastAskPrice - lastBidPrice) > settings.RESONABLE_PRICE_GAP:
            #数据无效,filter the unresonable pricegap
            return 0
        self.lastAskPrice = lastAskPrice
        self.lastBidPrice = lastBidPrice
        self.lastAskSize = lastAskSize
        self.lastBidSize = lastBidSize
        
        lastPrice = (lastAskPrice + lastBidPrice) / 2
        
        if lastPrice > self.todayHighPrice:
            self.todayHighPrice = lastPrice
        if lastPrice < self.todayLowPrice:
            self.todayLowPrice = lastPrice
        self.baseBenifit = (lastPrice - self.initBitcoinPrice) / self.initBitcoinPrice * 100
        #logger.info('Debug by Lu: prevClosePrice: %.2f, prevHighPrice: %.2f, prevLowPrice: %.2f' %(self.prevClosePrice, self.prevHighPrice,self.prevLowPrice))
        #logger.info('Debug_by_Lu: lastprice: %.2f, buy_break: %.2f, sell_setup: %.2f, sell_enter: %.2f, buy_enter: %.2f, buy_setup: %.2f, sell_break: %.2f' %(lastPrice, self.buy_break, self.sell_setup, self.sell_enter, self.buy_enter, self.buy_setup, self.sell_break))
        #logger.info('Debug by Lu: position = %d' % position)
        operateposition = settings.POSITION_SIZE * 100
        
        if not self.firstDay:
            self.Zhishun(lastPrice, lastBidSize, lastBidPrice, lastAskPrice, lastAskSize)
            if self.dynamic_position == 0: 
                if lastPrice > self.buy_break:
                    if lastAskSize >= operateposition:
                        self.startPrice_profit = lastAskPrice
                        self.dynamic_position +=operateposition
                        #print('lastPrice > buy_break, buy triggered: price = %.2f, nummer = %d' % (lastAskPrice, operateposition))
                if lastPrice < self.sell_break:
                    if lastBidSize >= operateposition:
                        self.startPrice_profit = lastBidPrice
                        self.dynamic_position -=operateposition
                        #print('lastPrice < sell_break, sell triggered: price = %.2f, nummer = %d' % (lastBidPrice, operateposition))
            elif self.dynamic_position > 0:
                if lastPrice < self.sell_break:
                    if lastBidSize >= operateposition:
                        self.endPrice_profit = lastBidPrice
                        #print('lastPrice < sell_break, sell triggered: price = %.2f, nummer = %d' % (lastBidPrice, operateposition))
                        self.benifitCaculate()
                        self.dynamic_position -=operateposition
                elif self.todayHighPrice > self.sell_setup and lastPrice < self.sell_enter:
                    if lastBidSize >= operateposition:
                        self.endPrice_profit = lastBidPrice
                        #print('todayHighPrice > sell_setup and lastPrice < sell_enter, sell triggered: price = %.2f, nummer = %d' % (lastBidPrice, operateposition))
                        self.benifitCaculate()
                        self.dynamic_position -=operateposition
                        restBidSize = lastBidSize - operateposition
                        if restBidSize >= operateposition:
                            self.startPrice_profit = lastBidPrice
                            self.dynamic_position -= operateposition          
            elif self.dynamic_position < 0:
                if lastPrice > self.buy_break:
                    if lastAskSize >= operateposition:
                        self.endPrice_profit = lastAskPrice
                        #print('lastPrice > buy_break, buy triggered: price = %.2f, nummer = %d' % (lastAskPrice, operateposition))
                        self.benifitCaculate()
                        self.dynamic_position +=operateposition
                elif self.todayLowPrice < self.buy_setup and lastPrice > self.buy_enter:
                    if lastAskSize >= operateposition:
                        self.endPrice_profit = lastAskPrice
                        #print('todayLowPrice < buy_setup and lastPrice > buy_enter, buy triggered: price = %.2f, nummer = %d' % (lastAskPrice, operateposition))
                        self.benifitCaculate()
                        self.dynamic_position +=operateposition
                        restAskSize = lastAskSize - operateposition
                        if restAskSize >= operateposition:
                            self.startPrice_profit = lastAskPrice
                            self.dynamic_position += operateposition
                            

    def place_orders(self):
        """Create order items for use in convergence."""

        buy_orders = []
        sell_orders = []
        # Create orders from the outside in. This is intentional - let's say the inner order gets taken;
        # then we match orders from the outside in, ensuring the fewest number of orders are amended and only
        # a new order is created in the inside. If we did it inside-out, all orders would be amended
        # down and a new order would be created at the outside.
        for i in reversed(range(1, settings.ORDER_PAIRS + 1)):
            if not self.long_position_limit_exceeded():
                buy_orders.append(self.prepare_order(-i))
            if not self.short_position_limit_exceeded():
                sell_orders.append(self.prepare_order(i))
        #logger.info('Debug by Lu: place_orders is called') 
        return self.converge_orders(buy_orders, sell_orders)

    def prepare_order(self, index):
        """Create an order object."""

        if settings.RANDOM_ORDER_SIZE is True:
            quantity = random.randint(settings.MIN_ORDER_SIZE, settings.MAX_ORDER_SIZE)
        else:
            quantity = settings.ORDER_START_SIZE + ((abs(index) - 1) * settings.ORDER_STEP_SIZE)

        price = self.get_price_offset(index)

        return {'price': price, 'orderQty': quantity, 'side': "Buy" if index < 0 else "Sell"}
    
    def create_order(self, index, par_instrument):
        """Create an order object."""
        
        if settings.RANDOM_ORDER_SIZE is True:
            quantity = random.randint(settings.MIN_ORDER_SIZE, settings.MAX_ORDER_SIZE)
        else:
            quantity = settings.ORDER_START_SIZE + ((abs(index) - 1) * settings.ORDER_STEP_SIZE)
            
        if index < 0: # buy
            price = par_instrument["askPrice"]
        if index > 0: # sell
            price = par_instrument["bidPrice"]

        return {'price': price, 'orderQty': quantity, 'side': "Buy" if index < 0 else "Sell"}   
    

    def converge_orders(self, buy_orders, sell_orders):
        """Converge the orders we currently have in the book with what we want to be in the book.
           This involves amending any open orders and creating new ones if any have filled completely.
           We start from the closest orders outward."""

        tickLog = self.exchange.get_instrument()['tickLog']
        to_amend = []
        to_create = []
        to_cancel = []
        buys_matched = 0
        sells_matched = 0
        existing_orders = self.exchange.get_orders()

        # Check all existing orders and match them up with what we want to place.
        # If there's an open one, we might be able to amend it to fit what we want.
        for order in existing_orders:
            try:
                if order['side'] == 'Buy':
                    desired_order = buy_orders[buys_matched]
                    buys_matched += 1
                else:
                    desired_order = sell_orders[sells_matched]
                    sells_matched += 1

                # Found an existing order. Do we need to amend it?
                if desired_order['orderQty'] != order['leavesQty'] or (
                        # If price has changed, and the change is more than our RELIST_INTERVAL, amend.
                        desired_order['price'] != order['price'] and
                        abs((desired_order['price'] / order['price']) - 1) > settings.RELIST_INTERVAL):
                    to_amend.append({'orderID': order['orderID'], 'leavesQty': desired_order['orderQty'],
                                     'price': desired_order['price'], 'side': order['side']})
            except IndexError:
                # Will throw if there isn't a desired order to match. In that case, cancel it.
                to_cancel.append(order)

        while buys_matched < len(buy_orders):
            to_create.append(buy_orders[buys_matched])
            buys_matched += 1

        while sells_matched < len(sell_orders):
            to_create.append(sell_orders[sells_matched])
            sells_matched += 1

        if len(to_amend) > 0:
            for amended_order in reversed(to_amend):
                reference_order = [o for o in existing_orders if o['orderID'] == amended_order['orderID']][0]
                logger.info("Amending %4s: %d @ %.*f to %d @ %.*f (%+.*f)" % (
                    amended_order['side'],
                    reference_order['leavesQty'], tickLog, reference_order['price'],
                    amended_order['leavesQty'], tickLog, amended_order['price'],
                    tickLog, (amended_order['price'] - reference_order['price'])
                ))
            # This can fail if an order has closed in the time we were processing.
            # The API will send us `invalid ordStatus`, which means that the order's status (Filled/Canceled)
            # made it not amendable.
            # If that happens, we need to catch it and re-tick.
            try:
                self.exchange.amend_bulk_orders(to_amend)
            except requests.exceptions.HTTPError as e:
                errorObj = e.response.json()
                if errorObj['error']['message'] == 'Invalid ordStatus':
                    logger.warn("Amending failed. Waiting for order data to converge and retrying.")
                    sleep(0.5)
                    #return self.place_orders()
                    return self.handle_trade()
                else:
                    logger.error("Unknown error on amend: %s. Exiting" % errorObj)
                    sys.exit(1)

        if len(to_create) > 0:
            logger.info("Creating %d orders:" % (len(to_create)))
            for order in reversed(to_create):
                logger.info("%4s %d @ %.*f" % (order['side'], order['orderQty'], tickLog, order['price']))
            self.exchange.create_bulk_orders(to_create)

        # Could happen if we exceed a delta limit
        if len(to_cancel) > 0:
            logger.info("Canceling %d orders:" % (len(to_cancel)))
            for order in reversed(to_cancel):
                logger.info("%4s %d @ %.*f" % (order['side'], order['leavesQty'], tickLog, order['price']))
            self.exchange.cancel_bulk_orders(to_cancel)

    ###
    # Position Limits
    ###

    def short_position_limit_exceeded(self):
        "Returns True if the short position limit is exceeded"
        if not settings.CHECK_POSITION_LIMITS:
            return False
        position = self.exchange.get_delta()
        return position <= settings.MIN_POSITION

    def long_position_limit_exceeded(self):
        "Returns True if the long position limit is exceeded"
        if not settings.CHECK_POSITION_LIMITS:
            return False
        position = self.exchange.get_delta()
        return position >= settings.MAX_POSITION

    ###
    # Sanity
    ##

    def sanity_check(self):
        """Perform checks before placing orders."""

        # Check if OB is empty - if so, can't quote.
        self.exchange.check_if_orderbook_empty()

        # Ensure market is still open.
        self.exchange.check_market_open()

        # Get ticker, which sets price offsets and prints some debugging info.
        ticker = self.get_ticker()

        # Sanity check:
        if self.get_price_offset(-1) >= ticker["sell"] or self.get_price_offset(1) <= ticker["buy"]:
            logger.error(self.start_position_buy, self.start_position_sell)
            logger.error("First buy position: %s\nBitMEX Best Ask: %s\nFirst sell position: %s\nBitMEX Best Bid: %s" %
                         (self.get_price_offset(-1), ticker["sell"], self.get_price_offset(1), ticker["buy"]))
            logger.error("Sanity check failed, exchange data is inconsistent")
            self.exit()
        logger.debug('Debug by Lu: get_price_offset(-1): %.2f, get_price_offset(1): %.2f' %
                    (self.get_price_offset(-1), self.get_price_offset(1)))
        logger.debug('Debug by Lu: position = self.exchange.get_delta() = %.2f' %
                    (self.exchange.get_delta()))
        # Messanging if the position limits are reached
        if self.long_position_limit_exceeded():
            logger.info("Long delta limit exceeded")
            logger.info("Current Position: %.f, Maximum Position: %.f" %
                        (self.exchange.get_delta(), settings.MAX_POSITION))

        if self.short_position_limit_exceeded():
            logger.info("Short delta limit exceeded")
            logger.info("Current Position: %.f, Minimum Position: %.f" %
                        (self.exchange.get_delta(), settings.MIN_POSITION))

    ###
    # Running
    ###

    def check_file_change(self):
        """Restart if any files we're watching have changed."""
        for f, mtime in watched_files_mtimes:
            if getmtime(f) > mtime:
                self.restart()

    def check_connection(self):
        """Ensure the WS connections are still open."""
        return self.exchange.is_open()

    def exit(self):
        logger.info("Shutting down. All open orders will be cancelled.")
        try:
            self.exchange.cancel_all_orders()
            self.exchange.bitmex.exit()
        except errors.AuthenticationError as e:
            logger.info("Was not authenticated; could not cancel orders.")
        except Exception as e:
            logger.info("Unable to cancel orders: %s" % e)

        sys.exit()

    def run_loop(self):
        while True:
            sys.stdout.write("-----\n")
            sys.stdout.flush()

            self.check_file_change()
            sleep(settings.LOOP_INTERVAL)

            # This will restart on very short downtime, but if it's longer,
            # the MM will crash entirely as it is unable to connect to the WS on boot.
            if not self.check_connection():
                logger.error("Realtime data connection unexpectedly closed, restarting.")
                self.restart()

            self.sanity_check()  # Ensures health of mm - several cut-out points here
            self.print_status()  # Print skew, delta, etc
            self.handle_trade()  # this function will replace the place_orders()
            #self.place_orders()  # Creates desired orders and converges to existing orders
    
    def recordbenifit(self, file):
            #file.write(str("%.2f" % self.baseBenifit))
            file.write(str("%.2f" % self.prevClosePrice))
            file.write(" ")
            file.write(str("%.2f" % self.totalUSDbenifit))
            file.write(" ")
            file.write(str("%d" % self.dynamic_position))
            file.write(" ")
            file.write(str("%.2f" % self.movingAveragePrice))
            file.write(" ")
            file.write(str("%.2f" % self.baseBenifit))
            file.write("\n")
    
    def run_backtesting(self):
        logger.info("Start backtesting from date: " + settings.START_DATE + " to date: " + settings.END_DATE)
        recorddata = open(settings.BACKTESTFILE,"r")
        graficdata = open("grafic.txt", "w")
        dateindex = settings.START_DATE
        number_per_day = 1440 // settings.BACKTEST_PERIOD
        line = " " 
        while True:
            for i in range(0,number_per_day):
                line = recorddata.readline()
                if not line:
                    break
                if settings.STRATEGY == "R_Breaker":
                    self.handle_trade_R_Breaker_backtest(line)
                if settings.STRATEGY == "Turtle":
                    self.handle_trade_Turtle_backtest(line)
                if settings.STRATEGY == "MovingAverage":
                    self.handle_movingaverage_backtest(line)
                    
            dateindex = getTradeHis.getNextDay(dateindex)
            # write the benifit comparision
            
            self.recordbenifit(graficdata)
    
            if self.bankrupt:
                print("you are bankrupt now!!!!!!")
                break

            if getTradeHis.is_datefinished(dateindex,settings.END_DATE):
                self.lastDaysettlement(line)
                self.recordbenifit(graficdata)
                print("back testing is finished!")
                print("盈利交易次数为:%d, 亏损交易次数为%d" %(self.numberPostiveTrade,self.numberNegativTrade))
                break 
        recorddata.close()
        graficdata.close()
            
    def restart(self):
        logger.info("Restarting the market maker...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

#
# Helpers
#


def XBt_to_XBT(XBt):
    return float(XBt) / constants.XBt_TO_XBT


def cost(instrument, quantity, price):
    mult = instrument["multiplier"]
    P = mult * price if mult >= 0 else mult / price
    return abs(quantity * P)


def margin(instrument, quantity, price):
    return cost(instrument, quantity, price) * instrument["initMargin"]

def drange(x, y, jump):
  while x < y:
    yield float(x)
    #x += decimal.Decimal(jump)
    x+=jump

def run():
    logger.info('BitMEX Market Maker Version: %s\n' % constants.VERSION)

    om = OrderManager()
    # Try/except just keeps ctrl-c from printing an ugly stacktrace
    try:
        om.init()
        if settings.IS_BACKTESTING:
            om.run_backtesting()
        else:
            om.run_loop()
    except (KeyboardInterrupt, SystemExit):
        sys.exit()

def findBestParameterForRBreaker():
    logger.info("start to find the best parameter for R breaker")
    bestfinalBenifit = -1000.0
    bestf1 = 0.3
    bestf2 = 0.01
    bestf3 = 0.2
    om = OrderManager()
    
    try:
        om.init()
        for f1 in drange(0.20,0.50,0.02):
            for f2 in drange(0, 0.20, 0.02):
                for f3 in drange(0.10,0.40,0.02):
                    om.f1 = f1
                    om.f2 = f2
                    om.f3 = f3
                    om.run_backtesting()
                    thistimebenifit = om.finalUSDBenifit
                    if(thistimebenifit > bestfinalBenifit ):
                        bestfinalBenifit = thistimebenifit
                        bestf1 = f1
                        bestf2 = f2
                        bestf3 = f3       
    except (KeyboardInterrupt, SystemExit):
        sys.exit()
    print("best parameter found. f1 = %.2f, f2 = %.2f, f3 = %.2f, benifit = %.2f%%" % (bestf1, bestf2, bestf3, bestfinalBenifit))    


