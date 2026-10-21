from flask import Flask, request, jsonify, Response
import requests
import json
from collections import OrderedDict

app = Flask(__name__)

# ── Auth & Limits ─────────────────────────────────────────────────────────────
VALID_API_KEYS = {"like"}

daily_limit = 300
used_count  = 0

# ── External Like API config ──────────────────────────────────────────────────
EXT_API_BASE = "https://like.mujahid.xyz/like"
EXT_API_KEY  = "ABHINANDAN"

VALID_REGIONS = {"IND", "BD"}


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

    # API key check
    api_key = request.args.get("key")
    if api_key not in VALID_API_KEYS:
        return Response(
            json.dumps({"error": "Invalid or missing API key", "status": 3},
                       separators=(',', ':')),
            status=401, mimetype='application/json'
        )

    uid    = request.args.get("uid", "").strip()
    region = request.args.get("region", "").upper().strip()

    if not uid or not uid.isdigit():
        return jsonify({"error": "UID must be a number"}), 400
    if region not in VALID_REGIONS:
        return jsonify({"error": f"Region '{region}' not supported. Use IND or BD."}), 400

    # ── Call external API ────────────────────────────────────────────────────
    try:
        resp = requests.get(
            EXT_API_BASE,
            params={"uid": uid, "region": region, "key": EXT_API_KEY},
            timeout=60
        )
    except requests.exceptions.Timeout:
        return jsonify({"error": "External API timed out", "status": 0}), 504
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"External API unreachable: {str(e)}", "status": 0}), 502

    if resp.status_code != 200:
        return jsonify({
            "error":  f"External API returned HTTP {resp.status_code}",
            "detail": resp.text[:300],
            "status": 0
        }), resp.status_code

    try:
        data = resp.json()
    except Exception:
        return jsonify({"error": "External API returned invalid JSON", "status": 0}), 502

    # ── Normalise field names (handle both snake_case and PascalCase APIs) ───
    def _get(*keys, default=0):
        for k in keys:
            if k in data:
                return data[k]
        return default

    likes_given  = _get("LikesGivenByAPI",    "likes_given",   default=0)
    after_likes  = _get("LikesafterCommand",   "likes_after",   default=0)
    before_likes = _get("LikesbeforeCommand",  "likes_before",  default=0)
    nickname     = _get("PlayerNickname",      "nickname",      default="?")
    level        = _get("Level",               "level",         default="?")
    p_region     = _get("Region",              "region",        default=region)
    p_uid        = _get("UID",                 "uid",           default=uid)
    status       = _get("status",                               default=2)

    # ── Quota tracking ───────────────────────────────────────────────────────
    if status == 1:
        used_count += 1
    remaining = max(daily_limit - used_count, 0)

    result = OrderedDict([
        ("LikesGivenByAPI",   likes_given),
        ("LikesafterCommand", after_likes),
        ("LikesbeforeCommand",before_likes),
        ("PlayerNickname",    nickname),
        ("Level",             level),
        ("Region",            p_region),
        ("UID",               p_uid),
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
    import os
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
