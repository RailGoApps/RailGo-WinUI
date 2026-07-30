# web_app.py - Flask backend for RailGPT Web
import json
import queue
import random
import threading
import time
import os
import re
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_file, stream_with_context
from app_runtime import APP_NAME, APP_VERSION, RELEASE_ICON_FILE, WINDOW_TITLE, resource_path, user_data_path
from app_settings import AppSettingsError, DEFAULT_PROVIDER_ID, get_app_settings
from tools.rail.station_dict import station_dict as _station_dict
from tools.rail.rail_store import railstore

app = Flask(
    __name__,
    template_folder=resource_path("templates"),
    static_folder=resource_path("static"),
    static_url_path="/static",
)

# ============================================================
# Global state (single-user local app, mirrors Qt architecture)
# ============================================================
_backend = None
_store = None
_llm = None
_session = None         # Current SessionMemory
_sse_queue = None       # Active SSE queue (one at a time)
_busy = False           # Whether agent is currently running
_settings = get_app_settings()


def init_app(backend, store, llm):
    """Called from main.py to wire up backend dependencies."""
    global _backend, _store, _llm, _session
    _backend = backend
    _store = store
    _llm = llm

    from memory.session import SessionMemory
    _session = SessionMemory()

    # Register persistent listeners (once, at startup)
    _backend.thinking_engine.set_listener(_on_thinking_event)
    _backend.psw.add_listener(_on_psw_event)


def _on_thinking_event(event):
    global _sse_queue
    q = _sse_queue
    if q and isinstance(event, dict) and event.get("type") == "thinking_token":
        try:
            q.put_nowait({"type": "thinking", "text": event["text"]})
        except Exception:
            pass


def _on_psw_event(event):
    global _sse_queue
    q = _sse_queue
    if q:
        state = event.get("state", "")
        if hasattr(state, "value"):
            state = state.value
        line = (
            f"[{event.get('timestamp', '')}]"
            f" [round {event.get('round', '')}]"
            f" [{state}]"
            f" {event.get('detail', '')}"
        )
        try:
            q.put_nowait({"type": "psw", "text": line})
        except Exception:
            pass


# ============================================================
# Static page
# ============================================================

@app.route("/")
def index():
    embedded = request.args.get("embedded", "0").lower() in {"1", "true", "yes"}
    return render_template(
        "index.html",
        app_name=APP_NAME,
        app_version=APP_VERSION,
        window_title=WINDOW_TITLE,
        favicon_url="/favicon.ico",
        embedded=embedded,
    )


@app.route("/favicon.ico")
def favicon():
    with open(resource_path(RELEASE_ICON_FILE), "rb") as fh:
        return Response(fh.read(), mimetype="image/x-icon")


_ASSET_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


@app.route("/api/assets/coach/<content_hash>")
def coach_asset(content_hash):
    content_hash = str(content_hash or "").lower()
    if not _ASSET_HASH_RE.fullmatch(content_hash):
        return jsonify({"error": "invalid asset id"}), 400
    record = railstore.get_coach_media_by_hash(content_hash)
    if not record:
        return jsonify({"error": "asset not found"}), 404
    path = Path(str(record.get("local_path") or "")).resolve()
    root = Path(user_data_path("media", "coach")).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return jsonify({"error": "asset path rejected"}), 403
    if not path.is_file():
        return jsonify({"error": "asset file missing"}), 404
    return send_file(path, mimetype=str(record.get("mime_type") or "application/octet-stream"), max_age=31536000)


@app.route("/api/assets/routes/<asset_id>")
def route_asset(asset_id):
    asset_id = str(asset_id or "").lower()
    if not _ASSET_HASH_RE.fullmatch(asset_id):
        return jsonify({"error": "invalid asset id"}), 400
    record = railstore.get_route_asset(asset_id)
    if not record:
        return jsonify({"error": "asset not found"}), 404
    return jsonify(
        {
            "asset_id": asset_id,
            "geojson": record.get("geojson_json") or {},
            "summary": record.get("summary_json") or {},
            "coordinate_metadata": record.get("raw_metadata_json") or {},
            "fallback_svg": record.get("fallback_svg") or "",
            "attribution": "Route data © RailGo; map © OpenStreetMap contributors",
        }
    )


