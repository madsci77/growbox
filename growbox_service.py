import time
import threading
import requests
import subprocess
import hid
import struct
import os
import json
from flask import Flask, request, jsonify, send_file
import sqlite3
import datetime

app = Flask(__name__)

CONFIG_FILE = "config.json"

# Scale config
VENDOR_ID = 0x0922
PRODUCT_ID = 0x8007
EMPTY_WEIGHT = 4.6  # Pounds
FULL_WEIGHT = 8.9   # Pounds

# Default configuration parameters
default_config = {
    "heater_on_temp": 20.0,      # Turn heater on if below 20°C
    "heater_off_temp": 22.0,     # Turn heater off if above 22°C

    "humidifier_interval": 60,       # Check humidity every 60 seconds
    "humidifier_threshold": 85.0,    # If humidity < 85%, turn on humidifier
    "humidifier_on_time": 10,        # Turn on humidifier for 10 seconds
    "humidifier_dead_time": 120,     # 2 minutes after humidifier off before camera is allowed

    "lights_on_time": "08:00",  # Turn lights on at 08:00
    "lights_off_time": "20:00", # Turn lights off at 20:00

    "fan_on_duration": 30,      # Fan on for 30 seconds
    "fan_off_duration": 300,    # Fan off for 5 minutes

    # Camera settings
    "camera_interval": 300,      # 5 minutes in seconds
    "camera_resolution": "3264x2448",
    "camera_save_path": "/home/argent/growbox/camera/snapshots"
}

config = dict(default_config)  # Will be overridden by load_config if config file exists

# Load config from disk if available
def load_config():
    global config
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                loaded = json.load(f)
                # Validate that all keys are present and of correct type if needed.
                # For now, we trust the data or just merge defaults:
                for k, v in default_config.items():
                    if k not in loaded:
                        loaded[k] = v
                config = loaded
        except Exception as e:
            print(f"Error loading config file: {e}. Using defaults.")
            config = dict(default_config)
    else:
        # No config file, use defaults
        config = dict(default_config)

# Save config to disk
def save_config():
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        print(f"Error saving config: {e}")

# Call load_config at startup
load_config()

latest_sensors = {
    "temperature": None,
    "humidity": None,
    "timestamp": None,
}

system_status = {
    "reservoir": None,
}

# Relay numbers
HEATER_RELAY = 1
HUMIDIFIER_RELAY = 2
LIGHTS_RELAY = 3
FAN_RELAY = 4

# Runtime state variables
heater_state = False
humidifier_state = False
fan_state = False
lights_state = False

last_humidifier_on = None
last_humidifier_off = None
last_humidifier_check = 0

last_fan_toggle = time.time()
fan_currently_on = False

last_camera_capture = 0

def parse_time(tstr):
    h, m = tstr.split(':')
    return int(h), int(m)

def current_time_of_day():
    now = datetime.datetime.now()
    return now.hour, now.minute

def time_in_minutes(hour, minute):
    return hour * 60 + minute

def is_light_on_time():
    on_h, on_m = parse_time(config["lights_on_time"])
    off_h, off_m = parse_time(config["lights_off_time"])
    now_h, now_m = current_time_of_day()

    on_min = time_in_minutes(on_h, on_m)
    off_min = time_in_minutes(off_h, off_m)
    now_min = time_in_minutes(now_h, now_m)

    return on_min <= now_min < off_min

def set_relay(relay_number, state):
    url = f"http://localhost:5001/relay/{relay_number}"
    try:
        r = requests.post(url, json={"state": state}, timeout=2)
        r.raise_for_status()
    except Exception as e:
        print(f"Error setting relay {relay_number}: {e}")

def get_sensors():
    url = "http://localhost:5001/sensors"
    try:
        r = requests.get(url, timeout=2)
        r.raise_for_status()
        data = r.json()
        return data
    except Exception as e:
        print(f"Error reading sensors: {e}")
        return None

def take_camera_snapshot():
    # Ensure directory exists
    os.makedirs(config["camera_save_path"], exist_ok=True)

    # Filenames
    latest_filename = os.path.join(config["camera_save_path"], "latest.jpg")
    timestamp_str = time.strftime("%Y%m%d_%H%M%S")
    timestamped_filename = os.path.join(config["camera_save_path"], f"{timestamp_str}.jpg")

    # Use fswebcam to capture an image
    resolution = config["camera_resolution"]
    cmd = ["fswebcam", "-r", resolution, "--no-banner", latest_filename]
    subprocess.run(cmd, check=True)

    # Copy latest.jpg to timestamped file
    subprocess.run(["cp", latest_filename, timestamped_filename], check=True)

def get_humidifier_tank_level():
    try:
        # Open the HID device
        with hid.Device(VENDOR_ID, PRODUCT_ID) as device:
            # Read data from the scale
            while True:
                data = device.read(6)
                if data:
                    weight = data[4] / 10.0
                    percentage = (weight - EMPTY_WEIGHT) / (FULL_WEIGHT - EMPTY_WEIGHT) * 100
                    percentage = max(0, min(100, percentage))
                    percentage = round(percentage)
                    return percentage
    except OSError as e:
        print(f"Error: {e}")

def can_run_humidifier_now(current_time):
    # Check if turning on the humidifier now would cause camera capture issues
    next_capture = last_camera_capture + config["camera_interval"]
    off_time = current_time + config["humidifier_on_time"]
    # We want next_capture >= off_time + dead_time
    if next_capture < off_time + config["humidifier_dead_time"]:
        return False
    return True

