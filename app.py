# app.py — Amazon PPC Optimizer（含上传进度、早期否定扫描、词库建议 + v1.1 提价➝拆词/SKAG/否定导出，否定词根从 config.yaml 读取）
import os, io, time, tempfile
import pandas as pd
import streamlit as st
from datetime import datetime

from ppc_optimizer_lib import load_config, calculate_metrics  # 你已有的库：读取 config.yaml & 主分析

st.set_page_config(page_title="Amazon PPC Optimizer", layout="wide")

# =========================
# v1.1 新增：共用小工具
# =========================
def _safe_div(a, b):
    try:
        return (a / b) if b else 0.0
    except Exception:
        return 0.0

def _standardize_columns(df):
    """
    将常见的列名映射成标准列，便于统一计算。
    有则映射，无则忽略（保持鲁棒）。
    """
    rename_map = {
        # 搜索词
        "Customer Search Term": "search_term",
        "customer_search_term": "search_term",
        "Search Term": "search_term",
        "Query": "search_term",
        # 指标
        "Clicks": "clicks",
        "Impressions": "impressions",
        "Spend": "spend",
        "Cost": "spend",
        "7 Day Total Sales": "sales",
        "Sales": "sales",
        "7 Day Total Orders (#)": "orders",
        "Orders": "orders",
        # 结构
        "Campaign Name": "campaign",
        "Ad Group Name": "ad_group",
    }
    for k, v in rename_map.items():
        if k in df.columns and v not in df.columns:
            df = df.rename(columns={k: v})
    return df

