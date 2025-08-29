import os
import sqlite3
import requests
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Dict, Optional, Set
from newspaper import Article, Config as NewspaperConfig
from tqdm import tqdm
from dotenv import load_dotenv
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from urllib.parse import urlparse

# --- 配置日志系统 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('news_scraper.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- 1. 数据模型定义 ---
@dataclass
class NewsArticle:
    """新闻文章数据模型"""
    title: str
    url: str
    source: str
    publish_date: str
    author: str
    sub_category: str
    category: str
    summary: str
    keywords: str
    value_score: int
    value_reason: str
    created_at: datetime = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()

@dataclass
class SearchTask:
    """搜索任务模型"""
    query: str
    sub_category: str
    type: str

# --- 2. 配置管理 ---
class AppConfig:
    """应用配置管理类"""
    def __init__(self):
        load_dotenv()
        self.GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
        self.SEARCH_ENGINE_ID = os.getenv("SEARCH_ENGINE_ID")
        self.DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
        self.DB_FILE = 'news.db'
        self.MAX_WORKERS = 10
        self.REQUEST_TIMEOUT = 20
        self.RETRY_COUNT = 3
        self.RETRY_DELAY = 2
        
        # Newspaper3k 配置
        self.newspaper_config = self._setup_newspaper_config()
        
    def _setup_newspaper_config(self):
        config = NewspaperConfig()
        config.browser_user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0'
        config.request_timeout = self.REQUEST_TIMEOUT
        return config

