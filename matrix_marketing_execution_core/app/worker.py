"""Run a separate durable simulation worker: python -m app.worker [--once]."""
import sys
import time
from . import main,execution

def run():
    main.startup()
    while True:
        count=execution.run_once(main)
        if '--once' in sys.argv:return
        if not count:time.sleep(1)

if __name__=='__main__':run()
