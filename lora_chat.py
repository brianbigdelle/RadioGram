#!/usr/bin/env python3
"""
LoRa Chat — Simple Reticulum-based peer-to-peer messenger
Supports both Console Mode (default) and Inkplate UI Mode (-S).
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
import json 
import sys

# --- RNS Setup ---
APP_NAME = "lora_chat"
DEST_FAMILY = ["apps", APP_NAME, "simple"]
PATH_WAIT_TIMEOUT = 10.0
PATH_WAIT_STEP = 0.1
# -----------------


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
    """Handles serial communication and frame packaging (JCTL, EVNT, TXTP, TXIN)."""

    def __init__(self, port: str, baud: int = 115200):
        self.port = os.path.expanduser(port)
        self.baud = baud
        self._q = queue.Queue(maxsize=100)
        self._stop = False
        self._on_text = None
        
        self._tx_t = threading.Thread(target=self._tx_worker, daemon=True)
        self._rx_t = threading.Thread(target=self._rx_worker, daemon=True)
        self._tx_t.start()
        self._rx_t.start()

    def set_on_text(self, cb):
        self._on_text = cb

    # --- Public API ---
    def send_json(self, data_dict: dict):
        """Queue a dictionary to show on Inkplate as JCTL (JSON Control) frame."""
        try:
            json_str = json.dumps(data_dict)
            self._q.put_nowait(("JCTL", json_str))
        except queue.Full:
            try: self._q.get_nowait()
            except queue.Empty: pass
            self._q.put_nowait(("JCTL", json.dumps(data_dict)))
        except TypeError:
            print("[inkplate] WARNING: Tried to send non-serializable JSON object.")

    def send_txtp(self, text: str):
        """Queue plain text to show on Inkplate terminal (TXTP frame)."""
        try:
            self._q.put_nowait(("TXTP", text))
        except queue.Full:
            try: self._q.get_nowait()
            except queue.Empty: pass
            self._q.put_nowait(("TXTP", text))

    def send_toast(self, text: str):
        """Helper to send a quick, temporary message update to the Inkplate."""
        # In terminal mode, toasts just appear as log lines
        self.send_txtp(f"[!] {text}")

    def close(self):
        self._stop = True
        for t in (self._tx_t, self._rx_t):
            try: t.join(timeout=1.0)
            except Exception: pass

    # --- Internals ---
    
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
                    frame_type, msg = self._q.get(timeout=0.2) 
                except queue.Empty:
                    continue
                
                if frame_type == "JCTL":
                    header = b"JCTL"
                else: 
                    header = b"TXTP" 
                    
                data = msg.encode("utf-8")[:60000]
                frame = header + struct.pack("<H", len(data)) + data
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

                hdr = self._read_exact(ser, 4, to=0.5) 
                if hdr is None: continue
                
                # Accept both EVNT (Legacy) and TXIN (Terminal Input) headers
                if hdr not in [b"EVNT", b"TXIN"]:
                    ser.reset_input_buffer() 
                    continue

                ln = self._read_exact(ser, 2)
                if ln is None: continue
                L = ln[0] | (ln[1] << 8)
                
                if L == 0 or L > 60000:
                    ser.reset_input_buffer()
                    continue

                data = self._read_exact(ser, L)
                if data is None: continue

                text = data.decode("utf-8", errors="replace").strip()
                if self._on_text:
                    try:
                        self._on_text(text)
                    except Exception as e:
                        print(f"[inkplate] on_text callback error: {e}")

            except serial.SerialException as se:
                print(f"[inkplate] RX serial error: {se}")
                if ser: ser.close()
                ser = None
                time.sleep(1.0)
            except Exception as e:
                print(f"[inkplate] RX general error: {e}")
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
        self.inkplate = inkplate

        cfg = os.path.expanduser(configdir)
        if os.path.basename(cfg) == "config":
            cfg = os.path.dirname(cfg)
        self.rns = RNS.Reticulum(configdir=cfg)
        
        self._print_status(f"[i] RNS config dir: {self.rns.configdir}", console_only=True)
        
        self.identity = load_or_create_identity(storage)

        self.rx_dest = RNS.Destination(
            self.identity, RNS.Destination.IN, RNS.Destination.SINGLE, *DEST_FAMILY)

        self.rx_dest.set_packet_callback(self._on_packet)
        try:
            self.rx_dest.set_link_established_callback(self._on_incoming_link_established)
            self.rx_dest.set_proof_strategy(RNS.Destination.PROVE_ALL)
        except Exception: pass
        
        self.link: Optional[RNS.Link] = None
        self.peer_hash: Optional[bytes] = None
        
        self._print_status(f"Your address: {self.address()}", prompt=False)

    def address(self) -> str:
        """Return this node’s destination hash as hex (no delimiters)."""
        return RNS.hexrep(self.rx_dest.hash, delimit=False)

    def _print_status(self, text: str, console_only: bool = False, prompt: bool = True) -> None:
        """Routes status messages to the appropriate output(s)."""
        # 1. Print to console
        print(text)
        
        # 2. If Inkplate is attached, send TXTP frame (unless console_only)
        if self.inkplate and not console_only:
             self.inkplate.send_txtp(text)
             
        # 3. If in console mode (inkplate might be None or passive), print prompt
        if not self.inkplate and prompt:
             sys.stdout.write("> ")
             sys.stdout.flush()

    def announce(self) -> None:
        """Broadcast our presence so peers learn our path & identity."""
        self.rx_dest.announce()
        self._print_status("[→] Announce sent")

    def _wait_for_path_and_identity(self, peer_hash: bytes) -> Optional[RNS.Identity]:
        t0 = time.time()
        while not RNS.Transport.has_path(peer_hash) or RNS.Identity.recall(peer_hash) is None:
            if time.time() - t0 > PATH_WAIT_TIMEOUT:
                return None
            RNS.Transport.request_path(peer_hash) # Request path until found
            time.sleep(PATH_WAIT_STEP)
        return RNS.Identity.recall(peer_hash)

    def _out_dest_for_peer(self, peer_hash_bytes: bytes) -> RNS.Destination:
        peer_identity = self._wait_for_path_and_identity(peer_hash_bytes)
        if peer_identity is None:
            raise RuntimeError(
                "Could not obtain path/identity for peer. Make sure the peer has announced.")
        out = RNS.Destination(
            peer_identity, RNS.Destination.OUT, RNS.Destination.SINGLE, *DEST_FAMILY)
        return out

    def connect(self, peer_hex: str) -> None:
        """Initiate a Link to a peer given its hex destination hash."""
        try:
            self.peer_hash = bytes.fromhex(peer_hex)
        except ValueError:
            self._print_status("[!] Invalid Peer Hash")
            return

        try:
            out_dest = self._out_dest_for_peer(self.peer_hash)
        except RuntimeError as e:
            self._print_status(str(e))
            return

        self.link = RNS.Link(out_dest)
        try:
            self.link.set_link_established_callback(self._on_link_established)
            self.link.set_link_closed_callback(self._on_link_closed)
            self.link.set_packet_callback(self._on_link_packet)
        except Exception: pass

        self._print_status(f"[i] Connecting to {peer_hex[:10]}...")

    def send_text(self, text: str) -> None:
        """Send a message and mirror it to the screen."""
        data = text.encode("utf-8", errors="replace")

        if not self.link or getattr(self.link, "status", None) != RNS.Link.ACTIVE:
            self._print_status("[!] Link is not active.")
            return

        try:
            pkt = RNS.Packet(self.link, data)
            pkt.send()
        except Exception as e:
            self._print_status(f"[!] Link packet send failed: {e}")
            self.link = None
            return
            
        # Echo the sent message to the local terminal
        self._print_status(f"[Me] {text}")

    # --- Callbacks ---

    def _on_incoming_link_established(self, link: RNS.Link) -> None:
        try:
            link.track_phy_stats(True)
            link.set_packet_callback(self._on_link_packet)
            link.set_link_closed_callback(self._on_link_closed)
        except Exception: pass
        self.link = link
        self._print_status("[✓] Incoming Link Established")

    def _fmt_phy(self, packet) -> str:
        rssi = None; snr = None
        try: rssi = packet.get_rssi()
        except Exception: pass
        try: snr = packet.get_snr()
        except Exception: pass
        parts = []
        if rssi is not None: parts.append(f"RSSI {rssi:.1f} dBm")
        if snr is not None: parts.append(f"SNR {snr:.1f} dB")
        return " | ".join(parts) if parts else ""

    def _on_packet(self, data: bytes, packet: RNS.Packet) -> None:
        # Fallback non-link packet received
        text = data.decode("utf-8", errors="replace")
        self._print_status(f"[?] Non-Link Msg: {text[:20]}...")

    def _on_link_packet(self, data: bytes, packet: RNS.Packet) -> None:
        # Message received over an active link
        text = data.decode("utf-8", errors="replace")
        
        # Determine sender name (short hash)
        try:
            peer_id = RNS.hexrep(packet.link.get_peer_id(), delimit=False)[:10]
        except:
            peer_id = "Remote"
            
        # Log to screen/console
        self._print_status(f"[{peer_id}] {text}")

    def _on_link_established(self, link: RNS.Link) -> None:
        try: link.track_phy_stats(True)
        except Exception: pass
        self.link = link
        self._print_status("[✓] Link Established")

    def _on_link_closed(self, link: RNS.Link) -> None:
        self.link = None
        self._print_status("[i] Link Closed")


# ----------------------------------------------------------------------
# --- Main Logic (Handle Commands & Events) ---

def _handle_command(chat: ChatNode, command: str) -> bool:
    """Parses and executes a command string from the console or Inkplate."""
    
    command = command.strip()
    if not command:
        return True

    if command.startswith(":"):
        parts = command.split(None, 1)
        cmd = parts[0]
        arg = parts[1].strip() if len(parts) > 1 else None

        if cmd == ":announce":
            chat.announce()
        elif cmd == ":connect" and arg:
            chat.connect(arg)
        elif cmd == ":me":
            chat._print_status(f"[you] {chat.address()}")
        elif cmd == ":quit":
            chat._print_status("[i] Exiting...")
            return False 
        else:
            chat._print_status(f"[!] Invalid command or syntax: {command}")

    elif chat.link and getattr(chat.link, "status", None) == RNS.Link.ACTIVE:
        chat.send_text(command)
    else:
        chat._print_status("[!] Link is not active. Use :connect <hash>.")
        
    return True 

def _run_console_mode(chat: ChatNode) -> None:
    """Run loop for terminal control (PRIMARY)."""
    
    chat._print_status("--- Reticulum Console Mode Initialized ---", prompt=False)
    chat._print_status(
        "Type messages or commands:\n"
        " :me, :announce, :connect <hash>, :quit", 
        prompt=False
    )
    
    sys.stdout.write("> "); sys.stdout.flush()

    try:
        while True:
            time.sleep(0.1) 
            if sys.stdin in enumerate(sys.stdin)[0] if False else [sys.stdin]:
                 line = sys.stdin.readline()
                 if line:
                     if not _handle_command(chat, line):
                         break
    except EOFError:
        pass
    except KeyboardInterrupt:
        pass

def _from_inkplate_event(chat: ChatNode, s: str):
    """Callback triggered when the Inkplate sends data (JSON or Plain Text)."""
    
    # 1. Handle Plain Text Commands (from new Terminal UI)
    if not s.strip().startswith("{"):
        # This is a raw command from the terminal (e.g., ":me" or "Hello")
        _handle_command(chat, s)
        return

    # 2. Handle Legacy JSON Events (Fallback)
    try:
        event = json.loads(s)
        event_type = event.get("type")
        
        if event_type == "ANNOUNCE":
            chat.announce()
            
        elif event_type == "CONNECT":
            peer_hash = event.get("peer")
            if peer_hash: 
                chat.connect(peer_hash)
            
        elif event_type == "SEND_MSG":
            text = event.get("text")
            if text and chat.link:
                chat.send_text(text)
            else:
                chat._print_status("[!] Send error: Not connected or empty.")

    except Exception as e:
        print(f"[Inkplate Event Error] Malformed data: {s}. Error: {e}")

def _run_screen_mode(ink: InkplateBridge, chat: ChatNode) -> None:
    # Register the Inkplate event handler
    ink.set_on_text(lambda s: _from_inkplate_event(chat, s))
    
    print("--- Reticulum Inkplate Bridge Initialized ---")
    print(f"My Hash: {chat.address()}")
    print("UI Control is now on the Inkplate. Press Ctrl+C to exit.")

    while True:
        time.sleep(0.5)


def main() -> None:
    parser = argparse.ArgumentParser(description="Tiny LoRa Chat over Reticulum")
    parser.add_argument("--idfile", default="lora_chat_id", help="Identity file path")
    parser.add_argument("--announce", action="store_true", help="Announce on startup")
    parser.add_argument(
        "--configdir",
        default=os.path.expanduser("~/dev/RadioGram_bb"),
        help="Reticulum config directory",
    )
    # Define the -S flag
    parser.add_argument(
        "-S", "--screen-control", 
        action="store_true", 
        help="Use the Inkplate as the primary command source (Terminal UI)."
    )
    # Define the Inkplate port, with the default set
    parser.add_argument(
    "--inkplate-port",
    default="/dev/ttyUSB0", 
    help="Serial port to an Inkplate (e.g., /dev/ttyACM0). Required if -S is set.",
    )

    args = parser.parse_args()
    
    # --- 1. CONDITIONAL INKPLATE INITIALIZATION ---
    ink = None
    if args.screen_control:
        # Only initialize the bridge if -S is present
        if not args.inkplate_port:
            print("[!] ERROR: --screen-control requires --inkplate-port to be set.")
            sys.exit(1)
        ink = InkplateBridge(args.inkplate_port)

    # 2. Initialize ChatNode (ink is either the Bridge object or None)
    chat = ChatNode(storage=args.idfile, configdir=args.configdir, inkplate=ink)

    if args.announce:
        chat.announce()
        
    # --- 3. MODE EXECUTION AND CLEANUP ---
    try:
        if args.screen_control:
            _run_screen_mode(ink, chat)
        else:
            _run_console_mode(chat)

    except KeyboardInterrupt:
        pass 

    finally:
        if ink:
            ink.close()


if __name__ == "__main__":
    main()