def control_loop():
    global heater_state, humidifier_state, lights_state, fan_state
    global last_humidifier_on, last_humidifier_off, last_humidifier_check
    global last_camera_capture, fan_currently_on, last_fan_toggle

    while True:
        current_time = time.time()

        # Get sensor data
        sdata = get_sensors()
        if sdata and sdata.get("temperature") is not None and sdata.get("humidity") is not None:
            latest_sensors["temperature"] = sdata["temperature"]
            latest_sensors["humidity"] = sdata["humidity"]
            latest_sensors["timestamp"] = sdata["timestamp"]

        temp = latest_sensors["temperature"]
        hum = latest_sensors["humidity"]

        # Heater control: bang-bang
        if temp is not None:
            if not heater_state and temp < config["heater_on_temp"]:
                set_relay(HEATER_RELAY, True)
                heater_state = True
            elif heater_state and temp > config["heater_off_temp"]:
                set_relay(HEATER_RELAY, False)
                heater_state = False

        # Lights control: based on schedule
        desired_lights = is_light_on_time()
        if desired_lights != lights_state:
            set_relay(LIGHTS_RELAY, desired_lights)
            lights_state = desired_lights

        # Humidifier control: Check every humidifier_interval
        if hum is not None:
            if (current_time - last_humidifier_check) >= config["humidifier_interval"]:
                if can_run_humidifier_now(current_time):
                    last_humidifier_check = current_time
                    # Check tank level
                    system_status["reservoir"] = get_humidifier_tank_level()
                # Check humidity threshold
                if hum < config["humidifier_threshold"]:
                    # Check if we can turn on now (dead time logic)
                    if can_run_humidifier_now(current_time):
                        set_relay(HUMIDIFIER_RELAY, True)
                        time.sleep(config["humidifier_on_time"])
                        set_relay(HUMIDIFIER_RELAY, False)
                        last_humidifier_on = current_time
                        last_humidifier_off = current_time + config["humidifier_on_time"]

        # Fan control: cycle on/off
        elapsed_fan = current_time - last_fan_toggle
        fan_cycle_time = config["fan_on_duration"] + config["fan_off_duration"]
        if fan_currently_on and elapsed_fan >= config["fan_on_duration"]:
            # turn off fan
            set_relay(FAN_RELAY, False)
            fan_currently_on = False
            last_fan_toggle = current_time
        elif (not fan_currently_on) and elapsed_fan >= config["fan_off_duration"]:
            # turn on fan
            set_relay(FAN_RELAY, True)
            fan_currently_on = True
            last_fan_toggle = current_time

        # Camera capture every camera_interval
        if (current_time - last_camera_capture) >= config["camera_interval"]:
            # Check humidifier dead time before capturing
            if last_humidifier_off is None or current_time >= (last_humidifier_off + config["humidifier_dead_time"]):
                # If lights are off, turn them on for the capture
                lights_were_off = False
                if not lights_state:
                    set_relay(LIGHTS_RELAY, True)
                    lights_were_off = True
                    time.sleep(2)

                try:
                    take_camera_snapshot()
                    last_camera_capture = time.time()
                except Exception as e:
                    print(f"Error taking camera snapshot: {e}")

                # Turn lights off if they were off
                if lights_were_off:
                    set_relay(LIGHTS_RELAY, False)
                    lights_state = False
            else:
                # Dead time not fulfilled, skip capture this round
                # Still update last_camera_capture so we don't immediately try again
                last_camera_capture = current_time

        time.sleep(1)

@app.route('/config', methods=['GET', 'POST'])
def handle_config():
    if request.method == 'GET':
        return jsonify(config)
    else:
        # Update config
        data = request.json
        for k, v in data.items():
            if k in config:
                config[k] = v
        # After updating, save to disk
        save_config()
        return jsonify({"status": "ok", "config": config})

@app.route('/status', methods=['GET'])
def get_status():
    return jsonify({"status": system_status})

@app.route('/history', methods=['GET'])
def get_history():
    # Return recent sensor data points from sensor_data.db
    conn = sqlite3.connect("sensor_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT timestamp, temperature, humidity FROM sensor_history ORDER BY id DESC LIMIT 1440")
    rows = cursor.fetchall()
    conn.close()

    result = []
    for r in rows:
        result.append({
            "timestamp": r[0],
            "temperature": r[1],
            "humidity": r[2]
        })
    return jsonify(result[::-1])  # reverse to chronological order

@app.route('/snapshot', methods=['GET'])
def get_latest_snapshot():
    latest_filename = os.path.join(config["camera_save_path"], "latest.jpg")
    if os.path.exists(latest_filename):
        return send_file(latest_filename, mimetype='image/jpeg')
    else:
        return jsonify({"error": "No snapshot available"}), 404

if __name__ == '__main__':

    set_relay(HEATER_RELAY, False)
    set_relay(HUMIDIFIER_RELAY, False)
    set_relay(LIGHTS_RELAY, False)
    set_relay(FAN_RELAY, False)

    # Start control loop in background
    control_thread = threading.Thread(target=control_loop, daemon=True)
    control_thread.start()

    # Start Flask app
    app.run(host='0.0.0.0', port=5002)
