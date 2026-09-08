import os
import time
from datetime import datetime, timedelta, timezone
import requests
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# ----------------- 設定區 -----------------
TDX_CLIENT_ID = "collinwei-efd04955-cfef-447f"
TDX_CLIENT_SECRET = "527f8c8a-65aa-4c93-9b88-717420f1b3d7"

# 請確認此處已填入你的實際金鑰
LINE_CHANNEL_ACCESS_TOKEN = "QJblEI7lA4KInn0oT05VUwUJ0w+c/G6r0UiBm8gBClZHnqFG2qaVH6F37rJ3k8+dFO1Y796esb0rpALaJtl9NOdFweyhhapslg1hVAaUe9bxPhaRR2tdMFYICHp/A9nVmzhlYn+bt8XFnIaOeNliuAdB04t89/1O/w1cDnyilFU="
LINE_CHANNEL_SECRET = "32fff31542292d95fab773380c920d36"
# ------------------------------------------

# 強制設定台灣時區 (UTC+8)
TAIPEI_TZ = timezone(timedelta(hours=8))

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
    """查詢北捷車站即時到站看板 (StationID: R27紅樹林, R17芝山, BL07板橋)"""
    try:
        token = tdx_auth.get_token()
        url = f"https://tdx.transportdata.tw/api/basic/v2/Rail/Metro/LiveBoard/TRTC?$filter=StationID eq '{station_id}'&$format=JSON"
        res = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=8)
        return res.json() if res.status_code == 200 else []
    except Exception:
        return []

def get_next_lrt_south():
    """推算淡水行政中心往紅樹林的推薦輕軌班次"""
    now = datetime.now(TAIPEI_TZ)
    now_hm = now.strftime("%H:%M")
    lrt_trains = []
    for t in LRT_TIMETABLE_SOUTH:
        if t >= now_hm:
            th, tm = map(int, t.split(":"))
            dept_dt = now.replace(hour=th % 24, minute=tm, second=0, microsecond=0)
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
    return now, lrt_trains

# ----------------- 1. 行政中心 ➔ 板橋 -----------------
def get_to_banqiao_reply():
    now, lrt_trains = get_next_lrt_south()
    if not lrt_trains:
        return TextSendMessage(text="目前非輕軌營運時段（營運時間約 06:00 - 24:00）。")

    first_lrt = lrt_trains[0]
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
        metro_text = "\n• 紅線班距密集（尖峰約 3-4 分、離峰約 6-8 分），到月台即可接續上車。"

    alt_lrt = f"備選次班：{lrt_trains[1]['dept']}（{lrt_trains[1]['wait_m']} 分鐘後）" if len(lrt_trains) > 1 else ""

    msg = (
        f"【去程】淡水行政中心 ➔ 板橋車站\n"
        f"查詢時間：{now.strftime('%H:%M:%S')}\n\n"
        f"🟢 淡海輕軌（淡水行政中心）\n"
        f"• 推薦班次：{first_lrt['dept']} 發車（約 {first_lrt['wait_m']} 分鐘後）\n"
        f"• 抵達紅樹林：約 {first_lrt['arr_hongshulin']}\n"
        f"• 轉乘至北捷月台：約 {first_lrt['ready_time'].strftime('%H:%M')}\n"
        f"{alt_lrt}\n\n"
        f"🔴 北捷紅樹林站銜接預估：{metro_text}\n\n"
        f"ℹ️ 後續乘車：\n"
        f"搭紅線至「台北車站」約 35 分 ➔ 轉板南線至「板橋站」約 13 分。"
    )
    return TextSendMessage(text=msg.strip())

