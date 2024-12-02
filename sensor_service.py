import serial
import json
import time
from flask import Flask, jsonify

app = Flask(__name__)

# Serial port configuration
SERIAL_PORT = '/dev/ttyACM0'  # Update for your setup
BAUD_RATE = 9600
ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)

# Store the latest sensor data
latest_data = {
    "temperature": None,
    "humidity": None,
    "timestamp": None
}

# Read from serial and update the latest data
def read_sensor_data():
    global latest_data
    try:
        line = ser.readline().decode('utf-8').strip()
        if line:
            # Example format: T:25.3,H:60.1
            parts = line.split(",")
            temp = float(parts[0].split(":")[1])
            hum = float(parts[1].split(":")[1])
            timestamp = time.time()

            # Update latest data
            latest_data = {
                "temperature": temp,
                "humidity": hum,
                "timestamp": timestamp
            }
    except Exception as e:
        print(f"Error reading sensor data: {e}")

@app.route('/sensors', methods=['GET'])
def get_sensor_data():
    # Serve the latest sensor data as JSON
    return jsonify(latest_data)

if __name__ == '__main__':
    # Run the Flask app
    import threading

    # Start the sensor data reading in a background thread
    def sensor_reader():
        while True:
            read_sensor_data()
            time.sleep(1)  # Adjust based on desired reading frequency

    threading.Thread(target=sensor_reader, daemon=True).start()

    # Start Flask server
    app.run(host='0.0.0.0', port=5000)
