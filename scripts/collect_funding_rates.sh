#!/usr/bin/env bash
# Collect funding rates from all 4 venues and store to MongoDB
# Run hourly via OpenClaw cron

export MONGO_URI='mongodb://admin:admin@localhost:27017/quants_lab?authSource=admin'

cd /home/maltese/.openclaw/workspace/builds/hummingbot-lab

/home/maltese/miniconda3/bin/conda run -n quants-lab python -c "
import asyncio, sys, os, pymongo
from datetime import datetime, timezone
sys.path.insert(0, '.')
os.chdir('.')
from core.data_sources.funding_rate_collector import FundingRateCollector

collector = FundingRateCollector()
df = asyncio.run(collector.collect())

client = pymongo.MongoClient(os.environ['MONGO_URI'])
col = client['quants_lab']['funding_rates']
col.create_index([('timestamp', -1), ('venue', 1), ('trading_pair', 1)])

records = df.to_dict('records')
ts = datetime.now(timezone.utc)
for r in records:
    r['timestamp'] = ts
    r['funding_rate_1h'] = float(r['funding_rate_1h'])
    r['mark_price'] = float(r.get('mark_price', 0) or 0)

col.insert_many(records)
total = col.count_documents({})
print(f'[{ts.isoformat()}] Stored {len(records)} records. Total in DB: {total}')
" 2>&1 | grep -v "Unclosed\|client_session\|connector\|aiohttp\|asyncio"
