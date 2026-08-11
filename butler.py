# -*- coding: utf-8 -*-
"""
butler.py — DART Open API 기반 한국 상장사 재무/펀더멘탈 패널 (Blueprint)

app.py 에 두 줄만 추가:
    from butler import butler_bp
    app.register_blueprint(butler_bp)

필요 환경변수 (Render 대시보드에서 설정):
    DART_API_KEY   ← https://opendart.fss.or.kr 무료 발급 (40자리)
키가 없으면 각 엔드포인트는 {"error":"DART_API_KEY 미설정", "need_key":true} 를 돌려주며,
프론트는 이를 받아 "키를 등록하세요" 안내를 띄운다(앱은 죽지 않음).

제공 엔드포인트 (1차):
    GET /api/butler/summary/<code>       요약 (핵심 지표 + 최근 실적 한눈에)
    GET /api/butler/financials/<code>    재무정보 (BS/IS/CF 3개년)
    GET /api/butler/fundamentals/<code>  펀더멘탈 (PER/PBR/ROE/부채비율 등)

<code> 는 6자리 종목코드(005930) 또는 005930.KS/.KQ 형태 모두 허용.
미국 티커 등 국내 종목이 아니면 {"error":"국내 상장 종목이 아닙니다","not_kr":true}.

원칙(준호님 규약 준수):
    - 리버스 엔지니어링 금지: 모든 수치는 DART 원본 계정에서 직접 파생.
    - 상수/가정에 출처·정의 라벨(_SRC, note 필드)을 붙인다.
    - 계산식은 코드에 그대로 노출(부채비율=부채총계/자본총계 등).
    - 데이터 없으면 None을 명시(억지 추정 금지).
"""

import io
import os
import re
import time
import zipfile
import xml.etree.ElementTree as ET

import requests
from flask import Blueprint, jsonify, request

butler_bp = Blueprint("butler", __name__)

_DART = "https://opendart.fss.or.kr/api"
_HEADERS = {"User-Agent": "Mozilla/5.0"}
_TTL = 60 * 60 * 6          # 재무데이터 6시간 캐시(분기마다만 갱신되므로 길게)
_CORP_TTL = 60 * 60 * 24    # 종목코드↔고유번호 매핑 24시간 캐시
_CACHE = {}
_CORP_MAP = None            # {stock_code(6자리): {"corp_code","corp_name"}}
_CORP_MAP_TS = 0

# 보고서 코드
REPRT = {"사업": "11011", "3Q": "11014", "반기": "11012", "1Q": "11013"}

# 펀더멘탈 계산에 쓰는 표준 계정ID (IFRS 태그 우선, 없으면 계정명 폴백)
# _SRC: DART fnlttSinglAcntAll account_id / account_nm
_ACC = {
    "매출액":     {"id": ("ifrs-full_Revenue", "ifrs_Revenue"),
                   "nm": ("매출액", "수익(매출액)", "영업수익")},
    "영업이익":   {"id": ("dart_OperatingIncomeLoss",),
                   "nm": ("영업이익", "영업이익(손실)")},
    "당기순이익": {"id": ("ifrs-full_ProfitLoss",),
                   "nm": ("당기순이익", "당기순이익(손실)", "분기순이익")},
    "자산총계":   {"id": ("ifrs-full_Assets",),
                   "nm": ("자산총계",)},
    "부채총계":   {"id": ("ifrs-full_Liabilities",),
                   "nm": ("부채총계",)},
    "자본총계":   {"id": ("ifrs-full_Equity", "ifrs-full_EquityAttributableToOwnersOfParent"),
                   "nm": ("자본총계", "자본과부채총계")},
    "영업활동현금흐름": {"id": ("ifrs-full_CashFlowsFromUsedInOperatingActivities",),
                   "nm": ("영업활동현금흐름", "영업활동으로인한현금흐름",
                          "영업활동으로 인한 현금흐름")},
    "자사주매입": {"id": ("ifrs-full_PaymentsToAcquireOrRedeemEntitysShares",
                     "dart_PaymentsToAcquireTreasuryShares"),
               "nm": ("자기주식의취득", "자기주식의 취득", "자기주식취득",
                      "자기주식의취득에따른현금유출", "자기주식의 취득에 따른 현금유출액")},
    "배당지급": {"id": ("ifrs-full_DividendsPaidClassifiedAsFinancingActivities",
                    "ifrs-full_DividendsPaid"),
              "nm": ("배당금지급", "배당금의지급", "배당금의 지급",
                     "배당금지급액", "현금배당", "배당금의지급액")},
}