# ============================================================
# Suggestions
# ============================================================

_SUGGESTION_POOL = [
    {"label": "北京→上海 明天高铁",     "text": "北京到上海明天有哪些高铁？"},
    {"label": "G1次列车停靠站",          "text": "G1次列车经过哪些站？"},
    {"label": "广州南→深圳北 最快车次",  "text": "广州南到深圳北最快的车次是什么？"},
    {"label": "武汉→成都 中转方案",      "text": "从武汉到成都如何中转？"},
    {"label": "动车与高铁的区别",        "text": "D字头和G字头列车有什么区别？"},
    {"label": "上海→杭州 城际列车",      "text": "上海到杭州有哪些城际列车？"},
    {"label": "成都→重庆 最快车次",      "text": "成都到重庆最快的高铁是哪趟？"},
    {"label": "西安→北京 高铁时长",      "text": "西安到北京高铁需要多长时间？"},
    {"label": "郑州→武汉 车次查询",      "text": "郑州到武汉有哪些高铁车次？"},
    {"label": "复兴号与和谐号区别",      "text": "复兴号和和谐号列车有什么区别？"},
    {"label": "上海虹桥→南京 余票",      "text": "上海虹桥到南京今天还有哪些车有票？"},
    {"label": "北京南→天津 通勤车次",    "text": "北京南到天津有哪些班次？"},
    {"label": "深圳北→长沙南 高铁",      "text": "深圳北到长沙南今天有哪些高铁？"},
    {"label": "杭州→福州 经停站查询",    "text": "杭州到福州的高铁经过哪些站？"},
    {"label": "G2次列车动车组",          "text": "G2次列车用的是哪款动车组？"},
    {"label": "南京→合肥 明日余票",      "text": "南京到合肥明天高铁还有余票吗？"},
    {"label": "武汉→广州 最早班次",      "text": "武汉到广州最早的高铁是几点发车？"},
    {"label": "哈尔滨→沈阳 中转方案",   "text": "哈尔滨到沈阳高铁如何中转？"},
]

_SUGGESTIONS_COUNT = 6


@app.route("/api/suggestions", methods=["GET"])
def get_suggestions():
    selected = random.sample(_SUGGESTION_POOL, _SUGGESTIONS_COUNT)
    return jsonify(selected)


# ============================================================
# City picker API
# ============================================================

_REGION_PROVINCES = [
    {"name": "华北", "provinces": ["北京", "天津", "河北", "山西", "内蒙古"]},
    {"name": "东北", "provinces": ["辽宁", "吉林", "黑龙江"]},
    {"name": "华东", "provinces": ["上海", "江苏", "浙江", "安徽", "福建", "江西", "山东"]},
    {"name": "中南", "provinces": ["河南", "湖北", "湖南", "广东", "广西", "海南"]},
    {"name": "西南", "provinces": ["重庆", "四川", "贵州", "云南", "西藏"]},
    {"name": "西北", "provinces": ["陕西", "甘肃", "青海", "宁夏", "新疆"]},
    {"name": "港澳及境外", "provinces": ["香港", "境外"]},
]

