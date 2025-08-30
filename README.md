# AutoCollectNews

本脚本自动从 Google 搜索收集与个人护理小家电（吹风机、美容仪、按摩枪）相关的新闻文章，使用 DeepSeek API 分析它们，并将它们保存到 SQLite 数据库。

## 前提条件

- Python 3.6+
- 一个 Google Custom Search API 密钥和一个搜索引擎 ID
- 一个 DeepSeek API 密钥

## 安装

1. 克隆存储库：

   ```bash
   git clone https://github.com/GeBabyGeeGee/AutoCollectNews.git
   cd AutoCollectNews
   ```

2. 安装依赖项：

   ```bash
   pip install -r requirements.txt
   ```

3. 创建一个包含 API 密钥的 `.env` 文件：

   ```
   GOOGLE_API_KEY=YOUR_GOOGLE_API_KEY
   SEARCH_ENGINE_ID=YOUR_SEARCH_ENGINE_ID
   DEEPSEEK_API_KEY=YOUR_DEEPSEEK_API_KEY
   ```

## 用法

运行脚本：

```bash
python main.py
```

## 组件

- `main.py`: 主脚本，用于协调新闻抓取、分析和存储过程。
- `AppConfig`: 管理应用程序配置，包括 API 密钥和数据库设置。
- `DatabaseManager`: 处理 SQLite 数据库操作，例如创建表、保存文章以及检索最近或高价值的文章。
- `SearchStrategyManager`: 根据预定义的目标和修饰符生成搜索任务。
- `GoogleSearchClient`: 与 Google Custom Search API 交互以检索搜索结果。
- `DeepSeekClient`: 与 DeepSeek API 交互以分析文章内容并评估其业务价值。
- `ArticleProcessor`: 获取文章内容、提取元数据并使用 AI 分析处理文章。
- `NewsArticle`: 一个表示新闻文章的数据类。
- `SearchTask`: 一个表示搜索任务的数据类。

## 数据库

该脚本将收集的新闻文章保存到名为 `news.db` 的 SQLite 数据库。 数据库包含一个名为 `articles` 的表，其中包含以下列：

- `id`: INTEGER PRIMARY KEY AUTOINCREMENT
- `title`: TEXT NOT NULL
- `url`: TEXT UNIQUE NOT NULL
- `source`: TEXT
- `publish_date`: TEXT
- `author`: TEXT
- `sub_category`: TEXT
- `category`: TEXT
- `summary`: TEXT
- `keywords`: TEXT
- `value_score`: INTEGER
- `value_reason`: TEXT
- `created_at`: TIMESTAMP DEFAULT CURRENT_TIMESTAMP

## 日志

该脚本将所有活动记录到名为 `news_scraper.log` 的文件中。

## 许可证

[MIT](LICENSE)
