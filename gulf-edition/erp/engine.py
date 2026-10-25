"""Question -> guarded SQL -> answer. LLM mode if a key is set, deterministic canned engine otherwise."""
import json
import os
import re
import sqlite3
import urllib.request

from . import guard
from .seed import AS_OF, DB_PATH

MONTH_START = AS_OF[:8] + "01"
CUR_WORDS = {"KWD": ["kuwait", "kwd", "kuwaiti", "كويت", "كويتي"],
             "QAR": ["qatar", "qar", "qatari", "قطر", "الدوحه"],
             "SAR": ["saudi", "sar", "ksa", "سعودي", "السعوديه"]}
COUNTRY_AR = {"Kuwait": "الكويت", "Qatar": "قطر", "Saudi Arabia": "السعودية"}
VALUE_AR = {"Current": "غير مستحقة", "open": "مفتوح", "partial": "جزئي", "received": "مستلم", "cancelled": "ملغى", **COUNTRY_AR}

# ---------------------------------------------------------------- language + Gulf Arabic
_DIAC = re.compile("[ً-ٰٟـ]")
_ARNUM = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_FOLD = str.maketrans("أإآةىؤئ", "اااهيوي")
# Gulf / Khaleeji dialect words -> the standard terms the intent triggers use (applied after normalisation)
GULF = {k.translate(_FOLD): v for k, v in {
    "شنو": "ما", "شو": "ما", "ايش": "ما", "وش": "ما", "شكثر": "كم", "شقد": "كم", "قديش": "كم", "وايد": "كثير",
    "هالشهر": "هذا الشهر", "هالسنه": "هذه السنه", "خلصت": "نفد", "خلص": "نفد", "خالص": "نفد", "خلصان": "نفد",
    "وريني": "اعرض", "ورني": "اعرض", "عطني": "اعرض", "جيبلي": "اعرض", "ابي": "اعرض", "ابغي": "اعرض", "ابغا": "اعرض",
    "بغيت": "اعرض", "الحين": "الان", "اللي": "الذي", "شلون": "كيف", "سددوا": "سددت", "دفعوا": "سددت",
    "متأخرين": "متاخره"}.items()}


def norm(t):
    t = _DIAC.sub("", t.lower().translate(_ARNUM)).translate(_FOLD)
    t = re.sub(r"[^\w\s\-]", " ", t)
    return " ".join(GULF.get(w, w) for w in t.split())


def lang_of(q):
    return "ar" if len(re.findall("[؀-ۿ]", q)) > len(re.findall("[a-zA-Z]", q)) else "en"


# ---------------------------------------------------------------- canned engine
def fm(x, cur=None):
    return f"{x or 0:,.{3 if cur == 'KWD' else 2 if cur else 0}f}" + (f" {cur}" if cur else "")


def usd(x):
    return f"USD {x:,.0f}"


def mon(x, p):  # money in the asked country's currency, else USD equivalent
    return fm(x, p["cur"]) if p["cur"] else usd(x)


FX = "JOIN fx_rates f ON f.currency = {t}.currency"
INTENTS = {}


def intent(name, en, ar, sql, ans, requires=(), chart=None, n=None, showcase=None):
    INTENTS[name] = dict(en=en, ar=[norm(x) for x in ar], sql=sql, ans=ans, requires=[norm(x) for x in requires],
                         chart=chart, n=n, showcase=showcase)


def cond(p, col="c.currency"):
    return f"AND {col} = '{p['cur']}'" if p["cur"] else ""


def revx(p, amount):
    return f"SUM({amount})" if p["cur"] else f"SUM({amount} * f.usd_per_unit)"


def col(p, local, usdname):
    return local if p["cur"] else usdname


