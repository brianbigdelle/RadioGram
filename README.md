# 🛰️ LoRa Chat — Simple Reticulum Radio Messaging

This project lets two computers **chat directly over 915 MHz LoRa radios** using  
**LILYGO LoRa32 (ESP32) boards** and the **Reticulum** networking stack — no Wi-Fi, no Internet, no router.

---

## 🧩 What You’ll Need
- 2 × **LILYGO LoRa32 (ESP32, 915 MHz)** boards  
- 2 × **Antennas** (always attach before powering)  
- 2 × **MacBooks** (or Linux machines)  
- **Python ≥ 3.8**  
- **USB-C or micro-USB** cables

---

## ⚙️ Setup Steps

### 1. Flash RNode Firmware
Flash each board using the [RNode web flasher](https://rnode.reconfigure.io/):

1. Plug in your LILYGO LoRa32.  
2. Choose your board type (v2.0/v2.1) and **Region = 915 MHz**.  
3. Click **Install Firmware**, then **Provision Device**.  
4. Repeat for the second board.

---

### 2. Install Reticulum
On both Macs:
```bash
python3 -m venv venv
source venv/bin/activate
pip install rns
3. Configure the LoRa Interface
Create a Reticulum config file:
mkdir -p ~/.reticulum
nano ~/.reticulum/config
Paste this (change only the serial port path to match your Mac):
[Reticulum]
interfaces_default = no
enable_transport = yes

[[LoRa 915 Chat]]
type = RNodeInterface
enabled = yes
port = /dev/cu.SLAB_USBtoUART    # or /dev/cu.usbserial-xxxx
frequency = 915000000
bandwidth = 125000
spreadingfactor = 9
codingrate = 5
txpower = 14
Find your port with:
ls /dev/cu.*
4. Verify the Link
Run:
rnsd -v
You should see:
Interface [LoRa 915 Chat] activated using RNode /dev/cu.SLAB_USBtoUART
In another terminal:
rnstatus
If both interfaces appear, the LoRa link is live.
5. Run the Chat Program
Clone or copy this repo and run:
git clone https://github.com/<yourusername>/lora-chat.git
cd lora-chat
python3 lora_chat.py --announce
Each side will display its address:
Tiny LoRa Chat
Your address: 7f3a2c9d4b8e0123
[→] Announce sent
Exchange these addresses between the two Macs.
6. Connect and Chat
On Mac 1:
> :connect <address_from_Mac2>
On Mac 2:
> :connect <address_from_Mac1>
Then type messages and hit Enter to send.
Example:

[←] hello from the other Mac
💬 Chat Commands
Command	Description
:me	Show your address
:announce	Broadcast your presence
:connect <peer_hex>	Connect to another node
<text>	Send message
:quit	Exit
⚠️ Notes
Antennas must be attached before power-on.
Both sides must use the same frequency, bandwidth, spreadingfactor, and codingrate.
Keep transmissions short — LoRa airtime increases rapidly with higher SF.
915 MHz is an ISM band; comply with your local regulations.
🧠 Next Steps
Add LXMF for reliable/queued messaging.
Bridge more nodes for a mesh.
Wrap the CLI in a simple GUI.
Author
Brian Bigdelle
MIT License