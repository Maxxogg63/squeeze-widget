"""
squeeze_replay.py — калибровка поиска диапазонов
Автономный скрипт: прогревает, сканирует, показывает активные монеты,
даёт заполнить свои эталонные значения.

Запуск: python squeeze_replay.py
"""
import csv
import io
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
import urllib.request
from collections import deque
from datetime import datetime
from urllib.parse import quote

import customtkinter as ctk
from tkinter import Menu


# ============ ПУТИ ============
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "squeeze_config.json")
FILTERS_PATH = os.path.join(BASE_DIR, "squeeze_filters.txt")
WORKER_LOG_PATH = os.path.join(BASE_DIR, "squeeze_worker.log")
CALIBRATION_PATH = os.path.join(BASE_DIR, "calibration.csv")


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
    "growth_24h_min_pct": 20.0,
    "volume_24h_min_usdt": 10_000_000,
    "range_width_k1": 0.8,
    "range_width_abs_max_pct": 20.0,
    "min_range_width_pct": 0.1,
    "range_forming_min_candles": 6,
    "range_active_min_candles": 6,
    "warmup_hours": 24,
    "warmup_parallel": 5,
}

FILTER_KEYS = [
    "growth_24h_min_pct",
    "volume_24h_min_usdt",
    "range_width_k1",
    "range_width_abs_max_pct",
    "min_range_width_pct",
    "range_forming_min_candles",
    "range_active_min_candles",
]

REST_EXCHANGE_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"
REST_KLINES = "https://fapi.binance.com/fapi/v1/klines"


# ============ СОСТОЯНИЕ ============
status = {
    "text": "инициализация...",
    "warmup_done": 0,
    "warmup_total": 0,
    "warmup_finished": False,
}

buf_5m: dict = {}   # symbol -> deque 288 свечей
scan_result: list = []   # список активных монет

cfg = dict(DEFAULTS)