intent("low_stock", ["low stock", "below reorder", "reorder", "running low", "out of stock", "shortage"],
       ["تحت حد", "حد اعاده الطلب", "حد الطلب", "مخزون منخفض", "مخزون قليل", "نفد", "نواقص", "ناقص", "نقص"],
       lambda p: f"""SELECT w.name_{p['L']} AS warehouse, i.sku, i.name_{p['L']} AS item, s.qty_on_hand, i.reorder_level,
 i.reorder_level - s.qty_on_hand AS shortfall
FROM stock_levels s JOIN items i ON i.item_id = s.item_id JOIN warehouses w ON w.warehouse_id = s.warehouse_id
WHERE s.qty_on_hand < i.reorder_level ORDER BY shortfall DESC, i.sku LIMIT {p['n']}""",
       lambda r, p: (f"{len(r)} item-warehouse lines are below reorder level; the worst is {r[0][2]} at {r[0][0]} "
                     f"({r[0][3]} on hand vs {r[0][4]} required).",
                     f"{len(r)} صنف/مستودع تحت حد إعادة الطلب، وأشدها نقصاً {r[0][2]} في {r[0][0]} "
                     f"({r[0][3]} متوفر مقابل {r[0][4]} مطلوب).") if r else ("Nothing is below reorder level.", "لا توجد أصناف تحت حد إعادة الطلب."),
       chart=("item", "shortfall"), n=15, showcase=("Which items are below reorder level?", "شنو الأصناف اللي خلصت من المخزون؟"))

intent("stock_value", ["stock value", "inventory value", "value of stock", "value of inventory", "inventory by warehouse"],
       ["قيمه المخزون", "قيمه البضاعه", "قيمه مخزون", "المخزون حسب المستودع"],
       lambda p: f"""SELECT w.name_{p['L']} AS warehouse, SUM(s.qty_on_hand) AS units,
 ROUND(SUM(s.qty_on_hand * i.unit_cost_usd), 0) AS stock_value_usd
FROM stock_levels s JOIN items i ON i.item_id = s.item_id JOIN warehouses w ON w.warehouse_id = s.warehouse_id
GROUP BY w.warehouse_id ORDER BY stock_value_usd DESC""",
       lambda r, p: (f"Total stock is worth {usd(sum(x[2] for x in r))} at cost; {r[0][0]} holds the most ({usd(r[0][2])}).",
                     f"إجمالي قيمة المخزون بسعر التكلفة {usd(sum(x[2] for x in r))}، وأكبرها في {r[0][0]} ({usd(r[0][2])})."),
       chart=("warehouse", "stock_value_usd"), showcase=("What is our stock value by warehouse?", "كم قيمة المخزون في كل مستودع؟"))

intent("top_customers", ["top customers", "best customers", "biggest customers", "largest customers", "top clients", "customers by revenue", "customers by sales"],
       ["افضل العملاء", "اكبر العملاء", "اعلى العملاء", "اهم العملاء", "كبار العملاء", "اكثر العملاء", "اكبر عملاء", "اعلى عملاء", "افضل عملاء", "اهم عملاء"],
       lambda p: f"""SELECT c.name_{p['L']} AS customer, c.country, COUNT(*) AS invoices,
 ROUND({revx(p, 'i.total_amount')}, 2) AS {col(p, 'revenue', 'revenue_usd')}
FROM invoices i JOIN customers c ON c.customer_id = i.customer_id {FX.format(t='i')}
WHERE 1 = 1 {cond(p)} GROUP BY c.customer_id ORDER BY 4 DESC LIMIT {p['n']}""",
       lambda r, p: (f"Top customer is {r[0][0]} with {mon(r[0][3], p)} across {r[0][2]} invoices; the top {len(r)} are listed below.",
                     f"أكبر عميل هو {r[0][0]} بإجمالي {mon(r[0][3], p)} من {r[0][2]} فاتورة، وأدناه أكبر {len(r)} عملاء."),
       chart=("customer", None), n=5, showcase=("Who are our top 5 customers in Kuwait?", "وريني أكبر ٥ عملاء بالكويت"))

