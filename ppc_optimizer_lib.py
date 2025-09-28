import pandas as pd
import yaml

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
