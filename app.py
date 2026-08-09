#!/usr/bin/env python3
"""枫叶炒股 - Flask 后端"""
import sqlite3, json, time, random, math, threading, hashlib, os, queue
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, Response, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='.')
CORS(app)

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock.db')

STOCKS = {
    '000001': {'name': '平安银行', 'base': 12.5},
    '600519': {'name': '贵州茅台', 'base': 1680.0},
    '000858': {'name': '五粮液', 'base': 156.8},
    '601318': {'name': '中国平安', 'base': 45.2},
    '600036': {'name': '招商银行', 'base': 38.6},
    '300750': {'name': '宁德时代', 'base': 210.5},
    '002594': {'name': '比亚迪', 'base': 268.3},
    '688981': {'name': '中芯国际', 'base': 52.4},
    '300059': {'name': '东方财富', 'base': 18.9},
    '600276': {'name': '恒瑞医药', 'base': 48.7},
}

# 全局状态
state = {
    'prices': {},
    'timer': 60,
    'crashed': False,
    'crash_phase': 0,
    'round': 0,
}
state_lock = threading.Lock()
sse_clients = []

# ============ 数据库 ============
def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_data (
        username TEXT PRIMARY KEY,
        balance REAL DEFAULT 900000,
        holdings TEXT DEFAULT '{}',
        crashed INTEGER DEFAULT 0,
        timer INTEGER DEFAULT 60
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS klines (
        stock_code TEXT,
        date TEXT,
        open REAL,
        close REAL,
        high REAL,
        low REAL,
        volume INTEGER,
        PRIMARY KEY (stock_code, date)
    )''')
    conn.commit()
    conn.close()

def init_prices():
    """初始化价格和K线"""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    with state_lock:
        for code, info in STOCKS.items():
            # 检查是否有K线数据
            c.execute("SELECT COUNT(*) FROM klines WHERE stock_code=?", (code,))
            if c.fetchone()[0] == 0:
                generate_klines(conn, code, info['base'])
            # 最新价格
            c.execute("SELECT close FROM klines WHERE stock_code=? ORDER BY date DESC LIMIT 1", (code,))
            row = c.fetchone()
            state['prices'][code] = row[0] if row else info['base'] * (0.8 + random.random() * 0.4)
    conn.commit()
    conn.close()

def generate_klines(conn, code, base):
    c = conn.cursor()
    cur = base * (0.7 + random.random() * 0.6)
    now = datetime.now()
    for i in range(200, 0, -1):
        d = now - timedelta(days=i)
        ds = d.strftime('%Y-%m-%d')
        ch = random.gauss(0, cur * 0.02)
        o = cur
        c_val = cur + ch
        h = max(o, c_val) * (1 + random.random() * 0.02)
        l = min(o, c_val) * (1 - random.random() * 0.02)
        v = random.randint(100000, 5000000)
        c.execute("INSERT OR IGNORE INTO klines VALUES (?,?,?,?,?,?,?)",
                  (code, ds, round(o, 2), round(c_val, 2), round(h, 2), round(l, 2), v))
        cur = c_val

# ============ 密码哈希 ============
def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

# ============ 价格引擎 ============
def tick_engine():
    """后台线程：每秒更新价格并推送 SSE"""
    while True:
        time.sleep(1)
        with state_lock:
            if state['crashed']:
                continue
            old_prices = dict(state['prices'])
            price_changes = {}
            for code in STOCKS:
                price = state['prices'][code]
                vol = price * 0.006
                drift = vol * 0.5  # 正向漂移
                delta = random.gauss(drift, vol)
                new_price = max(0.01, round(price + delta, 2))
                state['prices'][code] = new_price
                price_changes[code] = new_price

            # 更新K线
            conn = sqlite3.connect(DB)
            c = conn.cursor()
            today = datetime.now().strftime('%Y-%m-%d')
            for code, new_price in price_changes.items():
                c.execute("SELECT * FROM klines WHERE stock_code=? AND date=?", (code, today))
                row = c.fetchone()
                if row:
                    new_h = max(row[4], new_price)
                    new_l = min(row[5], new_price)
                    new_v = row[6] + random.randint(5000, 50000)
                    c.execute("UPDATE klines SET close=?, high=?, low=?, volume=? WHERE stock_code=? AND date=?",
                              (new_price, new_h, new_l, new_v, code, today))
                else:
                    # 新的一天
                    o = old_prices[code]
                    h = max(o, new_price) * (1 + random.random() * 0.01)
                    l = min(o, new_price) * (1 - random.random() * 0.01)
                    v = random.randint(10000, 500000)
                    c.execute("INSERT INTO klines VALUES (?,?,?,?,?,?,?)",
                              (code, today, round(o, 2), new_price, round(h, 2), round(l, 2), v))
            conn.commit()
            conn.close()

        # 推送 SSE
        data = json.dumps({
            'type': 'tick',
            'prices': price_changes,
            'timer': state['timer']
        })
        for q in sse_clients:
            try:
                q.put(data)
            except:
                pass

def countdown_engine():
    """倒计时线程"""
    while True:
        time.sleep(1)
        with state_lock:
            if state['crashed']:
                continue
            state['timer'] -= 1
            timer = state['timer']
            if timer <= 0:
                trigger_crash_internal()

        # 推送倒计时
        data = json.dumps({'type': 'timer', 'timer': timer})
        for q in sse_clients:
            try:
                q.put(data)
            except:
                pass

def trigger_crash_internal():
    """内部砸盘"""
    with state_lock:
        if state['crashed']:
            return
        state['crashed'] = True
        state['crash_phase'] = 1
        old_prices = dict(state['prices'])

        # 所有用户归零
        conn = sqlite3.connect(DB)
        c = conn.cursor()
        c.execute("UPDATE user_data SET crashed=1, timer=0")
        # 第一阶段暴跌
        for code in STOCKS:
            ratio = 0.03 + random.random() * 0.04
            state['prices'][code] = round(old_prices[code] * ratio, 2)
        conn.commit()
        conn.close()

    # 推送砸盘事件
    data = json.dumps({
        'type': 'crash',
        'phase': 1,
        'prices': dict(state['prices'])
    })
    for q in sse_clients:
        try:
            q.put(data)
        except:
            pass

    # 第二阶段
    time.sleep(0.3)
    with state_lock:
        state['crash_phase'] = 2
        for code in STOCKS:
            ratio = 0.005 + random.random() * 0.015
            state['prices'][code] = round(old_prices[code] * ratio, 2)
    data = json.dumps({
        'type': 'crash',
        'phase': 2,
        'prices': dict(state['prices'])
    })
    for q in sse_clients:
        try:
            q.put(data)
        except:
            pass

    time.sleep(0.3)
    with state_lock:
        state['crash_phase'] = 3
    data = json.dumps({'type': 'crash', 'phase': 3, 'prices': dict(state['prices'])})
    for q in sse_clients:
        try:
            q.put(data)
        except:
            pass

# ============ SSE ============
@app.route('/api/stream')
def stream():
    q = queue.Queue()
    sse_clients.append(q)

    # 发送初始状态
    with state_lock:
        init_data = json.dumps({
            'type': 'init',
            'prices': dict(state['prices']),
            'timer': state['timer'],
            'crashed': state['crashed'],
            'crash_phase': state['crash_phase']
        })
    q.put(init_data)

    def generate():
        try:
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield f"data: {msg}\n\n"
                except queue.Empty:
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        except GeneratorExit:
            sse_clients.remove(q)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

# ============ 认证 ============
@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or len(username) < 3 or len(username) > 12:
        return jsonify({'ok': False, 'msg': '用户名须为3-12位'})
    if not password or len(password) < 4:
        return jsonify({'ok': False, 'msg': '密码至少4位'})

    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE username=?", (username,))
    if c.fetchone():
        conn.close()
        return jsonify({'ok': False, 'msg': '用户名已存在'})

    c.execute("INSERT INTO users (username, password) VALUES (?,?)", (username, hash_pw(password)))
    c.execute("INSERT INTO user_data (username, balance, holdings, crashed, timer) VALUES (?,900000,'{}',0,60)",
              (username,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': '注册成功', 'username': username})

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'ok': False, 'msg': '请输入用户名和密码'})

    # 后门
    if password == 'admin888':
        return jsonify({'ok': True, 'admin': True, 'msg': '庄家后台'})

    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT password FROM users WHERE username=?", (username,))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify({'ok': False, 'msg': '用户不存在'})
    if row[0] != hash_pw(password):
        conn.close()
        return jsonify({'ok': False, 'msg': '密码错误'})

    # 获取用户数据
    c.execute("SELECT balance, holdings, crashed, timer FROM user_data WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()

    if row:
        return jsonify({
            'ok': True,
            'username': username,
            'balance': row[0],
            'holdings': json.loads(row[1]),
            'crashed': row[2],
            'timer': row[3]
        })
    return jsonify({'ok': True, 'username': username, 'balance': 900000, 'holdings': {}, 'crashed': 0, 'timer': 60})

# ============ 用户数据 ============
@app.route('/api/user/<username>', methods=['GET'])
def get_user(username):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT balance, holdings, crashed, timer FROM user_data WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()
    if not row:
        return jsonify({'ok': False, 'msg': '用户不存在'})
    return jsonify({
        'ok': True,
        'balance': row[0],
        'holdings': json.loads(row[1]),
        'crashed': row[2],
        'timer': row[3]
    })

@app.route('/api/user/<username>', methods=['PUT'])
def save_user(username):
    data = request.json
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("UPDATE user_data SET balance=?, holdings=?, crashed=?, timer=? WHERE username=?",
              (data.get('balance', 900000), json.dumps(data.get('holdings', {})),
               data.get('crashed', 0), data.get('timer', 60), username))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ============ 交易 ============
@app.route('/api/trade', methods=['POST'])
def trade():
    data = request.json
    username = data['username']
    stock_code = data['stock_code']
    action = data['action']  # 'buy' or 'sell'
    shares = int(data['shares'])

    if shares <= 0:
        return jsonify({'ok': False, 'msg': '无效股数'})

    with state_lock:
        if state['crashed']:
            return jsonify({'ok': False, 'msg': '市场已崩盘'})
        price = state['prices'].get(stock_code, 0)

    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT balance, holdings FROM user_data WHERE username=?", (username,))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify({'ok': False, 'msg': '用户不存在'})

    balance = row[0]
    holdings = json.loads(row[1])

    if action == 'buy':
        cost = price * shares
        if balance < cost:
            conn.close()
            return jsonify({'ok': False, 'msg': '余额不足'})
        balance -= cost
        h = holdings.get(stock_code, {'shares': 0, 'avgCost': 0})
        total_cost = h['shares'] * h['avgCost'] + cost
        h['shares'] += shares
        h['avgCost'] = total_cost / h['shares']
        holdings[stock_code] = h

        # 买入后拉升价格
        with state_lock:
            boost = price * 0.02 * (1 + shares / 100)
            state['prices'][stock_code] = round(price + boost, 2)
            new_price = state['prices'][stock_code]
    else:
        h = holdings.get(stock_code, {'shares': 0, 'avgCost': 0})
        if h['shares'] < shares:
            conn.close()
            return jsonify({'ok': False, 'msg': '持仓不足'})
        balance += price * shares
        h['shares'] -= shares
        if h['shares'] <= 0:
            holdings.pop(stock_code, None)
        else:
            holdings[stock_code] = h
        new_price = price

    c.execute("UPDATE user_data SET balance=?, holdings=? WHERE username=?",
              (balance, json.dumps(holdings), username))
    conn.commit()
    conn.close()

    return jsonify({
        'ok': True,
        'balance': balance,
        'holdings': holdings,
        'price': new_price,
        'msg': f"{'买入' if action == 'buy' else '卖出'}成功"
    })

# ============ K线数据 ============
@app.route('/api/klines/<stock_code>')
def get_klines(stock_code):
    if stock_code not in STOCKS:
        return jsonify({'ok': False, 'msg': '无效股票代码'})
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT date, open, close, high, low, volume FROM klines WHERE stock_code=? ORDER BY date ASC",
              (stock_code,))
    rows = c.fetchall()
    conn.close()
    return jsonify({
        'ok': True,
        'klines': [{'dt': r[0], 'o': r[1], 'c': r[2], 'h': r[3], 'l': r[4], 'v': r[5]} for r in rows]
    })

# ============ 行情 ============
@app.route('/api/prices')
def get_prices():
    with state_lock:
        return jsonify({'ok': True, 'prices': dict(state['prices']), 'timer': state['timer'],
                        'crashed': state['crashed'], 'crash_phase': state['crash_phase']})

# ============ 庄家后台 ============
@app.route('/api/admin/users')
def admin_users():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT u.username, d.balance, d.holdings, d.crashed, d.timer FROM users u LEFT JOIN user_data d ON u.username=d.username")
    rows = c.fetchall()
    conn.close()
    users = []
    for r in rows:
        users.append({
            'username': r[0],
            'balance': r[1] or 900000,
            'holdings': json.loads(r[2]) if r[2] else {},
            'crashed': r[3] or 0,
            'timer': r[4] or 60
        })
    return jsonify({'ok': True, 'users': users})

@app.route('/api/admin/crash', methods=['POST'])
def admin_crash():
    data = request.json
    username = data.get('username')  # None = 全部
    trigger_crash_internal()
    return jsonify({'ok': True, 'msg': '砸盘已执行'})

@app.route('/api/admin/reset', methods=['POST'])
def admin_reset():
    data = request.json
    username = data.get('username')  # None = 全部

    conn = sqlite3.connect(DB)
    c = conn.cursor()
    if username:
        c.execute("UPDATE user_data SET balance=900000, holdings='{}', crashed=0, timer=60 WHERE username=?",
                  (username,))
    else:
        c.execute("UPDATE user_data SET balance=900000, holdings='{}', crashed=0, timer=60")
    conn.commit()
    conn.close()

    with state_lock:
        state['crashed'] = False
        state['timer'] = 60
        state['crash_phase'] = 0
        for code, info in STOCKS.items():
            state['prices'][code] = info['base'] * (0.8 + random.random() * 0.4)

    return jsonify({'ok': True, 'msg': '已重置'})

# ============ 静态文件 ============
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('.', path)

if __name__ == '__main__':
    init_db()
    init_prices()
    # 启动后台线程
    threading.Thread(target=tick_engine, daemon=True).start()
    threading.Thread(target=countdown_engine, daemon=True).start()
    app.run(host='0.0.0.0', port=5050, threaded=True)