intent("monthly_sales", ["monthly sales", "sales trend", "revenue by month", "sales by month", "sales per month", "monthly revenue", "revenue trend"],
       ["المبيعات الشهريه", "المبيعات شهريا", "اتجاه المبيعات", "مبيعات كل شهر", "المبيعات حسب الشهر", "الايرادات الشهريه", "مبيعاتنا شهريا"],
       lambda p: f"""SELECT strftime('%Y-%m', i.invoice_date) AS month, COUNT(*) AS invoices,
 ROUND({revx(p, 'i.total_amount')}, 0) AS {col(p, 'sales', 'sales_usd')}
FROM invoices i {FX.format(t='i')} WHERE 1 = 1 {cond(p, 'i.currency')} GROUP BY month ORDER BY month""",
       lambda r, p: (lambda best, tot: (f"Sales over the last {len(r)} months total {mon(tot, p)}; best month was {best[0]} ({mon(best[2], p)}).",
                                        f"إجمالي المبيعات خلال آخر {len(r)} شهراً {mon(tot, p)}، وأفضل شهر {best[0]} ({mon(best[2], p)})."))(
           max(r, key=lambda x: x[2]), sum(x[2] for x in r)),
       chart=("month", None), showcase=("Show monthly sales for the last 12 months", "اعرض المبيعات الشهرية لآخر ١٢ شهر"))

intent("sales_this_month", ["this month", "month to date", "sold this month"], ["هذا الشهر", "الشهر الحالي"],
       lambda p: f"""SELECT currency, COUNT(*) AS invoices, ROUND(SUM(total_amount), 3) AS sales
FROM invoices WHERE invoice_date >= '{MONTH_START}' AND invoice_date <= '{AS_OF}' GROUP BY currency ORDER BY currency""",
       lambda r, p: ("This month (to " + AS_OF + ") we invoiced " + ", ".join(f"{fm(x[2], x[0])} ({x[1]} invoices)" for x in r) + ".",
                     "مبيعات هذا الشهر حتى " + AS_OF + ": " + "، ".join(f"{fm(x[2], x[0])} ({x[1]} فاتورة)" for x in r) + "."),
       requires=["sales", "sell", "revenue", "sold", "invoiced", "مبيعات", "ايرادات", "بعنا"], chart="none",
       showcase=("How much did we sell this month?", "شكثر مبيعاتنا هالشهر؟"))

BUCKET_ORDER = "CASE a.bucket WHEN 'Current' THEN 0 WHEN '1-30' THEN 1 WHEN '31-60' THEN 2 WHEN '61-90' THEN 3 ELSE 4 END"
intent("receivables_ageing", ["ageing", "aging", "receivables", "aged debt", "debtor"],
       ["اعمار الديون", "اعمار الذمم", "تقادم", "الذمم المدينه", "الذمم", "الديون", "اعمار"],
       lambda p: f"""SELECT a.bucket, COUNT(*) AS invoices,
 ROUND({revx(p, 'a.outstanding')}, 0) AS {col(p, 'outstanding', 'outstanding_usd')}
FROM receivables_ageing a {FX.format(t='a')} WHERE 1 = 1 {cond(p, 'a.currency')} GROUP BY a.bucket ORDER BY {BUCKET_ORDER}""",
       lambda r, p: (lambda tot, late: (f"Outstanding receivables are {mon(tot, p)}; {late / tot:.0%} is more than 60 days overdue.",
                                        f"إجمالي الذمم المدينة {mon(tot, p)}، منها {late / tot:.0%} متأخر أكثر من 60 يوماً."))(
           sum(x[2] for x in r) or 1, sum(x[2] for x in r if x[0] in ("61-90", "90+", "61-90")) ),
       chart=("bucket", None, "donut"), showcase=("Show the receivables ageing", "اعرض أعمار الديون"))

