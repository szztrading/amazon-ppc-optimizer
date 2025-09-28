# app.py — 带可视化进度的上传与处理流程
import os, io, time, math, tempfile
import pandas as pd
import streamlit as st
from ppc_optimizer_lib import load_config, calculate_metrics  # 保持与你现有库一致

st.set_page_config(page_title="Amazon PPC Optimizer", layout="wide")

st.title("📊 Amazon 广告优化工具（带进度显示）")
st.markdown("""
📥 **先从 Seller Central 导出 Sponsored Products 搜索词报告**（Search Term Report，30–60 天），再上传文件。  
获取路径（新版界面）：**Advertising → Campaign Manager → Reports/Measurement → Advertising reports → Create report → Sponsored Products → Search term**  
上传后会显示处理进度：**接收 → 解析 → 分析 → 导出**。
""")

uploaded_file = st.file_uploader("📤 上传 Search Term Report（CSV 或 XLSX）", type=["csv", "xlsx"])

if uploaded_file:
    st.caption(f"文件：**{uploaded_file.name}** | 大小约 **{len(uploaded_file.getbuffer())/1024/1024:.2f} MB**")

    # 状态面板
    with st.status("准备处理上传的文件…", state="running") as status:
        # ─── 阶段 1：接收（写入临时文件，模拟“上传进度”） ───
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
                # 轻微 sleep 仅为更顺滑的 UI 体验（可去掉）
                time.sleep(0.01)
        st.write("✅ 文件接收完成")
        recv_prog.empty()

        # ─── 阶段 2：解析 ───
        st.write("📖 正在解析文件（CSV/XLSX）…")
        parse_prog = st.progress(0, text="开始解析…")
        try:
            if uploaded_file.name.lower().endswith(".csv"):
                df = pd.read_csv(tmp_path)
            else:
                df = pd.read_excel(tmp_path)
            parse_prog.progress(100, text="解析完成 100%")
            st.write("🔎 数据预览（前 10 行）")
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

        # ─── 阶段 3：分析（你的业务逻辑） ───
        st.write("🧠 正在分析（计算 CTR/CVR/ACOS，生成建议）…")
        analyze_prog = st.progress(0, text="载入配置…")
        try:
            cfg = load_config()               # 读取 config.yaml
            analyze_prog.progress(30, text="计算基础指标…")
            time.sleep(0.02)

            results = calculate_metrics(df, cfg)  # 你的现有函数：返回 dict
            analyze_prog.progress(70, text="生成分类建议…")
            time.sleep(0.02)

            analyze_prog.progress(100, text="分析完成")
            st.write("✅ 分析完成")
        except Exception as e:
            analyze_prog.empty()
            status.update(label=f"❌ 分析失败：{e}", state="error")
            st.stop()

        # ─── 阶段 4：导出（Excel & 否定词 CSV） ───
        st.write("📤 正在导出结果…")
        export_prog = st.progress(0, text="导出 Excel…")
        try:
            # 导出 Excel（包含所有 Sheet）
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
                for name, result_df in results.items():
                    # 确保是 DataFrame
                    if isinstance(result_df, pd.DataFrame):
                        result_df.to_excel(writer, sheet_name=name[:31], index=False)
            export_prog.progress(60, text="生成 Excel 完成")

            # 可选：导出否定词 CSV（如果你的字典里有 Negatives）
            negatives_csv = None
            if "Negatives" in results and isinstance(results["Negatives"], pd.DataFrame):
                neg_df = results["Negatives"].copy()
                # 尝试寻找搜索词字段名（兼容常见命名）
                for col in ["search_term", "Customer Search Term", "Search term"]:
                    if col in neg_df.columns:
                        neg_col = col
                        break
                else:
                    neg_col = None

                if neg_col:
                    neg_upload = pd.DataFrame({
                        "Campaign Name": "",          # 可手动填或后续扩展自动填充
                        "Ad Group Name": "",
                        "Negative Keyword": neg_df[neg_col],
                        "Match Type": "negative exact",
                    })
                    negatives_csv = neg_upload.to_csv(index=False).encode("utf-8")

            export_prog.progress(100, text="导出完成")

            # 下载按钮
            st.download_button(
                "⬇️ 下载 Excel（建议动作清单）",
                data=output.getvalue(),
                file_name="ppc_output.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
            if negatives_csv:
                st.download_button(
                    "⬇️ 下载否定词 CSV（可直接上传）",
                    data=negatives_csv,
                    file_name="negatives_upload.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

            status.update(label="🎉 全流程完成：接收 → 解析 → 分析 → 导出", state="complete")
        except Exception as e:
            export_prog.empty()
            status.update(label=f"❌ 导出失败：{e}", state="error")
            st.stop()

else:
    st.info("👆 请上传文件后，系统会显示处理进度。")
