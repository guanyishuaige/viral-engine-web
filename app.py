import os
import datetime
import isodate
import re
from flask import Flask, render_template, request, session, redirect, url_for
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

app = Flask(__name__)
app.secret_key = 'viral_monkey_pro_secret_key_v6_channels'

# === 数字格式化 ===
def format_number(num):
    if not num: return "0"
    try:
        num = int(num)
        if num < 10000:
            return str(num)
        else:
            return f"{int(num/10000)}W"
    except:
        return str(num)

app.jinja_env.filters['fmt_num'] = format_number

# === 多Key轮询逻辑 (通用) ===
def execute_with_fallback(api_keys, func, *args, **kwargs):
    if not api_keys or len(api_keys) == 0:
        raise Exception("请先配置 API Key")

    for i, key in enumerate(api_keys):
        try:
            return func(key, *args, **kwargs)
        except HttpError as e:
            if e.resp.status in [403, 429]:
                print(f"Key {i+1} 配额耗尽，切换下一个...")
                continue
            else:
                raise e
        except Exception as e:
            print(f"Key {i+1} 错误: {e}")
            continue
    raise Exception(f"所有 {len(api_keys)} 个 Key 都已失效。")

# === 1. 视频搜索核心逻辑 ===
def _search_videos_logic(api_key, query, duration, page_token):
    youtube = build('youtube', 'v3', developerKey=api_key)
    
    hours_map = {'24h': 24, '72h': 72, '7d': 168, '30d': 720}
    hours = hours_map.get(duration, 72)
    
    published_after = None
    if duration != 'all':
        time_window = datetime.datetime.utcnow() - datetime.timedelta(hours=hours)
        published_after = time_window.isoformat(timespec='seconds') + "Z"

    search_params = {
        'q': query, 'part': 'id,snippet', 'maxResults': 50,
        'type': 'video', 'videoDuration': 'short', 'pageToken': page_token
    }
    if published_after: search_params['publishedAfter'] = published_after

    items = []
    next_page_token = None
    
    # 策略：ViewCount -> Relevance -> Date
    strategies = ['viewCount', 'relevance', 'date']
    for strategy in strategies:
        try:
            search_params['order'] = strategy
            response = youtube.search().list(**search_params).execute()
            items = response.get('items', [])
            next_page_token = response.get('nextPageToken')
            if items: break
        except HttpError as e:
            if e.resp.status not in [403, 429]: raise e
            else: raise e # 抛给外层

    if not items: return [], None

    video_ids = [item['id']['videoId'] for item in items if 'videoId' in item['id']]
    if not video_ids: return [], None

    stats_response = youtube.videos().list(id=','.join(video_ids), part='snippet,statistics').execute()

    videos = []
    for item in stats_response['items']:
        stats = item['statistics']
        snippet = item['snippet']
        view_count = int(stats.get('viewCount', 0))
        if view_count <= 0: continue
        
        pub_time = isodate.parse_datetime(snippet['publishedAt'])
        now = datetime.datetime.now(datetime.timezone.utc)
        hours_ago = max(0.1, (now - pub_time).total_seconds() / 3600)
        
        ago_str = f"{int(hours_ago)}h ago" if hours_ago < 24 else f"{int(hours_ago/24)}d ago"

        videos.append({
            'id': item['id'],
            'title': snippet['title'],
            'thumb': snippet['thumbnails'].get('high', snippet['thumbnails']['default'])['url'],
            'views': view_count,
            'vph': int(view_count / hours_ago),
            'channel': snippet['channelTitle'],
            'channel_id': snippet['channelId'],
            'ago': ago_str
        })
    
    videos.sort(key=lambda x: x['views'], reverse=True)
    return videos, next_page_token