# ================================================================
# 0. 키 / 종목코드 정규화
# ================================================================
def _key():
    return os.environ.get("DART_API_KEY", "").strip()


def _norm_code(code):
    """005930.KS / 005930.KQ / 005930 → 6자리 코드. 국내코드 아니면 None."""
    c = (code or "").strip().upper()
    c = re.sub(r"\.(KS|KQ)$", "", c)
    return c if re.fullmatch(r"\d{6}", c) else None


# ================================================================
# 1. 종목코드 → DART 고유번호(corp_code) 매핑
#    corpCode.xml(zip) 을 받아 1회 파싱 후 캐시.
# ================================================================
def _load_corp_map():
    global _CORP_MAP, _CORP_MAP_TS
    if _CORP_MAP is not None and time.time() - _CORP_MAP_TS < _CORP_TTL:
        return _CORP_MAP
    key = _key()
    if not key:
        return None
    r = requests.get(_DART + "/corpCode.xml",
                     params={"crtfc_key": key},
                     headers=_HEADERS, timeout=20)
    r.raise_for_status()
    # 응답이 zip이 아니면(키 오류 등) status/message XML이 옴 → 예외로
    try:
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        xml = zf.read("CORPCODE.xml")
    except zipfile.BadZipFile:
        try:
            t = ET.fromstring(r.content)
            raise RuntimeError("DART corpCode 오류: "
                               + (t.findtext("message") or "알 수 없음"))
        except ET.ParseError:
            raise RuntimeError("DART corpCode 응답 파싱 실패")
    tree = ET.fromstring(xml)
    m = {}
    for li in tree.findall("list"):
        sc = (li.findtext("stock_code") or "").strip()
        if re.fullmatch(r"\d{6}", sc):      # 상장사(종목코드 있는 것)만
            m[sc] = {"corp_code": (li.findtext("corp_code") or "").strip(),
                     "corp_name": (li.findtext("corp_name") or "").strip()}
    _CORP_MAP = m
    _CORP_MAP_TS = time.time()
    return m


def _corp_code(stock6):
    m = _load_corp_map()
    if not m:
        return None
    hit = m.get(stock6)
    return hit["corp_code"] if hit else None


# ================================================================
# 2. 재무제표 원본 조회 (fnlttSinglAcntAll)
#    연결(CFS) 우선, 없으면 별도(OFS). 사업보고서 우선, 없으면 최신 분기.
# ================================================================
def _fetch_fs(corp_code, year, reprt_code):
    key = _key()
    for fs_div in ("CFS", "OFS"):
        r = requests.get(_DART + "/fnlttSinglAcntAll.json",
                         params={"crtfc_key": key, "corp_code": corp_code,
                                 "bsns_year": str(year), "reprt_code": reprt_code,
                                 "fs_div": fs_div},
                         headers=_HEADERS, timeout=20)
        try:
            j = r.json()
        except ValueError:
            continue
        if j.get("status") == "000" and j.get("list"):
            return j["list"], fs_div
    return None, None


def _fs_latest(corp_code):
    """가장 최근 사용 가능한 (사업보고서 우선) 재무제표를 찾아 반환."""
    ck = ("fs", corp_code)
    hit = _CACHE.get(ck)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    this_year = time.gmtime().tm_year
    # 최근 연도부터, 사업보고서 → 3Q → 반기 순으로 탐색
    for year in (this_year, this_year - 1, this_year - 2):
        for rc in (REPRT["사업"], REPRT["3Q"], REPRT["반기"], REPRT["1Q"]):
            rows, fs_div = _fetch_fs(corp_code, year, rc)
            if rows:
                out = {"rows": rows, "year": year, "reprt": rc, "fs_div": fs_div}
                _CACHE[ck] = (time.time(), out)
                return out
    _CACHE[ck] = (time.time(), None)
    return None


