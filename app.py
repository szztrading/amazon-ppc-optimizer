import streamlit as st
import pandas as pd
from ppc_optimizer_lib import load_config, calculate_metrics

st.set_page_config(page_title="Amazon PPC Optimizer", layout="wide")

st.title("📊 Amazon 广告优化工具")
st.write("上传 Sponsored Products 搜索词报告，自动生成关键词优化建议。")

# 上传报表
uploaded_file = st.file_uploader("📤 上传 Search Term Report (CSV 或 Excel)", type=["csv", "xlsx"])

if uploaded_file:
    if uploaded_file.name.endswith(".csv"):
        df = pd.read_csv(uploaded_file)
    else:
        df = pd.read_excel(uploaded_file)

    # 加载配置
    config = load_config()

    # 计算指标
    results = calculate_metrics(df, config)

    st.subheader("📈 优化结果")

    for sheet_name, result_df in results.items():
        st.write(f"### 📊 {sheet_name}")
        st.dataframe(result_df.head(50))

    # 下载结果
    with pd.ExcelWriter("ppc_output.xlsx") as writer:
        for name, result_df in results.items():
            result_df.to_excel(writer, sheet_name=name, index=False)

    with open("ppc_output.xlsx", "rb") as f:
        st.download_button("⬇️ 下载优化结果 Excel", f, file_name="ppc_output.xlsx")

else:
    st.info("请上传 Amazon 搜索词报告文件。")
