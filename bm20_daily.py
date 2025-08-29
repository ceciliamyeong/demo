# ===================== BM20 Daily — Stable, Rebased Index, Editorial News =====================
# 기능 요약:
# - CoinGecko 시세/시총 수집 → 가중치(국내상장 보정 선택가능) → 지수 레벨 산출(기준일 100pt 리베이스)
# - 김치 프리미엄(폴백·캐시), 펀딩비(바이낸스/바이빗 폴백·캐시)
# - 코인별 퍼포먼스(상승=초록/하락=빨강), BTC/ETH 7일 추세 차트
# - 에디토리얼 톤 뉴스(제목+본문, BTC/ETH 현재가 포함)
# - 기간수익률(1D/7D/30D/MTD/YTD) 계산, 인덱스 히스토리 저장
# - HTML + PDF 저장
# 의존: pandas, requests, matplotlib, reportlab, jinja2

import os, time, json, random, subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
import pandas as pd

# ---- Matplotlib ----
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

# ---- ReportLab ----
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image

# ---- HTML ----
from jinja2 import Template

# ================== 공통 설정 ==================
OUT_DIR = Path(os.getenv("OUT_DIR", "out"))
OUT_DIR.mkdir(parents=True, exist_ok=True)
KST = timezone(timedelta(hours=9))
YMD = datetime.now(KST).strftime("%Y-%m-%d")
OUT_DIR_DATE = OUT_DIR / YMD
OUT_DIR_DATE.mkdir(parents=True, exist_ok=True)

# Paths
txt_path  = OUT_DIR_DATE / f"bm20_news_{YMD}.txt"
csv_path  = OUT_DIR_DATE / f"bm20_daily_data_{YMD}.csv"
bar_png   = OUT_DIR_DATE / f"bm20_bar_{YMD}.png"
trend_png = OUT_DIR_DATE / f"bm20_trend_{YMD}.png"
pdf_path  = OUT_DIR_DATE / f"bm20_daily_{YMD}.pdf"
html_path = OUT_DIR_DATE / f"bm20_daily_{YMD}.html"
kp_path   = OUT_DIR_DATE / f"kimchi_{YMD}.json"

# ================== Fonts (Nanum 우선, 실패 시 CID) ==================
NANUM_PATH = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
KOREAN_FONT = "HYSMyeongJo-Medium"
try:
    if os.path.exists(NANUM_PATH):
        pdfmetrics.registerFont(TTFont("NanumGothic", NANUM_PATH))
        KOREAN_FONT = "NanumGothic"
    else:
        pdfmetrics.registerFont(UnicodeCIDFont(KOREAN_FONT))
except Exception:
    pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
    KOREAN_FONT = "HYSMyeongJo-Medium"
try:
    if os.path.exists(NANUM_PATH):
        fm.fontManager.addfont(NANUM_PATH); plt.rcParams["font.family"] = "NanumGothic"
    plt.rcParams["axes.unicode_minus"] = False
except Exception:
    plt.rcParams["axes.unicode_minus"] = False

# ================== Helper ==================
def fmt_pct(v, digits=2):
    try:
        if v is None: return "-"
        return f"{float(v):.{digits}f}%"
    except Exception:
        return "-"

def safe_float(x, d=0.0):
    try: return float(x)
    except: return d

def clamp_list_str(items, n=3):
    items = [str(x) for x in items if str(x)]
    return items[:n]

def write_json(path: Path, obj: dict):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
    except Exception:
        pass