# ================================================================
# 3. 계정 추출 헬퍼
# ================================================================
def _to_num(s):
    if s is None:
        return None
    s = str(s).replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _pick(rows, logical_name, sj_div=None):
    """_ACC 매핑을 이용해 한 계정의 당기/전기/전전기 금액을 찾는다."""
    spec = _ACC.get(logical_name)
    if not spec:
        return None
    ids = set(spec["id"])
    nms = set(spec["nm"])
    cand = None
    for r in rows:
        if sj_div and r.get("sj_div") != sj_div:
            continue
        aid = (r.get("account_id") or "").strip()
        anm = (r.get("account_nm") or "").replace(" ", "")
        if aid in ids or any(anm == n.replace(" ", "") for n in nms):
            cand = r
            break
    if not cand:
        return None
    return {
        "account_nm": cand.get("account_nm"),
        "thstrm": _to_num(cand.get("thstrm_amount")),
        "frmtrm": _to_num(cand.get("frmtrm_amount")),
        "bfefrmtrm": _to_num(cand.get("bfefrmtrm_amount")),
        "thstrm_nm": cand.get("thstrm_nm"),
    }


# ================================================================
# 3.5 미국 재무 — Financial Modeling Prep (FMP). 무료 API 키 필요.
#     티커 직접 사용(CIK 불필요). income/balance/cash-flow 3종 조회.
#     Render IP가 SEC에 차단되는 문제를 우회(FMP 서버가 정제 데이터 제공).
# ================================================================
_FMP = "https://financialmodelingprep.com/stable"


def _fmp_key():
    return os.environ.get("FMP_API_KEY", "").strip()


def _fmp_get(endpoint, ticker, params=None):
    """FMP stable 요청. 티커는 symbol= 쿼리로 전달. 응답은 리스트(최신순)."""
    key = _fmp_key()
    if not key:
        raise RuntimeError("FMP_API_KEY 미설정")
    p = {"symbol": ticker.upper(), "apikey": key}
    if params:
        p.update(params)
    r = requests.get(f"{_FMP}/{endpoint}",
                     params=p, headers=_HEADERS, timeout=20)
    if r.status_code == 401:
        raise RuntimeError("FMP 인증 실패(401) — API 키를 확인하세요.")
    if r.status_code == 403:
        raise RuntimeError("FMP 접근 거부(403) — 무료 플랜 제한이거나 키 문제.")
    if r.status_code == 429:
        raise RuntimeError("FMP 호출 한도 초과(429) — 무료는 하루 250회. 잠시 후 재시도.")
    r.raise_for_status()
    j = r.json()
    if isinstance(j, dict) and j.get("Error Message"):
        raise RuntimeError("FMP: " + str(j.get("Error Message")))
    return j if isinstance(j, list) else []


def _fmp_financials(ticker):
    """손익·재무상태·현금흐름 3종을 각각 연간 최근 3期씩 받아 캐시. 실패 시 예외.
    현금흐름표는 무료 플랜에서 막힐 수 있어 실패해도 무시(주주환원·영업CF만 비게 됨)."""
    ck = ("fmp", ticker.upper())
    hit = _CACHE.get(ck)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    inc = _fmp_get("income-statement", ticker, {"period": "annual", "limit": 5})
    bal = _fmp_get("balance-sheet-statement", ticker, {"period": "annual", "limit": 5})
    if not inc and not bal:
        raise RuntimeError("no_data")   # 종목 없음 신호
    # 현금흐름표: 실패(무료 플랜 제한 등)해도 앱 안 죽임 — 주주환원/영업CF만 빈다
    try:
        cf = _fmp_get("cash-flow-statement", ticker, {"period": "annual", "limit": 5})
    except Exception:  # noqa: BLE001
        cf = []
    out = {"income": inc, "balance": bal, "cashflow": cf}
    _CACHE[ck] = (time.time(), out)
    return out