# ----------------- 2. 板橋 ➔ 行政中心 -----------------
def get_from_banqiao_reply():
    now = datetime.now(TAIPEI_TZ)
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

    est_arrive_hongshulin = now + timedelta(minutes=55)
    arr_hm = est_arrive_hongshulin.strftime("%H:%M")

    lrt_connect = []
    for t in LRT_TIMETABLE_NORTH:
        if t >= arr_hm:
            th, tm = map(int, t.split(":"))
            dept_dt = est_arrive_hongshulin.replace(hour=th % 24, minute=tm, second=0, microsecond=0)
            wait_m = max(0, int((dept_dt - est_arrive_hongshulin).total_seconds() // 60))
            arr_center = dept_dt + timedelta(minutes=12)
            lrt_connect.append(f"• 輕軌 {t} 發車（下捷運後等約 {wait_m} 分）➔ {arr_center.strftime('%H:%M')} 到行政中心")
            if len(lrt_connect) == 2:
                break

    lrt_text = "\n".join(lrt_connect) if lrt_connect else "• 該時段輕軌已收班。"

    msg = (
        f"【回程】板橋車站 ➔ 淡水行政中心\n"
        f"查詢時間：{now.strftime('%H:%M:%S')}\n\n"
        f"🔵 北捷板橋站（板南線往台北車站）：{bl_text}\n\n"
        f"🟢 紅樹林站轉乘輕軌預估（預計 {arr_hm} 抵達）：\n"
        f"{lrt_text}\n\n"
        f"ℹ️ 乘車路徑：板橋搭板南線至台北車站 ➔ 轉紅線至紅樹林 ➔ 轉淡海輕軌。"
    )
    return TextSendMessage(text=msg.strip())

# ----------------- 3. 行政中心 ➔ 芝山 -----------------
def get_to_zhishan_reply():
    now, lrt_trains = get_next_lrt_south()
    if not lrt_trains:
        return TextSendMessage(text="目前非輕軌營運時段（營運時間約 06:00 - 24:00）。")

    first_lrt = lrt_trains[0]
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
        # 紅樹林到芝山搭紅線約 17 分鐘
        zhishan_arr = arr_dt + timedelta(minutes=17)
        metro_text += f"\n• 往 {dest}：{arr_dt.strftime('%H:%M')} 到紅樹林 [{status_tag}]\n   ➔ 直達抵達芝山約 {zhishan_arr.strftime('%H:%M')}"

    if not metro_text:
        metro_text = "\n• 紅線尖峰 3-4 分、離峰 6-8 分一班，一車直達芝山站。"

    alt_lrt = f"備選次班：{lrt_trains[1]['dept']}（{lrt_trains[1]['wait_m']} 分鐘後）" if len(lrt_trains) > 1 else ""

    msg = (
        f"【去程】淡水行政中心 ➔ 芝山站（直達免轉車）\n"
        f"查詢時間：{now.strftime('%H:%M:%S')}\n\n"
        f"🟢 淡海輕軌（淡水行政中心）\n"
        f"• 推薦班次：{first_lrt['dept']} 發車（約 {first_lrt['wait_m']} 分鐘後）\n"
        f"• 抵達紅樹林：約 {first_lrt['arr_hongshulin']}\n"
        f"• 轉乘至北捷月台：約 {first_lrt['ready_time'].strftime('%H:%M')}\n"
        f"{alt_lrt}\n\n"
        f"🔴 北捷紅樹林站銜接預估：{metro_text}\n\n"
        f"ℹ️ 乘車說明：紅樹林上車後不用換線，紅線直達芝山約 17 分鐘，全程約 32-35 分鐘。"
    )
    return TextSendMessage(text=msg.strip())

# ----------------- 4. 芝山 ➔ 行政中心 -----------------
def get_from_zhishan_reply():
    now = datetime.now(TAIPEI_TZ)
    raw_metro_r = fetch_metro_board("R17")  # R17 為芝山站
    r_text = ""
    for m in raw_metro_r:
        dest = m.get("DestinationStationName", {}).get("Zh_tw", "")
        # 篩選北上往淡水方向的列車
        if dest != "淡水":
            continue
        sec = m.get("EstimateTime", 0)
        arr_dt = now + timedelta(seconds=sec)
        r_text += f"\n• 往淡水：{arr_dt.strftime('%H:%M')} 進芝山站（約 {sec//60} 分鐘後）"

    if not r_text:
        r_text = "\n• 紅線往淡水班距約 4-7 分鐘，進站即可直接搭乘。"

    # 芝山搭到紅樹林約 17 分鐘 + 連通道轉乘 3 分鐘 = 約 20 分鐘抵達輕軌月台
    est_arrive_hongshulin = now + timedelta(minutes=20)
    arr_hm = est_arrive_hongshulin.strftime("%H:%M")

    lrt_connect = []
    for t in LRT_TIMETABLE_NORTH:
        if t >= arr_hm:
            th, tm = map(int, t.split(":"))
            dept_dt = est_arrive_hongshulin.replace(hour=th % 24, minute=tm, second=0, microsecond=0)
            wait_m = max(0, int((dept_dt - est_arrive_hongshulin).total_seconds() // 60))
            arr_center = dept_dt + timedelta(minutes=12)
            lrt_connect.append(f"• 輕軌 {t} 發車（下捷運後等約 {wait_m} 分）➔ {arr_center.strftime('%H:%M')} 到行政中心")
            if len(lrt_connect) == 2:
                break

    lrt_text = "\n".join(lrt_connect) if lrt_connect else "• 該時段輕軌已收班。"

    msg = (
        f"【回程】芝山站 ➔ 淡水行政中心\n"
        f"查詢時間：{now.strftime('%H:%M:%S')}\n\n"
        f"🔴 北捷芝山站（紅線北上往淡水）：{r_text}\n\n"
        f"🟢 紅樹林站轉乘輕軌預估（預計 {arr_hm} 抵達月台）：\n"
        f"{lrt_text}\n\n"
        f"ℹ️ 乘車說明：芝山搭紅線至紅樹林約 17 分鐘 ➔ 步行轉乘淡海輕軌至行政中心。"
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

    # 指令判斷
    if any(k in user_msg for k in ["芝山回", "回淡水(芝山)", "回淡水-芝山"]):
        reply = get_from_zhishan_reply()
    elif "芝山" in user_msg:
        if "回" in user_msg:
            reply = get_from_zhishan_reply()
        else:
            reply = get_to_zhishan_reply()
    elif "去" in user_msg or "板橋" in user_msg or user_msg == "1":
        if "回" in user_msg:
            reply = get_from_banqiao_reply()
        else:
            reply = get_to_banqiao_reply()
    elif "回" in user_msg or "淡水" in user_msg or user_msg == "2":
        reply = get_from_banqiao_reply()
    else:
        menu_text = (
            "請傳送以下關鍵字查詢通勤銜接：\n\n"
            "🏢 【板橋路線】\n"
            "• 輸入「去板橋」：行政中心 ➔ 板橋\n"
            "• 輸入「回板橋」或「回程」：板橋 ➔ 行政中心\n\n"
            "🏬 【芝山路線】\n"
            "• 輸入「去芝山」或「芝山」：行政中心 ➔ 芝山（直達）\n"
            "• 輸入「回芝山」：芝山 ➔ 行政中心"
        )
        reply = TextSendMessage(text=menu_text)

    line_bot_api.reply_message(event.reply_token, reply)

if __name__ == "__main__":
    app.run(port=5000)
