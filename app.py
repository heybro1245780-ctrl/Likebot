from flask import Flask, request, jsonify, Response
import requests
import json
import os
from collections import OrderedDict

app = Flask(__name__)

# ── Auth & Limits ─────────────────────────────────────────────────────────────
VALID_API_KEYS = {"like"}

daily_limit = 300
used_count  = 0

VALID_REGIONS = {"IND", "BD"}

# ── Fallback Like API chain ───────────────────────────────────────────────────
# Tried in order; first success wins.  Add/remove/reorder as APIs come and go.
LIKE_APIS = [
    {
        "name": "mujahid",
        "url":  "https://like.mujahid.xyz/like",
        "params": lambda uid, region: {"uid": uid, "region": region, "key": "ABHINANDAN"},
        "method": "GET",
    },
    {
        "name": "likeservice1",
        "url":  "https://likeservice.vercel.app/like",
        "params": lambda uid, region: {"uid": uid, "region": region},
        "method": "GET",
    },
    {
        "name": "fflike_api",
        "url":  "https://fflike-api.vercel.app/like",
        "params": lambda uid, region: {"uid": uid, "region": region},
        "method": "GET",
    },
    {
        "name": "garenalike",
        "url":  "https://garena-like.vercel.app/api/like",
        "params": lambda uid, region: {"uid": uid, "region": region},
        "method": "GET",
    },
    {
        "name": "fflikeapi_xyz",
        "url":  "https://fflikeapi.xyz/like",
        "params": lambda uid, region: {"uid": uid, "region": region},
        "method": "GET",
    },
]



def _get(data: dict, *keys, default=0):
    for k in keys:
        if k in data:
            return data[k]
    return default


def normalize(data: dict, region: str, uid: str) -> dict:
    """Map any external API's response shape to our standard fields."""
    return {
        "LikesGivenByAPI":   _get(data, "LikesGivenByAPI",   "likes_given",  "liked",        default=0),
        "LikesafterCommand": _get(data, "LikesafterCommand",  "likes_after",  "after_likes",  default=0),
        "LikesbeforeCommand":_get(data, "LikesbeforeCommand", "likes_before", "before_likes", default=0),
        "PlayerNickname":    _get(data, "PlayerNickname",     "nickname",     "name",         default="?"),
        "Level":             _get(data, "Level",              "level",                        default="?"),
        "Region":            _get(data, "Region",             "region",                       default=region),
        "UID":               _get(data, "UID",                "uid",                          default=uid),
        "status":            _get(data, "status",                                             default=2),
    }


# ── Multi-API fallback like sender ────────────────────────────────────────────
def send_likes(uid: str, region: str) -> dict:
    """
    Try each API in LIKE_APIS in order.
    Returns a normalized dict on first success, or {"status": 0, "error": ...} if all fail.
    Tracks which APIs failed for the error message.
    """
    errors = []

    for api in LIKE_APIS:
        try:
            params = api["params"](uid, region)
            if api["method"] == "GET":
                resp = requests.get(api["url"], params=params, timeout=25)
            else:
                resp = requests.post(api["url"], json=params, timeout=25)

            # Skip clearly failed responses
            if resp.status_code in (403, 401, 404):
                errors.append(f"{api['name']}:HTTP{resp.status_code}")
                continue

            if resp.status_code != 200:
                errors.append(f"{api['name']}:HTTP{resp.status_code}")
                continue

            try:
                data = resp.json()
            except Exception:
                errors.append(f"{api['name']}:bad_json")
                continue

            # If the API itself returned an error status, keep trying
            if isinstance(data, dict) and data.get("status") == 0:
                errors.append(f"{api['name']}:status0")
                continue

            # Success — normalize and return
            result = normalize(data, region, uid)
            print(f"[LIKE] {api['name']} OK for uid={uid} region={region}", flush=True)
            return result

        except requests.exceptions.Timeout:
            errors.append(f"{api['name']}:timeout")
        except requests.exceptions.RequestException as e:
            errors.append(f"{api['name']}:{str(e)[:40]}")

    # All APIs failed
    return {"status": 0, "error": f"All like APIs failed: {', '.join(errors)}"}


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

    data = send_likes(uid_str, region)

    if data.get("status") == 0:
        return jsonify({"error": data.get("error", "All like APIs failed"), "status": 0}), 502

    status = data.get("status", 2)
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