# FMP 필드 매핑 (논리명 → FMP JSON 키, 어느 재무제표인지)
# ※ 새 stable 엔드포인트 기준 필드명. 배당은 netDividendsPaid로 변경됨.
_FMP_FIELD = {
    "매출액": ("income", "revenue"),
    "영업이익": ("income", "operatingIncome"),
    "당기순이익": ("income", "netIncome"),
    "자산총계": ("balance", "totalAssets"),
    "부채총계": ("balance", "totalLiabilities"),
    "자본총계": ("balance", "totalStockholdersEquity"),
    "영업활동현금흐름": ("cashflow", "operatingCashFlow", "netCashProvidedByOperatingActivities"),
    "자사주매입": ("cashflow", "commonStockRepurchased", "netStockRepurchased"),
    "배당지급": ("cashflow", "netDividendsPaid", "commonDividendsPaid", "dividendsPaid"),
}


def _fmp_series(data, logical):
    """논리명의 연간 값 리스트(최신순). 없으면 []."""
    spec = _FMP_FIELD.get(logical)
    if not spec:
        return []
    stmt = spec[0]
    fields = spec[1:]              # 값 필드 후보(앞에서부터 매칭)
    rows = data.get(stmt) or []
    out = []
    for r in rows:
        v = None
        for f in fields:
            if r.get(f) is not None:
                v = r.get(f)
                break
        if v is None:
            continue
        try:
            out.append((r.get("date") or r.get("calendarYear") or "", float(v)))
        except (ValueError, TypeError):
            continue
    return out


def _fmp_latest(data, logical):
    s = _fmp_series(data, logical)
    return s[0][1] if s else None


