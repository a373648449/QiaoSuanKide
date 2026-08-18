# -*- coding: utf-8 -*-
"""讯飞在线语音合成（Websocket）。密钥从环境变量读取。"""
from __future__ import print_function

import base64
import hashlib
import hmac
import json
import os
import socket
import ssl
import struct
from email.utils import formatdate

try:
    from urllib.parse import urlencode
except ImportError:
    from urllib import urlencode

HOST_NAME = "tts-api.xfyun.cn"
WS_PATH = "/v2/tts"
MAX_TEXT = 200


def _recv_exact(sock, n, buf):
    while len(buf) < n:
        chunk = sock.recv(4096)
        if not chunk:
            raise IOError("tts socket closed")
        buf += chunk
    return buf[:n], buf[n:]


def _mask(payload):
    key = os.urandom(4)
    masked = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return key + masked


def ws_send(sock, opcode, payload):
    n = len(payload)
    hdr = bytearray([0x80 | opcode])
    if n < 126:
        hdr.append(0x80 | n)
    elif n < 65536:
        hdr.append(0x80 | 126)
        hdr.extend(struct.pack("!H", n))
    else:
        hdr.append(0x80 | 127)
        hdr.extend(struct.pack("!Q", n))
    sock.sendall(bytes(hdr) + _mask(payload))


def ws_recv(sock, buf):
    collected = bytearray()
    opcode0 = None
    while True:
        hdr, buf = _recv_exact(sock, 2, buf)
        fin = hdr[0] & 0x80
        opcode = hdr[0] & 0x0F
        masked = hdr[1] & 0x80
        ln = hdr[1] & 0x7F
        if ln == 126:
            ext, buf = _recv_exact(sock, 2, buf)
            ln = struct.unpack("!H", ext)[0]
        elif ln == 127:
            ext, buf = _recv_exact(sock, 8, buf)
            ln = struct.unpack("!Q", ext)[0]
        mask = b""
        if masked:
            mask, buf = _recv_exact(sock, 4, buf)
        payload, buf = _recv_exact(sock, ln, buf)
        if masked:
            payload = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
        if opcode == 8:
            return 8, payload, buf
        if opcode == 9:
            ws_send(sock, 0xA, payload)
            continue
        if opcode == 10:
            continue
        if opcode0 is None:
            opcode0 = opcode
        collected.extend(payload)
        if fin:
            return opcode0, bytes(collected), buf


def assemble_url(api_key, api_secret):
    date = formatdate(usegmt=True)
    origin = "host: %s\ndate: %s\nGET %s HTTP/1.1" % (HOST_NAME, date, WS_PATH)
    digest = hmac.new(
        api_secret.encode("utf-8"),
        origin.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    signature = base64.b64encode(digest).decode("utf-8")
    auth = 'api_key="%s", algorithm="hmac-sha256", headers="host date request-line", signature="%s"' % (
        api_key,
        signature,
    )
    authorization = base64.b64encode(auth.encode("utf-8")).decode("utf-8")
    query = urlencode({"authorization": authorization, "date": date, "host": HOST_NAME})
    return WS_PATH + "?" + query


def handshake(path_qs, timeout):
    raw = socket.create_connection((HOST_NAME, 443), timeout=timeout)
    ctx = ssl.create_default_context()
    sock = ctx.wrap_socket(raw, server_hostname=HOST_NAME)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    req = (
        "GET %s HTTP/1.1\r\n"
        "Host: %s\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: %s\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    ) % (path_qs, HOST_NAME, key)
    sock.sendall(req.encode("ascii"))
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            sock.close()
            raise IOError("tts handshake closed")
        buf += chunk
    header, rest = buf.split(b"\r\n\r\n", 1)
    status = header.split(b"\r\n", 1)[0].decode("ascii", "replace")
    if " 101 " not in status:
        sock.close()
        raise IOError("tts handshake failed: %s" % header.decode("utf-8", "replace")[:400])
    return sock, rest


def synthesize(appid, api_key, api_secret, text, vcn="xiaoyan", speed=40, timeout=20):
    text = (text or "").strip()
    if not text:
        raise ValueError("empty text")
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT]
    path_qs = assemble_url(api_key, api_secret)
    sock, buf = handshake(path_qs, timeout)
    try:
        payload = {
            "common": {"app_id": appid},
            "business": {
                "aue": "lame",
                "sfl": 1,
                "tte": "UTF8",
                "vcn": vcn,
                "speed": int(speed),
                "volume": 70,
                "pitch": 50,
                "rdn": "0",
            },
            "data": {
                "status": 2,
                "text": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            },
        }
        ws_send(sock, 0x1, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        chunks = []
        while True:
            opcode, body, buf = ws_recv(sock, buf)
            if opcode == 8:
                break
            if opcode != 1:
                continue
            msg = json.loads(body.decode("utf-8"))
            code = msg.get("code")
            if code not in (0, None):
                raise IOError("tts error %s: %s" % (code, msg.get("message") or ""))
            data = msg.get("data") or {}
            audio = data.get("audio")
            if audio:
                chunks.append(base64.b64decode(audio))
            if data.get("status") == 2:
                break
        if not chunks:
            raise IOError("tts empty audio")
        return b"".join(chunks)
    finally:
        try:
            sock.close()
        except Exception:
            pass
