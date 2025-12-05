(AI Slop README)


RadioGram — Reticulum LoRa Terminal
RadioGram is a peer-to-peer, off-grid messaging application powered by the Reticulum Network Stack. It runs on a Raspberry Pi connected to a LoRa radio, with an optional "Screen Mode" that offloads the user interface to an Inkplate 6 Plus e-paper display, creating a standalone communication device.

🏗 Hardware Architecture
The system consists of three main components connected via USB Serial:

Compute Node: Raspberry Pi Zero 2W (runs the Reticulum logic and Python bridge).

Network Interface: LILYGO LoRa32 (T3) flashed with RNode Firmware.

Display Terminal: Inkplate 6 Plus (acts as a serial keyboard and e-paper monitor).

⚡️ Software Dependencies
Raspberry Pi (Host)

Python 3

rns (Reticulum Network Stack)

pyserial

Inkplate (Display)

Arduino IDE

Inkplate Library (e-radionica)

ArduinoJson (Required for compilation)

🚀 Installation
1. Setup the LoRa Radio (RNode)

Connect your LILYGO LoRa32 to your computer.

Visit the RNode Firmware Flasher.

Install the firmware for Generic ESP32 (or specific board if listed) at 915 MHz.

Once provisioned, connect this board to the Raspberry Pi USB.

2. Flash the Inkplate Terminal

Open inkplate_Phone_UI.ino in the Arduino IDE.

Select Inkplate 6 Plus as your board.

Install the required libraries (Inkplate, ArduinoJson).

Upload the sketch.

Connect the Inkplate to the Raspberry Pi USB.

3. Setup the Host (Raspberry Pi)

Create a virtual environment (optional but recommended):

Bash
python3 -m venv venv
source venv/bin/activate
Install dependencies:

Bash
pip install rns pyserial
Configure Reticulum:

Run rnsd once to generate the config file at ~/.reticulum/config.

Edit the config to enable the RNodeInterface on the correct serial port for your LoRa radio.

📖 Usage
Option A: Screen Mode (Inkplate UI)

Use this mode to run the device as a standalone e-paper communicator. The Inkplate handles all input and output.

Bash
python3 lora_chat.py -S
Default Port: /dev/ttyUSB0

Custom Port: If your Inkplate is on a different port, specify it:

Bash
python3 lora_chat.py -S --inkplate-port /dev/ttyACM0
Option B: Console Mode (Headless)

Use this mode to chat directly from the Raspberry Pi terminal (via SSH or connected keyboard).

Bash
python3 lora_chat.py
💬 Commands
The following commands can be typed into the terminal (Inkplate or Console):

Command	Description
:announce	Broadcast your identity to the network. Required for peers to find you.
:connect <hash>	Initiate a link to a peer. Replace <hash> with their 32-byte hex address.
:me	Display your own Destination Hash (Address).
:quit	Exit the application.
To Chat: Once a link is established (you will see [✓] Link Established), simply type your message and press Enter.

🔧 Troubleshooting
1. "Error: --screen-control requires --inkplate-port"

Ensure the Inkplate is plugged in.

Check which port it is assigned to using ls /dev/tty*. It is usually /dev/ttyUSB0 or /dev/ttyACM0.

2. Messages are not sending

Ensure you have run :announce on both devices.

Ensure you have established a connection using :connect <peer_hash>.

Check that your Reticulum config (~/.reticulum/config) has the correct port and baud rate for the LoRa radio.

3. Keyboard touch is inaccurate

The Inkplate code is set to ROTATION = 2. If your text is upside down or touch is inverted, change this value in inkplate_Phone_UI.ino.
