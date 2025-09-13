# CarRent-BinIO — README

ระบบตัวอย่าง **จัดการเช่ารถด้วยไฟล์ไบนารี (Binary I/O)** เขียนด้วย Python Standard Library เท่านั้น ใช้โครงสร้าง **fixed‑length record + `struct` + ดัชนีแบบ open addressing** ทำงานผ่านเมนูบนเทอร์มินัล (CRUD + View + Report)

> โค้ดหลักอยู่ที่ `car_rental_binio.py`
> สคริปต์เติมข้อมูลตัวอย่าง (แยกไฟล์) คือ `seed_sample_data.py`

---

## คุณสมบัติหลัก

* 3 ไฟล์บันทึกข้อมูล: `customers.bin`, `cars.bin`, `contracts.bin` (+ รายงาน `report.txt`)
* ระเบียนความยาวคงที่, สตริงตัด/เติม (`\x00`) ให้พอดี, **Little‑Endian**
* Header 128B + Index 16B/slot, ดัชนีแบบ linear probing (open addressing)
* **Soft delete** (ตั้ง `flag=0`) + **tombstone** ที่ index + **free‑list** reuse ช่องว่าง
* เมนู: Create/Add, Read/View, Update, Delete, Filter, Stats, Report

## ข้อกำหนดระบบ

* Python **3.10+**
* ใช้เฉพาะ Standard Library (`struct`, `os`, `argparse`, `datetime` ฯลฯ)
* ไม่ต้องติดตั้งเพิ่มเติม

---

## โครงสร้างโปรเจกต์ (แนะนำ)

```
project/
├─ car_rental_binio.py       # โปรแกรมหลัก (เมนู CRUD/REPORT)
├─ seed_sample_data.py       # เติมข้อมูลตัวอย่าง (standalone)
└─ data/                     # โฟลเดอร์เก็บ .bin และ report.txt (ถูกสร้างอัตโนมัติ)
```

---

## เริ่มต้นใช้งานอย่างรวดเร็ว (Quick Start)

1. เติมข้อมูลตัวอย่าง (แนะนำ):

```bash
# macOS/Linux
python3 seed_sample_data.py --data-dir data --customers 10 --cars 10 --contracts 5 --report

# Windows (PowerShell)
python seed_sample_data.py --data-dir data --customers 10 --cars 10 --contracts 5 --report
```

2. รันโปรแกรมหลัก

```bash
python car_rental_binio.py --data-dir data
```

3. ในเมนู กด `5` เพื่อสร้างรายงาน `report.txt` ได้ทุกเวลา

---

## เมนู (คร่าว ๆ)

* **1) Add**: ลูกค้า / รถ / สัญญาเช่าใหม่
* **2) Update**: ลูกค้า / รถ / คืนรถ (ปิดสัญญา)
* **3) Delete**: ลบแบบ *soft delete* (เก็บซากใน index + ส่งเข้า free‑list)
* **4) View**: รายการเดียว, ทั้งหมด, แบบกรอง, สถิติรวม
* **5) Report**: สร้างรายงานสรุป `report.txt`

---

## แบบจำลองข้อมูล

### 1) Customer (128B)

`<B I 13s 50s 10s I B 45x>`

| ฟิลด์      | ชนิด | ขนาด | หมายเหตุ                              |
| ---------- | ---- | ---: | ------------------------------------- |
| flag       | B    |    1 | 0=Deleted, 1=Active                   |
| cus\_id    | I    |    4 | PK                                    |
| id\_card   | 13s  |   13 | UTF‑8 fixed, pad `\x00`               |
| name       | 50s  |   50 | UTF‑8 fixed                           |
| phone      | 10s  |   10 | UTF‑8 fixed                           |
| birth\_ymd | I    |    4 | YYYYMMDD                              |
| gender     | B    |    1 | 0=unk,1=male,2=female                 |
| padding    | 45x  |   45 | + **free‑list pointer** ที่ offset 83 |

### 2) Cars (128B)

`<B I 12s 12s 16s H I I B I 68x>`

| ฟิลด์          | ชนิด | ขนาด | หมายเหตุ                                     |
| -------------- | ---- | ---: | -------------------------------------------- |
| flag           | B    |    1 | 0=Deleted, 1=Active                          |
| car\_id        | I    |    4 | PK                                           |
| license\_plate | 12s  |   12 | UTF‑8 fixed                                  |
| brand          | 12s  |   12 | UTF‑8 fixed                                  |
| model          | 16s  |   16 | UTF‑8 fixed                                  |
| year           | H    |    2 | พ.ศ./ค.ศ.ที่ใช้จริงในโค้ด = ค.ศ.             |
| rate\_cents    | I    |    4 | ค่าเช่า/วัน หน่วยสตางค์                      |
| odometer\_km   | I    |    4 | เลขไมล์                                      |
| status         | B    |    1 | 0=available,1=rented,2=maintenance,3=retired |
| updated\_at    | I    |    4 | Unix timestamp                               |
| padding        | 68x  |   68 | + **free‑list pointer** ที่ offset 60        |