def _ensure_metrics(df):
    """
    确保 df 具备 ctr / cpc / acos / cvr 指标；若缺失则计算。
    """
    df = df.copy()
    df = _standardize_columns(df)
    for col in ["clicks", "impressions", "spend", "sales", "orders"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    if "ctr" not in df.columns and {"clicks","impressions"}.issubset(df.columns):
        df["ctr"] = df.apply(lambda r: _safe_div(r["clicks"], r["impressions"]), axis=1)
    if "cpc" not in df.columns and {"spend","clicks"}.issubset(df.columns):
        df["cpc"] = df.apply(lambda r: _safe_div(r["spend"], r["clicks"]), axis=1)
    if "acos" not in df.columns and {"spend","sales"}.issubset(df.columns):
        df["acos"] = df.apply(lambda r: _safe_div(r["spend"], r["sales"]), axis=1)
    if "cvr" not in df.columns and {"orders","clicks"}.issubset(df.columns):
        df["cvr"] = df.apply(lambda r: _safe_div(r["orders"], r["clicks"]), axis=1)
    return df

def _build_v11_decision_tables(df_all_terms, cfg, target_acos=0.30, min_clicks=20, min_orders=2):
    """
    依据规则生成：黄金词（拆词建Exact）、继续测试、降价/否定，
    并产出 SKAG 建组建议、否定清单（Exact / Phrase Roots 从 config.yaml）
    """
    df = _ensure_metrics(df_all_terms)

    # 决策标签
    cond_pass = (df["clicks"] >= min_clicks) & (df["orders"] >= min_orders) & (df["acos"] <= target_acos)
    cond_test = (df["clicks"] >= min_clicks) & (
        (df["orders"] < min_orders) | ((df["acos"] > target_acos) & (df["acos"] <= target_acos + 0.10))
    )
    cond_fail = (df["clicks"] >= min_clicks) & (df["orders"] < min_orders) & (df["acos"] > target_acos + 0.10)

    df_dec = df.copy()
    df_dec["decision"] = pd.Series("样本不足", index=df_dec.index)
    df_dec.loc[cond_pass, "decision"] = "拆词建Exact"
    df_dec.loc[cond_test, "decision"] = "继续测试"
    df_dec.loc[cond_fail, "decision"] = "降价/否定"

    df_pass = df_dec[df_dec["decision"]=="拆词建Exact"].sort_values(["acos","clicks"], ascending=[True, False])
    df_test = df_dec[df_dec["decision"]=="继续测试"].sort_values(["clicks"], ascending=False)
    df_fail = df_dec[df_dec["decision"]=="降价/否定"].sort_values(["acos","clicks"], ascending=[False, False])

    # SKAG 建组建议表（Exact 单词组）
    rows = []
    for _, r in df_pass.iterrows():
        kw = str(r.get("search_term","")).strip()
        base_cpc = r.get("cpc", 0.3) or 0.3
        start_bid = max(0.05, round(base_cpc * 1.00, 2))  # 初始出价 = 最近期平均CPC * 1.0
        rows.append({
            "Campaign Name": "Exact - SKAG - Core",
            "Ad Group Name": f"Exact - {kw[:70]}",
            "Match Type": "Exact",
            "Keyword": kw,
            "Start Bid": start_bid,
            "Top of Search Adj": "30%~50%",   # 增长型·中等档
            "Reason": "黄金词拆分，独立冲量"
        })
    df_skag = pd.DataFrame(rows)

    # 否定：点击≥min_clicks 且 无单 或 ACOS>50%
    neg_exact = df_dec[
        (df_dec["clicks"] >= min_clicks) & ((df_dec["orders"] == 0) | (df_dec["acos"] > 0.50))
    ].copy()
    neg_exact = neg_exact.rename(columns={"search_term":"Negative Term"})
    if "Negative Term" in neg_exact.columns:
        neg_exact["Match Type"] = "Negative Exact"
    neg_exact = neg_exact[["Negative Term","clicks","orders","spend","sales","acos","Match Type"]]

    # ⬇️ 从 config.yaml 读取否定词根
    phrase_roots = []
    try:
        phrase_roots = cfg.get("negatives_scan", {}).get("phrase_roots", [])
    except Exception:
        pass
    df_neg_phrase_roots = pd.DataFrame({"Negative Phrase Root": phrase_roots})

    return df_dec, df_pass, df_test, df_fail, df_skag, neg_exact, df_neg_phrase_roots

def _export_v11_excel(df_all, df_pass, df_test, df_fail, df_skag, df_neg_exact, df_neg_phrase):
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
        df_all.to_excel(writer, sheet_name="All_Analyzed", index=False)
        df_pass.to_excel(writer, sheet_name="To_Exact_Split", index=False)
        df_test.to_excel(writer, sheet_name="Keep_Testing", index=False)
        df_fail.to_excel(writer, sheet_name="BidDown_or_Neg", index=False)
        df_skag.to_excel(writer, sheet_name="SKAG_Plan", index=False)
        df_neg_exact.to_excel(writer, sheet_name="Neg_Exact", index=False)
        df_neg_phrase.to_excel(writer, sheet_name="Neg_Phrase_Roots", index=False)
    buffer.seek(0)
    return buffer


# =========================
# 页面开始（保留你原有的内容）
# =========================
st.title("📊 SZZ Amazon 广告优化工具（早期否定 & 词库建议 + v1.1 行动表）")
st.markdown("""
📥 **请先从 Seller Central 导出 Sponsored Products · Search Term Report（30–60天）** 后上传。  
运行流程：**接收 → 解析 → 分析 → 导出**。在数据很少/无转化时，也会给出**早期否定词建议**与**词库更新建议**。  
🆕 v1.1：新增 **“提价 ➝ 拆词（专业版）/ SKAG 建组 / 否定清单 / 一键导出”**（否定词根从 `config.yaml` 读取）。
""")

uploaded_file = st.file_uploader("📤 上传 Search Term Report（CSV 或 XLSX）", type=["csv", "xlsx"])

if uploaded_file:
    st.caption(f"文件：**{uploaded_file.name}** | 大小约 **{len(uploaded_file.getbuffer())/1024/1024:.2f} MB**")

    with st.status("准备处理上传的文件…", state="running") as status:
        # ── 阶段 1：接收（写入临时文件，带进度条） ──
        st.write("📦 正在接收文件（写入服务器）…")
        buf = uploaded_file.getbuffer()
        total = len(buf)
        recv_prog = st.progress(0, text="接收进度 0%")
        chunk = 1024 * 1024  # 1MB
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name
            for i in range(0, total, chunk):
                end = min(i + chunk, total)
                tmp.write(buf[i:end])
                pct = int(end / total * 100) if total else 100
                recv_prog.progress(min(pct, 100), text=f"接收进度 {pct}%")
                time.sleep(0.005)
        st.write("✅ 文件接收完成")
        recv_prog.empty()

        # ── 阶段 2：解析 ──
        st.write("📖 正在解析文件（CSV/XLSX）…")
        parse_prog = st.progress(0, text="开始解析…")
        try:
            if uploaded_file.name.lower().endswith(".csv"):
                df = pd.read_csv(tmp_path)
            else:
                df = pd.read_excel(tmp_path)
            parse_prog.progress(100, text="解析完成 100%")
            st.write("🔎 原始列名：", list(df.columns))
            st.write("📊 数据预览（前 10 行）")
            st.dataframe(df.head(10))
        except Exception as e:
            parse_prog.empty()
            status.update(label=f"❌ 解析失败：{e}", state="error")
            st.stop()
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass

        # ── 阶段 3：分析（沿用你原有 calculate_metrics） ──
        st.write("🧠 正在分析（计算 CTR/CVR/ACOS；分类建议；早期否定；词库建议）…")
        analyze_prog = st.progress(0, text="载入配置…")
        try:
            cfg = load_config()  # 读取 config.yaml
            analyze_prog.progress(30, text="指标计算 & 规则分类…")
            results = calculate_metrics(df, cfg)
            analyze_prog.progress(100, text="分析完成")
            st.write("✅ 分析完成")
        except Exception as e:
            analyze_prog.empty()
            status.update(label=f"❌ 分析失败：{e}", state="error")
            st.stop()

        # ── 阶段 4：展示你原有的输出 ──
        today_str = datetime.now().strftime("%Y-%m-%d")

        st.subheader("📈 汇总与明细（All_Terms）")
        st.dataframe(results["All_Terms"].head(300))

        col1, col2 = st.columns(2)
        with col1:
            st.subheader("🟢 Scale Up（低 ACOS & 有转化）")
            st.dataframe(results["Scale_Up"].head(200))
            st.subheader("🟡 Bid Down（高 ACOS & 点击足够）")
            st.dataframe(results["Bid_Down"].head(200))
        with col2:
            st.subheader("❌ Negatives（点击多且无单）")
            st.dataframe(results["Negatives"].head(200))
            st.subheader("🌱 Harvest（高转化率）")
            st.dataframe(results["Harvest"].head(200))

        # 早期否定（低数据量也能输出建议）
        st.subheader("🧭 早期否定词（含来源与理由）")
        st.caption("说明：当样本很少/无转化时，基于词根库识别潜在无效词供你审核。")
        st.dataframe(results["Early_Negatives_Source"].head(300))
        if not results["Early_Negatives_Source"].empty:
            src_csv = results["Early_Negatives_Source"].to_csv(index=False).encode("utf-8")
            st.download_button(
                f"⬇️ 下载_否定词来源追踪_{today_str}.csv",
                src_csv,
                file_name=f"early_negatives_source_{today_str}.csv",
                mime="text/csv",
                use_container_width=True
            )

        st.subheader("📤 早期否定词上传草稿（negative exact）")
        st.caption("可直接粘贴/上传到后台；也可按源活动就地否定。")
        st.dataframe(results["Early_Negatives_Upload"].head(300))
        if not results["Early_Negatives_Upload"].empty:
            up_csv = results["Early_Negatives_Upload"].to_csv(index=False).encode("utf-8")
            st.download_button(
                f"⬇️ 下载_否定词上传草稿_{today_str}.csv",
                up_csv,
                file_name=f"early_negatives_upload_{today_str}.csv",
                mime="text/csv",
                use_container_width=True
            )

        # 词库建议
        st.subheader("🧩 词库更新建议（低数据量适用）")
        st.caption("从无转化/低质量搜索词中提炼高频词根，与有转化词对比，给出建议。ADD_TO_PATTERNS 可直接加入 config.yaml 的 negatives_scan.patterns。")
        lex = results["Lexicon_Suggestions"]
        st.dataframe(lex)
        if not lex.empty:
            csv = lex.to_csv(index=False).encode("utf-8")
            st.download_button(
                f"⬇️ 下载_词库建议_{today_str}.csv",
                csv, file_name=f"lexicon_suggestions_{today_str}.csv",
                mime="text/csv", use_container_width=True
            )
            add_list = lex.loc[lex["Recommendation"]=="ADD_TO_PATTERNS","Token"].tolist()
            if add_list:
                st.markdown("**📋 建议加入到 `negatives_scan.patterns.UNRELATED_CONTEXT`（示例）**")
                yaml_block = "- " + "\n- ".join(add_list)
                st.code(yaml_block, language="yaml")
                st.caption("把上面列表粘到 config.yaml 对应的 patterns 分类下（或按你的分类拆分）。")

        # =========================
        # v1.1 新增面板：提价 ➝ 拆词 / SKAG / 否定 / 一键导出
        # =========================
        st.markdown("---")
        st.header("📈 v1.1 提价 ➝ 拆词（专业版） · SKAG 建组 · 否定清单 · 导出")
        st.caption("根据目标 ACOS、最少点击、最少订单，自动判定黄金词/继续测试/降价否定，并生成 SKAG 建组与否定词清单（否定词根来自 config.yaml）。")

        with st.expander("⚙️ 判定阈值（可调整）", expanded=False):
            target_acos = st.slider("🎯 目标 ACOS", 0.10, 0.60, 0.30, 0.01)
            min_clicks  = st.number_input("🔎 最少点击（进入判断）", 5, 200, 20, 1)
            min_orders  = st.number_input("📦 最少订单（黄金词门槛）", 1, 10, 2, 1)

        # 使用你 pipeline 的 All_Terms 作为基础（若没有则回退用原 df）
        base_df_for_v11 = results.get("All_Terms", df)
        df_all, df_pass, df_test, df_fail, df_skag, df_neg_exact, df_neg_phrase = _build_v11_decision_tables(
            base_df_for_v11, cfg, target_acos=target_acos, min_clicks=min_clicks, min_orders=min_orders
        )

        st.success(f"分析结果：✅ 拆词建Exact {len(df_pass)} 条 ｜ ⚠️ 继续测试 {len(df_test)} 条 ｜ ❌ 降价/否定 {len(df_fail)} 条")

        st.subheader("✅ 拆词建 Exact（黄金词）")
        cols_show = ["search_term","clicks","orders","spend","sales","acos","cpc","cvr"]
        if "campaign" in df_pass.columns: cols_show += ["campaign","ad_group"]
        st.dataframe(df_pass[cols_show], use_container_width=True)

        st.subheader("🧩 SKAG 建组建议（Exact 单词组）")
        st.dataframe(df_skag, use_container_width=True)

        st.subheader("❌ 否定词候选（Negative Exact）")
        st.dataframe(df_neg_exact, use_container_width=True)

        st.subheader("🧱 词根否定（Negative Phrase Roots / 来自 config.yaml）")
        st.dataframe(df_neg_phrase, use_container_width=True)

        # —— v1.1 专用导出 —— #
        today_compact = datetime.now().strftime("%Y%m%d")
        v11_excel = _export_v11_excel(df_all, df_pass, df_test, df_fail, df_skag, df_neg_exact, df_neg_phrase)
        st.download_button(
            label=f"📥 下载：ppc_actions_{today_compact}.xlsx",
            data=v11_excel,
            file_name=f"ppc_actions_{today_compact}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
        st.download_button(
            "📥 下载：skag_plan.csv",
            data=df_skag.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"skag_plan_{today_compact}.csv",
            mime="text/csv",
            use_container_width=True
        )
        st.download_button(
            "📥 下载：negatives_exact.csv",
            data=df_neg_exact.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"negatives_exact_{today_compact}.csv",
            mime="text/csv",
            use_container_width=True
        )
        st.download_button(
            "📥 下载：negatives_phrase_roots.csv",
            data=df_neg_phrase.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"negatives_phrase_roots_{today_compact}.csv",
            mime="text/csv",
            use_container_width=True
        )

        # ── 阶段 5：你的“总导出”（原有逻辑保留） ──
        st.markdown("---")
        st.write("📤 正在导出 Excel（全部结果合集） …")
        try:
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
                for name, df_out in results.items():
                    if isinstance(df_out, pd.DataFrame):
                        df_out.to_excel(writer, sheet_name=name[:31], index=False)
            st.download_button(
                "⬇️ 下载 Excel（全部结果）",
                data=output.getvalue(),
                file_name=f"ppc_output_{today_str}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
            status.update(label="🎉 全流程完成：接收 → 解析 → 分析 → 导出", state="complete")
        except Exception as e:
            status.update(label=f"❌ 导出失败：{e}", state="error")
            st.stop()

else:
    st.info("👆 请上传文件后，系统会显示处理进度。")
