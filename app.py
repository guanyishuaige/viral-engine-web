import os
import datetime
import isodate
from flask import Flask, render_template, request, session, redirect, url_for
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

app = Flask(__name__)
app.secret_key = 'super_secret_key_viral_monkey_pro'

# === 工具函数 ===
def format_number(num):
    if not num: return "0"
    num = float(num)
    if num >= 1000000: return f"{num/1000000:.1f}M"
    if num >= 1000: return f"{num/1000:.1f}K"
    return str(int(num))

app.jinja_env.filters['fmt_num'] = format_number

# === 核心修复逻辑 ===
def search_videos(api_key, query, order='viewCount', duration='24h', page_token=None):
    youtube = build('youtube', 'v3', developerKey=api_key)
    
    # 1. 计算时间窗口
    hours_map = {'24h': 24, '72h': 72, '7d': 168, '30d': 720} # 修正了 duration 键名
    hours = hours_map.get(duration, 24)
    
    time_window = datetime.datetime.utcnow() - datetime.timedelta(hours=hours)
    published_after = time_window.isoformat("T") + "Z"

    # --- 策略调整 ---
    # 如果用户想看 "View Count" (最火)，我们强制 API 按 "Date" (最新) 给我们数据
    # 然后我们在代码里自己排 "View Count"。
    # 为什么？因为 YouTube API 无法同时处理 publishedAfter + viewCount。
    api_order = 'date' if order == 'viewCount' else order
    
    # 我们多抓一点数据(maxResults=50)，以便我们在本地有足够的样本进行排序
    limit = 50 if order == 'viewCount' else 18

    search_params = {
        'q': query,
        'part': 'id,snippet',
        'maxResults': limit,
        'type': 'video',
        'videoDuration': 'short',
        'order': api_order,
        'pageToken': page_token,
        'publishedAfter': published_after
    }

    try:
        search_response = youtube.search().list(**search_params).execute()
    except HttpError as e:
        if e.resp.status == 400:
             # 如果 API 报错，尝试去掉 order 参数重试
             del search_params['order']
             search_response = youtube.search().list(**search_params).execute()
        else:
            raise e
    
    next_page_token = search_response.get('nextPageToken')
    video_ids = [item['id']['videoId'] for item in search_response.get('items', [])]

    if not video_ids:
        return [], None

    # 2. 获取详细数据
    stats_response = youtube.videos().list(
        id=','.join(video_ids),
        part='snippet,statistics,contentDetails'
    ).execute()

    videos = []
    for item in stats_response['items']:
        stats = item['statistics']
        snippet = item['snippet']
        
        view_count = int(stats.get('viewCount', 0))
        # 过滤掉播放量太低的（比如少于100），减少垃圾内容
        if view_count < 10: continue

        pub_time = isodate.parse_datetime(snippet['publishedAt'])
        now = datetime.datetime.now(datetime.timezone.utc)
        hours_ago = max(0.1, (now - pub_time).total_seconds() / 3600)
        
        # VPH (每小时浏览量) - 爆款的核心指标
        vph = int(view_count / hours_ago)

        # 格式化时间显示
        if hours_ago < 24:
            ago_str = f"{int(hours_ago)}h ago"
        else:
            days = int(hours_ago / 24)
            ago_str = f"{days}d ago"

        videos.append({
            'id': item['id'],
            'title': snippet['title'],
            'thumb': snippet['thumbnails'].get('high', snippet['thumbnails']['default'])['url'],
            'views': view_count,
            'vph': vph,
            'channel': snippet['channelTitle'],
            'channel_id': snippet['channelId'],
            'ago': ago_str
        })
    
    # 3. 本地手动排序
    # 如果用户选了 "viewCount"，我们在本地按 VPH (热度) 排序，这比单纯看总播放量更准
    if order == 'viewCount':
        videos.sort(key=lambda x: x['vph'], reverse=True)
    
    # 无论如何，只返回前 18 个最优质的给前端
    return videos[:18], next_page_token

# === 路由 (保持不变) ===
@app.route('/', methods=['GET', 'POST'])
def index():
    api_key = session.get('api_key', '')
    if request.method == 'POST' and request.form.get('new_api_key'):
        api_key = request.form.get('new_api_key')
        session['api_key'] = api_key

    query = request.args.get('query', request.form.get('query', ''))
    order = request.args.get('order', 'viewCount')
    duration = request.args.get('duration', '24h')
    page_token = request.args.get('page_token', None)

    videos = []
    next_token = None
    error = None

    if api_key and query:
        try:
            videos, next_token = search_videos(api_key, query, order, duration, page_token)
            if not videos:
                error = "未找到符合条件的视频（可能是时间限制太严或关键词太冷门）"
        except Exception as e:
            error = f"API Error: {str(e)}"

    return render_template('index.html', 
                         videos=videos, 
                         query=query, 
                         api_key=api_key, 
                         order=order, 
                         duration=duration, 
                         next_token=next_token, 
                         error=error)

@app.route('/channel/<channel_id>')
def channel_analysis(channel_id):
    # 这里你需要把之前写的 channel_analysis 函数补回来，或者如果你没存，告诉我我再发一遍
    return "Channel Analysis Page (请确保这里有完整代码)"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))