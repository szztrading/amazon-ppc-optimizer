import pandas as pd
import yaml
import re
import pandas as pd
from collections import Counter
from itertools import islice

def _norm_tokenize(text: str):
    """简单标准化 + 分词：小写、去符号、按空白切分。"""
    if not isinstance(text, str):
        return []
    t = text.lower()
    t = re.sub(r"[\W_]+", " ", t, flags=re.UNICODE)
    toks = [w for w in t.split() if w]
    return toks

def _gen_ngrams(tokens, n=2):
    if n <= 1:
        for tok in tokens:
            yield tok
        return
    # 1-gram + 2-gram
    for tok in tokens:
        yield tok
    for i in range(len(tokens)-1):
        yield tokens[i] + " " + tokens[i+1]

def suggest_lexicon_updates(df_std: pd.DataFrame, config: dict):
    """
    输入：标准化后的df，至少包含：search_term/clicks/orders。
    输出：一张“词根建议表”，用于人工审阅并更新 config.yaml 的 negatives_scan.patterns。
    原理：
      - 统计【差词集合】：点击≥min_clicks 且 订单=0 的搜索词
      - 统计【好词集合】：点击≥min_clicks 且 订单>0 的搜索词
      - 生成 1-gram/2-gram 词元，计算 差词频 - λ*好词频 的得分，过滤停用词/白名单/已在词库中的词。
    """
    lx = config.get("lexicon", {}) or {}
    min_bad = int(lx.get("min_clicks_for_bad", 1))
    min_good = int(lx.get("min_clicks_for_good", 1))
    top_k = int(lx.get("suggest_top_k", 50))
    ngram_max = int(lx.get("ngram_max", 2))
    min_bad_freq = int(lx.get("min_bad_freq", 2))
    whitelist = set([w.lower() for w in lx.get("whitelist", [])])
    stopwords = set([w.lower() for w in lx.get("stopwords", [])])

    # 现有词库（避免重复建议）
    existing = set()
    neg_cfg = (config.get("negatives_scan") or {}).get("patterns") or {}
    for lst in neg_cfg.values():
        for w in lst:
            existing.add(w.lower())

    df = df_std.copy()
    # 数值统一
    for col in ["clicks","orders"]:
        if col in df.columns:
            if df[col].dtype == "O":
                df[col] = df[col].replace({",":""}, regex=True)
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # 构造“差词/好词”集合
    bad_terms = df[(df["clicks"] >= min_bad) & (df["orders"] == 0)]["search_term"].astype(str).tolist()
    good_terms = df[(df["clicks"] >= min_good) & (df["orders"] > 0)]["search_term"].astype(str).tolist()

    bad_counter = Counter()
    good_counter = Counter()

    def feed_counter(terms, counter):
        for s in terms:
            toks = _norm_tokenize(s)
            grams = []
            for n in range(1, ngram_max+1):
                grams.extend(list(_gen_ngrams(toks, n=n)))
            for g in grams:
                # 过滤停用词/过短项
                if g in stopwords: 
                    continue
                if len(g) <= 2: 
                    continue
                counter[g] += 1

    feed_counter(bad_terms, bad_counter)
    feed_counter(good_terms, good_counter)

    if not bad_counter:
        return pd.DataFrame(columns=["Token","BadFreq","GoodFreq","Score","SampleTerms","Recommendation"])

    # 评分：差词频 - 0.5*好词频（0.5 可调）
    alpha = 0.5
    rows = []
    for token, bf in bad_counter.most_common():
        if bf < min_bad_freq:
            continue
        gf = good_counter.get(token, 0)
        score = bf - alpha * gf

        # 过滤保护/已存在词库
        if token in whitelist or token in existing:
            continue

        # 样例搜索词（便于你判断）
        samples = []
        for s in bad_terms:
            if token in " ".join(_norm_tokenize(s)):
                samples.append(s)
            if len(samples) >= 3:
                break

        rows.append({
            "Token": token,
            "BadFreq": int(bf),
            "GoodFreq": int(gf),
            "Score": float(score),
            "SampleTerms": " | ".join(samples),
            "Recommendation": "ADD_TO_PATTERNS" if gf == 0 else "REVIEW"
        })

    sugg = pd.DataFrame(rows).sort_values(["Score","BadFreq"], ascending=[False,False]).head(top_k).reset_index(drop=True)
    return sugg

def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def calculate_metrics(df, config):
    # 列名映射
    m = config["columns_map"]
    df = df.rename(columns={
        m["search_term"]: "search_term",
        m["clicks"]: "clicks",
        m["impressions"]: "impressions",
        m["spend"]: "spend",
        m["sales"]: "sales",
        m["orders"]: "orders",
    })
    
    # 基本指标计算
    df["ctr"] = df["clicks"] / df["impressions"]
    df["cpc"] = df["spend"] / df["clicks"]
    df["acos"] = df["spend"] / df["sales"]
    df["conversion_rate"] = df["orders"] / df["clicks"]

    # 筛选建议
    target_acos = config["target_acos"]

    scale_up = df[(df["acos"] < target_acos) & (df["orders"] >= config["min_conversions"])]
    bid_down = df[(df["acos"] > target_acos) & (df["clicks"] >= config["min_clicks"])]
    negatives = df[(df["clicks"] > 50) & (df["orders"] == 0)]
    harvest = df[(df["conversion_rate"] > config["harvest_threshold"])]

    return {
        "All_Terms": df,
        "Scale_Up": scale_up,
        "Bid_Down": bid_down,
        "Negatives": negatives,
        "Harvest": harvest,
    }
