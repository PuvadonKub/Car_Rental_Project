# CarRent‑BinIO — README (Updated)

ระบบตัวอย่าง **จัดการเช่ารถด้วยไฟล์ไบนารี (Binary I/O)** เขียนด้วย Python Standard Library เท่านั้น ใช้โครงสร้าง **fixed‑length record + `struct` + header 128B + index 16B/slot (open addressing)** ทำงานผ่านเมนูบนเทอร์มินัล (CRUD + View + Report)

> โค้ดหลัก: `car_rental_binio.py`
> เติมข้อมูลตัวอย่าง: `seed_sample_data.py`
> ตรวจ Header: `inspect_header.py`

---

## คุณสมบัติเด่น

* 3 ไฟล์ข้อมูล: `customers.bin`, `cars.bin`, `contracts.bin` (+ รายงาน `report.txt`)
* ระเบียนความยาวคงที่ (fixed‑length) + สตริงตัด/เติม `\x00` ให้พอดีความยาว
* **Little‑Endian** (`'<'`) สำหรับทุกตาราง/ส่วนหัว
* ดัชนีแบบ **open addressing** (linear probing) + **tombstone** เมื่อถูกลบ
* **Soft delete** (ตั้ง `flag=0`) + **free‑list** reuse ช่องว่าง
* เมนู: Add / Update / Delete / View / Filter / Stats / Report

---

## โครงสร้างโปรเจกต์ (แนะนำ)

```
project/
├─ car_rental_binio.py       # โปรแกรมหลัก (เมนู CRUD/REPORT)
├─ seed_sample_data.py       # เติมข้อมูลตัวอย่าง (standalone)
├─ inspect_header.py         # อ่าน/ตรวจ Header 128 ไบต์
└─ data/                     # โฟลเดอร์ไฟล์ .bin / report.txt (สร้างอัตโนมัติ)
```

---

## เริ่มต้นใช้งาน (Quick Start)

1. เติมข้อมูลตัวอย่าง (แนะนำ):

```bash
# macOS/Linux
python3 seed_sample_data.py --data-dir data --customers 10 --cars 10 --contracts 5 --report

# Windows (PowerShell)
python seed_sample_data.py --data-dir data --customers 10 --cars 10 --contracts 5 --report
```

2. รันโปรแกรมหลัก:

```bash
python car_rental_binio.py --data-dir data
```

3. เมนู: `1 Add / 2 Update / 3 Delete / 4 View / 5 Report / 0 Exit`

---

## เมนูโดยสรุป

* **1) Add**: ลูกค้า / รถ / สัญญาเช่า
* **2) Update**: ลูกค้า / รถ / คืนรถ (ปิดสัญญา → คำนวณค่าเช่าตามวัน)
* **3) Delete**: ลบแบบ *soft delete* (ทำ tombstone + ส่งเข้า free‑list)
* **4) View**: รายการเดียว / ทั้งหมด / กรอง / สถิติรวม
* **5) Report**: สร้างไฟล์ `report.txt` (ดูสรุป, ค่าเช่าต่ำ‑สูง‑เฉลี่ย, จำนวนตามยี่ห้อ ฯลฯ)

---

## สเปกไฟล์ไบนารี

### Header (128 bytes)

```
<'4s B B H I I I I I i I 92x'>
 magic, version, endian, record_size,
 created_at, updated_at,
 next_id, active_count, deleted_count,
 free_head, index_slots, padding
```

* `magic`: ลายเซ็นตาราง (`b'CUST'`, `b'CARS'`, `b'CONT'`)
* `endian`: 0 = Little‑Endian
* `record_size`: ขนาดระเบียน (ขึ้นกับตาราง)
* `free_head`: ชี้ไปยังหัว free‑list (`-1` = ไม่มี)
* `index_slots`: จำนวนช่องดัชนี (ต่อจาก Header)

**Index Slot (16 bytes)**: `<'I I 8x'>` ⇒ `key`, `rec_index` (+ padding)
**Tombstone** เวลา delete: ใช้คีย์พิเศษ `0xFFFFFFFF` (ยังกันเส้นทาง probing ไว้)

---

## แบบจำลองข้อมูล (Fixed‑length Records)

### Customers — 128 bytes

`<B I 13s 50s 10s I B 45x>`

| # | ฟิลด์        | ชนิด | ขนาด | หมายเหตุ                             |
| - | ------------ | ---- | ---: | ------------------------------------ |
| 1 | flag         | B    |    1 | 0=Deleted, 1=Active                  |
| 2 | cus\_id (PK) | I    |    4 | รหัสลูกค้า                           |
| 3 | id\_card     | 13s  |   13 | UTF‑8 fixed, pad `\x00`              |
| 4 | name         | 50s  |   50 | UTF‑8 fixed                          |
| 5 | phone        | 10s  |   10 | UTF‑8 fixed                          |
| 6 | birth\_ymd   | I    |    4 | รูปแบบ `YYYYMMDD`                    |
| 7 | gender       | B    |    1 | 0=unk,1=male,2=female                |
| – | padding      | 45x  |   45 | มี **free‑list pointer @ offset 83** |

### Cars — 128 bytes

`<B I 12s 12s 16s H I I B I 68x>`

