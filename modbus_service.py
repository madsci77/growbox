from flask import Flask, request, jsonify
from pymodbus.client import ModbusSerialClient

app = Flask(__name__)

# Configure the Modbus client
client = ModbusSerialClient(
    port='/dev/ttyUSB0',  # Update if necessary
    baudrate=9600,
    bytesize=8,
    parity='N',
    stopbits=1,
    timeout=1
)

# Slave ID for the Modbus relay board
SLAVE_ID = 1

# Check connection on startup
if not client.connect():
    print("Failed to connect to Modbus relay board.")
    exit(1)

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

        # Convert to 0-indexed coil number
        coil_number = relay_number - 1
        response = client.write_coil(coil_number, state, slave=SLAVE_ID)

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
        # Convert to 0-indexed coil number
        coil_number = relay_number - 1
        response = client.read_coils(coil_number, count=1, slave=SLAVE_ID)

        if response.isError():
            return jsonify({"error": f"Failed to read relay {relay_number} status."}), 500

        status = response.bits[0]  # Get the first bit for this coil
        return jsonify({"relay": relay_number, "state": status})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/relays', methods=['GET'])
def get_all_relays_status():
    """
    Get the status of all relays.
    """
    try:
        response = client.read_coils(0, count=8, slave=SLAVE_ID)  # Adjust count if there are more relays

        if response.isError():
            return jsonify({"error": "Failed to read relay states."}), 500

        statuses = [{"relay": i + 1, "state": response.bits[i]} for i in range(len(response.bits))]
        return jsonify({"relays": statuses})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    try:
        app.run(host='0.0.0.0', port=5001)
    finally:
        client.close()
        print("Modbus client connection closed.")
