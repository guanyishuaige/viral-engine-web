import os
import datetime
import isodate
import re
from flask import Flask, render_template, request, session, redirect, url_for
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

app = Flask(__name__)
app.secret_key = 'viral_monkey_pro_secret_key_v5_72h'

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

# === 多Key轮询逻辑 ===
def search_with_fallback(api_keys, query, duration='72h', page_token=None):
    if not api_keys or len(api_keys) == 0:
        raise Exception("请先配置 API Key")

    last_error = None
    
    for i, key in enumerate(api_keys):
        try:
            return _execute_search(key, query, duration, page_token)
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

# === 核心搜索逻辑 ===
def _execute_search(api_key, query, duration, page_token):
    youtube = build('youtube', 'v3', developerKey=api_key)
    
    # 1. 计算时间
    hours_map = {'24h': 24, '72h': 72, '7d': 168, '30d': 720}
    hours = hours_map.get(duration, 72) # 默认 72 小时
    
    published_after = None
    if duration != 'all':
        time_window = datetime.datetime.utcnow() - datetime.timedelta(hours=hours)
        published_after = time_window.isoformat(timespec='seconds') + "Z"

    # 2. 搜索参数
    search_params = {
        'q': query,
        'part': 'id,snippet',
        'maxResults': 50,
        'type': 'video',
        'videoDuration': 'short',
        'pageToken': page_token
    }
    if published_after:
        search_params['publishedAfter'] = published_after

    items = []
    next_page_token = None
    
    # === 策略：在时间限制内找最火的 ===
    try:
        # [尝试1] 直接按 ViewCount 搜 (最理想，但API可能返回0)
        search_params['order'] = 'viewCount'
        response = youtube.search().list(**search_params).execute()
        items = response.get('items', [])
        next_page_token = response.get('nextPageToken')

        # [尝试2] 如果没结果，按 Relevance 搜 (保证有数据)
        if not items:
            print("ViewCount 策略无结果，切换 Relevance...")
            search_params['order'] = 'relevance'
            response = youtube.search().list(**search_params).execute()
            items = response.get('items', [])
            next_page_token = response.get('nextPageToken')
        
        # [尝试3] 实在不行，按 Date 搜 (保底)
        if not items:
            print("Relevance 策略无结果，切换 Date...")
            search_params['order'] = 'date'
            response = youtube.search().list(**search_params).execute()
            items = response.get('items', [])
            next_page_token = response.get('nextPageToken')

    except HttpError as e:
        if e.resp.status not in [403, 429]:
            raise e
        else:
            raise e

    if not items:
        return [], None

    # 3. 获取详情
    video_ids = [item['id']['videoId'] for item in items if 'videoId' in item['id']]
    if not video_ids: return [], None

    stats_response = youtube.videos().list(
        id=','.join(video_ids),
        part='snippet,statistics,contentDetails'
    ).execute()

    videos = []
    for item in stats_response['items']:
        stats = item['statistics']
        snippet = item['snippet']
        
        view_count = int(stats.get('viewCount', 0))
        if view_count <= 0: continue

        pub_time = isodate.parse_datetime(snippet['publishedAt'])
        now = datetime.datetime.now(datetime.timezone.utc)
        hours_ago = max(0.1, (now - pub_time).total_seconds() / 3600)
        vph = int(view_count / hours_ago)

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
    
    # 4. 强制按【播放量】排序
    videos.sort(key=lambda x: x['views'], reverse=True)
    
    return videos, next_page_token

# === 路由 ===
@app.route('/', methods=['GET', 'POST'])
def index():
    api_keys = session.get('api_keys', [])
    
    if request.method == 'POST' and request.form.get('api_keys_input'):
        input_text = request.form.get('api_keys_input')
        keys = [k.strip() for k in re.split(r'[,\s\n]+', input_text) if k.strip()]
        session['api_keys'] = keys
        return redirect(url_for('index'))

    query = request.args.get('query', '')
    duration = request.args.get('duration', '72h') # 默认改为 72h
    page_token = request.args.get('page_token', None)

    videos = []
    next_token = None
    error = None

    if api_keys and query:
        try:
            videos, next_token = search_with_fallback(api_keys, query, duration, page_token)
            if not videos:
                error = f"未找到 '{query}' 的相关视频。"
        except Exception as e:
            error = f"系统错误: {str(e)}"

    return render_template('index.html', 
                         videos=videos, 
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