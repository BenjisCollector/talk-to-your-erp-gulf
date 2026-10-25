"""Deterministic synthetic Gulf ERP dataset (SQLite). All company names are invented."""
import csv
import datetime as dt
import os
import random
import sqlite3
import sys

AS_OF = "2026-09-30"  # frozen "today" so every answer and eval is reproducible
HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("ERP_DB", os.path.join(HERE, "..", "data", "erp.db"))
CSV_DIR = os.path.join(HERE, "..", "..", "data", "scenarios", "gulf_erp", "tables")

FX = {"KWD": 3.25, "QAR": 0.2747, "SAR": 0.2667}  # USD per 1 unit
DEC = {"KWD": 3, "QAR": 2, "SAR": 2}
COUNTRY = {"KWD": ("Kuwait", ["Kuwait City", "Hawalli", "Salmiya"]),
           "QAR": ("Qatar", ["Doha", "Al Wakrah", "Lusail"]),
           "SAR": ("Saudi Arabia", ["Riyadh", "Jeddah", "Dammam"])}

SCHEMA = """
CREATE TABLE customers(customer_id INTEGER PRIMARY KEY, name_en TEXT, name_ar TEXT, country TEXT, city TEXT,
  currency TEXT, segment TEXT, payment_terms_days INTEGER, credit_limit REAL);
CREATE TABLE suppliers(supplier_id INTEGER PRIMARY KEY, name_en TEXT, name_ar TEXT, country TEXT, currency TEXT);
CREATE TABLE items(item_id INTEGER PRIMARY KEY, sku TEXT, name_en TEXT, name_ar TEXT, category TEXT, category_ar TEXT,
  unit_cost_usd REAL, list_price_usd REAL, reorder_level INTEGER);
CREATE TABLE warehouses(warehouse_id INTEGER PRIMARY KEY, name_en TEXT, name_ar TEXT, country TEXT, city TEXT);
CREATE TABLE stock_levels(warehouse_id INTEGER, item_id INTEGER, qty_on_hand INTEGER, PRIMARY KEY(warehouse_id, item_id));
CREATE TABLE invoices(invoice_id INTEGER PRIMARY KEY, invoice_no TEXT, customer_id INTEGER, invoice_date TEXT, due_date TEXT,
  currency TEXT, total_amount REAL, paid_amount REAL, status TEXT);
CREATE TABLE invoice_lines(line_id INTEGER PRIMARY KEY, invoice_id INTEGER, item_id INTEGER, qty INTEGER, unit_price REAL, line_total REAL);
CREATE TABLE purchase_orders(po_id INTEGER PRIMARY KEY, po_no TEXT, supplier_id INTEGER, order_date TEXT, expected_date TEXT,
  currency TEXT, total_amount REAL, status TEXT);
CREATE TABLE po_lines(po_line_id INTEGER PRIMARY KEY, po_id INTEGER, item_id INTEGER, qty INTEGER, unit_cost REAL);
CREATE TABLE fx_rates(currency TEXT PRIMARY KEY, usd_per_unit REAL);
CREATE VIEW receivables_ageing AS
 SELECT i.invoice_id, i.invoice_no, i.customer_id, c.name_en AS customer_name, c.name_ar AS customer_name_ar, i.currency,
  ROUND(i.total_amount - i.paid_amount, 3) AS outstanding, i.due_date,
  CAST(julianday('%(asof)s') - julianday(i.due_date) AS INTEGER) AS days_overdue,
  CASE WHEN julianday('%(asof)s') <= julianday(i.due_date) THEN 'Current'
       WHEN julianday('%(asof)s') - julianday(i.due_date) <= 30 THEN '1-30'
       WHEN julianday('%(asof)s') - julianday(i.due_date) <= 60 THEN '31-60'
       WHEN julianday('%(asof)s') - julianday(i.due_date) <= 90 THEN '61-90'
       ELSE '90+' END AS bucket
 FROM invoices i JOIN customers c ON c.customer_id = i.customer_id
 WHERE i.total_amount - i.paid_amount > 0.0005;
""" % {"asof": AS_OF}

