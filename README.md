# LoRa Chat — Simple Reticulum Radio Messaging

This project lets two Macs chat directly over 915 MHz LoRa radios using LILYGO LoRa32 (ESP32) boards and the Reticulum network stack. No Wi-Fi, no Internet.

## Full Setup & Run Instructions

# 1. Flash RNode firmware onto each board
# Visit https://rnode.reconfigure.io/ in Chrome/Edge
#  • Plug in your LILYGO LoRa32 (v2.0/v2.1)
#  • Choose Region: 915 MHz
#  • Click "Install Firmware", then "Provision Device"
#  • Repeat for the second board
# Antennas must always be attached before power-on.

# 2. Install Reticulum on both Macs
python3 -m venv venv
source venv/bin/activate
pip install rns

# 3. Create Reticulum config (LoRa-only)
mkdir -p ~/.reticulum
nano ~/.reticulum/config

# Paste this block (edit only 'port' to match your board’s serial device)
[Reticulum]
interfaces_default = no
enable_transport = yes

[[LoRa 915 Chat]]
type = RNodeInterface
enabled = yes
port = /dev/cu.SLAB_USBtoUART     # or /dev/cu.usbserial-XXXX
frequency = 915000000
bandwidth = 125000
spreadingfactor = 9
codingrate = 5
txpower = 14

# (Find the correct port with: ls /dev/cu.*)

# 4. Verify that the RNode link is active
rnsd -v
# You should see: "Interface [LoRa 915 Chat] activated using RNode /dev/cu.…"
# In another terminal:
rnstatus
# Both sides should list the same LoRa interface.

# 5. Clone or create the chat program
git clone https://github.com/<yourusername>/lora-chat.git
cd lora-chat
nano lora_chat.py

# Paste the complete Python script below into lora_chat.py
#!/usr/bin/env python3
import argparse, os, sys, time, threading
import RNS

APP_NAME = "lora_chat"
DEST_FAMILY = ["apps", APP_NAME, "simple"]

def load_or_create_identity(path):
    if os.path.exists(path):
        try:
            return RNS.Identity.from_file(path)
        except Exception:
            print(f"[!] Failed loading identity {path}, creating new one…")
    ident = RNS.Identity()
    ident.to_file(path)
    return ident

class ChatNode:
    def __init__(self, storage="lora_chat_id"):
        self.rns = RNS.Reticulum()
        self.identity = load_or_create_identity(storage)
        self.rx_dest = RNS.Destination(
            self.identity,
            RNS.Destination.IN,
            RNS.Destination.SINGLE,
            *DEST_FAMILY
        )
        self.rx_dest.set_default_app_data_callback(self._on_packet)
        self.rx_dest.register_link_established_callback(self._on_link_established)
        self.rx_dest.register_link_closed_callback(self._on_link_closed)
        self.link = None
        self.peer_hash = None

    def address(self):
        return RNS.hexrep(self.rx_dest.hash, delimit=False)

    def announce(self):
        self.rx_dest.announce()

    def _out_dest_for_peer(self, peer_hash_bytes):
        out = RNS.Destination(None, RNS.Destination.OUT, RNS.Destination.SINGLE, *DEST_FAMILY)
        out.hash = peer_hash_bytes
        return out

    def connect(self, peer_hex):
        try:
            self.peer_hash = bytes.fromhex(peer_hex)
        except ValueError:
            print("[!] Peer address must be hex (no spaces).")
            return False
        out_dest = self._out_dest_for_peer(self.peer_hash)
        self.link = RNS.Link(out_dest)
        t0 = time.time()
        while not (self.link and self.link.established):
            if time.time() - t0 > 8: break
            time.sleep(0.05)
        if self.link and self.link.established:
            print("[✓] Link established")
        else:
            print("[i] Link not established yet; sending best-effort packets OK.")
        return True

    def send_text(self, text):
        data = text.encode("utf-8", errors="replace")
        if self.link and self.link.established:
            try:
                self.link.send(data)
                return
            except Exception as e:
                print(f"[!] Link send failed: {e}")
        if self.peer_hash is None:
            print("[!] No peer set. Use :connect <peer_hex> first.")
            return
        out_dest = self._out_dest_for_peer(self.peer_hash)
        try:
            pkt = RNS.Packet(out_dest, data)
            pkt.send()
        except Exception as e:
            print(f"[!] Packet send failed: {e}")

    def _on_packet(self, dest, data, context):
        try: text = data.decode("utf-8", errors="replace")
        except Exception: text = str(data)
        print(f"\n[←] {text}")
        print("> ", end="", flush=True)

    def _on_link_established(self, link): print("[✓] Incoming link established")
    def _on_link_closed(self, link): print("[i] Link closed")

def reader_thread(chat):
    print("Type messages then Enter. Commands: :me, :announce, :connect <peer>, :quit\n")
    while True:
        try: line = input("> ").strip()
        except (EOFError, KeyboardInterrupt): break
        if not line: continue
        if line == ":quit": break
        elif line == ":me": print(f"[you] {chat.address()}")
        elif line == ":announce": chat.announce(); print("[→] Announce sent")
        elif line.startswith(":connect "): chat.connect(line.split(None,1)[1])
        else: chat.send_text(line)

def main():
    p = argparse.ArgumentParser(description="Tiny LoRa Chat over Reticulum")
    p.add_argument("--idfile", default="lora_chat_id")
    p.add_argument("--announce", action="store_true")
    a = p.parse_args()
    chat = ChatNode(storage=a.idfile)
    print("Tiny LoRa Chat\nYour address:", chat.address())
    if a.announce: chat.announce(); print("[→] Announce sent")
    t = threading.Thread(target=reader_thread, args=(chat,), daemon=False)
    t.start()
    try:
        while t.is_alive(): time.sleep(0.2)
    except KeyboardInterrupt: pass

if __name__ == "__main__": main()

# Save and exit nano.

# 6. Run the program on both Macs
python3 lora_chat.py --announce
# Each will print an address like: 7f3a2c9d4b8e0123
# Exchange those addresses between the two machines.

# On Mac 1:
:connect <address_from_Mac2>

# On Mac 2:
:connect <address_from_Mac1>

# Now type and press Enter to chat.
# Example output:
# [←] hello from the other Mac

# 7. Useful commands inside chat:
#   :me             -> show your address
#   :announce       -> broadcast presence
#   :connect <hex>  -> connect to peer
#   :quit           -> exit program

# Keep LoRa parameters identical on both sides.
# For longer range: spreadingfactor = 10 or 11.
# For faster short range: SF 7-8, BW 250 kHz.
# Stay within 915 MHz ISM-band rules.

# Done — you now have a working 915 MHz LoRa chat link between two Macs.
