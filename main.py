import requests
import json
import sqlite3
import time
import concurrent.futures
from tqdm import tqdm
from openai import OpenAI
import os
from dotenv import load_dotenv
from newspaper import Article, Config # <-- 引入新工具

# --- 1. 配置信息 ---
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
SEARCH_ENGINE_ID = os.getenv("SEARCH_ENGINE_ID")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DB_FILE = "News.db"

if not all([GOOGLE_API_KEY, SEARCH_ENGINE_ID, DEEPSEEK_API_KEY]):
    raise ValueError("API密钥未在 .env 文件中配置或加载失败。")

client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com/v1")

# Newspaper3k 的配置，模拟浏览器访问以防被屏蔽
user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36'
config = Config()
config.browser_user_agent = user_agent
config.request_timeout = 15


# --- 2. 策略与防线 ---
CORE_KEYWORDS = {
    "吹风机": [
        'intitle:"高速吹风机" AND ("技术" OR "新品" OR "趋势")',
        'intitle:"hair dryer" AND ("new technology" OR "innovation")',
        '("戴森" OR "徕芬" OR "小米") AND intitle:"吹风机" AND ("新品" OR "评测")',
    ],
    "按摩仪": [
        'intitle:("筋膜枪" OR "颈部按摩仪") AND ("技术" OR "新品")',
        'intitle:"massage gun" AND ("new release" OR "review")',
    ],
    "美容仪": [
        'intitle:("美容仪" OR "射频美容仪" OR "微电流美容仪") AND ("技术突破" OR "专利")',
        'intitle:"beauty device" AND ("clinical trial" OR "FDA" OR "Certificate")',
        'intitle:"家用美容仪" AND ("行业报告" OR "监管" OR "新规")',
    ]
}
EXPLORATORY_KEYWORDS = {
    "前沿趋势": [
        '("AI" OR "人工智能") AND ("肤质检测" OR "个性化护肤")',
        '("可持续" OR "环保") AND ("美妆个护" OR "包装材料")',
        '("生物科技" OR "基因编辑") AND "护肤"',
    ]
}
SOURCE_DRIVEN_KEYWORDS = {
    "行业动态": [
        '("美容科技" OR "个护家电") site:36kr.com',
        '("消费电子" OR "智能硬件") site:geekpark.net',
        '"beauty tech" OR "personal care" site:techcrunch.com',
    ]
}
DOMAIN_BLACKLIST = {
    "winbo4x4.com",
    # 可以在这里持续添加更多已知的垃圾域名
}


# --- 3. 数据库操作 ---
def setup_database():
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
        value_score INTEGER,
        value_reason TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    conn.commit()
    conn.close()

def get_existing_urls():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT url FROM news")
        urls = {row[0] for row in cursor.fetchall()}
    except sqlite3.OperationalError:
        urls = set() # 如果表还不存在，返回空集合
    conn.close()
    return urls