intent("overdue_invoices", ["overdue invoices", "late invoices", "past due", "unpaid invoices", "overdue"],
       ["فواتير متاخره", "فواتير مستحقه", "فواتير غير مسدده", "فواتير لم تسدد", "فواتير غير مدفوعه", "متاخرات", "متاخره"],
       lambda p: f"""SELECT a.invoice_no, a.customer_name{'_ar' if p['L'] == 'ar' else ''} AS customer, a.currency, a.outstanding, a.days_overdue
FROM receivables_ageing a WHERE a.days_overdue > {p['days']} {cond(p, 'a.currency')}
ORDER BY a.outstanding * (SELECT usd_per_unit FROM fx_rates WHERE currency = a.currency) DESC LIMIT {p['n']}""",
       lambda r, p: (f"{len(r)} invoices are more than {p['days']} days overdue; the largest is {r[0][0]} for {fm(r[0][3], r[0][2])} ({r[0][1]}, {r[0][4]} days late).",
                     f"{len(r)} فاتورة متأخرة أكثر من {p['days']} يوماً، أكبرها {r[0][0]} بمبلغ {fm(r[0][3], r[0][2])} ({r[0][1]}، متأخرة {r[0][4]} يوماً).")
       if r else (f"No invoices are more than {p['days']} days overdue.", f"لا توجد فواتير متأخرة أكثر من {p['days']} يوماً."),
       requires=["invoice", "فاتور", "فواتير", "متاخرات"], chart=("invoice_no", "days_overdue"), n=10,
       showcase=("Which invoices are more than 60 days overdue?", "أي فواتير متأخرة أكثر من ٦٠ يوم؟"))

intent("outstanding_by_customer", ["who owes", "owes us", "owe us", "biggest debtors", "top debtors", "outstanding by customer", "customer balances", "highest balances"],
       ["من عليه", "مديونيه", "المدينين", "ارصده العملاء", "عليهم", "يدينون", "المستحق علي العملاء", "اكثر العملاء مديونيه", "العملاء المدينين"],
       lambda p: f"""SELECT a.customer_name{'_ar' if p['L'] == 'ar' else ''} AS customer, COUNT(*) AS open_invoices,
 ROUND({revx(p, 'a.outstanding')}, 0) AS {col(p, 'outstanding', 'outstanding_usd')}
FROM receivables_ageing a {FX.format(t='a')} WHERE 1 = 1 {cond(p, 'a.currency')} GROUP BY a.customer_id ORDER BY 3 DESC LIMIT {p['n']}""",
       lambda r, p: (f"{r[0][0]} owes the most: {mon(r[0][2], p)} across {r[0][1]} open invoices.",
                     f"أكثر عميل مديونية هو {r[0][0]} بمبلغ {mon(r[0][2], p)} على {r[0][1]} فواتير مفتوحة."),
       chart=("customer", None), n=5, showcase=("Which customers owe us the most?", "من أكثر العملاء مديونية علينا؟"))

intent("open_pos", ["open purchase orders", "open pos", "pending purchase orders", "outstanding purchase orders", "open po", "pending pos"],
       ["اوامر الشراء المفتوحه", "اوامر شراء مفتوحه", "طلبات الشراء المفتوحه", "اوامر الشراء المعلقه", "اوامر شراء معلقه", "اوامر الشراء"],
       lambda p: f"""SELECT s.name_{p['L']} AS supplier, COUNT(*) AS open_pos, ROUND(SUM(po.total_amount * f.usd_per_unit), 0) AS value_usd
FROM purchase_orders po JOIN suppliers s ON s.supplier_id = po.supplier_id {FX.format(t='po')}
WHERE po.status IN ('open', 'partial') GROUP BY s.supplier_id ORDER BY value_usd DESC""",
       lambda r, p: (f"{sum(x[1] for x in r)} purchase orders worth {usd(sum(x[2] for x in r))} are still open across {len(r)} suppliers; "
                     f"{r[0][0]} has the largest exposure ({usd(r[0][2])}).",
                     f"يوجد {sum(x[1] for x in r)} أمر شراء مفتوح بقيمة {usd(sum(x[2] for x in r))} لدى {len(r)} موردين، وأكبرها لدى {r[0][0]} ({usd(r[0][2])})."),
       chart=("supplier", "value_usd"), showcase=("What open purchase orders do we have?", "كم أوامر الشراء المفتوحة وقيمتها؟"))