# --- 3. 数据库管理 ---
class DatabaseManager:
    """SQLite数据库管理类"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_database()
    
    def _init_database(self):
        """初始化数据库表结构"""
        with self._get_connection() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS articles (
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
            
            # 创建索引以提高查询性能
            conn.execute('CREATE INDEX IF NOT EXISTS idx_url ON articles(url)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_publish_date ON articles(publish_date)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_value_score ON articles(value_score)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_category ON articles(category)')
            conn.commit()
    
    @contextmanager
    def _get_connection(self):
        """获取数据库连接的上下文管理器"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def get_existing_urls(self) -> Set[str]:
        """获取已存在的URL集合"""
        with self._get_connection() as conn:
            cursor = conn.execute('SELECT url FROM articles')
            return {row['url'] for row in cursor}
    
    def save_articles(self, articles: List[NewsArticle]) -> int:
        """批量保存文章"""
        if not articles:
            return 0
            
        with self._get_connection() as conn:
            saved_count = 0
            for article in articles:
                try:
                    conn.execute('''
                        INSERT INTO articles (
                            title, url, source, publish_date, author,
                            sub_category, category, summary, keywords,
                            value_score, value_reason
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        article.title, article.url, article.source,
                        article.publish_date, article.author,
                        article.sub_category, article.category,
                        article.summary, article.keywords,
                        article.value_score, article.value_reason
                    ))
                    saved_count += 1
                except sqlite3.IntegrityError:
                    logger.warning(f"文章已存在: {article.url}")
            
            conn.commit()
            return saved_count
    
    def get_recent_articles(self, limit: int = 10) -> List[Dict]:
        """获取最近的文章"""
        with self._get_connection() as conn:
            cursor = conn.execute('''
                SELECT * FROM articles
                ORDER BY created_at DESC
                LIMIT ?
            ''', (limit,))
            return [dict(row) for row in cursor]
    
    def get_high_value_articles(self, min_score: int = 70) -> List[Dict]:
        """获取高价值文章"""
        with self._get_connection() as conn:
            cursor = conn.execute('''
                SELECT * FROM articles
                WHERE value_score >= ?
                ORDER BY value_score DESC, created_at DESC
            ''', (min_score,))
            return [dict(row) for row in cursor]

# --- 4. 搜索策略管理 ---
class SearchStrategyManager:
    """搜索策略管理类"""
    
    SEARCH_TARGETS = [
        {"category": "吹风机", "terms": ["高速吹风机", "hair dryer"]},
        {"category": "美容仪", "terms": ["美容仪", "beauty device", "RF beauty device", "microcurrent device"]},
        {"category": "按摩仪", "terms": ["筋膜枪", "massage gun"]},
    ]
    
    SEARCH_MODIFIERS = [
        {"type": "技术创新", "terms": ["技术", "新品", "专利", "new technology", "innovation", "patent", "launch"]},
        {"type": "市场评测", "terms": ["评测", "对比", "review", "vs"]},
        {"type": "法规监管", "terms": ["监管", "法规", "FDA approval", "clinical trial"]},
        {"type": "行业报告", "terms": ["行业报告", "市场趋势", "market research", "industry report"]},
    ]
    
    TARGETED_SOURCES = [
        {"domain": "36kr.com", "keywords": ["美容科技", "个护家电", "消费电子"]},
        {"domain": "geekpark.net", "keywords": ["智能硬件", "消费电子"]},
        {"domain": "techcrunch.com", "keywords": ["beauty tech", "personal care", "hardware"]},
        {"domain": "theverge.com", "keywords": ["personal care", "gadgets", "review"]},
    ]
    
    DOMAIN_BLACKLIST = {
        'winbo4x4.com', 'youtube.com', 'bilibili.com', 'facebook.com',
        'twitter.com', 'instagram.com', 'pinterest.com', 'linkedin.com',
        'reddit.com', 'tiktok.com'
    }
    
    @classmethod
    def generate_search_tasks(cls) -> List[SearchTask]:
        """生成搜索任务列表"""
        tasks = []
        
        # 组合搜索词和修饰词
        for target in cls.SEARCH_TARGETS:
            for term in target["terms"]:
                for modifier in cls.SEARCH_MODIFIERS:
                    for mod_term in modifier["terms"]:
                        query = f'intitle:"{term}" "{mod_term}"'
                        tasks.append(SearchTask(
                            query=query,
                            sub_category=target["category"],
                            type=modifier["type"]
                        ))
        
        # 针对特定网站的搜索
        for source in cls.TARGETED_SOURCES:
            for keyword in source["keywords"]:
                query = f'"{keyword}" site:{source["domain"]}'
                tasks.append(SearchTask(
                    query=query,
                    sub_category="行业动态",
                    type=source["domain"]
                ))
        
        logger.info(f"生成了 {len(tasks)} 个搜索任务")
        return tasks
    
    @classmethod
    def is_url_blacklisted(cls, url: str) -> bool:
        """检查URL是否在黑名单中"""
        domain = urlparse(url).netloc
        return domain in cls.DOMAIN_BLACKLIST

# --- 5. API客户端 ---
class GoogleSearchClient:
    """Google搜索API客户端"""
    
    def __init__(self, api_key: str, search_engine_id: str):
        self.api_key = api_key
        self.search_engine_id = search_engine_id
        self.base_url = "https://www.googleapis.com/customsearch/v1"
    
    def search(self, query: str, num: int = 5) -> List[Dict]:
        """执行Google搜索"""
        params = {
            'key': self.api_key,
            'cx': self.search_engine_id,
            'q': query,
            'num': num
        }
        
        try:
            response = requests.get(self.base_url, params=params, timeout=15)
            response.raise_for_status()
            return response.json().get('items', [])
        except requests.RequestException as e:
            logger.error(f"Google搜索失败: {e}")
            return []

class DeepSeekClient:
    """DeepSeek API客户端"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.deepseek.com/chat/completions"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    
    def _make_request(self, prompt: str, temperature: float = 0.1) -> Optional[str]:
        """发送请求到DeepSeek API"""
        try:
            response = requests.post(
                self.base_url,
                headers=self.headers,
                json={
                    "model": "deepseek-chat",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
                    "stream": False
                },
                timeout=30
            )
            response.raise_for_status()
            return response.json()['choices'][0]['message']['content']
        except requests.RequestException as e:
            logger.error(f"DeepSeek API请求失败: {e}")
            return None
    
    def analyze_article(self, title: str, text: str) -> Optional[Dict]:
        """分析文章内容"""
        prompt = f"""
        你是一个专业的行业分析师，专注于个护小家电领域。请分析以下文章，并严格按照JSON格式输出。

        文章标题: {title}
        文章全文（部分）:
        {text[:3000]}

        请执行以下任务：
        1. **category**: 判断文章的核心类别。只能从以下选项中选择一个：["技术创新", "市场动态", "法规政策", "竞品分析", "用户反馈", "行业报告", "无关"]。
        2. **summary**: 生成一段不超过200字的中文摘要，精准概括文章核心内容。
        3. **keywords**: 提取3-5个最关键的中文关键词。

        输出格式必须是严格的JSON，如下所示：
        {{
          "category": "...",
          "summary": "...",
          "keywords": ["...", "...", "..."]
        }}
        """
        
        content = self._make_request(prompt)
        if content:
            try:
                return json.loads(content)
            except json.JSONDecodeError as e:
                logger.error(f"JSON解析失败: {e}")
                return None
        return None
    
    def evaluate_value(self, summary: str, sub_category: str) -> Optional[Dict]:
        """评估文章业务价值"""
        prompt = f"""
        你是一位经验丰富的个护小家电产品线负责人。请基于以下情报摘要，评估其对于我们业务的价值。

        情报子分类: {sub_category}
        情报摘要: {summary}

        请执行以下任务：
        1. **score**: 对情报的业务价值打分，范围0-100。
        2. **reason**: 用一句话简明扼要地解释打分原因。

        输出格式必须是严格的JSON：
        {{
            "score": ...,
            "reason": "..."
        }}
        """
        
        content = self._make_request(prompt)
        if content:
            try:
                return json.loads(content)
            except json.JSONDecodeError as e:
                logger.error(f"JSON解析失败: {e}")
                return None
        return None

# --- 6. 文章处理器 ---
class ArticleProcessor:
    """文章处理器"""
    
    def __init__(self, config: AppConfig, deepseek_client: DeepSeekClient):
        self.config = config
        self.deepseek_client = deepseek_client
    
    def fetch_article(self, url: str) -> Optional[Article]:
        """抓取文章内容"""
        try:
            article = Article(url, config=self.config.newspaper_config)
            article.download()
            article.parse()
            return article
        except Exception as e:
            logger.error(f"文章抓取失败 {url}: {e}")
            return None
    
    def extract_metadata(self, item: Dict) -> tuple:
        """从搜索结果中提取元数据"""
        pagemap = item.get('pagemap', {})
        metatags = pagemap.get('metatags', [{}])[0]
        publish_date = metatags.get('article:published_time', '未知')
        author = metatags.get('author', '未知')
        return publish_date, author
    
    def process_single_article(self, search_result: Dict, task: SearchTask) -> Optional[NewsArticle]:
        """处理单篇文章"""
        url = search_result.get('link')
        if not url or SearchStrategyManager.is_url_blacklisted(url):
            return None
        
        # 提取基本信息
        title = search_result.get('title', '')
        display_link = search_result.get('displayLink', '')
        publish_date, author = self.extract_metadata(search_result)
        
        # 抓取文章内容
        article = self.fetch_article(url)
        if not article or not article.text or len(article.text) < 200:
            logger.debug(f"文章内容过短或无法获取: {url}")
            return None
        
        # 处理发布日期
        if publish_date and publish_date != "未知":
            publish_date = publish_date.split('T')[0]
        elif article.publish_date:
            publish_date = article.publish_date.strftime('%Y-%m-%d')
        else:
            publish_date = "未知"
        
        # AI分析
        analysis = self.deepseek_client.analyze_article(title, article.text)
        if not analysis or analysis.get('category') == '无关':
            logger.debug(f"文章被AI过滤: {title[:50]}...")
            return None
        
        # 评估价值
        summary = analysis.get('summary', '')
        evaluation = self.deepseek_client.evaluate_value(summary, task.sub_category)
        
        if not evaluation:
            value_score, value_reason = 0, '评估失败'
        else:
            value_score = evaluation.get('score', 0)
            value_reason = evaluation.get('reason', '评估失败')
        
        return NewsArticle(
            title=title,
            url=url,
            source=display_link,
            publish_date=publish_date,
            author=author,
            sub_category=task.sub_category,
            category=analysis.get('category', '其他'),
            summary=summary,
            keywords=", ".join(analysis.get('keywords', [])),
            value_score=value_score,
            value_reason=value_reason
        )

# --- 7. 主程序 ---
class NewsScraper:
    """新闻抓取主程序"""
    
    def __init__(self):
        self.config = AppConfig()
        self.db_manager = DatabaseManager(self.config.DB_FILE)
        self.google_client = GoogleSearchClient(
            self.config.GOOGLE_API_KEY,
            self.config.SEARCH_ENGINE_ID
        )
        self.deepseek_client = DeepSeekClient(self.config.DEEPSEEK_API_KEY)
        self.article_processor = ArticleProcessor(self.config, self.deepseek_client)
        self.existing_urls = self.db_manager.get_existing_urls()
    
    def process_search_task(self, task: SearchTask) -> List[NewsArticle]:
        """处理单个搜索任务"""
        logger.info(f"执行搜索: {task.query}")
        search_results = self.google_client.search(task.query, num=5)
        
        if not search_results:
            return []
        
        articles = []
        for result in search_results:
            url = result.get('link')
            if url and url not in self.existing_urls:
                article = self.article_processor.process_single_article(result, task)
                if article:
                    articles.append(article)
                    self.existing_urls.add(url)
                    logger.info(f"成功处理: {article.title[:50]}... (价值分: {article.value_score})")
        
        return articles
    
    def run(self):
        """主执行函数"""
        logger.info("🚀 开始执行情报抓取任务...")
        logger.info(f"📊 数据库中已存在 {len(self.existing_urls)} 条记录")
        
        # 生成搜索任务
        search_tasks = SearchStrategyManager.generate_search_tasks()
        all_articles = []
        
        # 使用线程池并发处理
        with ThreadPoolExecutor(max_workers=self.config.MAX_WORKERS) as executor:
            future_to_task = {
                executor.submit(self.process_search_task, task): task
                for task in search_tasks
            }
            
            for future in tqdm(as_completed(future_to_task), total=len(search_tasks), desc="处理搜索任务"):
                try:
                    articles = future.result()
                    all_articles.extend(articles)
                    time.sleep(1)  # 避免请求过快
                except Exception as e:
                    logger.error(f"任务执行失败: {e}")
        
        # 保存结果
        if all_articles:
            saved_count = self.db_manager.save_articles(all_articles)
            logger.info(f"✅ 成功保存 {saved_count} 篇新文章")
            
            # 显示高价值文章
            high_value_articles = [a for a in all_articles if a.value_score >= 70]
            if high_value_articles:
                logger.info("\n🌟 高价值情报 (≥70分):")
                for article in sorted(high_value_articles, key=lambda x: x.value_score, reverse=True):
                    logger.info(f"  [{article.value_score}分] {article.title[:60]}...")
                    logger.info(f"    理由: {article.value_reason}")
        else:
            logger.info("本次运行未发现新情报")

# --- 8. 程序入口 ---
if __name__ == "__main__":
    scraper = NewsScraper()
    scraper.run()