def save_to_db(news_items):
    if not news_items:
        return
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.executemany('''
    INSERT OR IGNORE INTO news (title, url, source, publish_date, author, sub_category, category, summary, keywords, value_score, value_reason)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', [(item['title'], item['url'], item['source'], item['publish_date'], item['author'], item['sub_category'], item['category'], item['summary'], item['keywords'], item['value_score'], item['value_reason']) for item in news_items])
    conn.commit()
    conn.close()
    print(f"\n成功向数据库保存 {len(news_items)} 条新情报。")


# --- 4. 核心功能函数 ---
def search_google(query):
    url = "https://www.googleapis.com/customsearch/v1"
    params = {'key': GOOGLE_API_KEY, 'cx': SEARCH_ENGINE_ID, 'q': query, 'num': 10}
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json().get('items', [])
    except requests.exceptions.RequestException as e:
        tqdm.write(f"Google搜索请求失败: {e}")
        return []

def get_article_text(url):
    """*** 新增的核心功能：抓取并解析网页全文 ***"""
    try:
        article = Article(url, config=config, language='zh')
        article.download()
        article.parse()
        return article.text
    except Exception as e:
        tqdm.write(f"[抓取失败] 无法从 {url} 获取全文: {e}")
        return None

def analyze_with_deepseek(title, full_text):
    """*** AI分析函数升级，现在分析的是全文 ***"""
    prompt = f"""
    你是一位极其严格的“个护小家电”行业情报分析师。你正在分析一篇完整的网页文章。

    **第一步：相关性判断 (守门员)**
    - 你必须首先判断文章全文是否与“个护小家电”（吹风机、美容仪、按摩仪等）行业高度相关。
    - 如果内容无关（赌博、广告、不相关产品等），则将 'category' 设为 "无关"，并停止后续分析。

    **第二步：信息提取 (分析师)**
    - 如果内容相关，请基于**全文内容**，完成以下任务：
        - **分类**: 归类到最合适的类别：[技术创新, 产品发布, 市场趋势, 法规认证, 用户反馈, 企业动态, 其他]。
        - **总结**: 用不超过200字的中文，精准总结文章的核心观点和关键信息。
        - **关键词**: 提取3-5个最核心的关键词。

    **输出格式(JSON):**
    - 无关内容: {{"category": "无关", "summary": "内容与个护小家电行业无关。", "keywords": []}}
    - 相关内容: {{"category": "...", "summary": "...", "keywords": ["...", "..."]}}
    ---
    文章标题: {title}
    文章全文内容:
    {full_text}
    ---
    """
    try:
        response = client.chat.completions.create(
            model="deepseek-chat", messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            response_format={"type": "json_object"}
        )
        return response.choices[0].message.content
    except Exception as e:
        tqdm.write(f"[AI分析失败] 标题: {title[:30]}... 错误: {e}")
        return None

def evaluate_operational_value(summary, sub_category):
    prompt = f"""
    你是一位经验丰富的“个护小家电”产品运营。请基于以下新闻摘要，评估其对“{sub_category}”品类的运营价值。

    **评估维度:**
    - **创新性/稀缺性:** 是否是新技术、新观点或罕见信息？
    - **可操作性:** 是否能直接启发内容创作、营销活动或产品迭代？
    - **影响力:** 涉及的是头部品牌、重要法规还是广泛趋势？

    **任务:**
    1.  **打分:** 给出1-10分的运营价值分数。
    2.  **理由:** 用一句话解释打分的核心原因。

    **输出格式(JSON):** {{"score": <分数>, "reason": "<理由>"}}
    ---
    新闻摘要: {summary}
    ---
    """
    try:
        response = client.chat.completions.create(
            model="deepseek-chat", messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            response_format={"type": "json_object"}
        )
        return response.choices[0].message.content
    except Exception as e:
        tqdm.write(f"[价值评估失败] 错误: {e}")
        return None

def extract_metadata(item):
    pagemap = item.get('pagemap', {})
    metatags = pagemap.get('metatags', [{}])[0]
    publish_date = metatags.get('article:published_time', '未知')
    author = metatags.get('author', '未知')
    return publish_date, author

# --- 5. 核心工作流 ---
def process_article(article_info):
    try:
        url = article_info.get('url')
        title = article_info.get('title')

        # 第1步：抓取网页全文
        full_text = get_article_text(url)
        
        if not full_text or len(full_text) < 200:
            tqdm.write(f"[内容过短或抓取失败] 跳过: {title[:50]}...")
            return None
        
        truncated_text = full_text[:15000]

        # 第2步：基于全文进行AI分析与过滤
        analysis_str = analyze_with_deepseek(title, truncated_text)
        if not analysis_str: return None
        
        analysis = json.loads(analysis_str)
        
        if analysis.get('category') == '无关':
            tqdm.write(f"[AI过滤] 内容无关: {title[:50]}...")
            return None
        
        summary = analysis.get('summary', '')

        # 第3步：进行价值评估
        value_score, value_reason = 0, "评估失败"
        if summary:
            evaluation_str = evaluate_operational_value(summary, article_info.get('sub_category'))
            if evaluation_str:
                evaluation = json.loads(evaluation_str)
                value_score = evaluation.get('score', 0)
                value_reason = evaluation.get('reason', '无理由')
        
        return {
            "title": title, "url": url,
            "source": article_info.get('displayLink'), "publish_date": article_info.get('publish_date'),
            "author": article_info.get('author'), "sub_category": article_info.get('sub_category'),
            "category": analysis.get('category', '其他'), "summary": summary,
            "keywords": ", ".join(analysis.get('keywords', [])),
            "value_score": value_score, "value_reason": value_reason
        }
    except (json.JSONDecodeError, KeyError, Exception) as e:
        tqdm.write(f"[处理异常] 在处理 {article_info.get('url')} 时出错: {e}")
        return None

def job():
    print(f"[{time.ctime()}] 开始执行情报采集任务 (终极版)...")
    
    existing_urls = get_existing_urls()
    tasks_to_process = []
    
    all_queries = []
    for strategy_dict in [CORE_KEYWORDS, EXPLORATORY_KEYWORDS, SOURCE_DRIVEN_KEYWORDS]:
        for sub_cat, queries in strategy_dict.items():
            for query in queries:
                all_queries.append({'sub_cat': sub_cat, 'query': query})

    print("Phase 1: 正在从Google搜索新文章...")
    for task in tqdm(all_queries, desc="Google Searching"):
        search_results = search_google(task['query'])
        for item in search_results:
            url = item.get('link')
            display_link = item.get('displayLink')

            if display_link in DOMAIN_BLACKLIST:
                tqdm.write(f"[黑名单拦截] 已跳过: {display_link}")
                continue
            
            if not url or url in existing_urls:
                continue
            
            publish_date, author = extract_metadata(item)

            tasks_to_process.append({
                'title': item.get('title'), 'url': url,
                'displayLink': display_link, 'publish_date': publish_date,
                'author': author, 'sub_category': task['sub_cat']
            })
            existing_urls.add(url)
        time.sleep(1) 

    if not tasks_to_process:
        print("\n没有发现新的有效文章。任务结束。")
        return

    print(f"\nPhase 2: 发现 {len(tasks_to_process)} 篇新文章，开始抓取全文并进行AI分析...")
    
    new_items_to_save = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_article = {executor.submit(process_article, task): task for task in tasks_to_process}
        for future in tqdm(concurrent.futures.as_completed(future_to_article), total=len(tasks_to_process), desc="AI Analyzing"):
            result = future.result()
            if result:
                new_items_to_save.append(result)

    print("\nPhase 3: 分析完成，正在保存高质量情报到数据库...")
    save_to_db(new_items_to_save)
    print(f"[{time.ctime()}] 所有任务执行完毕。")

# --- 6. 启动任务 ---
if __name__ == "__main__":
    print("正在初始化数据库...")
    setup_database()
    job()
