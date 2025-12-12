from flask import Flask, render_template, request, jsonify
import requests
import json
import sqlite3
from bs4 import BeautifulSoup

app = Flask(__name__)

# --- ⚠️ 중요: 네이버 API 인증 정보 입력 ⚠️ ---
# 실제 값으로 대체해야 합니다!
CLIENT_ID = "rp4gWjzI5KM1csxw_vrG" 
CLIENT_SECRET = ""
# ---------------------------------------------

# 네이버 블로그 검색 API URL
NAVER_API_URL = "https://openapi.naver.com/v1/search/blog.json"
DB_NAME = 'search_rank.db' # SQLite 데이터베이스 파일 이름

# ==========================================================
# 🔍 SQLite 데이터베이스 관리 함수
# ==========================================================

def get_db_connection():
    """데이터베이스 연결 객체를 반환합니다."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row # 컬럼 이름으로 데이터에 접근할 수 있도록 설정
    return conn

def init_db():
    """데이터베이스를 초기화(테이블 생성)합니다."""
    conn = get_db_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS keywords (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL UNIQUE,
            count INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    # 🎵 멜론 차트 저장 테이블 추가
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS melon_charts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rank INTEGER NOT NULL,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(rank, title, artist) -- 중복 데이터 방지
        );
        """
    )
    conn.commit()
    conn.close()

def save_search_query(query):
    """검색어를 저장하거나 횟수를 1 증가시킵니다."""
    conn = get_db_connection()
    try:
        # 1. UPDATE 실행: 커서 객체를 변수에 저장합니다.
        cursor = conn.execute(
            "UPDATE keywords SET count = count + 1 WHERE keyword = ?", (query,)
        )
        
        # 2. 커서 객체의 rowcount를 확인합니다.
        if cursor.rowcount == 0: 
            # 업데이트된 행이 없다면 (새로운 검색어라면) 삽입
            conn.execute(
                "INSERT INTO keywords (keyword) VALUES (?)", (query,)
            )
            
        conn.commit()
    except sqlite3.Error as e:
        print(f"Database error: {e}")
    finally:
        conn.close()