intent("late_pos", ["late purchase orders", "late pos", "delayed purchase orders", "overdue purchase orders", "overdue deliveries", "late deliveries", "suppliers late", "delayed pos", "late supplier", "orders are late", "orders are overdue", "pos are late", "late orders", "orders are delayed", "pos are overdue", "late on delivery"],
       ["اوامر شراء متاخره", "اوامر الشراء المتاخره", "تاخر المورد", "موردين متاخرين", "توريد متاخر", "تاخير التوريد", "متاخره عن موعد", "تاخر"],
       lambda p: f"""SELECT po.po_no, s.name_{p['L']} AS supplier, po.expected_date,
 CAST(julianday('{AS_OF}') - julianday(po.expected_date) AS INTEGER) AS days_late, po.currency, po.total_amount
FROM purchase_orders po JOIN suppliers s ON s.supplier_id = po.supplier_id
WHERE po.status IN ('open', 'partial') AND po.expected_date < '{AS_OF}' ORDER BY days_late DESC LIMIT {p['n']}""",
       lambda r, p: (f"{len(r)} purchase orders are past their expected date; the most delayed is {r[0][0]} from {r[0][1]} ({r[0][3]} days late).",
                     f"{len(r)} أمر شراء تجاوز موعد التوريد، وأكثرها تأخراً {r[0][0]} من {r[0][1]} (متأخر {r[0][3]} يوماً).")
       if r else ("No purchase orders are late.", "لا توجد أوامر شراء متأخرة."),
       requires=["purchase", "po", "supplier", "مورد", "شراء", "توريد"], chart=("po_no", "days_late"), n=15,
       showcase=("Which purchase orders are late?", "أي أوامر شراء متأخرة عن موعد التوريد؟"))

intent("revenue_by_country", ["by country", "by currency", "per country", "sales by country", "revenue by country"],
       ["حسب الدوله", "حسب العمله", "كل دوله", "حسب البلد", "الايرادات حسب الدول"],
       lambda p: f"""SELECT c.country, i.currency, COUNT(*) AS invoices, ROUND(SUM(i.total_amount * f.usd_per_unit), 0) AS revenue_usd
FROM invoices i JOIN customers c ON c.customer_id = i.customer_id {FX.format(t='i')} GROUP BY c.country, i.currency ORDER BY revenue_usd DESC""",
       lambda r, p: (f"{r[0][0]} is the biggest market with {usd(r[0][3])} ({r[0][3] / sum(x[3] for x in r):.0%} of {usd(sum(x[3] for x in r))}).",
                     f"{r[0][0]} هي أكبر سوق بإيرادات {usd(r[0][3])} ({r[0][3] / sum(x[3] for x in r):.0%} من {usd(sum(x[3] for x in r))})."),
       chart=("country", "revenue_usd"))

intent("top_items", ["best selling", "top selling", "top items", "best sellers", "top products", "most sold"],
       ["الاكثر مبيعا", "اكثر الاصناف", "اكثر المنتجات", "الاكثر بيعا", "اكثر ما بعنا"],
       lambda p: f"""SELECT it.name_{p['L']} AS item, SUM(l.qty) AS units_sold, ROUND(SUM(l.line_total * f.usd_per_unit), 0) AS revenue_usd
FROM invoice_lines l JOIN items it ON it.item_id = l.item_id JOIN invoices i ON i.invoice_id = l.invoice_id {FX.format(t='i')}
GROUP BY it.item_id ORDER BY units_sold DESC, it.item_id LIMIT {p['n']}""",
       lambda r, p: (f"Best seller is {r[0][0]} with {r[0][1]:,} units ({usd(r[0][2])}).", f"الأكثر مبيعاً هو {r[0][0]} بعدد {r[0][1]:,} وحدة ({usd(r[0][2])})."),
       chart=("item", "units_sold"), n=5)

