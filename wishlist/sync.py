from .store import now


def sync_once(store, remote, submit, progress=lambda _: None):
    bound = store.meta('account_hash')
    if bound and bound != remote.account_hash:
        raise ValueError('当前登录账号与此清单不一致，已停止同步')
    items, evidence = remote.read_all(progress)
    evidence['observed_at_utc'] = now()
    store.import_snapshot(items, evidence)
    store.set_meta('account_hash', remote.account_hash)
    if not submit:
        return {'message': '已读取完整收藏，未提交取消收藏', 'count': len(items), 'removed': 0}
    pending = store.pending_items()
    attempted, failed = [], 0
    for index, item in enumerate(pending, 1):
        # Store.stage committed the durable task before this worker can run.
        if item['state'] == 'queued' and not item['task_id']:
            raise ValueError('比价任务未保存，已停止取消收藏')
        progress(f'正在同步取消收藏 {index}/{len(pending)}')
        try:
            remote.unfavorite(item['id'])
            attempted.append(item['id'])
        except Exception as exc:
            store.fail(item['id'], str(exc))
            failed += 1
            break
    if pending:
        # Even ambiguous failures are reconciled by a complete authoritative read.
        items, evidence = remote.read_all(progress)
        evidence['observed_at_utc'] = now()
        store.import_snapshot(items, evidence)
        present = {str(x['id']) for x in items}
        for item_id in attempted:
            if item_id in present:
                store.fail(item_id, '闲鱼仍显示为收藏，操作保留待重试')
    remaining = len(store.pending_items())
    result = {'message': f'同步完成：确认取消 {len(pending) - remaining} 项，待重试 {remaining} 项',
              'count': len(items), 'removed': len(pending) - remaining, 'remaining': remaining, 'failed': failed}
    store.set_meta('last_sync', dict(result, observed_at_utc=now()))
    return result
