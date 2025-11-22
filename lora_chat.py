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
from typing import Optional
import struct
import serial
import queue



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



class InkplateBridge:
    """
    Bidirectional serial bridge for Inkplate.
      - Host -> Inkplate:  b"TXTP" + uint16_le + utf8
      - Inkplate -> Host:  b"SEND" + uint16_le + utf8
    """
    def __init__(self, port: str, baud: int = 115200):
        self.port = os.path.expanduser(port)
        self.baud = baud
        self._q = queue.Queue(maxsize=100)
        self._stop = False
        self._on_text = None    # set via set_on_text(cb: Callable[[str], None])

        # TX and RX background threads
        self._tx_t = threading.Thread(target=self._tx_worker, daemon=True)
        self._rx_t = threading.Thread(target=self._rx_worker, daemon=True)
        self._tx_t.start()
        self._rx_t.start()

    def set_on_text(self, cb):
        """Register callback to receive text from Inkplate (from 'SEND' frames)."""
        self._on_text = cb

    # ------------ public API ------------
    def send(self, text: str):
        """Queue text to show on Inkplate (TXTP). Non-blocking."""
        try:
            self._q.put_nowait(text)
        except queue.Full:
            try: self._q.get_nowait()
            except queue.Empty: pass
            self._q.put_nowait(text)

    def close(self):
        self._stop = True
        for t in (self._tx_t, self._rx_t):
            try: t.join(timeout=1.0)
            except Exception: pass

    # ------------ internals -------------
    def _open(self):
        ser = serial.Serial(
            self.port, self.baud,
            timeout=0.2, write_timeout=3,
            rtscts=False, dsrdtr=False, xonxoff=False
        )
        try:
            ser.setDTR(False); ser.setRTS(False)
            ser.reset_input_buffer(); ser.reset_output_buffer()
        except Exception:
            pass
        return ser

    def _read_exact(self, ser, n, to=5.0):
        buf = bytearray()
        t0 = time.time()
        while len(buf) < n and not self._stop:
            b = ser.read(n - len(buf))
            if b:
                buf += b
            elif time.time() - t0 > to:
                return None
        return bytes(buf)

    def _tx_worker(self):
        ser = None
        last_err = 0
        while not self._stop:
            try:
                if ser is None or not ser.is_open:
                    ser = self._open()
                try:
                    msg = self._q.get(timeout=0.2)
                except queue.Empty:
                    continue
                data = msg.encode("utf-8")[:60000]
                frame = b"TXTP" + struct.pack("<H", len(data)) + data
                ser.write(frame); ser.flush()
            except Exception as e:
                if time.time() - last_err > 2:
                    print(f"[inkplate] TX error: {e}")
                    last_err = time.time()
                try:
                    if ser: ser.close()
                except Exception:
                    pass
                ser = None
                time.sleep(0.4)
        try:
            if ser: ser.close()
        except Exception:
            pass

    def _rx_worker(self):
        ser = None
        while not self._stop:
            try:
                if ser is None or not ser.is_open:
                    ser = self._open()

                # Look for "SEND" header
                hdr = self._read_exact(ser, 4)
                if hdr is None:
                    continue
                if hdr != b"SEND":
                    # not our frame; try to resync by skipping a byte
                    continue

                ln = self._read_exact(ser, 2)
                if ln is None:
                    continue
                L = ln[0] | (ln[1] << 8)
                if L == 0 or L > 60000:
                    continue
                data = self._read_exact(ser, L)
                if data is None:
                    continue

                text = data.decode("utf-8", errors="replace")
                if self._on_text:
                    try:
                        self._on_text(text)
                    except Exception as e:
                        print(f"[inkplate] on_text callback error: {e}")

            except Exception as e:
                print(f"[inkplate] RX error: {e}")
                try:
                    if ser: ser.close()
                except Exception:
                    pass
                ser = None
                time.sleep(0.4)
        try:
            if ser: ser.close()
        except Exception:
            pass