_PROVINCE_CITIES = {
    "北京":   ["北京"],
    "天津":   ["天津"],
    "上海":   ["上海"],
    "重庆":   ["重庆","万州","合川","涪陵","梁平","长寿","永川","綦江","璧山",
                "荣昌","南川","垫江","丰都","云阳","奉节","巫山","黔江","秀山",
                "酉阳","彭水","石柱","石柱县","江津","大足","潼南"],
    "河北":   ["石家庄","保定","唐山","沧州","廊坊","承德","张家口","秦皇岛",
                "邢台","邯郸","衡水"],
    "山西":   ["太原","大同","长治","晋城","朔州","晋中","运城","忻州",
                "临汾","吕梁","阳泉"],
    "内蒙古": ["呼和浩特","包头","乌海","赤峰","通辽","呼伦贝尔","乌兰察布",
                "鄂尔多斯","巴彦淖尔","二连浩特","锡林郭勒","阿拉善"],
    "辽宁":   ["沈阳","大连","鞍山","抚顺","本溪","丹东","锦州","营口",
                "阜新","辽阳","盘锦","铁岭","朝阳","葫芦岛"],
    "吉林":   ["长春","吉林","四平","辽源","通化","白山","松原","白城","延边"],
    "黑龙江": ["哈尔滨","齐齐哈尔","鸡西","鹤岗","双鸭山","大庆","伊春",
                "佳木斯","七台河","牡丹江","黑河","绥化","加格达奇","林口","桦南"],
    "江苏":   ["南京","无锡","徐州","常州","苏州","南通","连云港","淮安",
                "盐城","扬州","镇江","泰州","宿迁","仪征"],
    "浙江":   ["杭州","宁波","温州","嘉兴","湖州","绍兴","金华","衢州",
                "台州","丽水","建德"],
    "安徽":   ["合肥","芜湖","蚌埠","淮南","马鞍山","淮北","铜陵","安庆",
                "黄山","滁州","阜阳","宿州","六安","宣城","池州","亳州","凤阳"],
    "福建":   ["福州","厦门","莆田","三明","泉州","漳州","南平","龙岩",
                "宁德","沙县","上杭","建宁","长汀","安溪","来舟"],
    "江西":   ["南昌","景德镇","萍乡","九江","新余","鹰潭","赣州","吉安",
                "宜春","抚州","上饶","临川","崇仁","于都","资溪","芦溪"],
    "山东":   ["济南","青岛","淄博","枣庄","东营","烟台","潍坊","济宁",
                "泰安","威海","日照","滨州","德州","聊城","临沂","菏泽","莱芜"],
    "河南":   ["郑州","开封","洛阳","平顶山","安阳","鹤壁","新乡","焦作",
                "濮阳","许昌","漯河","三门峡","南阳","商丘","信阳","周口","驻马店","济源"],
    "湖北":   ["武汉","黄石","十堰","宜昌","襄阳","鄂州","荆门","孝感",
                "荆州","黄冈","咸宁","随州","恩施","仙桃","潜江","天门",
                "神农架","汉川","武穴","蕲春","浠水","宜城","当阳","钟祥","京山"],
    "湖南":   ["长沙","株洲","湘潭","衡阳","邵阳","岳阳","常德","张家界",
                "益阳","郴州","永州","怀化","娄底","吉首","邵东"],
    "广东":   ["广州","深圳","珠海","汕头","佛山","韶关","湛江","茂名",
                "肇庆","惠州","梅州","汕尾","河源","阳江","清远","东莞","中山",
                "潮州","揭阳","云浮","江门"],
    "广西":   ["南宁","柳州","桂林","梧州","北海","防城港","钦州","贵港",
                "玉林","百色","贺州","河池","来宾","崇左","兴安"],
    "海南":   ["海口","三亚","儋州","文昌","琼海","万宁","东方","陵水",
                "乐东","澄迈","昌江","临高"],
    "四川":   ["成都","自贡","攀枝花","泸州","德阳","绵阳","广元","遂宁",
                "内江","乐山","南充","眉山","宜宾","广安","达州","雅安","巴中",
                "资阳","西昌","阿坝藏族羌族自治州"],
    "贵州":   ["贵阳","六盘水","遵义","安顺","毕节","铜仁","凯里","都匀","兴义"],
    "云南":   ["昆明","大理","曲靖","楚雄","文山","普洱","景洪","丽江",
                "临沧","保山","玉溪","昭通","蒙自","个旧","南涧","宁洱","墨江",
                "漾濞","永平","元江","香格里拉","峨山","磨丁","江边村","马桥河"],
    "西藏":   ["拉萨","日喀则","山南","林芝","那曲","扎囊","桑日","朗县",
                "贡嘎","加查","纳堆","岗嘎","米林"],
    "陕西":   ["西安","铜川","宝鸡","咸阳","渭南","延安","汉中","安康",
                "商洛","华阴","富平","蒲城"],
    "甘肃":   ["兰州","嘉峪关","金昌","白银","天水","武威","张掖","平凉",
                "酒泉","庆阳","定西","陇南","玉门"],
    "青海":   ["西宁","海东","海北州","海西州","格尔木","德令哈","茫崖"],
    "宁夏":   ["银川","石嘴山","吴忠","固原","中卫","灵武","青铜峡"],
    "新疆":   ["乌鲁木齐","克拉玛依","吐鲁番","哈密","昌吉","阿克苏","喀什",
                "和田","伊宁","博乐","库尔勒","塔城","阿勒泰","石河子","阿图什",
                "铁门关","巴音郭楞蒙古自治州"],
    "香港":   ["香港"],
    "境外":   ["万象","孟赛","琅勃拉邦","老挝万荣"],
}

