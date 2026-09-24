"""Loopback integration: actual MAVLink bytes, real UDP and concurrent telemetry/ACK."""
import socket
import threading
import time
import unittest
from idefix_link import SignalStore, MavlinkLink
from idefix_mavlink import require_pymavlink
from idefix_web import build_parser
from test_idefix_web import sample


class UdpIntegrationTests(unittest.TestCase):
    def test_real_udp_led_ack_and_named_values_share_one_receiver(self):
        radio=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        radio.bind(('127.0.0.1',0))
        radio.settimeout(2)
        args=build_parser().parse_args(['--host','127.0.0.1','--bind','127.0.0.1',
            '--port',str(radio.getsockname()[1]),'--local-port','0','--timeout','1','--retries','0'])
        store=SignalStore();store.experiment('start')
        link=MavlinkLink(args,store)
        dialect=require_pymavlink().mavlink
        encoder=dialect.MAVLink(None,srcSystem=1,srcComponent=1)
        failures=[]
        def peer():
            try:
                data,address=radio.recvfrom(2048)
                received=encoder.parse_buffer(data)[0]
                self.assertEqual(received.get_type(),'COMMAND_LONG')
                self.assertEqual(received.command,60002)
                values=sample()
                for name in reversed(list(values)):
                    packet=dialect.MAVLink_named_value_int_message(5000,name.encode(),values[name])
                    radio.sendto(packet.pack(encoder),address)
                    encoder.seq=(encoder.seq+1)%256
                ack=dialect.MAVLink_command_ack_message(60002,0,0,0,42,191)
                radio.sendto(ack.pack(encoder),address)
            except Exception as exc:
                failures.append(exc)
        worker=threading.Thread(target=peer)
        try:
            link.start();worker.start()
            self.assertEqual(link.send_led('spin')[0],200)
            worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(failures,[])
            self.assertEqual(store.snapshot()['latest']['rssi'],-82)
            self.assertEqual(store.snapshot()['sample_count'],1)
        finally:
            link.close();radio.close();worker.join(3)


if __name__=='__main__':unittest.main()
