import io
import json
import threading
import unittest
from email.message import Message
from types import SimpleNamespace
from unittest.mock import Mock, patch

from idefix_web import Controller, build_parser, make_handler
from idefix_link import SignalStore, MavlinkLink, DemoLink, MAX_POINTS


def sample(seq=1, boot=12, result=1, **kwargs):
    values=dict(LR_BOOT=boot, LR_SEQ=seq, LR_TX=seq, LR_RX=seq,
                LR_OK=seq, LR_TO=0, LR_ERR=0, LR_RESULT=result,
                LR_RSSI=-82, LR_SNR=6, LR_RTT=280, LR_AGE=0, LR_END=1)
    values.update(kwargs)
    return values


def deliver(store, values, stamp=None):
    result=False
    for key, value in values.items():
        result=store.ingest(stamp if stamp is not None else values['LR_SEQ']*5000, key, value)
    return result


def msg(kind, source=(1,1), **kwargs):
    return SimpleNamespace(get_type=lambda:kind, get_srcSystem=lambda:source[0],
                           get_srcComponent=lambda:source[1], **kwargs)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.now=100.0
        self.store=SignalStore(lambda:self.now)
        self.store.experiment('start')

    def test_out_of_order_complete_snapshot_and_duplicate(self):
        values=sample()
        for key in reversed(list(values)):
            self.store.ingest(5000,key,values[key])
        self.assertEqual(self.store.snapshot()['latest']['rssi'],-82)
        deliver(self.store,values)
        self.assertEqual(self.store.snapshot()['sample_count'],1)

    def test_partial_snapshot_never_updates_metrics(self):
        for key,value in sample().items():
            if key!='LR_SNR': self.store.ingest(5,key,value)
        self.assertIsNone(self.store.snapshot()['latest'])

        self.now+=4
        self.store.ingest(5,'LR_SNR',6)
        self.assertIsNone(self.store.snapshot()['latest'])

    def test_service_restart_changes_browser_stream_identity(self):
        self.assertNotEqual(self.store.snapshot()['instance'],SignalStore().snapshot()['instance'])

    def test_timeout_creates_gap_and_last_measurement_ages(self):
        deliver(self.store,sample())
        self.now+=5
        deliver(self.store,sample(2,result=2,LR_RX=1,LR_OK=1,LR_TO=1,LR_RTT=-1,LR_AGE=5000))
        state=self.store.snapshot()
        self.assertEqual(state['latest']['rssi'],-82)
        self.assertEqual(state['latest']['signal_age_s'],5)
        self.assertIsNone(state['points'][-1]['rssi'])
        self.now+=13
        state=self.store.snapshot()
        self.assertTrue(state['stale'])
        self.assertEqual(state['latest']['signal_age_s'],18)

    def test_start_with_no_radio_response_is_unknown_not_zero(self):
        deliver(self.store,sample(result=2,LR_RX=0,LR_OK=0,LR_TO=1,LR_AGE=-1,LR_RTT=-1))
        self.assertIsNone(self.store.snapshot()['latest']['rssi'])

    def test_boot_change_clears_led_and_rejects_old_boot(self):
        deliver(self.store,sample(seq=20))
        self.store.led_mode='spin'
        self.now+=5
        deliver(self.store,sample(seq=1,boot=99))
        self.assertTrue(self.store.snapshot()['latest']['reboot'])
        self.assertIsNone(self.store.led_mode)
        deliver(self.store,sample(seq=21,boot=12))
        self.assertEqual(self.store.snapshot()['latest']['boot'],99)
        self.assertEqual(self.store.snapshot()['sample_count'],2)

    def test_missed_ethernet_sample_breaks_trace(self):
        deliver(self.store,sample(seq=1))
        self.now+=10
        deliver(self.store,sample(seq=3))
        self.assertTrue(self.store.snapshot()['points'][-1]['gap'])

    def test_old_sequence_does_not_override_newer_sample(self):
        deliver(self.store,sample(seq=4))
        deliver(self.store,sample(seq=3))
        self.assertEqual(self.store.snapshot()['latest']['sequence'],4)

    def test_stop_freezes_history_but_not_live_metrics(self):
        deliver(self.store,sample())
        self.now+=5
        self.store.experiment('stop')
        deliver(self.store,sample(seq=2,LR_RSSI=-99))
        self.now+=10
        s=self.store.snapshot()
        self.assertEqual(s['sample_count'],1)
        self.assertEqual(s['latest']['rssi'],-99)
        self.assertEqual(s['elapsed_s'],5)
        self.store.experiment('start')
        self.assertEqual(self.store.snapshot()['sample_count'],0)
        self.assertEqual(self.store.snapshot()['generation'],2)

    def test_invalid_data_and_version_are_ignored(self):
        for changes in ({'LR_OK':2},{'LR_END':99},{'LR_RESULT':99},{'LR_AGE':-2}, {'LR_SEQ':-1}):
            self.assertFalse(deliver(self.store,sample(**changes)))
        self.assertIsNone(self.store.snapshot()['latest'])

    def test_bounded_eight_hour_history_and_incremental_cursor(self):
        for seq in range(1,MAX_POINTS+5):
            self.now+=5
            deliver(self.store,sample(seq=seq))
        s=self.store.snapshot()
        self.assertEqual(len(s['points']),MAX_POINTS)
        cursor=s['points'][-2]['id']
        self.assertEqual(len(self.store.snapshot(cursor)['points']),1)


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.args=build_parser().parse_args(['--host','127.0.0.1','--timeout','.1','--retries','0'])
        self.store=SignalStore()
        self.store.experiment('start')
        self.link=MavlinkLink(self.args,self.store)
        self.link.connection=SimpleNamespace(mav=Mock())

    def test_telemetry_arrives_while_waiting_for_led_ack(self):
        def sent(*args):
            for name,value in sample().items():
                self.link.handle(msg('NAMED_VALUE_INT',name=name,value=value,time_boot_ms=5000))
            self.link.handle(msg('COMMAND_ACK',command=args[2],result=0,target_system=42,target_component=191))
        self.link.connection.mav.command_long_send.side_effect=sent
        status,result=self.link.send_led('spin')
        self.assertEqual(status,200)
        self.assertTrue(result['ok'])
        self.assertEqual(self.store.snapshot()['latest']['rssi'],-82)
        self.assertEqual(self.store.led_mode,'spin')

    def test_wrong_sender_wrong_command_or_wrong_target_cannot_ack(self):
        def sent(*args):
            for source,command,target in [((2,1),60002,42),((1,1),60001,42),((1,1),60002,43)]:
                self.link.handle(msg('COMMAND_ACK',source=source,command=command,result=0,target_system=target,target_component=191))
        self.link.connection.mav.command_long_send.side_effect=sent
        self.assertEqual(self.link.send_led('spin')[0],502)
        self.assertIsNone(self.store.led_mode)
        self.assertFalse(self.link.command_lock.locked())

    def test_wrong_source_telemetry_is_ignored(self):
        for name,value in sample().items():
            self.link.handle(msg('NAMED_VALUE_INT',source=(99,1),name=name,value=value,time_boot_ms=1))
        self.assertIsNone(self.store.latest)

    def test_busy_and_send_failure_release_lock(self):
        with self.link.command_lock:
            self.assertEqual(self.link.send_led('off')[0],409)
        self.link.connection.mav.command_long_send.side_effect=OSError('network down')
        self.assertEqual(self.link.send_led('off')[0],502)
        self.assertFalse(self.link.command_lock.locked())