# ================================================================
# 4-US. 미국 응답 빌더 (요약/재무/펀더멘탈 공용) — FMP 기반
# ================================================================
def _us_payload(ticker, kind):
    if not _fmp_key():
        return {"error": "FMP_API_KEY 미설정", "need_fmp_key": True}
    try:
        data = _fmp_financials(ticker)
    except RuntimeError as e:
        if str(e) == "no_data":
            return {"error": "미국 상장 종목을 찾을 수 없습니다", "not_us": True}
        raise
    # 회사명: income 첫 행에 없으면 티커로
    name = ticker.upper()
    inc0 = (data.get("income") or [{}])[0]
    if inc0.get("symbol"):
        name = inc0.get("symbol")

    def ann3(logical):
        """최근 3개 연간 값 [당기, 전기, 전전기]."""
        s = _fmp_series(data, logical)
        vals = [x[1] for x in s[:3]]
        while len(vals) < 3:
            vals.append(None)
        return vals, (s[0][0] if s else None)

    if kind == "financials":
        def block(names, tag_of):
            out = []
            for n in names:
                vals, _end = ann3(tag_of[n])
                out.append({"name": n, "values": vals, "found": vals[0] is not None})
            return out
        bs_map = {"자산총계": "자산총계", "부채총계": "부채총계", "자본총계": "자본총계"}
        is_map = {"매출액": "매출액", "영업이익": "영업이익", "당기순이익": "당기순이익"}
        cf_map = {"영업활동현금흐름": "영업활동현금흐름"}
        _, end0 = ann3("자산총계")
        return {
            "market": "US", "corp_name": name, "stock_code": ticker.upper(),
            "period": {"basis": "연간", "latest_end": end0},
            "unit": "USD",
            "statements": {
                "재무상태표": block(list(bs_map), bs_map),
                "손익계산서": block(list(is_map), is_map),
                "현금흐름표": block(list(cf_map), cf_map),
            },
            "note": "Financial Modeling Prep 재무데이터. 최근 3개 회계연도(연간) 기준.",
        }

    if kind == "fundamentals":
        rev = _fmp_latest(data, "매출액")
        op = _fmp_latest(data, "영업이익")
        ni = _fmp_latest(data, "당기순이익")
        assets = _fmp_latest(data, "자산총계")
        liab = _fmp_latest(data, "부채총계")
        equity = _fmp_latest(data, "자본총계")

        def ratio(a, b):
            return round(a / b * 100, 2) if (a is not None and b) else None
        # 주주환원 (FMP는 유출을 음수로 줌 → 절대값으로 정규화)
        buyback = _fmp_latest(data, "자사주매입")
        dividend = _fmp_latest(data, "배당지급")
        bb = abs(buyback) if buyback is not None else None
        dv = abs(dividend) if dividend is not None else None
        total_return = None
        if bb is not None or dv is not None:
            total_return = (bb or 0) + (dv or 0)
        payout = ratio(total_return, ni) if (total_return is not None and ni) else None
        return {
            "market": "US", "corp_name": name, "stock_code": ticker.upper(),
            "period": {"basis": "연간"},
            "raw": {"매출액": rev, "영업이익": op, "당기순이익": ni,
                    "자산총계": assets, "부채총계": liab, "자본총계": equity},
            "metrics": {
                "영업이익률": {"value": ratio(op, rev), "unit": "%",
                             "formula": "영업이익 / 매출액 × 100"},
                "순이익률": {"value": ratio(ni, rev), "unit": "%",
                           "formula": "당기순이익 / 매출액 × 100"},
                "ROE": {"value": ratio(ni, equity), "unit": "%",
                        "formula": "당기순이익 / 자본총계 × 100"},
                "ROA": {"value": ratio(ni, assets), "unit": "%",
                        "formula": "당기순이익 / 자산총계 × 100"},
                "부채비율": {"value": ratio(liab, equity), "unit": "%",
                           "formula": "부채총계 / 자본총계 × 100"},
            },
            "shareholder_return": {
                "자사주매입": bb, "배당지급": dv, "총환원": total_return,
                "총환원율": {"value": payout, "unit": "%",
                          "formula": "(자사주매입 + 배당) / 당기순이익 × 100",
                          "note": "100% 초과 = 번 것보다 더 돌려줌(현금·차입 활용). 자사주로 자본총계가 줄면 ROE가 커짐."},
                "unit": "USD",
            },
            "note": ("PER·PBR 등 시장가 지표는 별도 단계에서 추가 예정. "
                     "여기 지표는 재무제표만으로 계산."),
        }

    if kind == "series":
        # 그래프용: 연도별 시계열을 오래된→최신 순으로. 라벨은 연도(YYYY).
        def ser(logical):
            s = _fmp_series(data, logical)          # 최신순 [(date,val),...]
            rows = list(reversed(s))                # 오래된→최신
            labels = [(d or "")[:4] for d, _ in rows]
            vals = [v for _, v in rows]
            return labels, vals
        rev_l, rev_v = ser("매출액")
        _, op_v = ser("영업이익")
        _, ni_v = ser("당기순이익")
        _, eq_v = ser("자본총계")
        return {
            "market": "US", "corp_name": name, "stock_code": ticker.upper(),
            "unit": "USD", "period": {"basis": "연간"},
            "labels": rev_l,
            "series": {
                "매출액": rev_v, "영업이익": op_v,
                "당기순이익": ni_v, "자본총계": eq_v,
            },
            "note": "연간 기준. 무료 FMP는 최근 5개년까지 제공.",
        }

    # summary
    def hi(logical):
        s = _fmp_series(data, logical)
        v = s[0][1] if s else None
        prev = s[1][1] if len(s) > 1 else None
        yoy = round((v / prev - 1) * 100, 1) if (v is not None and prev) else None
        return v, yoy
    rev, rev_y = hi("매출액")
    op, op_y = hi("영업이익")
    ni, ni_y = hi("당기순이익")
    return {
        "market": "US", "corp_name": name, "stock_code": ticker.upper(),
        "period": {"basis": "연간"},
        "highlights": [
            {"label": "매출액", "value": rev, "yoy": rev_y, "unit": "USD"},
            {"label": "영업이익", "value": op, "yoy": op_y, "unit": "USD"},
            {"label": "당기순이익", "value": ni, "yoy": ni_y, "unit": "USD"},
        ],
        "note": "YoY = 당기/전기 − 1 (연간 기준).",
    }


def _is_kr(code):
    return _norm_code(code) is not None


def _is_us_ticker(code):
    return bool(re.fullmatch(r"[A-Za-z.\-]{1,6}", (code or "").strip()))


