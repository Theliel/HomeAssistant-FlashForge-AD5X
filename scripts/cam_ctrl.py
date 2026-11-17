#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FlashForge AD5X — V4L2 UVC camera control utility (pure Python stdlib).
Permite consultar y ajustar exposición, ganancia (ISO), brillo, contraste
y nitidez directamente sobre /dev/video0 sin tocar firmwareExe.
Llamada sin parámetros, muestras las propiedades, o se puiede llamar directamente para modificarlas:
/usr/prog/Python-3.8.2/bin/python3 /usr/data/cam_ctrl.py sharpness=2
o usando el ID:
/usr/prog/Python-3.8.2/bin/python3 /usr/data/cam_ctrl.py 0x0098091a=5000
"""

import fcntl
import json
import os
import struct
import sys

DEV_DEFAULT = "/dev/video0"

VIDIOC_QUERYCTRL = 0xc0445624
VIDIOC_G_CTRL    = 0xc008561b
VIDIOC_S_CTRL    = 0xc008561c

V4L2_CTRL_FLAG_NEXT_CTRL = 0x80000000
V4L2_CTRL_FLAG_DISABLED  = 0x0001
V4L2_CTRL_FLAG_READ_ONLY = 0x0004
V4L2_CTRL_FLAG_INACTIVE  = 0x0010


def list_ctrls(fd):
    ctrls = []
    cid = V4L2_CTRL_FLAG_NEXT_CTRL
    while True:
        buf = bytearray(68)
        struct.pack_into("<I", buf, 0, cid)
        try:
            fcntl.ioctl(fd, VIDIOC_QUERYCTRL, buf, True)
        except OSError:
            break

        cur_id, ctype = struct.unpack_from("<II", buf, 0)
        name = buf[8:40].split(b"\x00", 1)[0].decode("latin1")
        cmin, cmax, cstep, cdef = struct.unpack_from("<iiii", buf, 40)
        flags, = struct.unpack_from("<I", buf, 56)
        cid = cur_id | V4L2_CTRL_FLAG_NEXT_CTRL

        if flags & V4L2_CTRL_FLAG_DISABLED:
            continue

        val = None
        if ctype in (1, 2, 3):
            gbuf = bytearray(8)
            struct.pack_into("<I", gbuf, 0, cur_id)
            try:
                fcntl.ioctl(fd, VIDIOC_G_CTRL, gbuf, True)
                val, = struct.unpack_from("<i", gbuf, 4)
            except OSError:
                pass

        ctrls.append({
            "id": cur_id,
            "hex": f"0x{cur_id:08x}",
            "name": name,
            "type": ctype,
            "min": cmin,
            "max": cmax,
            "step": cstep,
            "default": cdef,
            "flags": flags,
            "inactive": bool(flags & V4L2_CTRL_FLAG_INACTIVE),
            "val": val,
        })
    return ctrls


def set_ctrl(fd, cid, val):
    sbuf = bytearray(8)
    struct.pack_into("<Ii", sbuf, 0, cid, val)
    fcntl.ioctl(fd, VIDIOC_S_CTRL, sbuf, True)


def apply_setting(fd, ctrls, key, val):
    key_l = key.lower().strip()
    hex_id = None
    if key_l.startswith("0x"):
        try:
            hex_id = int(key_l, 16)
        except ValueError:
            pass

    matched = (
        [c for c in ctrls if hex_id is not None and c["id"] == hex_id]
        or [c for c in ctrls if key_l == c["name"].lower()]
        or [c for c in ctrls if key_l in c["name"].lower()]
    )
    if not matched:
        print(f"[ERROR] Control no encontrado: {key}")
        return False

    c = matched[0]
    if val < c["min"] or val > c["max"]:
        print(f"[AVISO] {val} fuera de rango para '{c['name']}' ({c['min']}..{c['max']})")

    try:
        set_ctrl(fd, c["id"], val)
        print(f"[OK] {c['name']} (0x{c['id']:08x}) -> {val}")
        return True
    except OSError as e:
        print(f"[ERROR] No se pudo fijar '{c['name']}' = {val}: {e}")
        return False


def main():
    if not os.path.exists(DEV_DEFAULT):
        sys.exit(f"No se encuentra {DEV_DEFAULT}")

    fd = os.open(DEV_DEFAULT, os.O_RDWR | os.O_NONBLOCK)
    try:
        ctrls = list_ctrls(fd)

        if len(sys.argv) == 2 and sys.argv[1] == "--json":
            out = {c["hex"]: c["val"] for c in ctrls if c["val"] is not None}
            print(json.dumps(out))
            return

        if len(sys.argv) == 1:
            print(f"{'ID (hex)':<12} {'Control':<32} {'Actual':>7} {'Min':>6} {'Max':>6} {'Step':>5} {'Def':>6}  Estado")
            print("-" * 92)
            for c in ctrls:
                st = []
                if c["flags"] & V4L2_CTRL_FLAG_READ_ONLY:
                    st.append("RO")
                if c["inactive"]:
                    st.append("INACTIVO")
                val_s = str(c["val"]) if c["val"] is not None else "-"
                print(
                    f"0x{c['id']:08x}   {c['name']:<32} {val_s:>7} "
                    f"{c['min']:>6} {c['max']:>6} {c['step']:>5} {c['default']:>6}  {','.join(st)}"
                )
            return

        args = sys.argv[1:]
        if len(args) == 2 and "=" not in args[0]:
            apply_setting(fd, ctrls, args[0], int(args[1]))
        else:
            for item in args:
                if "=" not in item:
                    sys.exit("Formato inválido. Usa: python3 cam_ctrl.py nombre=valor [nombre2=valor2 ...]")
                k, v = item.split("=", 1)
                apply_setting(fd, ctrls, k, int(v))
                ctrls = list_ctrls(fd)
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()