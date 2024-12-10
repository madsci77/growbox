from flask import Flask, request, jsonify
from pymodbus.client import ModbusSerialClient
import threading
import time
import sqlite3

app = Flask(__name__)

# Modbus configuration
client = ModbusSerialClient(
    port='/dev/ttyUSB0',  # Ensure this is the correct port
    baudrate=9600,
    bytesize=8,
    parity='N',
    stopbits=1,
    timeout=1
)

RELAY_SLAVE_ID = 1
SENSOR_SLAVE_ID = 2

# Global lock for Modbus operations
modbus_lock = threading.Lock()

# Global latest sensor data
latest_data = {
    "temperature": None,
    "humidity": None,
    "timestamp": None
}

# Database file
DB_FILE = "sensor_data.db"

def initialize_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sensor_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            temperature REAL,
            humidity REAL
        )
    """)
    conn.commit()
    conn.close()

def read_sensor_data():
    """
    Thread function to periodically read sensor data from the Modbus sensor.
    Runs every 1 second, updates 'latest_data'.
    """
    global latest_data
    while True:
        try:
            with modbus_lock:
                response = client.read_holding_registers(address=0, count=2, slave=SENSOR_SLAVE_ID)

            if not response.isError():
                raw_temp = response.registers[0]
                raw_hum = response.registers[1]

                temperature = raw_temp / 100.0
                humidity = raw_hum / 100.0
                timestamp = time.time()

                latest_data = {
                    "temperature": temperature,
                    "humidity": humidity,
                    "timestamp": timestamp
                }
        except Exception as e:
            print(f"Error reading sensor data: {e}")

        time.sleep(1)  # Poll every second

def log_to_db():
    """
    Thread function to log the current latest_data to the DB every 5 seconds.
    """
    while True:
        try:
            if latest_data["temperature"] is not None and latest_data["humidity"] is not None:
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO sensor_history (timestamp, temperature, humidity)
                    VALUES (?, ?, ?)
                """, (time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(latest_data["timestamp"])),
                      latest_data["temperature"],
                      latest_data["humidity"]))
                conn.commit()
                conn.close()
        except Exception as e:
            print(f"Error logging to database: {e}")

        time.sleep(5)  # Log every 5 seconds

@app.route('/relay/<int:relay_number>', methods=['POST'])
def set_relay(relay_number):
    """
    Set the state of a relay.
    Relay numbers are 1-indexed.
    Request body: {"state": true/false}
    """
    try:
        data = request.get_json()
        state = data.get('state', None)
        if state is None or not isinstance(state, bool):
            return jsonify({"error": "Invalid state. Must be true or false."}), 400

        coil_number = relay_number - 1

        with modbus_lock:
            response = client.write_coil(coil_number, state, slave=RELAY_SLAVE_ID)

        if response.isError():
            return jsonify({"error": f"Failed to set relay {relay_number} state."}), 500

        return jsonify({"relay": relay_number, "state": state})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/relay/<int:relay_number>', methods=['GET'])
def get_relay_status(relay_number):
    """
    Get the status of a relay.
    Relay numbers are 1-indexed.
    """
    try:
        coil_number = relay_number - 1

        with modbus_lock:
            response = client.read_coils(coil_number, count=1, slave=RELAY_SLAVE_ID)

        if response.isError():
            return jsonify({"error": f"Failed to read relay {relay_number} status."}), 500

        status = response.bits[0]
        return jsonify({"relay": relay_number, "state": status})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/relays', methods=['GET'])
def get_all_relays_status():
    """
    Get the status of all relays.
    """
    try:
        with modbus_lock:
            response = client.read_coils(0, count=8, slave=RELAY_SLAVE_ID)

        if response.isError():
            return jsonify({"error": "Failed to read relay states."}), 500

        statuses = [{"relay": i + 1, "state": response.bits[i]} for i in range(len(response.bits))]
        return jsonify({"relays": statuses})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/sensors', methods=['GET'])
def get_sensor_data():
    """
    Serve the latest sensor data from memory.
    """
    return jsonify(latest_data)

if __name__ == '__main__':
    # Initialize DB
    initialize_db()

    # Attempt to connect before starting threads or the Flask app
    if not client.connect():
        print("Failed to connect to Modbus device(s). Is the port in use?")
        exit(1)

    # Start sensor reading thread
    sensor_thread = threading.Thread(target=read_sensor_data, daemon=True)
    sensor_thread.start()

    # Start DB logging thread
    db_thread = threading.Thread(target=log_to_db, daemon=True)
    db_thread.start()

    try:
        app.run(host='0.0.0.0', port=5001)
    finally:
        client.close()
        print("Modbus client connection closed.")
