"""Self-contained local seafood retail demo; deliberately has no Luna database dependency."""
import csv
import io
import json
import os
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'fish_price_demo.sqlite3'
CATALOG = ROOT / 'catalog_2026-09-26.csv'
with CATALOG.open(encoding='utf-8-sig', newline='') as source:
    PRICE_ROWS = list(csv.DictReader(source))
PRODUCTS = [
    (r['category'], r['name'], r['unit'], int(r['price_kzt']),
     int((Decimal(r['price_kzt']) * Decimal('0.70')).quantize(Decimal('1'), rounding=ROUND_HALF_UP)),
     r['barcode'], r['ntin'], r['weight_kg'], r['country'], r['promo_price_kzt'])
    for r in PRICE_ROWS
]
BRANCHES = ['Центр', 'Юго-Восток', 'Майкудук', 'Пришахтинск', 'Новый город']
PEOPLE = ['Айгерим', 'Марат', 'Динара', 'Ержан', 'Алия', 'Руслан', 'Мадина', 'Нурлан', 'Жанар', 'Азамат', 'Аружан', 'Бекзат', 'Айдана', 'Диас', 'Салтанат', 'Тимур', 'Камилла', 'Олжас', 'Назерке', 'Арман']
CUSTOMERS = ['Айгерим', 'Марат', 'Динара', 'Ержан', 'Мадина', 'Алия', 'Жанар', 'Азамат', 'Айдана', 'Руслан', 'Салтанат', 'Арман']
SUPPLIERS = ['Северный улов (демо)', 'Океан Трейд (демо)', 'Мясной ряд (демо)', 'Кухня Fish (демо)']


