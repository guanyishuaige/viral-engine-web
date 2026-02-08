import os
import datetime
import isodate
from flask import Flask, render_template, request, session, redirect, url_for
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

app = Flask(__name__)
app.secret_key = 'super_secret_key_viral_monkey_pro'

# === 工具函数：格式化数字 (1.2M, 500K) ===
def format_number(num):
    num = float(num)
    if num >= 1000000:
        return f"{num/1000000:.1f}M"
    if num >= 1000:
        return f"{num/1000:.1f}K"
    return str(int(num))

app.jinja_env.filters['fmt_num'] = format_number

# === 核心逻辑：获取视频列表 ===
def search_videos(api_key, query, order='viewCount', duration='24h', page_token=None):
    youtube = build('youtube', 'v3', developerKey=api_key)
    
    # 1. 时间过滤
    hours_map = {'24h': 24, '7d': 168, '30d': 720, 'all': 0}
    hours = hours_map.get(duration, 24)
    
    published_after = None
    if hours > 0:
        time_window = datetime.datetime.utcnow() - datetime.timedelta(hours=hours)
        published_after = time_window.isoformat("T") + "Z"

    # 2. 搜索请求
    search_params = {
        'q': query,
        'part': 'id,snippet',
        'maxResults': 18,
        'type': 'video',
        'videoDuration': 'short',
        'order': order,
        'pageToken': page_token
    }
    if published_after:
        search_params['publishedAfter'] = published_after

    search_response = youtube.search().list(**search_params).execute()
    
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
        
        # VPH 计算
        view_count = int(stats.get('viewCount', 0))
        pub_time = isodate.parse_datetime(snippet['publishedAt'])
        now = datetime.datetime.now(datetime.timezone.utc)
        hours_ago = max(0.1, (now - pub_time).total_seconds() / 3600)
        vph = int(view_count / hours_ago)

        videos.append({
            'id': item['id'],
            'title': snippet['title'],
            'thumb': snippet['thumbnails'].get('high', snippet['thumbnails']['default'])['url'],
            'views': view_count,
            'likes': int(stats.get('likeCount', 0)),
            'comments': int(stats.get('commentCount', 0)),
            'vph': vph,
            'channel': snippet['channelTitle'],
            'channel_id': snippet['channelId'],
            'published': pub_time.strftime('%Y-%m-%d'),
            'ago': f"{int(hours_ago)}h ago" if hours_ago < 24 else f"{int(hours_ago/24)}d ago"
        })
    
    # 如果不是按日期排序，我们手动按 VPH 再排一次以确保热门
    if order == 'viewCount':
        videos.sort(key=lambda x: x['vph'], reverse=True)

    return videos, next_page_token

# === 核心逻辑：频道分析 ===
def get_channel_stats(api_key, channel_id):
    youtube = build('youtube', 'v3', developerKey=api_key)
    
    # 1. 频道基础信息
    chan_resp = youtube.channels().list(id=channel_id, part='snippet,statistics,contentDetails').execute()
    if not chan_resp['items']: return None
    channel = chan_resp['items'][0]
    
    # 2. 获取该频道最新的 10 个 Shorts
    uploads_id = channel['contentDetails']['relatedPlaylists']['uploads']
    # 注意：这里简化逻辑，直接搜频道视频，比查播放列表更准确实时
    search_resp = youtube.search().list(
        channelId=channel_id, part='id', order='date', type='video', videoDuration='short', maxResults=12
    ).execute()
    
    video_ids = [i['id']['videoId'] for i in search_resp.get('items', [])]
    latest_shorts = []
    
    if video_ids:
        vid_resp = youtube.videos().list(id=','.join(video_ids), part='snippet,statistics').execute()
        for item in vid_resp['items']:
            latest_shorts.append({
                'id': item['id'],
                'title': item['snippet']['title'],
                'thumb': item['snippet']['thumbnails']['medium']['url'],
                'views': int(item['statistics'].get('viewCount', 0)),
                'vph': int(int(item['statistics'].get('viewCount', 0)) / max(0.1, (datetime.datetime.now(datetime.timezone.utc) - isodate.parse_datetime(item['snippet']['publishedAt'])).total_seconds()/3600))
            })

    return {
        'title': channel['snippet']['title'],
        'thumb': channel['snippet']['thumbnails']['medium']['url'],
        'subs': int(channel['statistics']['subscriberCount']),
        'total_views': int(channel['statistics']['viewCount']),
        'video_count': int(channel['statistics']['videoCount']),
        'latest_shorts': latest_shorts
    }

# === 路由 ===
@app.route('/', methods=['GET', 'POST'])
def index():
    api_key = session.get('api_key', '')
    if request.method == 'POST' and request.form.get('new_api_key'):
        api_key = request.form.get('new_api_key')
        session['api_key'] = api_key

    query = request.args.get('query', request.form.get('query', ''))
    order = request.args.get('order', 'viewCount') # date, viewCount, relevance
    duration = request.args.get('duration', '24h')
    page_token = request.args.get('page_token', None)

    videos = []
    next_token = None
    error = None

    if api_key and query:
        try:
            videos, next_token = search_videos(api_key, query, order, duration, page_token)
        except Exception as e:
            error = str(e)

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
    return render_template('channel.html', c=data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))