def get_top_keywords(limit=10):
    """검색 횟수가 많은 상위 키워드를 가져옵니다."""
    conn = get_db_connection()
    # count 기준 내림차순 정렬하여 상위 limit개만 선택
    keywords = conn.execute(
        "SELECT keyword, count FROM keywords ORDER BY count DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return keywords

def save_melon_chart_data(chart_data):
    """멜론 차트 데이터를 DB에 저장합니다."""
    conn = get_db_connection()
    # 기존 차트 데이터를 삭제하지 않고 저장 시점의 기록을 남기려면 이 부분을 생략하거나,
    # 주기적인 차트 업데이트라면 기존 데이터를 비우는 로직을 추가할 수 있습니다.
    # 여기서는 간단히 UNIQUE 제약조건으로 중복을 방지하고 새 데이터만 추가하는 방식으로 진행합니다.
    try:
        for item in chart_data:
            conn.execute(
                """
                INSERT OR IGNORE INTO melon_charts (rank, title, artist)
                VALUES (?, ?, ?)
                """, 
                (item['rank'], item['title'], item['artist'])
            )
        conn.commit()
        return True
    except sqlite3.Error as e:
        print(f"멜론 차트 DB 저장 오류: {e}")
        return False
    finally:
        conn.close()

def get_artist_songs(artist_name):
    """DB에서 특정 가수의 곡 목록을 가져옵니다."""
    conn = get_db_connection()
    # 멜론 차트 테이블에서 검색어가 'artist' 컬럼에 포함된 모든 곡을 가져옵니다.
    songs = conn.execute(
        """
        SELECT rank, title, artist, recorded_at 
        FROM melon_charts 
        WHERE artist LIKE ? 
        ORDER BY rank ASC
        """, 
        ('%' + artist_name + '%',) # LIKE 검색을 위해 와일드카드(%) 사용
    ).fetchall()
    conn.close()
    return songs

def get_artist_count_ranking():
    """DB에서 멜론 차트 내 가수별 노래 갯수 순위를 가져옵니다."""
    conn = get_db_connection()
    
    # SQL 쿼리 설명:
    # 1. SELECT artist, COUNT(*) as song_count: 가수 이름과 해당 가수의 노래 갯수를 센다.
    # 2. FROM melon_charts: melon_charts 테이블에서 데이터를 가져온다.
    # 3. GROUP BY artist: 같은 가수의 행을 묶는다.
    # 4. ORDER BY song_count DESC, artist ASC: 노래 갯수(song_count) 기준으로 내림차순 정렬하고,
    #    갯수가 같으면 가수 이름(artist) 기준으로 오름차순 정렬한다.
    ranking = conn.execute(
        """
        SELECT artist, COUNT(*) as song_count 
        FROM melon_charts 
        GROUP BY artist 
        ORDER BY song_count DESC, artist ASC
        """
    ).fetchall()
    
    conn.close()
    return ranking

# ==========================================================
# 🎵 멜론 차트 크롤링 함수
# ==========================================================

def get_melon_chart_data():
    """멜론 실시간 차트 상위 50위 데이터를 크롤링하여 반환합니다."""
    url = "https://www.melon.com/chart/index.htm"
    
    # 멜론 서버의 접근 차단을 피하기 위해 User-Agent를 설정하는 것이 중요합니다.
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status() # HTTP 오류가 발생하면 예외 발생
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        chart_list = []
        # 멜론 차트의 주요 목록 태그 선택자 (클래스명은 시간이 지나면 변경될 수 있습니다.)
        # lst50과 lst100 클래스를 모두 포함하는 행을 선택
        rows = soup.select('tr.lst50, tr.lst100') 
        
        for row in rows:
            # 순위 추출
            # .rank01 > span.none 또는 .rank
            rank_text = row.select_one('.rank').text.strip()
            
            # 곡명 추출
            title_tag = row.select_one('div.ellipsis.rank01 a')
            title = title_tag.text.strip() if title_tag else 'N/A'
            
            # 아티스트 추출
            artist_tag = row.select_one('div.ellipsis.rank02 a')
            artist = artist_tag.text.strip() if artist_tag else 'N/A'
            
            chart_list.append({
                'rank': rank_text,
                'title': title,
                'artist': artist
            })
            
    except requests.exceptions.RequestException as e:
        print(f"멜론 차트 크롤링 실패: {e}")
        return []
        
    return chart_list


# ==========================================================
# 🌐 Flask 라우팅 설정
# ==========================================================

@app.route('/artist_rank')
def artist_rank():
    """가수별 노래 갯수 순위 페이지를 보여줍니다."""
    # DB에서 순위 데이터를 가져옵니다.
    artist_ranking = get_artist_count_ranking()
    
    # 노래가 없는 경우를 제외하기 위해 song_count가 0보다 큰 경우만 전달할 수 있으나, 
    # GROUP BY를 사용했으므로 기본적으로 1개 이상의 노래가 있는 가수만 나옴.
    return render_template('artist_ranking.html', artist_ranking=artist_ranking)

@app.route('/search_artist', methods=['GET'])
def search_artist():
    """가수 검색 결과를 보여줍니다."""
    artist_query = request.args.get('artist_name')
    artist_info = []
    
    if artist_query:
        artist_info = get_artist_songs(artist_query)
    
    return render_template(
        'artist_search.html', 
        artist_query=artist_query, 
        artist_info=artist_info
    )

@app.route('/melon_chart')
def melon_chart():
    """멜론 차트 페이지를 보여줍니다."""
    chart_data = get_melon_chart_data()
    # 크롤링 성공 시 DB에 저장
    if chart_data:
        save_result = save_melon_chart_data(chart_data)
        if not save_result:
            print("DB 저장에 실패했지만 차트는 표시합니다.")

    return render_template('melon_chart.html', chart_data=chart_data)

@app.route('/search_blog', methods=['GET', 'POST'])
def search_blog():
    search_results = None
    if request.method == 'POST':
        query = request.form.get('query')
        if query:
            # 1. 검색어 DB에 저장/업데이트
            save_search_query(query)

            # 1. API 요청 헤더 설정
            headers = {
                "X-Naver-Client-Id": CLIENT_ID,
                "X-Naver-Client-Secret": CLIENT_SECRET
            }
            
            # 2. API 요청 파라미터 설정
            # query: 검색어, display: 검색 결과 수 (최대 100), sort: 정렬 옵션 (sim: 정확도순, date: 날짜순)
            params = {
                "query": query, # 검색어에 "맛집"을 추가하여 블로그 검색 정확도 높이기
                "display": 20,
                "sort": "sim" 
            }

            # 3. 네이버 API 호출
            response = requests.get(NAVER_API_URL, headers=headers, params=params)
            
            if response.status_code == 200:
                data = response.json()
                search_results = data.get('items')
            else:
                # API 호출 오류 처리
                print(f"Error: {response.status_code}, {response.text}")

    # GET 요청이나 검색 결과가 없는 경우 None을 전달
    return render_template('index.html', search_results=search_results)

@app.route('/ranking')
def ranking():
    """인기 검색어 순위 페이지를 보여줍니다."""
    top_keywords = get_top_keywords(limit=10) # 상위 10개 키워드
    return render_template('ranking.html', top_keywords=top_keywords)

@app.route('/')
def hello():
    """메인 메뉴 페이지를 보여줍니다."""
    return render_template('index.html')
    # return 'Hello, World!'

with app.app_context():
    init_db()

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0',debug=True)