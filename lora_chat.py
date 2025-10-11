#!/usr/bin/env python3
"""
LoRa Chat — Simple Reticulum-based peer-to-peer messenger
Compatible with Reticulum v1.0.0 and LILYGO LoRa32 (ESP32, 915 MHz)
Author: Brian Bigdelle
"""

import argparse
import os
import time
import threading
import RNS


APP_NAME = "lora_chat"
DEST_FAMILY = ["apps", APP_NAME, "simple"]


def load_or_create_identity(path):
    if os.path.exists(path):
        try:
            return RNS.Identity.from_file(path)
        except Exception:
            print(f"[!] Failed to load identity at {path}, creating a new one…")
    ident = RNS.Identity()
    ident.to_file(path)
    return ident


class ChatNode:
    """Simple peer-to-peer chat node using Reticulum 1.0+"""

    def __init__(self, storage="lora_chat_id"):
        # Start Reticulum (reads ~/.reticulum/config)
        self.rns = RNS.Reticulum()
        self.identity = load_or_create_identity(storage)

        # Create destination for incoming packets
        self.rx_dest = RNS.Destination(
            self.identity,
            RNS.Destination.IN,
            RNS.Destination.SINGLE,
            *DEST_FAMILY,
        )

        # Register the packet receive callback (correct for Reticulum 1.0)
        self.rx_dest.set_packet_callback(self._on_packet)

        self.link = None
        self.peer_hash = None

    # ------------------------------------------------------------

    def address(self):
        return RNS.hexrep(self.rx_dest.hash, delimit=False)

    def announce(self):
        self.rx_dest.announce()

    # ------------------------------------------------------------

    def _out_dest_for_peer(self, peer_hash_bytes):
        out = RNS.Destination(
            None,
            RNS.Destination.OUT,
            RNS.Destination.SINGLE,
            *DEST_FAMILY,
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
        self.link.set_link_established_callback(self._on_link_established)
        self.link.set_link_closed_callback(self._on_link_closed)

        print("[i] Attempting to establish link…")
        return True

    def send_text(self, text):
        data = text.encode("utf-8", errors="replace")

        if self.link and self.link.status == RNS.Link.ACTIVE:
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

    # ------------------------------------------------------------

    def _on_packet(self, dest, data):
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            text = str(data)
        print(f"\n[←] {text}")
        print("> ", end="", flush=True)

    def _on_link_established(self, link):
        print("[✓] Link established")

    def _on_link_closed(self, link):
        print("[i] Link closed")


# -----------------------------------------------------------------

def reader_thread(chat):
    print(
        "Type messages and press Enter.\n"
        "Commands:\n"
        "  :me                  -> show my address\n"
        "  :announce            -> broadcast my presence\n"
        "  :connect <peer_hex>  -> set peer and open link\n"
        "  :quit                -> exit\n"
    )
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
    parser = argparse.ArgumentParser(description="Tiny LoRa Chat over Reticulum")
    parser.add_argument("--idfile", default="lora_chat_id", help="Identity file path")
    parser.add_argument("--announce", action="store_true", help="Announce on startup")
    args = parser.parse_args()

    chat = ChatNode(storage=args.idfile)
    print("Tiny LoRa Chat")
    print(f"Your address: {chat.address()}")

    if args.announce:
        chat.announce()
        print("[→] Announce sent")

    t = threading.Thread(target=reader_thread, args=(chat,), daemon=False)
    t.start()

    try:
        while t.is_alive():
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
