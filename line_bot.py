import time
from datetime import datetime, timedelta
import requests
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FlexSendMessage

# ----------------- 設定區 -----------------
TDX_CLIENT_ID = "collinwei-efd04955-cfef-447f"
TDX_CLIENT_SECRET = "527f8c8a-65aa-4c93-9b88-717420f1b3d7"

LINE_CHANNEL_ACCESS_TOKEN = "qfQcKyefdbXmuzGOc0J872lOFCrnc/49YiqcOBCtqVftoQjsAmavJUe8j4Su6SvwlLIAkfFA3Pqz4eZNgSqbg6Ceyhfxulyufa18zt3Dyn3wSPvANQlzRTCkHCac2Mk77tb+U7VNvii97O9y2cLPVwdB04t89/1O/w1cDnyilFU="
LINE_CHANNEL_SECRET = "AIzaSyA98o8nEjdfhpxhciAByuvmZVA5lGv6G0Q"
# ------------------------------------------

app = Flask(__name__)
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 淡海輕軌 綠山線 發車基準 (06:00 - 24:00)
LRT_TIMETABLE_SOUTH = [
    f"{h:02d}:{m:02d}"
    for h in range(6, 24)
    for m in ([3, 11, 18, 26, 33, 41, 48, 56] if (7 <= h < 9 or 17 <= h < 20) else [5, 17, 29, 41, 53])
] + ["24:00"]

LRT_TIMETABLE_NORTH = [
    f"{h:02d}:{m:02d}"
    for h in range(6, 24)
    for m in ([2, 10, 17, 25, 32, 40, 47, 55] if (7 <= h < 9 or 17 <= h < 20) else [4, 16, 28, 40, 52])
] + ["24:00"]

class TDXAuth:
    def __init__(self):
        self.access_token = None
        self.token_expiry = 0

    def get_token(self):
        now = time.time()
        if self.access_token and now < self.token_expiry:
            return self.access_token
        auth_url = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
        res = requests.post(auth_url, data={
            "grant_type": "client_credentials",
            "client_id": TDX_CLIENT_ID,
            "client_secret": TDX_CLIENT_SECRET
        }, timeout=10)
        res.raise_for_status()
        info = res.json()
        self.access_token = info["access_token"]
        self.token_expiry = now + info.get("expires_in", 86400) - 60
        return self.access_token

tdx_auth = TDXAuth()

def fetch_metro_board(station_id):
    try:
        token = tdx_auth.get_token()
        url = f"https://tdx.transportdata.tw/api/basic/v2/Rail/Metro/LiveBoard/TRTC?$filter=StationID eq '{station_id}'&$format=JSON"
        res = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=8)
        return res.json() if res.status_code == 200 else []
    except Exception:
        return []

