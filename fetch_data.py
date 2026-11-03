import bisect
import json
import os
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
except Exception as e:
    print("Khong dang nhap duoc:", e)

# Danh sách dự phòng, chỉ dùng khi không lấy được danh sách đầy đủ
MA_DU_PHONG = ["NTP", "PXL", "TV1", "DHA", "PCH", "VNM", "VCB",
               "CTF", "VC3", "VJC", "HPG", "FPT", "SSI", "VND", "MWG"]

# Điều kiện lọc bước 1: chỉ giữ mã đạt cả hai
LOC_KL = 100000   # KL trung bình 20 phiên phải lớn hơn (cổ phiếu)
LOC_GIA = 10.0    # giá phải lớn hơn (nghìn đồng: 10.0 = 10.000đ)

# Giới hạn tốc độ: tổng số lượt gọi tối đa mỗi phút (tất cả luồng cộng lại)
RATE_PER_MIN = int(os.environ.get("RATE_PER_MIN", "55"))
SO_LUONG = 6  # số luồng chạy song song
KHOANG = 60.0 / RATE_PER_MIN
_khoa = threading.Lock()
_ke_tiep = [0.0]

# Lịch các phiên giao dịch thật (lấy từ mã VCB), dùng để đếm số phiên
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


def lay_lich():
    """Lấy danh sách ngày giao dịch thật từ VCB (mã thanh khoản cao)."""
    for lan in range(3):
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
        except Exception as e:
            print("Khong lay duoc lich:", e)
            time.sleep(5)
    print("Khong co lich, se dem theo so nen")
    return []


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

    ngay_ts = pd.to_datetime(df["time"].iloc[i]).normalize()
    if LICH:
        # Đếm số phiên giao dịch THẬT từ ngày báo đến phiên mới nhất
        phien = len(LICH) - bisect.bisect_right(LICH, ngay_ts)
    else:
        phien = n - 1 - i  # dự phòng: đếm theo số nến

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

    # Khối lượng trung bình 20 phiên gần nhất (đơn vị: cổ phiếu)
    kl20 = 0
    if "volume" in df.columns:
        kl20 = float(df["volume"].tail(20).mean())

    return {"ma": ma,
            "nay": round(float(c.iloc[-1]), 2),
            "kl20": int(kl20),
            "st": tin_hieu(up_st, df),
            "ema": tin_hieu(up_ema, df)}


def xu_ly(ma):
    """Phân tích 1 mã, thử lại 1 lần nếu lỗi."""
    for lan in range(2):
        cho_luot()
        try:
            return phan_tich(ma)
        except Exception as e:
            loi = str(e).lower()
            print("LOI", ma, e)
            if "limit" in loi or "429" in loi or "rate" in loi:
                time.sleep(20)  # bị giới hạn, nghỉ rồi thử lại
            else:
                return None
    return None


def dat(r):
    """Mã có đạt điều kiện lọc bước 1 không."""
    return r.get("kl20", 0) > LOC_KL and r["nay"] > LOC_GIA


# ---- Đọc dữ liệu lần chạy trước ----
CU = {}
try:
    with open("data.json", encoding="utf-8") as f:
        for r in json.load(f):
            CU[r["ma"]] = r
except Exception as e:
    print("Chua co data.json cu:", e)

BQ = set()
CO_BQ = os.path.exists("bo_qua.json")
if CO_BQ:
    try:
        with open("bo_qua.json", encoding="utf-8") as f:
            BQ = set(json.load(f))
    except Exception as e:
        print("Khong doc duoc bo_qua.json:", e)
        CO_BQ = False

# Tải đầy đủ: thứ Hai, hoặc chưa có bo_qua.json, hoặc đặt FULL=1
TAI_DAY_DU = (os.environ.get("FULL") == "1"
              or date.today().weekday() == 0
              or not CO_BQ)

LICH = lay_lich()
MA_LIST = lay_danh_sach()

if TAI_DAY_DU:
    MA_TAI = list(MA_LIST)
    bq_moi = set()          # xây lại danh sách loại từ đầu
else:
    MA_TAI = [m for m in MA_LIST if m not in BQ]
    bq_moi = set(m for m in BQ if m in set(MA_LIST))

print("Tai", len(MA_TAI), "/", len(MA_LIST), "ma",
      "(tai day du)" if TAI_DAY_DU else "(bo qua " + str(len(BQ)) + " ma da loai)")

ket_qua = {}
bat_dau = time.time()

with ThreadPoolExecutor(max_workers=SO_LUONG) as ex:
    futures = {ex.submit(xu_ly, ma): ma for ma in MA_TAI}
    for stt, f in enumerate(as_completed(futures), 1):
        ma = futures[f]
        r = f.result()
        if r:
            if dat(r):
                ket_qua[ma] = r
            else:
                bq_moi.add(ma)
        elif ma in CU and dat(CU[ma]):
            ket_qua[ma] = CU[ma]  # lỗi tải: giữ tạm dữ liệu cũ
        if stt % 100 == 0:
            phut = (time.time() - bat_dau) / 60
            print("Da xu ly", stt, "/", len(MA_TAI), "ma,",
                  len(ket_qua), "ma dat,", round(phut, 1), "phut")

ds_ket_qua = sorted(ket_qua.values(), key=lambda x: x["ma"])

if ds_ket_qua:
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(ds_ket_qua, f, ensure_ascii=False)
    with open("bo_qua.json", "w", encoding="utf-8") as f:
        json.dump(sorted(bq_moi), f)

print("Xong:", len(ds_ket_qua), "ma dat dieu kien,", len(bq_moi), "ma bi loai")