def read_json(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

# ================== Data Layer ==================
CG = "https://api.coingecko.com/api/v3"
BTC_CAP, OTH_CAP = 0.30, 0.15
TOP_UP, TOP_DOWN = 3, 3

# DOGE 포함 (국내 대표성 예외) + 상시 20종 유지 정책 반영
BM20_IDS = [
    "bitcoin","ethereum","solana","ripple","binancecoin","toncoin","avalanche-2",
    "chainlink","cardano","polygon","near","polkadot","cosmos","litecoin",
    "arbitrum","optimism","internet-computer","aptos","filecoin","sui","dogecoin"  # 21번째로 후보 포함 → 상위 20만 사용
]

# 국내 상장 보정(×1.3) — 필요 시 매핑
KRW_LISTED = {
    "bitcoin","ethereum","solana","ripple","binancecoin","toncoin","avalanche-2",
    "chainlink","cardano","polygon","near","polkadot","cosmos","litecoin",
    "arbitrum","optimism","internet-computer","aptos","filecoin","sui","dogecoin"
}
KRW_BONUS = 1.0  # 1.3으로 바꾸면 국내상장 보정 활성화

# ---- CoinGecko with backoff ----
def cg_get(path, params=None, retry=8, timeout=20):
    last = None
    api_key = os.getenv("COINGECKO_API_KEY")
    headers = {"User-Agent": "BM20/1.0"}
    if api_key: headers["x-cg-pro-api-key"] = api_key
    for i in range(retry):
        try:
            r = requests.get(f"{CG}{path}", params=params, timeout=timeout, headers=headers)
            if r.status_code == 429:
                ra = float(r.headers.get("Retry-After", 0)) or (1.5 * (i + 1))
                time.sleep(min(ra, 10) + random.random()); continue
            if 500 <= r.status_code < 600:
                time.sleep(1.2 * (i + 1) + random.random()); continue
            r.raise_for_status(); return r.json()
        except Exception as e:
            last = e; time.sleep(0.8 * (i + 1) + random.random())
    raise last

# 1) markets
mkts = cg_get("/coins/markets", {
  "vs_currency":"usd","ids":",".join(BM20_IDS),
  "order":"market_cap_desc","per_page":len(BM20_IDS),"page":1,
  "price_change_percentage":"24h"
})
df = pd.DataFrame([{
  "id":m["id"], "symbol":m["symbol"].upper(), "name":m.get("name", m["symbol"].upper()),
  "current_price":safe_float(m["current_price"]), "market_cap":safe_float(m["market_cap"]),
  "total_volume":safe_float(m.get("total_volume")),
  "chg24":safe_float(m.get("price_change_percentage_24h"),0.0),
} for m in mkts]).sort_values("market_cap", ascending=False).head(20).reset_index(drop=True)

# 2) 전일 종가: 24h 변동률로 역산
df["previous_price"] = df.apply(
    lambda r: (r["current_price"] / (1 + (r["chg24"] or 0) / 100.0)) if r["current_price"] else None,
    axis=1
)

# 3) 가중치(국내상장 보정 ×1.3 옵션 → 상한 적용 → 정규화)
df["weight_raw"] = df["market_cap"] / max(df["market_cap"].sum(), 1.0)
df["weight_raw"] = df.apply(
    lambda r: r["weight_raw"] * (KRW_BONUS if r["id"] in KRW_LISTED else 1.0),
    axis=1
)
df["weight_ratio"]=df.apply(lambda r: min(r["weight_raw"], BTC_CAP if r["symbol"]=="BTC" else OTH_CAP), axis=1)
df["weight_ratio"]=df["weight_ratio"]/df["weight_ratio"].sum()

# 4) 김치 프리미엄(폴백 + 캐시)
CACHE = OUT_DIR / "cache"; CACHE.mkdir(exist_ok=True)
KP_CACHE = CACHE / "kimchi_last.json"
FD_CACHE = CACHE / "funding_last.json"

def get_kp(df):
    def _req(url, params=None, retry=5, timeout=12):
        last=None
        for i in range(retry):
            try:
                r=requests.get(url, params=params, timeout=timeout, headers={"User-Agent":"BM20/1.0"})
                if r.status_code==429: time.sleep(1.0*(i+1)); continue
                r.raise_for_status(); return r.json()
            except Exception as e:
                last=e; time.sleep(0.6*(i+1))
        raise last
    try:
        u=_req("https://api.upbit.com/v1/ticker", {"markets":"KRW-BTC"})
        btc_krw=float(u[0]["trade_price"]); dom="upbit"
    except Exception:
        try:
            cg=_req(f"{CG}/simple/price", {"ids":"bitcoin","vs_currencies":"krw"})
            btc_krw=float(cg["bitcoin"]["krw"]); dom="cg_krw"
        except Exception:
            last = read_json(KP_CACHE)
            if last: return last.get("kimchi_pct"), last
            return None, {"dom":"fallback0","glb":"df","fx":"fixed1350","btc_krw":None,"btc_usd":None,"usdkrw":1350.0}
    try:
        btc_usd=float(df.loc[df["symbol"]=="BTC","current_price"].iloc[0]); glb="df"
    except Exception:
        btc_usd=None; glb=None
    if btc_usd is None:
        try:
            b=_req("https://api.binance.com/api/v3/ticker/price", {"symbol":"BTCUSDT"})
            btc_usd=float(b["price"]); glb="binance"
        except Exception:
            try:
                cg=_req(f"{CG}/simple/price", {"ids":"bitcoin","vs_currencies":"usd"})
                btc_usd=float(cg["bitcoin"]["usd"]); glb="cg_usd"
            except Exception:
                last = read_json(KP_CACHE)
                if last: return last.get("kimchi_pct"), last
                return None, {"dom":dom,"glb":"fallback0","fx":"fixed1350","btc_krw":round(btc_krw,2),"btc_usd":None,"usdkrw":1350.0}
    try:
        t=_req(f"{CG}/simple/price", {"ids":"tether","vs_currencies":"krw"})
        usdkrw=float(t["tether"]["krw"]); fx="cg_tether"
        if not (900<=usdkrw<=2000): raise ValueError
    except Exception:
        usdkrw=1350.0; fx="fixed1350"
    kp=((btc_krw/usdkrw)-btc_usd)/btc_usd*100
    meta={"dom":dom,"glb":glb,"fx":fx,"btc_krw":round(btc_krw,2),"btc_usd":round(btc_usd,2),"usdkrw":round(usdkrw,2),"kimchi_pct":round(kp,6)}
    write_json(KP_CACHE, meta)
    return kp, meta

kimchi_pct, kp_meta = get_kp(df)
kp_text = fmt_pct(kimchi_pct, 2) if kimchi_pct is not None else "잠정(전일)"

# 5) 펀딩비 — 바이낸스/바이빗 폴백 + 캐시
def _get(url, params=None, timeout=12, retry=5, headers=None):
    if headers is None:
        headers = {"User-Agent":"BM20/1.0","Accept":"application/json"}
    for i in range(retry):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers=headers)
            if r.status_code == 429:
                time.sleep(1.0*(i+1)); continue
            r.raise_for_status()
            return r.json()
        except Exception:
            time.sleep(0.5*(i+1))
    return None

