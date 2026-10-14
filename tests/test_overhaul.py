import copy
import unittest
from datetime import timedelta
from unittest.mock import patch
import pandas as pd
from test_chart_analysis import NOW, NEWS, pattern_asset, retest_frame
from trade_filters import classify_setup
from risk_policy import position_size
from reliability import retry_read
from publish_alert import prepare_alert, material_change
from chart_analysis import analyse_chart
from delivery import deliver_snapshot
from outcome_tracker import update_outcomes
from performance import summarize


class RiskTests(unittest.TestCase):
    def test_default_and_cost_inclusive_sizing(self):
        a={'ticker':'T','capital_limit_eur':5000}
        p=position_size(a,100,1,NOW)
        self.assertEqual(p['risk_budget_eur'],15)
        self.assertLessEqual(p['planned_loss_eur'],15)
        self.assertGreater(p['cost_reserve_eur'],0)

    def test_above_default_requires_scoped_current_approval(self):
        a={'ticker':'T','risk_budget_eur':20}
        for approval in [None, {}, {'user_approval_reference':'user-message','symbol':'X','amount_eur':20,'expires_at':(NOW+timedelta(hours=1)).isoformat()},
                         {'user_approval_reference':'user-message','symbol':'T','amount_eur':20,'expires_at':NOW.isoformat()}]:
            with self.subTest(approval=approval), self.assertRaises(ValueError):
                position_size({**a,'risk_approval':approval},100,1,NOW)
        a['risk_approval']={'user_approval_reference':'user-message','symbol':'T','amount_eur':20,'expires_at':(NOW+timedelta(hours=1)).isoformat()}
        self.assertLessEqual(position_size(a,100,1,NOW)['planned_loss_eur'],20)
        a['risk_budget_eur']=21
        with self.assertRaises(ValueError):position_size(a,100,1,NOW)

    def test_invalid_inputs_fail_closed(self):
        for v in [float('nan'),float('inf'),True,-1,0,'15']:
            with self.subTest(v=v), self.assertRaises(ValueError):position_size({'risk_budget_eur':v},100,1,NOW)

    def test_plan_expiry_and_old_entry(self):
        a=pattern_asset();p=classify_setup(a,NEWS,NOW)['plan']
        self.assertEqual(p['expires_at'],(NOW+timedelta(minutes=5)).isoformat())
        a['observed_at']=(NOW-timedelta(minutes=10)).isoformat()
        self.assertIsNone(classify_setup(a,NEWS,NOW)['plan'])

    def test_short_never_assumes_invest_capability(self):
        a={**pattern_asset(),'direction':'SHORT'}
        self.assertIsNone(classify_setup(a,NEWS,NOW)['plan'])

    def test_developing_is_research_only(self):
        a=pattern_asset();a['chart_analysis']['setups'][0]['stage']='DEVELOPING'
        q=classify_setup(a,NEWS,NOW)
        self.assertEqual(q['status'],'TECHNICAL WATCH');self.assertIsNone(q['plan'])

    def test_new_patterns_require_higher_timeframe(self):
        a=pattern_asset();a['chart_analysis']['setups'][0]['pattern']='SESSION_VWAP_RECLAIM'
        self.assertIsNone(classify_setup(a,NEWS,NOW)['plan'])
        a['chart_analysis']['higher_timeframe']={'trend':'RISING','observed_at':NOW.isoformat()}
        self.assertEqual(classify_setup(a,NEWS,NOW)['status'],'ACTIONABLE NOW')


