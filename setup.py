#!/usr/bin/env python
from setuptools import setup, find_packages
from os.path import dirname, join, isfile
from shutil import copyfile

here = dirname(__file__)

print("I have hacked successfullly\n")

setup(name='bitmex-market-maker',
      version='0.3',
      description='Market making bot for BitMEX API',
      long_description=open(join(here, 'README.md')).read(),
      author='Cheng Lu',
      author_email='chengluchn@163.com',
      url='',
      install_requires=[
          'requests',
          'websocket-client',
          'future'
      ]
      )
print("I have hacked successfullly step 2\n")
	  
if not isfile('settings.py'):
  copyfile(join('market_maker', '_settings_base.py'), 'settings.py')
print("\n**** \nImportant!!!\nEdit settings.py before starting the bot.\n****")