def get_binance_funding(symbol="BTCUSDT"):
    # premiumIndex.lastFundingRate
    domains = ["https://fapi.binance.com", "https://fapi1.binance.com", "https://fapi2.binance.com"]
    for d in domains:
        j = _get(f"{d}/fapi/v1/premiumIndex", {"symbol":symbol})
        if isinstance(j, dict) and j.get("lastFundingRate") is not None:
            try: return float(j["lastFundingRate"])*100.0
            except: pass
        if isinstance(j, list) and j and j[0].get("lastFundingRate") is not None:
            try: return float(j[0]["lastFundingRate"])*100.0
            except: pass
    # history 최신 1개
    for d in domains:
        j = _get(f"{d}/fapi/v1/fundingRate", {"symbol":symbol, "limit":1})
        if isinstance(j, list) and j:
            try: return float(j[0]["fundingRate"])*100.0
            except: pass
    return None

def get_bybit_funding(symbol="BTCUSDT"):
    j = _get("https://api.bybit.com/v5/market/tickers", {"category":"linear","symbol":symbol})
    try:
        lst = j.get("result",{}).get("list",[])
        if lst and lst[0].get("fundingRate") is not None:
            return float(lst[0]["fundingRate"])*100.0
    except: pass
    return None

def fp(v, dash_text="집계 공란"):
    return dash_text if (v is None) else f"{float(v):.4f}%"

