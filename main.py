import requests
import json
import sqlite3
import time
import concurrent.futures
from tqdm import tqdm
from openai import OpenAI

# --- 1. 配置信息 ---
DEEPSEEK_API_KEY = st.secrets["DEEPSEEK_API_KEY"]
GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
SEARCH_ENGINE_ID = st.secrets["SEARCH_ENGINE_ID"]
DB_FILE = "News.db"

# DeepSeek API 客户端
client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com/v1")

# --- 2. 精准关键词策略 ---
KEYWORD_STRATEGY = {
    "吹风机": [
        '("负离子" OR "高速" OR "智能温控") "吹风机" "新技术"',
        '"hair dryer" "new technology" OR "patent"',
        '"吹风机" "市场趋势" OR "消费者报告"',
        '"hair dryer" "safety standards" OR "certification" (CE OR FCC OR UL)',
    ],
    "按摩仪": [
        '("筋膜枪" OR "颈部按摩仪" OR "眼部按摩仪") "新品发布" OR "技术升级"',
        '"massage gun" OR "neck massager" "review" OR "innovation"',
        '"按摩设备" "医疗器械认证" OR "注册标准"',
        '"percussive therapy" OR "TENS" "health benefits"',
    ],
    "美容仪": [
        '("射频" OR "微电流" OR "LED光疗") "美容仪" "临床效果" OR "技术专利"',
        '"beauty device" OR "RF" OR "microcurrent" "FDA clearance" OR "new"',
        '"家用美容仪" "行业报告" OR "监管政策"',
        '"anti-aging device" "market trends"',
    ]
}

# --- 3. 数据库操作 (包含价值评估字段) ---
def setup_database():
    """初始化数据库和表 (增加价值评估字段)"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS news (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        url TEXT UNIQUE NOT NULL,
        source TEXT,
        publish_date TEXT,
        author TEXT,
        sub_category TEXT,
        category TEXT,
        summary TEXT,
        keywords TEXT,
        value_score INTEGER,        -- 新增: 运营价值分数 (1-10)
        value_reason TEXT,          -- 新增: 评估理由
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    conn.commit()
    conn.close()

def get_existing_urls():
    """一次性获取数据库中所有URL，用于内存中快速去重"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT url FROM news")
    urls = {row[0] for row in cursor.fetchall()}
    conn.close()
    return urls

