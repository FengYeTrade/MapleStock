#!/usr/bin/env python3
"""Stock Trading Simulator - 枫叶炒股模拟器 Backend"""
import json
import random
import time
import threading
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, session

app = Flask(__name__)
app.secret_key = 'maple-leaf-stock-trader-2024-secret'

# ============ 模拟股票数据 ============
STOCKS = {
    "000001": {"name": "平安银行", "price": 12.50, "change": 0.0, "change_pct": 0.0},
    "600519": {"name": "贵州茅台", "price": 1680.00, "change": 0.0, "change_pct": 0.0},
    "000858": {"name": "五粮液", "price": 156.80, "change": 0.0, "change_pct": 0.0},
    "601318": {"name": "中国平安", "price": 45.20, "change": 0.0, "change_pct": 0.0},
    "600036": {"name": "招商银行", "price": 38.60, "change": 0.0, "change_pct": 0.0},
    "300750": {"name": "宁德时代", "price": 210.50, "change": 0.0, "change_pct": 0.0},
    "002594": {"name": "比亚迪", "price": 268.30, "change": 0.0, "change_pct": 0.0},
    "688981": {"name": "中芯国际", "price": 52.40, "change": 0.0, "change_pct": 0.0},
    "300059": {"name": "东方财富", "price": 18.90, "change": 0.0, "change_pct": 0.0},
    "600276": {"name": "恒瑞医药", "price": 48.70, "change": 0.0, "change_pct": 0.0},
}

# 历史K线数据缓存 (每只股票存200根K线)
kline_cache = {}
for code in STOCKS:
    kline_cache[code] = []

# 用户持仓 {user_id: {code: {shares: int, avg_cost: float}}}
holdings = {}
# 用户余额
balances = {}


def generate_initial_klines(code, count=200):
    """生成初始K线历史数据"""
    base_price = STOCKS[code]["price"]
    klines = []
    current = base_price * random.uniform(0.7, 1.3)
    now = datetime.now()

    for i in range(count, 0, -1):
        day = now - timedelta(days=i)
        change = random.gauss(0, current * 0.02)
        open_p = current
        close_p = current + change
        high_p = max(open_p, close_p) * (1 + random.random() * 0.02)
        low_p = min(open_p, close_p) * (1 - random.random() * 0.02)
        volume = int(random.uniform(100000, 5000000))

        klines.append({
            "date": day.strftime("%Y-%m-%d"),
            "open": round(open_p, 2),
            "close": round(close_p, 2),
            "high": round(high_p, 2),
            "low": round(low_p, 2),
            "volume": volume,
        })
        current = close_p

    return klines


def update_prices():
    """更新股票价格（模拟实时波动）"""
    for code, stock in STOCKS.items():
        if not kline_cache[code]:
            kline_cache[code] = generate_initial_klines(code)

        last_k = kline_cache[code][-1]
        last_close = last_k["close"]
        change = random.gauss(0, last_close * 0.008)
        new_price = max(last_close * 0.9, min(last_close * 1.1, last_close + change))
        stock["price"] = round(new_price, 2)
        stock["change"] = round(new_price - last_close, 2)
        stock["change_pct"] = round((new_price - last_close) / last_close * 100, 2)

        # 每5分钟添加一根新K线
        now = datetime.now()
        if last_k["date"] != now.strftime("%Y-%m-%d"):
            new_k = {
                "date": now.strftime("%Y-%m-%d"),
                "open": last_close,
                "close": round(new_price, 2),
                "high": round(max(new_price, last_close) * random.uniform(1.001, 1.01), 2),
                "low": round(min(new_price, last_close) * random.uniform(0.99, 0.999), 2),
                "volume": int(random.uniform(500000, 3000000)),
            }
            kline_cache[code].append(new_k)
            if len(kline_cache[code]) > 200:
                kline_cache[code].pop(0)


# 价格更新线程
def price_updater():
    while True:
        update_prices()
        time.sleep(30)


threading.Thread(target=price_updater, daemon=True).start()

# 初始化K线数据
for code in STOCKS:
    kline_cache[code] = generate_initial_klines(code)


def get_user_id():
    """获取或创建用户ID"""
    user_id = session.get("user_id")
    if not user_id:
        user_id = f"user_{random.randint(10000, 99999)}"
        session["user_id"] = user_id
    return user_id


def init_user(user_id):
    """初始化用户数据"""
    if user_id not in balances:
        balances[user_id] = 900.0  # 开局送900块
    if user_id not in holdings:
        holdings[user_id] = {}


# ============ API 路由 ============

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/init", methods=["POST"])
def api_init():
    """初始化用户"""
    user_id = get_user_id()
    init_user(user_id)
    return jsonify({
        "user_id": user_id,
        "balance": balances[user_id],
        "holdings": holdings[user_id],
    })