PREFIX = [("Pearl Coast", "ساحل اللؤلؤ"), ("Falcon Ridge", "قمة الصقر"), ("Palm Terrace", "شرفة النخيل"),
          ("Desert Rose", "وردة الصحراء"), ("Blue Dhow", "الداو الأزرق"), ("Amber Dune", "الكثيب العنبري"),
          ("Coral Bay", "خليج المرجان"), ("Oasis Crown", "تاج الواحة"), ("Silver Tide", "المد الفضي"),
          ("Date Grove", "بستان التمر"), ("Gulf Horizon", "أفق الخليج"), ("Sand Lantern", "فانوس الرمل"),
          ("Cedar Mirage", "سراب الأرز"), ("Saffron Gate", "بوابة الزعفران"), ("Jade Minaret", "منارة اليشم"),
          ("Ivory Harbor", "ميناء العاج"), ("Copper Mesa", "هضبة النحاس"), ("Noble Caravan", "القافلة النبيلة"),
          ("Starlit Souq", "سوق النجوم"), ("Granite Wave", "موجة الغرانيت"), ("Mirage Pier", "رصيف السراب"),
          ("Sable Oryx", "المها الأسود"), ("Lotus Bastion", "حصن اللوتس"), ("Windward Palm", "نخلة الريح"),
          ("Opal Fortress", "قلعة الأوبال"), ("Zephyr Court", "ساحة النسيم"), ("Tamarind Cove", "خليج التمر هندي"),
          ("Citadel Pearl", "لؤلؤة القلعة"), ("Dawn Caravel", "كارافيل الفجر"), ("Hazel Minaret", "منارة البندق")]
SECTOR = [("Trading Co.", "للتجارة"), ("Contracting", "للمقاولات"), ("Engineering", "للهندسة"),
          ("Facilities Services", "لخدمات المرافق"), ("Industrial Supplies", "للتوريدات الصناعية"),
          ("Electromechanical", "للأعمال الكهروميكانيكية")]
SUPPLIER = [("Halden Cable Works", "هالدن لصناعة الكابلات"), ("Norvik Lighting", "نورفيك للإضاءة"),
            ("Brenmar Pumps", "برينمار للمضخات"), ("Calder Climate Systems", "كالدر لأنظمة التكييف"),
            ("Ostrava Safety Gear", "أوسترافا لمعدات السلامة"), ("Tavern Hill Tools", "تافرن هيل للأدوات"),
            ("Marlow Valves", "مارلو للصمامات"), ("Kestrel Switchgear", "كيستريل للمفاتيح الكهربائية"),
            ("Dunmore Fasteners", "دنمور للمثبتات"), ("Altair Pipe Systems", "ألتير لأنظمة الأنابيب"),
            ("Wexford Batteries", "ويكسفورد للبطاريات"), ("Ravenel Paints", "رافينيل للدهانات")]

# category, category_ar, [(name_en, name_ar, usd_price)]
CATALOG = [
 ("Cables", "الكابلات", [("Copper Cable 2.5mm 100m", "كيبل نحاس 2.5 مم 100م", 58), ("Copper Cable 4mm 100m", "كيبل نحاس 4 مم 100م", 92),
   ("Copper Cable 6mm 100m", "كيبل نحاس 6 مم 100م", 138), ("Armoured Cable 4x16mm 50m", "كيبل مدرع 4x16 مم 50م", 410),
   ("Fibre Optic Cable 12 core 500m", "كيبل ألياف ضوئية 12 خيط 500م", 640), ("Flexible Cable 3x1.5mm 100m", "كيبل مرن 3x1.5 مم 100م", 74)]),
 ("Lighting", "الإضاءة", [("LED Panel 60x60 40W", "لوحة إضاءة LED 60x60 40 واط", 24), ("LED Floodlight 100W", "كشاف LED 100 واط", 46),
   ("LED Street Light 150W", "إنارة شوارع LED 150 واط", 138), ("Emergency Exit Light", "إضاءة مخرج طوارئ", 19),
   ("LED Tube 18W", "أنبوب LED 18 واط", 4.5), ("High Bay Light 200W", "إنارة عالية 200 واط", 112)]),
 ("Pumps", "المضخات", [("Centrifugal Pump 5HP", "مضخة طاردة مركزية 5 حصان", 780), ("Submersible Pump 3HP", "مضخة غاطسة 3 حصان", 520),
   ("Booster Pump Set 7.5HP", "مجموعة مضخة رفع الضغط 7.5 حصان", 1450), ("Water Transfer Pump 2HP", "مضخة نقل مياه 2 حصان", 310),
   ("Fire Jockey Pump", "مضخة حريق مساندة", 1980), ("Sump Pump 1HP", "مضخة تصريف 1 حصان", 245)]),
 ("HVAC", "التكييف", [("Split AC 2 Ton", "مكيف سبليت 2 طن", 640), ("Split AC 3 Ton", "مكيف سبليت 3 طن", 890),
   ("Ducted Unit 10 Ton", "وحدة مجاري هواء 10 طن", 4200), ("Chilled Water Fan Coil", "وحدة مروحة مياه مبردة", 380),
   ("Air Filter Pack G4", "عبوة فلاتر هواء G4", 36), ("Thermostat Digital", "ثرموستات رقمي", 42)]),
 ("Safety", "السلامة", [("Safety Helmet", "خوذة سلامة", 7.5), ("Safety Boots Steel Toe", "حذاء سلامة مقدمة فولاذية", 31),
   ("Fire Extinguisher 6kg", "طفاية حريق 6 كجم", 38), ("High Visibility Vest", "سترة عاكسة", 4.2),
   ("Safety Goggles", "نظارات سلامة", 3.1), ("First Aid Kit Industrial", "حقيبة إسعافات أولية صناعية", 55)]),
 ("Tools", "الأدوات", [("Cordless Drill 18V", "مثقاب لاسلكي 18 فولت", 96), ("Angle Grinder 125mm", "صاروخ جلخ 125 مم", 64),
   ("Torque Wrench Set", "طقم مفتاح عزم", 175), ("Tool Cabinet 7 Drawer", "خزانة أدوات 7 أدراج", 260),
   ("Laser Distance Meter", "جهاز قياس مسافة ليزر", 88), ("Welding Machine 250A", "ماكينة لحام 250 أمبير", 430)]),
 ("Electrical", "الكهرباء", [("Circuit Breaker 63A", "قاطع كهربائي 63 أمبير", 29), ("Distribution Board 24 Way", "لوحة توزيع 24 فتحة", 210),
   ("Contactor 40A", "كونتاكتور 40 أمبير", 47), ("Socket Outlet Twin", "مأخذ كهرباء مزدوج", 5.8),
   ("UPS 3kVA", "مصدر طاقة احتياطي 3 كيلو فولت أمبير", 690), ("Generator 100kVA", "مولد 100 كيلو فولت أمبير", 9800)]),
 ("Piping", "الأنابيب", [("PVC Pipe 110mm 6m", "أنبوب PVC 110 مم 6م", 17), ("HDPE Pipe 200mm 12m", "أنبوب HDPE 200 مم 12م", 148),
   ("Ball Valve 2 inch", "محبس كروي 2 إنش", 22), ("Gate Valve 4 inch", "محبس بوابة 4 إنش", 76),
   ("Pipe Fitting Kit", "طقم وصلات أنابيب", 33), ("Flange Set DN100", "طقم شفة DN100", 41)]),
]


