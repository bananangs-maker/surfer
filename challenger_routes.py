"""
challenger_routes.py — 도전자 프로토콜 서버 라우트 (Render용)
app.py 맨 아래(if __name__ 위)에 아래 두 줄만 추가하면 등록됨:

    import challenger_routes
    challenger_routes.register(app, _load)

사용법 (배포 후 브라우저에서):
  /research/challengers?ticker=TQQQ          ← C1(TSMOM)·C2(앙상블), 종목당 1회
  /research/challengers?ticker=TQQQ&hist=max ← 최대 기간으로
  /research/rotation?a=TQQQ&b=SOXL           ← C3 로테이션
  /research/ext?ticker=TQQQ                  ← 트랜치(시간/하락)+변동성 스케일링
  /research/divergence?ticker=TQQQ           ← 다이버전스 선매매 (quant에 이미 있음)
무료 플랜 타임아웃(30초) 대응: 종목당 1요청. 그래도 초과하면 &hist= 빼고(10년→기본) 재시도.
"""
from flask import jsonify, request

def register(app, _load):
    def _get_df(ticker):
        hist = (request.args.get("hist") or "").strip()
        if hist == "max":
            df, src = _load(ticker, "1day", long=True, hist="max")
        else:
            df, src = _load(ticker, "1day", long=True)
        return df, src

    @app.route("/research/challengers")
    def research_challengers():
        ticker = (request.args.get("ticker") or "TQQQ").upper()
        try:
            df, src = _get_df(ticker)
            import challengers as C
            r1 = C.c1_tsmom(df)
            r2 = C.c2_ensemble(df)
            return jsonify({"ticker": ticker, "source": src, "bars": len(df),
                            "period": f"{df['date'].iloc[0]}~{df['date'].iloc[-1]}",
                            "criteria": "①4종목중3+ d_calmar>0 ②인접3칸+ 평원 ③비용상쇄 ④관찰승격만",
                            "C1_TSMOM": r1, "C2_ensemble": r2})
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": str(e), "ticker": ticker}), 500

    @app.route("/research/rotation")
    def research_rotation():
        a = (request.args.get("a") or "TQQQ").upper()
        b = (request.args.get("b") or "SOXL").upper()
        try:
            da, sa = _get_df(a); db, sb = _get_df(b)
            import challengers as C
            r3 = C.c3_rotation(da, db, a, b)
            return jsonify({"pair": f"{a}/{b}", "bars": min(len(da), len(db)), "C3_rotation": r3})
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": str(e)}), 500

    @app.route("/research/ext")
    def research_ext_route():
        ticker = (request.args.get("ticker") or "TQQQ").upper()
        try:
            df, src = _get_df(ticker)
            import research_ext as R
            return jsonify({"ticker": ticker, "source": src, "bars": len(df),
                            "tranche_time": R.tranche_time(df),
                            "tranche_dip": R.tranche_dip(df),
                            "vol_scale": R.vol_scale(df)})
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": str(e), "ticker": ticker}), 500

    @app.route("/research/divergence")
    def research_divergence():
        ticker = (request.args.get("ticker") or "TQQQ").upper()
        try:
            df, src = _get_df(ticker)
            import quant as Q
            return jsonify({"ticker": ticker, "source": src, "bars": len(df),
                            "divergence_pretrade": Q.divergence_pretrade_research(df)})
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": str(e), "ticker": ticker}), 500


# ═══ 시즌 2 라우트 (challenger_routes.register 안이 아니라 별도 등록 함수) ═══
def register_season2(app, _load):
    from flask import jsonify, request

    def _get_df(ticker):
        hist = (request.args.get("hist") or "").strip()
        if hist == "max":
            df, src = _load(ticker, "1day", long=True, hist="max")
        else:
            df, src = _load(ticker, "1day", long=True)
        return df, src

    @app.route("/research/season2")
    def research_season2():
        ticker = (request.args.get("ticker") or "TQQQ").upper()
        try:
            df, src = _get_df(ticker)
            import season2 as S2
            return jsonify({"ticker": ticker, "source": src, "bars": len(df),
                            "period": f"{df['date'].iloc[0]}~{df['date'].iloc[-1]}",
                            "criteria": "①4종목중3+ d_calmar>0 ②인접3칸+ 평원 ③비용상쇄 ④관찰승격만",
                            "S1_hysteresis": S2.s1_hysteresis(df),
                            "S2_multima": S2.s2_multima(df)})
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": str(e), "ticker": ticker}), 500

    @app.route("/research/season2_pair")
    def research_season2_pair():
        a = (request.args.get("a") or "TQQQ").upper()
        b = (request.args.get("b") or "SOXL").upper()
        try:
            da, _ = _get_df(a); db, _ = _get_df(b)
            import season2 as S2
            return jsonify({"pair": f"{a}/{b}",
                            "S3_corrcap": S2.s3_corrcap(da, db)})
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": str(e)}), 500