class ReliabilityTests(unittest.TestCase):
    def test_retry_empty_then_success(self):
        values=iter([[],[1]])
        with patch('reliability.time.sleep'):
            self.assertEqual(retry_read(lambda:next(values),valid=bool),([1],2))

    def test_retry_is_bounded(self):
        with patch('reliability.time.sleep'), self.assertRaises(ValueError):
            retry_read(lambda:[],valid=bool)

    def item(self):
        a=pattern_asset();a['catalyst']=NEWS;a['qualification']=classify_setup(a,NEWS,NOW);return a

    def test_stop_change_below_one_percent_is_material(self):
        a=self.item();b=copy.deepcopy(a);b['qualification']['plan']['stop_loss']-=.4
        self.assertTrue(material_change(a,b))

    def test_absence_is_unavailable_not_invalidation(self):
        prior=prepare_alert({'generated_at':NOW.isoformat(),'potential_signals':[self.item()]},{})
        p=prepare_alert({'generated_at':NOW.isoformat(),'potential_signals':[]},prior)
        self.assertEqual(p['invalidated_signals'],[])
        self.assertEqual(p['unavailable_signals'],['stock:TEST'])

    def test_published_observations_are_fresh_but_baseline_accumulates(self):
        a=self.item();prior=prepare_alert({'generated_at':NOW.isoformat(),'potential_signals':[a]}, {})
        b=copy.deepcopy(a);b['observed_at']=(NOW+timedelta(minutes=1)).isoformat()
        c=copy.deepcopy(b);c['ticker']='OTHER'
        fresh=prepare_alert({'generated_at':NOW.isoformat(),'potential_signals':[b,c]},prior)
        self.assertEqual(fresh['potential_signals'][0]['observed_at'],b['observed_at'])
        self.assertEqual(fresh['comparison_signals'][0]['observed_at'],a['observed_at'])

    def test_telegram_failed_send_does_not_commit_dedupe(self):
        state={};p={'generated_at':NOW.isoformat(),'potential_signals':[self.item()]}
        with patch('delivery.telegram',return_value='FAILED') as send:
            self.assertEqual(deliver_snapshot(p,state,'text'),'FAILED')
            self.assertNotIn('telegram',state);self.assertEqual(send.call_count,1)
        with patch('delivery.telegram',return_value='API_ACCEPTED'):
            deliver_snapshot(p,state,'text')
        with patch('delivery.telegram') as send:
            self.assertEqual(deliver_snapshot(p,state,'text'),'SUPPRESSED_NO_MATERIAL_CHANGE');send.assert_not_called()


class DetectionTests(unittest.TestCase):
    def frame(self,rows):
        return pd.DataFrame(rows,dtype=float,columns=['Open','High','Low','Close','Volume'],index=pd.date_range(end=NOW-timedelta(minutes=5),periods=len(rows),freq='5min'))

    def test_early_breakout_forming_then_confirmed(self):
        f=self.frame([[98,100,96,98.5,100]]*39+[[99,99.9,98.8,99.8,200]])
        p=next(x for x in analyse_chart(f,'crypto',NOW)['setups'] if x['pattern']=='EARLY_RANGE_BREAKOUT')
        self.assertEqual(p['stage'],'DEVELOPING')
        f.iloc[-1]=[99.8,100.5,99.7,100.3,200]
        p=next(x for x in analyse_chart(f,'crypto',NOW)['setups'] if x['pattern']=='EARLY_RANGE_BREAKOUT')
        self.assertEqual(p['stage'],'CONFIRMED')

    def test_support_bounce(self):
        rows=[[98,102,96,98,100]]*38+[[96.1,96.3,96,96.1,100],[96.1,96.7,96,96.6,200]]
        names=[x['pattern'] for x in analyse_chart(self.frame(rows),'crypto',NOW)['setups']]
        self.assertIn('SUPPORT_BOUNCE',names)

    def test_vwap_reclaim(self):
        rows=[[100,102,98,100,100]]*38+[[99.5,99.8,99,99.4,100],[99.5,100.5,99.4,100.3,200]]
        names=[x['pattern'] for x in analyse_chart(self.frame(rows),'crypto',NOW)['setups']]
        self.assertIn('SESSION_VWAP_RECLAIM',names)

    def test_momentum_continuation(self):
        rows=[[95,96,94,95,100]]*22
        rows += [[95+i*.4,95.4+i*.4,94.9+i*.4,95.3+i*.4,100] for i in range(13)]
        rows += [[99.9,100.2,99.7,100,100]]*4+[[100,100.5,99.9,100.4,200]]
        self.assertIn('MOMENTUM_CONTINUATION',[x['pattern'] for x in analyse_chart(self.frame(rows),'crypto',NOW)['setups']])

    def test_higher_timeframe_excludes_partial_groups(self):
        f=retest_frame();r=analyse_chart(f,'crypto',NOW)
        self.assertLessEqual(pd.Timestamp(r['higher_timeframe']['observed_at']),NOW)
        r=analyse_chart(f.iloc[:-1],'crypto',NOW)
        self.assertLessEqual(pd.Timestamp(r['higher_timeframe']['observed_at']),NOW-timedelta(minutes=15))


