"""Persistent least-recently-attempted rotation and scan cadence diagnostics."""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from outcome_tracker import git, stamp

BRANCH = 'radar-state'
FILE = 'scan_state.json'


def load_state():
    try:
        exists = git(['ls-remote','--exit-code','--heads','origin',BRANCH], check=False)
        if exists.returncode == 2:
            return {'schema_version':1,'entries':{}}, {'status':'INITIALIZED'}, None
        if exists.returncode:
            raise RuntimeError('branch lookup failed')
        git(['fetch','origin',BRANCH])
        parent=git(['rev-parse','FETCH_HEAD']).stdout.strip()
        state=json.loads(git(['show',parent+':'+FILE]).stdout)
        if not isinstance(state.get('entries'),dict):
            raise ValueError('invalid rotation state')
        return state, {'status':'LOADED'}, parent
    except Exception as exc:
        # Continue discovery, but never overwrite unreadable persistent state.
        return {'entries':{}}, {'status':'UNAVAILABLE','error':type(exc).__name__}, None


def cadence(state, now):
    previous=stamp(state.get('last_scan'))
    gap=(now-previous).total_seconds()/60 if previous else None
    return {'target_minutes':15, 'previous_scan_at':state.get('last_scan'),
            'actual_gap_minutes':round(gap,1) if gap is not None else None,
            'status':'BASELINE_PENDING' if gap is None else 'CLOCK_ERROR' if gap < 0 else 'DELAYED' if gap > 20 else 'ON_CADENCE',
            'notice':'GitHub scheduling is best-effort; gaps are measured, not guaranteed.'}


def rotate_candidates(ranked, state, now, kind, limit, rotation_slots):
    """Retain priority slots, then draw from the ENTIRE eligible pool by age."""
    entries=state.setdefault('entries',{})
    def key(x):
        return kind+':'+(x.get('coin_id') or x.get('ticker') or x.get('symbol'))
    for x in ranked:
        entries.setdefault(key(x),{'first_seen':now.isoformat()})['last_seen']=now.isoformat()
    size=min(limit,len(ranked)); rotating=min(rotation_slots,size)
    priority=ranked[:size-rotating]
    used={key(x) for x in priority}
    pool=[x for x in ranked if key(x) not in used]
    # Stable sort preserves rank within the same age. Failed requests rotate too,
    # but last_success is tracked separately so attempts aren't called coverage.
    pool.sort(key=lambda x: (entries[key(x)].get('last_attempt') or '', entries[key(x)]['first_seen']))
    chosen=priority+pool[:rotating]
    for x in chosen:
        x['selection_lane']='PRIORITY' if key(x) in used else 'ROTATION'
    pending=[entries[key(x)] for x in ranked if key(x) not in {key(v) for v in chosen}]
    oldest=max(((now-(stamp(x.get('last_attempt')) or stamp(x['first_seen']))).total_seconds()/60 for x in pending),default=0)
    return chosen, {'priority_slots_used':len(priority),'rotation_slots_used':len(chosen)-len(priority),
                    'rotation_pool':len(ranked),'oldest_deferred_minutes':round(oldest,1)}


def finish_state(state, items, now):
    entries=state.setdefault('entries',{})
    for x in items:
        k=x['asset_type']+':'+(x.get('coin_id') or x.get('ticker') or x.get('symbol'))
        if x.get('history_status') not in {'OK','FAILED','STALE'}:
            continue
        e=entries.setdefault(k,{'first_seen':now.isoformat(),'last_seen':now.isoformat()})
        e.update(last_attempt=now.isoformat(), last_result=x['history_status'])
        if x['history_status']=='OK':
            e['last_success']=now.isoformat()
    state['entries']={k:v for k,v in entries.items() if stamp(v.get('last_seen')) and stamp(v['last_seen']) >= now-timedelta(days=7)}
    state.update(schema_version=1,last_scan=now.isoformat())
    return state


def persist_state(state, health, parent):
    if health['status']=='UNAVAILABLE':
        return {**health,'persistence':'SKIPPED_TO_PROTECT_EXISTING_STATE'}
    try:
        text=json.dumps(state,allow_nan=False)+'\n'
        Path(FILE).write_text(text)
        blob=git(['hash-object','-w','--stdin'],input=text).stdout.strip()
        tree=git(['mktree'],input='100644 blob '+blob+'\t'+FILE+'\n').stdout.strip()
        git(['config','user.name','github-actions[bot]']);git(['config','user.email','41898282+github-actions[bot]@users.noreply.github.com'])
        commit=git(['commit-tree',tree]+(['-p',parent] if parent else [])+['-m','Persist radar rotation and cadence']).stdout.strip()
        git(['push','origin',commit+':refs/heads/'+BRANCH])
        return {**health,'persistence':'PERSISTED','tracked_candidates':len(state['entries'])}
    except Exception as exc:
        return {**health,'persistence':'FAILED','error':type(exc).__name__}
