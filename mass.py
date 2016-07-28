#!/usr/bin/env python3

import json
import time
import struct
import atexit
import asyncio
import bluetooth
import threading
import websockets
import http.server
import socketserver
from constants import *

boards = {}

class BalanceBoard:
  def __init__(self, address):
    self.address = address
    self.status = "Disconnected"
    self.calibration_data = [0]*CALIBRATION_LENGTH
    self.calibration_mask = [1]*CALIBRATION_LENGTH
    self.sensor_data = [0]*8
    self.mass = [0]*4
    self.total_mass = 0
    self.recv_thread = threading.Thread(target=self.run)
    try:
      self.receivesocket = bluetooth.BluetoothSocket(bluetooth.L2CAP)
      self.controlsocket = bluetooth.BluetoothSocket(bluetooth.L2CAP)
    except ValueError:
      raise Exception("Error: Bluetooth not found")
    self.receivesocket.connect((address, 0x13))
    self.controlsocket.connect((address, 0x11))
    if self.receivesocket and self.controlsocket:
      self.status = "Connected"
      print("Successfully connected to board {}".format(self.address))
      self.recv_thread.start()
      self.read(CALIBRATION_ADDR, CALIBRATION_LENGTH)
      self.set_report_mode(True, REPORT_CB8E)

  def send(self, data):
    print("Sending data: {}".format(":".join("{:02x}".format(c) for c in data)))
    self.controlsocket.send(data)

  def read(self, location, length):
    data = OUTPUT + READ + location + struct.pack(">H", length)
    self.send(data)

  def write(self, location, contents):
    length = len(contents)
    data = OUTPUT + WRITE + location + struct.pack("B", length) + contents
    self.send(data)

  def set_report_mode(self, continuous, mode):
    cont = b'\x04' if continuous else b'\x00'
    data = OUTPUT + SET_REPORT_MODE + cont + mode
    self.send(data)

  def set_light(self, state):
    cont = b'\x10' if state else b'\x00'
    data = OUTPUT + LIGHT + cont
    self.send(data)

  def run(self):
    while self.status == "Connected":
      data = self.receivesocket.recv(25)
      packet_type = data[1]
      if packet_type == STATUS[0]:
        print("Buttons: {0:b}".format(data[3]))
        print("Flags: {0:b}".format(data[4]))
        print("Battery: {}".format(data[6:8]))
        self.set_report_mode(True, REPORT_CB8E)
      elif packet_type == REPORT_CB[0]:
        print("Buttons: {0:b}".format(data[3]))
      elif packet_type == READ_RTN[0]:
        length = (data[4] >> 4) + 1
        addr = struct.unpack(">H", data[5:7])[0]
        start_LSB = struct.unpack(">H", CALIBRATION_ADDR[-2:])[0]
        if start_LSB <= addr < (start_LSB + CALIBRATION_LENGTH):
          offset = addr - start_LSB
          for i in range(length):
            self.calibration_data[offset+i] = data[7+i]
            self.calibration_mask[offset+i] = 0
          print(self.calibration_mask)
      elif packet_type == REPORT_CB8E[0]:
        self.sensor_data = struct.unpack(">HHHH", data[4:12])
        if not any(self.calibration_mask):
          self.calculate_mass()
      else:
        print("Unknown packet type: {}".format(packet_type))

  def calculate_mass(self):
    for i, point in enumerate(self.sensor_data):
      zero = struct.unpack(">H", bytes(self.calibration_data[i*2:i*2+2]))[0]
      half = struct.unpack(">H", bytes(self.calibration_data[i*2+8:i*2+10]))[0]
      full = struct.unpack(">H", bytes(self.calibration_data[i*2+16:i*2+18]))[0]
      if point < zero:
        self.mass[i] = 0
      elif zero <= point < half:
        self.mass[i] = (point - zero) * (17 / (half - zero))
      else:
        self.mass[i] = ((point - half) * (17 / (full - half))) + 17
    self.total_mass = sum(self.mass)

  def disconnect(self):
    self.status == "Disconnected"
    self.recv_thread.join()
    try:
      self.receivesocket.close()
    except:
      print("Could not close receive socket")
    try:
      self.controlsocket.close()
    except:
      print("Could not close control socket")
    print("Disconnected {}".format(self.address))

def discovery():
  while True:
    devices = bluetooth.discover_devices(duration=1, lookup_names=True)
    for dev in devices:
      if dev[1] == "Nintendo RVL-WBC-01" and not(dev[0] in boards.keys()) and dev[0]:
        print("Discovered new board: {}".format(dev[0]))
        new = BalanceBoard(dev[0])
        boards[dev[0]] = new

def cleanup():
  for i in boards.values():
    i.disconnect()

def http_server():
  Handler = http.server.SimpleHTTPRequestHandler
  httpd = socketserver.TCPServer(("", 80), Handler)
  print("Serving at port 80")
  httpd.serve_forever()

async def mass_server(websocket, path):
  while True:
    sum = 0
    for i in boards.values():
      sum += i.total_mass
    board_masses = []
    keys = list(boards.keys())
    keys.sort()
    for i in keys:
      board_masses.append({"addr": i, "mass": boards[i].total_mass})
    data = {
      "total": sum,
      "boards": board_masses,
    }
    data = json.dumps(data)
    await websocket.send(data)
    await asyncio.sleep(0.1)

def __main__():
  atexit.register(cleanup)
  discover_thread = threading.Thread(target=discovery)
  discover_thread.start()

  http_thread = threading.Thread(target=http_server)
  http_thread.start()

  start_server = websockets.serve(mass_server, '0.0.0.0', 8080)
  asyncio.get_event_loop().run_until_complete(start_server)
  asyncio.get_event_loop().run_forever()

__main__()
