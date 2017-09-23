import plotly
from plotly.graph_objs import Scatter, Layout
import plotly.plotly as py
from plotly.graph_objs import *
import numpy as np

def run():
    index = 1
    x1 = []
    y1 = []
    y2 = []
    y3 = []
    everydayprofit = []
    graficdatafile = open("grafic.txt","r")
    while True:
        line = graficdatafile.readline()
        if not line:
            break
        x1.append(index)
        y1.append(float(line.split()[0]))
        y2.append(float(line.split()[1]))
        y3.append(float(line.split()[2])/10)
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
        for j in range(i+1,index-1,1):
            if y2[i] > y2[j] and y2[i] > 0.0:
                drawback = (y2[i] - y2[j]) / (y2[i]+100.0) * 100
                if drawback > max:
                    max = drawback
    print("最大回撤为-%.2f%%!" % max)
    print("夏普率为%.2f" % sharpratio)
    #print("收益日平均为%.2f" % profitmean)
   
    basebenifit = Scatter(x=x1,y=y1)
    yourbenifit = Scatter(x=x1,y=y2)
    dynamicposition = Scatter(x=x1,y=y3)
    data = Data([basebenifit, yourbenifit, dynamicposition])
    plotly.offline.plot({"data": data,"layout": Layout(title="benifit comparision")})
    