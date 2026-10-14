"""
_history_chart_worker.py — график отработанного БИТ
Запуск: python _history_chart_worker.py SYMBOL fired_ts range_high range_low exit_price "detected_at"
"""
import sys
import json
import os
import time
import urllib.request
from urllib.parse import quote

import pandas as pd
from lightweight_charts import Chart


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REST_KLINES = "https://fapi.binance.com/fapi/v1/klines"


def load_klines(sym, interval, limit):
    url = f"{REST_KLINES}?symbol={quote(sym)}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    rows = []
    for row in data:
        rows.append({
            "time": pd.to_datetime(int(row[0]), unit="ms"),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        })
    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=["time"]).sort_values("time")
    df = df.set_index("time")
    return df


def main():
    if len(sys.argv) < 7:
        print("Usage: python _history_chart_worker.py SYMBOL fired_ts range_high range_low exit_price \"detected_at\"")
        return

    sym = sys.argv[1]
    try:
        fired_ts = float(sys.argv[2])
        range_high = float(sys.argv[3])
        range_low = float(sys.argv[4])
        exit_price = float(sys.argv[5])
    except ValueError as e:
        print(f"arg parse error: {e}")
        return
    detected_at = sys.argv[6]   # "2026-10-14 12:34:56"

    # парсим detected_at → started_ts
    started_ts = 0
    try:
        started_ts = time.mktime(
            time.strptime(detected_at, "%Y-%m-%d %H:%M:%S"))
    except Exception as e:
        print(f"detected_at parse error: {e}")

    # загружаем свечи: 400 × 5m (limit 1500 хватит)
    try:
        df = load_klines(sym, "5m", 1500)
    except Exception as e:
        print(f"klines error: {e}")
        return

    if df is None or df.empty:
        print("no data")
        return

    # фильтруем по времени: fired_ts ± 200 свечей (200 × 5 мин = 1000 мин = 60000 сек)
    window = 200 * 300   # 200 свечей * 300 сек
    t0 = fired_ts - window
    t1 = fired_ts + window
    df["ts"] = df.index.astype("int64") // 10**9
    df = df[(df["ts"] >= t0) & (df["ts"] <= t1)]
    df = df.drop(columns=["ts"])

    if df.empty:
        print("no data after filter")
        return

    # график
    try:
        chart = Chart(width=1100, height=650,
                      title=f"{sym} · БИТ · 5m")
        chart.layout(background_color="#116466", text_color="#D1E8E2")
        chart.candle_style(
            up_color="#4ADE80", down_color="#EF4444",
            border_up_color="#4ADE80", border_down_color="#EF4444",
            wick_up_color="#4ADE80", wick_down_color="#EF4444",
        )
        chart.set(df)

        # горизонтальные линии диапазона
        if range_high > 0:
            try:
                chart.horizontal_line(range_high, color="#FFCB9A",
                                      width=2, style="dashed", text="high")
            except Exception:
                pass
        if range_low > 0:
            try:
                chart.horizontal_line(range_low, color="#FFCB9A",
                                      width=2, style="dashed", text="low")
            except Exception:
                pass

        # вертикальная жёлтая — начало диапазона
        if started_ts > 0:
            try:
                chart.vertical_line(
                    pd.to_datetime(started_ts, unit="s"),
                    color="#FFD166", width=2, style="dashed",
                    text="start",
                )
            except Exception:
                pass

        # вертикальная зелёная/красная — выход из диапазона
        if fired_ts > 0:
            try:
                # вверх или вниз?
                if exit_price >= range_high:
                    fired_color = "#4ADE80"   # зелёный
                    fired_text = "fired ↑"
                else:
                    fired_color = "#EF4444"   # красный
                    fired_text = "fired ↓"
                chart.vertical_line(
                    pd.to_datetime(fired_ts, unit="s"),
                    color=fired_color, width=3, style="solid",
                    text=fired_text,
                )
            except Exception:
                pass

        # topbar info
        try:
            if range_high > 0 and range_low > 0:
                w = (range_high / range_low - 1) * 100
                chart.topbar.textbox(
                    "info",
                    f"Range: {range_low:.6f} — {range_high:.6f} ({w:.2f}%) | "
                    f"Exit: {exit_price:.6f}"
                )
        except Exception:
            pass

        chart.show(block=True)
    except Exception as e:
        print(f"chart error: {e}")


if __name__ == "__main__":
    main()