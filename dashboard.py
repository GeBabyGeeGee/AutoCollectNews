import streamlit as st
import pandas as pd
from sqlalchemy import create_engine # <-- 从 sqlalchemy 导入
from datetime import datetime, timedelta

# --- 1. 配置信息 ---
# 数据库连接现在通过 Streamlit Secrets 管理

# --- 函数：加载本地CSS文件 ---
def local_css(file_name):
    with open(file_name, "r", encoding="utf-8") as f: # <--- 解决方案：明确指定 encoding="utf-8"
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)


# --- 2. 数据加载 ---
@st.cache_data(ttl=300) # 每5分钟刷新一次数据
def load_data():
    try:
        # 从 Streamlit Secrets 安全地获取数据库连接信息
        db_url = st.secrets["connections"]["supabase"]["url"]
        engine = create_engine(db_url)
        
        df = pd.read_sql("SELECT * FROM news ORDER BY created_at DESC", engine)
        
        # --- 数据类型转换和清洗 ---
        df['created_at'] = pd.to_datetime(df['created_at'])
        df.fillna({
            'publish_date': '未知', 'author': '未知',
            'value_score': 0, 'value_reason': '未评估',
            'sub_category': '未分类', 'category': '未分类'
        }, inplace=True)
        df['value_score'] = df['value_score'].astype(int)
        return df
    except Exception as e:
        # 捕获secrets未配置的错误，提供友好提示
        if "connections.supabase" in str(e):
             st.error("数据库连接信息未配置。请在Streamlit Cloud的Secrets中设置 `[connections.supabase]`。")
        else:
            st.error(f"加载云数据库失败: {e}")
        return pd.DataFrame()

# --- 3. 页面布局与标题 ---
st.set_page_config(page_title="个护行业智能情报库", layout="wide")

# 加载自定义CSS
local_css("style.css")

st.title("💡 个护行业智能情报库")
st.markdown("<sub>由AI评估运营价值，助您快速锁定核心动态</sub>", unsafe_allow_html=True)

df = load_data()

if df.empty:
    st.warning("数据库中还没有数据，或数据加载失败。")
else:
    # --- 4. 侧边栏筛选与排序 ---
    st.sidebar.header("筛选与排序")
    
    # 排序
    sort_by = st.sidebar.selectbox(
        "**排序方式**",
        ["录入时间 (由新到旧)", "运营价值 (由高到低)"]
    )

    # 文本搜索
    search_term = st.sidebar.text_input("搜索标题、摘要或关键词")

    # 品类筛选
    sub_categories = ['全部'] + sorted(df['sub_category'].unique().tolist())
    selected_sub_category = st.sidebar.selectbox("按产品/主题筛选", sub_categories)
    
    # 类别筛选
    categories = ['全部'] + sorted(df['category'].unique().tolist())
    selected_category = st.sidebar.selectbox("按信息类别筛选", categories)
    
    # 运营价值分数筛选
    st.sidebar.markdown("**按运营价值筛选**")
    score_range = st.sidebar.slider(
        "选择价值分数范围", 0, 10, (0, 10)
    )

    # 录入时间筛选
    st.sidebar.markdown("**按录入时间筛选**")
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=30)
    date_range = st.sidebar.date_input(
        "选择日期范围", [start_date, end_date]
    )


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

    filtered_df = filtered_df[
        (filtered_df['value_score'] >= score_range[0]) & 
        (filtered_df['value_score'] <= score_range[1])
    ]
    
    if len(date_range) == 2:
        start_date_dt = pd.to_datetime(date_range[0])
        end_date_dt = pd.to_datetime(date_range[1]) + timedelta(days=1)
        filtered_df = filtered_df[
            (filtered_df['created_at'] >= start_date_dt) & 
            (filtered_df['created_at'] < end_date_dt)
        ]

    if sort_by == "运营价值 (由高到低)":
        filtered_df = filtered_df.sort_values(by='value_score', ascending=False)
    # 默认按录入时间排序，因为数据加载时已经排好序

    st.write(f"共找到 **{len(filtered_df)}** 条相关情报")
    st.divider()

    # --- 6. 主页面展示 (*** 全新卡片式布局 ***) ---
    for index, row in filtered_df.iterrows():
        # 使用 st.container 创建每条新闻的独立卡片
        with st.container():
            col1, col2 = st.columns([4, 1]) # 左侧宽，右侧窄

            with col1: # 左侧内容
                st.markdown(f"#### {row['title']}")
                
                # 标签区域
                st.markdown(
                    f"""
                    <span style='background-color:#E9ECEF; color:#495057; padding:3px 8px; border-radius:15px; font-size: 14px; margin-right: 10px;'>
                        🏷️ {row['sub_category']}
                    </span>
                    <span style='background-color:#E0F7FA; color:#006064; padding:3px 8px; border-radius:15px; font-size: 14px;'>
                        📂 {row['category']}
                    </span>
                    """, unsafe_allow_html=True
                )
                
                # 来源和时间
                created_time = row['created_at'].strftime('%Y-%m-%d %H:%M')
                st.caption(f"来源: {row['source']} | 发布时间: {row['publish_date']} | 录入时间: {created_time}")

            with col2: # 右侧内容
                score = row['value_score']
                delta_color = "normal"
                if score >= 8: delta_text, emoji = "高价值", "🔥"
                elif score >= 5: delta_text, emoji = "中等价值", "💡"
                else: delta_text, emoji = "一般价值", "📄"
                
                st.metric(
                    label="运营价值评估", 
                    value=f"{score}/10 {emoji}", 
                    delta=delta_text,
                    delta_color=delta_color
                )

            # AI 评估理由和展开详情
            st.info(f"**AI评估理由:** {row['value_reason']}")
            
            with st.expander("查看AI摘要、关键词及原文链接"):
                st.markdown(f"**AI摘要**: {row['summary']}")
                st.markdown(f"**关键词**: *{row['keywords']}*")
                st.markdown(f"**原文链接**: [{row['url']}]({row['url']})")
