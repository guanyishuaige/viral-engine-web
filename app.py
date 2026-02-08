import os
import datetime
import isodate
from flask import Flask, render_template, request, session
from googleapiclient.discovery import build

app = Flask(__name__)
# 必须设置密钥才能使用 session，随便写一串乱码即可
app.secret_key = 'super_secret_key_viral_engine_2026'

# === YouTube API 核心逻辑 ===
def search_youtube(query, api_key):
    try:
        youtube = build('youtube', 'v3', developerKey=api_key)
        # 搜索过去 48 小时的 Shorts
        time_window = datetime.datetime.utcnow() - datetime.timedelta(hours=48)
        published_after = time_window.isoformat("T") + "Z"

        search_response = youtube.search().list(
            q=query, part='id', maxResults=12, order='viewCount',
            type='video', publishedAfter=published_after, videoDuration='short'
        ).execute()

        video_ids = [item['id']['videoId'] for item in search_response.get('items', [])]
        if not video_ids: return []

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
    except Exception as e:
        print(f"Error: {e}")
        return []

# === 路由 ===
@app.route('/', methods=['GET', 'POST'])
def index():
    # 优先从表单获取 Key，其次从 Session 获取
    api_key = request.form.get('api_key') or session.get('api_key', '')
    query = request.form.get('query', '')
    
    videos = []
    error = None

    if request.method == 'POST':
        # 如果用户提交了 Key，保存到 Session
        if request.form.get('api_key'):
            session['api_key'] = api_key
        
        if not api_key:
            error = "请输入 API Key"
        elif query:
            videos = search_youtube(query, api_key)
            if not videos:
                error = "未找到相关视频 (或 API Key 无效)"

    return render_template('index.html', videos=videos, api_key=api_key, error=error, query=query)

@app.route('/analyze/<video_id>')
def analyze(video_id):
    # 详情页也从 Session 获取 Key
    api_key = session.get('api_key')
    if not api_key:
        return "请先在主页设置 API Key"
        
    try:
        youtube = build('youtube', 'v3', developerKey=api_key)
        response = youtube.videos().list(id=video_id, part='snippet,statistics').execute()
        if not response['items']: return "Video not found"
        
        item = response['items'][0]
        snippet = item['snippet']
        stats = item['statistics']
        
        video_data = {
            'id': video_id,
            'title': snippet['title'],
            'channel': snippet['channelTitle'],
            'views': int(stats.get('viewCount', 0)),
            'likes': int(stats.get('likeCount', 0)),
            'thumb': snippet['thumbnails']['high']['url'],
            'tags': snippet.get('tags', []),
            'published': snippet['publishedAt']
        }
        return render_template('detail.html', v=video_data)
    except Exception as e:
        return f"Error: {e}"

if __name__ == '__main__':
    # 这里的 host='0.0.0.0' 是部署的关键
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))