class ChatNode:
    """Simple peer-to-peer chat node for Reticulum 1.0+."""

    def __init__(self, storage: str, configdir: str, inkplate: Optional[InkplateBridge] = None):
        # honor --configdir
        cfg = os.path.expanduser(configdir)
        if os.path.basename(cfg) == "config":
            cfg = os.path.dirname(cfg)
        self.rns = RNS.Reticulum(configdir=cfg)
        print("[i] RNS config dir:", self.rns.configdir)

        self.inkplate = inkplate

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
        # When a peer connects to us, Reticulum creates an incoming Link.
        # Attach our callbacks to it so we can receive link packets.
        try:
            self.rx_dest.set_link_established_callback(self._on_incoming_link_established)
        except Exception:
            pass

        # Optional: automatically prove reception for senders that request it
        try:
            self.rx_dest.set_proof_strategy(RNS.Destination.PROVE_ALL)
        except Exception:
            # Older builds may not support set_proof_strategy; safe to ignore
            pass

    
        self.link: Optional[RNS.Link] = None
        self.peer_hash: Optional[bytes] = None

    # ------------------------------------------------------------------

    def address(self) -> str:
        """Return this node’s destination hash as hex (no delimiters)."""
        return RNS.hexrep(self.rx_dest.hash, delimit=False)

    def announce(self) -> None:
        """Broadcast our presence so peers learn our path & identity."""
        self.rx_dest.announce()

    # ------------------------------------------------------------------

    def _wait_for_path_and_identity(self, peer_hash: bytes) -> Optional[RNS.Identity]:
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

        # If we have an active link, send a Packet addressed to the Link
        if self.link and getattr(self.link, "status", None) == RNS.Link.ACTIVE:
            try:
                pkt = RNS.Packet(self.link, data)  # destination can be a Link
                pkt.send()
                return
            except Exception as e:
                print(f"[!] Link packet send failed: {e}")

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

    def _on_incoming_link_established(self, link: RNS.Link) -> None:
        # Track stats and attach the same packet/closed handlers
        try:
            link.track_phy_stats(True)
            link.set_packet_callback(self._on_link_packet)
            link.set_link_closed_callback(self._on_link_closed)
        except Exception:
            pass
        # Optionally remember it so :rssi works on the receiver too
        self.link = link
        print("[✓] Incoming link established")



    def _fmt_phy(self, packet) -> str:
        """Return 'RSSI -xx.x dBm | SNR yy.y dB' or '' if unavailable."""
        rssi = None
        snr  = None
        try:
            rssi = packet.get_rssi()    # dBm (float) or None
        except Exception:
            pass
        try:
            snr = packet.get_snr()      # dB (float) or None
        except Exception:
            pass

        parts = []
        if rssi is not None:
            parts.append(f"RSSI {rssi:.1f} dBm")
        if snr is not None:
            parts.append(f"SNR {snr:.1f} dB")
        return " | ".join(parts) if parts else ""

    def _on_packet(self, data: bytes, packet: RNS.Packet) -> None:
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            text = str(data)
        phy = self._fmt_phy(packet)
        suffix = f"   ({phy})" if phy else ""
        print(f"\n[←] {text}{suffix}")
        # NEW: mirror to Inkplate
        if self.inkplate:
            try:
                self.inkplate.send(text)
            except Exception:
                pass
        print("> ", end="", flush=True)

    def _on_link_packet(self, data: bytes, packet: RNS.Packet) -> None:
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            text = str(data)
        phy = self._fmt_phy(packet)
        suffix = f"   ({phy})" if phy else ""
        print(f"\n[← link] {text}{suffix}")
        # NEW: mirror to Inkplate
        if self.inkplate:
            try:
                self.inkplate.send(text)
            except Exception:
                pass
        print("> ", end="", flush=True)



    def _on_link_established(self, link: RNS.Link) -> None:
        try:
            link.track_phy_stats(True)   # enables link.get_rssi()/get_snr()
        except Exception:
            pass
        print("[✓] Link established")

    def _on_link_closed(self, link: RNS.Link) -> None:
        print("[i] Link closed")

    # Optional: used by :rssi command
    def print_link_stats(self):
        if not self.link or getattr(self.link, "status", None) != RNS.Link.ACTIVE:
            print("[i] No active link")
            return
        rssi = None
        snr  = None
        try:
            rssi = self.link.get_rssi()
        except Exception:
            pass
        try:
            snr = self.link.get_snr()
        except Exception:
            pass
        if rssi is None and snr is None:
            print("[i] PHY stats unavailable on this interface")
        else:
            parts = []
            if rssi is not None: parts.append(f"RSSI {rssi:.1f} dBm")
            if snr  is not None: parts.append(f"SNR {snr:.1f} dB")
            print("[link] " + " | ".join(parts))


# ----------------------------------------------------------------------

def reader_thread(chat: ChatNode) -> None:
    print(
        "Type messages and press Enter.\n"
        "Commands:\n"
        "  :me                  -> show my address\n"
        "  :announce            -> broadcast my presence\n"
        "  :connect <peer_hex>  -> set peer and open link\n"
        "  :rssi                -> show current link RSSI/SNR (if available)\n"
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
        elif line == ":rssi":
            chat.print_link_stats()
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
    parser.add_argument(
        "--configdir",
        default=os.path.expanduser("~/dev/RadioGram_bb"),
        help="Reticulum config directory",

    )
    parser.add_argument(
    "--inkplate-port",
    default=None,
    help="Serial port to an Inkplate (e.g., /dev/ttyACM0 or /dev/cu.usbmodemXYZ). If set, received messages are mirrored to the e-paper.",
    )


    args = parser.parse_args()

    ink = None
    if args.inkplate_port:
        ink = InkplateBridge(args.inkplate_port)

    chat = ChatNode(storage=args.idfile, configdir=args.configdir, inkplate=ink)

    # NEW: when Inkplate sends "SEND …", forward it over Reticulum
    if ink:
        def _from_inkplate_to_radio(s: str):
            print(f"[inkplate->radio] {s}")
            chat.send_text(s)
        ink.set_on_text(_from_inkplate_to_radio)


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

    finally:
        if ink:
            ink.close()



if __name__ == "__main__":
    main()