# === 2. 频道搜索核心逻辑 (反向侦察) ===
def _search_channels_logic(api_key, query):
    youtube = build('youtube', 'v3', developerKey=api_key)
    
    # 1. 搜过去30天最火的视频 (Shorts)
    # 我们不搜 type='channel'，因为那搜出来的不一定活跃。
    # 我们搜 type='video'，然后找这些视频的作者，这样能保证找到的是"最近流量大"的。
    time_window = datetime.datetime.utcnow() - datetime.timedelta(days=30)
    published_after = time_window.isoformat(timespec='seconds') + "Z"
    
    search_response = youtube.search().list(
        q=query, part='id,snippet', maxResults=50,
        type='video', videoDuration='short', order='viewCount',
        publishedAfter=published_after
    ).execute()
    
    if not search_response.get('items'): return []
    
    # 2. 提取唯一的 Channel ID，并记录是哪个爆款视频带来的
    channel_map = {} # {channel_id: {video_title, video_views, ...}}
    
    video_ids = [item['id']['videoId'] for item in search_response['items']]
    video_stats_resp = youtube.videos().list(id=','.join(video_ids), part='statistics,snippet').execute()
    
    for item in video_stats_resp['items']:
        chan_id = item['snippet']['channelId']
        views = int(item['statistics'].get('viewCount', 0))
        
        # 如果这个频道已经收录过，且当前视频播放量更低，就跳过（只记录该频道最火的那个视频）
        if chan_id in channel_map and channel_map[chan_id]['viral_views'] >= views:
            continue
            
        channel_map[chan_id] = {
            'viral_video_title': item['snippet']['title'],
            'viral_views': views,
            'viral_video_id': item['id']
        }
    
    if not channel_map: return []

    # 3. 批量获取频道详细信息 (头像、粉丝数)
    # 切片处理，每次最多50个
    unique_channel_ids = list(channel_map.keys())[:50]
    chan_resp = youtube.channels().list(
        id=','.join(unique_channel_ids),
        part='snippet,statistics'
    ).execute()
    
    channels = []
    for item in chan_resp['items']:
        c_id = item['id']
        viral_data = channel_map.get(c_id)
        if not viral_data: continue
        
        channels.append({
            'id': c_id,
            'title': item['snippet']['title'],
            'thumb': item['snippet']['thumbnails']['medium']['url'],
            'subs': int(item['statistics'].get('subscriberCount', 0)),
            'video_count': int(item['statistics'].get('videoCount', 0)),
            'viral_video_title': viral_data['viral_video_title'],
            'viral_views': viral_data['viral_views'],
            'viral_video_id': viral_data['viral_video_id']
        })
    
    # 按爆款视频的播放量排序
    channels.sort(key=lambda x: x['viral_views'], reverse=True)
    return channels

# === 路由 ===
@app.route('/', methods=['GET', 'POST'])
def index():
    api_keys = session.get('api_keys', [])
    
    if request.method == 'POST' and request.form.get('api_keys_input'):
        input_text = request.form.get('api_keys_input')
        keys = [k.strip() for k in re.split(r'[,\s\n]+', input_text) if k.strip()]
        session['api_keys'] = keys
        return redirect(url_for('index', mode=request.args.get('mode', 'videos')))

    # 获取参数
    mode = request.args.get('mode', 'videos') # videos 或 channels
    query = request.args.get('query', '')
    duration = request.args.get('duration', '72h')
    page_token = request.args.get('page_token', None)

    results = [] # 可以是 videos 也可以是 channels
    next_token = None
    error = None

    if api_keys and query:
        try:
            if mode == 'channels':
                # 频道搜索模式
                results = execute_with_fallback(api_keys, _search_channels_logic, query)
                if not results: error = "未找到相关的高流量频道。"
            else:
                # 视频搜索模式
                results, next_token = execute_with_fallback(api_keys, _search_videos_logic, query, duration, page_token)
                if not results: error = "未找到相关视频。"
                
        except Exception as e:
            error = f"系统错误: {str(e)}"

    return render_template('index.html', 
                         mode=mode,
                         results=results, 
                         query=query, 
                         api_keys=api_keys, 
                         duration=duration, 
                         next_token=next_token, 
                         error=error)

@app.route('/channel/<channel_id>')
def channel_analysis(channel_id):
    return render_template('channel.html', c={'title': '频道分析开发中', 'thumb': '', 'latest_shorts': []})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))