def get_southbound_reply():
    """去程：淡水行政中心 -> 板橋車站"""
    now = datetime.now()
    now_hm = now.strftime("%H:%M")

    # 1. 輕軌推薦班次
    lrt_trains = []
    for t in LRT_TIMETABLE_SOUTH:
        if t >= now_hm:
            th, tm = map(int, t.split(":"))
            dept_dt = now.replace(hour=th % 24, minute=tm, second=0)
            wait_m = max(0, int((dept_dt - now).total_seconds() // 60))
            hongshulin_arr = dept_dt + timedelta(minutes=12)
            hongshulin_ready = hongshulin_arr + timedelta(minutes=3)
            lrt_trains.append({
                "dept": t,
                "wait_m": wait_m,
                "arr_hongshulin": hongshulin_arr.strftime("%H:%M"),
                "ready_time": hongshulin_ready
            })
            if len(lrt_trains) == 2:
                break

    if not lrt_trains:
        return TextSendMessage(text="目前非輕軌營運時段（營運時間約 06:00 - 24:00）。")

    first_lrt = lrt_trains[0]

    # 2. 北捷紅樹林站即時動態 (R27)
    raw_metro = fetch_metro_board("R27")
    metro_text = ""
    for m in raw_metro:
        dest = m.get("DestinationStationName", {}).get("Zh_tw", "")
        if dest == "淡水":
            continue
        sec = m.get("EstimateTime", 0)
        arr_dt = now + timedelta(seconds=sec)
        can_catch = arr_dt >= first_lrt["ready_time"]
        status_tag = "可順利銜接" if can_catch else "輕軌未到"
        metro_text += f"\n• 往 {dest}：{arr_dt.strftime('%H:%M')} 到站（{sec//60}分）[{status_tag}]"

    if not metro_text:
        metro_text = "\n• 紅線尖峰 3-4 分、離峰 6-8 分一班，到月台即可接續上車。"

    alt_lrt_info = f"備選次班：{lrt_trains[1]['dept']}（{lrt_trains[1]['wait_m']} 分鐘後）" if len(lrt_trains) > 1 else ""

    msg = (
        f"【去程通勤動態】淡水行政中心 ➔ 板橋\n"
        f"查詢時間：{now.strftime('%H:%M:%S')}\n\n"
        f"🟢 淡海輕軌（淡水行政中心）\n"
        f"• 推薦班次：{first_lrt['dept']} 發車（約 {first_lrt['wait_m']} 分鐘後）\n"
        f"• 抵達紅樹林：約 {first_lrt['arr_hongshulin']}\n"
        f"• 轉乘至北捷月台：約 {first_lrt['ready_time'].strftime('%H:%M')}\n"
        f"{alt_lrt_info}\n\n"
        f"🔴 北捷紅樹林站銜接預估：{metro_text}\n\n"
        f"ℹ️ 後續乘車：\n"
        f"搭紅線至「台北車站」約 35 分，轉板南線至「板橋站」約 13 分。"
    )
    return TextSendMessage(text=msg.strip())

def get_northbound_reply():
    """回程：板橋車站 -> 淡水行政中心"""
    now = datetime.now()

    # 1. 板橋站板南線即時動態 (BL07)
    raw_metro_bl = fetch_metro_board("BL07")
    bl_text = ""
    for m in raw_metro_bl:
        dest = m.get("DestinationStationName", {}).get("Zh_tw", "")
        if "頂埔" in dest or "亞東" in dest:
            continue
        sec = m.get("EstimateTime", 0)
        arr_dt = now + timedelta(seconds=sec)
        bl_text += f"\n• 往 {dest}：{arr_dt.strftime('%H:%M')} 進站（約 {sec//60} 分鐘後）"

    if not bl_text:
        bl_text = "\n• 板南線班距約 2-4 分鐘，進站即可直接上車。"

    # 2. 估算 55 分鐘後抵達紅樹林輕軌月台
    est_arrive_hongshulin = now + timedelta(minutes=55)
    arr_hm = est_arrive_hongshulin.strftime("%H:%M")

    # 3. 銜接之淡海輕軌班次
    lrt_connect = []
    for t in LRT_TIMETABLE_NORTH:
        if t >= arr_hm:
            th, tm = map(int, t.split(":"))
            dept_dt = est_arrive_hongshulin.replace(hour=th % 24, minute=tm, second=0)
            wait_m = max(0, int((dept_dt - est_arrive_hongshulin).total_seconds() // 60))
            arr_center = dept_dt + timedelta(minutes=12)
            lrt_connect.append(f"• 輕軌 {t} 發車（下捷運後等約 {wait_m} 分）➔ {arr_center.strftime('%H:%M')} 到行政中心")
            if len(lrt_connect) == 2:
                break

    lrt_text = "\n".join(lrt_connect) if lrt_connect else "• 該時段輕軌已收班。"

    msg = (
        f"【回程通勤動態】板橋 ➔ 淡水行政中心\n"
        f"查詢時間：{now.strftime('%H:%M:%S')}\n\n"
        f"🔵 北捷板橋站（板南線往市區）：{bl_text}\n\n"
        f"🟢 紅樹林站轉乘輕軌預估（預計 {arr_hm} 抵達）：\n"
        f"{lrt_text}\n\n"
        f"ℹ️ 乘車路徑：板橋搭板南線至台北車站 ➔ 轉紅線至紅樹林 ➔ 轉淡海輕軌。"
    )
    return TextSendMessage(text=msg.strip())

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text.strip().lower()
    
    if "去" in user_msg or "板橋" in user_msg or user_msg == "1":
        reply = get_southbound_reply()
    elif "回" in user_msg or "淡水" in user_msg or "輕軌" in user_msg or user_msg == "2":
        reply = get_northbound_reply()
    else:
        reply = TextSendMessage(text="請傳送「去程」（往板橋）或「回程」（往淡水），即時取得搭車與轉乘銜接建議！")
        
    line_bot_api.reply_message(event.reply_token, reply)

if __name__ == "__main__":
    app.run(port=5000)