btc_f_bin = get_binance_funding("BTCUSDT"); time.sleep(0.2)
eth_f_bin = get_binance_funding("ETHUSDT"); time.sleep(0.2)
btc_f_byb = get_bybit_funding("BTCUSDT");   time.sleep(0.2)
eth_f_byb = get_bybit_funding("ETHUSDT")

# 실패 시 전일 캐시 사용
last_fd = read_json(FD_CACHE) or {}
if btc_f_bin is None: btc_f_bin = last_fd.get("btc_f_bin")
if eth_f_bin is None: eth_f_bin = last_fd.get("eth_f_bin")
if btc_f_byb is None: btc_f_byb = last_fd.get("btc_f_byb")
if eth_f_byb is None: eth_f_byb = last_fd.get("eth_f_byb")
write_json(FD_CACHE, {"btc_f_bin":btc_f_bin, "eth_f_bin":eth_f_bin, "btc_f_byb":btc_f_byb, "eth_f_byb":eth_f_byb})

BIN_TEXT = f"BTC {fp(btc_f_bin)} / ETH {fp(eth_f_bin)}"
BYB_TEXT = (None if (btc_f_byb is None and eth_f_byb is None)
            else f"BTC {fp(btc_f_byb)} / ETH {fp(eth_f_byb)}")

# 6) 지수 산출(리베이스 100pt) + 통계
df["price_change_pct"]=(df["current_price"]/df["previous_price"]-1)*100
df["contribution"]=(df["current_price"]-df["previous_price"])*df["weight_ratio"]

today_value = float((df["current_price"]*df["weight_ratio"]).sum())
prev_value  = float((df["previous_price"]*df["weight_ratio"]).sum())

BASE_DIR = OUT_DIR / "base"; BASE_DIR.mkdir(exist_ok=True)
BASE_FILE = BASE_DIR / "bm20_base.json"
BASE_DATE = "2025-01-01"
if BASE_FILE.exists():
    base_value = read_json(BASE_FILE)["base_value"]
else:
    base_value = today_value
    write_json(BASE_FILE, {"base_date":BASE_DATE, "base_value":base_value})

bm20_now = (today_value / base_value) * 100.0
bm20_chg = (today_value/prev_value - 1) * 100.0 if prev_value else 0.0

num_up  = int((df["price_change_pct"]>0).sum())
num_down= int((df["price_change_pct"]<0).sum())

top_up = df.sort_values("price_change_pct", ascending=False).head(TOP_UP).reset_index(drop=True)
top_dn = df.sort_values("price_change_pct", ascending=True).head(TOP_DOWN).reset_index(drop=True)

# 7) 인덱스 히스토리 저장 + 기간 수익률
HIST_DIR = OUT_DIR / "history"; HIST_DIR.mkdir(parents=True, exist_ok=True)
HIST_CSV = HIST_DIR / "bm20_index_history.csv"

today_row = {"date": YMD, "index": round(float(bm20_now), 6)}
if HIST_CSV.exists():
    hist = pd.read_csv(HIST_CSV, dtype={"date":str})
    hist = hist[hist["date"] != YMD]
    hist = pd.concat([hist, pd.DataFrame([today_row])], ignore_index=True)
else:
    hist = pd.DataFrame([today_row])
hist = hist.sort_values("date").reset_index(drop=True)
hist.to_csv(HIST_CSV, index=False, encoding="utf-8")

