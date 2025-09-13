#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inspect_header.py — ตัวตรวจ/อ่าน Header ของไฟล์ไบนารีในระบบ CarRent-BinIO

การใช้งาน:
    python inspect_header.py data/cars.bin
    python inspect_header.py data/customers.bin data/contracts.bin
    python inspect_header.py --json data/cars.bin

คุณสมบัติ:
- อ่าน 128 ไบต์แรก (Header) และถอดด้วย struct
- แสดงค่า magic, version, endianness, record_size, created/updated, next_id,
  สถิติ active/deleted, free_head, index_slots
- คำนวณจำนวนระเบียนทั้งหมดโดยดูจากขนาดไฟล์จริง
- รองรับการพิมพ์แบบ JSON (--json)

หมายเหตุ: ไฟล์ที่สร้างจากโค้ดโปรเจกต์นี้ใช้ Little-Endian (endianness = 0)
"""
from __future__ import annotations
import argparse, os, sys, json, struct
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Tuple, Dict

# รูปแบบ Header (ความยาวคงที่ 128 ไบต์)
BASE_FMT = '4s B B H I I I I I i I 92x'  # ไม่ใส่ endianness ที่นี่ จะเติมทีหลัง
HEADER_SIZE = 128
INDEX_SLOT_SIZE = 16

MAGIC_NAME = {b'CUST': 'customers', b'CARS': 'cars', b'CONT': 'contracts'}
ENDIAN_NAME = {0: 'Little-Endian', 1: 'Big-Endian'}

@dataclass
class Header:
    magic: bytes
    version: int
    endian: int
    record_size: int
    created_at: int
    updated_at: int
    next_id: int
    active_count: int
    deleted_count: int
    free_head: int
    index_slots: int

    @classmethod
    def unpack(cls, raw: bytes, endian_prefix: str) -> 'Header':
        vals = struct.unpack(endian_prefix + BASE_FMT, raw)
        # หมายเหตุ: padding ("92x") ไม่ถูกคืนค่าจาก struct.unpack อยู่แล้ว
        # ดังนั้นไม่ต้องตัดค่าใด ๆ ออก ให้ส่งทั้งหมดเข้า __init__ ตรง ๆ
        return cls(*vals)

    def to_dict(self) -> Dict:
        d = asdict(self)
        d['magic'] = self.magic.decode('ascii', 'ignore')
        d['endianness_str'] = ENDIAN_NAME.get(self.endian, f'unknown({self.endian})')
        d['created_at_str'] = datetime.fromtimestamp(self.created_at).isoformat(sep=' ', timespec='seconds')
        d['updated_at_str'] = datetime.fromtimestamp(self.updated_at).isoformat(sep=' ', timespec='seconds')
        d['table'] = MAGIC_NAME.get(self.magic, 'unknown')
        return d


def read_header(path: str) -> Tuple[Header, int, int]:
    """อ่าน Header และคืนค่า (header, file_size, total_records_approx)."""
    with open(path, 'rb') as f:
        raw = f.read(HEADER_SIZE)
        if len(raw) != HEADER_SIZE:
            raise RuntimeError('ไฟล์สั้นเกินไป ไม่พบ Header ครบ 128 ไบต์')
        # ลองถอดแบบ LE ก่อน (ตามสเปกของระบบ)
        h = Header.unpack(raw, '<')
        if h.endian == 1:
            # ถ้าเขียนแบบ Big-Endian มา ให้ถอดใหม่อีกครั้ง
            h = Header.unpack(raw, '>')
        f.seek(0, os.SEEK_END)
        size = f.tell()
    # คำนวณจำนวนเรคคอร์ดจากขนาดไฟล์จริง
    records_region = HEADER_SIZE + h.index_slots * INDEX_SLOT_SIZE
    total_records = (size - records_region) // h.record_size if size >= records_region else 0
    return h, size, total_records


def print_pretty(path: str, h: Header, size: int, total_records: int) -> None:
    d = h.to_dict()
    print(f"\nFile : {path}")
    print(f"Type : {d['table']}  (magic={d['magic']})")
    print(f"Endian : {d['endianness_str']}")
    print(f"Record Size : {h.record_size} bytes")
    print(f"Index Slots : {h.index_slots}  (slot size = {INDEX_SLOT_SIZE} bytes)")
    print(f"Created : {d['created_at_str']}")
    print(f"Updated : {d['updated_at_str']}")
    print(f"Next ID : {h.next_id}")
    print(f"Active : {h.active_count}    Deleted : {h.deleted_count}")
    print(f"Free-list Head : {h.free_head}")
    print(f"File Size : {size} bytes  |  Approx Records : {total_records}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='Inspect CarRent-BinIO header (128 bytes)')
    ap.add_argument('paths', nargs='+', help='ไฟล์ .bin ที่ต้องการอ่าน header')
    ap.add_argument('--json', action='store_true', help='พิมพ์ผลลัพธ์เป็น JSON')
    args = ap.parse_args(argv)
    out_all = []
    for p in args.paths:
        try:
            h, size, total = read_header(p)
            if args.json:
                dd = h.to_dict(); dd.update({'file': p, 'file_size': size, 'total_records': int(total)})
                out_all.append(dd)
            else:
                print_pretty(p, h, size, total)
        except Exception as e:
            if args.json:
                out_all.append({'file': p, 'error': str(e)})
            else:
                print(f"\nFile : {p}\n! Error : {e}")
    if args.json:
        print(json.dumps(out_all, ensure_ascii=False, indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