### 3) Rental Contract (64B)

`<B I I I I I I B 38x>`

| ฟิลด์        | ชนิด | ขนาด | หมายเหตุ                              |
| ------------ | ---- | ---: | ------------------------------------- |
| flag         | B    |    1 | 0=Deleted, 1=Active                   |
| rent\_id     | I    |    4 | PK                                    |
| cus\_id      | I    |    4 | FK → customers.cus\_id                |
| car\_id      | I    |    4 | FK → cars.car\_id                     |
| rent\_ymd    | I    |    4 | YYYYMMDD                              |
| return\_ymd  | I    |    4 | 0=ยังไม่คืน                           |
| total\_cents | I    |    4 | หน่วยสตางค์                           |
| returned     | B    |    1 | 0=open, 1=closed                      |
| padding      | 38x  |   38 | + **free‑list pointer** ที่ offset 26 |

> **Endianness:** Little‑Endian (`'<'`)

---

## Header & Index (ไฟล์ไบนารี)

* **Header (128B)**: `<'4s B B H I I I I I i I 92x'>`

  * `magic(4)`, `version(1)`, `endian(1)`, `record_size(2)`, `created_at(4)`, `updated_at(4)`,
    `next_id(4)`, `active_count(4)`, `deleted_count(4)`, `free_head(4)`, `index_slots(4)`
* **Index slot (16B)**: `<'I I 8x'>` ⇒ `key`, `rec_index` (+ padding)
* การลบ: ทำ **tombstone** ในดัชนีด้วยค่า `0xFFFFFFFF` เพื่อรักษาเส้นทาง probing
* **Free‑list**: เก็บ pointer (`int32`) ไว้ในบริเวณ padding ของระเบียนแต่ละตาราง

---

## รายงาน (Report)

สร้างได้จากเมนูข้อ 5 หรือผ่าน `seed_sample_data.py --report` ผลลัพธ์จะคล้ายตัวอย่างนี้:

```
Car Rent System — Summary Report (Sample)
Generated At : 2025-08-26 14:00:00 (+0700)
App Version  : 1.0
Endianness   : Little-Endian
Encoding     : UTF-8 (fixed-length)

 CarID | Plate      | Brand      | Model      | Year | Rate (THB/day) | Status | Rented
-----  | ---------- | ---------- | ---------- | ---- | -------------- | ------ | ------
 1001  | ABC-1234   | Toyota     | Camry      | 2021 |       1500.00  | Active | Yes
 ...
```

---

## คำสั่งที่ใช้บ่อย

```bash
# เติมข้อมูลตัวอย่าง
python seed_sample_data.py --data-dir data --customers 20 --cars 20 --contracts 10 --report

# รันโปรแกรมหลัก
python car_rental_binio.py --data-dir data
```

---

## แนวทางแก้ปัญหาพบบ่อย (Troubleshooting)

* **bad file format**: โครงสร้างไฟล์ไม่ตรงสเปก/ขนาดระเบียนต่างกัน ⇒ ลบไฟล์ `.bin` เก่าใน `data/` แล้วสร้างใหม่
* **duplicate key** ตอนเพิ่ม: `*_id` ซ้ำในดัชนี ⇒ เปลี่ยน id (ปกติระบบใช้ `next_id` ให้แล้ว จึงไม่เกิดในการใช้งานปรกติ)
* **index full**: จำนวนช่องดัชนีไม่พอ ⇒ ปรับค่า `slots` ตอนสร้างไฟล์ใหม่ (ในโค้ด default 1024/2048)
* **NoneType** ตอนอ่านเรคคอร์ด: ไฟล์/ดัชนีเสียหาย ⇒ ลบ `.bin` แล้ว seed ใหม่ด้วย `seed_sample_data.py`

---

## ใบอนุญาต (License)

ตัวอย่างเพื่อการเรียนรู้/สอน ใช้ได้อิสระภายในหลักสูตร/โครงงาน

---

## ผู้พัฒนา

* โค้ดหลัก: `car_rental_binio.py (clean)`
* สคริปต์ seed: `seed_sample_data.py`

> ต้องการให้ปรับหัวรายงาน/คอลัมน์/ความยาวสตริง เพิ่ม export CSV/JSON หรือแตกไฟล์ I/O เป็นโมดูล แวะบอกได้เลยครับ 🙂