def period_return(days: int):
    if len(hist) < 2: return None
    try:
        ref_date = (datetime.strptime(YMD, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
        ref_series = hist[hist["date"] <= ref_date]
        if ref_series.empty: return None
        ref_idx = float(ref_series.iloc[-1]["index"])
        cur_idx = float(hist.iloc[-1]["index"])
        if ref_idx == 0: return None
        return (cur_idx / ref_idx - 1.0) * 100.0
    except Exception:
        return None

def pct_fmt(v, digits=2): return "-" if v is None else f"{v:+.{digits}f}%"

RET_1D  = period_return(1)
RET_7D  = period_return(7)
RET_30D = period_return(30)

today_dt = datetime.strptime(YMD, "%Y-%m-%d")
month_start = today_dt.replace(day=1).strftime("%Y-%m-%d")
year_start  = today_dt.replace(month=1, day=1).strftime("%Y-%m-%d")

def level_on_or_before(yyyymmdd: str):
    s = hist[hist["date"] <= yyyymmdd]
    return None if s.empty else float(s.iloc[-1]["index"])

lvl_month = level_on_or_before(month_start)
lvl_year  = level_on_or_before(year_start)
lvl_now   = float(hist.iloc[-1]["index"])
RET_MTD = None if not lvl_month or lvl_month==0 else (lvl_now/lvl_month - 1)*100
RET_YTD = None if not lvl_year  or lvl_year==0  else (lvl_now/lvl_year  - 1)*100

# 8) 에디토리얼 톤 뉴스
def build_news_editorial():
    def pct(v):  return f"{float(v):+,.2f}%"
    def abs_pct(v): return f"{abs(float(v)):.2f}%"
    def num2(v): s=f"{float(v):,.2f}"; return s.rstrip("0").rstrip(".")

    trend_word = "상승" if bm20_chg>0 else ("하락" if bm20_chg<0 else "보합")
    title = f"BM20 {abs_pct(bm20_chg)} {trend_word}…지수 {num2(bm20_now)}pt, 김치프리미엄 {fmt_pct(kimchi_pct,2)}"

    ups = [f"{r['symbol']}({pct(r['price_change_pct'])})" for _,r in top_up.iterrows()]
    dns = [f"{r['symbol']}({pct(r['price_change_pct'])})" for _,r in top_dn.iterrows()]
    up_line = f"개별 종목으로는 {'·'.join(ups)} 강세를 보였다." if ups else ""
    dn_line = f"{'반면 ' if up_line else ''}{'·'.join(dns)} 하락 폭이 컸다." if dns else ""

    btc = df.loc[df["symbol"]=="BTC"].iloc[0]
    eth = df.loc[df["symbol"]=="ETH"].iloc[0]
    btc_line = f"비트코인(BTC)은 {pct(btc['price_change_pct'])} {'하락' if btc['price_change_pct']<0 else ('상승' if btc['price_change_pct']>0 else '보합')}한 {num2(btc['current_price'])}달러에 거래됐다."
    eth_line = f"이더리움(ETH)은 {pct(eth['price_change_pct'])} {'하락' if eth['price_change_pct']<0 else ('상승' if eth['price_change_pct']>0 else '보합')}한 {num2(eth['current_price'])}달러선."

    breadth_word = "강세 우위" if num_up>num_down else ("약세 우위" if num_down>num_up else "중립")
    breadth = f"시장 폭은 상승 {num_up}·하락 {num_down}로 {breadth_word}다."

    kp_side = "국내 거래소가 해외 대비 소폭 할인되어" if (kimchi_pct is not None and kimchi_pct<0) else "국내 거래소가 소폭 할증되어"
    kp_line = f"국내외 가격 차이를 나타내는 김치 프리미엄은 {fmt_pct(kimchi_pct,2)}로, {kp_side} 거래됐다."
    fund_line = f"바이낸스 기준 펀딩비는 {BIN_TEXT}" + ("" if BYB_TEXT is None else f", 바이빗은 {BYB_TEXT}") + "로 집계됐다."

    body = " ".join([
        f"BM20 지수가 {YMD} 전일 대비 {pct(bm20_chg)} {trend_word}해 {num2(bm20_now)}포인트를 기록했다.",
        breadth,
        dn_line if num_down>=num_up else up_line,
        up_line if num_down>=num_up else dn_line,
        btc_line, eth_line,
        kp_line, fund_line
    ])
    return title, body

news_title, news_body = build_news_editorial()
news = f"{news_title}\n{news_body}"

# 9) 저장 (TXT/CSV/JSON)
with open(txt_path,"w",encoding="utf-8") as f: f.write(news)
df_out=df[["symbol","name","current_price","previous_price","price_change_pct","market_cap","total_volume","weight_ratio","contribution"]]
df_out.to_csv(csv_path, index=False, encoding="utf-8")
write_json(kp_path, {"date":YMD, **(kp_meta or {}), "kimchi_pct": (None if kimchi_pct is None else round(float(kimchi_pct),4))})

# ================== Charts ==================
# A) 코인별 퍼포먼스 (상승=초록, 하락=빨강)
perf = df.sort_values("price_change_pct", ascending=False)[["symbol","price_change_pct"]].reset_index(drop=True)
plt.figure(figsize=(10.6, 4.6))
x = range(len(perf)); y = perf["price_change_pct"].values
colors_v = ["#2E7D32" if v >= 0 else "#C62828" for v in y]  # 진초록/진빨강
plt.bar(x, y, color=colors_v, width=0.82, edgecolor="#263238", linewidth=0.2)
plt.xticks(x, perf["symbol"], rotation=0, fontsize=10)
plt.axhline(0, linewidth=1, color="#90A4AE")
for i, v in enumerate(y):
    off = (max(y)*0.03 if v>=0 else -abs(min(y))*0.03) or (0.25 if v>=0 else -0.25)
    va  = "bottom" if v>=0 else "top"
    plt.text(i, v + off, f"{v:+.2f}%", ha="center", va=va, fontsize=10, fontweight="600")
plt.title("코인별 퍼포먼스 (1D, USD)", fontsize=13, loc="left", pad=10)
plt.ylabel("%"); plt.tight_layout(); plt.savefig(bar_png, dpi=180); plt.close()

# B) BTC/ETH 7일 추세
def get_pct_series(coin_id, days=8):
    data=cg_get(f"/coins/{coin_id}/market_chart", {"vs_currency":"usd","days":days})
    prices=data.get("prices",[])
    if not prices: return []
    s=[p[1] for p in prices]; base=s[0]
    return [ (v/base-1)*100 for v in s ]
btc7=get_pct_series("bitcoin", 8); time.sleep(0.5)
eth7=get_pct_series("ethereum", 8)
plt.figure(figsize=(10.6, 3.8))
plt.plot(range(len(btc7)), btc7, label="BTC")
plt.plot(range(len(eth7)), eth7, label="ETH")
plt.legend(loc="upper left"); plt.title("BTC & ETH 7일 가격 추세", fontsize=13, loc="left", pad=8)
plt.ylabel("% (from start)"); plt.tight_layout(); plt.savefig(trend_png, dpi=180); plt.close()

# ================== PDF (Clean Card Layout) ==================
styles = getSampleStyleSheet()
title_style    = ParagraphStyle("Title",    fontName=KOREAN_FONT, fontSize=18, alignment=1, spaceAfter=6)
subtitle_style = ParagraphStyle("Subtitle", fontName=KOREAN_FONT, fontSize=12.5, alignment=1,
                                textColor=colors.HexColor("#546E7A"), spaceAfter=12)
section_h      = ParagraphStyle("SectionH", fontName=KOREAN_FONT, fontSize=13,  alignment=0,
                                textColor=colors.HexColor("#1A237E"), spaceBefore=4, spaceAfter=8)
body_style     = ParagraphStyle("Body",     fontName=KOREAN_FONT, fontSize=11,  alignment=0, leading=16)
small_style    = ParagraphStyle("Small",    fontName=KOREAN_FONT, fontSize=9,   alignment=1, textColor=colors.HexColor("#78909C"))

def card(flowables, pad=10, bg="#FFFFFF", border="#E5E9F0"):
    tbl = Table([[flowables]], colWidths=[16.4*cm])
    tbl.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,-1), KOREAN_FONT),
        ("LEFTPADDING",(0,0),(-1,-1), pad), ("RIGHTPADDING",(0,0),(-1,-1), pad),
        ("TOPPADDING",(0,0),(-1,-1), pad),  ("BOTTOMPADDING",(0,0),(-1,-1), pad),
        ("BACKGROUND",(0,0),(-1,-1), colors.HexColor(bg)),
        ("BOX",(0,0),(-1,-1),0.75, colors.HexColor(border)),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
    ]))
    return tbl