_city_picker_cache = None


def _build_city_picker():
    global _city_picker_cache
    if _city_picker_cache:
        return _city_picker_cache

    # city -> [station_name, ...], sorted by 12306 idx
    idx_map = {}
    city_stations = {}
    for info in _station_dict.data.values():
        city = info.get("city", "")
        name = info.get("name", "")
        if city and name:
            city_stations.setdefault(city, [])
            if name not in city_stations[city]:
                city_stations[city].append(name)
        try:
            idx_map[name] = int(info.get("idx", 9999))
        except (ValueError, TypeError):
            idx_map[name] = 9999

    for city, names in city_stations.items():
        names.sort(key=lambda n: (0 if n == city else 1, idx_map.get(n, 9999)))

    cities_by_province = {
        prov: [c for c in cities if c in city_stations]
        for prov, cities in _PROVINCE_CITIES.items()
    }
    cities_by_province = {p: v for p, v in cities_by_province.items() if v}

    regions = [
        {"name": r["name"],
         "provinces": [p for p in r["provinces"] if p in cities_by_province]}
        for r in _REGION_PROVINCES
    ]
    regions = [r for r in regions if r["provinces"]]

    _city_picker_cache = {
        "regions": regions,
        "cities": cities_by_province,
        "stations": city_stations,
    }
    return _city_picker_cache


@app.route("/api/city_picker")
def city_picker_api():
    return jsonify(_build_city_picker())


# ============================================================
# Conversation management API
# ============================================================

@app.route("/api/conversations", methods=["GET"])
def list_conversations():
    items = []
    for item in _store.index:
        items.append({
            "id": item["id"],
            "title": item["title"],
            "updated": item.get("updated", ""),
        })
    return jsonify(items)


@app.route("/api/conversations", methods=["POST"])
def new_conversation():
    global _session
    from memory.session import SessionMemory
    cid = _store.new_conversation("")
    _session = SessionMemory()
    if hasattr(_session, "set_session_id"):
        _session.set_session_id(cid)
    return jsonify({"id": cid, "title": _store.title})


@app.route("/api/conversations/<int:cid>", methods=["GET"])
def get_conversation(cid):
    if not _store.load_conversation(cid):
        return jsonify({"error": "Not found"}), 404
    return jsonify({
        "id": cid,
        "title": _store.title,
        "messages": _store.messages,
    })


@app.route("/api/conversations/<int:cid>/load", methods=["POST"])
def load_conversation(cid):
    global _session
    if not _store.load_conversation(cid):
        return jsonify({"error": "Not found"}), 404
    from memory.session import SessionMemory
    _session = SessionMemory()
    _store.build_session_memory(_session)
    return jsonify({
        "id": cid,
        "title": _store.title,
        "messages": _store.messages,
    })


@app.route("/api/conversations/<int:cid>", methods=["DELETE"])
def delete_conversation(cid):
    ok = _store.delete_conversation(cid)
    return jsonify({"ok": ok})


