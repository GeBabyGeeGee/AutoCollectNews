import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
import json

# --- 页面配置 ---
st.set_page_config(
    page_title="个护行业智能情报分析平台",
    page_icon="💡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- 常量定义 ---
DB_FILE = "news.db"  # 与主程序保持一致
CACHE_TTL = 300  # 5分钟缓存

# --- 自定义CSS ---
def load_custom_css():
    """加载自定义CSS样式"""
    css = """
    <style>
    /* 主容器样式 */
    .main {
        padding: 0rem 1rem;
    }
    
    /* 卡片样式 */
    .article-card {
        background-color: #f8f9fa;
        padding: 1.5rem;
        border-radius: 10px;
        margin-bottom: 1rem;
        border: 1px solid #e9ecef;
        transition: all 0.3s ease;
    }
    
    .article-card:hover {
        box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        transform: translateY(-2px);
    }
    
    /* 标签样式 */
    .tag {
        display: inline-block;
        padding: 0.25rem 0.75rem;
        margin-right: 0.5rem;
        border-radius: 15px;
        font-size: 0.875rem;
        font-weight: 500;
    }
    
    .tag-subcategory {
        background-color: #e3f2fd;
        color: #1565c0;
    }
    
    .tag-category {
        background-color: #f3e5f5;
        color: #7b1fa2;
    }
    
    /* 价值评分样式 */
    .value-score-high {
        color: #d32f2f;
        font-weight: bold;
    }
    
    .value-score-medium {
        color: #f57c00;
        font-weight: bold;
    }
    
    .value-score-low {
        color: #388e3c;
    }
    
    /* 摘要框样式 */
    .summary-box {
        background-color: #e8f4f8;
        padding: 1rem;
        border-radius: 8px;
        margin: 1rem 0;
        border-left: 4px solid #1976d2;
    }
    
    /* 关键词样式 */
    .keyword {
        background-color: #fff3cd;
        color: #856404;
        padding: 0.2rem 0.5rem;
        border-radius: 3px;
        margin-right: 0.3rem;
        font-size: 0.9rem;
    }
    
    /* 统计卡片样式 */
    .stat-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 1.5rem;
        border-radius: 10px;
        text-align: center;
    }
    
    .stat-number {
        font-size: 2rem;
        font-weight: bold;
    }
    
    .stat-label {
        font-size: 0.9rem;
        opacity: 0.9;
    }
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)

# --- 数据库管理类 ---
class DatabaseManager:
    """数据库管理类"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        
    def check_database_exists(self) -> bool:
        """检查数据库文件是否存在"""
        return Path(self.db_path).exists()
    
    @st.cache_data(ttl=CACHE_TTL)
    def load_articles(_self) -> pd.DataFrame:
        """加载文章数据"""
        if not _self.check_database_exists():
            return pd.DataFrame()
            
        try:
            with sqlite3.connect(_self.db_path) as conn:
                query = """
                SELECT 
                    id, title, url, source, publish_date, author,
                    sub_category, category, summary, keywords,
                    value_score, value_reason, created_at
                FROM articles
                ORDER BY created_at DESC
                """
                df = pd.read_sql_query(query, conn)
                
                # 数据类型转换和清洗
                df['created_at'] = pd.to_datetime(df['created_at'])
                df['publish_date'] = pd.to_datetime(df['publish_date'], errors='coerce')
                
                # 填充缺失值
                df.fillna({
                    'author': '未知',
                    'value_score': 0,
                    'value_reason': '未评估',
                    'sub_category': '未分类',
                    'category': '未分类',
                    'keywords': ''
                }, inplace=True)
                
                df['value_score'] = df['value_score'].astype(int)
                
                return df
                
        except Exception as e:
            st.error(f"加载数据库失败: {e}")
            return pd.DataFrame()
    
    @st.cache_data(ttl=CACHE_TTL)
    def get_statistics(_self, df: pd.DataFrame) -> dict:
        """计算统计数据"""
        if df.empty:
            return {}
            
        stats = {
            'total_articles': len(df),
            'high_value_articles': len(df[df['value_score'] >= 70]),
            'articles_today': len(df[df['created_at'].dt.date == datetime.now().date()]),
            'articles_this_week': len(df[df['created_at'] >= datetime.now() - timedelta(days=7)]),
            'avg_value_score': df['value_score'].mean(),
            'top_categories': df['category'].value_counts().head(3).to_dict(),
            'top_subcategories': df['sub_category'].value_counts().head(5).to_dict(),
            'articles_by_date': df.groupby(df['created_at'].dt.date).size().to_dict(),
            'score_distribution': df['value_score'].value_counts().sort_index().to_dict()
        }
        
        return stats

# --- UI组件函数 ---
def render_statistics_dashboard(stats: dict):
    """渲染统计仪表板"""
    if not stats:
        st.warning("暂无统计数据")
        return
        
    # 第一行：核心指标
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.markdown("""
        <div class="stat-card">
            <div class="stat-number">{}</div>
            <div class="stat-label">总情报数</div>
        </div>
        """.format(stats['total_articles']), unsafe_allow_html=True)
    
    with col2:
        st.markdown("""
        <div class="stat-card" style="background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);">
            <div class="stat-number">{}</div>
            <div class="stat-label">高价值情报</div>
        </div>
        """.format(stats['high_value_articles']), unsafe_allow_html=True)
    
    with col3:
        st.markdown("""
        <div class="stat-card" style="background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);">
            <div class="stat-number">{}</div>
            <div class="stat-label">今日新增</div>
        </div>
        """.format(stats['articles_today']), unsafe_allow_html=True)
    
    with col4:
        avg_score = stats.get('avg_value_score', 0)
        st.markdown("""
        <div class="stat-card" style="background: linear-gradient(135deg, #fa709a 0%, #fee140 100%);">
            <div class="stat-number">{:.1f}</div>
            <div class="stat-label">平均价值分</div>
        </div>
        """.format(avg_score), unsafe_allow_html=True)
    
    st.markdown("---")
    
    # 第二行：图表
    col1, col2 = st.columns(2)
    
    with col1:
        # 文章时间趋势图
        if stats['articles_by_date']:
            dates = list(stats['articles_by_date'].keys())
            counts = list(stats['articles_by_date'].values())
            
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=dates, y=counts,
                mode='lines+markers',
                name='文章数量',
                line=dict(color='#1976d2', width=2),
                marker=dict(size=8)
            ))
            
            fig.update_layout(
                title="情报收集趋势",
                xaxis_title="日期",
                yaxis_title="文章数量",
                height=300,
                showlegend=False,
                hovermode='x unified'
            )
            
            st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        # 价值分布饼图
        if stats['score_distribution']:
            # 将分数分组
            score_groups = {'高价值 (≥70)': 0, '中价值 (40-69)': 0, '低价值 (<40)': 0}
            for score, count in stats['score_distribution'].items():
                if score >= 70:
                    score_groups['高价值 (≥70)'] += count
                elif score >= 40:
                    score_groups['中价值 (40-69)'] += count
                else:
                    score_groups['低价值 (<40)'] += count
            
            fig = go.Figure(data=[go.Pie(
                labels=list(score_groups.keys()),
                values=list(score_groups.values()),
                hole=0.3,
                marker_colors=['#d32f2f', '#f57c00', '#388e3c']
            )])
            
            fig.update_layout(
                title="价值分布",
                height=300,
                showlegend=True
            )
            
            st.plotly_chart(fig, use_container_width=True)