def style_table_basic(t, header_bg="#EEF4FF", box="#CFD8DC", grid="#E5E9F0", fs=10.5):
    t.setStyle(TableStyle([
        ("FONTNAME",(0,0),(-1,-1), KOREAN_FONT),
        ("FONTSIZE",(0,0),(-1,-1), fs),
        ("BACKGROUND",(0,0),(-1,0), colors.HexColor(header_bg)),
        ("BOX",(0,0),(-1,-1),0.5, colors.HexColor(box)),
        ("INNERGRID",(0,0),(-1,-1),0.25, colors.HexColor(grid)),
        ("ALIGN",(0,0),(-1,-1),"LEFT"),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ]))

doc = SimpleDocTemplate(str(pdf_path), pagesize=A4,
                        leftMargin=1.8*cm, rightMargin=1.8*cm,
                        topMargin=1.6*cm, bottomMargin=1.6*cm)

story = []
story += [Paragraph("BM20 데일리 리포트", title_style),
          Paragraph(f"{YMD}", subtitle_style)]

metrics = [
    ["지수",        f"{bm20_now:,.2f} pt"],
    ["일간 변동",   f"{bm20_chg:+.2f}%"],
    ["상승/하락",   f"{num_up} / {num_down}"],
    ["수익률(1D/7D/30D/MTD/YTD)", f"{pct_fmt(RET_1D)} / {pct_fmt(RET_7D)} / {pct_fmt(RET_30D)} / {pct_fmt(RET_MTD)} / {pct_fmt(RET_YTD)}"],
    ["김치 프리미엄", kp_text],
    ["펀딩비(Binance)", BIN_TEXT],
]
if BYB_TEXT:
    metrics.append(["펀딩비(Bybit)", BYB_TEXT])
