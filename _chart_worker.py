"""
_chart_worker.py — отдельный процесс для отображения графика
Запуск: python _chart_worker.py SYMBOL
"""
import sys
import json
import os
from collections import deque
import urllib.request
from urllib.parse import quote
import time

import pandas as pd
from lightweight_charts import Chart


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REST_KLINES = "https://fapi.binance.com/fapi/v1/klines"


def load_klines(sym, interval, limit):
    """REST запрос свечей с Binance."""
    url = f"{REST_KLINES}?symbol={quote(sym)}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
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
    if len(sys.argv) < 2:
        print("Usage: python _chart_worker.py SYMBOL")
        return

    sym = sys.argv[1]
    interval = sys.argv[2] if len(sys.argv) > 2 else "5m"
    limit = 200 if interval == "5m" else 26

    # читаем state из кэша (границы диапазона)
    range_high = 0
    range_low = 0
    started = 0
    cache_path = os.path.join(BASE_DIR, "squeeze_cache.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
            st = cache.get("squeeze_state", {}).get(sym, {})
            range_high = st.get("range_high", 0)
            range_low = st.get("range_low", 0)
            started = st.get("started", 0)
        except Exception as e:
            print(f"cache read error: {e}")

    # данные
    try:
        df = load_klines(sym, interval, limit)
    except Exception as e:
        print(f"klines error: {e}")
        return

    if df.empty:
        print("no data")
        return

    # график
    try:
        chart = Chart(
            width=1000, height=600,
            title=f"{sym} · {interval}",
        )
        chart.layout(background_color="#116466", text_color="#D1E8E2")
        chart.candle_style(
            up_color="#4ADE80", down_color="#EF4444",
            border_up_color="#4ADE80", border_down_color="#EF4444",
            wick_up_color="#4ADE80", wick_down_color="#EF4444",
        )
        chart.set(df)

        # горизонтальные линии
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

        # topbar
        try:
            if range_high > 0 and range_low > 0:
                w = (range_high / range_low - 1) * 100
                chart.topbar.textbox(
                    "range",
                    f"Range: {range_low:.6f} — {range_high:.6f} ({w:.2f}%)"
                )
        except Exception:
            pass

        chart.show(block=True)
    except Exception as e:
        print(f"chart error: {e}")


if __name__ == "__main__":
    main()