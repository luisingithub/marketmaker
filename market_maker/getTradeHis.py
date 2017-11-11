"""BitMEX Histroy datas getting"""
from __future__ import absolute_import
import requests
import time
import datetime
import json
import base64
import uuid
import logging
import sys
from time import sleep
from market_maker import bitmex
from market_maker.settings import settings
from market_maker.utils import log, constants, errors

class GetHisTradeDatas:
    def __init__(self):
        if len(sys.argv) > 1:
            self.symbol = sys.argv[1]
        else:
            self.symbol = settings.SYMBOL
        self.bitmex = bitmex.BitMEX(base_url=settings.REAL_BASE_URL, symbol=self.symbol, login=settings.REAL_LOGIN,
                                    password=settings.REAL_PASSWORD, otpToken=settings.OTPTOKEN, apiKey=settings.REAL_API_KEY,
                                    apiSecret=settings.REAL_API_SECRET, orderIDPrefix=settings.REAL_ORDERID_PREFIX, shouldWSAuth=False)
        self.period = settings.BACKTEST_PERIOD
        self.number_per_day = 1440 // self.period
        
    def createFile(self,datafilename):
        self.recordFile = open(datafilename,"w")
        
    def writeLineintoFile(self,index):
        self.recordFile.write(self.quote[index]["timestamp"])
        self.recordFile.write(" ")
        self.recordFile.write(str(self.quote[index]["bidSize"]))
        self.recordFile.write(" ")
        self.recordFile.write(str(self.quote[index]["bidPrice"]))
        self.recordFile.write(" ")
        self.recordFile.write(str(self.quote[index]["askPrice"]))
        self.recordFile.write(" ")
        self.recordFile.write(str(self.quote[index]["askSize"]))
        self.recordFile.write(" ")
        self.recordFile.write(str(self.tradeBucket[0]["close"]))
        self.recordFile.write("\n")
        
    def closeFile(self):
        self.recordFile.close()   
        
    def run_loop(self):
        dateindex = settings.START_DATE
        while True:
            sleep(1)
            self.quote = self.bitmex.quoteBucketed(self.symbol, self.period, dateindex)
            self.tradeBucket = self.bitmex.tradeBucketed(self.symbol, 1440, dateindex)
            dateindex = getNextDay(dateindex)
            for i in range(0,self.number_per_day):
                self.writeLineintoFile(i) 
            print(dateindex)           
            if is_datefinished(dateindex,settings.END_DATE):
                print("data recording finish!")
                break   
            
    def run_loop_back(self):
        dateindex = settings.END_DATE
        while True:
            sleep(1)
            self.quote = self.bitmex.quoteBucketed(self.symbol, self.period, dateindex)
            self.tradeBucket = self.bitmex.tradeBucketed(self.symbol, 1440, dateindex)
            dateindex = getYesterday(dateindex)
            print(dateindex)
            for i in range(self.number_per_day-1,-1,-1):
                #print("i = %d" %i)
                self.writeLineintoFile(i)               
            if is_datefinished(dateindex,settings.START_DATE):
                print("data recording finish!")
                break   
        
def run():
    DR = GetHisTradeDatas()
    DR.createFile(settings.DATARECORDFILE)
    DR.run_loop()
    #DR.run_loop_back()
    DR.closeFile()
    
def is_datefinished(currentdate = "2017-01-01", enddate = "2017-08-31"):
    if currentdate == enddate:
        return True
    else:
        return False
        
def getNextDay(currentdate = "2017-01-01"):
    today = datetime.date(fetchYearFromTime(currentdate),fetchMonthFromTime(currentdate),fetchDayFromTime(currentdate))    
    tomorrow = today + datetime.timedelta(days=1)
    strtomorrow = tomorrow.strftime("%Y-%m-%d")
    #print(strtomorrow)
    return strtomorrow

def getYesterday(currentdate = "2017-09-01"):
    today = datetime.date(fetchYearFromTime(currentdate),fetchMonthFromTime(currentdate),fetchDayFromTime(currentdate))    
    yesterday = today - datetime.timedelta(days=1)
    stryesterday = yesterday.strftime("%Y-%m-%d")
    #print(strtomorrow)
    return stryesterday

def getXDaysBefore(currentdate = "2017-09-01", x = 1):
    today = datetime.date(fetchYearFromTime(currentdate),fetchMonthFromTime(currentdate),fetchDayFromTime(currentdate))    
    xdaysbefore = today - datetime.timedelta(days=x)
    strxdaysbefore = xdaysbefore.strftime("%Y-%m-%d")
    #print(strtomorrow)
    return strxdaysbefore
        
def fetchYearFromTime(currentdate = "2017-11-21"):
    yearstr = currentdate[:4]
    year = int(yearstr)
    return year
    
def fetchMonthFromTime(currentdate = "2017-11-21"):
    monthstr = currentdate[5:-3]
    month = int(monthstr)
    return month 
    
def fetchDayFromTime(currentdate = "2017-11-21"):
    daystr = currentdate[8:]
    day = int(daystr)
    return day 

def getClockFromLine(linestr = "2017-08-01T00:00:00.000Z 28547 2854.7 2859 28448 2854.7"):
    clock = linestr[11:18]
    return clock

def getDateFromLine(linestr = "2017-08-01T00:00:00.000Z 28547 2854.7 2859 28448 2854.7"):
    date = linestr[:10]
    return date

def getbidSizeFromLine(linestr = "2017-08-01T00:00:00.000Z 28547 2854.7 2859 28448 2854.7"):
    bidSizeStr = linestr.split()[1]
    if bidSizeStr == "None":
        return 0
    bidSize = int(bidSizeStr)
    return bidSize

def getbidPriceFromLine(linestr = "2017-08-01T00:00:00.000Z 28547 2854.7 2859 28448 2854.7"):
    bidPriceStr = linestr.split()[2]
    if bidPriceStr == "None":
       bidPriceStr = linestr.split()[3] 
    if bidPriceStr == "None":
        return -1
    bidPrice = float(bidPriceStr)
    return bidPrice

def getaskPriceFromLine(linestr = "2017-08-01T00:00:00.000Z 28547 2854.7 2859 28448 2854.7"):
    askPriceStr = linestr.split()[3]
    if askPriceStr == "None":
       askPriceStr = linestr.split()[2] 
    if askPriceStr == "None":
        return -1
    askPrice = float(askPriceStr)
    return askPrice

def getaskSizeFromLine(linestr = "2017-08-01T00:00:00.000Z 28547 2854.7 2859 28448 2854.7"):
    askSizeStr = linestr.split()[4]
    if askSizeStr == "None":
        return 0
    askSize = int(askSizeStr)
    return askSize

def getPrevClosePriceFromLine(linestr = "2017-08-01T00:00:00.000Z 28547 2854.7 2859 28448 2854.7"):
    preClosePriceStr = linestr.split()[5]
    preClosePrice = float(preClosePriceStr)
    return preClosePrice

def IsThereANone(linestr = "2017-08-01T00:00:00.000Z 28547 2854.7 2859 28448 2854.7"):
    for i in range(1, 5, 1):
        text = linestr.split()[i]
        if text == "None":
            return True
    return False
    