def money(cur, usd):
    return round(usd / FX[cur], DEC[cur])


def d(s):
    return dt.date.fromisoformat(s)


def build(path=DB_PATH):
    r = random.Random(2026)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        os.remove(path)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    asof = d(AS_OF)

    db.executemany("INSERT INTO fx_rates VALUES(?,?)", FX.items())

    # customers: 18 per country, a few chronic slow payers
    combos = [(p, s) for p in PREFIX for s in SECTOR]
    r.shuffle(combos)
    custs, slow = [], set()
    for i in range(54):
        cur = ["KWD", "QAR", "SAR"][i % 3]
        country, cities = COUNTRY[cur]
        (pe, pa), (se, sa) = combos[i]
        seg = r.choices(["Enterprise", "Mid-market", "Small"], [2, 4, 4])[0]
        terms = r.choice([30, 45, 60])
        limit = {"Enterprise": 250000, "Mid-market": 80000, "Small": 20000}[seg]
        custs.append((i + 1, f"{pe} {se}", f"{pa} {sa}", country, r.choice(cities), cur, seg, terms, money(cur, limit)))
        if r.random() < 0.18:
            slow.add(i + 1)
    db.executemany("INSERT INTO customers VALUES(?,?,?,?,?,?,?,?,?)", custs)

    db.executemany("INSERT INTO suppliers VALUES(?,?,?,?,?)", [
        (i + 1, e, a, COUNTRY[c][0], c) for i, ((e, a), c) in enumerate(zip(SUPPLIER, ["SAR", "QAR", "KWD"] * 4))])

    whs = [(1, "Kuwait Central Warehouse", "مستودع الكويت المركزي", "Kuwait", "Shuwaikh"),
           (2, "Doha Distribution Hub", "مركز توزيع الدوحة", "Qatar", "Doha"),
           (3, "Riyadh Warehouse", "مستودع الرياض", "Saudi Arabia", "Riyadh"),
           (4, "Jeddah Warehouse", "مستودع جدة", "Saudi Arabia", "Jeddah")]
    db.executemany("INSERT INTO warehouses VALUES(?,?,?,?,?)", whs)

    items = []
    for cat, cat_ar, rows in CATALOG:
        for n_en, n_ar, price in rows:
            iid = len(items) + 1
            cost = round(price * r.uniform(0.62, 0.78), 2)
            items.append((iid, f"{cat[:3].upper()}-{iid:03d}", n_en, n_ar, cat, cat_ar, cost, price, r.choice([10, 15, 20, 30, 40, 60])))
    db.executemany("INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?)", items)

    low_pairs = set(r.sample([(w, it[0]) for w in range(1, 5) for it in items], 14))
    stock = []
    for w in range(1, 5):
        for it in items:
            rl = it[8]
            q = r.randint(0, max(1, rl // 2)) if (w, it[0]) in low_pairs else r.randint(rl + 3, rl * 6)
            stock.append((w, it[0], q))
    db.executemany("INSERT INTO stock_levels VALUES(?,?,?)", stock)

    # invoices: Oct 2025 .. Sep 2026
    inv_id = line_id = 0
    start = dt.date(2025, 10, 1)
    for c in custs:
        cid, cur, terms, seg = c[0], c[5], c[7], c[6]
        per_month = {"Enterprise": 3, "Mid-market": 2, "Small": 1}[seg]
        for m in range(12):
            for _ in range(r.randint(0, per_month)):
                day = start + dt.timedelta(days=m * 30 + r.randint(0, 29))
                if day > asof:
                    continue
                inv_id += 1
                lines = []
                for it in r.sample(items, r.randint(1, 4)):
                    qty = r.randint(1, 40 if it[7] < 100 else 6)
                    up = money(cur, it[7] * r.uniform(1.05, 1.25))
                    lines.append((it[0], qty, up, round(qty * up, DEC[cur])))
                total = round(sum(l[3] for l in lines), DEC[cur])
                due = day + dt.timedelta(days=terms)
                late = cid in slow
                age = (asof - due).days
                if age < 0:
                    paid = total if r.random() < 0.25 else 0.0
                elif r.random() < (0.35 if late else 0.05) * max(0.12, 1 - age / 240):
                    paid = 0.0 if r.random() < 0.7 else round(total * r.uniform(0.3, 0.7), DEC[cur])
                else:
                    paid = total
                status = "paid" if paid >= total else ("partial" if paid > 0 else "open")
                db.execute("INSERT INTO invoices VALUES(?,?,?,?,?,?,?,?,?)",
                           (inv_id, f"INV-{day.year}-{inv_id:05d}", cid, day.isoformat(), due.isoformat(), cur, total, paid, status))
                for l in lines:
                    line_id += 1
                    db.execute("INSERT INTO invoice_lines VALUES(?,?,?,?,?,?)", (line_id, inv_id, *l))

    # purchase orders
    po_line = 0
    for po in range(1, 111):
        sup = r.randint(1, len(SUPPLIER))
        cur = ["SAR", "QAR", "KWD"][(sup - 1) % 3]
        od = start + dt.timedelta(days=r.randint(0, 360))
        od = min(od, asof - dt.timedelta(days=1))
        exp = od + dt.timedelta(days=r.choice([21, 30, 45, 60]))
        lines = []
        for it in r.sample([i for i in items if i[4] == CATALOG[(sup - 1) % len(CATALOG)][0]] or items, 3):
            qty = r.randint(10, 200 if it[6] < 100 else 20)
            lines.append((it[0], qty, money(cur, it[6])))
        total = round(sum(q * c for _, q, c in lines), DEC[cur])
        age = (asof - exp).days
        if age > 20:
            status = r.choices(["received", "partial", "open", "cancelled"], [80, 8, 8, 4])[0]
        elif age > -10:
            status = r.choices(["received", "partial", "open"], [40, 25, 35])[0]
        else:
            status = r.choices(["open", "partial"], [85, 15])[0]
        db.execute("INSERT INTO purchase_orders VALUES(?,?,?,?,?,?,?,?)",
                   (po, f"PO-{od.year}-{po:04d}", sup, od.isoformat(), exp.isoformat(), cur, total, status))
        for l in lines:
            po_line += 1
            db.execute("INSERT INTO po_lines VALUES(?,?,?,?,?)", (po_line, po, *l))
    db.commit()
    return db


def export_csv(db):
    """Same data as an Azure scenario pack (data/scenarios/gulf_erp/tables) for the upstream Fabric loader."""
    os.makedirs(CSV_DIR, exist_ok=True)
    for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
        cur = db.execute(f"SELECT * FROM {t}")
        with open(os.path.join(CSV_DIR, f"{t}.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([c[0] for c in cur.description])
            w.writerows(cur.fetchall())


if __name__ == "__main__":
    db = build()
    if "--csv" in sys.argv:
        export_csv(db)
    for t in ("customers", "items", "stock_levels", "invoices", "invoice_lines", "purchase_orders"):
        print(t, db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
