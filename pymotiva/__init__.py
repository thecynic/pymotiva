#!/sur/bin/env python3

__author__ = "Dima Zavin"
__copyright__ = "Copyright 2016, Dima Zavin"

import logging
import select
import socket
import threading
import time
import xml.etree.ElementTree as ET

_LOGGER = logging.getLogger(__name__)

class Error(Exception):
  pass


class InvalidTransponderResponse(Error):
  pass


class EmotivaNotifier(threading.Thread):
  def __init__(self):
    threading.Thread.__init__(self)

    self._devs = {}
    self._socks_by_port = {}
    self._socks_by_fileno = {}
    self._lock = threading.Lock()
    self._epoll = select.epoll()
    self.setDaemon(True)
    self.start()

  def register(self, ip, port, callback):
    with self._lock:
      if port not in self._socks_by_port:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('', port))
        sock.setblocking(0)
        self._socks_by_port[port] = sock
        self._socks_by_fileno[sock.fileno()] = sock
        self._epoll.register(sock.fileno(), select.POLLIN)
      if ip not in self._devs:
        self._devs[ip] = callback

  def run(self):
    print("Created")
    while True:
      events = self._epoll.poll(1)
      for fileno, event in events:
        if event & select.POLLIN:
          with self._lock:
            sock = self._socks_by_fileno[fileno]
          data, (ip, port) = sock.recvfrom(2048)
          print("Got data %s from %s:%d" % (data, ip, port))
          with self._lock:
            cb = self._devs[ip]
          cb(data)


class Emotiva(object):
  XML_HEADER = '<?xml version="1.0" encoding="utf-8"?>'.encode('utf-8') 
  DISCOVER_REQ_PORT = 7000
  DISCOVER_RESP_PORT = 7001
  NOTIFY_EVENTS = [
      'power', 'zone2_power', 'source', 'mode', 'volume', 'audio_input',
      'audio_bitstream', 'video_input', 'video_format'
  ] + ['input_%d' % d for d in range(1, 9)]
  __notifier = EmotivaNotifier()

  def __init__(self, ip, transp_xml):
    self._ip = ip
    self._name = 'Unknown'
    self._model = 'Unknown'
    self._proto_ver = None
    self._ctrl_port = None
    self._notify_port = None
    self._info_port = None
    self._setup_port_tcp = None
    self._ctrl_sock = None

    self.__parse_transponder(transp_xml)
    if not self._ctrl_port or not self._notify_port:
      raise InvalidTransponderResponse("Coulnd't find ctrl/notify ports")

  def connect(self):
    self._ctrl_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    self._ctrl_sock.bind(('', self._ctrl_port))
    self._ctrl_sock.settimeout(0.5)
    self.__notifier.register(self._ip, self._notify_port, self._notify_handler)
    self._subscribe_events(self.NOTIFY_EVENTS)

  def _send_request(self, req, ack=True):
    self._ctrl_sock.sendto(req, (self._ip, self._ctrl_port))

    while True:
      try:
        _resp_data, (ip, port) = self._ctrl_sock.recvfrom(2048)
        resp = self._parse_response(_resp_data)
        print("RESP: " + _resp_data.decode('utf-8'))
      except socket.timeout:
        break

  def _notify_handler(self, data):
    resp = self._parse_response(data)
    print(resp)

  def _subscribe_events(self, events):
    msg = self.format_request('emotivaSubscription',
                              [(ev, None) for ev in events])
    self._send_request(msg)

  def __parse_transponder(self, transp_xml):
    elem = transp_xml.find('name')
    if elem is not None: self._name = elem.text.strip()
    elem = transp_xml.find('model')
    if elem is not None: self._model = elem.text.strip()

    ctrl = transp_xml.find('control')
    elem = ctrl.find('version')
    if elem is not None: self._proto_ver = elem.text
    elem = ctrl.find('controlPort')
    if elem is not None: self._ctrl_port = int(elem.text)
    elem = ctrl.find('notifyPort')
    if elem is not None: self._notify_port = int(elem.text)
    elem = ctrl.find('infoPort')
    if elem is not None: self._info_port = int(elem.text)
    elem = ctrl.find('setupPortTCP')
    if elem is not None: self._setup_port_tcp = int(elem.text)

  @classmethod
  def discover(cls):
    resp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    resp_sock.bind(('', cls.DISCOVER_RESP_PORT))
    resp_sock.settimeout(0.5)
    
    req_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    req_sock.bind(('', 0))
    req_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    req = cls.format_request('emotivaPing', [])
    req_sock.sendto(req, ('<broadcast>', cls.DISCOVER_REQ_PORT))

    devices = []
    while True:
      try:
        _resp_data, (ip, port) = resp_sock.recvfrom(2048)
        resp = cls._parse_response(_resp_data)
        devices.append((ip, resp))
      except socket.timeout:
        break
    return devices
      
  @classmethod
  def _parse_response(cls, data):
    data_lines = data.decode('utf-8').split('\n')
    data_joined = ''.join([x.strip() for x in data_lines])
    root = ET.fromstring(data_joined)
    return root

  @classmethod
  def format_request(cls, pkt_type, req):
    """

    req is a list of 2-element tuples with first element being the command,
    and second being a dict of parameters. E.g.
    ('power_on', {'value': "0"})
    """
    output = cls.XML_HEADER
    builder = ET.TreeBuilder()
    builder.start(pkt_type)
    for cmd, params in req:
      builder.start(cmd, params) 
      builder.end(cmd)
    builder.end(pkt_type)
    pkt = builder.close()
    return output + ET.tostring(pkt)

foo = Emotiva.format_request('emotivaControl', [('volume', {'value': '1', 'ack': 'yes'})])
bar = Emotiva.discover()


# recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
# recv_sock.bind(('', 7001))
#  
# sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
# sock.bind(('', 0))
# sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
# sock.sendto(output, ('<broadcast>', 7000))
# 
# data, addr = recv_sock.recvfrom(2048)
# data_lines = data.decode('utf-8').split('\n')
# data_joined = ''.join([x.strip() for x in data_lines])
# root = ET.fromstring(data_joined)