| #  | ฟิลด์          | ชนิด | ขนาด | หมายเหตุ                                     |
| -- | -------------- | ---- | ---: | -------------------------------------------- |
| 1  | flag           | B    |    1 | 0/1                                          |
| 2  | car\_id (PK)   | I    |    4 | รหัสรถ                                       |
| 3  | license\_plate | 12s  |   12 | UTF‑8 fixed                                  |
| 4  | brand          | 12s  |   12 | UTF‑8 fixed                                  |
| 5  | model          | 16s  |   16 | UTF‑8 fixed                                  |
| 6  | year           | H    |    2 | ค.ศ.                                         |
| 7  | rate\_cents    | I    |    4 | ค่าเช่า/วัน หน่วยสตางค์                      |
| 8  | odometer\_km   | I    |    4 | เลขไมล์                                      |
| 9  | status         | B    |    1 | 0=available,1=rented,2=maintenance,3=retired |
| 10 | updated\_at    | I    |    4 | Unix timestamp                               |
| –  | padding        | 68x  |   68 | **free‑list pointer @ offset 60**            |

### Contracts — 64 bytes

`<B I I I I I I B 38x>`

| # | ฟิลด์         | ชนิด | ขนาด | หมายเหตุ                          |
| - | ------------- | ---- | ---: | --------------------------------- |
| 1 | flag          | B    |    1 | 0/1                               |
| 2 | rent\_id (PK) | I    |    4 | รหัสสัญญา                         |
| 3 | cus\_id (FK)  | I    |    4 | → customers.cus\_id               |
| 4 | car\_id (FK)  | I    |    4 | → cars.car\_id                    |
| 5 | rent\_ymd     | I    |    4 | `YYYYMMDD`                        |
| 6 | return\_ymd   | I    |    4 | 0 = ยังไม่คืน                     |
| 7 | total\_cents  | I    |    4 | ยอดรวม หน่วยสตางค์                |
| 8 | returned      | B    |    1 | 0=open, 1=closed                  |
| – | padding       | 38x  |   38 | **free‑list pointer @ offset 26** |

**หมายเหตุสตริง**: เข้ารหัส UTF‑8, ตัด/เติม `\x00` ให้พอดีขนาดฟิลด์เสมอ

---

## การใช้งานสคริปต์ประกอบ

### เติมข้อมูลตัวอย่าง — `seed_sample_data.py`

```bash
python seed_sample_data.py --data-dir data --customers 20 --cars 20 --contracts 10 --report
# หรือปล่อย --contracts เป็นค่าเริ่มต้น (-1) ให้ประมาณครึ่งหนึ่งของจำนวนรถ
```

สคริปต์จะสร้าง/เพิ่มเรคคอร์ด พร้อมทั้งอัปเดต index และตัวนับใน Header อัตโนมัติ และสามารถสร้าง `report.txt` ทันทีด้วย `--report` ได้

### ตรวจ Header — `inspect_header.py`

```bash
python inspect_header.py data/cars.bin
python inspect_header.py data/customers.bin data/contracts.bin
python inspect_header.py --json data/cars.bin
```

จะแสดง: `magic`, `version`, `endianness`, `record_size`, `created/updated`, `next_id`, `active/deleted`, `free_head`, `index_slots` และคำนวณจำนวนเรคคอร์ดจากขนาดไฟล์จริง

---

## รายงาน (Report)

สร้างได้จากเมนูข้อ 5 หรือผ่าน `seed_sample_data.py --report` โครงสรุปประกอบด้วย:

* รายการรถทั้งหมด (Active/Deleted, สถานะเช่า, ค่าเช่าต่อวัน)
* สรุปจำนวน Active/Deleted/Rented/Available
* สถิติค่าเช่า: Min/Max/Avg (หน่วยบาท/วัน)
* นับจำนวนรถตามยี่ห้อ (Active เท่านั้น)

---

## แนวทางแก้ปัญหา (Troubleshooting)

* **bad file format**: ไฟล์ไม่ตรงสเปก/record size ไม่ตรง → ลบ `.bin` เก่าใน `data/` แล้ว seed ใหม่
* **duplicate key** ตอนเพิ่ม: `*_id` ซ้ำ → ให้ใช้ id จาก `next_id` (ระบบทำให้โดยอัตโนมัติ)
* **index full**: ช่องดัชนีไม่พอ → เพิ่มค่า `slots` ตอนสร้างไฟล์ใหม่ (ค่าเริ่มต้น: customers/cars = 1024, contracts = 2048)
* **อ่านเรคคอร์ดเป็น None**: ไฟล์/ดัชนีเสียหาย → ลบ `.bin` และ seed ใหม่ด้วย `seed_sample_data.py`

---

## ใบอนุญาต (License)

ตัวอย่างเพื่อการเรียนรู้/สอน ใช้ได้อิสระภายในหลักสูตร/โครงงาน

---

## ผู้พัฒนา

* โปรแกรมหลัก: `car_rental_binio.py`
* สคริปต์ seed: `seed_sample_data.py`
* เครื่องมืออ่าน Header: `inspect_header.py`

> ต้องการให้เพิ่ม export CSV/JSON, ปรับรูปแบบรายงาน, แยก I/O เป็นโมดูล, หรือใส่ unit test แจ้งมาได้เลย 🙂
