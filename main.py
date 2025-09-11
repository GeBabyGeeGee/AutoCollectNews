import os
import sqlite3
import requests
import json
import logging
import time
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Optional, Set
from newspaper import Article, Config as NewspaperConfig
from tqdm import tqdm
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from urllib.parse import urlparse
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- 配置日志系统 (已优化) ---
# 关键: 统一使用 utf-8 编码，彻底解决中文乱码问题
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    encoding='utf-8',
    handlers=[
        logging.FileHandler('news_scraper.log', encoding='utf-8'),
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
    created_at: Optional[datetime] = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()

@dataclass
class SearchTask:
    """搜索任务模型"""
    query: str
    sub_category: str
    type: str

# --- 2. 配置管理 (已优化) ---
class AppConfig:
    """应用配置管理类"""
    def __init__(self):
        load_dotenv()
        self.GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
        self.SEARCH_ENGINE_ID = os.getenv("SEARCH_ENGINE_ID")
        self.DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

        if not all([self.GOOGLE_API_KEY, self.SEARCH_ENGINE_ID, self.DEEPSEEK_API_KEY]):
            raise ValueError("错误：必须在 .env 文件中设置 GOOGLE_API_KEY, SEARCH_ENGINE_ID, 和 DEEPSEEK_API_KEY")
            
        self.DB_FILE = 'news.db'
        self.MAX_WORKERS = 10
        self.REQUEST_TIMEOUT = 20
        self.RETRY_COUNT = 3
        self.RETRY_DELAY = 2
        
        # 创建一个带重试逻辑的全局Session (新增)
        self.http_session = self._create_resilient_session()
        
        # Newspaper3k 配置
        self.newspaper_config = self._setup_newspaper_config()
        
    def _create_resilient_session(self) -> requests.Session:
        """创建一个能自动重试的requests Session对象，增强网络请求的健壮性"""
        session = requests.Session()
        retry_strategy = Retry(
            total=self.RETRY_COUNT,
            status_forcelist=[429, 500, 502, 503, 504],  # 对这些服务器错误状态码进行重试
            allowed_methods=["HEAD", "GET", "POST"],
            backoff_factor=1  # 重试间隔时间因子
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session
        
    def _setup_newspaper_config(self):
        """配置Newspaper3k，增加更完整的浏览器头信息"""
        config = NewspaperConfig()
        config.browser_user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        config.request_timeout = self.REQUEST_TIMEOUT
        config.headers = {
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
        }
        return config

# --- 3. 数据库管理 ---
class DatabaseManager:
    """SQLite数据库管理类"""
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_database()

    def _init_database(self):
        with self._get_connection() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, url TEXT UNIQUE NOT NULL,
                    source TEXT, publish_date TEXT, author TEXT, sub_category TEXT, category TEXT,
                    summary TEXT, keywords TEXT, value_score INTEGER, value_reason TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_url ON articles(url)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_publish_date ON articles(publish_date)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_value_score ON articles(value_score)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_category ON articles(category)')
            conn.commit()
    
    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def get_existing_urls(self) -> Set[str]:
        with self._get_connection() as conn:
            cursor = conn.execute('SELECT url FROM articles')
            return {row['url'] for row in cursor}
    
    def save_articles(self, articles: List[NewsArticle]) -> int:
        if not articles:
            return 0
        with self._lock:
            with self._get_connection() as conn:
                saved_count = 0
                for article in articles:
                    try:
                        conn.execute('''
                            INSERT INTO articles (
                                title, url, source, publish_date, author, sub_category, category,
                                summary, keywords, value_score, value_reason
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            article.title, article.url, article.source, article.publish_date,
                            article.author, article.sub_category, article.category, article.summary,
                            article.keywords, article.value_score, article.value_reason
                        ))
                        saved_count += 1
                    except sqlite3.IntegrityError:
                        logger.warning(f"文章已存在: {article.url}")
                conn.commit()
                return saved_count

# --- 4. 搜索策略管理 ---
class SearchStrategyManager:
    """搜索策略管理类"""
    SEARCH_TARGETS = [
        {"category": "吹风机", "terms": ["高速吹风机", "hair dryer"]},
        {"category": "美容仪", "terms": ["美容仪", "beauty device", "RF beauty device", "microcurrent device"]},
        {"category": "按摩仪", "terms": ["筋膜枪", "massage gun"]},
    ]
    SEARCH_MODIFIERS = [
        {"type": "技术创新", "terms": ["展会","技术", "新品", "专利", "new technology", "innovation", "patent", "launch"]},
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
    # 扩展黑名单，加入日志中频繁失败或无价值的域名 (已优化)
    DOMAIN_BLACKLIST = {
        # 社交和视频平台
        'youtube.com', 'bilibili.com', 'facebook.com', 'twitter.com', 'instagram.com',
        'pinterest.com', 'linkedin.com', 'reddit.com', 'tiktok.com', 'quora.com', 'zhihu.com', 'sohu.com', 'theverge.com',
        # 电商和图片素材网站
        'amazon.com', 'tmall.com', 'jd.com', 'taobao.com', 'aliexpress.com', 'vmall.com',
        'shutterstock.com', 'adobestock.com', 'gettyimages.com', 'behance.net',
        # 日志中明确出现问题且非核心信息源的网站
        'yohohongkong.com', 'bukshisha.com', 'drselimguldiken.com', 'winbo4x4.com',
        'tortillasdurum.com', 'centrooleodinamica.com', 'queenstreetonline.com',
        'shoplc.com', 'i5a6.com', 'revnabio.com', 'wizevo.com', 'huadicn.com',
        'manuals.plus', 'inspire.com', 'medestheticsmag.com', 'fittopfactory.com'
    }
    # 新增文件扩展名黑名单 (已优化)
    EXTENSION_BLACKLIST = {'.pdf', '.jpg', '.jpeg', '.png', '.gif', '.zip', '.rar', '.docx', '.xlsx', '.mp4', '.avi'}

    @classmethod
    def generate_search_tasks(cls) -> List[SearchTask]:
        tasks = []
        for target in cls.SEARCH_TARGETS:
            for term in target["terms"]:
                for modifier in cls.SEARCH_MODIFIERS:
                    for mod_term in modifier["terms"]:
                        query = f'"{term}" "{mod_term}"'
                        tasks.append(SearchTask(query=query, sub_category=target["category"], type=modifier["type"]))
        for source in cls.TARGETED_SOURCES:
            for keyword in source["keywords"]:
                query = f'"{keyword}" site:{source["domain"]}'
                tasks.append(SearchTask(query=query, sub_category="行业动态", type=source["domain"]))
        logger.info(f"生成了 {len(tasks)} 个搜索任务")
        return tasks

    @classmethod
    def is_url_blacklisted(cls, url: str) -> bool:
        """检查URL的域名或文件扩展名是否在黑名单中 (已优化)"""
        if not url or not url.startswith(('http://', 'https://')):
            return True
        try:
            parsed_url = urlparse(url)
            domain = parsed_url.netloc
            path = parsed_url.path.lower()
            
            # 检查域名
            if any(blacklisted_domain in domain for blacklisted_domain in cls.DOMAIN_BLACKLIST):
                logger.debug(f"URL域名被屏蔽: {url}")
                return True
            
            # 检查文件扩展名
            if any(path.endswith(ext) for ext in cls.EXTENSION_BLACKLIST):
                logger.debug(f"URL文件类型被屏蔽: {url}")
                return True
                
            return False
        except Exception as e:
            logger.warning(f"URL解析失败 '{url}', 错误: {e}. 将其视为黑名单URL.")
            return True

# --- 5. API客户端 (已优化) ---
class GoogleSearchClient:
    """Google搜索API客户端"""
    def __init__(self, api_key: str, search_engine_id: str, session: requests.Session):
        self.api_key = api_key
        self.search_engine_id = search_engine_id
        self.base_url = "https://www.googleapis.com/customsearch/v1"
        self.session = session  # 使用传入的带重试的session

    def search(self, query: str, num: int = 5) -> List[Dict]:
        params = {'key': self.api_key, 'cx': self.search_engine_id, 'q': query, 'num': num}
        try:
            response = self.session.get(self.base_url, params=params, timeout=15)
            response.raise_for_status()
            return response.json().get('items', [])
        except requests.RequestException as e:
            logger.error(f"Google搜索失败: {e}")
            return []

class DeepSeekClient:
    """DeepSeek API客户端"""
    def __init__(self, api_key: str, session: requests.Session):
        self.api_key = api_key
        self.base_url = "https://api.deepseek.com/chat/completions"
        self.headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self.session = session  # 使用传入的带重试的session

    def _make_request(self, prompt: str, temperature: float = 0.1) -> Optional[str]:
        response = None  # 修复: 在 try 块之前初始化 response
        try:
            response = self.session.post(
                self.base_url, headers=self.headers,
                json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}], "temperature": temperature, "stream": False},
                timeout=45
            )
            response.raise_for_status()
            return response.json()['choices'][0]['message']['content']
        except requests.RequestException as e:
            logger.error(f"DeepSeek API请求失败: {e}")
            return None
        except (KeyError, IndexError) as e:
            response_text = response.text if response else 'N/A'
            logger.error(f"DeepSeek API响应格式不正确: {e} - 响应: {response_text}")
            return None

    def _parse_json_response(self, content: Optional[str]) -> Optional[Dict]:
        """健壮的JSON解析函数 (已优化)"""
        if not content:
            return None
        try:
            # 移除常见的Markdown代码块标记
            if content.strip().startswith("```json"):
                content = content.strip()[7:-3]
            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}. 原始内容: {content[:500]}...")
            return None

    def analyze_article(self, title: str, text: str) -> Optional[Dict]:
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
        return self._parse_json_response(content)

    def evaluate_value(self, summary: str, sub_category: str) -> Optional[Dict]:
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
        return self._parse_json_response(content)

# --- 6. 文章处理器 (已优化) ---
class ArticleProcessor:
    """文章处理器"""
    def __init__(self, config: AppConfig, deepseek_client: DeepSeekClient):
        self.config = config
        self.deepseek_client = deepseek_client
    
    def fetch_article(self, url: str) -> Optional[Article]:
        """抓取文章内容，并增加手动重试逻辑 (已优化)"""
        for attempt in range(self.config.RETRY_COUNT):
            try:
                article = Article(url, config=self.config.newspaper_config)
                article.download()
                article.parse()
                return article
            except Exception as e:
                logger.warning(f"文章抓取失败 (第 {attempt + 1}/{self.config.RETRY_COUNT} 次): {url}, 错误: {e}")
                if attempt < self.config.RETRY_COUNT - 1:
                    time.sleep(self.config.RETRY_DELAY)
        logger.error(f"文章抓取最终失败: {url}")
        return None

    def extract_metadata(self, item: Dict) -> tuple:
        pagemap = item.get('pagemap', {})
        metatags = pagemap.get('metatags', [{}])[0]
        publish_date = metatags.get('article:published_time', '未知')
        author = metatags.get('author', '未知')
        return publish_date, author
    
    def process_single_article(self, search_result: Dict, task: SearchTask) -> Optional[NewsArticle]:
        url = search_result.get('link')
        if not url or SearchStrategyManager.is_url_blacklisted(url):
            return None
        
        title = search_result.get('title', '')
        display_link = search_result.get('displayLink', '')
        publish_date, author = self.extract_metadata(search_result)
        
        article = self.fetch_article(url)
        if not article or not article.text or len(article.text) < 200:
            logger.debug(f"文章内容过短或无法获取: {url}")
            return None
        
        if publish_date and publish_date != "未知":
            publish_date = publish_date.split('T')[0]
        # 修复: 检查 article.publish_date 是否为 datetime 对象
        elif article.publish_date and isinstance(article.publish_date, datetime):
            publish_date = article.publish_date.strftime('%Y-%m-%d')
        else:
            publish_date = "未知"
        
        analysis = self.deepseek_client.analyze_article(title, article.text)
        if not analysis or analysis.get('category') == '无关':
            logger.debug(f"文章被AI过滤: {title[:50]}...")
            return None
        
        summary = analysis.get('summary', '')
        evaluation = self.deepseek_client.evaluate_value(summary, task.sub_category)
        value_score, value_reason = (evaluation.get('score', 0), evaluation.get('reason', '评估失败')) if evaluation else (0, '评估失败')
        
        return NewsArticle(
            title=title, url=url, source=display_link, publish_date=publish_date,
            author=author, sub_category=task.sub_category, category=analysis.get('category', '其他'),
            summary=summary, keywords=", ".join(analysis.get('keywords', [])),
            value_score=value_score, value_reason=value_reason
        )

# --- 7. 主程序 (已优化) ---
class NewsScraper:
    """新闻抓取主程序"""
    def __init__(self):
        self.config = AppConfig()
        self.db_manager = DatabaseManager(self.config.DB_FILE)
        # 将创建好的带重试功能的session注入到各个客户端
        # 修复: 添加断言以解决类型检查器的警告
        assert self.config.GOOGLE_API_KEY is not None
        assert self.config.SEARCH_ENGINE_ID is not None
        assert self.config.DEEPSEEK_API_KEY is not None
        
        self.google_client = GoogleSearchClient(
            self.config.GOOGLE_API_KEY, self.config.SEARCH_ENGINE_ID, self.config.http_session
        )
        self.deepseek_client = DeepSeekClient(self.config.DEEPSEEK_API_KEY, self.config.http_session)
        self.article_processor = ArticleProcessor(self.config, self.deepseek_client)
        self.existing_urls = self.db_manager.get_existing_urls()
    
    def process_search_task(self, task: SearchTask) -> List[NewsArticle]:
        logger.info(f"执行搜索: {task.query}")
        search_results = self.google_client.search(task.query, num=10)
        if not search_results:
            return []
        
        articles = []
        for result in search_results:
            url = result.get('link')
            if url and url not in self.existing_urls:
                self.existing_urls.add(url) # 提前加入，避免并发任务重复处理
                article = self.article_processor.process_single_article(result, task)
                if article:
                    articles.append(article)
                    logger.info(f"成功处理: {article.title[:50]}... (价值分: {article.value_score})")
        return articles
    
    def run(self):
        logger.info("🚀 开始执行情报抓取任务...")
        logger.info(f"📊 数据库中已存在 {len(self.existing_urls)} 条记录")
        
        search_tasks = SearchStrategyManager.generate_search_tasks()
        all_articles = []
        
        with ThreadPoolExecutor(max_workers=self.config.MAX_WORKERS) as executor:
            future_to_task = {executor.submit(self.process_search_task, task): task for task in search_tasks}
            
            progress_bar = tqdm(as_completed(future_to_task), total=len(search_tasks), desc="处理搜索任务")
            for future in progress_bar:
                try:
                    articles = future.result()
                    if articles:
                        all_articles.extend(articles)
                except Exception as e:
                    task = future_to_task[future]
                    logger.error(f"任务 '{task.query}' 执行失败: {e}", exc_info=True)
                time.sleep(0.5) # 轻微延时，避免过于频繁
        
        if all_articles:
            saved_count = self.db_manager.save_articles(all_articles)
            logger.info(f"✅ 成功保存 {saved_count} 篇新文章")
            
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