@app.route("/api/conversations/<int:cid>/rename", methods=["PUT"])
def rename_conversation(cid):
    data = request.get_json(force=True) or {}
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error": "Title required"}), 400
    ok = _store.rename_conversation(cid, title)
    return jsonify({"ok": ok})


@app.route("/api/conversations/<int:cid>/export", methods=["GET"])
def export_conversation(cid):
    path = _store.export_markdown(cid)
    if path is None:
        return jsonify({"error": "Not found"}), 404
    return send_file(path, as_attachment=True)


def suggest_export_filename(cid: int) -> str:
    if not _store:
        return f"conversation_{cid:03d}.md"
    return _store.suggest_export_filename(cid)


def export_conversation_markdown(cid: int, save_path: str | None = None):
    if not _store:
        return None
    return _store.export_markdown(cid, save_path=save_path)


# ============================================================
# Mode switching
# ============================================================

@app.route("/api/mode", methods=["POST"])
def set_mode():
    data = request.get_json(force=True) or {}
    mode = str(data.get("mode", "fast-go")).strip().lower()
    if mode == "fast":
        mode = "fast-go"
    if mode not in {"fast-go", "fast-plus", "deep"}:
        mode = "fast-go"
    _llm.set_mode(mode)
    if hasattr(_backend, "set_mode"):
        _backend.set_mode(mode)
    return jsonify({"ok": True, "mode": mode})


# ============================================================
# Settings
# ============================================================

@app.route("/api/settings", methods=["GET"])
def get_settings():
    payload = _settings.get_frontend_payload()
    payload["busy"] = _busy
    return jsonify(payload)


@app.route("/api/settings/api", methods=["PUT"])
def update_api_settings():
    if _busy:
        return jsonify({"error": "当前正在生成回复，请稍后再修改 API 设置。"}), 409

    data = request.get_json(force=True) or {}
    provider = str(data.get("provider") or DEFAULT_PROVIDER_ID).strip().lower()
    primary_api_key = data.get("primary_api_key") if "primary_api_key" in data else None
    thinking_api_key = data.get("thinking_api_key") if "thinking_api_key" in data else None

    try:
        payload = _settings.save_api_settings(
            provider,
            primary_api_key=primary_api_key,
            thinking_api_key=thinking_api_key,
        )
    except AppSettingsError as exc:
        return jsonify({"error": str(exc)}), 400

    payload["ok"] = True
    payload["busy"] = _busy
    return jsonify(payload)


@app.route("/api/settings/api", methods=["DELETE"])
def delete_api_settings():
    if _busy:
        return jsonify({"error": "当前正在生成回复，请稍后再修改 API 设置。"}), 409

    slot = request.args.get("slot", "primary")

    try:
        payload = _settings.delete_api_key(slot=slot)
    except AppSettingsError as exc:
        return jsonify({"error": str(exc)}), 400

    payload["ok"] = True
    payload["busy"] = _busy
    return jsonify(payload)


# ============================================================
# Chat: SSE streaming endpoint
# ============================================================