class WebTests(unittest.TestCase):
    def setUp(self):
        self.controller=Controller(build_parser().parse_args(['--host','127.0.0.1','--demo']))

    def request(self, method, path, payload=None, token='test-token'):
        with patch('idefix_web.secrets.token_urlsafe',return_value='test-token'):
            cls=make_handler(self.controller)
        h=object.__new__(cls)
        h.path=path
        h.headers=Message()
        h.headers['X-Idefix-Token']=token
        h.headers['Content-Type']='application/json'
        body=json.dumps(payload).encode()
        h.headers['Content-Length']=str(len(body))
        h.rfile=io.BytesIO(body)
        h.reply=Mock()
        getattr(h,'do_'+method)()
        return h.reply.call_args.args

    def test_four_led_modes_and_invalid_requests(self):
        for mode in ('off','spin','blink','bounce'):
            self.assertEqual(self.controller.send(mode)[0],200)
        for mode in ('servo',[],{},None,'blink; reboot'):
            self.assertEqual(self.controller.send(mode)[0],400)

    def test_post_token_required_for_every_mutation(self):
        for path,body in [('/api/led',{'mode':'off'}),('/api/lora/experiment',{'action':'start'}),('/api/demo/scenario',{'scenario':'off'})]:
            self.assertEqual(self.request('POST',path,body,token='wrong')[0],403)

    def test_validation_gets_and_assets(self):
        self.assertEqual(self.request('POST','/api/led',[])[0],400)
        self.assertEqual(self.request('POST','/api/lora/experiment',{'action':'bad'})[0],400)
        self.assertEqual(self.request('GET','/api/led?mode=off')[0],404)
        self.assertEqual(self.request('GET','/api/lora/state?after=bad')[0],400)
        for path in ('/','/static/panel.css','/static/panel.js','/api/info','/api/lora/state'):
            self.assertEqual(self.request('GET',path)[0],200)
        self.assertFalse(self.controller.store.recording)

    def test_shared_experiment_state_and_demo_only_endpoint(self):
        self.assertEqual(self.request('POST','/api/lora/experiment',{'action':'start'})[0],200)
        self.assertTrue(self.request('GET','/api/lora/state')[1]['recording'])
        self.controller.args.demo=False
        self.assertEqual(self.request('POST','/api/demo/scenario',{'scenario':'off'})[0],404)

    def test_preview_is_labelled_and_simulates_missing_reply(self):
        self.controller.store.experiment('start')
        self.controller.link.tick()
        self.controller.store.scenario='off'
        self.controller.link.tick()
        data=self.controller.store.snapshot()
        self.assertTrue(data['demo'])
        self.assertEqual(data['latest']['result'],'timeout')
        self.assertIsNone(data['points'][-1]['snr'])


if __name__=='__main__':
    unittest.main()