def _resolve_or_error(code):
    """공통 진입 처리: 키 확인 → 국내코드 확인 → corp_code 해석."""
    if not _key():
        return None, (jsonify({"error": "DART_API_KEY 미설정", "need_key": True}), 200)
    stock6 = _norm_code(code)
    if not stock6:
        return None, (jsonify({"error": "국내 상장 종목이 아닙니다", "not_kr": True}), 200)
    try:
        cc = _corp_code(stock6)
    except Exception as e:  # noqa: BLE001
        return None, (jsonify({"error": str(e)}), 502)
    if not cc:
        return None, (jsonify({"error": "고유번호를 찾을 수 없습니다", "not_kr": True}), 200)
    return {"stock6": stock6, "corp_code": cc,
            "corp_name": (_CORP_MAP.get(stock6, {}) or {}).get("corp_name", "")}, None


def _dispatch(code, kind, kr_builder):
    """국내(6자리)=DART, 그 외=미국(EDGAR)로 분기. kr_builder는 DART 경로 처리 함수."""
    if _is_kr(code):
        return kr_builder(code)
    if _is_us_ticker(code):
        try:
            return jsonify(_us_payload(code, kind))
        except requests.HTTPError as e:  # noqa: BLE001
            sc = getattr(e.response, "status_code", None)
            if sc == 404:
                return jsonify({"error": "미국 상장 종목을 찾을 수 없습니다", "not_us": True})
            return jsonify({"error": f"SEC 조회 실패: {e}"}), 502
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": str(e)}), 502
    return jsonify({"error": "인식할 수 없는 종목 코드", "unknown": True})


# ================================================================
# 4. 엔드포인트
# ================================================================
@butler_bp.route("/api/butler/financials/<code>")
def financials(code):
    return _dispatch(code, "financials", _kr_financials)


def _kr_financials(code):
    ctx, err = _resolve_or_error(code)
    if err:
        return err
    fs = _fs_latest(ctx["corp_code"])
    if not fs:
        return jsonify({"error": "재무제표를 찾을 수 없습니다"}), 200
    rows = fs["rows"]

    def three(name, sj):
        p = _pick(rows, name, sj)
        if not p:
            return {"name": name, "values": [None, None, None], "found": False}
        return {"name": name,
                "values": [p["thstrm"], p["frmtrm"], p["bfefrmtrm"]],
                "found": True}

    bs = [three(n, "BS") for n in ("자산총계", "부채총계", "자본총계")]
    is_ = [three(n, "IS") for n in ("매출액", "영업이익", "당기순이익")]
    cf = [three(n, "CF") for n in ("영업활동현금흐름",)]
    # 라벨(당기/전기/전전기)
    sample = _pick(rows, "자산총계", "BS") or {}
    return jsonify({
        "market": "KR",
        "corp_name": ctx["corp_name"], "stock_code": ctx["stock6"],
        "period": {"year": fs["year"], "reprt": fs["reprt"], "fs_div": fs["fs_div"],
                   "thstrm_nm": sample.get("thstrm_nm")},
        "unit": "원",
        "statements": {"재무상태표": bs, "손익계산서": is_, "현금흐름표": cf},
        "note": "DART 정기보고서 원본 계정. 연결(CFS) 우선, 없으면 별도(OFS).",
    })


@butler_bp.route("/api/butler/fundamentals/<code>")
def fundamentals(code):
    return _dispatch(code, "fundamentals", _kr_fundamentals)


