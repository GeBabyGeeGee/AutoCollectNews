import streamlit as st
import pandas as pd
import sqlite3

# --- 1. 配置信息 ---
DB_FILE = "News.db"

# --- 2. 数据加载 ---
@st.cache_data(ttl=300) # 每5分钟刷新一次数据
def load_data():
    try:
        conn = sqlite3.connect(DB_FILE)
        df = pd.read_sql_query("SELECT * FROM news", conn)
        conn.close()
        # 填充空值以避免显示错误
        df.fillna({
            'publish_date': '未知', 'author': '未知',
            'value_score': 0, 'value_reason': '未评估'
        }, inplace=True)
        # 确保分数字段是整数类型
        df['value_score'] = df['value_score'].astype(int)
        return df
    except Exception as e:
        st.error(f"加载数据库 '{DB_FILE}' 失败: {e}")
        return pd.DataFrame()

# --- 3. 页面布局与标题 ---
st.set_page_config(page_title="个护行业智能情报库", layout="wide")
st.title("个护行业智能情报库 💡")
st.markdown("<sub>由AI评估运营价值，助您快速锁定核心动态</sub>", unsafe_allow_html=True)

df = load_data()

if df.empty:
    st.warning(f"数据库 '{DB_FILE}' 中还没有数据。请先运行 `main.py` 脚本采集数据。")
else:
    # --- 4. 侧边栏筛选与排序 ---
    st.sidebar.header("筛选与排序")
    
    sort_by = st.sidebar.selectbox(
        "**排序方式**",
        ["运营价值 (由高到低)", "录入时间 (由新到旧)"]
    )

    sub_categories = ['全部'] + sorted(df['sub_category'].unique().tolist())
    selected_sub_category = st.sidebar.selectbox("按产品品类筛选", sub_categories)
    
    available_categories = sorted(df['category'].unique().tolist())
    categories = ['全部'] + available_categories
    selected_category = st.sidebar.selectbox("按信息类别筛选", categories)
    search_term = st.sidebar.text_input("搜索标题或摘要")

    # --- 5. 数据筛选与排序逻辑 ---
    filtered_df = df.copy()
    if selected_sub_category != '全部':
        filtered_df = filtered_df[filtered_df['sub_category'] == selected_sub_category]
    if selected_category != '全部':
        filtered_df = filtered_df[filtered_df['category'] == selected_category]
    if search_term:
        mask = (
            filtered_df['title'].str.contains(search_term, case=False, na=False) |
            filtered_df['summary'].str.contains(search_term, case=False, na=False) |
            filtered_df['keywords'].str.contains(search_term, case=False, na=False)
        )
        filtered_df = filtered_df[mask]

    if sort_by == "运营价值 (由高到低)":
        filtered_df = filtered_df.sort_values(by='value_score', ascending=False)
    else:
        filtered_df = filtered_df.sort_values(by='created_at', ascending=False)
    
    st.write(f"共找到 **{len(filtered_df)}** 条相关情报")

    # --- 6. 主页面展示 ---
    for index, row in filtered_df.iterrows():
        score = row['value_score']
        
        if score >= 8: color, emoji = "#28a745", "🔥🔥🔥" # 高价值
        elif score >= 5: color, emoji = "#007bff", "💡" # 中等价值
        else: color, emoji = "#6c757d", "📄" # 低价值

        st.markdown(f"#### {row['title']}")

        score_html = f"<span style='color: white; background-color: {color}; padding: 5px 10px; border-radius: 15px; font-weight: bold;'>运营价值: {score}/10 {emoji}</span>"
        st.markdown(score_html, unsafe_allow_html=True)
        st.info(f"**AI评估理由:** {row['value_reason']}")

        sub_cat_tag = f"<span style='background-color:#E9ECEF; color:#495057; padding:3px 8px; border-radius:5px; margin-right: 10px;'>{row['sub_category']}</span>"
        cat_tag = f"<span style='background-color:#E0F7FA; color:#006064; padding:3px 8px; border-radius:5px;'>{row['category']}</span>"
        st.markdown(f"<div style='margin-top: 10px;'>{sub_cat_tag} {cat_tag}</div>", unsafe_allow_html=True)
        
        with st.expander("查看摘要、来源及更多信息"):
            created_time = pd.to_datetime(row['created_at']).strftime('%Y-%m-%d %H:%M')
            st.markdown(f"**来源**: {row['source']} | **发布时间**: {row['publish_date']} | **录入时间**: {created_time}")
            st.markdown(f"**AI摘要**: {row['summary']}")
            st.markdown(f"**关键词**: *{row['keywords']}*")
            st.markdown(f"**原文链接**: [{row['url']}]({row['url']})")

        st.divider()
