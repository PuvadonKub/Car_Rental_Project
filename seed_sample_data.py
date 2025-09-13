#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
seed_sample_data.py — เติมข้อมูลตัวอย่างลงไฟล์ไบนารี (standalone, ไม่พึ่งโมดูลอื่น)
รองรับไฟล์สเปกเดียวกับ CarRent-BinIO:
- customers.bin, cars.bin, contracts.bin
- fixed-length records + struct + header/index แบบเดียวกัน

การใช้งานตัวอย่าง:
    python seed_sample_data.py --data-dir data --customers 10 --cars 10 --contracts 5 --report

หมายเหตุ:
- สคริปต์นี้จะ "เพิ่ม" (append/reuse ช่องว่าง) บนไฟล์ที่มีอยู่ หรือสร้างใหม่ถ้ายังไม่มี
- ใช้เฉพาะ Python Standard Library
"""
from __future__ import annotations
import argparse, os, struct, random
from datetime import datetime, date
from typing import Optional, Tuple, Dict, Any, List

# ----------------------------
# ค่าคงที่/สเปกไฟล์ (ต้องตรงกับระบบหลัก)
# ----------------------------
E = '<'  # little-endian
HEADER_SIZE = 128
INDEX_SLOT_SIZE = 16
HEADER_FMT = E + '4s B B H I I I I I i I 92x'  # =128B
INDEX_FMT  = E + 'I I 8x'                      # =16B

# Record ฟอร์แมต (ขนาดคงที่)
CUST_FMT = E + 'B I 13s 50s 10s I B 45x'; CUST_SIZE=128; CUST_PAD=83
CARS_FMT = E + 'B I 12s 12s 16s H I I B I 68x'; CARS_SIZE=128; CARS_PAD=60
CONT_FMT = E + 'B I I I I I I B 38x';           CONT_SIZE=64;  CONT_PAD=26

CAR_STATUS = {0:'available', 1:'rented', 2:'maintenance', 3:'retired'}
CAR_STATUS_REV = {v:k for k,v in CAR_STATUS.items()}

# ----------------------------
# ยูทิลิตี้
# ----------------------------
now = lambda: int(datetime.now().timestamp())

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
# Header / Index โครงสร้าง
# ----------------------------
class Header:
    __slots__ = ('magic','version','endian','record_size','created_at','updated_at',
                 'next_id','active_count','deleted_count','free_head','index_slots')
    def __init__(self, magic: bytes, version: int, endian: int, record_size: int,
                 created_at: int, updated_at: int, next_id: int, active_count: int,
                 deleted_count: int, free_head: int, index_slots: int) -> None:
        self.magic=magic; self.version=version; self.endian=endian; self.record_size=record_size
        self.created_at=created_at; self.updated_at=updated_at; self.next_id=next_id
        self.active_count=active_count; self.deleted_count=deleted_count
        self.free_head=free_head; self.index_slots=index_slots
    def pack(self) -> bytes:
        return struct.pack(HEADER_FMT, self.magic, self.version, self.endian, self.record_size,
                           self.created_at, self.updated_at, self.next_id,
                           self.active_count, self.deleted_count, self.free_head,
                           self.index_slots)
    @classmethod
    def unpack(cls, b: bytes) -> 'Header':
        (magic, version, endian, record_size, created_at, updated_at,
         next_id, active_count, deleted_count, free_head, index_slots) = struct.unpack(HEADER_FMT, b)
        return cls(magic,version,endian,record_size,created_at,updated_at,
                   next_id,active_count,deleted_count,free_head,index_slots)
    @classmethod
    def new(cls, magic: bytes, record_size: int, index_slots: int) -> 'Header':
        return cls(magic, 1, 0, record_size, now(), now(), 1, 0, 0, -1, index_slots)

class IndexSlot:
    __slots__=('key','rec_index')
    def __init__(self, key: int, rec_index: int) -> None:
        self.key=key; self.rec_index=rec_index
    def pack(self) -> bytes: return struct.pack(INDEX_FMT, self.key, self.rec_index)
    @classmethod
    def unpack(cls, b: bytes) -> 'IndexSlot':
        k,ri = struct.unpack(INDEX_FMT, b); return cls(k,ri)

# ----------------------------
# ชั้นตารางไบนารีแบบย่อ
# ----------------------------
class BinTable:
    def __init__(self, path: str, magic: bytes, rsize: int, rfmt: str, slots: int, pad_off: int) -> None:
        self.path=path; self.magic=magic; self.rsize=rsize; self.rfmt=rfmt
        self.slots=slots; self.pad_off=pad_off
        self.f=None; self.h=None

    # --- low-level I/O ---
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
        self.h.updated_at = now()
        self.f.seek(0); self.f.write(self.h.pack()); self._sync()

    # --- index helpers ---
    def _index_ofs(self, slot: int) -> int: return HEADER_SIZE + slot*INDEX_SLOT_SIZE
    def _read_slot(self, slot: int) -> IndexSlot:
        self.f.seek(self._index_ofs(slot)); return IndexSlot.unpack(self.f.read(INDEX_SLOT_SIZE))
    def _write_slot(self, slot: int, slotval: IndexSlot) -> None:
        self.f.seek(self._index_ofs(slot)); self.f.write(slotval.pack())
    def _hash(self, key: int) -> int: return key % self.h.index_slots
    def _find_slot(self, key: int) -> Tuple[int, Optional[IndexSlot]]:
        start = self._hash(key)
        for i in range(self.h.index_slots):
            j = (start + i) % self.h.index_slots
            sl = self._read_slot(j)
            if sl.key == 0 or sl.key == key:
                return j, (None if sl.key == 0 else sl)
        raise RuntimeError('index full')
    def _lookup(self, key: int) -> Optional[int]:
        start = self._hash(key)
        for i in range(self.h.index_slots):
            j = (start + i) % self.h.index_slots
            sl = self._read_slot(j)
            if sl.key == 0: return None
            if sl.key == key: return sl.rec_index
        return None

    # --- records ---
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

    # --- CRUD minimal ---
    def next_id(self) -> int:
        nid = self.h.next_id; self.h.next_id += 1; self._write_header(); return nid
    def _alloc(self) -> int:
        if self.h.free_head != -1:
            i = self.h.free_head
            self.f.seek(self._record_ofs(i) + self.pad_off)
            nxt = struct.unpack(E+'i', self.f.read(4))[0]
            self.h.free_head = nxt
            return i
        return self._records_count()
    def add_record(self, key: int, packed: bytes) -> int:
        i = self._alloc(); self._write_raw(i, packed)
        slot, cur = self._find_slot(key)
        if cur is not None: raise ValueError('duplicate key')
        self._write_slot(slot, IndexSlot(key, i))
        self.h.active_count += 1; self._write_header(); self._sync(); return i
    def read_record(self, key: int) -> Optional[bytes]:
        ri = self._lookup(key); return None if ri is None else self._read_raw(ri)
    def update_record(self, key: int, packed: bytes) -> None:
        ri = self._lookup(key)
        if ri is None: raise KeyError('not found')
        self._write_raw(ri, packed); self._write_header(); self._sync()
    def iter_active(self):
        for i in range(self._records_count()):
            raw = self._read_raw(i)
            if raw and raw[0] == 1:
                yield i, raw

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
# สร้างข้อมูลตัวอย่าง
# ----------------------------
FIRST = ["Somchai","Sudarat","Anan","Napat","Arisa","Kittisak","Warin","Ploy","Somsak","Siriporn"]
LAST  = ["Boonmee","Chaiyakul","Srisuk","Prasert","Chanthara","Pattana","Sukprasert","Deejai","Inthra","Thavorn"]
BRAND_MODELS = {
    'Toyota':['Vios','Yaris','Altis'],
    'Honda' :['City','Civic','Jazz'],
    'Mazda' :['2','3','CX-30'],
    'Nissan':['Almera','Note','March'],
    'Mitsu' :['Attrage','Mirage','Xpander'],
}


def seed_once(data_dir: str, n_customers: int, n_cars: int, n_contracts: Optional[int], seed: int, make_report: bool) -> None:
    ensure_dir(data_dir)
    customers = Customers(os.path.join(data_dir,'customers.bin'))
    cars      = Cars(os.path.join(data_dir,'cars.bin'))
    contracts = Contracts(os.path.join(data_dir,'contracts.bin'))

    customers.open(); cars.open(); contracts.open()
    try:
        random.seed(seed)
        # 1) customers
        cus_ids: List[int] = []
        for _ in range(max(0,n_customers)):
            cid = customers.next_id()
            name = f"{random.choice(FIRST)} {random.choice(LAST)}"
            id_card = f"{1103700000000 + cid:013d}"
            phone = f"08{random.randint(10000000, 99999999):08d}"
            y = random.randint(1980, 2005); m = random.randint(1,12); d=random.randint(1,28)
            birth = y*10000 + m*100 + d
            gender = random.choice([0,1,2])
            customers.add_record(cid, customers.pack(1,cid,id_card,name,phone,birth,gender))
            cus_ids.append(cid)

        # 2) cars
        car_ids: List[int] = []
        for _ in range(max(0,n_cars)):
            car_id = cars.next_id()
            brand = random.choice(list(BRAND_MODELS.keys()))
            model = random.choice(BRAND_MODELS[brand])
            plate = f"TH-{car_id:04d}"
            year = random.randint(2017, datetime.now().year+1)
            rate_cents = random.choice([90000,120000,150000,180000,200000,250000])
            odo = random.randint(5000, 120000)
            rec = cars.pack(1,car_id,plate,brand,model,year,rate_cents,odo,0,now())
            cars.add_record(car_id, rec)
            car_ids.append(car_id)

        # 3) contracts (ครึ่งหนึ่งปิดสัญญา ครึ่งหนึ่งเปิดอยู่)
        if n_contracts is None:
            n_contracts = max(1, min(len(cus_ids), len(car_ids))//2)
        used: set[int] = set()
        for k in range(max(0,n_contracts)):
            if not car_ids or not cus_ids: break
            rid = contracts.next_id()
            cus = random.choice(cus_ids)
            avail = [x for x in car_ids if x not in used]
            if not avail: break
            car = random.choice(avail); used.add(car)
            # อ่านข้อมูลรถล่าสุด (ต้องพบ เพราะเพิ่ง add)
            car_raw = cars.read_record(car)
            if car_raw is None:
                raise RuntimeError(f"internal error: car id {car} not found in index")
            car_obj = cars.unpack(car_raw)

            base_y, base_m = 2025, random.randint(6, 9)
            base_d = random.randint(1, 25)
            rent_ymd = base_y*10000 + base_m*100 + base_d

            if k % 2 == 0:
                days = random.randint(1, 5)
                ret_ymd = base_y*10000 + base_m*100 + min(28, base_d + days)
                total = days * car_obj['rate_cents']
                contracts.add_record(rid, contracts.pack(1,rid,cus,car,rent_ymd,ret_ymd,total,1))
                # รถกลับว่าง
                cars.update_record(car, cars.pack(1,car_obj['car_id'],car_obj['license'],car_obj['brand'],car_obj['model'],car_obj['year'],car_obj['rate_cents'],car_obj['odometer_km'],0,now()))
            else:
                contracts.add_record(rid, contracts.pack(1,rid,cus,car,rent_ymd,0,0,0))
                # รถกำลังเช่า
                cars.update_record(car, cars.pack(1,car_obj['car_id'],car_obj['license'],car_obj['brand'],car_obj['model'],car_obj['year'],car_obj['rate_cents'],car_obj['odometer_km'],1,now()))

        if make_report:
            out = os.path.join(data_dir, 'report.txt')
            generate_report(cars, out)
            print('สร้างรายงานแล้ว:', out)

        print(f"Seed สำเร็จ: customers={len(cus_ids)}, cars={len(car_ids)}, contracts~{n_contracts}")
    finally:
        customers.close(); cars.close(); contracts.close()

# ----------------------------
# รายงานแบบย่อ (ตามภาพตัวอย่าง)
# ----------------------------

def generate_report(cars: Cars, out_path: str) -> None:
    # สแกนทั้ง active/deleted
    total=active=deleted=rented=avail=0
    rates: List[int] = []
    by_brand: Dict[str,int] = {}
    lines: List[str] = []

    ts = datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S (%z)')
    lines += [
        'Car Rent System — Summary Report (Sample)',
        f'Generated At : {ts}',
        'App Version  : 1.0',
        'Endianness   : Little-Endian',
        'Encoding     : UTF-8 (fixed-length)',
        ''
    ]
    th = f"{'CarID':>5} | {'Plate':<10} | {'Brand':<10} | {'Model':<10} | {'Year':>4} | {'Rate (THB/day)':>14} | {'Status':<6} | {'Rented':<3}"
    lines += [th, '-'*len(th)]

    # iterate ทุก record (รวมที่ลบ)
    # หมายเหตุ: สคริปต์นี้ไม่สร้าง deleted เอง แต่รองรับกรณีมีอยู่จากระบบหลัก
    cars_count = cars._records_count()
    for i in range(cars_count):
        raw = cars._read_raw(i)
        if not raw: continue
        total += 1
        c = cars.unpack(raw)
        is_active = (raw[0] == 1)
        status = 'Active' if is_active else 'Deleted'
        rented_str = 'Yes' if (is_active and c['status'] == 1) else 'No'
        lines.append(f"{c['car_id']:>5} | {c['license']:<10.10} | {c['brand']:<10.10} | {c['model']:<10.10} | {c['year']:>4} | {c['rate_cents']/100:>14.2f} | {status:<6} | {rented_str:<3}")
        if is_active:
            active += 1
            rates.append(c['rate_cents'])
            by_brand[c['brand']] = by_brand.get(c['brand'], 0) + 1
            if c['status'] == 1: rented += 1
            if c['status'] == 0: avail += 1
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
            '- Min : 0.00','- Max : 0.00','- Avg : 0.00',''
        ]
    lines.append('Cars by Brand (Active only)')
    if by_brand:
        for b in sorted(by_brand): lines.append(f"- {b} : {by_brand[b]}")
    else:
        lines.append('(no active cars)')

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

# ----------------------------
# CLI
# ----------------------------

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description='Seed sample data for CarRent-BinIO (standalone)')
    p.add_argument('--data-dir', default='data', help='โฟลเดอร์เก็บไฟล์ .bin/.txt')
    p.add_argument('--customers', type=int, default=10)
    p.add_argument('--cars', type=int, default=10)
    p.add_argument('--contracts', type=int, default=-1, help='-1 = ประมาณครึ่งหนึ่งของจำนวนรถ')
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--report', action='store_true', help='สร้าง report.txt หลัง seed')
    args = p.parse_args(argv)

    n_contracts = None if args.contracts < 0 else args.contracts
    seed_once(args.data_dir, args.customers, args.cars, n_contracts, args.seed, args.report)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
