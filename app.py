# app.py — Amazon PPC Optimizer（含上传进度、早期否定扫描、词库建议）
import os, io, time, tempfile
import pandas as pd
import streamlit as st
from datetime import datetime

from ppc_optimizer_lib import load_config, calculate_metrics

st.set_page_config(page_title="Amazon PPC Optimizer", layout="wide")

st.title("📊 Amazon 广告优化工具（带早期否定 & 词库建议）")
st.markdown("""
📥 **请先从 Seller Central 导出 Sponsored Products · Search Term Report（30–60天）** 后上传。  
运行流程：**接收 → 解析 → 分析 → 导出**。在数据很少/无转化时，也会给出**早期否定词建议**与**词库更新建议**。
""")

uploaded_file = st.file_uploader("📤 上传 Search Term Report（CSV 或 XLSX）", type=["csv", "xlsx"])

if uploaded_file:
    st.caption(f"文件：**{uploaded_file.name}** | 大小约 **{len(uploaded_file.getbuffer())/1024/1024:.2f} MB**")

    with st.status("准备处理上传的文件…", state="running") as status:
        # ── 阶段 1：接收（写入临时文件，模拟网络进度） ──
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

        # ── 阶段 3：分析 ──
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

        # ── 阶段 4：展示 & 导出 ──
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

        # 总导出（Excel 打包各表）
        st.write("📤 正在导出 Excel …")
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