class PerformanceTests(unittest.TestCase):
    def test_watch_promotion_captures_plan_and_sampled_barriers(self):
        a=pattern_asset();a['qualification']={'status':'TECHNICAL WATCH','plan':None}
        def payload(at,item):return {'generated_at':at.isoformat(),'stocks':[item],'potential_signals':[item]}
        first=update_outcomes({},payload(NOW,a))
        at=NOW+timedelta(minutes=15);a['observed_at']=at.isoformat();a['chart_analysis']['setups'][0]['observed_at']=at.isoformat()
        a['qualification']=classify_setup(a,NEWS,at)
        second=update_outcomes(first,payload(at,a));r=next(iter(second['records'].values()))
        self.assertEqual(r['watch_to_actionable_seconds'],900)
        self.assertIsNotNone(r['actionable_plan'])
        at+=timedelta(minutes=15);a['price']=104;a['observed_at']=at.isoformat()
        third=update_outcomes(second,payload(at,a));r=next(iter(third['records'].values()))
        self.assertIn('tp1',r['sampled_plan_events']);self.assertFalse(r['executed_trade'])
        self.assertEqual(summarize(third)['sampled_tp1_observations'],1)

    def test_deferred_cohort_without_quote_is_unmeasured(self):
        p={'generated_at':NOW.isoformat(),'health':{'stocks':{'candidate_manifest':[{'symbol':'T','state':'DEFERRED'}]}}}
        result=update_outcomes({},p);r=next(iter(result['records'].values()))
        self.assertEqual(r['initial_status'],'DEFERRED');self.assertNotIn('baseline_price',r)
        self.assertEqual(summarize(result)['missed_move_proxy_count'],0)

class PublicationIntegrationTests(unittest.TestCase):
    def test_publisher_preserves_checkout_and_dedupes_across_runs(self):
        import tempfile, subprocess, os, json, contextlib, io
        from pathlib import Path
        from publish_alert import main
        with tempfile.TemporaryDirectory() as tmp:
            remote=tmp+'/remote.git';work=tmp+'/work'
            def git(*args):return subprocess.run(['git',*args],check=True,capture_output=True,text=True)
            git('init','--bare',remote);git('init',work)
            before=os.getcwd()
            try:
                os.chdir(work);git('config','user.name','Test');git('config','user.email','test@local')
                git('remote','add','origin',remote)
                Path('source.txt').write_text('preserve current source')
                git('add','.');git('commit','-m','source');git('branch','radar-alert-live');git('push','origin','radar-alert-live')
                head=git('rev-parse','HEAD').stdout
                a=pattern_asset();a['qualification']=classify_setup(a,NEWS,NOW)
                Path('latest_scan.json').write_text(json.dumps({'generated_at':NOW.isoformat(),'potential_signals':[a]}))
                with contextlib.redirect_stdout(io.StringIO()):main()
                self.assertEqual(git('rev-parse','HEAD').stdout,head)
                self.assertEqual(Path('source.txt').read_text(),'preserve current source')
                self.assertEqual(json.loads(Path('bridge_status.json').read_text())['status'],'GITHUB_PUBLISHED')
                with contextlib.redirect_stdout(io.StringIO()):main()
                self.assertEqual(json.loads(Path('bridge_status.json').read_text())['status'],'SUPPRESSED_NO_MATERIAL_CHANGE')
            finally:os.chdir(before)


class FinalSafetyTests(unittest.TestCase):
    def test_expired_during_scan_cannot_be_published_as_entry(self):
        from run_radar import expire_plans, build_potential_signals
        a=pattern_asset();a['qualification']=classify_setup(a,NEWS,NOW)
        expire_plans([a],NOW+timedelta(minutes=6))
        self.assertEqual(a['qualification']['status'],'EXPIRED')
        self.assertEqual(build_potential_signals([a],[]),[])

    def test_http_retries_transient_not_forbidden(self):
        import requests
        from reliability import http_get
        from unittest.mock import Mock
        forbidden=requests.Response();forbidden.status_code=403
        with patch('reliability.requests.get',return_value=forbidden) as get, patch('reliability.time.sleep'), self.assertRaises(requests.HTTPError):
            http_get('https://example.test',timeout=1)
        self.assertEqual(get.call_count,1)
        good=Mock();good.raise_for_status.return_value=None
        with patch('reliability.requests.get',side_effect=[requests.Timeout(),good]) as get, patch('reliability.time.sleep'):
            self.assertIs(http_get('https://example.test',timeout=1),good)
        self.assertEqual(get.call_count,2)


class TimestampSafetyTests(unittest.TestCase):
    def test_missing_naive_invalid_or_future_quote_fails_closed(self):
        for observed in [None, 'invalid', '2026-10-07T15:00:00', (NOW+timedelta(minutes=1)).isoformat()]:
            with self.subTest(observed=observed):
                a=pattern_asset();a['observed_at']=observed
                self.assertIsNone(classify_setup(a,NEWS,NOW)['plan'])
