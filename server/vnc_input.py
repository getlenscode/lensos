"""Minimal RFB client that injects input into the desktop through wayvnc.

The user's browser drives the desktop over this same channel, so events sent
here behave exactly like real input on the compositor. Only client-to-server
messages are sent; framebuffer updates are never requested.
"""

import socket
import struct
import time

HOST = "127.0.0.1"
PORT = 5901

# RFB button-mask bits.
BUTTON_LEFT = 1
BUTTON_MIDDLE = 2
BUTTON_RIGHT = 3
SCROLL_UP = 4
SCROLL_DOWN = 5
SCROLL_LEFT = 6
SCROLL_RIGHT = 7

_BUTTON_BITS = {1: 0x01, 2: 0x02, 3: 0x04, 4: 0x08, 5: 0x10, 6: 0x20, 7: 0x40}


class VNCError(RuntimeError):
    pass


def _recv_all(sock, count):
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise VNCError("wayvnc closed the connection")
        data += chunk
    return data


class VNC:
    def __init__(self, host=HOST, port=PORT):
        self.sock = socket.create_connection((host, port), timeout=5)
        version = _recv_all(self.sock, 12)
        if not version.startswith(b"RFB "):
            raise VNCError("Unexpected VNC handshake: %r" % version)
        self.sock.sendall(version)
        count = _recv_all(self.sock, 1)[0]
        if count == 0:
            reason_len = struct.unpack(">I", _recv_all(self.sock, 4))[0]
            raise VNCError("VNC refused connection: " +
                           _recv_all(self.sock, reason_len).decode("utf-8", "replace"))
        types = _recv_all(self.sock, count)
        if 1 not in types:
            raise VNCError("wayvnc requires authentication; input injection needs security type None")
        self.sock.sendall(bytes([1]))
        if struct.unpack(">I", _recv_all(self.sock, 4))[0] != 0:
            raise VNCError("VNC security handshake failed")
        self.sock.sendall(bytes([1]))  # ClientInit: shared session
        server_init = _recv_all(self.sock, 24)
        self.width, self.height = struct.unpack(">HH", server_init[:4])
        name_len = struct.unpack(">I", server_init[20:24])[0]
        if name_len:
            _recv_all(self.sock, name_len)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def pointer(self, x, y, mask=0):
        x = max(0, min(int(x), self.width - 1))
        y = max(0, min(int(y), self.height - 1))
        self.sock.sendall(struct.pack(">BBHH", 5, mask, x, y))

    def key(self, keysym, down=True):
        self.sock.sendall(struct.pack(">BBHI", 4, 1 if down else 0, 0, keysym))

    def click(self, x, y, button=BUTTON_LEFT, clicks=1):
        self.pointer(x, y, 0)
        bit = _BUTTON_BITS[button]
        for _ in range(clicks):
            self.pointer(x, y, bit)
            time.sleep(0.05)
            self.pointer(x, y, 0)
            time.sleep(0.08)

    def scroll(self, x, y, button, count=3):
        self.pointer(x, y, 0)
        bit = _BUTTON_BITS[button]
        for _ in range(count):
            self.pointer(x, y, bit)
            self.pointer(x, y, 0)
            time.sleep(0.05)

    def drag(self, x1, y1, x2, y2, button=BUTTON_LEFT, steps=12, super_held=False):
        bit = _BUTTON_BITS[button]
        if super_held:
            # XK_Super_L — held so tiling compositors treat this as a window move.
            self.key(0xFFEB, down=True)
        self.pointer(x1, y1, 0)
        time.sleep(0.05)
        self.pointer(x1, y1, bit)
        for i in range(1, steps + 1):
            x = x1 + (x2 - x1) * i / steps
            y = y1 + (y2 - y1) * i / steps
            self.pointer(x, y, bit)
            time.sleep(0.03)
        self.pointer(x2, y2, 0)
        if super_held:
            self.key(0xFFEB, down=False)
