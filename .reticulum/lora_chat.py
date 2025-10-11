#!/usr/bin/env python3
import argparse, os, sys, time, threading
import RNS

APP_NAME = "lora_chat"
DEST_FAMILY = ["apps", APP_NAME, "simple"]  # name components

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
        # Start Reticulum (reads ~/.reticulum/config -> LoRa RNodeInterface)
        self.rns = RNS.Reticulum()
        self.identity = load_or_create_identity(storage)
        # IN destination for receiving
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
        # Make ourselves discoverable
        self.rx_dest.announce()

    # When we *send*, we need an OUT destination bound to the peer’s hash
    def _out_dest_for_peer(self, peer_hash_bytes):
        # Create a temporary OUT destination with the peer’s hash
        out = RNS.Destination(
            None,  # identity is unknown for peer
            RNS.Destination.OUT,
            RNS.Destination.SINGLE,
            *DEST_FAMILY
        )
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
        # Optionally wait for link establishment (or just send best-effort packets)
        t0 = time.time()
        while not (self.link and self.link.established):
            if time.time() - t0 > 8:
                break
            time.sleep(0.05)
        if self.link and self.link.established:
            print("[✓] Link established")
            return True
        else:
            print("[i] Link not established yet; will send as best-effort packets.")
            return True

    def send_text(self, text):
        data = text.encode("utf-8", errors="replace")
        if self.link and self.link.established:
            try:
                self.link.send(data)
                return
            except Exception as e:
                print(f"[!] Link send failed: {e}")

        # Fall back to direct packet (no link reliability/ordering)
        if self.peer_hash is None:
            print("[!] No peer set. Use :connect <peer_hex> first.")
            return
        out_dest = self._out_dest_for_peer(self.peer_hash)
        try:
            pkt = RNS.Packet(out_dest, data)
            pkt.send()
        except Exception as e:
            print(f"[!] Packet send failed: {e}")

    # ---- Callbacks ----
    def _on_packet(self, dest, data, context):
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            text = str(data)
        print(f"\n[←] {text}")
        print("> ", end="", flush=True)

    def _on_link_established(self, link):
        print("[✓] Incoming link established")

    def _on_link_closed(self, link):
        print("[i] Link closed")

def reader_thread(chat):
    print("Type messages and press Enter. Commands:")
    print("  :me                  -> show my address")
    print("  :announce            -> (re)announce my address")
    print("  :connect <peer_hex>  -> set peer and open link")
    print("  :quit                -> exit")
    print("")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line == ":quit":
            break
        elif line == ":me":
            print(f"[you] {chat.address()}")
        elif line == ":announce":
            chat.announce()
            print("[→] Announce sent")
        elif line.startswith(":connect "):
            peer = line.split(None, 1)[1]
            chat.connect(peer)
        else:
            chat.send_text(line)

def main():
    parser = argparse.ArgumentParser(description="Tiny LoRa chat over Reticulum")
    parser.add_argument("--idfile", default="lora_chat_id", help="Identity file path")
    parser.add_argument("--announce", action="store_true", help="Announce on start")
    args = parser.parse_args()

    chat = ChatNode(storage=args.idfile)

    print("Tiny LoRa Chat")
    print(f"Your address: {chat.address()}")
    if args.announce:
        chat.announce()
        print("[→] Announce sent")

    t = threading.Thread(target=reader_thread, args=(chat,), daemon=False)
    t.start()
    # Keep Reticulum alive
    try:
        while t.is_alive():
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
