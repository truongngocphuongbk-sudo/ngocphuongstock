import json
import os
import time
from datetime import date, timedelta

import numpy as np
import pandas as pd
from vnstock import Vnstock

# Đăng nhập bằng khóa API (lấy từ GitHub Secret)
try:
    import vnstock
    if os.environ.get("VNSTOCK_API_KEY"):
        vnstock.login(os.environ["VNSTOCK_API_KEY"])
except Exception as e:
    print("Khong dang nhap duoc:", e)

# Danh sách dự phòng, chỉ dùng khi không lấy được danh sách đầy đủ
MA_DU_PHONG = ["NTP", "PXL", "TV1", "DHA", "PCH", "VNM", "VCB",
               "CTF", "VC3", "VJC", "HPG", "FPT", "SSI", "VND", "MWG"]


def lay_danh_sach():
    try:
        ls = Vnstock().stock(symbol="VCB", source="VCI").listing
        df = None
        for ten in ("symbols_by_exchange", "all_symbols"):
            try:
                df = getattr(ls, ten)()
                if df is not None and len(df):
                    break
            except Exception as e:
                print("Listing loi", ten, e)
        if df is not None and len(df):
            if "type" in df.columns:
                df = df[df["type"].astype(str).str.upper() == "STOCK"]
            cot = "symbol" if "symbol" in df.columns else df.columns[0]
            ds = [str(s).upper() for s in df[cot].dropna()]
            ds = sorted({s for s in ds if len(s) == 3 and s.isalnum()})
            if len(ds) > 100:
                print("Lay duoc", len(ds), "ma")
                return ds
    except Exception as e:
        print("Khong lay duoc danh sach:", e)
    print("Dung danh sach du phong")
    return MA_DU_PHONG


def supertrend_up(df, period=10, mult=3.0):
    """Trả về mảng True/False: True = xu hướng tăng (Supertrend)."""
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)
    n = len(df)

    tr = np.zeros(n)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    atr = pd.Series(tr).ewm(alpha=1.0 / period, adjust=False).mean().values

    hl2 = (h + l) / 2.0
    ub = hl2 + mult * atr
    lb = hl2 - mult * atr
    fub = ub.copy()
    flb = lb.copy()
    up = np.ones(n, dtype=bool)

    for i in range(1, n):
        fub[i] = ub[i] if (ub[i] < fub[i - 1] or c[i - 1] > fub[i - 1]) else fub[i - 1]
        flb[i] = lb[i] if (lb[i] > flb[i - 1] or c[i - 1] < flb[i - 1]) else flb[i - 1]
        if up[i - 1]:
            up[i] = not (c[i] < flb[i])
        else:
            up[i] = c[i] > fub[i]
    return up


def tin_hieu(up, df):
    """Từ chuỗi xu hướng tăng/giảm, suy ra tín hiệu MUA/GIU/BAN/OUT."""
    c = df["close"]
    n = len(df)
    i = n - 1
    while i > 0 and up[i] == up[i - 1]:
        i -= 1  # lùi về phiên đầu tiên của trạng thái hiện tại

    phien = n - 1 - i
    ngay = pd.to_datetime(df["time"].iloc[i]).strftime("%d/%m/%Y")
    bao = float(c.iloc[i])

    if up[-1]:
        tin = "MUA" if phien == 0 else "GIU"
    else:
        tin = "BAN" if phien == 0 else "OUT"

    if tin == "OUT":
        ngay, bao, phien = "-", 0, 0

    return {"tin": tin, "ngay": ngay, "phien": int(phien), "bao": round(bao, 2)}


def phan_tich(ma):
    start = (date.today() - timedelta(days=200)).strftime("%Y-%m-%d")
    end = date.today().strftime("%Y-%m-%d")
    df = Vnstock().stock(symbol=ma, source="VCI").quote.history(
        start=start, end=end, interval="1D")
    df = df.reset_index(drop=True)
    if len(df) < 40:
        return None

    c = df["close"]
    e9 = c.ewm(span=9, adjust=False).mean()
    e21 = c.ewm(span=21, adjust=False).mean()
    up_ema = (e9 > e21).values
    up_st = supertrend_up(df, 10, 3.0)

    return {"ma": ma,
            "nay": round(float(c.iloc[-1]), 2),
            "st": tin_hieu(up_st, df),
            "ema": tin_hieu(up_ema, df)}


MA_LIST = lay_danh_sach()
ket_qua = []

for stt, ma in enumerate(MA_LIST, 1):
    r = None
    for lan in range(2):
        try:
            r = phan_tich(ma)
            break
        except Exception as e:
            loi = str(e).lower()
            print("LOI", ma, e)
            if "limit" in loi or "429" in loi or "rate" in loi:
                time.sleep(20)  # bị giới hạn, nghỉ rồi thử lại
            else:
                break
    if r:
        ket_qua.append(r)
    if stt % 100 == 0:
        print("Da xu ly", stt, "/", len(MA_LIST), "ma, co", len(ket_qua), "ket qua")
    time.sleep(1.1)  # giữ dưới 60 lượt/phút

if ket_qua:
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(ket_qua, f, ensure_ascii=False)

print("Xong:", len(ket_qua), "ma")
