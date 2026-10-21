from flask import Flask, request, jsonify, Response
import requests
import json
import random
import os
from collections import OrderedDict

# ── Protobuf imports ──────────────────────────────────────────────────────────
from like_pb2 import like as LikeMessage
from uid_generator_pb2 import uid_generator as UIDGen
from visit_count_pb2 import Info as VisitInfo

app = Flask(__name__)

# ── Auth & Limits ─────────────────────────────────────────────────────────────
VALID_API_KEYS = {"like"}

daily_limit = 300
used_count  = 0

VALID_REGIONS = {"IND", "BD"}

# ── Region → Garena like endpoint ─────────────────────────────────────────────
REGION_LIKE_URL = {
    "IND": "https://client.ind.freefiremobile.com/GetPlayerPersonalShow",
    "BD":  "https://clientbd.freefiremobile.com/GetPlayerPersonalShow",
}

# Token files per region
TOKEN_FILES = {
    "IND": "token_ind.json",
    "BD":  "token_bd.json",
}


# ── Token loader ──────────────────────────────────────────────────────────────
def load_tokens(region: str) -> list:
    path = TOKEN_FILES.get(region, "")
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        tokens = []
        for entry in data:
            if isinstance(entry, dict):
                t = entry.get("token") or entry.get("jwt_token") or entry.get("access_token")
            elif isinstance(entry, str):
                t = entry
            else:
                continue
            if t:
                tokens.append(t)
        return tokens
    except Exception:
        return []


def pick_token(region: str):
    tokens = load_tokens(region)
    return random.choice(tokens) if tokens else None


# ── Build protobuf bodies ─────────────────────────────────────────────────────
def build_like_body(uid: int, region: str) -> bytes:
    msg = LikeMessage()
    msg.uid    = uid
    msg.region = region
    return msg.SerializeToString()


def build_uid_body(uid: int) -> bytes:
    msg = UIDGen()
    msg.saturn_ = uid
    msg.garena  = 0
    return msg.SerializeToString()


# ── Common Garena headers ─────────────────────────────────────────────────────
def garena_headers(token: str) -> dict:
    return {
        "Authorization":   f"Bearer {token}",
        "Content-Type":    "application/x-www-form-urlencoded",
        "User-Agent":      "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
        "Connection":      "Keep-Alive",
        "Accept-Encoding": "gzip",
        "X-Unity-Version": "2018.4.11f1",
        "ReleaseVersion":  "OB55",
    }


# ── Fetch current likes for a player ─────────────────────────────────────────
def get_player_info(uid: int, region: str, token: str = None):
    """Returns (nickname, level, p_region, likes) or raises on failure."""
    if token is None:
        token = pick_token(region)
    if not token:
        raise RuntimeError(f"No tokens available for region {region}")
    url  = REGION_LIKE_URL.get(region)
    body = build_uid_body(uid)
    resp = requests.post(url, data=body, headers=garena_headers(token), timeout=20)
    resp.raise_for_status()
    info = VisitInfo()
    info.ParseFromString(resp.content)
    acc = info.AccountInfo
    return acc.PlayerNickname or str(uid), acc.Levels, acc.PlayerRegion or region, acc.Likes


# ── Send likes to a UID ───────────────────────────────────────────────────────
def send_likes(uid: int, region: str) -> dict:
    token = pick_token(region)
    if not token:
        return {"error": f"No tokens available for region {region}", "status": 0}

    url  = REGION_LIKE_URL.get(region)

    # Fetch info BEFORE sending likes
    try:
        nickname, level, p_region, before_likes = get_player_info(uid, region, token)
    except Exception as e:
        return {"error": f"Player info fetch failed: {str(e)}", "status": 0}

    # Send like
    like_body = build_like_body(uid, region)
    try:
        resp = requests.post(url, data=like_body, headers=garena_headers(token), timeout=30)
    except requests.exceptions.Timeout:
        return {"error": "Garena API timed out", "status": 0}
    except requests.exceptions.RequestException as e:
        return {"error": f"Network error: {str(e)}", "status": 0}

    if resp.status_code not in (200, 400):
        return {"error": f"Garena returned HTTP {resp.status_code}", "status": 0}

    # Fetch info AFTER to calculate delta
    try:
        _, _, _, after_likes = get_player_info(uid, region, token)
    except Exception:
        after_likes = before_likes   # fallback — assume maxed

    given  = max(after_likes - before_likes, 0)
    status = 1 if given > 0 else 2   # 1=sent, 2=maxed/already full

    return {
        "LikesGivenByAPI":   given,
        "LikesafterCommand": after_likes,
        "LikesbeforeCommand":before_likes,
        "PlayerNickname":    nickname,
        "Level":             level,
        "Region":            p_region,
        "UID":               uid,
        "status":            status,
    }