def _kr_fundamentals(code):
    ctx, err = _resolve_or_error(code)
    if err:
        return err
    fs = _fs_latest(ctx["corp_code"])
    if not fs:
        return jsonify({"error": "재무제표를 찾을 수 없습니다"}), 200
    rows = fs["rows"]

    def val(name, sj):
        p = _pick(rows, name, sj)
        return (p["thstrm"] if p else None)

    revenue = val("매출액", "IS")
    op = val("영업이익", "IS")
    ni = val("당기순이익", "IS")
    assets = val("자산총계", "BS")
    liab = val("부채총계", "BS")
    equity = val("자본총계", "BS")

    def ratio(a, b):
        return round(a / b * 100, 2) if (a is not None and b) else None

    # 마진·비율 (계산식 그대로 노출)
    metrics = {
        "영업이익률": {"value": ratio(op, revenue), "unit": "%",
                     "formula": "영업이익 / 매출액 × 100"},
        "순이익률":   {"value": ratio(ni, revenue), "unit": "%",
                     "formula": "당기순이익 / 매출액 × 100"},
        "ROE":       {"value": ratio(ni, equity), "unit": "%",
                     "formula": "당기순이익 / 자본총계 × 100",
                     "note": "연환산 아님(보고서 기준 그대로). 분기보고서면 과소평가될 수 있음."},
        "ROA":       {"value": ratio(ni, assets), "unit": "%",
                     "formula": "당기순이익 / 자산총계 × 100"},
        "부채비율":   {"value": ratio(liab, equity), "unit": "%",
                     "formula": "부채총계 / 자본총계 × 100"},
    }
    # 주주환원 (현금흐름표 재무활동 — 유출 부호 혼재 → 절대값)
    buyback = val("자사주매입", "CF")
    dividend = val("배당지급", "CF")
    bb = abs(buyback) if buyback is not None else None
    dv = abs(dividend) if dividend is not None else None
    total_return = ((bb or 0) + (dv or 0)) if (bb is not None or dv is not None) else None
    payout = ratio(total_return, ni) if (total_return is not None and ni) else None
    return jsonify({
        "market": "KR",
        "corp_name": ctx["corp_name"], "stock_code": ctx["stock6"],
        "period": {"year": fs["year"], "reprt": fs["reprt"], "fs_div": fs["fs_div"]},
        "raw": {"매출액": revenue, "영업이익": op, "당기순이익": ni,
                "자산총계": assets, "부채총계": liab, "자본총계": equity},
        "metrics": metrics,
        "shareholder_return": {
            "자사주매입": bb, "배당지급": dv, "총환원": total_return,
            "총환원율": {"value": payout, "unit": "%",
                      "formula": "(자사주매입 + 배당) / 당기순이익 × 100",
                      "note": "현금흐름표 재무활동 기준. 계정명이 회사마다 달라 일부 종목은 안 잡힐 수 있음."},
            "unit": "원",
        },
        "note": ("PER·PBR은 시가총액(주가×주식수)이 필요해 별도 단계에서 추가 예정. "
                 "여기 지표는 DART 재무제표만으로 계산 가능한 것들."),
    })


@butler_bp.route("/api/butler/summary/<code>")
def summary(code):
    return _dispatch(code, "summary", _kr_summary)


@butler_bp.route("/api/butler/series/<code>")
def series(code):
    return _dispatch(code, "series", _kr_series_stub)


def _kr_series_stub(code):
    # 한국(DART) 그래프 시계열은 이후 단계에서 구현 예정.
    return jsonify({"error": "한국 종목 그래프는 준비 중입니다", "not_ready": True}), 200


def _kr_summary(code):
    ctx, err = _resolve_or_error(code)
    if err:
        return err
    fs = _fs_latest(ctx["corp_code"])
    if not fs:
        return jsonify({"error": "재무제표를 찾을 수 없습니다"}), 200
    rows = fs["rows"]
    rev = _pick(rows, "매출액", "IS")
    op = _pick(rows, "영업이익", "IS")
    ni = _pick(rows, "당기순이익", "IS")

    def yoy(p):
        if not p or p["thstrm"] is None or not p["frmtrm"]:
            return None
        return round((p["thstrm"] / p["frmtrm"] - 1) * 100, 1)

    return jsonify({
        "market": "KR",
        "corp_name": ctx["corp_name"], "stock_code": ctx["stock6"],
        "period": {"year": fs["year"], "reprt": fs["reprt"], "fs_div": fs["fs_div"]},
        "highlights": [
            {"label": "매출액", "value": rev["thstrm"] if rev else None,
             "yoy": yoy(rev), "unit": "원"},
            {"label": "영업이익", "value": op["thstrm"] if op else None,
             "yoy": yoy(op), "unit": "원"},
            {"label": "당기순이익", "value": ni["thstrm"] if ni else None,
             "yoy": yoy(ni), "unit": "원"},
        ],
        "note": "YoY = 당기/전기 − 1. 보고서 종류가 섞이면 비교가 왜곡될 수 있어 같은 보고서끼리만 비교 권장.",
    })
