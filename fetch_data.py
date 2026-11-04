import bisect
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import numpy as np
import pandas as pd
from vnstock import Vnstock

# Đăng nhập bằng khóa API (lấy từ GitHub Secret)
try:
    import vnstock
    if os.environ.get("VNSTOCK_API_KEY"):
        vnstock.login(os.environ["VNSTOCK_API_KEY"])
        print("Da dang nhap vnstock")
    else:
        print("CANH BAO: khong co VNSTOCK_API_KEY, se bi gioi han thap")
except BaseException as e:
    print("Khong dang nhap duoc:", e)

# Hạn mức vnstock bản Cộng đồng: 60 lượt/phút. Đặt thấp hơn để an toàn.
RATE_PER_MIN = int(os.environ.get("RATE_PER_MIN", "45"))
SO_LUONG = 6
KHOANG = 60.0 / RATE_PER_MIN
_khoa = threading.Lock()
_ke_tiep = [0.0]

# Dừng êm sau số phút này (phải nhỏ hơn timeout-minutes trong update.yml)
GIOI_HAN_PHUT = float(os.environ.get("GIOI_HAN_PHUT", "20"))
HET_GIO = time.time() + GIOI_HAN_PHUT * 60

LICH = []


def cho_luot():
    """Xếp hàng xin lượt gọi API, đảm bảo không vượt RATE_PER_MIN."""
    with _khoa:
        bay_gio = time.time()
        t = max(bay_gio, _ke_tiep[0])
        _ke_tiep[0] = t + KHOANG
    cho = t - bay_gio
    if cho > 0:
        time.sleep(cho)


def nghi_chung(giay):
    """Bắt TẤT CẢ các luồng cùng nghỉ (khi chạm hạn mức)."""
    with _khoa:
        _ke_tiep[0] = max(_ke_tiep[0], time.time() + giay)


def doc_json(duong_dan, mac_dinh):
    try:
        with open(duong_dan, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return mac_dinh


def doc_danh_sach():
    """Đọc danh sách mã từ danh_sach.txt (cách nhau bằng dấu phẩy/khoảng trắng/xuống dòng)."""
    try:
        with open("danh_sach.txt", encoding="utf-8") as f:
            noi_dung = f.read()
    except Exception as e:
        print("Khong doc duoc danh_sach.txt:", e)
        return []
    ds = []
    for dong in noi_dung.splitlines():
        dong = dong.split("#")[0]
        for m in re.split(r"[\s,;]+", dong):
            m = m.strip().upper()
            if m and m.isalnum() and m not in ds:
                ds.append(m)
    return ds


def lay_lich():
    """Lấy danh sách ngày giao dịch thật từ VCB."""
    for lan in range(4):
        try:
            cho_luot()
            start = (date.today() - timedelta(days=200)).strftime("%Y-%m-%d")
            end = date.today().strftime("%Y-%m-%d")
            df = Vnstock().stock(symbol="VCB", source="VCI").quote.history(
                start=start, end=end, interval="1D")
            ds = sorted(set(pd.to_datetime(df["time"]).dt.normalize()))
            if len(ds) > 50:
                print("Lich giao dich:", len(ds), "phien, phien cuoi",
                      ds[-1].strftime("%d/%m/%Y"))
                return ds
        except (Exception, SystemExit) as e:
            print("Khong lay duoc lich:", e)
            nghi_chung(65)
    print("Khong co lich, se dem theo so nen")
    return []


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
        i -= 1

    ngay_ts = pd.to_datetime(df["time"].iloc[i]).normalize()
    if LICH:
        phien = len(LICH) - bisect.bisect_right(LICH, ngay_ts)
    else:
        phien = n - 1 - i

    ngay = ngay_ts.strftime("%d/%m/%Y")
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

    kl20 = 0
    if "volume" in df.columns:
        kl20 = float(df["volume"].tail(20).mean())

    h = [[pd.to_datetime(t).strftime("%Y-%m-%d"), round(float(o), 2),
          round(float(hh), 2), round(float(ll), 2), round(float(cc), 2), int(v)]
         for t, o, hh, ll, cc, v in
         df[["time", "open", "high", "low", "close", "volume"]].tail(120).itertuples(index=False)]

    return {"ma": ma,
            "nay": round(float(c.iloc[-1]), 2),
            "kl20": int(kl20),
            "st": tin_hieu(up_st, df),
            "ema": tin_hieu(up_ema, df),
            "h": h}


def xu_ly(ma):
    """Phân tích 1 mã. Trả về: dict | None (quá ít dữ liệu) | "LOI" | "HET"."""
    for lan in range(3):
        if time.time() > HET_GIO:
            return "HET"
        cho_luot()
        if time.time() > HET_GIO:
            return "HET"
        try:
            return phan_tich(ma)
        except SystemExit:
            print("CHAM HAN MUC, nghi 65s (", ma, ")")
            nghi_chung(65)
        except Exception as e:
            loi = str(e).lower()
            print("LOI", ma, str(e)[:120])
            if "limit" in loi or "429" in loi or "rate" in loi:
                nghi_chung(30)
            else:
                return "LOI"
    return "LOI"


def main():
    global LICH
    DS = doc_danh_sach()
    if not DS:
        print("danh_sach.txt trong hoac khong co. Khong thay doi du lieu.")
        return
    print("Danh sach theo doi:", len(DS), "ma")

    CU = {r["ma"]: r for r in doc_json("data.json", [])}
    HIST = doc_json("hist.json", {})
    LICH = lay_lich()

    # Chỉ giữ các mã trong danh sách (mã cũ ngoài danh sách bị bỏ)
    ket_qua = {m: CU[m] for m in DS if m in CU}

    def luu():
        ds = sorted(ket_qua.values(), key=lambda x: x["ma"])
        if ds:
            with open("data.json", "w", encoding="utf-8") as f:
                json.dump(ds, f, ensure_ascii=False)
        with open("hist.json", "w", encoding="utf-8") as f:
            json.dump({m: HIST[m] for m in HIST if m in ket_qua}, f,
                      separators=(",", ":"))

    loi_ds = []
    try:
        with ThreadPoolExecutor(max_workers=SO_LUONG) as ex:
            futures = {ex.submit(xu_ly, ma): ma for ma in DS}
            for stt, f in enumerate(as_completed(futures), 1):
                ma = futures[f]
                try:
                    r = f.result()
                except BaseException as e:
                    print("LOI luong", ma, repr(e))
                    r = "LOI"

                if r == "HET" or r == "LOI":
                    loi_ds.append(ma)         # giữ dữ liệu cũ (nếu có)
                elif r is None:
                    print("Qua it du lieu, bo qua:", ma)
                    ket_qua.pop(ma, None)
                    HIST.pop(ma, None)
                else:
                    h = r.pop("h", None)
                    ket_qua[ma] = r
                    if h:
                        HIST[ma] = h
                if stt % 25 == 0:
                    luu()
                    print("Da xu ly", stt, "/", len(DS), "ma")
    finally:
        luu()

    if loi_ds:
        print("CHUA LAY DUOC", len(loi_ds), "ma:", ", ".join(sorted(loi_ds)))
        print("Kiem tra lai ma co dung khong, hoac chay workflow them 1 lan.")
    print("Xong:", len(ket_qua), "/", len(DS), "ma")


try:
    main()
except BaseException as e:
    print("LOI NGHIEM TRONG:", repr(e))
sys.exit(0)