def connect():
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def init():
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS branches(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, category TEXT NOT NULL, name TEXT NOT NULL, unit TEXT NOT NULL, price INTEGER NOT NULL, cost INTEGER NOT NULL, barcode TEXT NOT NULL, ntin TEXT NOT NULL, weight_kg TEXT NOT NULL, country TEXT NOT NULL, promo_price TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS stock(branch_id INTEGER NOT NULL REFERENCES branches(id), product_id INTEGER NOT NULL REFERENCES products(id), qty REAL NOT NULL CHECK(qty >= 0), PRIMARY KEY(branch_id, product_id));
        CREATE TABLE IF NOT EXISTS customer_groups(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, discount_pct REAL NOT NULL DEFAULT 0, min_spent INTEGER NOT NULL DEFAULT 0, min_purchases INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT UNIQUE NOT NULL, points INTEGER NOT NULL DEFAULT 0, group_id INTEGER REFERENCES customer_groups(id));
        CREATE TABLE IF NOT EXISTS movements(id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, branch_id INTEGER NOT NULL, product_id INTEGER NOT NULL, kind TEXT NOT NULL, qty REAL NOT NULL, unit_cost INTEGER NOT NULL, note TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sales(id INTEGER PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL, branch_id INTEGER NOT NULL, product_id INTEGER NOT NULL, customer_id INTEGER, qty REAL NOT NULL, total INTEGER NOT NULL, earned INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS employees(id INTEGER PRIMARY KEY, name TEXT NOT NULL, branch_id INTEGER NOT NULL REFERENCES branches(id), role TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS suppliers(id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS promotions(id INTEGER PRIMARY KEY, name TEXT NOT NULL, product_id INTEGER NOT NULL REFERENCES products(id), branch_id INTEGER REFERENCES branches(id), starts_on TEXT NOT NULL, ends_on TEXT NOT NULL, min_qty REAL NOT NULL CHECK(min_qty > 0), promo_price INTEGER, discount_pct REAL, active INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL DEFAULT 'manual', CHECK((promo_price IS NOT NULL) != (discount_pct IS NOT NULL)));
        ''')
        columns = {row['name'] for row in db.execute('PRAGMA table_info(sales)')}
        for name, definition in [('base_total', 'INTEGER'), ('discount', 'INTEGER'), ('promotion_id', 'INTEGER')]:
            if name not in columns:
                db.execute(f'ALTER TABLE sales ADD COLUMN {name} {definition}')
        customer_columns = {row['name'] for row in db.execute('PRAGMA table_info(customers)')}
        if 'group_id' not in customer_columns:
            db.execute('ALTER TABLE customers ADD COLUMN group_id INTEGER')
        if not db.execute('SELECT count(*) FROM customer_groups').fetchone()[0]:
            db.executemany('INSERT INTO customer_groups(name,discount_pct,min_spent,min_purchases) VALUES(?,?,?,?)', [
                ('Обычный', 0, 0, 0), ('Постоянный', 5, 100000, 5), ('VIP', 10, 500000, 15), ('Оптовик', 15, 1000000, 30)])
        if not db.execute('SELECT count(*) FROM branches').fetchone()[0]:
            db.executemany('INSERT INTO branches(name) VALUES(?)', [(x,) for x in BRANCHES])
            db.executemany('INSERT INTO products(category,name,unit,price,cost,barcode,ntin,weight_kg,country,promo_price) VALUES(?,?,?,?,?,?,?,?,?,?)', PRODUCTS)
            for branch in range(1, 6):
                for product in range(1, len(PRODUCTS) + 1):
                    qty = float(18 + (branch * 3 + product * 7) % 19)
                    db.execute('INSERT INTO stock VALUES(?,?,?)', (branch, product, qty))
                    db.execute('INSERT INTO movements(created_at,branch_id,product_id,kind,qty,unit_cost,note) VALUES(?,?,?,?,?,?,?)',
                               ((datetime.now()-timedelta(days=12)).isoformat(timespec='seconds'), branch, product, 'Приход', qty, PRODUCTS[product-1][4], 'ДЕМО: начальная приёмка'))
        if not db.execute('SELECT count(*) FROM employees').fetchone()[0]:
            db.executemany('INSERT INTO employees(name,branch_id,role) VALUES(?,?,?)',
                           [(name+' (демо)', i//4+1, ['Управляющий', 'Продавец', 'Продавец', 'Кладовщик'][i%4]) for i,name in enumerate(PEOPLE)])
        if not db.execute('SELECT count(*) FROM suppliers').fetchone()[0]:
            db.executemany('INSERT INTO suppliers(name,category) VALUES(?,?)', zip(SUPPLIERS, ['Рыба', 'Морепродукты', 'Мясо', 'Готовые блюда']))
        for product_id, item in enumerate(PRODUCTS, 1):
            if item[9] and not db.execute("SELECT 1 FROM promotions WHERE product_id=? AND source='price-list'", (product_id,)).fetchone():
                db.execute('INSERT INTO promotions(name,product_id,branch_id,starts_on,ends_on,min_qty,promo_price,discount_pct,active,source) VALUES(?,?,NULL,?,?,?,?,NULL,0,?)',
                           ('Акция из прайса: '+item[1], product_id, '2026-09-26', '2026-09-26', 1, int(item[9]), 'price-list'))
        existing_phones = {x[0] for x in db.execute('SELECT phone FROM customers')}
        for i, name in enumerate(CUSTOMERS, 1):
            phone = f'+7700000{i:04}'
            if phone not in existing_phones:
                db.execute('INSERT INTO customers(name,phone,points) VALUES(?,?,0)', (name+' (демо)',phone))
        regular = db.execute("SELECT id FROM customer_groups WHERE name='Обычный'").fetchone()['id']
        db.execute('UPDATE customers SET group_id=COALESCE(group_id,?)', (regular,))
        # Historical transactions are generated only for a fresh stand, so restarting never duplicates them.
        if not db.execute('SELECT count(*) FROM sales').fetchone()[0]:
            for day in range(10, 0, -1):
                for branch in range(1, 6):
                    for n in range(3):
                        product = (day*3 + branch*5 + n*7) % len(PRODUCTS) + 1
                        qty = 1 if PRODUCTS[product-1][2] == 'шт' else [0.5, 0.8, 1.2][n]
                        customer = (day + branch + n) % len(CUSTOMERS) + 1 if n != 2 else None
                        moment = (datetime.now()-timedelta(days=day)).replace(hour=11+n*3,minute=branch*7,second=0,microsecond=0).isoformat(timespec='seconds')
                        total = int((Decimal(PRODUCTS[product-1][3])*Decimal(str(qty))).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
                        earned = int((Decimal(total)*Decimal('0.03')).quantize(Decimal('1'), rounding=ROUND_HALF_UP)) if customer else 0
                        db.execute('UPDATE stock SET qty=round(qty-?,3) WHERE branch_id=? AND product_id=?', (qty,branch,product))
                        db.execute('INSERT INTO movements(created_at,branch_id,product_id,kind,qty,unit_cost,note) VALUES(?,?,?,?,?,?,?)',
                                   (moment,branch,product,'Продажа',qty,PRODUCTS[product-1][4],'ДЕМО: историческая продажа'))
                        db.execute('INSERT INTO sales(request_id,created_at,branch_id,product_id,customer_id,qty,total,earned) VALUES(?,?,?,?,?,?,?,?)',
                                   (f'demo-{day}-{branch}-{n}',moment,branch,product,customer,qty,total,earned))
                        if customer:
                            db.execute('UPDATE customers SET points=points+? WHERE id=?', (earned,customer))
                    spoil = (day+branch)%len(PRODUCTS)+1
                    db.execute('UPDATE stock SET qty=round(qty-0.2,3) WHERE branch_id=? AND product_id=?', (branch,spoil))
                    db.execute('INSERT INTO movements(created_at,branch_id,product_id,kind,qty,unit_cost,note) VALUES(?,?,?,?,?,?,?)',
                               (moment,branch,spoil,'Списание',0.2,PRODUCTS[spoil-1][4],'ДЕМО: срок годности'))


def rows(db, sql):
    return [dict(x) for x in db.execute(sql)]


def snapshot():
    with connect() as db:
        return {
            'branches': rows(db, 'SELECT * FROM branches'),
            'products': rows(db, 'SELECT * FROM products'),
            'stock': rows(db, 'SELECT * FROM stock'),
            'customers': rows(db, 'SELECT * FROM customers'),
            'customer_groups': rows(db, 'SELECT * FROM customer_groups WHERE active=1 ORDER BY min_spent, min_purchases'),
            'employees': rows(db, 'SELECT e.*,b.name branch FROM employees e JOIN branches b ON b.id=e.branch_id'),
            'suppliers': rows(db, 'SELECT * FROM suppliers'),
            'promotions': rows(db, 'SELECT a.*,p.name product,b.name branch FROM promotions a JOIN products p ON p.id=a.product_id LEFT JOIN branches b ON b.id=a.branch_id ORDER BY a.id DESC'),
            'movements': rows(db, 'SELECT m.*,b.name branch,p.name product FROM movements m JOIN branches b ON b.id=m.branch_id JOIN products p ON p.id=m.product_id ORDER BY m.id DESC'),
            'sales': rows(db, 'SELECT s.*,b.name branch,p.name product,c.name customer FROM sales s JOIN branches b ON b.id=s.branch_id JOIN products p ON p.id=s.product_id LEFT JOIN customers c ON c.id=s.customer_id ORDER BY s.id DESC'),
        }


def quantity(value):
    q = Decimal(str(value))
    if not q.is_finite() or q <= 0 or q > 10000 or q.as_tuple().exponent < -3:
        raise ValueError('Количество должно быть от 0.001 до 10000, не более 3 знаков после запятой')
    return float(q)


def current_price(db, item, branch, qty, customer_id=None, today=None):
    today = today or datetime.now(ZoneInfo('Asia/Almaty')).date().isoformat()
    base = Decimal(item['price'])
    gross = (base * Decimal(str(qty))).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    candidates = []
    if customer_id:
        group = db.execute('SELECT g.discount_pct,g.name FROM customers c JOIN customer_groups g ON g.id=c.group_id WHERE c.id=? AND g.active=1', (customer_id,)).fetchone()
        if group and group['discount_pct'] > 0:
            total = (base * Decimal(str(qty)) * (Decimal('1') - Decimal(str(group['discount_pct'])) / 100)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
            candidates.append((int(total), None, group['name']))
    for rule in db.execute('SELECT * FROM promotions WHERE product_id=? AND active=1 AND (branch_id IS NULL OR branch_id=?) AND starts_on<=? AND ends_on>=? AND min_qty<=?',
                           (item['id'], branch, today, today, qty)):
        unit = Decimal(rule['promo_price']) if rule['promo_price'] is not None else base * (Decimal('1') - Decimal(str(rule['discount_pct'])) / 100)
        total = (unit * Decimal(str(qty))).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        if total < gross:
            candidates.append((int(total), rule['id'], ''))
    final, promotion_id, group_name = min(candidates, key=lambda x: x[0], default=(int(gross), None, ''))
    return {'base_total': int(gross), 'total': final, 'discount': int(gross) - final, 'promotion_id': promotion_id, 'group_name': group_name}


def save_promotion(data):
    product = int(data['product_id'])
    branch = int(data['branch_id']) if data.get('branch_id') else None
    start, end = str(data['starts_on']), str(data['ends_on'])
    if datetime.strptime(start, '%Y-%m-%d') > datetime.strptime(end, '%Y-%m-%d'):
        raise ValueError('Дата окончания раньше начала')
    min_qty = quantity(data.get('min_qty', 1))
    pct = Decimal(str(data['discount_pct'])) if data.get('discount_pct') not in (None, '') else None
    price = int(data['promo_price']) if data.get('promo_price') not in (None, '') else None
    if (pct is None) == (price is None) or pct is not None and (not pct.is_finite() or pct <= 0 or pct >= 100) or price is not None and price < 0:
        raise ValueError('Укажите одну скидку: цену от 0 ₸ или процент больше 0 и меньше 100')
    with connect() as db:
        item = db.execute('SELECT * FROM products WHERE id=?', (product,)).fetchone()
        if item is None or branch and not db.execute('SELECT 1 FROM branches WHERE id=?', (branch,)).fetchone():
            raise ValueError('Товар или филиал не найден')
        if price is not None and price >= item['price']:
            raise ValueError('Акционная цена должна быть ниже обычной')
        if data.get('id'):
            promotion_id = int(data['id'])
            if not db.execute('SELECT 1 FROM promotions WHERE id=?', (promotion_id,)).fetchone():
                raise ValueError('Акция не найдена')
            db.execute('UPDATE promotions SET product_id=?,branch_id=?,starts_on=?,ends_on=?,min_qty=?,promo_price=?,discount_pct=?,active=? WHERE id=?',
                       (product,branch,start,end,min_qty,price,float(pct) if pct else None,int(data.get('active') is True),promotion_id))
        else:
            name = str(data.get('name', '')).strip()
            if not name or len(name) > 160:
                raise ValueError('Нужно название акции до 160 символов')
            promotion_id = db.execute('INSERT INTO promotions(name,product_id,branch_id,starts_on,ends_on,min_qty,promo_price,discount_pct,active) VALUES(?,?,?,?,?,?,?,?,?)',
                                      (name,product,branch,start,end,min_qty,price,float(pct) if pct else None,int(data.get('active') is True))).lastrowid
        return {'promotion_id': promotion_id}

def save_customer(data):
    name, phone = str(data.get('name','')).strip(), str(data.get('phone','')).strip()
    if not name or not phone or len(name) > 120 or len(phone) > 40:
        raise ValueError('Укажите имя и телефон клиента')
    group = int(data.get('group_id') or 1)
    with connect() as db:
        if not db.execute('SELECT 1 FROM customer_groups WHERE id=? AND active=1',(group,)).fetchone():
            raise ValueError('Группа не найдена')
        try:
            if data.get('id'):
                cid=int(data['id']); db.execute('UPDATE customers SET name=?, phone=?, group_id=? WHERE id=?',(name,phone,group,cid))
            else:
                cid=db.execute('INSERT INTO customers(name,phone,points,group_id) VALUES(?,?,0,?)',(name,phone,group)).lastrowid
        except sqlite3.IntegrityError:
            raise ValueError('Клиент с таким телефоном уже есть')
        return {'customer_id':cid}

def save_group(data):
    gid=int(data.get('id') or 0); name=str(data.get('name','')).strip()
    pct=Decimal(str(data.get('discount_pct',0))); spent=int(data.get('min_spent',0)); purchases=int(data.get('min_purchases',0))
    if not gid or not name or pct<0 or pct>=100 or spent<0 or purchases<0: raise ValueError('Проверьте параметры группы')
    with connect() as db:
        if not db.execute('SELECT 1 FROM customer_groups WHERE id=?',(gid,)).fetchone(): raise ValueError('Группа не найдена')
        db.execute('UPDATE customer_groups SET name=?,discount_pct=?,min_spent=?,min_purchases=? WHERE id=?',(name,float(pct),spent,purchases,gid))
        return {'group_id':gid}

def save_product(data):
    pid=int(data.get('id') or 0); name=str(data.get('name','')).strip(); price=int(data.get('price') or 0)
    if not pid or not name or price<0: raise ValueError('Проверьте товар и цену')
    with connect() as db:
        if not db.execute('SELECT 1 FROM products WHERE id=?',(pid,)).fetchone(): raise ValueError('Товар не найден')
        db.execute('UPDATE products SET name=?, price=? WHERE id=?',(name,price,pid))
    return {'product_id':pid}

def save_employee(data):
    eid=int(data.get('id') or 0); name=str(data.get('name','')).strip(); role=str(data.get('role','')).strip()
    if not eid or not name or not role: raise ValueError('Проверьте сотрудника')
    with connect() as db:
        db.execute('UPDATE employees SET name=?, role=? WHERE id=?',(name,role,eid))
    return {'employee_id':eid}

def save_supplier(data):
    sid=int(data.get('id') or 0); name=str(data.get('name','')).strip(); category=str(data.get('category','')).strip()
    if not sid or not name or not category: raise ValueError('Проверьте поставщика')
    with connect() as db:
        db.execute('UPDATE suppliers SET name=?, category=? WHERE id=?',(name,category,sid))
    return {'supplier_id':sid}


def quote(data):
    branch, product = int(data['branch_id']), int(data['product_id'])
    qty = quantity(data['qty'])
    with connect() as db:
        if not db.execute('SELECT 1 FROM branches WHERE id=?', (branch,)).fetchone():
            raise ValueError('Филиал не найден')
        item = db.execute('SELECT * FROM products WHERE id=?', (product,)).fetchone()
        if item is None:
            raise ValueError('Товар не найден')
        return current_price(db, item, branch, qty, int(data['customer_id']) if data.get('customer_id') else None)


def mutate(data):
    branch, product = int(data['branch_id']), int(data['product_id'])
    qty = quantity(data['qty'])
    kind = data['kind']
    if kind not in ('Приход', 'Списание', 'Продажа'):
        raise ValueError('Неизвестная операция')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        stock = db.execute('SELECT qty FROM stock WHERE branch_id=? AND product_id=?', (branch, product)).fetchone()
        item = db.execute('SELECT * FROM products WHERE id=?', (product,)).fetchone()
        if stock is None or item is None:
            raise ValueError('Товар или филиал не найден')
        if kind == 'Продажа':
            request_id = str(data.get('request_id', '')).strip()
            if not request_id or len(request_id) > 100:
                raise ValueError('Нужен уникальный request_id чека')
            existing = db.execute('SELECT id FROM sales WHERE request_id=?', (request_id,)).fetchone()
            if existing:
                return {'sale_id': existing['id'], 'duplicate': True}
        if kind != 'Приход' and stock['qty'] + 1e-8 < qty:
            raise ValueError('Недостаточно товара на складе этого филиала')
        customer_id = None
        if kind == 'Продажа' and data.get('customer_id'):
            customer_id = int(data['customer_id'])
            if not db.execute('SELECT id FROM customers WHERE id=?', (customer_id,)).fetchone():
                raise ValueError('Клиент не найден')
        cost = int(item['cost'])
        if kind == 'Приход' and data.get('unit_cost') not in (None, ''):
            cost = int(data['unit_cost'])
            if cost < 0 or cost > 10000000:
                raise ValueError('Некорректная себестоимость')
            db.execute('UPDATE products SET cost=? WHERE id=?', (cost, product))
        delta = qty if kind == 'Приход' else -qty
        db.execute('UPDATE stock SET qty=round(qty+?,3) WHERE branch_id=? AND product_id=?', (delta, branch, product))
        now = datetime.now().isoformat(timespec='seconds')
        note = str(data.get('note', 'ДЕМО'))[:150]
        db.execute('INSERT INTO movements(created_at,branch_id,product_id,kind,qty,unit_cost,note) VALUES(?,?,?,?,?,?,?)', (now, branch, product, kind, qty, cost, note))
        if kind == 'Продажа':
            pricing = current_price(db, item, branch, qty, customer_id)
            total = pricing['total']
            earned = int((Decimal(total) * Decimal('0.03')).quantize(Decimal('1'), rounding=ROUND_HALF_UP)) if customer_id else 0
            cur = db.execute('INSERT INTO sales(request_id,created_at,branch_id,product_id,customer_id,qty,total,earned,base_total,discount,promotion_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                             (request_id, now, branch, product, customer_id, qty, total, earned, pricing['base_total'], pricing['discount'], pricing['promotion_id']))
            if customer_id:
                db.execute('UPDATE customers SET points=points+? WHERE id=?', (earned, customer_id))
                spend = db.execute('SELECT COALESCE(SUM(total),0) value, COUNT(*) count FROM sales WHERE customer_id=?', (customer_id,)).fetchone()
                db.execute('UPDATE customers SET group_id=(SELECT id FROM customer_groups WHERE active=1 AND min_spent<=? AND min_purchases<=? ORDER BY discount_pct DESC LIMIT 1) WHERE id=?', (spend['value'], spend['count'], customer_id))
            return {'sale_id': cur.lastrowid, 'total': total, 'earned': earned, 'discount': pricing['discount'], 'promotion_id': pricing['promotion_id']}
        return {'ok': True}


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, content_type='application/json; charset=utf-8'):
        raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/':
            self.send(200, (ROOT / 'index.html').read_bytes(), 'text/html; charset=utf-8')
        elif path == '/warehouse.css':
            self.send(200, (ROOT / 'warehouse.css').read_bytes(), 'text/css; charset=utf-8')
        elif path == '/blueprint':
            self.send(200, (ROOT / 'FISH_STORE_BLUEPRINT_RU.md').read_bytes(), 'text/plain; charset=utf-8')
        elif path == '/api/state':
            self.send(200, snapshot())
        elif path == '/export/stock.csv':
            state = snapshot()
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(['Филиал', 'Штрихкод', 'Категория', 'Товар', 'Ед.', 'Остаток', 'Цена ₸', 'Оценка себестоимости ₸'])
            for entry in state['stock']:
                p = state['products'][entry['product_id'] - 1]
                writer.writerow([state['branches'][entry['branch_id'] - 1]['name'], p['barcode'], p['category'], p['name'], p['unit'], entry['qty'], p['price'], p['cost']])
            self.send(200, ('\ufeff' + buf.getvalue()).encode('utf-8'), 'text/csv; charset=utf-8')
        else:
            self.send(404, {'error': 'Не найдено'})

    def do_POST(self):
        if self.path not in ('/api/operation', '/api/promotions', '/api/quote', '/api/customers', '/api/groups', '/api/products', '/api/employees', '/api/suppliers'):
            return self.send(404, {'error': 'Не найдено'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size < 1 or size > 10000:
                raise ValueError('Неверный размер запроса')
            data = json.loads(self.rfile.read(size))
            action = save_promotion if self.path == '/api/promotions' else save_customer if self.path == '/api/customers' else save_group if self.path == '/api/groups' else save_product if self.path == '/api/products' else save_employee if self.path == '/api/employees' else save_supplier if self.path == '/api/suppliers' else quote if self.path == '/api/quote' else mutate
            self.send(200, action(data))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self.send(400, {'error': str(exc)})


if __name__ == '__main__':
    init()
    # Render передаёт порт через переменную PORT; локально сохраняем 8091.
    port = int(os.environ.get('PORT', '8091'))
    host = '0.0.0.0' if os.environ.get('PORT') else '127.0.0.1'
    print(f'Fish_opt_krg demo: http://{host}:{port}')
    ThreadingHTTPServer((host, port), Handler).serve_forever()
