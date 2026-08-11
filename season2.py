"""
season2.py — 도전자 프로토콜 시즌 2 (사전 등록: 2026-07-12)
S1 히스테리시스 밴드 · S2 멀티 룩백 게이트 · S3 포트폴리오 상관 캡
판정 기준(1기와 동일·고정): ①4종목 3+ d_calmar>0 ②인접 3칸+ 평원 ③비용 상쇄 ④관찰 승격만.
인프라: quant._exec_returns(T+1 체결) 재사용. 미래참조 없음(모든 상태는 과거 봉만으로 갱신).
"""
import numpy as np, pandas as pd
import strategy as ST
from quant import _exec_returns, _metrics, _trade_stats

def _run(df, e, cost_bps=5.0, expense=0.0095):
    strat, pos, turn, _ = _exec_returns(df, e, cost_bps, expense)
    m=_metrics(strat, pos, turn); m.update(_trade_stats(strat, pos)); return m

def _delta(m, base):
    m=dict(m); m["d_calmar"]=round(m["calmar"]-base["calmar"],3)
    m["d_total"]=round(m["total"]-base["total"],1); return m

def _rebuild(df, k, tf_new_arr, p):
    """추세계수만 tf_new로 교체한 노출 재구성 (변동성·급성 성분은 코어와 동일)."""
    comp=ST._components(k,p)
    vt=(p["target_vol"]/k["rvol"]).clip(p["vt_floor"],p["vt_cap"]).fillna(0.5).to_numpy()
    acute=(comp["ext"]*p["acute_w_ext"]+comp["dd"]*p["acute_w_dd"]
           +comp["crash"]*p["acute_w_crash"]+comp["cmf"]*p["acute_w_cmf"]).to_numpy()
    e=np.clip(p["maxcap"]*tf_new_arr*vt*(1-p["acute_strength"]*acute),0,p["maxcap"])
    return e

# ── S1: 히스테리시스 밴드 — 진입 sma200*(1+b) 상향, 청산 sma200*(1-b) 하향 ──
def s1_hysteresis(df, cost_bps=5.0, expense=0.0095, bands=(0.01,0.02,0.03,0.05)):
    p=dict(ST.PARAMS); k=ST.indikit(df)
    core=ST.exposure_core(df,p,k).to_numpy(float); warm=np.isnan(core)
    base=_run(df,core,cost_bps,expense)
    c=k["close"].to_numpy(); s200=k["sma200"].to_numpy()
    align=(k["sma50"]>k["sma200"]).to_numpy()
    ranging=(k["adx"]<p["adx_range"]).fillna(False).to_numpy()
    out={"base":base,"variants":{}}
    for b in bands:
        H=np.zeros(len(c),dtype=bool); h=False
        for i in range(len(c)):
            if s200[i]!=s200[i]: h=False
            elif not h and c[i]>s200[i]*(1+b): h=True
            elif h and c[i]<s200[i]*(1-b): h=False
            H[i]=h
        tf=np.where(H&align,p["tf_strong"],np.where(H,p["tf_weak"],p["tf_down"]))
        tf=tf*np.where(ranging,p["tf_range_mult"],1.0)
        e=_rebuild(df,k,tf,p); e[warm]=np.nan
        out["variants"]["band=%.0f%%"%(b*100)]=_delta(_run(df,e,cost_bps,expense),base)
    return out

# ── S2: 멀티 룩백 게이트 — {100,150,200,300}MA 상회 비율로 연속 비중 ──
def s2_multima(df, cost_bps=5.0, expense=0.0095, sets=((100,200),(100,150,200,300),(150,200,250))):
    p=dict(ST.PARAMS); k=ST.indikit(df)
    core=ST.exposure_core(df,p,k).to_numpy(float); warm=np.isnan(core)
    base=_run(df,core,cost_bps,expense)
    close=k["close"]
    ranging=(k["adx"]<p["adx_range"]).fillna(False).to_numpy()
    out={"base":base,"variants":{}}
    for lbs in sets:
        smas=[close.rolling(L).mean() for L in lbs]
        frac=sum((close>s).astype(float) for s in smas).to_numpy()/len(lbs)
        tf=p["tf_down"]+(p["tf_strong"]-p["tf_down"])*frac
        tf=tf*np.where(ranging,p["tf_range_mult"],1.0)
        e=_rebuild(df,k,tf,p)
        wm=warm.copy()
        for s in smas: wm|=s.isna().to_numpy()
        e[wm]=np.nan
        tag="/".join(str(L) for L in lbs)
        out["variants"][tag]=_delta(_run(df,e,cost_bps,expense),base)
    return out

# ── S3: 포트폴리오 상관 캡 — 롤링상관>0.6일 때 합산노출을 cap 이하로 축소 ──
def s3_corrcap(df_a, df_b, cost_bps=5.0, expense=0.0095, caps=(0.4,0.5,0.6,0.7), corr_th=0.6, corr_win=63):
    a=df_a[["date","close"]].rename(columns={"close":"ca"})
    b=df_b[["date","close"]].rename(columns={"close":"cb"})
    m=pd.merge(a,b,on="date").reset_index(drop=True)
    da=df_a[df_a["date"].isin(m["date"])].reset_index(drop=True)
    db=df_b[df_b["date"].isin(m["date"])].reset_index(drop=True)
    ea=ST.exposure_core(da,ST.PARAMS).to_numpy(float)
    eb=ST.exposure_core(db,ST.PARAMS).to_numpy(float)
    corr=m["ca"].pct_change().rolling(corr_win).corr(m["cb"].pct_change()).to_numpy()
    def run_pair(sa_w, sb_w):
        ra,_,_,_=_exec_returns(da,np.nan_to_num(ea)*sa_w,cost_bps,expense)
        rb,_,_,_=_exec_returns(db,np.nan_to_num(eb)*sb_w,cost_bps,expense)
        r=(ra.fillna(0)+rb.fillna(0)); r[np.isnan(ea)&np.isnan(eb)]=np.nan
        return _metrics(r.dropna())
    n=len(m); half=np.full(n,0.5)
    base=run_pair(half,half)  # 자본 50/50, 캡 없음
    out={"base_nocap":base,"variants":{}}
    for cap in caps:
        tot=0.5*np.nan_to_num(ea)+0.5*np.nan_to_num(eb)
        hi=(corr>corr_th)&(tot>cap)
        with np.errstate(divide="ignore",invalid="ignore"):
            s=np.where(hi,cap/np.where(tot>0,tot,1.0),1.0)
        mm=run_pair(half*s,half*s)
        mm=dict(mm); mm["d_calmar"]=round(mm["calmar"]-base["calmar"],2)
        mm["d_total"]=round(mm["total"]-base["total"],1)
        mm["capped_days_pct"]=round(float(hi.mean()*100),1)
        out["variants"]["cap=%.1f"%cap]=mm
    return out
