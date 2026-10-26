"""Import an already verified local read-only snapshot; never sends writes."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wishlist.remote import FavoritesRemote
from wishlist.store import Store

p = argparse.ArgumentParser()
p.add_argument('--snapshot', type=Path, required=True)
p.add_argument('--state', type=Path, required=True)
p.add_argument('--data', type=Path, required=True)
args = p.parse_args()
store = Store(args.data / 'wishlist.sqlite3')
if store.snapshot()['items']:
    raise SystemExit('已有清单，初始化已跳过。请在页面读取收藏。')
snapshot = json.loads(args.snapshot.read_text(encoding='utf-8'))
remote = FavoritesRemote(args.state)
try:
    store.set_meta('account_hash', remote.account_hash)
    store.import_snapshot(snapshot['items'], snapshot['evidence'])
finally:
    remote.close()
print(json.dumps({'imported': len(store.snapshot()['items']), 'remote_writes': 0}))