@app.route("/api/stocks", methods=["GET"])
def api_stocks():
    """获取所有股票实时行情"""
    result = []
    for code, stock in STOCKS.items():
        result.append({
            "code": code,
            "name": stock["name"],
            "price": stock["price"],
            "change": stock["change"],
            "change_pct": stock["change_pct"],
        })
    return jsonify(result)


@app.route("/api/kline/<code>", methods=["GET"])
def api_kline(code):
    """获取K线数据"""
    if code not in kline_cache:
        return jsonify({"error": "股票代码不存在"}), 404
    days = request.args.get("days", 60, type=int)
    data = kline_cache[code][-days:] if len(kline_cache[code]) > days else kline_cache[code]
    return jsonify(data)


@app.route("/api/portfolio", methods=["GET"])
def api_portfolio():
    """获取用户持仓"""
    user_id = get_user_id()
    init_user(user_id)

    portfolio = []
    total_value = balances[user_id]
    total_cost = 0.0

    for code, h in holdings[user_id].items():
        if h["shares"] > 0:
            stock = STOCKS[code]
            market_value = h["shares"] * stock["price"]
            cost = h["shares"] * h["avg_cost"]
            profit = market_value - cost
            profit_pct = (profit / cost * 100) if cost > 0 else 0

            portfolio.append({
                "code": code,
                "name": stock["name"],
                "shares": h["shares"],
                "avg_cost": round(h["avg_cost"], 2),
                "price": stock["price"],
                "market_value": round(market_value, 2),
                "profit": round(profit, 2),
                "profit_pct": round(profit_pct, 2),
            })
            total_value += market_value
            total_cost += cost

    total_profit = total_value - 900 - total_cost
    if total_cost > 0:
        total_profit_pct = round((total_value - 900 - total_cost) / (900 + total_cost) * 100, 2)
    else:
        total_profit_pct = round((total_value - 900) / 900 * 100, 2)

    return jsonify({
        "balance": round(balances[user_id], 2),
        "holdings": portfolio,
        "total_value": round(total_value, 2),
        "total_profit": round(total_profit, 2),
        "total_profit_pct": total_profit_pct,
    })


@app.route("/api/trade", methods=["POST"])
def api_trade():
    """交易：买入/卖出"""
    user_id = get_user_id()
    init_user(user_id)

    data = request.json
    code = data.get("code")
    action = data.get("action")  # "buy" or "sell"
    shares = data.get("shares", 0)

    if code not in STOCKS:
        return jsonify({"error": "股票代码不存在"}), 400
    if shares <= 0:
        return jsonify({"error": "股数必须大于0"}), 400
    if action not in ("buy", "sell"):
        return jsonify({"error": "操作类型无效"}), 400

    stock = STOCKS[code]
    price = stock["price"]

    if action == "buy":
        cost = price * shares
        if balances[user_id] < cost:
            return jsonify({"error": f"余额不足！需要 ¥{cost:.2f}，当前余额 ¥{balances[user_id]:.2f}"}), 400

        balances[user_id] -= cost
        if code not in holdings[user_id]:
            holdings[user_id][code] = {"shares": 0, "avg_cost": 0}

        h = holdings[user_id][code]
        total_cost = h["shares"] * h["avg_cost"] + cost
        h["shares"] += shares
        h["avg_cost"] = total_cost / h["shares"] if h["shares"] > 0 else 0

        return jsonify({
            "success": True,
            "action": "buy",
            "code": code,
            "name": stock["name"],
            "shares": shares,
            "price": price,
            "cost": round(cost, 2),
            "balance": round(balances[user_id], 2),
            "holdings": holdings[user_id],
        })

    else:  # sell
        if code not in holdings[user_id] or holdings[user_id][code]["shares"] < shares:
            return jsonify({"error": f"持仓不足！你只有 {holdings[user_id].get(code, {}).get('shares', 0)} 股"}), 400

        revenue = price * shares
        balances[user_id] += revenue
        holdings[user_id][code]["shares"] -= shares

        if holdings[user_id][code]["shares"] == 0:
            del holdings[user_id][code]

        return jsonify({
            "success": True,
            "action": "sell",
            "code": code,
            "name": stock["name"],
            "shares": shares,
            "price": price,
            "revenue": round(revenue, 2),
            "balance": round(balances[user_id], 2),
            "holdings": holdings[user_id],
        })


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """重置账户"""
    user_id = get_user_id()
    balances[user_id] = 900.0
    holdings[user_id] = {}
    return jsonify({"success": True, "balance": 900.0})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)