import os
import datetime
import isodate
from flask import Flask, render_template, request, session, redirect, url_for
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

app = Flask(__name__)
app.secret_key = 'viral_monkey_pro_secret_key'

# === 工具函数 ===
def format_number(num):
    if not num: return "0"
    num = float(num)
    if num >= 1000000: return f"{num/1000000:.1f}M"
    if num >= 1000: return f"{num/1000:.1f}K"
    return str(int(num))

app.jinja_env.filters['fmt_num'] = format_number

# === 核心逻辑：智能搜索 ===
def search_videos(api_key, query, order='viewCount', duration='24h', page_token=None):
    youtube = build('youtube', 'v3', developerKey=api_key)
    
    # 1. 计算时间窗口
    hours_map = {'24h': 24, '72h': 72, '7d': 168, '30d': 720}
    hours = hours_map.get(duration, 24)
    
    published_after = None
    if duration != 'all':
        time_window = datetime.datetime.utcnow() - datetime.timedelta(hours=hours)
        published_after = time_window.isoformat("T") + "Z"

    # 2. 策略调整 (关键修复)
    # 如果有时间限制，强制使用 relevance (相关度)，否则 API 配合 viewCount 经常返回空或忽略时间
    # 我们先抓取大量“相关”视频，然后在本地按热度排序
    api_order = order
    if duration != 'all':
        api_order = 'relevance'
    
    search_params = {
        'q': query,
        'part': 'id,snippet',
        'maxResults': 50, # 抓取更多样本以供本地筛选
        'type': 'video',
        'videoDuration': 'short',
        'order': api_order,
        'pageToken': page_token
    }
    if published_after:
        search_params['publishedAfter'] = published_after

    try:
        search_response = youtube.search().list(**search_params).execute()
    except HttpError as e:
        # 容错：如果 API 不支持某种组合，降级重试
        if e.resp.status == 400:
             del search_params['order']
             del search_params['publishedAfter']
             search_response = youtube.search().list(**search_params).execute()
        else:
            raise e
    
    next_page_token = search_response.get('nextPageToken')
    video_ids = [item['id']['videoId'] for item in search_response.get('items', [])]

    if not video_ids:
        return [], None

    # 3. 获取详细数据
    stats_response = youtube.videos().list(
        id=','.join(video_ids),
        part='snippet,statistics,contentDetails'
    ).execute()

    videos = []
    for item in stats_response['items']:
        stats = item['statistics']
        snippet = item['snippet']
        
        view_count = int(stats.get('viewCount', 0))
        # 移除严格的播放量过滤，防止新视频被误删
        # 但过滤掉 0 播放的无效数据
        if view_count <= 0: continue

        pub_time = isodate.parse_datetime(snippet['publishedAt'])
        now = datetime.datetime.now(datetime.timezone.utc)
        hours_ago = max(0.1, (now - pub_time).total_seconds() / 3600)
        
        # VPH 计算
        vph = int(view_count / hours_ago)

        # 时间显示优化
        if hours_ago < 24:
            ago_str = f"{int(hours_ago)}h ago"
        else:
            ago_str = f"{int(hours_ago/24)}d ago"

        videos.append({
            'id': item['id'],
            'title': snippet['title'],
            'thumb': snippet['thumbnails'].get('high', snippet['thumbnails']['default'])['url'],
            'views': view_count,
            'vph': vph,
            'channel': snippet['channelTitle'],
            'channel_id': snippet['channelId'],
            'ago': ago_str,
            'published': pub_time
        })
    
    # 4. 本地智能排序
    # 如果用户选了 viewCount，我们在本地按 VPH (真实热度) 重新排
    if order == 'viewCount':
        videos.sort(key=lambda x: x['vph'], reverse=True)
    elif order == 'date':
        videos.sort(key=lambda x: x['published'], reverse=True)
    
    return videos[:18], next_page_token # 只返回前18个最优质的

# === 核心逻辑：频道分析 (补全) ===
def get_channel_stats(api_key, channel_id):
    youtube = build('youtube', 'v3', developerKey=api_key)
    try:
        chan_resp = youtube.channels().list(id=channel_id, part='snippet,statistics').execute()
        if not chan_resp['items']: return None
        channel = chan_resp['items'][0]
        
        # 获取最新 Shorts
        search_resp = youtube.search().list(
            channelId=channel_id, part='id', order='date', type='video', videoDuration='short', maxResults=10
        ).execute()
        
        video_ids = [i['id']['videoId'] for i in search_resp.get('items', [])]
        latest_shorts = []
        
        if video_ids:
            vid_resp = youtube.videos().list(id=','.join(video_ids), part='snippet,statistics').execute()
            for item in vid_resp['items']:
                pub = isodate.parse_datetime(item['snippet']['publishedAt'])
                now = datetime.datetime.now(datetime.timezone.utc)
                hours = max(0.1, (now - pub).total_seconds()/3600)
                views = int(item['statistics'].get('viewCount', 0))
                
                latest_shorts.append({
                    'id': item['id'],
                    'title': item['snippet']['title'],
                    'thumb': item['snippet']['thumbnails']['medium']['url'],
                    'views': views,
                    'vph': int(views/hours)
                })

        return {
            'id': channel_id,
            'title': channel['snippet']['title'],
            'thumb': channel['snippet']['thumbnails']['medium']['url'],
            'subs': int(channel['statistics']['subscriberCount']),
            'total_views': int(channel['statistics']['viewCount']),
            'video_count': int(channel['statistics']['videoCount']),
            'latest_shorts': latest_shorts
        }
    except:
        return None

# === 路由 ===
@app.route('/', methods=['GET', 'POST'])
def index():
    api_key = session.get('api_key', '')
    if request.method == 'POST' and request.form.get('new_api_key'):
        api_key = request.form.get('new_api_key').strip()
        session['api_key'] = api_key
        return redirect(url_for('index')) # 刷新页面清除 POST 数据

    # 统一使用 GET 参数，方便分享链接
    query = request.args.get('query', '')
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
                error = "未找到视频，建议切换排序方式或放宽时间限制。"
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
    api_key = session.get('api_key')
    if not api_key: return redirect(url_for('index'))
    data = get_channel_stats(api_key, channel_id)
    if not data: return "Channel not found or API error"
    return render_template('channel.html', c=data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))