# Growbox control and monitoring system

This is a one-off personal project that's still fairly crude but I'm sharing it
here for anyone who might be interested in using it as a starting point.

The project allows control of temperature, humidity, light, and fresh air in a
growing chamber. It's split into three parts - a relay/sensor interface
service, a control service, and a web front end.

My hardware setup uses a Modbus RTU relay module to control everything, a
Modbus RTU temperature/humidity probe for sensing, and a USB postage scale to
monitor the weight of the humidifier and calculate the reservoir's water level.

My Modbus relays (at slave address 1) are configured as follows:

1   Heater
2   Humidifier
3   Lights
4   Exhaust fan

Additionally, the control service uses a USB webcam to capture an image every
5 minutes and saves old snapshots for later assembly into a time lapse video.
The humidifier is prevented from running for 60 seconds before the camera
takes a snapshot, to allow time for the mist to clear. If the lights aren't
already on, they're turned on two seconds prior to the snapshot and then turned
off again.

The sensor is a Taidacent SHT30 Modbus RTU probe, configured for Modbus slave
address 2.

nginx is used to host the front end, and provides a reverse proxy for access
to the control service.
