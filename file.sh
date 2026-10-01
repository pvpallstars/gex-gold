python -c "
import sys; sys.path.insert(0,'scripts')
import gc_meta as M
for s in ['GCG6','GCJ6','GCM6','GCQ6','GCV6','GCZ6']:
    print(s, '| opt exp', M.option_expiry(s).strftime('%a %Y-%m-%d %H:%M'),
             '| fut LTD', M.futures_last_trade(s).strftime('%a %Y-%m-%d'))
"
