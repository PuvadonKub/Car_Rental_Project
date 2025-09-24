#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CarRent-BinIO (clean)
- 3 ไฟล์ไบนารี customers.bin / cars.bin / contracts.bin
- fixed-length records + struct (endianness '<') + header 128B + index 16B/slot
- index แบบ open addressing + free-list + soft delete (flag=0)
- เมนู CRUD/VIEW/REPORT (ไม่มี seed_demo — ใช้สคริปต์ seed แยก)

เข้ากันได้กับ seed_sample_data.py (ฟอร์แมต identical)
"""
from __future__ import annotations
import os, sys, struct, argparse
from dataclasses import dataclass
from datetime import datetime, date
from typing import Optional, Iterable, Tuple, Dict, Any

# ----------------------------
# สเปกไฟล์/บันทัดฐาน
# ----------------------------
E = '<'                               # little-endian
HEADER_SIZE = 128
INDEX_SLOT_SIZE = 16
HEADER_FMT = E + '4s B B H I I I I I i I 92x'   # 128B
INDEX_FMT  = E + 'I I 8x'                        # 16B
TOMBSTONE_KEY = 0xFFFFFFFF                       # ทำ tombstone ตอนลบ index slot

# Record format (ต้องตรงกับสคริปต์ seed)
CUST_FMT = E + 'B I 13s 50s 10s I B 45x'; CUST_SIZE=128; CUST_PAD=83
CARS_FMT = E + 'B I 12s 12s 16s H I I B I 68x'; CARS_SIZE=128; CARS_PAD=60
CONT_FMT = E + 'B I I I I I I B 38x';           CONT_SIZE=64;  CONT_PAD=26

CAR_STATUS = {0:'available', 1:'rented', 2:'maintenance', 3:'retired'}
CAR_STATUS_REV = {v:k for k,v in CAR_STATUS.items()}

# ----------------------------
# ยูทิลิตี้
# ----------------------------
now_ts = lambda: int(datetime.now().timestamp())

def fit(s: str, n: int) -> bytes:
    return (s or '').encode('utf-8','ignore')[:n].ljust(n, b'\x00')

def ymd_to_int(s: str) -> int:
    if not s: return 0
    y,m,d = map(int, s.split('-'))
    return y*10000 + m*100 + d

def int_to_ymd(n: int) -> str:
    if not n: return '-'
    y=n//10000; m=(n//100)%100; d=n%100
    return f"{y:04d}-{m:02d}-{d:02d}"

def ensure_dir(p: str) -> None:
    if p and not os.path.isdir(p): os.makedirs(p, exist_ok=True)

# ----------------------------
# Header/Index โครงสร้าง
# ----------------------------
@dataclass
class Header:
    magic: bytes; version: int; endian: int; record_size: int
    created_at: int; updated_at: int; next_id: int
    active_count: int; deleted_count: int; free_head: int; index_slots: int
    def pack(self) -> bytes:
        return struct.pack(HEADER_FMT, self.magic, self.version, self.endian,
                           self.record_size, self.created_at, self.updated_at,
                           self.next_id, self.active_count, self.deleted_count,
                           self.free_head, self.index_slots)
    @classmethod
    def unpack(cls, b: bytes) -> 'Header':
        (magic, ver, ed, rsz, c_at, u_at, nid, ac, dc, fh, slots) = struct.unpack(HEADER_FMT, b)
        return cls(magic,ver,ed,rsz,c_at,u_at,nid,ac,dc,fh,slots)
    @classmethod
    def new(cls, magic: bytes, record_size: int, index_slots: int) -> 'Header':
        t = now_ts(); return cls(magic, 1, 0, record_size, t, t, 1, 0, 0, -1, index_slots)

@dataclass
class IndexSlot:
    key: int; rec_index: int
    def pack(self) -> bytes: return struct.pack(INDEX_FMT, self.key, self.rec_index)
    @classmethod
    def unpack(cls, b: bytes) -> 'IndexSlot':
        k,ri = struct.unpack(INDEX_FMT, b); return cls(k,ri)

# ----------------------------
# ชั้นตารางไบนารี (ทั่วไป)
# ----------------------------
class BinTable:
    def __init__(self, path: str, magic: bytes, rsize: int, rfmt: str, slots: int, pad_off: int):
        self.path=path; self.magic=magic; self.rsize=rsize; self.rfmt=rfmt
        self.slots=slots; self.pad_off=pad_off
        self.f=None; self.h: Optional[Header]=None

    # --- file lifecycle ---
    def open(self) -> None:
        new = not os.path.exists(self.path)
        self.f = open(self.path, 'w+b' if new else 'r+b')
        if new:
            self.h = Header.new(self.magic, self.rsize, self.slots)
            self.f.seek(0); self.f.write(self.h.pack())
            for _ in range(self.slots): self.f.write(IndexSlot(0,0).pack())
            self._sync()
        else:
            self.f.seek(0); self.h = Header.unpack(self.f.read(HEADER_SIZE))
            if self.h.magic != self.magic or self.h.record_size != self.rsize:
                raise RuntimeError(f"bad file format for {self.path}")

    def close(self) -> None:
        if self.f: self.f.flush(); os.fsync(self.f.fileno()); self.f.close(); self.f=None

    def _sync(self) -> None:
        self.f.flush(); os.fsync(self.f.fileno())

    def _write_header(self) -> None:
        self.h.updated_at = now_ts(); self.f.seek(0); self.f.write(self.h.pack()); self._sync()

    # --- index helpers ---
    def _index_ofs(self, slot: int) -> int: return HEADER_SIZE + slot*INDEX_SLOT_SIZE
    def _read_slot(self, slot: int) -> IndexSlot:
        self.f.seek(self._index_ofs(slot)); return IndexSlot.unpack(self.f.read(INDEX_SLOT_SIZE))
    def _write_slot(self, slot: int, slotval: IndexSlot) -> None:
        self.f.seek(self._index_ofs(slot)); self.f.write(slotval.pack())
    def _hash(self, key: int) -> int: return key % self.h.index_slots

    def _find_slot_for_insert(self, key: int) -> int:
        """หา slot สำหรับ insert (reuse tombstone ถ้ามี)"""
        start = self._hash(key); first_tomb = -1
        for i in range(self.h.index_slots):
            j = (start + i) % self.h.index_slots
            sl = self._read_slot(j)
            if sl.key == key:
                raise ValueError('duplicate key')
            if sl.key == TOMBSTONE_KEY and first_tomb < 0:
                first_tomb = j
            if sl.key == 0:   # ว่างจริง
                return first_tomb if first_tomb >= 0 else j
        raise RuntimeError('index full')

    def _lookup(self, key: int) -> Optional[int]:
        start = self._hash(key)
        for i in range(self.h.index_slots):
            j = (start + i) % self.h.index_slots
            sl = self._read_slot(j)
            if sl.key == 0:   # เจอว่างจริง -> ไม่มีในตาราง
                return None
            if sl.key == key:
                return sl.rec_index
        return None

    def _slot_of_key(self, key: int) -> Optional[int]:
        start = self._hash(key)
        for i in range(self.h.index_slots):
            j = (start + i) % self.h.index_slots
            sl = self._read_slot(j)
            if sl.key == 0: return None
            if sl.key == key: return j
        return None

    # --- record space ---
    def _records_region_ofs(self) -> int: return HEADER_SIZE + self.h.index_slots*INDEX_SLOT_SIZE
    def _record_ofs(self, rec_index: int) -> int: return self._records_region_ofs() + rec_index*self.rsize
    def _records_count(self) -> int:
        self.f.seek(0, os.SEEK_END)
        payload = self.f.tell() - self._records_region_ofs()
        return 0 if payload <= 0 else payload // self.rsize
    def _read_raw(self, rec_index: int) -> bytes:
        self.f.seek(self._record_ofs(rec_index)); return self.f.read(self.rsize)
    def _write_raw(self, rec_index: int, data: bytes) -> None:
        assert len(data) == self.rsize
        self.f.seek(self._record_ofs(rec_index)); self.f.write(data)
    def _write_next_free(self, rec_index: int, next_free: int) -> None:
        self.f.seek(self._record_ofs(rec_index)+self.pad_off); self.f.write(struct.pack(E+'i', next_free))

    # --- CRUD ขั้นต่ำ ---
    def next_id(self) -> int:
        nid = self.h.next_id; self.h.next_id += 1; self._write_header(); return nid

    def _alloc_rec_index(self) -> int:
        if self.h.free_head != -1:
            i = self.h.free_head
            self.f.seek(self._record_ofs(i) + self.pad_off)
            nxt = struct.unpack(E+'i', self.f.read(4))[0]
            self.h.free_head = nxt
            return i
        return self._records_count()

    def add_record(self, key: int, packed: bytes) -> int:
        i = self._alloc_rec_index(); self._write_raw(i, packed)
        j = self._find_slot_for_insert(key); self._write_slot(j, IndexSlot(key, i))
        self.h.active_count += 1; self._write_header(); self._sync(); return i

    def read_record(self, key: int) -> Optional[bytes]:
        ri = self._lookup(key); return None if ri is None else self._read_raw(ri)

    def update_record(self, key: int, packed: bytes) -> None:
        ri = self._lookup(key)
        if ri is None: raise KeyError('not found')
        self._write_raw(ri, packed); self._write_header(); self._sync()

    def delete_record(self, key: int) -> None:
        ri = self._lookup(key)
        if ri is None: raise KeyError('not found')
        # mark record inactive (flag=0)
        rec = bytearray(self._read_raw(ri)); rec[0] = 0; self._write_raw(ri, bytes(rec))
        # push rec index to free-list
        self._write_next_free(ri, self.h.free_head); self.h.free_head = ri
        # tombstone index slot
        sj = self._slot_of_key(key)
        if sj is not None:
            self._write_slot(sj, IndexSlot(TOMBSTONE_KEY, 0))
        # header counters
        self.h.active_count -= 1; self.h.deleted_count += 1; self._write_header(); self._sync()

    # --- iterators ---
    def iter_active(self) -> Iterable[Tuple[int, bytes]]:
        for i in range(self._records_count()):
            raw = self._read_raw(i)
            if raw and raw[0] == 1: yield i, raw
    def iter_all(self) -> Iterable[Tuple[int, bytes]]:
        for i in range(self._records_count()):
            raw = self._read_raw(i)
            if raw: yield i, raw

# ----------------------------
# ตารางเฉพาะ
# ----------------------------
class Customers(BinTable):
    def __init__(self, path: str, slots: int = 1024):
        super().__init__(path, b'CUST', CUST_SIZE, CUST_FMT, slots, CUST_PAD)
    def pack(self, flag:int, cid:int, id_card:str, name:str, phone:str, birth_ymd:int, gender:int) -> bytes:
        return struct.pack(self.rfmt, flag, cid, fit(id_card,13), fit(name,50), fit(phone,10), birth_ymd, gender)
    def unpack(self, raw: bytes) -> Dict[str,Any]:
        f,cid,idc,nam,ph,birth,gen = struct.unpack(E+'B I 13s 50s 10s I B 45x', raw)
        dec=lambda b:b.rstrip(b'\x00').decode('utf-8','ignore')
        return {'flag':f,'cus_id':cid,'id_card':dec(idc),'name':dec(nam),'phone':dec(ph),'birth_ymd':birth,'gender':gen}

class Cars(BinTable):
    def __init__(self, path: str, slots: int = 1024):
        super().__init__(path, b'CARS', CARS_SIZE, CARS_FMT, slots, CARS_PAD)
    def pack(self, flag:int, car_id:int, plate:str, brand:str, model:str, year:int, rate_cents:int, odo_km:int, status:int, updated_at:int) -> bytes:
        return struct.pack(self.rfmt, flag, car_id, fit(plate,12), fit(brand,12), fit(model,16), year, rate_cents, odo_km, status, updated_at)
    def unpack(self, raw: bytes) -> Dict[str,Any]:
        f,car_id,pl,br,md,yr,rt,odo,st,up = struct.unpack(E+'B I 12s 12s 16s H I I B I 68x', raw)
        dec=lambda b:b.rstrip(b'\x00').decode('utf-8','ignore')
        return {'flag':f,'car_id':car_id,'license':dec(pl),'brand':dec(br),'model':dec(md),'year':yr,'rate_cents':rt,'odometer_km':odo,'status':st,'updated_at':up}

class Contracts(BinTable):
    def __init__(self, path: str, slots: int = 2048):
        super().__init__(path, b'CONT', CONT_SIZE, CONT_FMT, slots, CONT_PAD)
    def pack(self, flag:int, rid:int, cus_id:int, car_id:int, rent:int, ret:int, total:int, returned:int) -> bytes:
        return struct.pack(self.rfmt, flag, rid, cus_id, car_id, rent, ret, total, returned)
    def unpack(self, raw: bytes) -> Dict[str,Any]:
        f,rid,cus,car,rent,ret,tot,returned = struct.unpack(E+'B I I I I I I B 38x', raw)
        return {'flag':f,'rent_id':rid,'cus_id':cus,'car_id':car,'rent_ymd':rent,'return_ymd':ret,'total_cents':tot,'returned':returned}

# ----------------------------
# ตรวจสอบอินพุต
# ----------------------------
is_idcard = lambda s: s.isdigit() and len(s) == 13
is_phone  = lambda s: s.isdigit() and 9 <= len(s) <= 10
is_plate  = lambda s: 0 < len(s.strip()) <= 16
is_year   = lambda y: 1900 <= y <= datetime.now().year + 1

# ----------------------------
# แอปรายการคำสั่ง (CLI)
# ----------------------------
class App:
    def __init__(self, data_dir: str):
        ensure_dir(data_dir)
        self.customers = Customers(os.path.join(data_dir, 'customers.bin'))
        self.cars      = Cars(     os.path.join(data_dir, 'cars.bin'))
        self.contracts = Contracts(os.path.join(data_dir, 'contracts.bin'))

    # lifecycle
    def open(self): self.customers.open(); self.cars.open(); self.contracts.open()
    def close(self): self.customers.close(); self.cars.close(); self.contracts.close()

    # ---------- Add ----------
    def add_customer(self):
        name = input('ชื่อ: ').strip()
        idc  = input('เลขบัตร 13 หลัก: ').strip()
        phone= input('โทร (9-10 หลัก): ').strip()
        dob  = input('วันเกิด YYYY-MM-DD (เว้นได้): ').strip()
        gopt = (input('เพศ (unk/male/female) [unk]: ').strip() or 'unk').lower()
        gender = {'unk':0,'male':1,'female':2}.get(gopt,0)
        if not name or not is_idcard(idc) or not is_phone(phone):
            print('! ข้อมูลไม่ถูกต้อง'); return
        cid = self.customers.next_id()
        rec = self.customers.pack(1, cid, idc, name, phone, ymd_to_int(dob), gender)
        self.customers.add_record(cid, rec); print(f'+ เพิ่มลูกค้า id={cid}')

    def add_car(self):
        plate = input('ทะเบียน (<=16): ').strip()
        brand = input('ยี่ห้อ (<=12): ').strip()
        model = input('รุ่น (<=16): ').strip()
        try:
            year  = int(input('ปีผลิต: ').strip())
            rate  = float(input('ค่าเช่าต่อวัน (บาท): ').strip())
            odo   = int(input('เลขไมล์ (กม.): ').strip())
        except Exception:
            print('! ตัวเลขไม่ถูกต้อง'); return
        stat  = CAR_STATUS_REV.get((input('สถานะ [available]: ').strip() or 'available'), 0)
        if not is_plate(plate) or not is_year(year) or rate < 0 or odo < 0:
            print('! ข้อมูลไม่ถูกต้อง'); return
        car_id = self.cars.next_id(); rec = self.cars.pack(1, car_id, plate, brand, model, year, int(round(rate*100)), odo, stat, now_ts())
        self.cars.add_record(car_id, rec); print(f'+ เพิ่มรถ id={car_id}')

    def add_contract(self):
        try:
            cus_id = int(input('cus_id: '))
            car_id = int(input('car_id: '))
            rent   = ymd_to_int(input('วันที่เช่า YYYY-MM-DD: ').strip())
        except Exception:
            print('! อินพุตไม่ถูกต้อง'); return
        car_raw = self.cars.read_record(car_id)
        if not car_raw: print('! ไม่พบรถ'); return
        car = self.cars.unpack(car_raw)
        if car['status'] != 0: print('! รถไม่ว่าง'); return
        if not self.customers.read_record(cus_id): print('! ไม่พบลูกค้า'); return
        rid = self.contracts.next_id()
        self.contracts.add_record(rid, self.contracts.pack(1, rid, cus_id, car_id, rent, 0, 0, 0))
        self.cars.update_record(car_id, self.cars.pack(1, car['car_id'], car['license'], car['brand'], car['model'], car['year'], car['rate_cents'], car['odometer_km'], 1, now_ts()))
        print(f'+ เปิดสัญญา rent_id={rid}')

    # ---------- Update ----------
    def update_customer(self):
        try: cid = int(input('cus_id: '))
        except Exception: print('! อินพุตไม่ถูกต้อง'); return
        raw = self.customers.read_record(cid)
        if not raw: print('! ไม่พบลูกค้า'); return
        r = self.customers.unpack(raw)
        name = input(f"ชื่อ [{r['name']}]: ").strip() or r['name']
        idc  = input(f"บัตร13 [{r['id_card']}]: ").strip() or r['id_card']
        phone= input(f"โทร [{r['phone']}]: ").strip() or r['phone']
        dob  = input(f"เกิด YYYY-MM-DD [{int_to_ymd(r['birth_ymd'])}]: ").strip()
        g    = input('เพศ (unk/male/female) [คงเดิม]: ').strip().lower()
        gender = {'unk':0,'male':1,'female':2}.get(g, r['gender'])
        if not name or not is_idcard(idc) or not is_phone(phone): print('! ข้อมูลไม่ถูกต้อง'); return
        self.customers.update_record(cid, self.customers.pack(1, cid, idc, name, phone, (r['birth_ymd'] if not dob else ymd_to_int(dob)), gender))
        print('* อัปเดตลูกค้าแล้ว')

    def update_car(self):
        try: car_id = int(input('car_id: '))
        except Exception: print('! อินพุตไม่ถูกต้อง'); return
        raw = self.cars.read_record(car_id)
        if not raw: print('! ไม่พบรถ'); return
        r = self.cars.unpack(raw)
        plate = input(f"ทะเบียน [{r['license']}]: ").strip() or r['license']
        brand = input(f"ยี่ห้อ [{r['brand']}]: ").strip() or r['brand']
        model = input(f"รุ่น [{r['model']}]: ").strip() or r['model']
        try:
            year  = int(input(f"ปีผลิต [{r['year']}]: ") or r['year'])
            rateb = float(input(f"ค่าเช่าบาท [{r['rate_cents']/100:.2f}]: ") or r['rate_cents']/100)
            odo   = int(input(f"เลขไมล์ [{r['odometer_km']}]: ") or r['odometer_km'])
        except Exception:
            print('! ตัวเลขไม่ถูกต้อง'); return
        stat  = CAR_STATUS_REV.get(input(f"สถานะ ({'/'.join(CAR_STATUS.values())}) [{CAR_STATUS[r['status']]}]: ").strip() or CAR_STATUS[r['status']], r['status'])
        if not is_plate(plate) or not is_year(year) or rateb < 0 or odo < 0: print('! ข้อมูลไม่ถูกต้อง'); return
        self.cars.update_record(car_id, self.cars.pack(1, car_id, plate, brand, model, year, int(round(rateb*100)), odo, stat, now_ts()))
        print('* อัปเดตรถแล้ว')
        if stat == 3:  # retired
            has_open = False
            for _, rawc in self.contracts.iter_active():
                cc = self.contracts.unpack(rawc)
                if cc['car_id'] == car_id and cc['returned'] == 0:
                    has_open = True
                    break
            if has_open:
                print('! รถคันนี้ยังมีสัญญาเช่าเปิดอยู่ ห้ามตั้งเป็น retired')
                return

    def return_car(self):
        try:
            rid = int(input('rent_id: '))
            ret = ymd_to_int(input('วันคืน YYYY-MM-DD: ').strip())
        except Exception:
            print('! อินพุตไม่ถูกต้อง'); return
        raw = self.contracts.read_record(rid)
        if not raw: print('! ไม่พบสัญญา'); return
        r = self.contracts.unpack(raw)
        if r['returned'] == 1: print('! ปิดสัญญาแล้ว'); return
        if ret < r['rent_ymd']: print('! วันที่ผิด'); return
        new_status = 0 if car['status'] == 1 else car['status']
        self.cars.update_record(
            car['car_id'],
            self.cars.pack(
                1, car['car_id'], car['license'], car['brand'], car['model'],
                car['year'], car['rate_cents'], car['odometer_km'],
                new_status, now_ts()))
        car = self.cars.unpack(self.cars.read_record(r['car_id']))
        def to_dt(n:int) -> date: return date(n//10000, (n//100)%100, n%100)
        days = (to_dt(ret) - to_dt(r['rent_ymd'])).days or 1
        total = days * car['rate_cents']
        self.contracts.update_record(rid, self.contracts.pack(1, r['rent_id'], r['cus_id'], r['car_id'], r['rent_ymd'], ret, total, 1))
        self.cars.update_record(car['car_id'], self.cars.pack(1, car['car_id'], car['license'], car['brand'], car['model'], car['year'], car['rate_cents'], car['odometer_km'], 0, now_ts()))
        print(f"* ปิดสัญญาแล้ว ยอด {total/100:.2f} บาท ({days} วัน)")

    # ---------- Delete ----------
    def delete_customer(self):
        try: cid = int(input('cus_id: '))
        except Exception: print('! อินพุตไม่ถูกต้อง'); return
        try: self.customers.delete_record(cid); print('- ลบลูกค้าแล้ว')
        except Exception as e: print('!',e)

    def delete_car(self):
        try: car_id = int(input('car_id: '))
        except Exception: print('! อินพุตไม่ถูกต้อง'); return
        raw = self.cars.read_record(car_id)
        if not raw: print('! ไม่พบรถ'); return
        if self.cars.unpack(raw)['status'] == 1:
            print('! รถกำลังเช่า ลบไม่ได้'); return
        try: self.cars.delete_record(car_id); print('- ลบรถแล้ว')
        except Exception as e: print('!',e)
        

    def delete_contract(self):
        try: rid = int(input('rent_id: '))
        except Exception: print('! อินพุตไม่ถูกต้อง'); return
        try: self.contracts.delete_record(rid); print('- ลบสัญญาแล้ว')
        except Exception as e: print('!',e)

    # ---------- View ----------
    def view_single(self):
        t = input('ชนิด (customer/car/contract, 0=Back): ').strip().lower()
        if t in ('', '0', 'b', 'back'):
            return
        try: i = int(input('id: '))
        except Exception: print('! อินพุตไม่ถูกต้อง'); return
        if t.startswith('cust'):
            raw = self.customers.read_record(i); 
            if not raw: print('! ไม่พบ'); return
            r = self.customers.unpack(raw)
            print(f"[Customer] id={r['cus_id']} name={r['name']} id_card={r['id_card']} phone={r['phone']} birth={int_to_ymd(r['birth_ymd'])} gender={r['gender']}")
        elif t.startswith('car'):
            raw = self.cars.read_record(i)
            if not raw: print('! ไม่พบ'); return
            r = self.cars.unpack(raw)
            print(f"[Car] id={r['car_id']} plate={r['license']} brand={r['brand']} model={r['model']} year={r['year']} rate={r['rate_cents']/100:.2f} status={CAR_STATUS[r['status']]}")
        else:
            raw = self.contracts.read_record(i)
            if not raw: print('! ไม่พบ'); return
            r = self.contracts.unpack(raw)
            print(f"[Contract] id={r['rent_id']} cus_id={r['cus_id']} car_id={r['car_id']} rent={int_to_ymd(r['rent_ymd'])} return={int_to_ymd(r['return_ymd'])} total={r['total_cents']/100:.2f} returned={r['returned']}")

    def view_all(self):
        t = input('ชนิด (customer/car/contract, 0=Back): ').strip().lower()
        if t in ('', '0', 'b', 'back'):
            return
        if t.startswith('cust'):
            for _, raw in self.customers.iter_active():
                r = self.customers.unpack(raw)
                print(f"{r['cus_id']:>4} | {r['name']:<24} | {r['phone']} | {r['birth_ymd']} | {r['gender']}")
        elif t.startswith('car'):
            for _, raw in self.cars.iter_active():
                r = self.cars.unpack(raw)
                print(f"{r['car_id']:>4} | {r['license']:<10} | {r['brand']:<10} | {r['model']:<10} | "
                    f"{r['year']} | {r['rate_cents']/100:<10.2f} | {CAR_STATUS[r['status']]:<10}")
        elif t.startswith('cont'):
            for _, raw in self.contracts.iter_active():
                r = self.contracts.unpack(raw)
                print(f"{r['rent_id']:>4} | cus={r['cus_id']:<3} car={r['car_id']:<3} | "
                    f"{int_to_ymd(r['rent_ymd'])}->{int_to_ymd(r['return_ymd'])} | {r['total_cents']/100:.2f}")
        else:
            print("เขียนไม่ถูกต้อง")

    def view_filter(self):
        t = input('ชนิด (customer/car/contract, 0=Back): ').strip().lower()
        if t in ('', '0', 'b', 'back'):
            return
        if t.startswith('cust'):
            q = input('ค้นหาชื่อ: ').strip().lower()
            for _, raw in self.customers.iter_active():
                r = self.customers.unpack(raw)
                if q in r['name'].lower():
                    print(f"{r['cus_id']:>4} | {r['name']}")

        elif t.startswith('car'):
            raw_in = input('สถานะ (available/rented/maintenance/retired หรือเว้นว่าง): ').strip().lower()
            st_code = None  # None = ไม่กรอง
            if raw_in:
                if raw_in.isdigit():
                    v = int(raw_in)
                    if v in CAR_STATUS:
                        st_code = v
                else:
                    # ชื่อเต็มก่อน
                    exact = [code for code, label in CAR_STATUS.items() if label == raw_in]
                    if exact:
                        st_code = exact[0]
                    else:
                        # prefix match (avai/ren/main/ret)
                        matched = [code for code, label in CAR_STATUS.items() if label.startswith(raw_in)]
                        if len(matched) == 1:
                            st_code = matched[0]
                        elif len(matched) > 1:
                            print("คำค้นกำกวม: ", ', '.join(CAR_STATUS[m] for m in matched))
                            return
            for _, raw in self.cars.iter_active():
                r = self.cars.unpack(raw)
                if st_code is None or r['status'] == st_code:
                    print(f"{r['car_id']:>4} | {r['license']:<10} | {r['brand']:<10} | {r['model']:<10} | "
                        f"{r['year']} | {r['rate_cents']/100:<10.2f} | {CAR_STATUS[r['status']]:<10}")
        elif t.startswith('cont'):
            try:
                a_str, b_str = input('ช่วง FROM,TO (YYYY-MM-DD,YYYY-MM-DD): ').split(',')
                a, b = ymd_to_int(a_str.strip()), ymd_to_int(b_str.strip())
            except Exception:
                print('รูปแบบวันที่ไม่ถูกต้อง'); return
            for _, raw in self.contracts.iter_active():
                r = self.contracts.unpack(raw)
                if a <= r['rent_ymd'] <= b:
                    print(f"{r['rent_id']:>4} | {int_to_ymd(r['rent_ymd'])}")
        else:
            print("เขียนไม่ถูกต้อง")


    def view_stats(self):
        cnt = {k:0 for k in CAR_STATUS}
        for _,raw in self.cars.iter_active():
            cnt[self.cars.unpack(raw)['status']] += 1
        print('Cars by status:')
        for k,v in cnt.items(): print(f"  {CAR_STATUS[k]} = {v}")
        open_ct = sum(1 for _,raw in self.contracts.iter_active() if self.contracts.unpack(raw)['returned']==0)
        print('Open contracts =', open_ct)

    # ---------- Report ----------
    def generate_report(self, out_path: str):
        # --- รวบรวม open contracts -> car_id -> contract ล่าสุด ---
        open_by_car = {}
        for _, raw in self.contracts.iter_active():
            c = self.contracts.unpack(raw)
            if c['returned'] == 0:
                prev = open_by_car.get(c['car_id'])
                if (prev is None) or (c['rent_ymd'] > prev['rent_ymd']):
                    open_by_car[c['car_id']] = c

        # cache ชื่อลูกค้า ลดการอ่านไฟล์ซ้ำ
        _name_cache = {}
        def customer_name(cus_id: int) -> str:
            if cus_id in _name_cache: 
                return _name_cache[cus_id]
            raw = self.customers.read_record(cus_id)
            if not raw:
                _name_cache[cus_id] = f"cus#{cus_id}"
            else:
                _name_cache[cus_id] = self.customers.unpack(raw)['name']
            return _name_cache[cus_id]

        lines = []
        ts = datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S (%z)')
        lines += [
            'Car Rent System — Summary Report (Sample)',
            f'Generated At : {ts}',
            'App Version  : 1.0',
            'Endianness   : Little-Endian',
            'Encoding     : UTF-8 (fixed-length)',
            ''
        ]

        # เพิ่มคอลัมน์ Renter
        th = (f"{'CarID':>5} | {'Plate':<10} | {'Brand':<10} | {'Model':<10} | "
            f"{'Year':>4} | {'Rate (THB/day)':>14} | {'Record':<7} | {'Rented':<3} | {'Renter':<20}")
        lines += [th, '-' * len(th)]

        total = active = deleted = rented = avail = 0
        rates = []
        by_brand = {}

        for _, raw in self.cars.iter_all():
            total += 1
            c = self.cars.unpack(raw)
            is_active = (raw[0] == 1)

            record_state = 'Active' if is_active else 'Deleted'
            is_rented_now = (is_active and c['status'] == 1)

            renter_name = ''
            if is_rented_now:
                oc = open_by_car.get(c['car_id'])
                renter_name = customer_name(oc['cus_id']) if oc else '(unknown)'

            lines.append(
                f"{c['car_id']:>5} | {c['license']:<10.10} | {c['brand']:<10.10} | {c['model']:<10.10} | "
                f"{c['year']:>4} | {c['rate_cents']/100:>14.2f} | {record_state:<7} | "
                f"{'Yes' if is_rented_now else 'No':<3} | {renter_name:<20.20}"
            )

            if is_active:
                active += 1
                rates.append(c['rate_cents'])
                by_brand[c['brand']] = by_brand.get(c['brand'], 0) + 1
                if is_rented_now:
                    rented += 1
                if c['status'] == 0:
                    avail += 1

        deleted = total - active

        lines += [
            '',
            'Summary (นับเฉพาะสถานะ Active)',
            f'- Total Cars (records) : {total}',
            f'- Active Cars          : {active}',
            f'- Deleted Cars         : {deleted}',
            f'- Currently Rented     : {rented}',
            f'- Available Now        : {avail}',
            ''
        ]

        if rates:
            lines += [
                'Rate Statistics (THB/day, Active only)',
                f"- Min : {min(rates)/100:,.2f}",
                f"- Max : {max(rates)/100:,.2f}",
                f"- Avg : {(sum(rates)/len(rates))/100:,.2f}",
                ''
            ]
        else:
            lines += [
                'Rate Statistics (THB/day, Active only)',
                '- Min : 0.00', '- Max : 0.00', '- Avg : 0.00', ''
            ]

        lines.append('Cars by Brand (Active only)')
        if by_brand:
            for b in sorted(by_brand):
                lines.append(f"- {b} : {by_brand[b]}")
        else:
            lines.append('(no active cars)')

        # --- สรุปรายการที่เช่าอยู่พร้อมชื่อผู้เช่า ---
        lines += ['', 'Open Rentals (รายละเอียด)']
        if open_by_car:
            lines.append(f"{'RentID':>6} | {'CarID':>5} | {'Plate':<10} | {'Customer':<24} | {'Rent Date':<10}")
            lines.append('-' * 64)
            for car_id, oc in sorted(open_by_car.items(), key=lambda kv: (kv[1]['rent_ymd'], kv[0])):
                car_raw = self.cars.read_record(car_id)
                plate = self.cars.unpack(car_raw)['license'] if car_raw else f"car#{car_id}"
                cname = customer_name(oc['cus_id'])
                lines.append(f"{oc['rent_id']:>6} | {car_id:>5} | {plate:<10.10} | {cname:<24.24} | {int_to_ymd(oc['rent_ymd']):<10}")
        else:
            lines.append('(none)')

        with open(out_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        print('* เขียนรายงานที่', out_path)

    # ---------- Menu ----------
    def run(self):
        while True:
            print("\n===== CarRent-BinIO =====")
            print("1) Add  2) Update  3) Delete  4) View  5) Report  0) Exit")
            c = (input('เลือก: ') or '0').strip()
            try:
                if c == '1':
                    # --- Add submenu ---
                    while True:
                        print("\n[Add] 1) Customer 2) Car 3) Contract  0) Back")
                        ch = input('เลือก: ').strip().lower()
                        if ch in ('0', 'b', 'back'): break
                        {'1': self.add_customer,
                        '2': self.add_car,
                        '3': self.add_contract}.get(ch, lambda: print('ตัวเลือกไม่ถูกต้อง'))()

                elif c == '2':
                    # --- Update submenu ---
                    while True:
                        print("\n[Update] 1) Customer 2) Car 3) Return Car  0) Back")
                        ch = input('เลือก: ').strip().lower()
                        if ch in ('0', 'b', 'back'): break
                        {'1': self.update_customer,
                        '2': self.update_car,
                        '3': self.return_car}.get(ch, lambda: print('ตัวเลือกไม่ถูกต้อง'))()

                elif c == '3':
                    # --- Delete submenu ---
                    while True:
                        print("\n[Delete] 1) Customer 2) Car 3) Contract  0) Back")
                        ch = input('เลือก: ').strip().lower()
                        if ch in ('0', 'b', 'back'): break
                        {'1': self.delete_customer,
                        '2': self.delete_car,
                        '3': self.delete_contract}.get(ch, lambda: print('ตัวเลือกไม่ถูกต้อง'))()

                elif c == '4':
                    # --- View submenu ---
                    while True:
                        print("\n[View] 1) เดี่ยว 2) ทั้งหมด 3) กรอง 4) สถิติ  0) Back")
                        ch = input('เลือก: ').strip().lower()
                        if ch in ('0', 'b', 'back'): break
                        {'1': self.view_single,
                        '2': self.view_all,
                        '3': self.view_filter,
                        '4': self.view_stats}.get(ch, lambda: print('ตัวเลือกไม่ถูกต้อง'))()

                elif c == '5':
                    out = os.path.join(os.path.dirname(self.customers.path), 'report.txt')
                    self.generate_report(out)

                elif c == '0':
                    out = os.path.join(os.path.dirname(self.customers.path), 'report.txt')
                    self.generate_report(out)
                    print('บันทึกและออก...')
                    self.close()
                    break

                else:
                    print('ตัวเลือกไม่ถูกต้อง')

            except Exception as e:
                print('! error:', e)


# ----------------------------
# main
# ----------------------------
def main(argv=None) -> int:
    ap=argparse.ArgumentParser(description='CarRent-BinIO (clean)')
    ap.add_argument('--data-dir', default='data', help='โฟลเดอร์เก็บ .bin/.txt')
    args=ap.parse_args(argv)
    ensure_dir(args.data_dir)
    app=App(args.data_dir)
    try:
        app.open(); app.run(); return 0
    finally:
        app.close()

if __name__=='__main__':
    sys.exit(main())
