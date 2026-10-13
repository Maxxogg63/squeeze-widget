"""
Binance USDT-M Futures Squeeze Widget
Версия 2.0.1 — фикс Механизма 2, скролл главного списка

Changelog:
    1.0–1.3 — эволюция, фиксы
    1.4.x — попытка с find_active_range (провалилась)
    2.0.0 — ПОЛНАЯ ПЕРЕРАБОТКА:
        Механизм 1: активные диапазоны (поиск начала + отслеживание)
        Механизм 2: история БИТ (паттерн-матчинг 24ч)
        Оба механизма независимы
        Убраны find_active_range, t_high, is_trend
    2.0.1 — фикс Механизма 2: range_high фиксируется на момент начала
            скролл главного списка

Запуск:  python squeeze_widget.py
Тест:    python squeeze_widget.py --test
"""
import csv
import json
import os
import queue
import sys
import threading
import time
import urllib.request
import winsound
from collections import deque
from urllib.parse import quote

import websocket
import customtkinter as ctk
from tkinter import Menu, filedialog


# ============ ПУТИ ============
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
CONFIG_PATH = os.path.join(BASE_DIR, "squeeze_config.json")
FILTERS_PATH = os.path.join(BASE_DIR, "squeeze_filters.txt")
CACHE_PATH = os.path.join(BASE_DIR, "squeeze_cache.json")
LOG_PATH = os.path.join(BASE_DIR, "squeeze_log.csv")
BIT_HISTORY_PATH = os.path.join(BASE_DIR, "squeeze_bit_history.csv")
EXPIRED_LOG_PATH = os.path.join(BASE_DIR, "squeeze_expired.csv")
WORKER_LOG_PATH = os.path.join(BASE_DIR, "squeeze_worker.log")
LAST_SEEN_PATH = os.path.join(BASE_DIR, "last_seen_squeeze.txt")
SOUND_PATH = os.path.join(ASSETS_DIR, "squeeze_alert.wav")

DEBUG_SYMBOLS = ("GTCUSDT", "OGNUSDT", "SUSDT", "BATUSDT")

WIDGET_VERSION = "2.0.1"


# ============ ЦВЕТА ============
BG           = "#116466"
BG_DARK      = "#0E5153"
BG_ROW_ALT   = "#0F5A5C"
BG_SECTION   = "#0A3E40"
ACCENT       = "#D9B08C"
ACCENT_HOVER = "#FFCB9A"
SAND         = "#D9B08C"
PEACH        = "#FFCB9A"
MINT         = "#D1E8E2"
GREEN        = "#4ADE80"
RED          = "#EF4444"
GREY         = "#6B7772"
GOLD         = "#FFD166"


# ============ НАСТРОЙКИ ============
DEFAULTS = {
    # Фильтр свежести
    "growth_24h_min_pct": 20.0,
    "volume_24h_min_usdt": 10_000_000,

    # Фильтр ширины диапазона
    "range_width_k1": 0.8,
    "range_width_abs_max_pct": 20.0,
    "min_range_width_pct": 0.1,

    # Диапазон
    "range_forming_min_candles": 6,
    "range_active_min_candles": 6,

    # Касания/выносы
    "touch_gap_pct": 0.2,
    "breakout_exit_candles": 6,

    # Затянувшийся
    "long_range_hours": 15,

    # Задний двор
    "backstage_ttl_hours": 12,

    # История БИТ
    "bit_history_ttl_hours": 72,
    "bit_history_max_rows": 200,
    "bit_pattern_growth_min_pct": 20.0,
    "bit_pattern_pullback_min_pct": 5.0,
    "bit_pattern_range_min_candles": 5,
    "bit_pattern_exit_candles": 6,
    "bit_pattern_exit_pct": 5.0,

    # UI
    "forming_max_show": 5,
    "alert_show_sec": 30,
    "max_rows_visible": 10,
    "history_size": 50,
    "idle_text": "Список пуст. Ждём монеты...",

    # Система
    "chunk_size": 80,
    "cache_interval_sec": 300,
    "warmup_hours": 24,
    "warmup_parallel": 15,

    # Звук
    "sound": True,
    "sound_file": "assets/squeeze_alert.wav",
}

# параметры, которые можно менять в squeeze_filters.txt
FILTER_KEYS = [
    "growth_24h_min_pct",
    "volume_24h_min_usdt",
    "range_width_k1",
    "range_width_abs_max_pct",
    "min_range_width_pct",
    "range_forming_min_candles",
    "range_active_min_candles",
    "touch_gap_pct",
    "breakout_exit_candles",
    "long_range_hours",
]

REST_EXCHANGE_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"
REST_KLINES = "https://fapi.binance.com/fapi/v1/klines"
WS_BASE = "wss://fstream.binance.com/market/stream?streams="


# ============ СОСТОЯНИЕ ============
alerts_q: "queue.Queue[dict]" = queue.Queue()

status = {
    "text": "инициализация...",
    "ticks": 0,
    "last": 0.0,
    "symbols": 0,
    "warmup_done": 0,
    "warmup_total": 0,
    "warmup_finished": False,
    "stats_buf": 0,
    "stats_fresh": 0,
    "stats_range": 0,
    "fail_growth": 0,
    "fail_volume": 0,
    "fail_width_k1": 0,
    "fail_width_abs": 0,
    "fail_width_min": 0,
}

buf_5m: dict = {}
buf_1h: dict = {}
squeeze_state: dict = {}       # активные диапазоны
backstage: dict = {}           # задний двор
bit_history: list = []         # история БИТ
bit_history_lock = threading.Lock()

cfg = dict(DEFAULTS)


