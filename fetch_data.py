import json
import time
from datetime import date, timedelta

import pandas as pd
from vnstock import Vnstock

# Danh sách mã muốn theo dõi (sửa tùy ý)
MA_LIST = ["NTP", "PXL", "TV1", "DHA", "PCH", "VNM", "VCB",
           "CTF", "VC3", "VJC", "HPG", "FPT", "SSI", "VND", "MWG"]


def phan_tich(ma):
    start = (date.today() - timedelta(days=300)).strftime("%Y-%m-%d")
    end = date.today().strftime("%Y-%m-%d")
    df = Vnstock().stock(symbol=ma, source="VCI").quote.history(
        start=start, end=end, interval="1D")
    df = df.reset_index(drop=True)
    if len(df) < 30:
        return None

    c = df["close"]
    e9 = c.ewm(span=9, adjust=False).mean()
    e21 = c.ewm(span=21, adjust=False).mean()
    up = e9 > e21

    n = len(df)
    i = n - 1
    while i > 0 and up.iloc[i] == up.iloc[i - 1]:
        i -= 1  # lùi về phiên đầu tiên của trạng thái hiện tại

    phien = n - 1 - i
    ngay = pd.to_datetime(df["time"].iloc[i]).strftime("%d/%m/%Y")
    bao = float(c.iloc[i])
    nay = float(c.iloc[-1])

    if up.iloc[-1]:
        tin = "MUA" if phien == 0 else "GIU"
    else:
        tin = "BAN" if phien == 0 else "OUT"

    if tin == "OUT":
        ngay, bao, phien = "-", 0, 0

    return {"ma": ma, "tin": tin, "ngay": ngay, "phien": int(phien),
            "bao": round(bao, 2), "nay": round(nay, 2)}


ket_qua = []
for ma in MA_LIST:
    try:
        r = phan_tich(ma)
        if r:
            ket_qua.append(r)
            print("OK", ma, r["tin"])
    except Exception as e:
        print("LOI", ma, e)
    time.sleep(1.5)  # tránh bị giới hạn 60 request/phút

thu_tu = {"MUA": 0, "BAN": 1, "GIU": 2, "OUT": 3}
ket_qua.sort(key=lambda d: (thu_tu[d["tin"]], d["phien"]))

with open("data.json", "w", encoding="utf-8") as f:
    json.dump(ket_qua, f, ensure_ascii=False, indent=1)

print("Xong:", len(ket_qua), "mã")