@app.route("/api/chat", methods=["POST"])
def chat():
    global _sse_queue, _busy, _session

    if not _settings.has_api_key():
        return jsonify({
            "error": "请先在设置中配置主对话 API Key。",
            "code": "api_not_configured",
        }), 400

    if _busy:
        return jsonify({"error": "Agent is busy"}), 429

    data = request.get_json(force=True) or {}
    user_text = str(data.get("text", "")).strip()
    if not user_text:
        return jsonify({"error": "Empty message"}), 400

    ev_queue = queue.Queue()
    _sse_queue = ev_queue
    _busy = True

    def run_agent():
        global _busy, _sse_queue, _session
        try:
            # Create conversation if needed
            if _store.current_id is None:
                cid = _store.new_conversation("")
                if hasattr(_session, "set_session_id"):
                    _session.set_session_id(cid)

            _store.append_user(user_text)

            full_answer = ""
            attachments = []
            thinking_buffer = ""
            last_emit_time = time.time()

            for ev in _backend.stream_events(user_text, _session):
                etype = ev.get("type")
                text = ev.get("text", "")

                if etype == "token":
                    full_answer += text
                    ev_queue.put({"type": "token", "text": text})

                elif etype == "thinking_token":
                    thinking_buffer += text
                    now = time.time()
                    if now - last_emit_time > 0.08:
                        ev_queue.put({"type": "thinking", "text": thinking_buffer})
                        thinking_buffer = ""
                        last_emit_time = now

                elif etype == "pending":
                    ev_queue.put({"type": "pending", "text": text})

                elif etype == "attachment":
                    attachment = ev.get("attachment")
                    if isinstance(attachment, dict):
                        attachments.append(attachment)
                        ev_queue.put({"type": "attachment", "attachment": attachment})

                elif etype == "final":
                    full_answer = text
                    break

            # Flush remaining thinking buffer
            if thinking_buffer:
                ev_queue.put({"type": "thinking", "text": thinking_buffer})

            _store.append_ai(full_answer, attachments=attachments)
            _store.save_session_memory(_session)

            ev_queue.put({"type": "done", "cid": _store.current_id, "title": _store.title or ""})

        except Exception as e:
            ev_queue.put({"type": "error", "text": str(e)})
        finally:
            _busy = False
            _sse_queue = None

    threading.Thread(target=run_agent, daemon=True).start()

    def generate():
        while True:
            try:
                event = ev_queue.get(timeout=90)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event["type"] in ("done", "error"):
                    break
            except queue.Empty:
                # Heartbeat to keep connection alive
                yield "data: {\"type\":\"heartbeat\"}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ============================================================
# Status
# ============================================================

@app.route("/api/status", methods=["GET"])
def status():
    return jsonify({
        "service": APP_NAME,
        "version": APP_VERSION,
        "embedded_supported": True,
        "busy": _busy,
        "current_id": _store.current_id if _store else None,
        "has_api_key": _settings.has_api_key(),
        "has_thinking_api_key": _settings.get_frontend_payload().get("has_thinking_api_key", False),
    })


@app.route("/api/search", methods=["GET"])
def search_conversations():
    q = request.args.get("q", "").strip()
    scope = request.args.get("scope", "title")
    if not q or not _store:
        return jsonify([])

    q_lower = q.lower()
    results = []

    for item in _store.index:
        cid = item["id"]
        title = item.get("title", "")
        title_match = q_lower in title.lower()

        if scope == "title":
            if title_match:
                results.append({"cid": cid, "title": title, "matches": []})
        else:
            # Load conversation file and search message content
            path = _store._id_to_path(cid)
            content_matches = []
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    for idx, msg in enumerate(data.get("messages", [])):
                        text = msg.get("content", "")
                        if q_lower in text.lower():
                            pos = text.lower().find(q_lower)
                            start = max(0, pos - 40)
                            end = min(len(text), pos + len(q) + 40)
                            snippet = (
                                ("…" if start > 0 else "") +
                                text[start:end] +
                                ("…" if end < len(text) else "")
                            )
                            content_matches.append({
                                "msg_index": idx,
                                "role": msg.get("role", ""),
                                "snippet": snippet,
                            })
                except Exception:
                    pass
            if title_match or content_matches:
                results.append({"cid": cid, "title": title, "matches": content_matches})

    return jsonify(results)


@app.route("/api/readme", methods=["GET"])
def readme():
    readme_path = resource_path("README.md")
    if not os.path.isfile(readme_path):
        return jsonify({"error": "README.md not found"}), 404
    with open(readme_path, "r", encoding="utf-8") as f:
        content = f.read()
    return jsonify({"content": content})


# ============================================================
# Shutdown (for graceful exit from C# host)
# ============================================================

@app.route("/api/shutdown", methods=["GET", "POST"])
def shutdown():
    """Gracefully shut down the Flask server (called by C# host)."""
    import os
    import signal

    def _shutdown():
        os.kill(os.getpid(), signal.SIGTERM)

    import threading
    threading.Thread(target=_shutdown, daemon=True).start()
    return jsonify({"status": "shutting_down"})