# ============ ЛОГИ ============
def worker_log(msg):
    try:
        with open(WORKER_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {msg}\n")
    except Exception:
        pass


def _ts(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


def fmt_duration(sec):
    sec = int(sec)
    if sec < 3600:
        return f"{sec // 60}м"
    h = sec // 3600
    m = (sec % 3600) // 60
    return f"{h}ч{m:02d}м" if m else f"{h}ч"


def _make_filters_txt():
    if os.path.exists(FILTERS_PATH):
        return
    content = """# ============================================================
# SQUEEZE WIDGET v2.0.0 — НАСТРОЙКИ ФИЛЬТРОВ
# Меняй значения, сохраняй, перезапускай виджет.
# Строки с # — комментарии.
# ============================================================

# ------------------------------------------------------------
# ФИЛЬТР СВЕЖЕСТИ
# ------------------------------------------------------------

# Минимальный рост монеты за 24ч (в %).
# Дефолт: 20
growth_24h_min_pct = 20

# Минимальный оборот за 24ч (USDT).
# Дефолт: 10000000
volume_24h_min_usdt = 10000000

# ------------------------------------------------------------
# ФИЛЬТР ШИРИНЫ ДИАПАЗОНА
# ------------------------------------------------------------

# Ширина диапазона <= рост * этот коэффициент.
# 0.8 = диапазон не больше 80% роста.
# Дефолт: 0.8
range_width_k1 = 0.8

# Абсолютный потолок ширины (%).
# Дефолт: 20
range_width_abs_max_pct = 20

# Минимальная ширина (%).
# Дефолт: 0.1
min_range_width_pct = 0.1

# ------------------------------------------------------------
# ДИАПАЗОН
# ------------------------------------------------------------

# Минимум свечей для диапазона.
# Дефолт: 6
range_forming_min_candles = 6

# Свечей для "активного" диапазона.
# Дефолт: 6
range_active_min_candles = 6

# Зазор для касания границы (%).
# Дефолт: 0.2
touch_gap_pct = 0.2

# Свечей подряд close вне диапазона для ухода.
# Дефолт: 6
breakout_exit_candles = 6

# Порог "затянувшегося" диапазона (часы).
# Дефолт: 15
long_range_hours = 15
"""
    try:
        with open(FILTERS_PATH, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        print("filters.txt create error:", e)


def load_config():
    global cfg
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
            cfg.update({k: v for k, v in user.items() if k in DEFAULTS})
        except Exception as e:
            print("config load error:", e)

    _make_filters_txt()

    if os.path.exists(FILTERS_PATH):
        try:
            with open(FILTERS_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    if key not in FILTER_KEYS:
                        continue
                    try:
                        if "." in val:
                            cfg[key] = float(val)
                        else:
                            cfg[key] = int(val)
                    except ValueError:
                        print(f"filters.txt: bad value {key}={val}")
        except Exception as e:
            print("filters.txt load error:", e)

    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def save_last_seen(ts=None):
    try:
        t = ts if ts is not None else time.time()
        with open(LAST_SEEN_PATH, "w", encoding="utf-8") as f:
            f.write(str(int(t)))
    except Exception:
        pass


def log_squeeze(sym, state):
    try:
        new = not os.path.exists(LOG_PATH)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            if new:
                f.write("created;symbol;range_high;range_low;width_pct;"
                        "growth_24h;volume_24h\n")
            now = time.time()
            width = (state["range_high"] / state["range_low"] - 1) * 100 \
                if state["range_low"] > 0 else 0
            f.write(";".join(str(x) for x in (
                _ts(now), sym,
                f"{state['range_high']:.8f}",
                f"{state['range_low']:.8f}",
                f"{width:.4f}",
                f"{state.get('growth_24h', 0):.2f}",
                f"{state.get('volume_24h', 0):.0f}")) + "\n")
    except Exception as e:
        print("log_squeeze error:", e)


def log_expired(sym, state, reason, final_price):
    try:
        new = not os.path.exists(EXPIRED_LOG_PATH)
        with open(EXPIRED_LOG_PATH, "a", encoding="utf-8") as f:
            if new:
                f.write("ended;symbol;reason;range_high;range_low;"
                        "duration_sec;final_price\n")
            now = time.time()
            f.write(";".join(str(x) for x in (
                _ts(now), sym, reason,
                f"{state['range_high']:.8f}",
                f"{state['range_low']:.8f}",
                int(now - state.get("started", now)),
                f"{final_price:.8f}")) + "\n")
    except Exception as e:
        print("log_expired error:", e)


# ============ ИСТОРИЯ БИТ ============
def _bit_history_header():
    return [
        "symbol", "detected_at", "fired_at",
        "duration_in_range_sec",
        "range_low", "range_high", "width_pct",
        "growth_24h", "volume_24h",
        "exit_price",
        "max_after_1h", "max_after_6h", "max_after_24h",
        "pct_after_1h", "pct_after_6h", "pct_after_24h",
        "status",
    ]


def save_bit_history():
    try:
        with open(BIT_HISTORY_PATH, "w", encoding="utf-8",
                  newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(_bit_history_header())
            with bit_history_lock:
                for row in bit_history:
                    w.writerow([row.get(k, "") for k in _bit_history_header()])
    except Exception as e:
        worker_log(f"save_bit_history error: {e}")


def load_bit_history():
    global bit_history
    if not os.path.exists(BIT_HISTORY_PATH):
        return
    try:
        with open(BIT_HISTORY_PATH, "r", encoding="utf-8") as f:
            r = csv.DictReader(f, delimiter=";")
            rows = []
            for row in r:
                for k in ("duration_in_range_sec",):
                    if row.get(k):
                        try:
                            row[k] = int(float(row[k]))
                        except Exception:
                            row[k] = 0
                for k in ("range_low", "range_high", "width_pct",
                          "growth_24h", "volume_24h",
                          "exit_price", "max_after_1h", "max_after_6h",
                          "max_after_24h", "pct_after_1h",
                          "pct_after_6h", "pct_after_24h"):
                    if row.get(k):
                        try:
                            row[k] = float(row[k])
                        except Exception:
                            row[k] = 0.0
                rows.append(row)
            with bit_history_lock:
                bit_history = rows[-cfg["bit_history_max_rows"]:]
        worker_log(f"bit_history loaded: {len(bit_history)} rows")
    except Exception as e:
        worker_log(f"load_bit_history error: {e}")


def add_bit_history(sym, state, exit_price, fired_ts=None):
    """
    Добавляет запись в историю БИТ.
    Дедупликация по (symbol, fired_ts до минуты).
    """
    if fired_ts is None:
        fired_ts = time.time()

    exit_key = int(fired_ts // 60)
    with bit_history_lock:
        for existing in bit_history:
            ex_ts = existing.get("fired_ts", 0)
            try:
                ex_ts = float(ex_ts)
            except Exception:
                ex_ts = 0
            if existing.get("symbol") == sym and \
               int(ex_ts // 60) == exit_key:
                worker_log(f"[v2.0.0] dedup {sym} {exit_key}")
                return None

        width = (state["range_high"] / state["range_low"] - 1) * 100 \
            if state["range_low"] > 0 else 0
        row = {
            "symbol": sym,
            "detected_at": _ts(state.get("started", fired_ts)),
            "fired_at": _ts(fired_ts),
            "fired_ts": fired_ts,
            "duration_in_range_sec": int(fired_ts - state.get("started", fired_ts)),
            "range_low": state.get("range_low", 0),
            "range_high": state.get("range_high", 0),
            "width_pct": width,
            "growth_24h": state.get("growth_24h", 0),
            "volume_24h": state.get("volume_24h", 0),
            "exit_price": exit_price,
            "max_after_1h": 0.0,
            "max_after_6h": 0.0,
            "max_after_24h": 0.0,
            "pct_after_1h": 0.0,
            "pct_after_6h": 0.0,
            "pct_after_24h": 0.0,
            "status": "fired",
        }
        bit_history.append(row)
        if len(bit_history) > cfg["bit_history_max_rows"]:
            del bit_history[:len(bit_history) - cfg["bit_history_max_rows"]]
    save_bit_history()
    return row


def update_bit_history_stats():
    """Раз в минуту обновляет max_after_* для старых записей."""
    now = time.time()
    updated = False
    with bit_history_lock:
        for row in bit_history:
            sym = row.get("symbol")
            if not sym or sym not in buf_5m:
                continue
            try:
                fired_ts = float(row.get("fired_ts", 0))
            except Exception:
                continue
            if fired_ts <= 0:
                continue
            exit_price = row.get("exit_price", 0)
            if not exit_price or exit_price <= 0:
                continue
            buf = buf_5m.get(sym)
            if not buf:
                continue

            # 1ч
            if now - fired_ts >= 3600 and not row.get("pct_after_1h"):
                hs = [c["high"] for c in buf
                      if fired_ts <= c["t"] / 1000.0 <= fired_ts + 3600]
                if hs:
                    mx = max(hs)
                    row["max_after_1h"] = mx
                    row["pct_after_1h"] = (mx / exit_price - 1) * 100
                    updated = True

            # 6ч
            if now - fired_ts >= 6 * 3600 and not row.get("pct_after_6h"):
                hs = [c["high"] for c in buf
                      if fired_ts <= c["t"] / 1000.0 <= fired_ts + 6 * 3600]
                if hs:
                    mx = max(hs)
                    row["max_after_6h"] = mx
                    row["pct_after_6h"] = (mx / exit_price - 1) * 100
                    updated = True

            # 24ч
            if now - fired_ts >= 24 * 3600 and not row.get("pct_after_24h"):
                hs = [c["high"] for c in buf
                      if fired_ts <= c["t"] / 1000.0 <= fired_ts + 24 * 3600]
                if hs:
                    mx = max(hs)
                    row["max_after_24h"] = mx
                    row["pct_after_24h"] = (mx / exit_price - 1) * 100
                    updated = True

    if updated:
        save_bit_history()


# ============ ДЕТЕКТОР ============
def calc_growth_1h(candles_1h):
    """Рост за 24ч по 1H."""
    if not candles_1h or len(candles_1h) < 4:
        return None
    highs = [c["high"] for c in candles_1h]
    lows = [c["low"] for c in candles_1h]
    max_high = max(highs)
    min_low = min(lows)
    if min_low <= 0:
        return None
    growth = (max_high / min_low - 1) * 100
    # последний индекс хая
    idx_high = len(highs) - 1 - highs[::-1].index(max_high)
    return {
        "growth_24h": growth,
        "high_24h": max_high,
        "t_high_ts": candles_1h[idx_high]["t"] / 1000.0,
    }


def calc_volume_24h_usdt(candles_5m):
    total = 0.0
    for c in candles_5m:
        qv = c.get("quote_volume", 0.0)
        if qv > 0:
            total += qv
        else:
            total += c.get("volume", 0.0) * c.get("close", 0.0)
    return total


def is_fresh(growth, volume):
    if growth < cfg["growth_24h_min_pct"]:
        status["fail_growth"] += 1
        return False, "growth"
    if volume and volume < cfg["volume_24h_min_usdt"]:
        status["fail_volume"] += 1
        return False, "volume"
    return True, "ok"


def check_range_width(growth, width):
    if width > growth * cfg["range_width_k1"]:
        status["fail_width_k1"] += 1
        return False, "width_k1"
    if width > cfg["range_width_abs_max_pct"]:
        status["fail_width_abs"] += 1
        return False, "width_abs"
    if width < cfg["min_range_width_pct"]:
        status["fail_width_min"] += 1
        return False, "width_min"
    return True, "ok"


def find_range_start(candles_5m, t_high_ts):
    """
    Находит начало диапазона — последний минимум после хая,
    который не был пробит ниже.
    Возвращает индекс начала или None.
    """
    # отбираем свечи после хая
    after_high = []
    for i, c in enumerate(candles_5m):
        if c["t"] / 1000.0 >= t_high_ts:
            after_high.append((i, c))

    if len(after_high) < cfg["range_forming_min_candles"]:
        return None

    # ищем глобальный минимум после хая — включая заколы
    min_low = min(c["low"] for _, c in after_high)
    # последний индекс с этим минимумом
    min_idx = None
    for i, c in after_high:
        if c["low"] == min_low:
            min_idx = i  # последний — перезаписываем

    if min_idx is None:
        return None

    # проверяем, что после min_idx нет low < min_low
    for j in range(min_idx + 1, len(candles_5m)):
        if candles_5m[j]["low"] < min_low:
            # минимум пробит — сдвигаем
            min_idx = j
            min_low = candles_5m[j]["low"]

    return min_idx


def build_active_state(sym, candles_5m, candles_1h):
    """Строит активный диапазон. Возвращает state или None."""
    status["stats_buf"] += 1

    g1h = calc_growth_1h(candles_1h)
    if not g1h:
        return None

    volume = calc_volume_24h_usdt(candles_5m)
    ok, fail_reason = is_fresh(g1h["growth_24h"], volume)
    if not ok:
        if sym in DEBUG_SYMBOLS:
            worker_log(
                f"DEBUG {sym}: REJECT is_fresh/{fail_reason} "
                f"growth={g1h['growth_24h']:.2f}% volume={volume:.0f}"
            )
        return None
    status["stats_fresh"] += 1

    # ищем начало диапазона
    start_idx = find_range_start(candles_5m, g1h["t_high_ts"])

    if start_idx is None:
        # fallback — последние N свечей
        n = cfg["range_forming_min_candles"]
        if len(candles_5m) < n:
            return None
        start_idx = len(candles_5m) - n

    subset = list(candles_5m)[start_idx:]
    if len(subset) < cfg["range_forming_min_candles"]:
        return None

    # границы — по фитилям
    rh = max(c["high"] for c in subset)
    rl = min(c["low"] for c in subset)
    if rl <= 0:
        return None
    width = (rh / rl - 1) * 100

    ok, fail_reason = check_range_width(g1h["growth_24h"], width)
    if not ok:
        if sym in DEBUG_SYMBOLS:
            worker_log(
                f"DEBUG {sym}: REJECT width/{fail_reason} "
                f"growth={g1h['growth_24h']:.2f}% "
                f"width={width:.2f}% candles={len(subset)}"
            )
        return None
    status["stats_range"] += 1

    started_ts = subset[0]["t"] / 1000.0
    candles_in_range = len(subset)

    if candles_in_range < cfg["range_active_min_candles"]:
        return None

    now = time.time()
    state = {
        "status": "active",
        "range_high": rh,
        "range_low": rl,
        "started": started_ts,
        "candles_in_range": candles_in_range,
        "last_touch": 0.0,
        "last_breakout": 0.0,
        "breakout_side": None,
        "touch_count": 0,
        "breakout_count": 0,
        "exit_candles": 0,        # счётчик close вне
        "growth_24h": g1h["growth_24h"],
        "volume_24h": volume,
        "high_24h": g1h["high_24h"],
        "last_update_ts": now,
    }

    log_squeeze(sym, state)

    if sym in DEBUG_SYMBOLS:
        worker_log(
            f"DEBUG {sym}: ACCEPT active "
            f"growth={g1h['growth_24h']:.2f}% "
            f"width={width:.2f}% "
            f"candles={candles_in_range} "
            f"range=[{rl:.6f}..{rh:.6f}]"
        )

    return state


def update_active_state(sym, state, k):
    """
    Обновляет состояние активного диапазона.
    Возвращает event-dict или None.
    """
    now = time.time()
    close = k["close"]
    high = k["high"]
    low = k["low"]

    sv = state["status"]
    if sv == "cancelled":
        return None

    # границы НЕ двигаем — фиксированы
    state["candles_in_range"] += 1
    state["last_update_ts"] = now

    # касание
    gap = cfg["touch_gap_pct"] / 100.0
    touch_top = high >= state["range_high"] * (1 - gap)
    touch_bot = low <= state["range_low"] * (1 + gap)
    if touch_top or touch_bot:
        state["last_touch"] = now
        state["touch_count"] += 1

    # вынос по close
    broke_up = close > state["range_high"]
    broke_down = close < state["range_low"]

    if broke_up or broke_down:
        side = "up" if broke_up else "down"
        state["last_breakout"] = now
        state["breakout_side"] = side
        state["breakout_count"] += 1
        state["status"] = "broken_up" if broke_up else "broken_down"
        state["exit_candles"] = 1
        return {"type": "breakout", "symbol": sym,
                "side": side, "state": state}

    # если в статусе broken — считаем свечи вне
    if sv in ("broken_up", "broken_down"):
        # проверка возврата
        if state["range_low"] <= close <= state["range_high"]:
            state["status"] = "active"
            state["exit_candles"] = 0
            return {"type": "return", "symbol": sym, "state": state}
        else:
            state["exit_candles"] += 1
            if state["exit_candles"] >= cfg["breakout_exit_candles"]:
                # уход
                state["status"] = "cancelled"
                state["left_at"] = now
                state["left_reason"] = sv
                if sv == "broken_up":
                    # БИТ сработал
                    add_bit_history(sym, state, close, fired_ts=now)
                    return {"type": "bit_signal", "symbol": sym,
                            "state": state}
                else:
                    backstage[sym] = {
                        "state": state,
                        "left_at": now,
                        "left_reason": sv,
                    }
                    return {"type": "cancelled", "symbol": sym,
                            "state": state}

    return None


def check_backstage_return(sym, k):
    if sym not in backstage:
        return None
    b = backstage[sym]
    state = b["state"]
    close = k["close"]
    rl = state.get("range_low", 0)
    rh = state.get("range_high", 0)
    if rl <= 0 or rh <= 0:
        return None
    if rl <= close <= rh:
        state["status"] = "active"
        state["exit_candles"] = 0
        state["last_update_ts"] = time.time()
        squeeze_state[sym] = state
        del backstage[sym]
        return {"type": "return_from_backstage", "symbol": sym,
                "state": state}
    return None


def handle_kline_5m(sym, k):
    try:
        is_closed = bool(k.get("x", False))
        if not is_closed:
            return
        high = float(k["h"])
        low = float(k["l"])
        close = float(k["c"])
        open_ = float(k["o"])
        volume = float(k.get("v", 0.0))
        quote_volume = float(k.get("q", 0.0))
        t = k["t"]
    except Exception:
        return

    buf = buf_5m.get(sym)
    if buf is None:
        buf = deque(maxlen=cfg["warmup_hours"] * 12)
        buf_5m[sym] = buf
    buf.append({"high": high, "low": low, "close": close,
                "open": open_, "volume": volume,
                "quote_volume": quote_volume, "t": t})

    state = squeeze_state.get(sym)

    if state is None:
        # сначала задний двор
        ev = check_backstage_return(sym, {"high": high, "low": low,
                                          "close": close, "open": open_,
                                          "volume": volume,
                                          "quote_volume": quote_volume,
                                          "t": t})
        if ev:
            alerts_q.put(ev)
            return

        candles_1h = buf_1h.get(sym)
        if not candles_1h or len(candles_1h) < 4:
            return
        new_state = build_active_state(sym, buf, candles_1h)
        if new_state:
            squeeze_state[sym] = new_state
            alerts_q.put({"type": "new_active", "symbol": sym,
                          "state": new_state})
        return

    ev = update_active_state(sym, state, {"high": high, "low": low,
                                          "close": close, "open": open_,
                                          "volume": volume,
                                          "quote_volume": quote_volume,
                                          "t": t})
    if ev:
        alerts_q.put(ev)


# ============ МЕХАНИЗМ 2: ИСТОРИЯ БИТ ============
def find_bit_patterns(sym, candles_5m, candles_1h):
    """
    Ищет паттерны БИТ в буфере 24ч.
    Возвращает список найденных паттернов.
    """
    patterns = []
    if len(candles_5m) < 50:
        return patterns

    g1h = calc_growth_1h(candles_1h)
    if not g1h:
        return patterns
    if g1h["growth_24h"] < cfg["bit_pattern_growth_min_pct"]:
        return patterns

    volume = calc_volume_24h_usdt(candles_5m)
    if volume < cfg["volume_24h_min_usdt"]:
        return patterns

    # идём по буферу от хая
    t_high_ts = g1h["t_high_ts"]

    # ищем начало диапазона
    start_idx = find_range_start(candles_5m, t_high_ts)
    if start_idx is None:
        return patterns

    subset = list(candles_5m)[start_idx:]
    if len(subset) < cfg["bit_pattern_range_min_candles"]:
        return patterns

    # v2.0.1: range_high ФИКСИРУЕТСЯ на первые N свечей диапазона
    N = cfg["bit_pattern_range_min_candles"]
    first_n = subset[:N]
    rh = max(c["high"] for c in first_n)
    rl = min(c["low"] for c in first_n)
    if rl <= 0:
        return patterns
    width = (rh / rl - 1) * 100
    if width > cfg["range_width_abs_max_pct"]:
        return patterns
    if width < cfg["min_range_width_pct"]:
        return patterns
    if width > g1h["growth_24h"] * cfg["range_width_k1"]:
        return patterns

    # ищем выход вверх в subset ПОСЛЕ первых N свечей
    exit_candles = cfg["bit_pattern_exit_candles"]
    consecutive_outside = 0
    fired_ts = None
    exit_price = None

    for c in subset[N:]:
        if c["close"] > rh:
            consecutive_outside += 1
            if consecutive_outside >= exit_candles:
                fired_ts = c["t"] / 1000.0
                exit_price = c["close"]
                break
        else:
            consecutive_outside = 0

    if fired_ts is None:
        return patterns

    # один паттерн на монету
    pattern = {
        "started": subset[0]["t"] / 1000.0,
        "range_low": rl,
        "range_high": rh,
        "width_pct": width,
        "growth_24h": g1h["growth_24h"],
        "volume_24h": volume,
        "fired_ts": fired_ts,
        "exit_price": exit_price,
    }
    patterns.append(pattern)
    return patterns


def scan_bit_patterns_for_symbol(sym):
    """Прогоняет монету через Механизм 2 и записывает найденные БИТ."""
    c5 = buf_5m.get(sym)
    c1h = buf_1h.get(sym)
    if not c5 or not c1h:
        return
    try:
        patterns = find_bit_patterns(sym, c5, c1h)
        for p in patterns:
            state_like = {
                "started": p["started"],
                "range_low": p["range_low"],
                "range_high": p["range_high"],
                "growth_24h": p["growth_24h"],
                "volume_24h": p["volume_24h"],
            }
            add_bit_history(sym, state_like, p["exit_price"],
                            fired_ts=p["fired_ts"])
    except Exception as e:
        worker_log(f"[v2.0.0] scan_bit_patterns error {sym}: {e}")


# ============ BINANCE ============
def load_symbols():
    req = urllib.request.Request(REST_EXCHANGE_INFO,
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.load(r)
    out = []
    for s in data["symbols"]:
        if s.get("contractType") != "PERPETUAL":
            continue
        if s.get("quoteAsset") != "USDT":
            continue
        if s.get("status") != "TRADING":
            continue
        out.append(s["symbol"])
    return out


def load_history_rest(sym, interval, limit, retries=3):
    url = f"{REST_KLINES}?symbol={quote(sym)}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.load(r)
            return [
                {
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "open": float(row[1]),
                    "volume": float(row[5]),
                    "quote_volume": float(row[7]),
                    "t": int(row[0]),
                    "closed": row[6] <= time.time() * 1000,
                }
                for row in data
            ]
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(0.5)
                continue
            worker_log(f"REST {interval} error {sym}: {e}")
            return None
    return None


def warmup_symbol(sym):
    data_5m = load_history_rest(sym, "5m", cfg["warmup_hours"] * 12)
    if data_5m:
        buf = deque(maxlen=cfg["warmup_hours"] * 12)
        for c in data_5m:
            if c["closed"]:
                buf.append({k: c[k] for k in ("high", "low", "close",
                                              "open", "volume",
                                              "quote_volume", "t")})
        buf_5m[sym] = buf

    data_1h = load_history_rest(sym, "1h", cfg["warmup_hours"] + 1)
    if data_1h:
        buf = deque(maxlen=cfg["warmup_hours"] + 1)
        for c in data_1h:
            if c["closed"]:
                buf.append({k: c[k] for k in ("high", "low", "close",
                                              "open", "volume",
                                              "quote_volume", "t")})
        buf_1h[sym] = buf

    if sym in buf_5m and sym in buf_1h:
        # Механизм 1 — активный
        st = build_active_state(sym, buf_5m[sym], buf_1h[sym])
        if st:
            squeeze_state[sym] = st
        # Механизм 2 — история
        scan_bit_patterns_for_symbol(sym)


def warmup_all(symbols):
    status["warmup_total"] = len(symbols)
    status["text"] = f"прогрев: 0 / {len(symbols)}"
    done = [0]
    lock = threading.Lock()

    def worker(sym):
        try:
            warmup_symbol(sym)
        except Exception as e:
            worker_log(f"warmup error {sym}: {e}")
        with lock:
            done[0] += 1
            status["warmup_done"] = done[0]
            status["text"] = f"прогрев: {done[0]} / {len(symbols)}"

    batch = cfg["warmup_parallel"]
    for i in range(0, len(symbols), batch):
        chunk = symbols[i:i + batch]
        threads = []
        for sym in chunk:
            t = threading.Thread(target=worker, args=(sym,), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

    status["warmup_finished"] = True
    status["text"] = "онлайн"
    active = sum(1 for s in squeeze_state.values()
                 if s.get("status") == "active")
    worker_log(
        f"warmup finished: {done[0]} / {len(symbols)}, "
        f"active={active}, "
        f"filter: buf={status['stats_buf']}, "
        f"fresh={status['stats_fresh']}, "
        f"range={status['stats_range']}, "
        f"fail_growth={status['fail_growth']}, "
        f"fail_volume={status['fail_volume']}, "
        f"fail_width_k1={status['fail_width_k1']}"
    )


def ws_loop(symbols):
    url = WS_BASE + "/".join(f"{s.lower()}@kline_5m" for s in symbols)
    worker_log(f"WS start (kline_5m): {len(symbols)} symbols")
    conn = {"opened": False}

    def on_open(ws):
        conn["opened"] = True
        worker_log("WS open")

    def on_message(ws, msg):
        status["ticks"] += 1
        status["last"] = time.time()
        try:
            d = json.loads(msg)["data"]
            handle_kline_5m(d["s"], d["k"])
        except Exception as e:
            worker_log(f"msg error: {e}")

    def on_error(ws, err):
        worker_log(f"WS error: {err}")

    def on_close(ws, *a):
        worker_log("WS close")

    delay = 2
    while True:
        conn["opened"] = False
        try:
            ws = websocket.WebSocketApp(
                url, on_open=on_open, on_message=on_message,
                on_error=on_error, on_close=on_close)
            ws.run_forever(ping_interval=20, ping_timeout=10)
        except Exception as e:
            worker_log(f"WS exception: {e}")
        delay = 2 if conn["opened"] else min(delay * 1.5, 60)
        time.sleep(delay)


def manager():
    known = set()
    first_run = True
    while True:
        wait = 300
        try:
            syms = load_symbols()
            new = [s for s in syms if s not in known]
            status["symbols"] = len(known) + len(new)

            if first_run:
                warmup_all(syms)
                first_run = False

            if new:
                for i in range(0, len(new), cfg["chunk_size"]):
                    threading.Thread(
                        target=ws_loop,
                        args=(new[i:i + cfg["chunk_size"]],),
                        daemon=True).start()
                known.update(new)
                worker_log(f"symbols loaded: total={len(known)}, new={len(new)}")
        except Exception as e:
            worker_log(f"symbols error: {e}")
            status["text"] = "нет списка монет (REST)"
            wait = 5
        time.sleep(wait)


# ============ КЭШ ============
def save_cache():
    try:
        data = {
            "version": 100,   # v2.0.0
            "saved_at": time.time(),
            "squeeze_state": squeeze_state,
            "backstage": backstage,
        }
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        worker_log(f"save_cache error: {e}")


def load_cache():
    global squeeze_state, backstage
    if not os.path.exists(CACHE_PATH):
        return
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") != 100:
            worker_log(f"cache version mismatch, skipping")
            return
        squeeze_state.update(data.get("squeeze_state", {}))
        backstage.update(data.get("backstage", {}))
        worker_log(f"cache loaded: {len(squeeze_state)} states, "
                   f"{len(backstage)} backstage")
    except Exception as e:
        worker_log(f"load_cache error: {e}")


def cache_loop():
    while True:
        time.sleep(cfg["cache_interval_sec"])
        save_cache()


def backstage_cleanup_loop():
    while True:
        time.sleep(300)
        now = time.time()
        ttl = cfg["backstage_ttl_hours"] * 3600
        to_del = [s for s, b in backstage.items()
                  if now - b.get("left_at", 0) > ttl]
        for s in to_del:
            del backstage[s]
        to_del2 = [s for s, st in squeeze_state.items()
                   if st.get("status") == "cancelled"
                   and now - st.get("left_at", 0) > ttl]
        for s in to_del2:
            del squeeze_state[s]


def stats_loop():
    while True:
        time.sleep(60)
        active = sum(1 for s in squeeze_state.values()
                     if s.get("status") == "active")
        broken = sum(1 for s in squeeze_state.values()
                     if s.get("status", "").startswith("broken"))
        worker_log(
            f"stats: buf5m={len(buf_5m)}, buf1h={len(buf_1h)}, "
            f"filter: buf={status['stats_buf']}, "
            f"fresh={status['stats_fresh']}, "
            f"range={status['stats_range']}, "
            f"active={active}, broken={broken}, "
            f"backstage={len(backstage)}, "
            f"bit_history={len(bit_history)}"
        )
        worker_log(
            f"filter_debug: growth_fail={status['fail_growth']}, "
            f"volume_fail={status['fail_volume']}, "
            f"width_k1_fail={status['fail_width_k1']}, "
            f"width_abs_fail={status['fail_width_abs']}, "
            f"width_min_fail={status['fail_width_min']}"
        )
        try:
            update_bit_history_stats()
        except Exception as e:
            worker_log(f"update_bit_history error: {e}")


# ============ ЗВУК ============
def play_sound():
    if not cfg.get("sound", True):
        return
    path = os.path.join(BASE_DIR, cfg.get("sound_file",
                                          "assets/squeeze_alert.wav"))
    try:
        if os.path.exists(path):
            winsound.PlaySound(path,
                               winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            threading.Thread(target=_beep_fallback, daemon=True).start()
    except Exception as e:
        worker_log(f"sound error: {e}")


def _beep_fallback():
    try:
        winsound.Beep(1000, 100)
        time.sleep(0.08)
        winsound.Beep(1400, 100)
    except Exception:
        pass


# ============ UI ============
class LegendBar(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color=BG, height=18, corner_radius=0)
        self.pack_propagate(False)
        items = [
            ("●", PEACH, "активен"),
            ("●", GREEN, "вынос вверх"),
            ("●", RED, "вынос вниз"),
            ("●", GREY, "затянувшийся"),
            ("★", GOLD, "БИТ сработал"),
        ]
        for i, (ch, color, text) in enumerate(items):
            lbl_dot = ctk.CTkLabel(self, text=ch, font=("Segoe UI", 11),
                                   text_color=color)
            lbl_dot.pack(side="left", padx=(12 if i == 0 else 6, 2))
            lbl_txt = ctk.CTkLabel(self, text=text,
                                   font=("Segoe UI", 9),
                                   text_color=MINT)
            lbl_txt.pack(side="left")


class AlertPopup(ctk.CTkToplevel):
    WIDTH = 320
    HEIGHT = 56

    def __init__(self, parent, title, subtitle, color):
        super().__init__(parent)
        self.overrideredirect(True)
        try:
            self.attributes("-topmost", True)
        except Exception:
            pass
        self.configure(fg_color=BG_DARK)

        px = parent.winfo_x()
        py = parent.winfo_y()
        x = px + parent.winfo_width() + 8
        y = py + 60
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")

        frame = ctk.CTkFrame(self, fg_color=BG_DARK,
                             border_color=color, border_width=2,
                             corner_radius=8)
        frame.pack(fill="both", expand=True, padx=2, pady=2)

        dot = ctk.CTkLabel(frame, text="●", font=("Segoe UI", 18),
                           text_color=color)
        dot.pack(side="left", padx=(10, 6))

        text_frame = ctk.CTkFrame(frame, fg_color=BG_DARK)
        text_frame.pack(side="left", fill="both", expand=True, pady=6)

        lbl_title = ctk.CTkLabel(text_frame, text=title,
                                 font=("Segoe UI", 12, "bold"),
                                 text_color=color, anchor="w")
        lbl_title.pack(anchor="w")

        lbl_sub = ctk.CTkLabel(text_frame, text=subtitle,
                               font=("Segoe UI", 10),
                               text_color=MINT, anchor="w")
        lbl_sub.pack(anchor="w")


# ============ ОКНО ИСТОРИИ БИТ ============
class BitHistoryWindow(ctk.CTkToplevel):
    WIDTH = 1100
    HEIGHT = 600

    COLUMNS = [
        ("ТИКЕР",       80),
        ("ОБНАРУЖЕН",   130),
        ("СРАБОТАЛ",    130),
        ("В ДИАП",      70),
        ("ДИАПАЗОН",    180),
        ("ШИРИНА",      65),
        ("РОСТ",        60),
        ("ЦЕНА ВЫХ",    90),
        ("+1ч",         60),
        ("+6ч",         60),
        ("+24ч",        60),
    ]

    def __init__(self, parent):
        super().__init__(parent)
        self.overrideredirect(False)
        self.title("Отработанные БИТ")
        self.configure(fg_color=BG)
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.resizable(True, True)
        px = parent.winfo_x()
        py = parent.winfo_y()
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}+{px + 60}+{py + 60}")
        self._build()
        self.refresh()
        self._auto_refresh()

    def _build(self):
        head = ctk.CTkFrame(self, fg_color=BG_DARK, height=48,
                            corner_radius=0)
        head.pack(fill="x")
        head.pack_propagate(False)

        title = ctk.CTkLabel(head, text="★ Отработанные БИТ",
                             font=("Segoe UI", 16, "bold"),
                             text_color=GOLD)
        title.pack(side="left", padx=16)

        self.count_label = ctk.CTkLabel(head, text="",
                                        font=("Segoe UI", 11),
                                        text_color=SAND)
        self.count_label.pack(side="left", padx=8)

        btn_close = ctk.CTkButton(head, text="✕", width=32, height=28,
                                  font=("Segoe UI", 12, "bold"),
                                  fg_color=ACCENT, hover_color="#8B2E2E",
                                  text_color=BG_DARK,
                                  command=self.destroy)
        btn_close.pack(side="right", padx=8)

        btn_export = ctk.CTkButton(head, text="Экспорт CSV", width=110,
                                   height=28,
                                   font=("Segoe UI", 11),
                                   fg_color=ACCENT,
                                   hover_color=ACCENT_HOVER,
                                   text_color=BG_DARK,
                                   command=self._export_csv)
        btn_export.pack(side="right", padx=4)

        btn_refresh = ctk.CTkButton(head, text="Обновить", width=90,
                                    height=28,
                                    font=("Segoe UI", 11),
                                    fg_color=ACCENT,
                                    hover_color=ACCENT_HOVER,
                                    text_color=BG_DARK,
                                    command=self.refresh)
        btn_refresh.pack(side="right", padx=4)

        colhead = ctk.CTkFrame(self, fg_color=BG_DARK, height=28,
                               corner_radius=0)
        colhead.pack(fill="x", padx=8, pady=(4, 0))
        colhead.pack_propagate(False)
        for name, w in self.COLUMNS:
            lbl = ctk.CTkLabel(colhead, text=name,
                               font=("Segoe UI", 10, "bold"),
                               text_color=SAND, width=w, anchor="w")
            lbl.pack(side="left", padx=(4, 0))

        self.scroll_frame = ctk.CTkScrollableFrame(self, fg_color=BG,
                                                    corner_radius=0)
        self.scroll_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _export_csv(self):
        try:
            path = filedialog.asksaveasfilename(
                defaultextension=".csv",
                filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
                initialfile="bit_history_export.csv",
                title="Сохранить историю БИТ")
            if not path:
                return
            with open(path, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(_bit_history_header())
                with bit_history_lock:
                    for row in bit_history:
                        w.writerow([row.get(k, "")
                                    for k in _bit_history_header()])
        except Exception as e:
            worker_log(f"export_csv error: {e}")

    def refresh(self):
        for w in self.scroll_frame.winfo_children():
            w.destroy()
        with bit_history_lock:
            rows = list(bit_history)
        self.count_label.configure(text=f"всего: {len(rows)}")
        rows = list(reversed(rows))
        if not rows:
            lbl = ctk.CTkLabel(self.scroll_frame,
                               text="Пока нет отработанных БИТ",
                               font=("Segoe UI", 12, "italic"),
                               text_color=GREY)
            lbl.pack(pady=40)
            return
        for i, row in enumerate(rows):
            self._build_row(row, i)

    def _build_row(self, row, idx):
        bg = BG_ROW_ALT if idx % 2 else BG
        row_frame = ctk.CTkFrame(self.scroll_frame, fg_color=bg,
                                  height=32, corner_radius=4)
        row_frame.pack(fill="x", pady=1)
        row_frame.pack_propagate(False)

        sym = row.get("symbol", "")
        ticker = sym.replace("USDT", "")
        detected = row.get("detected_at", "")
        fired = row.get("fired_at", "")
        duration_sec = row.get("duration_in_range_sec", 0)
        range_high = row.get("range_high", 0)
        range_low = row.get("range_low", 0)
        width = row.get("width_pct", 0)
        growth = row.get("growth_24h", 0)
        exit_price = row.get("exit_price", 0)
        pct_1h = row.get("pct_after_1h", 0)
        pct_6h = row.get("pct_after_6h", 0)
        pct_24h = row.get("pct_after_24h", 0)

        if range_low and range_high:
            rng_str = f"{range_low:.4f} — {range_high:.4f}"
        else:
            rng_str = "—"

        values = [
            (ticker, GOLD, "bold"),
            (detected[:16] if detected else "—", MINT, "normal"),
            (fired[:16] if fired else "—", MINT, "normal"),
            (fmt_duration(duration_sec), MINT, "normal"),
            (rng_str, MINT, "normal"),
            (f"{width:.2f}%" if width else "—", PEACH, "bold"),
            (f"{growth:.1f}%" if growth else "—", SAND, "normal"),
            (f"{exit_price:.6f}" if exit_price else "—", MINT, "normal"),
            (self._pct_str(pct_1h), self._pct_color(pct_1h), "bold"),
            (self._pct_str(pct_6h), self._pct_color(pct_6h), "bold"),
            (self._pct_str(pct_24h), self._pct_color(pct_24h), "bold"),
        ]
        for (name, w), (val, color, weight) in zip(self.COLUMNS, values):
            lbl = ctk.CTkLabel(row_frame, text=str(val),
                               font=("Segoe UI", 10, weight),
                               text_color=color, width=w, anchor="w")
            lbl.pack(side="left", padx=(4, 0))

    @staticmethod
    def _pct_str(pct):
        if not pct:
            return "—"
        sign = "+" if pct >= 0 else ""
        return f"{sign}{pct:.2f}%"

    @staticmethod
    def _pct_color(pct):
        if not pct:
            return GREY
        if pct >= 5:
            return GREEN
        if pct >= 1:
            return PEACH
        if pct > 0:
            return SAND
        return RED

    def _auto_refresh(self):
        try:
            self.refresh()
        except Exception as e:
            worker_log(f"bit window auto_refresh error: {e}")
        self.after(10000, self._auto_refresh)


# ============ ГЛАВНОЕ ОКНО ============
class SqueezeWidget(ctk.CTk):
    WIDTH = 520
    HEADER_H = 44
    LEGEND_H = 18
    COL_HEADER_H = 26
    ROW_H = 32
    SECTION_H = 20
    TABLE_H = ROW_H * 12 + SECTION_H * 4
    IDLE_H = 60

    COLUMNS = [
        ("ТИКЕР",    90),
        ("ВРЕМЯ",    70),
        ("ШИРИНА",   70),
        ("ПОЗИЦИЯ",  90),
        ("КАСАНИЕ",  60),
        ("ОБЪЁМ",    70),
    ]
    COL_ICON_W = 24
    COL_PAD = 6

    def __init__(self):
        super().__init__(fg_color=BG)
        self.overrideredirect(True)
        self.title("Squeeze Widget")

        total_h = self.HEADER_H + self.LEGEND_H + self.COL_HEADER_H + \
            self.TABLE_H + 20

        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - self.WIDTH) // 2
        y = (sh - total_h) // 2
        self.geometry(f"{self.WIDTH}x{total_h}+{x}+{y}")

        self._setup_alt_tab()

        self.history = deque(maxlen=cfg["history_size"])
        self.paused = False
        self.collapsed = False
        self._press_x = 0
        self._press_y = 0
        self._dx = 0
        self._dy = 0
        self.last_draw = 0.0
        self._last_render_hash = None

        self.main = ctk.CTkFrame(self, fg_color=BG, corner_radius=10)
        self.main.pack(fill="both", expand=True)

        self._build_header()
        self._build_legend()
        self._build_warmup_bar()
        self._build_table_header()
        self._build_table()
        self._build_menu()

        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_motion)
        self.bind("<ButtonRelease-1>", self._on_release)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.active_popups = []
        self.bit_window = None
        self.poll()

    def _setup_alt_tab(self):
        try:
            import ctypes
            GWL_EXSTYLE = -20
            WS_EX_APPWINDOW = 0x00040000
            WS_EX_TOOLWINDOW = 0x00000080
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            if not hwnd:
                hwnd = self.winfo_id()
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            self.withdraw()
            self.after(50, self.deiconify)
            worker_log(f"[alt_tab] applied, hwnd={hwnd}")
        except Exception as e:
            worker_log(f"[alt_tab] error: {e}")

    def _build_header(self):
        h = ctk.CTkFrame(self.main, fg_color=BG_DARK, height=self.HEADER_H,
                         corner_radius=10)
        h.pack(fill="x")
        h.pack_propagate(False)

        title = ctk.CTkLabel(h, text="Squeeze Widget",
                             font=("Segoe UI", 14, "bold"),
                             text_color=PEACH)
        title.pack(side="left", padx=(14, 8))

        self.status_label = ctk.CTkLabel(h, text="инициализация...",
                                         font=("Segoe UI", 10),
                                         text_color=SAND)
        self.status_label.pack(side="left", padx=4)

        self.online_dot = ctk.CTkLabel(h, text="●", font=("Segoe UI", 14),
                                       text_color=RED)
        self.online_dot.pack(side="right", padx=(0, 10))

        btn_min = ctk.CTkButton(h, text="—", width=28, height=24,
                                font=("Segoe UI", 12, "bold"),
                                fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                text_color=BG_DARK,
                                command=self._toggle_collapse)
        btn_min.pack(side="right", padx=(0, 4))

        btn_close = ctk.CTkButton(h, text="✕", width=28, height=24,
                                  font=("Segoe UI", 12, "bold"),
                                  fg_color=ACCENT, hover_color="#8B2E2E",
                                  text_color=BG_DARK,
                                  command=self._on_close)
        btn_close.pack(side="right", padx=(0, 4))

        btn_bit = ctk.CTkButton(h, text="★ БИТ", width=70, height=24,
                                font=("Segoe UI", 11, "bold"),
                                fg_color=GOLD, hover_color=PEACH,
                                text_color=BG_DARK,
                                command=self._open_bit_window)
        btn_bit.pack(side="right", padx=(0, 4))

    def _build_legend(self):
        self.legend = LegendBar(self.main)
        self.legend.pack(fill="x", padx=12, pady=(2, 0))

    def _build_warmup_bar(self):
        self.warmup_frame = ctk.CTkFrame(self.main, fg_color=BG,
                                         height=26, corner_radius=0)
        self.warmup_frame.pack(fill="x", padx=12, pady=(2, 0))
        self.warmup_frame.pack_propagate(False)

        self.warmup_bar = ctk.CTkProgressBar(self.warmup_frame,
                                             progress_color=PEACH,
                                             fg_color=BG_DARK,
                                             height=10)
        self.warmup_bar.pack(fill="x", side="left", expand=True, padx=(0, 8))
        self.warmup_bar.set(0)

        self.warmup_label = ctk.CTkLabel(self.warmup_frame, text="",
                                         font=("Segoe UI", 10),
                                         text_color=SAND, width=120)
        self.warmup_label.pack(side="right")

    def _build_table_header(self):
        self.col_header = ctk.CTkFrame(self.main, fg_color=BG_DARK,
                                       height=self.COL_HEADER_H,
                                       corner_radius=0)
        self.col_header.pack(fill="x", padx=12, pady=(4, 0))
        self.col_header.pack_propagate(False)

        spacer = ctk.CTkLabel(self.col_header, text="",
                              width=self.COL_ICON_W)
        spacer.pack(side="left")

        for name, w in self.COLUMNS:
            lbl = ctk.CTkLabel(self.col_header, text=name,
                               font=("Segoe UI", 10, "bold"),
                               text_color=SAND, width=w, anchor="w")
            lbl.pack(side="left", padx=(self.COL_PAD, 0))

    def _build_table(self):
        # v2.0.1: скролл через CTkScrollableFrame
        self.table_frame = ctk.CTkScrollableFrame(
            self.main, fg_color=BG, corner_radius=0,
            scrollbar_button_color=ACCENT,
            scrollbar_button_hover_color=ACCENT_HOVER,
        )
        self.table_frame.pack(fill="both", expand=True,
                              padx=12, pady=(0, 4))
        self.row_widgets = []

        self.version_lbl = ctk.CTkLabel(
            self.main, text=f"v{WIDGET_VERSION}",
            font=("Segoe UI", 11, "bold"),
            text_color=GOLD, anchor="e"
        )
        self.version_lbl.pack(fill="x", padx=14, pady=(0, 4))

    def _build_menu(self):
        self.menu = Menu(self, tearoff=0, bg=BG_DARK, fg=MINT,
                         activebackground=ACCENT, activeforeground=BG_DARK,
                         bd=0)
        self.menu.add_command(label="Пауза", command=self._toggle_pause)
        self.menu.add_command(label="Очистить историю",
                              command=self._clear_history)
        self.menu.add_command(label="Открыть окно БИТ",
                              command=self._open_bit_window)
        self.menu.add_separator()
        self.menu.add_command(label="Выход", command=self._on_close)
        self.bind("<Button-3>", self._show_menu)

    def _open_bit_window(self):
        if self.bit_window is not None:
            try:
                if self.bit_window.winfo_exists():
                    self.bit_window.lift()
                    self.bit_window.focus_force()
                    return
            except Exception:
                pass
        self.bit_window = BitHistoryWindow(self)

    def _copy_ticker(self, symbol):
        try:
            self.clipboard_clear()
            self.clipboard_append(symbol)
            worker_log(f"[copy] {symbol}")
        except Exception as e:
            worker_log(f"[copy] error: {e}")

    def _calc_render_hash(self):
        now = time.time()
        parts = []
        for sym, st in sorted(squeeze_state.items()):
            sv = st.get("status", "forming")
            if sv == "cancelled":
                continue
            mins = int((now - st.get("started", now)) / 60)
            parts.append(f"{sym}:{sv}:{mins}:{st.get('touch_count', 0)}")
        return hash(tuple(parts))

    def _on_press(self, e):
        self._press_x = e.x
        self._press_y = e.y
        self._dx = e.x
        self._dy = e.y

    def _on_motion(self, e):
        dx = abs(e.x - self._press_x)
        dy = abs(e.y - self._press_y)
        if dx > 3 or dy > 3:
            x = self.winfo_x() + e.x - self._dx
            y = self.winfo_y() + e.y - self._dy
            self.geometry(f"+{x}+{y}")

    def _on_release(self, e):
        pass

    def _on_close(self):
        try:
            save_cache()
            save_last_seen()
            save_bit_history()
        except Exception:
            pass
        for p in list(self.active_popups):
            try:
                p.destroy()
            except Exception:
                pass
        if self.bit_window is not None:
            try:
                self.bit_window.destroy()
            except Exception:
                pass
        worker_log("=== stop ===")
        self.destroy()

    def _show_menu(self, e):
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def _toggle_pause(self):
        self.paused = not self.paused

    def _clear_history(self):
        self.history.clear()
        self.last_draw = 0

    def _toggle_collapse(self):
        self.collapsed = not self.collapsed
        if self.collapsed:
            self.geometry(f"{self.WIDTH}x{self.IDLE_H}")
        else:
            total_h = self.HEADER_H + self.LEGEND_H + self.COL_HEADER_H + \
                self.TABLE_H + 20
            self.geometry(f"{self.WIDTH}x{total_h}")

    def _render_warmup(self):
        if status["warmup_finished"]:
            self.warmup_frame.pack_forget()
            return
        total = max(1, status["warmup_total"])
        done = status["warmup_done"]
        self.warmup_bar.set(done / total)
        self.warmup_label.configure(text=f"{done} / {total}")

    def _render_status(self):
        self.status_label.configure(text=status.get("text", ""))
        live = status.get("last", 0) and time.time() - status["last"] < 15
        self.online_dot.configure(text_color=GREEN if live else RED)

    def _render_section_title(self, text, color=SAND):
        lbl = ctk.CTkLabel(self.table_frame, text=text,
                           font=("Segoe UI", 10, "bold"),
                           text_color=color, anchor="w",
                           fg_color=BG_SECTION, corner_radius=4,
                           height=self.SECTION_H)
        lbl.pack(fill="x", pady=(4, 2))
        self.row_widgets.append(lbl)

    def _render_rows(self):
        for w in self.row_widgets:
            w.destroy()
        self.row_widgets.clear()

        now = time.time()
        active, broken_up, broken_down, long_ranges = [], [], [], []

        for sym, st in squeeze_state.items():
            sv = st.get("status", "active")
            if sv == "cancelled":
                continue
            duration_h = (now - st.get("started", now)) / 3600.0
            is_long = duration_h >= cfg["long_range_hours"]

            if sv == "active":
                if is_long:
                    long_ranges.append((sym, st))
                else:
                    active.append((sym, st))
            elif sv == "broken_up":
                broken_up.append((sym, st))
            elif sv == "broken_down":
                broken_down.append((sym, st))

        def width_of(item):
            s = item[1]
            rh = s.get("range_high", 0)
            rl = s.get("range_low", 0)
            return (rh / rl - 1) * 100 if rl > 0 else 999

        active.sort(key=width_of)
        long_ranges.sort(key=width_of)
        broken_up.sort(key=lambda x: x[1].get("last_breakout", 0), reverse=True)
        broken_down.sort(key=lambda x: x[1].get("last_breakout", 0),
                         reverse=True)

        if not (active or broken_up or broken_down or long_ranges):
            lbl = ctk.CTkLabel(self.table_frame,
                               text=cfg["idle_text"],
                               font=("Segoe UI", 12, "italic"),
                               text_color=MINT)
            lbl.pack(pady=40)
            self.row_widgets.append(lbl)
            return

        # v2.0.1: без лимита — скролл позволяет видеть всё
        row_idx = 0

        if active:
            self._render_section_title("АКТИВНЫЕ", PEACH)
            for sym, st in active:
                self._build_row(sym, st, row_idx)
                row_idx += 1

        if (broken_up or broken_down):
            self._render_section_title("ВЫНОСЫ", GREEN)
            combined = broken_up + broken_down
            combined.sort(key=lambda x: x[1].get("last_breakout", 0),
                          reverse=True)
            for sym, st in combined:
                self._build_row(sym, st, row_idx)
                row_idx += 1

        if long_ranges:
            self._render_section_title("ЗАТЯНУВШИЕСЯ", GREY)
            for sym, st in long_ranges:
                self._build_row(sym, st, row_idx)
                row_idx += 1

    def _build_row(self, sym, st, idx):
        now = time.time()
        sv = st.get("status", "active")
        duration_h = (now - st.get("started", now)) / 3600.0
        is_long = duration_h >= cfg["long_range_hours"]

        row_bg = BG_ROW_ALT if idx % 2 else BG

        row = ctk.CTkFrame(self.table_frame, fg_color=row_bg,
                           height=self.ROW_H, corner_radius=4)
        row.pack(fill="x", pady=1)
        row.pack_propagate(False)

        if sv == "broken_up":
            icon_color = GREEN
            icon_char = "●"
        elif sv == "broken_down":
            icon_color = RED
            icon_char = "●"
        elif is_long:
            icon_color = GREY
            icon_char = "●"
        else:
            icon_color = PEACH
            icon_char = "●"

        last_touch = st.get("last_touch", 0)
        if last_touch and now - last_touch < 30:
            icon_char = "◉"
            icon_color = PEACH

        icon_lbl = ctk.CTkLabel(row, text=icon_char,
                                font=("Segoe UI", 14),
                                text_color=icon_color,
                                width=self.COL_ICON_W)
        icon_lbl.pack(side="left")

        ticker = sym.replace("USDT", "")
        if sv == "broken_up":
            fg = GREEN
            fnt = ("Segoe UI", 11, "bold")
        elif sv == "broken_down":
            fg = RED
            fnt = ("Segoe UI", 11, "bold")
        elif is_long:
            fg = GREY
            fnt = ("Segoe UI", 11, "italic")
        else:
            fg = PEACH
            fnt = ("Segoe UI", 11, "bold")
        l = ctk.CTkLabel(row, text=ticker, font=fnt, text_color=fg,
                         width=self.COLUMNS[0][1], anchor="w")
        l.pack(side="left", padx=(self.COL_PAD, 0))
        l.bind("<Button-1>", lambda e, s=sym: self._copy_ticker(s))

        dur = now - st.get("started", now)
        l = ctk.CTkLabel(row, text=fmt_duration(dur),
                         font=("Segoe UI", 11), text_color=MINT,
                         width=self.COLUMNS[1][1], anchor="w")
        l.pack(side="left", padx=(self.COL_PAD, 0))

        rh = st.get("range_high", 0)
        rl = st.get("range_low", 0)
        width = (rh / rl - 1) * 100 if rl > 0 else 0
        l = ctk.CTkLabel(row, text=f"{width:.2f}%",
                         font=("Segoe UI", 11, "bold"), text_color=PEACH,
                         width=self.COLUMNS[2][1], anchor="w")
        l.pack(side="left", padx=(self.COL_PAD, 0))

        try:
            buf = buf_5m.get(sym)
            close = buf[-1]["close"] if buf else 0
            if rh > rl and close > 0:
                pos_pct = (close - rl) / (rh - rl) * 100
            else:
                pos_pct = 50
        except Exception:
            pos_pct = 50
        if pos_pct <= 30:
            pos_text = "Низ"
        elif pos_pct >= 70:
            pos_text = "Верх"
        else:
            pos_text = "Середина"
        l = ctk.CTkLabel(row, text=pos_text,
                         font=("Segoe UI", 11), text_color=MINT,
                         width=self.COLUMNS[3][1], anchor="w")
        l.pack(side="left", padx=(self.COL_PAD, 0))

        tc = st.get("touch_count", 0)
        l = ctk.CTkLabel(row, text=f"{tc}" if tc else "—",
                         font=("Segoe UI", 11), text_color=SAND,
                         width=self.COLUMNS[4][1], anchor="w")
        l.pack(side="left", padx=(self.COL_PAD, 0))

        vol = st.get("volume_24h", 0)
        if vol >= 1_000_000_000:
            vt = f"{vol/1e9:.1f}B"
        elif vol >= 1_000_000:
            vt = f"{vol/1e6:.1f}M"
        else:
            vt = f"{vol/1e3:.0f}K"
        l = ctk.CTkLabel(row, text=vt,
                         font=("Segoe UI", 11), text_color=PEACH,
                         width=self.COLUMNS[5][1], anchor="w")
        l.pack(side="left", padx=(self.COL_PAD, 0))

        if is_long:
            long_lbl = ctk.CTkLabel(row, text="затянувшийся",
                                    font=("Segoe UI", 9, "italic"),
                                    text_color=GREY, anchor="w")
            long_lbl.pack(side="left", padx=(8, 0))

        self.row_widgets.append(row)

    def _render(self):
        self._render_warmup()
        self._render_status()
        self._render_rows()

    def _show_popup(self, ev):
        sym = ev.get("symbol", "?")
        ticker = sym.replace("USDT", "")
        etype = ev.get("type")
        st = ev.get("state", {})

        if etype == "new_active":
            title = f"{ticker} · активен"
            dur = time.time() - st.get("started", time.time())
            width = (st.get("range_high", 0) / st.get("range_low", 1) - 1) * 100 \
                if st.get("range_low", 0) > 0 else 0
            subtitle = f"Диапазон {width:.2f}% · {fmt_duration(dur)}"
            color = PEACH
        elif etype == "breakout":
            side = ev.get("side", "up")
            title = f"{ticker} · вынос {'вверх' if side == 'up' else 'вниз'}"
            subtitle = "Close за границей"
            color = GREEN if side == "up" else RED
        elif etype == "return":
            title = f"{ticker} · возврат"
            subtitle = "Вернулась в диапазон"
            color = PEACH
        elif etype == "return_from_backstage":
            title = f"{ticker} · возврат (задний двор)"
            subtitle = "Снова в диапазоне"
            color = PEACH
        elif etype == "bit_signal":
            title = f"★ {ticker} · БИТ сработал"
            subtitle = "Уход вверх"
            color = GOLD
        elif etype == "cancelled":
            title = f"{ticker} · отменён"
            subtitle = "Уход вниз"
            color = RED
        else:
            return

        try:
            popup = AlertPopup(self, title, subtitle, color)
            self.active_popups.append(popup)
            while len(self.active_popups) > 3:
                old = self.active_popups.pop(0)
                try:
                    old.destroy()
                except Exception:
                    pass
            self.after(cfg["alert_show_sec"] * 1000,
                       lambda p=popup: self._close_popup(p))
        except Exception as e:
            worker_log(f"popup error: {e}")

    def _close_popup(self, popup):
        try:
            popup.destroy()
        except Exception:
            pass
        if popup in self.active_popups:
            self.active_popups.remove(popup)

    def poll(self):
        now = time.time()
        changed = False
        while True:
            try:
                ev = alerts_q.get_nowait()
            except queue.Empty:
                break
            if self.paused:
                continue
            if ev.get("type") == "new_active":
                threading.Thread(target=play_sound, daemon=True).start()
            if ev.get("type") == "bit_signal":
                threading.Thread(target=play_sound, daemon=True).start()
            if ev.get("type") != "forming":
                self._show_popup(ev)
            changed = True

        new_hash = self._calc_render_hash()
        need_render = (changed or new_hash != self._last_render_hash)
        if need_render:
            self._render()
            self._last_render_hash = new_hash
            self.last_draw = now
        self.after(1000, self.poll)


# ============ TEST MODE ============
def inject_test():
    import random
    now = time.time()
    fake = [
        ("GTCUSDT", 0.85, 0.84, now - 1800, "active", False),
        ("OGNUSDT", 0.125, 0.122, now - 1800, "active", False),
        ("STRKUSDT", 2.45, 2.40, now - 1700, "active", False),
        ("CKBUSDT", 0.0125, 0.0123, now - 1800, "broken_up", False),
        ("WUSDT", 0.95, 0.92, now - 1700, "broken_down", False),
        ("SAFEUSDT", 0.510, 0.505, now - 60 * 3600, "active", True),
        ("DUSKUSDT", 0.350, 0.346, now - 900, "active", False),
    ]
    for sym, rh, rl, started, stat, is_long in fake:
        squeeze_state[sym] = {
            "status": stat,
            "range_high": rh,
            "range_low": rl,
            "started": started,
            "candles_in_range": 12,
            "last_touch": now - 120,
            "last_breakout": now - 60 if stat.startswith("broken") else 0.0,
            "breakout_side": "up" if stat == "broken_up" else
                             ("down" if stat == "broken_down" else None),
            "touch_count": random.randint(1, 4),
            "breakout_count": 0,
            "exit_candles": 1 if stat.startswith("broken") else 0,
            "growth_24h": 45.0,
            "volume_24h": 25_000_000,
            "high_24h": rh * 1.3,
            "last_update_ts": now,
        }
    status["warmup_finished"] = True
    status["text"] = "онлайн (тест)"


# ============ MAIN ============
if __name__ == "__main__":
    load_config()
    load_cache()
    load_bit_history()
    save_last_seen()
    worker_log(f"=== start v{WIDGET_VERSION} ===")

    if "--test" in sys.argv:
        inject_test()
    else:
        threading.Thread(target=manager, daemon=True).start()
        threading.Thread(target=cache_loop, daemon=True).start()
        threading.Thread(target=stats_loop, daemon=True).start()
        threading.Thread(target=backstage_cleanup_loop, daemon=True).start()

    try:
        app = SqueezeWidget()
        app.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        save_cache()
        save_last_seen()
        save_bit_history()