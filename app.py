import os
import datetime
import isodate
from flask import Flask, render_template, request, session, jsonify
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

app = Flask(__name__)
app.secret_key = 'super_secret_key_viral_engine_2026'

# === 时间筛选器映射 ===
TIME_FILTERS = {
    '24h': 24,
    '72h': 72,
    '7d': 168,   # 7 * 24
    '30d': 720   # 30 * 24
}

# === 核心搜索逻辑 ===
def search_youtube(query, api_key, hours_filter):
    try:
        youtube = build('youtube', 'v3', developerKey=api_key)
        
        # 1. 计算时间窗口
        time_window = datetime.datetime.utcnow() - datetime.timedelta(hours=hours_filter)
        published_after = time_window.isoformat("T") + "Z"

        # 2. 搜索视频
        search_response = youtube.search().list(
            q=query, part='id', maxResults=18, order='viewCount', # 稍微增加抓取数量
            type='video', publishedAfter=published_after, videoDuration='short'
        ).execute()

        video_ids = [item['id']['videoId'] for item in search_response.get('items', [])]
        if not video_ids: return []

        # 3. 获取详情
        stats_response = youtube.videos().list(
            id=','.join(video_ids), part='snippet,statistics'
        ).execute()

        videos = []
        for item in stats_response['items']:
            stats = item['statistics']
            snippet = item['snippet']
            
            view_count = int(stats.get('viewCount', 0))
            if view_count < 100: continue
            
            pub_time = isodate.parse_datetime(snippet['publishedAt'])
            now = datetime.datetime.now(datetime.timezone.utc)
            hours_ago = (now - pub_time).total_seconds() / 3600
            hours_ago = max(0.1, hours_ago)
            
            vph = int(view_count / hours_ago)
            
            thumbs = snippet['thumbnails']
            thumb = thumbs.get('maxres', thumbs.get('high', thumbs.get('medium')))['url']

            videos.append({
                'id': item['id'],
                'title': snippet['title'],
                'channel': snippet['channelTitle'],
                'views': view_count,
                'vph': vph,
                'hours_ago': f"{hours_ago:.1f}",
                'thumb': thumb
            })
        
        videos.sort(key=lambda x: x['vph'], reverse=True)
        return videos

    except HttpError as e:
        # 捕获 API 错误（如配额耗尽）
        if e.resp.status in [403, 429]:
            raise Exception("API配额已耗尽或Key无效")
        raise e
    except Exception as e:
        print(f"Error: {e}")
        return []

# === 路由 ===
@app.route('/', methods=['GET', 'POST'])
def index():
    # 优先从 Session 获取 Key
    api_key = session.get('api_key', '')
    
    # 获取参数
    query = request.form.get('query', '')
    time_option = request.form.get('time_filter', '24h') # 默认24小时
    
    # 换算成小时数
    hours = TIME_FILTERS.get(time_option, 24)
    
    videos = []
    error = None

    if request.method == 'POST':
        # 如果是“保存Key”的操作
        new_key = request.form.get('new_api_key')
        if new_key:
            session['api_key'] = new_key.strip()
            api_key = new_key.strip()
        
        # 核心搜索流程
        if not api_key:
            error = "请先配置 API Key"
        elif query:
            try:
                videos = search_youtube(query, api_key, hours)
                if not videos:
                    error = "未找到相关视频"
            except Exception as e:
                error = str(e) # 将“配额耗尽”等错误传给前端

    return render_template('index.html', videos=videos, api_key=api_key, error=error, query=query, time_option=time_option)

@app.route('/analyze/<video_id>')
def analyze(video_id):
    api_key = session.get('api_key')
    if not api_key: return "请先设置 API Key"
    # ... (详情页逻辑保持不变，为了节省篇幅这里省略，原样保留即可) ...
    # 如果你需要详情页代码，请告诉我，我再发一遍完整的
    return "详情页功能保持不变" 

# === 辅助接口：检查 Key 状态（模拟余量显示） ===
# YouTube API 不提供直接查询余量的接口，这里做一个简单的状态返回
@app.route('/api_status')
def api_status():
    key = session.get('api_key')
    if not key: return jsonify({'status': 'no_key'})
    # 简单掩码显示
    masked_key = key[:4] + "..." + key[-4:]
    return jsonify({'status': 'active', 'key_display': masked_key})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))