intent("supplier_spend", ["supplier spend", "spend by supplier", "purchases by supplier", "spend per supplier", "procurement spend"],
       ["مشتريات الموردين", "المشتريات حسب المورد", "الصرف علي الموردين", "حسب المورد", "انفاق الموردين"],
       lambda p: f"""SELECT s.name_{p['L']} AS supplier, COUNT(*) AS pos, ROUND(SUM(po.total_amount * f.usd_per_unit), 0) AS spend_usd
FROM purchase_orders po JOIN suppliers s ON s.supplier_id = po.supplier_id {FX.format(t='po')}
WHERE po.status <> 'cancelled' GROUP BY s.supplier_id ORDER BY spend_usd DESC LIMIT {p['n']}""",
       lambda r, p: (f"Largest supplier by spend is {r[0][0]} at {usd(r[0][2])} over {r[0][1]} purchase orders.", f"أكبر مورد من حيث الإنفاق هو {r[0][0]} بمبلغ {usd(r[0][2])} على {r[0][1]} أمر شراء."),
       chart=("supplier", "spend_usd"), n=8)

intent("item_stock", ["stock of", "how many", "how much", "in stock", "on hand", "do we have", "stock level"],
       ["كم عندنا", "كم عندي", "مخزون", "كميه", "المتوفر", "متوفر", "موجود"],
       lambda p: f"""SELECT w.name_{p['L']} AS warehouse, s.qty_on_hand, i.reorder_level
FROM stock_levels s JOIN items i ON i.item_id = s.item_id JOIN warehouses w ON w.warehouse_id = s.warehouse_id
WHERE i.item_id = {int(p['item'][0]) if p['item'] else 0} ORDER BY s.qty_on_hand DESC""",
       lambda r, p: (f"We hold {sum(x[1] for x in r):,} units of {p['item'][1]} in total; most in {r[0][0]} ({r[0][1]:,}).",
                     f"لدينا {sum(x[1] for x in r):,} وحدة من {p['item'][1]}، أكثرها في {r[0][0]} ({r[0][1]:,})."),
       requires=["__item__"], chart=("warehouse", "qty_on_hand"))

intent("customer_balance", ["balance", "owe", "owes", "outstanding", "statement", "receivable"],
       ["رصيد", "مديونيه", "عليه", "مستحق", "كشف حساب", "المتبقي"],
       lambda p: f"""SELECT invoice_no, due_date, days_overdue, currency, outstanding FROM receivables_ageing
WHERE customer_id = {int(p['cust'][0]) if p['cust'] else 0} ORDER BY days_overdue DESC""",
       lambda r, p: (f"{p['cust'][1]} has {len(r)} open invoices totalling {fm(sum(x[4] for x in r), r[0][3])}; the oldest is {r[0][2]} days past due.",
                     f"لدى {p['cust'][1]} عدد {len(r)} فواتير مفتوحة بإجمالي {fm(sum(x[4] for x in r), r[0][3])}، وأقدمها متأخرة {r[0][2]} يوماً.")
       if r else (f"{p['cust'][1]} has no open invoices.", f"لا توجد فواتير مفتوحة على {p['cust'][1]}."),
       requires=["__cust__"], chart="none")

SHOWCASE = [(k, v["showcase"]) for k, v in INTENTS.items() if v["showcase"]]
WORDNUM = {"three": 3, "five": 5, "ten": 10, "ثلاثه": 3, "ثلاث": 3, "خمسه": 5, "خمس": 5, "عشره": 10, "عشر": 10}


def params(nq, L, db):
    toks = nq.split()
    p = dict(L=L, cur=None, n=None, days=0, item=None, cust=None)
    for cur, words in CUR_WORDS.items():
        if any((w in toks) if w.isascii() else any(w in t for t in toks) for w in words):
            p["cur"] = cur
    m = re.search(r"(\d+)\s*(?:day|days|يوم|ايام)", nq)
    if m:
        p["days"] = int(m.group(1))
    else:
        m = re.search(r"\b(\d{1,3})\b", nq)
        p["n"] = int(m.group(1)) if m else None
    p["n"] = p["n"] or next((WORDNUM[t] for t in toks if t in WORDNUM), None)
    for iid, en, ar in db.execute("SELECT item_id, name_en, name_ar FROM items"):
        if norm(en) in nq or norm(ar) in nq:
            p["item"] = (iid, ar if L == "ar" else en)
    for cid, en, ar in db.execute("SELECT customer_id, name_en, name_ar FROM customers"):
        if norm(en) in nq or norm(ar) in nq:
            p["cust"] = (cid, ar if L == "ar" else en)
    return p