def save_to_db(news_items):
    """批量保存包含评估结果的新闻数据到数据库"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.executemany('''
    INSERT OR IGNORE INTO news (title, url, source, publish_date, author, sub_category, category, summary, keywords, value_score, value_reason)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', [(
        item['title'], item['url'], item['source'],
        item['publish_date'], item['author'],
        item['sub_category'], item['category'], item['summary'],
        item['keywords'], item['value_score'], item['value_reason']
    ) for item in news_items])
    conn.commit()
    conn.close()

# --- 4. API 调用 (包含两阶段AI处理) ---
def search_google(query):
    """调用Google Search API"""
    url = "https://www.googleapis.com/customsearch/v1"
    params = {'key': GOOGLE_API_KEY, 'cx': SEARCH_ENGINE_ID, 'q': query, 'num': 10}
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        return response.json().get('items', [])
    except requests.exceptions.RequestException as e:
        print(f"\nError searching Google for '{query}': {e}")
        return []

def analyze_with_deepseek(title, snippet, sub_category):
    """第一阶段AI：提取信息"""
    prompt = f"""
    你是一位专业的“个护小家电”行业分析师。请根据以下新闻信息，完成任务：
    1. **分类**: 将其归类到最合适的类别：[技术创新, 产品发布, 市场趋势, 法规认证, 用户反馈, 企业动态, 其他]。
    2. **总结**: 用不超过150字的中文，精准总结其核心内容。
    3. **关键词**: 提取3-5个最核心的关键词。
    请严格按照JSON格式返回：{{"category": "...", "summary": "...", "keywords": ["...", "..."]}}
    ---
    新闻标题: {title}
    新闻摘要: {snippet}
    ---
    """
    try:
        response = client.chat.completions.create(
            model="deepseek-chat", messages=[{"role": "user", "content": prompt}],
            temperature=0.1, response_format={"type": "json_object"}
        )
        return response.choices[0].message.content
    except Exception:
        return None

def evaluate_operational_value(summary, sub_category):
    """第二阶段AI：评估运营价值"""
    prompt = f"""
    你是一位专注于“个护小家电”领域的资深社交媒体运营策略师。请评估以下新闻摘要对于公司的内容创作、营销活动的启发和帮助程度。
    评估标准:
    - 高价值(7-10分): 颠覆性技术、重要法规、直接竞品重大动向、可直接转化为爆款内容的消费者痛点或市场趋势。
    - 中等价值(4-6分): 常规技术更新、产品发布、可作参考的市场数据。
    - 低价值(1-3分): 信息模糊、关联度低、过于宽泛或陈旧。
    任务:
    1.  **打分:** 给出1-10分的“运营价值分数”。
    2.  **说明理由:** 用一句话简明扼要地解释打分原因。
    请严格按照JSON格式返回：{{"score": <分数>, "reason": "..."}}
    ---
    产品品类: {sub_category}
    新闻摘要: {summary}
    ---
    """
    try:
        response = client.chat.completions.create(
            model="deepseek-chat", messages=[{"role": "user", "content": prompt}],
            temperature=0.2, response_format={"type": "json_object"}
        )
        return response.choices[0].message.content
    except Exception:
        return None

# --- 5. 核心工作流 ---
def process_article(article_info):
    """单个线程的工作单元：提取信息 + 价值评估"""
    try:
        title = article_info.get('title')
        snippet = article_info.get('snippet')
        sub_cat = article_info.get('sub_category')

        # 第一阶段: 信息提取
        analysis_str = analyze_with_deepseek(title, snippet, sub_cat)
        if not analysis_str: return None
        analysis = json.loads(analysis_str)
        summary = analysis.get('summary', '')

        # 第二阶段: 价值评估
        value_score, value_reason = 0, "评估失败"
        if summary:
            evaluation_str = evaluate_operational_value(summary, sub_cat)
            if evaluation_str:
                evaluation = json.loads(evaluation_str)
                value_score = evaluation.get('score', 0)
                value_reason = evaluation.get('reason', '无理由')
        
        return {
            "title": title, "url": article_info.get('url'),
            "source": article_info.get('displayLink'), "publish_date": article_info.get('publish_date'),
            "author": article_info.get('author'), "sub_category": sub_cat,
            "category": analysis.get('category', '其他'), "summary": summary,
            "keywords": ", ".join(analysis.get('keywords', [])),
            "value_score": value_score, "value_reason": value_reason
        }
    except Exception:
        return None

def job():
    """并行工作流主函数"""
    print(f"[{time.ctime()}] Starting parallel news job with value assessment...")
    
    print("Phase 1: Gathering all new articles...")
    existing_urls = get_existing_urls()
    tasks_to_process = []
    all_queries = [{'sub_cat': sc, 'query': q} for sc, qs in KEYWORD_STRATEGY.items() for q in qs]
    for task in tqdm(all_queries, desc="Searching Google"):
        search_results = search_google(task['query'])
        for item in search_results:
            url = item.get('link')
            if not url or url in existing_urls: continue
            
            pagemap = item.get('pagemap', {})
            metatags = pagemap.get('metatags', [{}])[0]
            publish_date = (metatags.get('article:published_time') or metatags.get('publishdate') or metatags.get('og:updated_time', '')).split('T')[0]
            author = metatags.get('author') or metatags.get('article:author')

            tasks_to_process.append({
                'title': item.get('title'), 'snippet': item.get('snippet'), 'url': url,
                'displayLink': item.get('displayLink'), 'publish_date': publish_date,
                'author': author, 'sub_category': task['sub_cat']
            })
            existing_urls.add(url)
        time.sleep(1)

    if not tasks_to_process:
        print("No new articles found. Job finished.")
        return

    print(f"\nFound {len(tasks_to_process)} new articles to process.")
    
    print("Phase 2: Analyzing and evaluating articles in parallel...")
    processed_items = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_article = {executor.submit(process_article, task): task for task in tasks_to_process}
        for future in tqdm(concurrent.futures.as_completed(future_to_article), total=len(tasks_to_process), desc="AI Processing"):
            result = future.result()
            if result:
                processed_items.append(result)

    if processed_items:
        print(f"\nPhase 3: Saving {len(processed_items)} processed articles to database...")
        save_to_db(processed_items)
        print("All new articles have been saved.")
    else:
        print("\nNo articles were successfully processed.")

    print(f"[{time.ctime()}] Job finished.")


# --- 6. 启动任务 ---
if __name__ == "__main__":
    print("Initializing database...")
    setup_database()
    job()
