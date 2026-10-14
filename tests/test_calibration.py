import copy
import json
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch
import pandas as pd
from test_chart_analysis import NOW, NEWS, pattern_asset, retest_frame
from swing_zones import resistance_zones
from scan_state import rotate_candidates, finish_state, cadence, persist_state
from replay_charts import replay
from trade_filters import classify_setup


class ZoneTests(unittest.TestCase):
    def frame(self, highs):
        return pd.DataFrame({'Open':[h-.5 for h in highs], 'High':highs, 'Low':[h-1 for h in highs], 'Close':[h-.4 for h in highs]},
                            index=pd.date_range(end=NOW,periods=len(highs),freq='5min'))

    def test_rising_candle_highs_are_not_resistance(self):
        self.assertEqual(resistance_zones(self.frame([100,101,102,103,104,105]),99),[])

    def test_pivot_requires_two_completed_confirmation_bars(self):
        f=self.frame([100,101,105,102,101])
        self.assertEqual(resistance_zones(f.iloc[:4],99),[])
        z=resistance_zones(f,99)
        self.assertEqual(len(z),1)
        self.assertEqual(z[0]['lower'],105)
        self.assertEqual(z[0]['confirmed_at'],str(f.index[-1]))

    def test_cluster_nearby_swings_and_preserve_separate_zone(self):
        f=self.frame([100,101,105,101,100,101,105.1,101,100,101,110,101,100])
        z=resistance_zones(f,99)
        self.assertEqual(len(z),2)
        self.assertEqual(z[0]['touches'],2)
        self.assertEqual(z[1]['lower'],110)

    def test_replay_never_supplies_future_bars(self):
        f=retest_frame();bars=[[t.isoformat()]+list(row) for t,row in f.iterrows()]
        from chart_analysis import analyse_chart
        def checked(frame,kind,now,**kwargs):
            self.assertEqual(frame.index[-1]+pd.Timedelta(minutes=5),now)
            self.assertTrue((frame.index+pd.Timedelta(minutes=5)<=now).all())
            return analyse_chart(frame,kind,now,**kwargs)
        with patch('replay_charts.analyse_chart',side_effect=checked):
            r=replay([{'symbol':'T','asset_type':'crypto','bars':bars}],bars_per_chart=4,max_seconds=20)
        self.assertEqual(r['decision_windows'],4)
        self.assertEqual(r['status'],'COMPLETE')
        self.assertIn('not actionable trades',r['notice'])


class RotationTests(unittest.TestCase):
    def test_rotation_reaches_beyond_ranked_shortlist_across_restarts(self):
        ranked=[{'ticker':f'S{i}','asset_type':'stock'} for i in range(200)]
        state={'entries':{}};seen=set()
        for n in range(9):
            now=NOW+timedelta(minutes=15*n)
            selected,h=rotate_candidates(copy.deepcopy(ranked),state,now,'stock',60,20)
            self.assertEqual(len(selected),60)
            self.assertEqual([x['ticker'] for x in selected[:40]],[f'S{i}' for i in range(40)])
            seen.update(x['ticker'] for x in selected)
            for x in selected:x['history_status']='OK'
            state=json.loads(json.dumps(finish_state(state,selected,now)))
        self.assertEqual(len(seen),200)

    def test_failure_rotates_but_closed_session_does_not_count(self):
        state={'entries':{}}
        ranked=[{'ticker':str(i),'asset_type':'stock'} for i in range(6)]
        selected,_=rotate_candidates(ranked,state,NOW,'stock',3,1)
        for x in selected:x['history_status']='FAILED'
        finish_state(state,selected,NOW)
        self.assertNotIn('last_success',state['entries']['stock:2'])
        next_,_=rotate_candidates(ranked,state,NOW+timedelta(minutes=15),'stock',3,1)
        self.assertEqual(next_[-1]['ticker'],'3')
        next_[-1]['history_status']='NOT_REQUESTED'
        finish_state(state,next_,NOW)
        self.assertNotIn('last_attempt',state['entries']['stock:3'])

    def test_state_failure_cannot_overwrite_existing_state(self):
        with patch('scan_state.git') as git:
            r=persist_state({'entries':{}},{'status':'UNAVAILABLE'},None)
        git.assert_not_called();self.assertEqual(r['persistence'],'SKIPPED_TO_PROTECT_EXISTING_STATE')

    def test_cadence_reports_actual_delay(self):
        r=cadence({'last_scan':(NOW-timedelta(minutes=31)).isoformat()},NOW)
        self.assertEqual(r['actual_gap_minutes'],31)
        self.assertEqual(r['status'],'DELAYED')


class DiagnosticTests(unittest.TestCase):
    def test_stop_and_both_reward_failures_are_separate(self):
        a=pattern_asset();p=a['chart_analysis']['setups'][0]
        p.update(support=99.95,resistance_1=100.1,resistance_2=100.2)
        q=classify_setup(a,NEWS,NOW)['assessments'][1]
        self.assertIn('Stop width is below 0.2%.',q['reasons'])
        self.assertIn('TP1 reward/risk is below 1.5R.',q['reasons'])
        self.assertIn('TP2 reward/risk is below 2R.',q['reasons'])
        self.assertIn('rr1',q['metrics'])

class IntegrationTests(unittest.TestCase):
    def test_selector_rotates_outside_top_150(self):
        from discovery import select_stock_candidates
        from test_selection import quote
        state={'entries':{}};seen=set()
        for n in range(8):
            now=NOW+timedelta(minutes=15*n)
            quotes=[{**quote(f'S{i}'),'regularMarketTime':now.timestamp()} for i in range(200)]
            selected,h=select_stock_candidates(quotes,{x['symbol']:x['symbol'] for x in quotes},{},True,now,rotation_state=state)
            seen.update(x['ticker'] for x in selected)
            finish_state(state,[{**x,'asset_type':'stock','history_status':'OK'} for x in selected],now)
            self.assertEqual(h['shortlisted_candidates'],150)
        self.assertEqual(len(seen),200)

    def test_persistence_roundtrip_uses_a_separate_branch(self):
        import subprocess,os
        from scan_state import load_state
        with tempfile.TemporaryDirectory() as tmp:
            remote=tmp+'/remote.git';work=tmp+'/work'
            subprocess.run(['git','init','--bare',remote],capture_output=True,check=True)
            subprocess.run(['git','init',work],capture_output=True,check=True)
            subprocess.run(['git','-C',work,'remote','add','origin',remote],capture_output=True,check=True)
            before=os.getcwd()
            try:
                os.chdir(work)
                state,health,parent=load_state()
                self.assertEqual(health['status'],'INITIALIZED')
                state['entries']['stock:T']={'first_seen':NOW.isoformat(),'last_seen':NOW.isoformat(),'last_attempt':NOW.isoformat()}
                self.assertEqual(persist_state(state,health,parent)['persistence'],'PERSISTED')
                loaded,health,parent=load_state()
                self.assertEqual(loaded,state)
                self.assertEqual(health['status'],'LOADED')
            finally:os.chdir(before)
