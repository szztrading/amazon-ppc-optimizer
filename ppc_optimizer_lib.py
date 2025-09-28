# ppc_optimizer_lib.py
import re
import yaml
import pandas as pd
from collections import Counter

# ---------- 配置 ----------
def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

# ---------- 工具函数 ----------
def _safe_num_series(s: pd.Series) -> pd.Series:
    if s is None:
        return pd.Series(dtype="float64")
    if getattr(s, "dtype", None) == "O":
        s = s.replace({",": ""}, regex=True)
    return pd.to_numeric(s, errors="coerce")

def _find_col(cols, candidates):
    if not candidates:
        return None
    if isinstance(candidates, str):
        candidates = [candidates]
    norm = [re.sub(r"[^a-z0-9]+", "", c.strip().lower()) for c in cols]
    for cand in candidates:
        key = re.sub(r"[^a-z0-9]+", "", cand.strip().lower())
        if key in norm:
            return cols[norm.index(key)]
    return None

# ---------- 标准化 ----------
def standardize_df(raw_df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    m = (cfg.get("columns_map") or {})
    cols = list(raw_df.columns)

    col_search = _find_col(cols, m.get("search_term", ["Customer Search Term","Search term"]))
    col_clicks = _find_col(cols, m.get("clicks", ["Clicks"]))
    col_impr   = _find_col(cols, m.get("impressions", ["Impressions"]))
    col_spend  = _find_col(cols, m.get("spend", ["Spend","Cost"]))
    col_sales  = _find_col(cols, m.get("sales", ["7 Day Total Sales","14 Day Total Sales","Total Sales"]))
    col_orders = _find_col(cols, m.get("orders", ["7 Day Total Orders (#)","7 Day Total Units Ordered","Orders"]))
    col_camp   = _find_col(cols, m.get("campaign", ["Campaign Name"]))
    col_adgrp  = _find_col(cols, m.get("ad_group", ["Ad Group Name"]))

    df = pd.DataFrame()
    if col_search: df["search_term"] = raw_df[col_search].astype(str)
    if col_clicks: df["clicks"]      = _safe_num_series(raw_df[col_clicks])
    if col_impr:   df["impressions"] = _safe_num_series(raw_df[col_impr])
    if col_spend:  df["spend"]       = _safe_num_series(raw_df[col_spend])
    if col_sales:  df["sales"]       = _safe_num_series(raw_df[col_sales])
    if col_orders: df["orders"]      = _safe_num_series(raw_df[col_orders])
    if col_camp:   df["campaign"]    = raw_df[col_camp].astype(str)
    if col_adgrp:  df["ad_group"]    = raw_df[col_adgrp].astype(str)

    for c in ["clicks","impressions","spend","sales","orders"]:
        if c not in df.columns:
            df[c] = 0
    for c in ["campaign","ad_group","search_term"]:
        if c not in df.columns:
            df[c] = ""

    df["ctr"]  = df["clicks"] / df["impressions"].replace(0, pd.NA)
    df["cpc"]  = df["spend"]  / df["clicks"].replace(0, pd.NA)
    df["acos"] = df["spend"]  / df["sales"].replace(0, pd.NA)
    df["cvr"]  = df["orders"] / df["clicks"].replace(0, pd.NA)
    return df.fillna(0)

# ---------- 早期否定词扫描 ----------
def scan_potential_negatives(df_std: pd.DataFrame, cfg: dict):
    scan = cfg.get("negatives_scan", {}) or {}
    mode       = str(scan.get("mode", "conservative")).lower()
    min_clk    = int(scan.get("min_clicks_no_order", 1))
    min_ctr    = float(scan.get("min_ctr", 0.0))
    match_type = str(scan.get("match_type", "negative exact"))
    patterns   = scan.get("patterns", {}) or {}

    df = df_std.copy()
    base = df[(df["clicks"] >= min_clk) & (df["orders"] == 0)]
    if min_ctr > 0:
        base = base[(base["ctr"].fillna(0) >= min_ctr)]

    rows = []
    for _, r in base.iterrows():
        term = str(r["search_term"]).lower()
        reasons = []
        for tag, plist in patterns.items():
            for p in plist:
                if p.lower() in term:
                    reasons.append(tag)
                    break
        if mode == "aggressive" and not reasons:
            for g in ["pad","belt","starter","bundle"]:
                if g in term:
                    reasons.append("GENERIC_HEURISTIC")
                    break
        if reasons:
            rows.append({
                "Campaign Name": r["campaign"],
                "Ad Group Name": r["ad_group"],
                "Customer Search Term": r["search_term"],
                "Clicks": int(r["clicks"]),
                "Orders": int(r["orders"]),
                "CTR": float(r["ctr"] or 0),
                "Reason": ";".join(sorted(set(reasons)))
            })
    neg_source = pd.DataFrame(rows)

    if not neg_source.empty:
        neg_upload = pd.DataFrame({
            "Campaign Name": neg_source["Campaign Name"],
            "Ad Group Name": neg_source["Ad Group Name"],
            "Negative Keyword": neg_source["Customer Search Term"],
            "Match Type": match_type
        })
    else:
        neg_upload = pd.DataFrame(columns=["Campaign Name","Ad Group Name","Negative Keyword","Match Type"])

    return neg_source, neg_upload

# ---------- 词库建议 ----------
def _norm_tokenize(text: str):
    if not isinstance(text, str):
        return []
    t = re.sub(r"[\W_]+", " ", text.lower())
    return [w for w in t.split() if w]

def _yield_ngrams(tokens, nmax=2):
    for t in tokens:
        yield t
    for i in range(len(tokens)-1):
        yield tokens[i] + " " + tokens[i+1]

def suggest_lexicon_updates(df_std: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    lx = cfg.get("lexicon", {}) or {}
    min_bad   = int(lx.get("min_clicks_for_bad", 1))
    min_good  = int(lx.get("min_clicks_for_good", 1))
    top_k     = int(lx.get("suggest_top_k", 50))
    ngram_max = int(lx.get("ngram_max", 2))
    min_bad_f = int(lx.get("min_bad_freq", 2))
    whitelist = set(w.lower() for w in lx.get("whitelist", []))
    stopwords = set(w.lower() for w in lx.get("stopwords", []))

    # 已有词库，避免重复建议
    existing = set()
    pat_cfg = (cfg.get("negatives_scan") or {}).get("patterns", {}) or {}
    for lst in pat_cfg.values():
        for w in lst:
            existing.add(w.lower())

    df = df_std.copy()
    for c in ["clicks","orders"]:
        if c in df.columns and df[c].dtype == "O":
            df[c] = df[c].replace({",":""}, regex=True)
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    bad_terms  = df[(df["clicks"] >= min_bad) & (df["orders"] == 0)]["search_term"].astype(str).tolist()
    good_terms = df[(df["clicks"] >= min_good) & (df["orders"] > 0)]["search_term"].astype(str).tolist()

    bad_cnt, good_cnt = Counter(), Counter()

    def feed(terms, counter):
        for s in terms:
            toks = _norm_tokenize(s)
            grams = list(_yield_ngrams(toks, nmax=ngram_max))
            for g in grams:
                if g in stopwords or len(g) <= 2:
                    continue
                counter[g] += 1

    feed(bad_terms, bad_cnt)
    feed(good_terms, good_cnt)

    rows = []
    alpha = 0.5
    for tok, bf in bad_cnt.most_common():
        if bf < min_bad_f:
            continue
        if tok in whitelist or tok in existing:
            continue
        gf = good_cnt.get(tok, 0)
        score = bf - alpha * gf
        samples = []
        for s in bad_terms:
            if tok in " ".join(_norm_tokenize(s)):
                samples.append(s)
            if len(samples) >= 3:
                break
        rows.append({
            "Token": tok,
            "BadFreq": int(bf),
            "GoodFreq": int(gf),
            "Score": float(score),
            "SampleTerms": " | ".join(samples),
            "Recommendation": "ADD_TO_PATTERNS" if gf == 0 else "REVIEW"
        })

    if not rows:
        return pd.DataFrame(columns=["Token","BadFreq","GoodFreq","Score","SampleTerms","Recommendation"])
    return pd.DataFrame(rows).sort_values(["Score","BadFreq"], ascending=[False,False]).head(top_k).reset_index(drop=True)

# ---------- 主分析 ----------
def calculate_metrics(raw_df: pd.DataFrame, cfg: dict) -> dict:
    df = standardize_df(raw_df, cfg)

    target_acos     = float(cfg.get("target_acos", 0.50))
    min_clicks      = int(cfg.get("min_clicks", 5))
    min_conversions = int(cfg.get("min_conversions", 1))
    harvest_th      = float(cfg.get("harvest_threshold", 0.05))

    scale_up  = df[(df["acos"] < target_acos) & (df["orders"] >= min_conversions)]
    bid_down  = df[(df["acos"] > target_acos) & (df["clicks"] >= min_clicks)]
    negatives = df[(df["clicks"] >= max(30, min_clicks)) & (df["orders"] == 0)]
    harvest   = df[(df["cvr"] >= harvest_th)]

    early_src, early_upload = scan_potential_negatives(df, cfg)
    lex_sugg = suggest_lexicon_updates(df, cfg)

    return {
        "All_Terms": df,
        "Scale_Up": scale_up.reset_index(drop=True),
        "Bid_Down": bid_down.reset_index(drop=True),
        "Negatives": negatives.reset_index(drop=True),
        "Harvest": harvest.reset_index(drop=True),
        "Early_Negatives_Source": early_src.reset_index(drop=True),
        "Early_Negatives_Upload": early_upload.reset_index(drop=True),
        "Lexicon_Suggestions": lex_sugg.reset_index(drop=True),
    }
