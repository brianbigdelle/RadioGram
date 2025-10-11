#!/usr/bin/env python3
"""
LoRa Chat — Simple Reticulum-based peer-to-peer messenger
Compatible with Reticulum v1.0.0 and LILYGO LoRa32 (ESP32, 915 MHz)

Usage (on BOTH Macs, with ~/.reticulum/config pointing at your LoRa RNode):
    python3 lora_chat.py --announce

Then exchange the printed hex addresses and, on each side:
    :connect <peer_hex>
Type to send messages. Use :quit to exit.
"""

import argparse
import os
import time
import threading
import RNS


APP_NAME = "lora_chat"
DEST_FAMILY = ["apps", APP_NAME, "simple"]

# How long to wait for path/identity after requesting (seconds)
PATH_WAIT_TIMEOUT = 10.0
PATH_WAIT_STEP = 0.1


def load_or_create_identity(path: str) -> RNS.Identity:
    """Load a saved identity or create a new one if none exists."""
    if os.path.exists(path):
        try:
            return RNS.Identity.from_file(path)
        except Exception:
            print(f"[!] Failed to load identity at {path}, creating a new one…")
    ident = RNS.Identity()
    ident.to_file(path)
    return ident


class ChatNode:
    """Simple peer-to-peer chat node for Reticulum 1.0+."""

    def __init__(self, storage: str = "lora_chat_id"):
        # Start Reticulum (reads ~/.reticulum/config)
        self.rns = RNS.Reticulum(configdir=os.path.expanduser("~/.reticulum"))
        self.identity = load_or_create_identity(storage)

        # INBOUND destination where we receive packets
        self.rx_dest = RNS.Destination(
            self.identity,
            RNS.Destination.IN,
            RNS.Destination.SINGLE,
            *DEST_FAMILY,
        )

        # In RNS 1.0 the destination packet callback signature is (data, packet)
        self.rx_dest.set_packet_callback(self._on_packet)

        # Optional: automatically prove reception for senders that request it
        try:
            self.rx_dest.set_proof_strategy(RNS.Destination.PROVE_ALL)
        except Exception:
            # Older builds may not support set_proof_strategy; safe to ignore
            pass

        self.link = None         # type: RNS.Link | None
        self.peer_hash = None    # type: bytes | None

    # ------------------------------------------------------------------

    def address(self) -> str:
        """Return this node’s destination hash as hex (no delimiters)."""
        return RNS.hexrep(self.rx_dest.hash, delimit=False)

    def announce(self) -> None:
        """Broadcast our presence so peers learn our path & identity."""
        self.rx_dest.announce()

    # ------------------------------------------------------------------

    def _wait_for_path_and_identity(self, peer_hash: bytes) -> RNS.Identity or None:
        """
        Ensure we have a route to the peer and have recalled its Identity.
        Returns the peer Identity or None if unavailable within timeout.
        """
        # Request a route if we don't have one (requires the peer to have announced)
        if not RNS.Transport.has_path(peer_hash):
            RNS.Transport.request_path(peer_hash)
            t0 = time.time()
            while not RNS.Transport.has_path(peer_hash):
                if time.time() - t0 > PATH_WAIT_TIMEOUT:
                    return None
                time.sleep(PATH_WAIT_STEP)

        # Recall the peer Identity (populated from the peer's announce)
        ident = RNS.Identity.recall(peer_hash)
        if ident is None:
            t0 = time.time()
            while ident is None:
                if time.time() - t0 > PATH_WAIT_TIMEOUT:
                    return None
                time.sleep(PATH_WAIT_STEP)
                ident = RNS.Identity.recall(peer_hash)
        return ident

    def _out_dest_for_peer(self, peer_hash_bytes: bytes) -> RNS.Destination:
        """
        Create an OUT/SINGLE destination for the peer USING ITS IDENTITY.
        In Reticulum 1.0, OUT+SINGLE requires an Identity (public key), not just a hash.
        """
        peer_identity = self._wait_for_path_and_identity(peer_hash_bytes)
        if peer_identity is None:
            raise RuntimeError(
                "Could not obtain path/identity for peer. "
                "Make sure the peer has announced (start with --announce or type :announce)."
            )

        out = RNS.Destination(
            peer_identity,
            RNS.Destination.OUT,
            RNS.Destination.SINGLE,
            *DEST_FAMILY,
        )
        return out

    def connect(self, peer_hex: str) -> bool:
        """Initiate a Link to a peer given its hex destination hash."""
        try:
            self.peer_hash = bytes.fromhex(peer_hex)
        except ValueError:
            print("[!] Peer address must be hex (no spaces).")
            return False

        try:
            out_dest = self._out_dest_for_peer(self.peer_hash)
        except RuntimeError as e:
            print(f"[!] {e}")
            return False

        # Create Link and attach link-specific callbacks (RNS 1.0)
        self.link = RNS.Link(out_dest)
        try:
            self.link.set_link_established_callback(self._on_link_established)
            self.link.set_link_closed_callback(self._on_link_closed)
            self.link.set_packet_callback(self._on_link_packet)
        except Exception:
            # If any of these are missing, it's safe to continue; we can still send/recv.
            pass

        print("[i] Attempting to establish link…")
        return True

    def send_text(self, text: str) -> None:
        """Send a message; prefer the Link if active, else best-effort packet."""
        data = text.encode("utf-8", errors="replace")

        # If we have an active link, use it for reliability/ordering
        if self.link and getattr(self.link, "status", None) == RNS.Link.ACTIVE:
            try:
                self.link.send(data)
                return
            except Exception as e:
                print(f"[!] Link send failed: {e}")

        # Fallback to direct packet (no link)
        if self.peer_hash is None:
            print("[!] No peer set. Use :connect <peer_hex> first.")
            return

        try:
            out_dest = self._out_dest_for_peer(self.peer_hash)
            pkt = RNS.Packet(out_dest, data)
            pkt.send()
        except Exception as e:
            print(f"[!] Packet send failed: {e}")

    # ------------------------------------------------------------------
    # Callbacks (RNS 1.0 signatures)
    # ------------------------------------------------------------------

    def _on_packet(self, data: bytes, packet: RNS.Packet) -> None:
        """Incoming packets addressed to our IN destination (not necessarily over a Link)."""
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            text = str(data)
        print(f"\n[←] {text}")
        print("> ", end="", flush=True)

    def _on_link_packet(self, data: bytes, packet: RNS.Packet) -> None:
        """Incoming packets arriving over an established Link."""
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            text = str(data)
        print(f"\n[← link] {text}")
        print("> ", end="", flush=True)

    def _on_link_established(self, link: RNS.Link) -> None:
        print("[✓] Link established")

    def _on_link_closed(self, link: RNS.Link) -> None:
        print("[i] Link closed")


# ----------------------------------------------------------------------

def reader_thread(chat: ChatNode) -> None:
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


def main() -> None:
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