# ============ ЛОГ ============
def worker_log(msg):
    try:
        with open(WORKER_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | [replay] {msg}\n")
    except Exception:
        pass


# ============ КОНФИГ ============
def load_config():
    global cfg
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
            cfg.update({k: v for k, v in user.items() if k in DEFAULTS})
        except Exception as e:
            print("config load error:", e)

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
                        pass
        except Exception as e:
            print("filters load error:", e)


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
                time.sleep(1 + attempt)
                continue
            worker_log(f"REST {interval} error {sym}: {e}")
            return None
    return None


# ============ ДЕТЕКТОР (копии из squeeze_widget.py) ============
def calc_growth_5m(candles_5m):
    """Рост и падение с учётом порядка."""
    if not candles_5m or len(candles_5m) < 4:
        return None
    lst = list(candles_5m)

    max_growth = 0.0
    min_low_so_far = lst[0]["low"]
    idx_high_for_growth = 0
    for i, c in enumerate(lst):
        if min_low_so_far > 0:
            g = c["high"] / min_low_so_far - 1
            if g > max_growth:
                max_growth = g
                idx_high_for_growth = i
        if c["low"] < min_low_so_far:
            min_low_so_far = c["low"]

    max_drop = 0.0
    max_high_so_far = lst[0]["high"]
    for i, c in enumerate(lst):
        if max_high_so_far > 0:
            d = 1 - c["low"] / max_high_so_far
            if d > max_drop:
                max_drop = d
        if c["high"] > max_high_so_far:
            max_high_so_far = c["high"]

    close_24h_ago = lst[0]["close"]
    close_now = lst[-1]["close"]
    change_24h = (close_now / close_24h_ago - 1) * 100 \
        if close_24h_ago > 0 else 0.0

    t_high_ts = lst[idx_high_for_growth]["t"] / 1000.0

    return {
        "growth_24h": max_growth * 100,
        "drop_24h": max_drop * 100,
        "change_24h": change_24h,
        "high_24h": max_high_so_far,
        "t_high_ts": t_high_ts,
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


def is_fresh(growth, volume, drop=0.0):
    if max(growth, drop) < cfg["growth_24h_min_pct"]:
        return False, "growth"
    if volume and volume < cfg["volume_24h_min_usdt"]:
        return False, "volume"
    return True, "ok"


def check_range_width(impulse, width):
    if impulse <= 0:
        return False, "no_impulse"
    if width > impulse * cfg["range_width_k1"]:
        return False, "width_k1"
    if width > cfg["range_width_abs_max_pct"]:
        return False, "width_abs"
    if width < cfg["min_range_width_pct"]:
        return False, "width_min"
    return True, "ok"


def find_range_start(candles_5m, t_high_ts):
    after_high = []
    for i, c in enumerate(candles_5m):
        if c["t"] / 1000.0 >= t_high_ts:
            after_high.append((i, c))

    if len(after_high) < cfg["range_forming_min_candles"]:
        return None

    min_low = min(c["low"] for _, c in after_high)
    min_idx = None
    for i, c in after_high:
        if c["low"] == min_low:
            min_idx = i

    if min_idx is None:
        return None
    return min_idx


def build_active_state(sym, candles_5m):
    """Возвращает state или None. + debug_info."""
    debug = {"sym": sym, "reason": "", "candles": 0}

    g1h = calc_growth_5m(candles_5m)
    if not g1h:
        debug["reason"] = "no_growth"
        return None, debug

    volume = calc_volume_24h_usdt(candles_5m)
    ok, reason = is_fresh(g1h["growth_24h"], volume,
                           drop=g1h.get("drop_24h", 0.0))
    if not ok:
        debug["reason"] = reason
        return None, debug

    start_idx = find_range_start(candles_5m, g1h["t_high_ts"])
    if start_idx is None:
        debug["reason"] = "no_start"
        return None, debug

    subset = list(candles_5m)[start_idx:]
    if len(subset) < cfg["range_forming_min_candles"]:
        debug["reason"] = "subset_too_small"
        return None, debug

    rh = max(c["high"] for c in subset)
    rl = min(c["low"] for c in subset)
    if rl <= 0:
        debug["reason"] = "rl_zero"
        return None, debug

    width = (rh / rl - 1) * 100
    impulse = max(g1h["growth_24h"], g1h.get("drop_24h", 0.0))

    ok, reason = check_range_width(impulse, width)
    if not ok:
        debug["reason"] = reason
        return None, debug

    state = {
        "status": "active",
        "range_high": rh,
        "range_low": rl,
        "started": subset[0]["t"] / 1000.0,
        "candles_in_range": len(subset),
        "growth_24h": g1h["growth_24h"],
        "drop_24h": g1h.get("drop_24h", 0.0),
        "change_24h": g1h.get("change_24h", 0.0),
        "volume_24h": volume,
        "high_24h": g1h["high_24h"],
        "impulse": impulse,
        "width": width,
    }
    debug["reason"] = "ok"
    debug["candles"] = len(subset)
    return state, debug


# ============ ПРОГРЕВ ============
def warmup_symbol(sym):
    data = load_history_rest(sym, "5m", cfg["warmup_hours"] * 12)
    if not data:
        return
    buf = deque(maxlen=cfg["warmup_hours"] * 12)
    for c in data:
        if c["closed"]:
            buf.append({k: c[k] for k in ("high", "low", "close",
                                          "open", "volume",
                                          "quote_volume", "t")})
    buf_5m[sym] = buf


def warmup_all(symbols):
    status["warmup_total"] = len(symbols)
    status["warmup_done"] = 0
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
    status["text"] = "сканирование..."
    worker_log(f"warmup finished: {done[0]}")


def scan_all():
    """Прогон build_active_state для всех монет."""
    global scan_result
    results = []
    for sym, buf in buf_5m.items():
        try:
            state, debug = build_active_state(sym, buf)
            if state:
                state["symbol"] = sym
                results.append(state)
        except Exception as e:
            worker_log(f"scan error {sym}: {e}")

    # сортировка по ширине
    results.sort(key=lambda s: s.get("width", 999))
    scan_result = results
    status["text"] = f"найдено: {len(results)}"


# ============ СОХРАНЕНИЕ CSV ============
def _ensure_calibration_file():
    if not os.path.exists(CALIBRATION_PATH):
        with open(CALIBRATION_PATH, "w", encoding="utf-8",
                  newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow([
                "date", "time", "ticker",
                "our_start_ts", "our_start_time",
                "our_high", "our_low", "our_width",
                "our_candles", "our_impulse",
                "my_start", "my_high", "my_low", "my_width",
                "notes", "not_bit",
            ])


def save_row(state, my_start, my_high, my_low, notes, not_bit):
    _ensure_calibration_file()
    try:
        now = time.time()
        date_str = time.strftime("%Y-%m-%d", time.localtime(now))
        time_str = time.strftime("%H:%M:%S", time.localtime(now))

        our_high = state.get("range_high", 0)
        our_low = state.get("range_low", 0)
        our_width = state.get("width", 0)
        our_started = state.get("started", 0)
        our_start_time = time.strftime("%H:%M", time.localtime(our_started)) \
            if our_started else ""

        # твои данные
        try:
            my_high_f = float(my_high) if my_high else 0
        except Exception:
            my_high_f = 0
        try:
            my_low_f = float(my_low) if my_low else 0
        except Exception:
            my_low_f = 0
        my_width = ((my_high_f / my_low_f - 1) * 100
                    if my_low_f > 0 and my_high_f > 0 else 0)

        with open(CALIBRATION_PATH, "a", encoding="utf-8",
                  newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow([
                date_str, time_str, state.get("symbol", ""),
                int(our_started), our_start_time,
                f"{our_high:.8f}", f"{our_low:.8f}", f"{our_width:.4f}",
                state.get("candles_in_range", 0),
                f"{state.get('impulse', 0):.4f}",
                my_start, my_high, my_low,
                f"{my_width:.4f}" if my_width else "",
                notes, 1 if not_bit else 0,
            ])
        worker_log(f"calibration saved: {state.get('symbol', '')}")
        return True
    except Exception as e:
        worker_log(f"save error: {e}")
        return False


# ============ UI ============
class ReplayWidget(ctk.CTk):
    WIDTH = 1300
    HEIGHT = 750
    HEADER_H = 50
    ROW_H = 32

    COLUMNS = [
        ("ТИКЕР",       80),
        ("НАШ START",   90),
        ("НАШ HIGH",    90),
        ("НАШ LOW",     90),
        ("ШИРИНА",      70),
        ("CANDLES",     70),
        ("ИМПУЛЬС",     70),
        # твои поля
        ("МОЙ START",   100),
        ("МОЙ HIGH",    110),
        ("МОЙ LOW",     110),
        ("КОММЕНТАРИЙ", 220),
        # действия
        ("",            60),
    ]

    def __init__(self):
        super().__init__(fg_color=BG)
        self.title("Squeeze Replay — Калибровка")
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}")

        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - self.WIDTH) // 2
        y = (sh - self.HEIGHT) // 2
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")

        self.row_widgets = []
        self.entries = {}   # symbol -> {start, high, low, notes}
        self.last_draw = 0
        self._rendered = False   # v1: рендер один раз

        self._build_header()
        self._build_table_header()
        self._build_scroll()
        self._build_footer()

        # запуск прогрева в фоне
        threading.Thread(target=self._init_scan, daemon=True).start()

        self.poll()

    def _build_header(self):
        h = ctk.CTkFrame(self, fg_color=BG_DARK, height=self.HEADER_H,
                         corner_radius=0)
        h.pack(fill="x")
        h.pack_propagate(False)

        title = ctk.CTkLabel(h, text="Squeeze Replay — Калибровка",
                             font=("Segoe UI", 16, "bold"),
                             text_color=PEACH)
        title.pack(side="left", padx=16)

        self.status_label = ctk.CTkLabel(h, text="инициализация...",
                                         font=("Segoe UI", 11),
                                         text_color=SAND)
        self.status_label.pack(side="left", padx=16)

        btn_close = ctk.CTkButton(h, text="✕", width=36, height=30,
                                  font=("Segoe UI", 13, "bold"),
                                  fg_color=ACCENT, hover_color="#8B2E2E",
                                  text_color=BG_DARK,
                                  command=self.destroy)
        btn_close.pack(side="right", padx=8)

        btn_refresh = ctk.CTkButton(h, text="🔄 Обновить", width=120,
                                    height=30,
                                    font=("Segoe UI", 12, "bold"),
                                    fg_color=ACCENT,
                                    hover_color=ACCENT_HOVER,
                                    text_color=BG_DARK,
                                    command=self._rescan)
        btn_refresh.pack(side="right", padx=4)

    def _build_table_header(self):
        h = ctk.CTkFrame(self, fg_color=BG_DARK, height=30, corner_radius=0)
        h.pack(fill="x", padx=8, pady=(4, 0))
        h.pack_propagate(False)

        for name, w in self.COLUMNS:
            color = PEACH if name in ("ТИКЕР",) else (
                GREEN if name.startswith("НАШ") else (
                    GOLD if name.startswith("МОЙ") else SAND))
            lbl = ctk.CTkLabel(h, text=name,
                               font=("Segoe UI", 10, "bold"),
                               text_color=color, width=w, anchor="w")
            lbl.pack(side="left", padx=(4, 0))

    def _build_scroll(self):
        self.scroll = ctk.CTkScrollableFrame(self, fg_color=BG,
                                              corner_radius=0)
        self.scroll.pack(fill="both", expand=True, padx=8, pady=(0, 4))

    def _build_footer(self):
        f = ctk.CTkFrame(self, fg_color=BG_DARK, height=50, corner_radius=0)
        f.pack(fill="x")
        f.pack_propagate(False)

        self.footer_label = ctk.CTkLabel(f, text="",
                                         font=("Segoe UI", 11),
                                         text_color=SAND)
        self.footer_label.pack(side="left", padx=16)

        btn_save = ctk.CTkButton(f, text="💾 Сохранить все",
                                 width=180, height=34,
                                 font=("Segoe UI", 12, "bold"),
                                 fg_color=GREEN, hover_color="#3BB55F",
                                 text_color=BG_DARK,
                                 command=self._save_all)
        btn_save.pack(side="right", padx=8, pady=8)

    # ---------- инициализация ----------
    def _init_scan(self):
        try:
            symbols = load_symbols()
            worker_log(f"symbols loaded: {len(symbols)}")
            warmup_all(symbols)
            status["text"] = "сканирование..."
            scan_all()
            status["text"] = f"найдено: {len(scan_result)}"
        except Exception as e:
            worker_log(f"init error: {e}")
            status["text"] = f"ошибка: {e}"

    def _rescan(self):
        def _run():
            status["text"] = "сканирование..."
            scan_all()
            status["text"] = f"найдено: {len(scan_result)}"
            self._rendered = False   # v1: перерисуем
        threading.Thread(target=_run, daemon=True).start()

    # ---------- сохранение ----------
    def _save_all(self):
        """v2: сохраняем ВСЕ заполненные строки. Ошибка одной не рвёт цикл."""
        saved = 0
        errors = 0
        for sym, fields in list(self.entries.items()):
            try:
                my_high = fields["high"].get().strip()
                my_low = fields["low"].get().strip()
                my_start = fields["start"].get().strip()
                notes = fields["notes"].get().strip()
                not_bit = bool(fields["not_bit"].get())

                # пропускаем пустые
                if not (my_high and my_low):
                    continue

                state = fields["state"]
                ok = save_row(state, my_start, my_high, my_low,
                              notes, not_bit)
                if ok:
                    saved += 1
                    # v2: очищаем ТОЛЬКО после успешной записи
                    fields["high"].delete(0, "end")
                    fields["low"].delete(0, "end")
                    fields["start"].delete(0, "end")
                    fields["notes"].delete(0, "end")
                    try:
                        fields["not_bit"].set(False)
                    except Exception:
                        pass
                else:
                    errors += 1
                    worker_log(f"save_row returned False for {sym}")
            except Exception as e:
                errors += 1
                worker_log(f"save_all loop error {sym}: {e}")
                continue

        self.footer_label.configure(
            text=f"сохранено: {saved} записей · ошибок: {errors}")
        worker_log(f"save_all: saved={saved}, errors={errors}")

    # ---------- рендер ----------
    def _render(self):
        # очистка
        for w in self.row_widgets:
            w.destroy()
        self.row_widgets.clear()
        self.entries.clear()

        if not status["warmup_finished"]:
            lbl = ctk.CTkLabel(self.scroll,
                               text=f"{status['text']}",
                               font=("Segoe UI", 14, "italic"),
                               text_color=PEACH)
            lbl.pack(pady=40)
            self.row_widgets.append(lbl)
            return

        if not scan_result:
            lbl = ctk.CTkLabel(self.scroll,
                               text="Нет активных монет",
                               font=("Segoe UI", 14, "italic"),
                               text_color=GREY)
            lbl.pack(pady=40)
            self.row_widgets.append(lbl)
            return

        for i, state in enumerate(scan_result):
            self._build_row(state, i)

    def _build_row(self, state, idx):
        sym = state.get("symbol", "?")
        row_bg = BG_ROW_ALT if idx % 2 else BG

        row = ctk.CTkFrame(self.scroll, fg_color=row_bg,
                           height=self.ROW_H, corner_radius=4)
        row.pack(fill="x", pady=1)
        row.pack_propagate(False)

        # наши данные
        started = state.get("started", 0)
        our_start = time.strftime("%H:%M", time.localtime(started)) \
            if started else "—"
        our_high = state.get("range_high", 0)
        our_low = state.get("range_low", 0)
        width = state.get("width", 0)
        candles = state.get("candles_in_range", 0)
        impulse = state.get("impulse", 0)

        def add_lbl(text, color, w):
            l = ctk.CTkLabel(row, text=text,
                             font=("Segoe UI", 10),
                             text_color=color, width=w, anchor="w")
            l.pack(side="left", padx=(4, 0))
            return l

        add_lbl(sym.replace("USDT", ""), PEACH, 80)
        add_lbl(our_start, MINT, 90)
        add_lbl(f"{our_high:.6f}", MINT, 90)
        add_lbl(f"{our_low:.6f}", MINT, 90)
        add_lbl(f"{width:.2f}%", PEACH, 70)
        add_lbl(f"{candles}", SAND, 70)
        add_lbl(f"{impulse:.1f}%", SAND, 70)

        # твои поля
        e_start = ctk.CTkEntry(row, width=100, height=24,
                                fg_color=BG_DARK, text_color=GOLD,
                                border_color=ACCENT, border_width=1,
                                font=("Segoe UI", 10))
        e_start.pack(side="left", padx=(4, 0))

        e_high = ctk.CTkEntry(row, width=110, height=24,
                               fg_color=BG_DARK, text_color=GOLD,
                               border_color=ACCENT, border_width=1,
                               font=("Segoe UI", 10))
        e_high.pack(side="left", padx=(4, 0))

        e_low = ctk.CTkEntry(row, width=110, height=24,
                              fg_color=BG_DARK, text_color=GOLD,
                              border_color=ACCENT, border_width=1,
                              font=("Segoe UI", 10))
        e_low.pack(side="left", padx=(4, 0))

        e_notes = ctk.CTkEntry(row, width=220, height=24,
                                fg_color=BG_DARK, text_color=MINT,
                                border_color=ACCENT, border_width=1,
                                font=("Segoe UI", 10),
                                placeholder_text="комментарий...")
        e_notes.pack(side="left", padx=(4, 0))

        not_bit_var = tk.BooleanVar(value=False)
        cb = ctk.CTkCheckBox(row, text="Не БИТ", width=60,
                              font=("Segoe UI", 9),
                              text_color=RED, variable=not_bit_var,
                              fg_color=RED, hover_color="#C43E3E",
                              border_color=RED)
        cb.pack(side="left", padx=(4, 0))

        self.entries[sym] = {
            "start": e_start,
            "high": e_high,
            "low": e_low,
            "notes": e_notes,
            "not_bit": not_bit_var,
            "state": state,
        }
        self.row_widgets.append(row)

    def poll(self):
        # v1: только статус-строка обновляется
        self.status_label.configure(text=status.get("text", ""))

        # рендер — один раз, после прогрева
        if not self._rendered and status["warmup_finished"]:
            self._render()
            self._rendered = True

        # при 0 монет — покажем "Нет активных"
        if self._rendered and not scan_result and not self.entries:
            # уже отрендерили "Нет активных"
            pass

        self.after(500, self.poll)


# ============ MAIN ============
if __name__ == "__main__":
    load_config()
    worker_log("=== replay start ===")
    try:
        app = ReplayWidget()
        app.mainloop()
    except KeyboardInterrupt:
        pass
    worker_log("=== replay stop ===")