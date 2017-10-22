import plotly
from plotly.graph_objs import Scatter, Layout
import plotly.plotly as py
from plotly.graph_objs import *
import numpy as np

def run():
    index = 1
    x1 = []
    dailyPrice_y1 = []
    y2 = []
    y3 = []
    basebenifit_y4 = []
    nowUSD = []
    YourBenifitBiggerBase = 0
    BaseBenifitBiggerYour = 0
    maxPositiveDiff = 0.0
    maxNegativeDiff = 0.0
    #unrealisedbenifit_y5 = []
    #realisedbenifit_y6 = []
    movingaverage = []
    everydayprofit = []
    timeindex = []
    graficdatafile = open("grafic2.txt","r")
    maxloss = 100.0
    while True:
        line = graficdatafile.readline()
        if not line:
            break
        x1.append(index)
        dailyPrice_y1.append(float(line.split()[0]))
        yourBenifit = float(line.split()[1])
        baseBenifit = float(line.split()[4])
        if yourBenifit > baseBenifit:
            YourBenifitBiggerBase += 1
            diff = yourBenifit - baseBenifit
            if diff > maxPositiveDiff:
                maxPositiveDiff = diff
        elif yourBenifit < baseBenifit:
            BaseBenifitBiggerYour += 1
            diff = baseBenifit - yourBenifit
            if diff > maxNegativeDiff:
                maxNegativeDiff = diff   
        y2.append(yourBenifit)
        y3.append(float(line.split()[2])/1000)
        movingaverage.append(float(line.split()[3]))
        basebenifit_y4.append(baseBenifit)
        timeindex.append(line.split()[5])
        nowUSD.append(float(line.split()[6]))
        #unrealisedbenifit_y5.append(float(line.split()[5]))
        #realisedbenifit_y6.append(float(line.split()[6]))
        if index >=2:
            everydayprofit.append(y2[index-1] - y2[index-2])
        index += 1
        
    graficdatafile.close()
    profitstd = np.std(everydayprofit)
    #profitmean = np.mean(everydayprofit)
    E_Rp = y2[-1]
    sharpratio = (E_Rp - 3.25)/profitstd
    print("标准差为%.2f" % profitstd)
    # caculate the max drawbackprocent
    max = 0
    for i in range(0,index-2,1):
        if y2[i] < maxloss:
            maxloss = y2[i]
        for j in range(i+1,index-1,1):
            if y2[i] > y2[j] and y2[i] > 0:
                drawback = (y2[i] - y2[j]) / (y2[i]+100.0) * 100
                if drawback > max:
                    max = drawback
    winRatio = YourBenifitBiggerBase / (YourBenifitBiggerBase + BaseBenifitBiggerYour) * 100.0
    print("最大回撤为-%.2f%%!" % max)
    print("夏普率为%.2f" % sharpratio)
    print("最高亏损为%.2f%%!" % maxloss)
    print("策略盈利高于基准盈利次数为: %d, 基准高于策略次数为: %d, 盈利比例为  %.2f%%" % (YourBenifitBiggerBase, BaseBenifitBiggerYour, winRatio))
    print("最大正盈利为: %.2f%%, 最大负盈利为: -%.2f%%" % (maxPositiveDiff, maxNegativeDiff))
    
   #pricedaily = Scatter(x=x1,y=dailyPrice_y1, name = "Daily Close Price(USD)")
    #basebenifit = Scatter(x=x1,y=basebenifit_y4,name = "Base Benifit(%)")
    #yourbenifit = Scatter(x=x1,y=y2,name = "Your Benifit(%)")
    #dynamicposition = Scatter(x=x1,y=y3,name = "Dynamic Position(USD)")
    #movingaveragescatter = Scatter(x=x1,y=movingaverage,name = "Moving Average(USD)")
    
    pricedaily = Scatter(x=timeindex,y=dailyPrice_y1, name = "Daily Close Price(USD)")
    basebenifit = Scatter(x=timeindex,y=basebenifit_y4,name = "Base Benifit(%)")
    yourbenifit = Scatter(x=timeindex,y=y2,name = "Your Benifit(%)")
    dynamicposition = Scatter(x=timeindex,y=y3,name = "Dynamic Position(USD)")
    movingaveragescatter = Scatter(x=timeindex,y=movingaverage,name = "Moving Average(USD)")
    dailyUSDscatter = Scatter(x=timeindex,y=nowUSD,name = "your money(USD)")
    
    #unrealisedbenifitscatter = Scatter(x=x1,y=unrealisedbenifit_y5)
    #realisedbenifitscatter = Scatter(x=x1,y=realisedbenifit_y6)
    data = Data([basebenifit, yourbenifit, dynamicposition, movingaveragescatter,pricedaily, dailyUSDscatter])
    plotly.offline.plot({"data": data,"layout": Layout(title="benifit comparision")})
    