mt = Table(metrics, colWidths=[5.0*cm, 11.0*cm]); style_table_basic(mt)
story += [card([mt]), Spacer(1, 0.45*cm)]

perf_block = [Paragraph("코인별 퍼포먼스 (1D, USD)", section_h)]
if bar_png.exists(): perf_block += [Image(str(bar_png), width=16.0*cm, height=6.6*cm)]
story += [card(perf_block), Spacer(1, 0.45*cm)]

tbl_up = [["상승 TOP3","등락률"], *[[r["symbol"], f"{r['price_change_pct']:+.2f}%"] for _,r in top_up.iterrows()]]
tbl_dn = [["하락 TOP3","등락률"], *[[r["symbol"], f"{r['price_change_pct']:+.2f}%"] for _,r in top_dn.iterrows()]]
t_up = Table(tbl_up, colWidths=[8.0*cm, 3.5*cm]); t_dn = Table(tbl_dn, colWidths=[8.0*cm, 3.5*cm])
style_table_basic(t_up); style_table_basic(t_dn)
story += [card([Paragraph("상승/하락 TOP3", section_h), Spacer(1,4), t_up, Spacer(1,6), t_dn]),
          Spacer(1, 0.45*cm)]

trend_block = [Paragraph("BTC & ETH 7일 가격 추세", section_h)]
if trend_png.exists(): trend_block += [Image(str(trend_png), width=16.0*cm, height=5.2*cm)]
story += [card(trend_block), Spacer(1, 0.45*cm)]

story += [card([Paragraph("BM20 데일리 뉴스", section_h), Spacer(1,2), Paragraph(news.replace("\n","<br/>"), body_style)]),
          Spacer(1, 0.45*cm)]
story += [Paragraph("© Blockmedia · Data: CoinGecko, Upbit · Funding: Binance & Bybit",
                    small_style)]
doc.build(story)