# ── Home ──────────────────────────────────────────────────────────────────────
@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "status":    "online",
        "service":   "Free Fire Like API",
        "endpoints": {
            "like":   "/like?uid=UID&region=REGION&key=API_KEY",
            "remain": "/remain"
        },
        "regions": list(VALID_REGIONS)
    })


# ── /like ─────────────────────────────────────────────────────────────────────
@app.route('/like', methods=['GET'])
def handle_requests():
    global used_count

    api_key = request.args.get("key")
    if api_key not in VALID_API_KEYS:
        return Response(
            json.dumps({"error": "Invalid or missing API key", "status": 3},
                       separators=(',', ':')),
            status=401, mimetype='application/json'
        )

    uid_str = request.args.get("uid", "").strip()
    region  = request.args.get("region", "").upper().strip()

    if not uid_str or not uid_str.isdigit():
        return jsonify({"error": "UID must be a number"}), 400
    if region not in VALID_REGIONS:
        return jsonify({"error": f"Region '{region}' not supported. Use IND or BD."}), 400

    data = send_likes(int(uid_str), region)

    if data.get("status") == 0:
        return jsonify({"error": data.get("error", "Like failed"), "status": 0}), 502

    status    = data.get("status", 2)
    if status == 1:
        used_count += 1
    remaining = max(daily_limit - used_count, 0)

    result = OrderedDict([
        ("LikesGivenByAPI",   data.get("LikesGivenByAPI", 0)),
        ("LikesafterCommand", data.get("LikesafterCommand", 0)),
        ("LikesbeforeCommand",data.get("LikesbeforeCommand", 0)),
        ("PlayerNickname",    data.get("PlayerNickname", "?")),
        ("Level",             data.get("Level", "?")),
        ("Region",            data.get("Region", region)),
        ("UID",               data.get("UID", uid_str)),
        ("status",            status),
        ("daily_limit",       daily_limit),
        ("used",              used_count),
        ("remaining",         remaining),
    ])

    return Response(
        json.dumps(result, separators=(',', ':')),
        status=200, mimetype='application/json'
    )


# ── /remain ───────────────────────────────────────────────────────────────────
@app.route('/remain', methods=['GET'])
def remain_info():
    remaining = max(daily_limit - used_count, 0)
    return jsonify({
        "daily_limit": daily_limit,
        "remaining":   remaining,
        "used":        used_count,
        "reset_info":  "4:00 AM IST"
    })


# ── /setlimit ─────────────────────────────────────────────────────────────────
@app.route('/setlimit', methods=['POST'])
def set_limit():
    global daily_limit
    body    = request.json or {}
    api_key = body.get('key')
    if api_key not in VALID_API_KEYS:
        return jsonify({"error": "Unauthorized"}), 401
    new_limit = body.get('limit')
    if not isinstance(new_limit, int) or new_limit < 0:
        return jsonify({"error": "Invalid limit value"}), 400
    daily_limit = new_limit
    return jsonify({"success": True, "daily_limit": daily_limit})


# ── /reset ────────────────────────────────────────────────────────────────────
@app.route('/reset', methods=['POST'])
def reset_count():
    global used_count
    body    = request.json or {}
    api_key = body.get('key')
    if api_key not in VALID_API_KEYS:
        return jsonify({"error": "Unauthorized"}), 401
    used_count = 0
    return jsonify({"success": True, "message": "Daily count reset"})


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