def render_article_card(row):
    """渲染文章卡片"""
    # 价值评分样式
    score = row['value_score']
    if score >= 70:
        score_class = "value-score-high"
        emoji = "🔥"
    elif score >= 40:
        score_class = "value-score-medium"
        emoji = "💡"
    else:
        score_class = "value-score-low"
        emoji = "📄"
    
    # 发布时间处理
    publish_date_str = "未知"
    if pd.notna(row['publish_date']) and row['publish_date'] != '未知':
        try:
            publish_date_str = pd.to_datetime(row['publish_date']).strftime('%Y-%m-%d')
        except:
            publish_date_str = str(row['publish_date'])
    
    created_time = row['created_at'].strftime('%Y-%m-%d %H:%M')
    
    # 渲染卡片
    st.markdown(f"""
    <div class="article-card">
        <div style="display: flex; justify-content: space-between; align-items: start;">
            <div style="flex: 1;">
                <h4 style="margin: 0 0 0.5rem 0;">{row['title']}</h4>
                <div style="margin-bottom: 0.5rem;">
                    <span class="tag tag-subcategory">{row['sub_category']}</span>
                    <span class="tag tag-category">{row['category']}</span>
                </div>
                <p style="color: #6c757d; font-size: 0.875rem; margin: 0.5rem 0;">
                    📰 {row['source']} | 📅 发布: {publish_date_str} | ⏰ 收录: {created_time}
                </p>
            </div>
            <div style="text-align: center; min-width: 120px;">
                <div class="{score_class}" style="font-size: 2rem;">{score}/100 {emoji}</div>
                <div style="color: #6c757d; font-size: 0.875rem;">运营价值</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # AI评估理由
    if row['value_reason']:
        st.info(f"💭 **AI评估**: {row['value_reason']}")
    
    # 展开详情
    with st.expander("查看详细信息"):
        if row['summary']:
            st.markdown(f"""
            <div class="summary-box">
                <strong>摘要</strong><br>
                {row['summary']}
            </div>
            """, unsafe_allow_html=True)
        
        if row['keywords']:
            keywords_html = ' '.join([f'<span class="keyword">{kw.strip()}</span>' 
                                     for kw in row['keywords'].split(',') if kw.strip()])
            st.markdown(f"**关键词**: {keywords_html}", unsafe_allow_html=True)
        
        st.markdown(f"🔗 **原文链接**: [{row['url']}]({row['url']})")

def render_sidebar_filters(df):
    """渲染侧边栏筛选器"""
    st.sidebar.header("🔍 筛选与搜索")
    
    # 搜索框
    search_term = st.sidebar.text_input(
        "搜索",
        placeholder="输入关键词搜索标题、摘要或关键词...",
        help="支持模糊搜索"
    )
    
    # 排序方式
    sort_options = {
        "最新收录": "created_at",
        "价值评分": "value_score",
        "发布时间": "publish_date"
    }
    sort_by = st.sidebar.selectbox(
        "排序方式",
        options=list(sort_options.keys()),
        index=0
    )
    
    st.sidebar.markdown("---")
    
    # 分类筛选
    st.sidebar.subheader("📁 分类筛选")
    
    # 子分类
    sub_categories = ['全部'] + sorted(df['sub_category'].unique().tolist())
    selected_sub_category = st.sidebar.selectbox(
        "产品/品牌",
        sub_categories,
        help="按产品类型或品牌筛选"
    )
    
    # 信息类别
    categories = ['全部'] + sorted(df['category'].unique().tolist())
    selected_category = st.sidebar.selectbox(
        "信息类别",
        categories,
        help="按信息类型筛选"
    )
    
    st.sidebar.markdown("---")
    
    # 价值评分筛选
    st.sidebar.subheader("💰 价值筛选")
    score_range = st.sidebar.slider(
        "价值评分范围",
        min_value=0,
        max_value=100,
        value=(0, 100),
        step=10,
        help="筛选特定价值区间的情报"
    )
    
    # 快速筛选按钮
    col1, col2 = st.sidebar.columns(2)
    with col1:
        if st.button("高价值", use_container_width=True):
            score_range = (70, 100)
    with col2:
        if st.button("中价值", use_container_width=True):
            score_range = (40, 69)
    
    st.sidebar.markdown("---")
    
    # 时间筛选
    st.sidebar.subheader("📅 时间筛选")
    
    # 快速时间选项
    time_options = {
        "全部时间": 0,
        "今天": 1,
        "最近7天": 7,
        "最近30天": 30,
        "自定义": -1
    }
    
    selected_time = st.sidebar.selectbox(
        "时间范围",
        options=list(time_options.keys()),
        index=0
    )
    
    # 自定义时间范围
    if selected_time == "自定义":
        date_range = st.sidebar.date_input(
            "选择日期范围",
            value=[datetime.now().date() - timedelta(days=30), datetime.now().date()],
            max_value=datetime.now().date()
        )
    else:
        date_range = None
    
    return {
        'search_term': search_term,
        'sort_by': sort_options[sort_by],
        'sub_category': selected_sub_category,
        'category': selected_category,
        'score_range': score_range,
        'time_option': selected_time,
        'time_days': time_options[selected_time],
        'date_range': date_range
    }

def apply_filters(df, filters):
    """应用筛选条件"""
    filtered_df = df.copy()
    
    # 文本搜索
    if filters['search_term']:
        search_term = filters['search_term']
        mask = (
            filtered_df['title'].str.contains(search_term, case=False, na=False) |
            filtered_df['summary'].str.contains(search_term, case=False, na=False) |
            filtered_df['keywords'].str.contains(search_term, case=False, na=False)
        )
        filtered_df = filtered_df[mask]
    
    # 分类筛选
    if filters['sub_category'] != '全部':
        filtered_df = filtered_df[filtered_df['sub_category'] == filters['sub_category']]
    
    if filters['category'] != '全部':
        filtered_df = filtered_df[filtered_df['category'] == filters['category']]
    
    # 价值评分筛选
    filtered_df = filtered_df[
        (filtered_df['value_score'] >= filters['score_range'][0]) &
        (filtered_df['value_score'] <= filters['score_range'][1])
    ]
    
    # 时间筛选
    if filters['time_option'] == "自定义" and filters['date_range'] and len(filters['date_range']) == 2:
        start_date = pd.to_datetime(filters['date_range'][0])
        end_date = pd.to_datetime(filters['date_range'][1]) + timedelta(days=1)
        filtered_df = filtered_df[
            (filtered_df['created_at'] >= start_date) &
            (filtered_df['created_at'] < end_date)
        ]
    elif filters['time_days'] > 0:
        cutoff_date = datetime.now() - timedelta(days=filters['time_days'])
        filtered_df = filtered_df[filtered_df['created_at'] >= cutoff_date]
    
    # 排序
    if filters['sort_by'] == 'value_score':
        filtered_df = filtered_df.sort_values(by=['value_score', 'created_at'], ascending=[False, False])
    elif filters['sort_by'] == 'publish_date':
        filtered_df = filtered_df.sort_values(by='publish_date', ascending=False, na_position='last')
    else:  # created_at
        filtered_df = filtered_df.sort_values(by='created_at', ascending=False)
    
    return filtered_df

# --- 主程序 ---
def main():
    # 加载自定义CSS
    load_custom_css()
    
    # 标题和说明
    st.title("💡 个护行业智能情报分析平台")
    st.markdown(
        """
        <p style="font-size: 1.2rem; color: #666;">
        基于AI的行业情报收集与价值评估系统，助您快速锁定核心动态
        </p>
        """,
        unsafe_allow_html=True
    )
    
    # 初始化数据库管理器
    db_manager = DatabaseManager(DB_FILE)
    
    # 检查数据库是否存在
    if not db_manager.check_database_exists():
        st.error(f"❌ 数据库文件 `{DB_FILE}` 不存在")
        st.info("请先运行数据采集程序 `python main.py` 来收集情报数据")
        return
    
    # 加载数据
    df = db_manager.load_articles()
    
    if df.empty:
        st.warning("📭 数据库中暂无数据")
        return
    
    # 创建标签页
    tab1, tab2, tab3 = st.tabs(["📊 数据总览", "📰 情报列表", "📈 数据分析"])
    
    with tab1:
        # 显示统计仪表板
        stats = db_manager.get_statistics(df)
        render_statistics_dashboard(stats)
    
    with tab2:
        # 获取筛选条件
        filters = render_sidebar_filters(df)
        
        # 应用筛选
        filtered_df = apply_filters(df, filters)
        
        # 显示结果统计
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            st.markdown(f"### 🔍 找到 **{len(filtered_df)}** 条相关情报")
        with col2:
            # 导出按钮
            if st.button("📥 导出数据", use_container_width=True):
                csv = filtered_df.to_csv(index=False, encoding='utf-8-sig')
                st.download_button(
                    label="下载CSV文件",
                    data=csv,
                    file_name=f'情报导出_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv',
                    mime='text/csv'
                )
        
        st.markdown("---")
        
        # 显示文章列表
        if filtered_df.empty:
            st.info("😅 没有找到符合条件的情报，试试调整筛选条件？")
        else:
            # 分页显示
            items_per_page = 10
            total_pages = (len(filtered_df) - 1) // items_per_page + 1
            
            if 'page' not in st.session_state:
                st.session_state.page = 0
            
            # 分页控制
            col1, col2, col3, col4, col5 = st.columns([1, 1, 2, 1, 1])
            with col1:
                if st.button("⬅️ 首页", disabled=st.session_state.page == 0):
                    st.session_state.page = 0
            with col2:
                if st.button("◀️ 上一页", disabled=st.session_state.page == 0):
                    st.session_state.page -= 1
            with col3:
                st.markdown(f"<center>第 {st.session_state.page + 1} / {total_pages} 页</center>", unsafe_allow_html=True)
            with col4:
                if st.button("下一页 ▶️", disabled=st.session_state.page >= total_pages - 1):
                    st.session_state.page += 1
            with col5:
                if st.button("末页 ➡️", disabled=st.session_state.page >= total_pages - 1):
                    st.session_state.page = total_pages - 1
            
            # 显示当前页的文章
            start_idx = st.session_state.page * items_per_page
            end_idx = min(start_idx + items_per_page, len(filtered_df))
            
            for idx in range(start_idx, end_idx):
                render_article_card(filtered_df.iloc[idx])
                if idx < end_idx - 1:
                    st.markdown("---")
    
    with tab3:
        st.markdown("### 📈 数据分析")
        
        # 类别分布
        col1, col2 = st.columns(2)
        
        with col1:
            category_counts = df['category'].value_counts()
            fig = px.bar(
                x=category_counts.index,
                y=category_counts.values,
                labels={'x': '类别', 'y': '文章数量'},
                title='信息类别分布'
            )
            st.plotly_chart(fig, use_container_width=True)
        
        with col2:
            subcategory_counts = df['sub_category'].value_counts().head(10)
            fig = px.bar(
                x=subcategory_counts.values,
                y=subcategory_counts.index,
                orientation='h',
                labels={'x': '文章数量', 'y': '产品/品牌'},
                title='Top 10 产品/品牌'
            )
            st.plotly_chart(fig, use_container_width=True)
        
        # 价值分数分布热图
        st.markdown("---")
        st.markdown("### 🔥 价值分布热图")
        
        # 创建交叉表
        heatmap_data = pd.crosstab(df['category'], df['sub_category'], values=df['value_score'], aggfunc='mean')
        
        fig = px.imshow(
            heatmap_data,
            labels=dict(x="产品/品牌", y="信息类别", color="平均价值分"),
            aspect="auto",
            color_continuous_scale="RdYlBu_r"
        )
        
        st.plotly_chart(fig, use_container_width=True)
        
        # 关键词词云（简单统计）
        st.markdown("---")
        st.markdown("### ☁️ 高频关键词")
        
        # 提取所有关键词
        all_keywords = []
        for keywords_str in df['keywords'].dropna():
            keywords = [k.strip() for k in keywords_str.split(',') if k.strip()]
            all_keywords.extend(keywords)
        
        # 统计关键词频率
        keyword_counts = pd.Series(all_keywords).value_counts().head(20)
        
        col1, col2 = st.columns([3, 1])
        with col1:
            fig = px.bar(
                x=keyword_counts.values,
                y=keyword_counts.index,
                orientation='h',
                labels={'x': '出现次数', 'y': '关键词'},
                title='Top 20 高频关键词'
            )
            st.plotly_chart(fig, use_container_width=True)
        
        with col2:
            st.markdown("**关键词统计**")
            st.metric("总关键词数", len(set(all_keywords)))
            st.metric("平均关键词/文章", f"{len(all_keywords)/len(df):.1f}")

if __name__ == "__main__":
    main()