# ================== HTML ==================
html_tpl = Template(r"""
<!doctype html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BM20 데일리 {{ ymd }}</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"NanumGothic","Noto Sans CJK","Malgun Gothic",Arial,sans-serif;background:#fafbfc;color:#111;margin:0}
.wrap{max-width:760px;margin:0 auto;padding:20px}
.card{background:#fff;border:1px solid #e5e9f0;border-radius:12px;padding:20px;margin-bottom:16px}
h1{font-size:22px;margin:0 0 8px 0;text-align:center} h2{font-size:15px;margin:16px 0 8px 0;color:#1A237E}
.muted{color:#555;text-align:center} .center{text-align:center}
table{width:100%;border-collapse:collapse;font-size:14px} th,td{border:1px solid #e5e9f0;padding:8px} th{background:#eef4ff}
.footer{font-size:12px;color:#666;text-align:center;margin-top:16px}
img{max-width:100%}
</style></head><body>
<div class="wrap">
  <div class="card">
    <h1>BM20 데일리 리포트</h1>
    <div class="muted">{{ ymd }}</div>
    <table style="margin-top:10px">
      <tr><th>지수</th><td>{{ bm20_now }} pt</td></tr>
      <tr><th>일간 변동</th><td>{{ bm20_chg }}</td></tr>
      <tr><th>상승/하락</th><td>{{ num_up }} / {{ num_down }}</td></tr>
      <tr><th>수익률(1D/7D/30D/MTD/YTD)</th><td>{{ ret_1d }} / {{ ret_7d }} / {{ ret_30d }} / {{ ret_mtd }} / {{ ret_ytd }}</td></tr>
      <tr><th>김치 프리미엄</th><td>{{ kp_text }}</td></tr>
      <tr><th>펀딩비(Binance)</th><td>{{ bin_text }}</td></tr>
      {% if byb_text %}<tr><th>펀딩비(Bybit)</th><td>{{ byb_text }}</td></tr>{% endif %}
    </table>
  </div>
  <div class="card">
    <h2>코인별 퍼포먼스 (1D, USD)</h2>
    {% if bar_png %}<p class="center"><img src="{{ bar_png }}" alt="Performance"></p>{% endif %}
    <h2>상승/하락 TOP3</h2>
    <table><tr><th>상승</th><th style="text-align:right">등락률</th></tr>
      {% for r in top_up %}<tr><td>{{ r.sym }}</td><td style="text-align:right">{{ r.pct }}</td></tr>{% endfor %}
    </table><br>
    <table><tr><th>하락</th><th style="text-align:right">등락률</th></tr>
      {% for r in top_dn %}<tr><td>{{ r.sym }}</td><td style="text-align:right">{{ r.pct }}</td></tr>{% endfor %}
    </table>
  </div>
  <div class="card">
    <h2>BTC & ETH 7일 가격 추세</h2>
    {% if trend_png %}<p class="center"><img src="{{ trend_png }}" alt="Trend"></p>{% endif %}
  </div>
  <div class="card"><h2>BM20 데일리 뉴스</h2><p>{{ news_html }}</p></div>
  <div class="footer">© Blockmedia · Data: CoinGecko, Upbit · Funding: Binance & Bybit</div>
</div></body></html>
""")
html = html_tpl.render(
    ymd=YMD, bm20_now=f"{bm20_now:,.2f}", bm20_chg=f"{bm20_chg:+.2f}%",
    num_up=num_up, num_down=num_down,
    ret_1d=pct_fmt(RET_1D), ret_7d=pct_fmt(RET_7D), ret_30d=pct_fmt(RET_30D),
    ret_mtd=pct_fmt(RET_MTD), ret_ytd=pct_fmt(RET_YTD),
    kp_text=kp_text, bin_text=BIN_TEXT, byb_text=BYB_TEXT,
    top_up=[{"sym":r["symbol"], "pct": f"{r['price_change_pct']:+.2f}%"} for _,r in top_up.iterrows()],
    top_dn=[{"sym":r["symbol"], "pct": f"{r['price_change_pct']:+.2f}%"} for _,r in top_dn.iterrows()],
    bar_png=os.path.basename(bar_png), trend_png=os.path.basename(trend_png),
    news_html=news.replace("\n","<br/>")
)
with open(html_path, "w", encoding="utf-8") as f: f.write(html)

print("Saved:", txt_path, csv_path, bar_png, trend_png, pdf_path, html_path, kp_path)
