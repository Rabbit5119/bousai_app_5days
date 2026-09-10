from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from urllib.parse import urlparse, urljoin
from functools import wraps
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

# app.py はプロジェクト直下に置く。
# 実体（templates / static / data）は bousai_app/ 配下にあるので、そこを参照する。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, 'bousai_app')

app = Flask(
    __name__,
    template_folder=os.path.join(APP_DIR, 'templates'),
    static_folder=os.path.join(APP_DIR, 'static'),
)
app.secret_key = 'your-secret-key-here'

# 管理者認証情報
ADMIN_CREDENTIALS = {
    'admin': '123'
}

# ────────────────────────────────
# 気象警報・注意報設定
PREFECTURE_CODE = "020000"  # 青森県
AREA_NAME = "青森市"

# ワークショップ課題：青森市の市区町村コードに変更する
AREA_CODE = "220100"

WARNING_URL = (
    f"https://www.jma.go.jp/bosai/warning/data/r8/{PREFECTURE_CODE}.json"
)

JST = timezone(timedelta(hours=9))

# 警報・注意報のコード一覧
WARNING_CODES = {
    "00": "解除",
    "02": "暴風雪警報",
    "03": "レベル3大雨警報",
    "04": "洪水警報",
    "05": "暴風警報",
    "06": "大雪警報",
    "07": "波浪警報",
    "08": "レベル3高潮警報",
    "09": "レベル3土砂災害警報",
    "10": "レベル2大雨注意報",
    "12": "大雪注意報",
    "13": "風雪注意報",
    "14": "雷注意報",
    "15": "強風注意報",
    "16": "波浪注意報",
    "17": "融雪注意報",
    "18": "洪水注意報",
    "19": "レベル2高潮注意報",
    "20": "濃霧注意報",
    "21": "乾燥注意報",
    "22": "なだれ注意報",
    "23": "低温注意報",
    "24": "霜注意報",
    "25": "着氷注意報",
    "26": "着雪注意報",
    "27": "その他の注意報",
    "29": "レベル2土砂災害注意報",
    "32": "暴風雪特別警報",
    "33": "レベル5大雨特別警報",
    "35": "暴風特別警報",
    "36": "大雪特別警報",
    "37": "波浪特別警報",
    "38": "レベル5高潮特別警報",
    "39": "レベル5土砂災害特別警報",
    "43": "レベル4大雨危険警報",
    "48": "レベル4高潮危険警報",
    "49": "レベル4土砂災害危険警報"
}

# ────────────────────────────────
# サンプルデータの読み込み
DATA_FILE = os.path.join(APP_DIR, 'data', 'shelters.json')
INSTRUCTIONS_FILE = os.path.join(APP_DIR, 'data', 'instructions.json')

