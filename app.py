import streamlit as st
import pandas as pd
from ppc_optimizer_lib import load_config, calculate_metrics

st.set_page_config(page_title="Amazon PPC Optimizer", layout="wide")

st.title("📊 Amazon 广告优化工具")

st.markdown("""
📥 **请先从 Amazon Seller Central 导出 Sponsored Products 搜索词报告，然后上传文件进行分析。**

**获取报告的路径：**

1. 登录 [Amazon Seller Central](https://sellercentral.amazon.com/)  
2. 顶部菜单进入 **广告（Advertising） → 广告报告（Campaign Manager）**  
3. 在左侧菜单选择 **报告（Reports） → 广告报告（Advertising Reports）**  
4. 点击 **创建报告（Create Report）**  
5. 类型选择：**Sponsored Products（赞助商品）**  
6. 报表类型选择：**Search Term Report（搜索词报告）**  
7. 时间范围建议选择：**最近 30 天或 60 天**  
8. 报表生成后，点击 **下载（Download）**，得到 CSV 或 Excel 文件

📤 然后在下方上传该文件，系统会自动计算关键词表现，生成出价建议、否定词建议和收割词列表。
""")


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