def canned(question, L, db):
    nq = " " + norm(question) + " "
    p = params(nq, L, db)
    text = " " + re.sub(r"\s+", " ", re.sub(r"\b\d+\b", " ", nq)) + " "  # numbers are parameters, not keywords
    best, score = None, 0
    for name, it in INTENTS.items():
        need = [r for r in it["requires"] if not r.startswith("__")]
        if ("__item__" in it["requires"] and not p["item"]) or ("__cust__" in it["requires"] and not p["cust"]):
            continue
        if need and not any(r in text for r in need):
            continue
        s = sum(len(t.split()) + 1 for t in it["en"] + it["ar"] if t in text)
        s += 6 if (name == "item_stock" and p["item"]) or (name == "customer_balance" and p["cust"]) else 0
        if s > score:
            best, score = name, s
    if not best:
        return None
    p["n"] = p["n"] or INTENTS[best]["n"] or 10
    return best, INTENTS[best]["sql"](p), p


# ---------------------------------------------------------------- LLM mode
def llm_config():
    g = os.environ.get
    if g("DEMO_MODE") == "canned":
        return None
    if g("AZURE_OPENAI_ENDPOINT") and g("AZURE_OPENAI_API_KEY") and g("AZURE_OPENAI_DEPLOYMENT"):
        url = (f"{g('AZURE_OPENAI_ENDPOINT').rstrip('/')}/openai/deployments/{g('AZURE_OPENAI_DEPLOYMENT')}"
               f"/chat/completions?api-version={g('AZURE_OPENAI_API_VERSION', '2024-10-21')}")
        return url, {"api-key": g("AZURE_OPENAI_API_KEY")}, None
    if g("OPENAI_API_KEY"):
        return (g("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/chat/completions",
                {"Authorization": "Bearer " + g("OPENAI_API_KEY")}, g("OPENAI_MODEL", "gpt-4o-mini"))
    return None


def chat(cfg, messages, json_mode):
    url, headers, model = cfg
    body = {"messages": messages, "temperature": 0}
    if model:
        body["model"] = model
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["choices"][0]["message"]["content"]


def schema_prompt(db):
    return "\n".join(f"{t}({', '.join(c[1] + ' ' + c[2] for c in db.execute(f'PRAGMA table_info({t})'))})"
                     for t in sorted(guard.ALLOWED_TABLES))


def llm_sql(cfg, question, L, db, error=None):
    sys_msg = f"""You translate business questions (Arabic incl. Gulf dialect, or English) into ONE read-only SQLite SELECT.
Tables (only these exist):
{schema_prompt(db)}
Rules: SELECT only, no semicolons, use only the tables above. Today is {AS_OF} (data is frozen at this date).
Amounts are in each row's currency (KWD 3 decimals, QAR/SAR 2). To add across currencies join fx_rates and multiply by usd_per_unit, and name the column *_usd.
Open receivables = view receivables_ageing (outstanding, days_overdue, bucket). Open POs: status IN ('open','partial').
Low stock: stock_levels.qty_on_hand < items.reorder_level. Question language is {'Arabic: select name_ar / customer_name_ar columns' if L == 'ar' else 'English: select name_en columns'}.
Always ORDER BY something sensible and LIMIT 50 for lists. Reply as JSON: {{"sql": "..."}}"""
    msgs = [{"role": "system", "content": sys_msg}, {"role": "user", "content": question}]
    if error:
        msgs.append({"role": "user", "content": f"That query was rejected or failed: {error}. Return a corrected JSON."})
    return json.loads(chat(cfg, msgs, True))["sql"]


def llm_answer(cfg, question, L, cols, rows):
    data = json.dumps({"columns": cols, "rows": rows[:25]}, ensure_ascii=False)
    msg = [{"role": "system", "content": f"Answer the manager's question in {'Arabic' if L == 'ar' else 'English'} in 1-3 short sentences using ONLY the numbers in the data. Mention currency. No markdown."},
           {"role": "user", "content": f"Question: {question}\nData: {data}"}]
    return chat(cfg, msg, False).strip()


# ---------------------------------------------------------------- public API
NOT_CHARTED = {"invoices", "open_invoices", "open_pos", "pos", "reorder_level", "qty_on_hand", "days_overdue"}


def pick_chart(cols, rows, hint=None):
    if hint == "none" or len(rows) < 2:
        return None
    num = [i for i, c in enumerate(cols) if all(isinstance(r[i], (int, float)) for r in rows) and not c.endswith("_id") and c not in NOT_CHARTED]
    lab = next((i for i, c in enumerate(cols) if any(isinstance(r[i], str) for r in rows)), None)
    if hint and hint[0] in cols:
        lab = cols.index(hint[0])
        if hint[1] in cols:
            num = [cols.index(hint[1])]
    if lab is None or not num:
        return None
    val = num[-1]
    rr = rows[:15]
    kind = "line" if all(re.match(r"^\d{4}-\d{2}$", str(r[lab])) for r in rr) else "bar"
    if hint and len(hint) > 2:
        kind = hint[2]
    return {"type": kind, "label": cols[lab], "value": cols[val], "labels": [str(r[lab]) for r in rr], "values": [r[val] for r in rr]}


def ask(question, db_path=DB_PATH, force_canned=False):
    question = (question or "").strip()[:500]
    L = lang_of(question)
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    out = {"question": question, "language": L, "mode": "canned", "as_of": AS_OF, "sql": "", "answer": "", "columns": [], "rows": [],
           "chart": None, "sources": [], "truncated": False, "error": None, "intent": None, "warning": None}
    try:
        cfg = None if force_canned else llm_config()
        sql = hint = None
        llm_answer_text = None
        if cfg:
            try:
                err = None
                for _ in range(2):
                    sql = llm_sql(cfg, question, L, db, err)
                    try:
                        cols, rows, tables, trunc = guard.run(db_path, sql)
                        break
                    except guard.Unsafe as e:
                        err = str(e)
                else:
                    raise guard.Unsafe(err)
                out["mode"] = "llm"
                if L == "ar":
                    rows = [[VALUE_AR.get(v, v) if isinstance(v, str) else v for v in r] for r in rows]
                llm_answer_text = llm_answer(cfg, question, L, cols, rows) if rows else ("لا توجد نتائج." if L == "ar" else "No matching rows.")
            except Exception as e:  # network, bad key, unsafe twice: fall back to the deterministic engine
                out["warning"] = f"LLM unavailable ({type(e).__name__}); used the canned engine"
                sql = None
        if sql is None:
            m = canned(question, L, db)
            if not m:
                out["error"] = ("لم أفهم السؤال في وضع العرض. جرّب أحد الأسئلة المقترحة." if L == "ar" else
                                "I could not match that question in demo mode. Try one of the suggested questions.")
                return out
            name, sql, p = m
            hint = INTENTS[name]["chart"]
            out["intent"] = name
            cols, rows, tables, trunc = guard.run(db_path, sql)
            if L == "ar":
                rows = [[VALUE_AR.get(v, v) if isinstance(v, str) else v for v in r] for r in rows]
            llm_answer_text = INTENTS[name]["ans"](rows, p)[1 if L == "ar" else 0]
        out.update(sql=sql.strip(), columns=cols, rows=rows, sources=tables, truncated=trunc, chart=pick_chart(cols, rows, hint),
                   answer=llm_answer_text)
        if trunc:
            out["answer"] += (" (عُرض أول %d صفاً فقط)" if L == "ar" else " (showing the first %d rows)") % guard.MAX_ROWS
    except guard.Unsafe as e:
        out["error"] = f"Blocked by the read-only guard: {e}"
    finally:
        db.close()
    return out