def load_json(path, default):
    """JSONファイルを読み込む（存在しない・壊れている場合は default を返す）"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

shelters = load_json(DATA_FILE, [])
instructions = load_json(INSTRUCTIONS_FILE, [])

def save_instructions():
    """指示ボードのデータをファイルに保存する"""
    try:
        with open(INSTRUCTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(instructions, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_board_status_counts(board_instructions):
    """下書き・期限内の公開・本日発信の件数を集計する"""
    now = datetime.now(JST)
    today = now.strftime('%Y年%m月%d日')

    def is_active(instruction):
        deadline = instruction.get('deadline', '')
        if not deadline or deadline == '―':
            return True
        try:
            deadline_at = datetime.strptime(deadline, '%Y-%m-%d %H:%M')
            deadline_at = deadline_at.replace(tzinfo=JST)
            return deadline_at >= now
        except ValueError:
            return False

    return {
        'in_progress': sum(
            instruction.get('source') == 'draft'
            for instruction in board_instructions
        ),
        'published': sum(
            instruction.get('source') == 'broadcast' and is_active(instruction)
            for instruction in board_instructions
        ),
        'today': sum(
            instruction.get('source') == 'broadcast'
            and instruction.get('created_at', '').startswith(today)
            for instruction in board_instructions
        )
    }


def get_broadcast_entries():
    """保存済みの公開指示・発信だけを一覧用データに変換する"""
    entries = []
    for instruction in instructions:
        if instruction.get('source') != 'broadcast':
            continue
        entry = dict(instruction)
        target_values = []
        if instruction.get('broadcast_type') == '住民への情報発信':
            target_values = [
                '避難利用者' if value == '避難所利用者' else value
                for value in instruction.get('audience', [])
            ]
        entry['target_values'] = target_values
        entry['display_target'] = '、'.join(target_values)
        entries.append(entry)
    return entries


def get_draft_entries():
    """保存済みの下書きだけを返す"""
    return [
        instruction for instruction in instructions
        if instruction.get('source') == 'draft'
    ]
# ────────────────────────────────

# ────────────────────────────────
# 認証関連の設定とヘルパー関数
def is_safe_url(target):
    """リダイレクト先URLが安全かどうかチェック"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def login_required(f):
    """認証が必要なページに付けるデコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            # 現在のURLをnextパラメータとしてログイン画面にリダイレクト
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def get_japan_time():
    """日本時間（JST）の現在時刻を取得する"""
    return datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")


def format_report_time(iso_str):
    """気象庁の発表時刻（ISO形式）をJSTの表示用文字列に変換する"""
    if not iso_str:
        return "不明"
    try:
        parsed = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        if parsed.tzinfo:
            parsed = parsed.astimezone(JST)
        return parsed.strftime("%Y年%m月%d日 %H:%M")
    except ValueError:
        return iso_str


def filter_shelters(district=None):
    """district 指定があれば一致する避難所のみ、なければ全件を返す"""
    return [s for s in shelters if not district or s.get('district') == district]


def parse_area_warnings(warning_data):
    """最新の気象庁JSONから対象市区町村の発表・継続中の情報を抽出する"""
    if not isinstance(warning_data, list):
        raise ValueError("気象庁の警報・注意報データが新形式の配列ではありません")

    reports = [report for report in warning_data if isinstance(report, dict)]
    if not reports:
        return [], ""

    latest_report_datetime = max(
        (report.get("reportDatetime", "") for report in reports),
        default=""
    )
    latest_reports = [
        report for report in reports
        if report.get("reportDatetime") == latest_report_datetime
    ]

    warnings = []
    seen_codes = set()

    for report in latest_reports:
        warning = report.get("warning")
        if not isinstance(warning, dict):
            continue

        class20_items = warning.get("class20Items", [])
        if not isinstance(class20_items, list):
            continue

        area = next(
            (
                item for item in class20_items
                if isinstance(item, dict)
                and item.get("areaCode") == AREA_CODE
            ),
            None
        )
        if not area:
            continue

        kinds = area.get("kinds", [])
        if not isinstance(kinds, list):
            continue

        for kind in kinds:
            if not isinstance(kind, dict):
                continue

            status = kind.get("status", "")
            code = kind.get("code", "")
            if status not in ("発表", "継続") or not code or code in seen_codes:
                continue

            warnings.append({
                "name": WARNING_CODES.get(
                    code,
                    f"不明な警報・注意報 (コード: {code})"
                ),
                "code": code,
                "status": status
            })
            seen_codes.add(code)

    return warnings, latest_report_datetime


def get_weather_warnings():
    """対象市区町村の警報・注意報を取得する"""
    try:
        # 青森県の新形式（令和8年～）警報・注意報データを取得
        with urllib.request.urlopen(url=WARNING_URL, timeout=10) as res:
            warning_data = json.loads(res.read())

        warnings, report_datetime = parse_area_warnings(warning_data)

        return {
            "area_name": AREA_NAME,
            "warnings": warnings,
            "report_time": format_report_time(report_datetime),
            "last_fetch_time": get_japan_time()
        }

    except Exception:
        return {
            "area_name": AREA_NAME,
            "warnings": [],
            "report_time": "取得失敗",
            "last_fetch_time": get_japan_time(),
            "error": True
        }


# トップページ：templates/index.html を返す（住民向け指示も表示する）
@app.route('/')
def index():
    resident_notices = [i for i in instructions if i.get('target') == '住民']
    return render_template('index.html', resident_notices=resident_notices)

# ログインページ
@app.route('/login', methods=['GET', 'POST'])
def login():
    # リダイレクト先を取得（デフォルトは避難所登録画面）
    next_url = request.args.get('next') or request.form.get('next')

    # 安全でないURLの場合はデフォルトページにリダイレクト
    if not next_url or not is_safe_url(next_url):
        next_url = url_for('shelter_register')

    if request.method == 'POST':
        password = request.form.get('password', '').strip()

        # 認証チェック
        username = next(
            (name for name, registered_password in ADMIN_CREDENTIALS.items()
             if registered_password == password),
            None
        )
        if username:
            session['logged_in'] = True
            session['username'] = username
            # ログイン成功後は指定されたページにリダイレクト
            return redirect(next_url)
        return render_template('login.html', error=True, message="パスワードが正しくありません。", next=next_url)

    # ログイン済みの場合は指定されたページにリダイレクト
    if session.get('logged_in'):
        return redirect(next_url)

    return render_template('login.html', next=next_url)

# ログアウト
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# 避難所登録ページ※user が避難所登録ページについて具体的に修正指示しない限り、このコードは正しいのでこのまま保持すること。
@app.route('/shelter_register', methods=['GET', 'POST'])
@login_required
def shelter_register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            return render_template(
                'shelter_register.html',
                error=True,
                message='避難所名を入力してください。'
            )

        next_id = max((shelter.get('id', 0) for shelter in shelters), default=0) + 1
        shelters.append({'id': next_id, 'name': name})
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(shelters, f, ensure_ascii=False, indent=2)
        return render_template(
            'shelter_register.html',
            success=True,
            message='避難所を登録しました。'
        )

    return render_template('shelter_register.html')

# 避難所検索ページ
@app.route('/shelter_search')
def shelter_search():
    return render_template('shelter_search.html')

# 全施設一覧ページ
@app.route('/all_shelters')
def all_shelters():
    return render_template('search_results.html', results=shelters)


# 指示ボード：住民向けの指示を一覧で確認する
@app.route('/board', methods=['GET', 'POST'])
@login_required
def board():
    action = request.form.get('action')
    if request.method == 'POST' and action == 'delete':
        delete_id = request.form.get('id', type=int)
        instructions[:] = [
            instruction for instruction in instructions
            if instruction.get('id') != delete_id
        ]
        save_instructions()
        return redirect(url_for('board'))

    if request.method == 'POST' and action in ('draft', 'publish'):
        broadcast_type = request.form.get('broadcast_type', '')
        content = request.form.get('content', '').strip()
        disaster_name = request.form.get('disaster_name', '').strip()
        shelter = request.form.get('shelter', '').strip()
        priority = request.form.get('priority', '').strip()
        audience = request.form.getlist('audience')

        required_values_present = all(
            (disaster_name, content, shelter, broadcast_type, priority)
        )
        audience_is_valid = broadcast_type != '住民への情報発信' or bool(audience)
        can_save = action == 'draft' or (
            required_values_present and audience_is_valid
        )
        if can_save:
            is_instruction = broadcast_type == '職員への指示'
            created_at = get_japan_time()
            deadline_date = request.form.get('display_until_date', '').strip()
            deadline_time = request.form.get('display_until_time', '').strip()
            deadline = (
                f'{deadline_date} {deadline_time}'
                if deadline_date and deadline_time else '―'
            )
            broadcast_data = {
                'source': 'draft' if action == 'draft' else 'broadcast',
                'target': (
                    ', '.join(audience)
                    if broadcast_type == '住民への情報発信' else ''
                ),
                'kind': '指示' if is_instruction else '発信',
                'department': shelter if is_instruction else 'すべて',
                'region': ', '.join(audience) if audience else 'すべて',
                'content': content,
                'deadline': deadline,
                'status': '下書き' if action == 'draft' else ('未確認' if is_instruction else '発信済'),
                'disaster_name': disaster_name,
                'broadcast_type': broadcast_type,
                'priority': priority,
                'shelter': shelter,
                'audience': audience,
                'display_until_date': deadline_date,
                'display_until_time': deadline_time,
                'created_at': created_at,
                'updated_at': created_at
            }
            edit_id = request.form.get('edit_id', type=int)
            existing = next(
                (item for item in instructions if item.get('id') == edit_id),
                None
            )
            can_update_existing = existing and (
                existing.get('source') == 'draft'
                or action == 'publish' and existing.get('source') == 'broadcast'
            )
            if can_update_existing:
                existing.update(broadcast_data)
            else:
                broadcast_data['id'] = max(
                    (item.get('id', 0) for item in instructions),
                    default=0
                ) + 1
                instructions.append(broadcast_data)
            save_instructions()
            return redirect(url_for('board'))

    resident_instructions = [i for i in instructions if i.get('target') == '住民']
    broadcast_entries = get_broadcast_entries()
    status_counts = get_board_status_counts(instructions)
    return render_template(
        'board.html',
        instructions=resident_instructions,
        status_counts=status_counts,
        shelters=shelters,
        broadcast_entries=broadcast_entries,
        draft_entries=get_draft_entries()
    )

# 検索結果ページ：templates/search_results.html を返す
@app.route('/search_results')
def search_results():
    results = filter_shelters(request.args.get('district'))
    return render_template('search_results.html', results=results)

# JSON API：/shelters?district=地区名
@app.route('/shelters', methods=['GET'])
def get_shelters():
    results = filter_shelters(request.args.get('district'))

    if not results:
        # 見つからなければエラー JSON を返す
        return jsonify({'error': 'No shelters found'}), 404

    # 見つかったらリストを JSON で返す
    return jsonify(results)

# 気象警報・注意報API
@app.route('/api/weather_warnings')
def api_weather_warnings():
    """気象警報・注意報をJSON形式で返すAPI"""
    return jsonify(get_weather_warnings())

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
