import os
import json
import piexif
import pathlib
import bisect
import argparse
from datetime import datetime
import tkinter as tk
from tkinter import filedialog
from tqdm import tqdm
import sys
import io
import base64
import re

# ==========================================
# 🛡️ 終極防護盾 1：強制終端機輸出，遇錯直接替換
# ==========================================
# 這樣一來，就算印出包含怪異符號的檔名，終端機也不會崩潰，而是印出 
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ==========================================
# 🛡️ 終極防護盾 2：清除所有會導致字型問題的符號
# ==========================================
def clean_text(text):
    """清除 Emoji 與罕見特殊符號，只保留基本可見字元與中日韓文字"""
    if not text: return ""
    text = str(text)
    # 移除非基本多文種平面 (BMP) 的字元 (這會殺掉 99% 的 Emoji 和怪異系統符號)
    return re.sub(r'[^\u0000-\uFFFF]', '', text)

if getattr(sys, 'frozen', False):
    # 先單獨 import 肇事的底層模組
    import gradio.component_meta
    
    # 將這個會去讀取原始碼的函數，強制替換成一個「什麼都不做」的空函數 (lambda)
    # 這樣 Gradio 就完全不會去讀取檔案，完美避開 DecodeError 與 ValueError！
    gradio.component_meta.create_or_modify_pyi = lambda *args, **kwargs: None

# ⚠️ 必須在防護盾架設完畢後，才能 import Gradio
import gradio as gr

# ==========================================
# 載入語系設定
# ==========================================
with open('config.json', 'r', encoding='utf-8', errors='replace') as f:
    I18N = json.load(f)

class LangManager:
    def __init__(self):
        self.lang = "zh"
    
    def get_text(self, key):
        return I18N[self.lang].get(key, f"[Missing: {key}]")
    
    def set_lang(self, lang):
        if lang in I18N:
            self.lang = lang
            return True
        return False

lang_manager = LangManager()

def get_t():
    """取得當前語言的翻譯字典"""
    return {k: I18N[lang_manager.lang].get(k, f"[Missing: {k}]") for k in I18N[lang_manager.lang].keys()}

T = get_t()

FILE_HAS_GPS = 'step1_has_gps.json'
FILE_NO_GPS = 'step1_no_gps.json'
FILE_MATCHED = 'step2_matched_plan.json'
FILE_PROGRESS = 'step3_write_progress.json'

# ==========================================
# 輔助函數
# ==========================================
def get_photo_exif_info(path):
    try:
        exif_dict = piexif.load(path)
        dt_str = None
        if "Exif" in exif_dict and piexif.ExifIFD.DateTimeOriginal in exif_dict["Exif"]:
            dt_str = exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal].decode('ascii').strip('\x00')
        elif "0th" in exif_dict and piexif.ImageIFD.DateTime in exif_dict["0th"]:
            dt_str = exif_dict["0th"][piexif.ImageIFD.DateTime].decode('ascii').strip('\x00')
        if not dt_str: return None, None, None
        try:
            dt = datetime.strptime(dt_str, '%Y:%m:%d %H:%M:%S')
        except ValueError:
            try:
                dt = datetime.fromisoformat(dt_str).replace(tzinfo=None)
            except ValueError:
                dt = datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
        ts = dt.timestamp()
        lat, lon = None, None
        gps_ifd = exif_dict.get("GPS", {})
        if piexif.GPSIFD.GPSLatitude in gps_ifd and piexif.GPSIFD.GPSLongitude in gps_ifd:
            lat = convert_exif_to_decimal(gps_ifd[piexif.GPSIFD.GPSLatitude], gps_ifd.get(piexif.GPSIFD.GPSLatitudeRef, b'N'))
            lon = convert_exif_to_decimal(gps_ifd[piexif.GPSIFD.GPSLongitude], gps_ifd.get(piexif.GPSIFD.GPSLongitudeRef, b'E'))
        return ts, lat, lon
    except Exception: return None, None, None

def convert_exif_to_decimal(exif_coords, ref):
    d = exif_coords[0][0] / exif_coords[0][1]
    m = exif_coords[1][0] / exif_coords[1][1]
    s = exif_coords[2][0] / exif_coords[2][1]
    val = d + (m / 60.0) + (s / 3600.0)
    if ref in [b'S', b'W', 'S', 'W']: val = -val
    return val

def to_deg(value):
    abs_v = abs(value)
    d = int(abs_v)
    m_f = (abs_v - d) * 60
    m = int(m_f)
    s = round((m_f - m) * 60, 5)
    return (d, m, s)

def get_exif_gps_dict(lat, lon):
    lat_d = to_deg(lat)
    lon_d = to_deg(lon)
    def to_r(v): return (int(v * 100000), 100000)
    return {
        piexif.GPSIFD.GPSVersionID: (2, 0, 0, 0),
        piexif.GPSIFD.GPSLatitudeRef: 'N' if lat >= 0 else 'S',
        piexif.GPSIFD.GPSLatitude: [to_r(lat_d[0]), to_r(lat_d[1]), to_r(lat_d[2])],
        piexif.GPSIFD.GPSLongitudeRef: 'E' if lon >= 0 else 'W',
        piexif.GPSIFD.GPSLongitude: [to_r(lon_d[0]), to_r(lon_d[1]), to_r(lon_d[2])],
    }

# ==========================================
# 核心處理類別
# ==========================================
class PhotoGPSProcessor:
    def __init__(self, photo_dir, max_diff_sec=1800, output_dir=None):
        self.photo_dir = photo_dir
        self.max_diff_sec = max_diff_sec
        self.output_dir = output_dir if output_dir else os.path.join(os.getcwd(), "data")
        os.makedirs(self.output_dir, exist_ok=True)
        self.state_has_gps = os.path.join(self.output_dir, FILE_HAS_GPS)
        self.state_no_gps = os.path.join(self.output_dir, FILE_NO_GPS)
        self.state_matched = os.path.join(self.output_dir, FILE_MATCHED)
        self.state_progress = os.path.join(self.output_dir, FILE_PROGRESS)

    def step1_scan(self):
        if os.path.exists(self.state_has_gps) and os.path.exists(self.state_no_gps):
            yield get_t()["msg_scan_skip"]
            return
        yield get_t()["msg_scan_start"]
        all_files = []
        for root, dirs, files in os.walk(self.photo_dir):
            if '@eaDir' in root: 
                dirs[:] = [d for d in dirs if d != '@eaDir']
                continue
            for file in files:
                if file.lower().endswith(('.jpg', '.jpeg')):
                    all_files.append(os.path.join(root, file))
        total = len(all_files)
        if total == 0:
            yield get_t()["msg_no_photo"]
            return
        yield get_t()["msg_scan_info"].format(total)
        has_gps, no_gps = [], []
        pbar = tqdm(total=total, desc="Scanning", unit="img")
        for count, full_path in enumerate(all_files, start=1):
            rel_path = str(pathlib.Path(full_path).relative_to(self.photo_dir))
            ts, lat, lon = get_photo_exif_info(full_path)
            if ts:
                if lat is not None: has_gps.append({'path': rel_path, 'ts': ts, 'lat': lat, 'lon': lon})
                else: no_gps.append({'path': rel_path, 'ts': ts})
            pbar.update(1)
            if count % 20 == 0: yield f"Progress: {count}/{total} (GPS: {len(has_gps)}, No GPS: {len(no_gps)})"
        pbar.close()
        with open(self.state_has_gps, 'w', encoding='utf-8') as f: json.dump(has_gps, f)
        with open(self.state_no_gps, 'w', encoding='utf-8') as f: json.dump(no_gps, f)
        yield get_t()["msg_scan_done"].format(total, len(has_gps), len(no_gps))

    def step2_match_internal(self):
        if os.path.exists(self.state_matched):
            yield get_t()["msg_match_skip"]
            return
        yield get_t()["msg_match_internal"]
        with open(self.state_has_gps, 'r', encoding='utf-8') as f: has_gps = json.load(f)
        with open(self.state_no_gps, 'r', encoding='utf-8') as f: no_gps = json.load(f)
        has_gps.sort(key=lambda x: x['ts'])
        gps_ts = [x['ts'] for x in has_gps]
        matched = []
        for i, item in enumerate(no_gps, start=1):
            idx = bisect.bisect_left(gps_ts, item['ts'])
            best, min_diff = None, float('inf')
            cands = []
            if idx < len(has_gps): cands.append(has_gps[idx])
            if idx > 0: cands.append(has_gps[idx-1])
            for c in cands:
                diff = abs(c['ts'] - item['ts'])
                if diff < min_diff: min_diff, best = diff, c
            if best and min_diff <= self.max_diff_sec:
                matched.append({'path': item['path'], 'diff_sec': min_diff, 'lat': best['lat'], 'lon': best['lon']})
            if i % 50 == 0: yield f"Matching: {i}/{len(no_gps)}..."
        with open(self.state_matched, 'w', encoding='utf-8') as f: json.dump(matched, f)
        yield get_t()["msg_match_done"].format(len(matched))

    def step2_match_timeline(self, timeline_path):
        if os.path.exists(self.state_matched):
            yield get_t()["msg_match_skip"]
            return
        yield get_t()["msg_match_timeline"]
        with open(self.state_no_gps, 'r', encoding='utf-8') as f: no_gps = json.load(f)
        gps_pts = []
        try:
            with open(timeline_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for seg in data.get('semanticSegments', []):
                    if 'timelinePath' in seg:
                        for tp in seg['timelinePath']:
                            lat_s, lon_s = tp['point'].replace('°', '').split(', ')
                            ts = datetime.fromisoformat(tp['time']).replace(tzinfo=None).timestamp()
                            gps_pts.append({"lat": float(lat_s), "lon": float(lon_s), "ts": ts})
        except Exception as e:
            yield f"Error: {e}"
            return
        gps_pts.sort(key=lambda x: x['ts'])
        ts_list = [x['ts'] for x in gps_pts]
        matched = []
        for i, item in enumerate(no_gps, start=1):
            idx = bisect.bisect_left(ts_list, item['ts'])
            best, min_diff = None, float('inf')
            cands = []
            if idx < len(gps_pts): cands.append(gps_pts[idx])
            if idx > 0: cands.append(gps_pts[idx-1])
            for c in cands:
                diff = abs(c['ts'] - item['ts'])
                if diff < min_diff: min_diff, best = diff, c
            if best and min_diff <= self.max_diff_sec:
                matched.append({'path': item['path'], 'diff_sec': min_diff, 'lat': best['lat'], 'lon': best['lon']})
        with open(self.state_matched, 'w', encoding='utf-8') as f: json.dump(matched, f)
        yield get_t()["msg_match_done"].format(len(matched))

    def step3_write_gps(self):
        if not os.path.exists(self.state_matched): return
        with open(self.state_matched, 'r', encoding='utf-8') as f: matched = json.load(f)
        yield get_t()["msg_write_start"].format(len(matched))
        proc = set()
        if os.path.exists(self.state_progress):
            with open(self.state_progress, 'r', encoding='utf-8') as f: proc = set(json.load(f))
            yield get_t()["msg_write_resume"].format(len(proc))
        ok, err, total = 0, 0, len(matched)
        for i, item in enumerate(matched, start=1):
            if item['path'] in proc: 
                ok += 1
                continue
            full = os.path.join(self.photo_dir, item['path'])
            try:
                exif_dict = piexif.load(full)
                exif_dict["GPS"] = get_exif_gps_dict(item['lat'], item['lon'])
                piexif.insert(piexif.dump(exif_dict), full)
                proc.add(item['path'])
                ok += 1
            except: err += 1
            if i % 20 == 0 or i == total:
                with open(self.state_progress, 'w', encoding='utf-8') as f: json.dump(list(proc), f)
                yield f"Writing: {i}/{total}"
        yield get_t()["msg_all_done"].format(ok, err)
    def clear_written_gps(self):
        """復原機制：只洗掉被判定為需要配對的照片的 GPS"""
        if not os.path.exists(self.state_matched):
            yield get_t()["msg_clear_error"]
            return
            
        with open(self.state_matched, 'r', encoding='utf-8') as f:
            matched = json.load(f)
            
        total = len(matched)
        yield get_t()["msg_clear_start"].format(total)
        
        ok, err = 0, 0
        for i, item in enumerate(matched, start=1):
            full_path = os.path.join(self.photo_dir, item['path'])
            try:
                if os.path.exists(full_path):
                    # 讀取 EXIF
                    exif_dict = piexif.load(full_path)
                    # 如果有 GPS 資訊，將其清空
                    if "GPS" in exif_dict:
                        exif_dict["GPS"] = {}
                        # 重新寫回照片
                        piexif.insert(piexif.dump(exif_dict), full_path)
                    ok += 1
            except Exception as e:
                err += 1
                
            if i % 20 == 0 or i == total:
                yield f"Clearing: {i}/{total}"
                
        # 洗掉 GPS 後，把「寫入進度檔(step3)」刪除，這樣下次配對後才能重新寫入
        if os.path.exists(self.state_progress):
            os.remove(self.state_progress)
            
        yield get_t()["msg_clear_done"].format(ok, err)
    def generate_map_html(self):
        all_pts = []
        if os.path.exists(self.state_has_gps):
            with open(self.state_has_gps, 'r', encoding='utf-8') as f:
                for p in json.load(f):
                    p['type'] = 'orig'
                    all_pts.append(p)
        if os.path.exists(self.state_matched):
            with open(self.state_matched, 'r', encoding='utf-8') as f:
                for p in json.load(f):
                    p['type'] = 'match'
                    all_pts.append(p)
                    
        if not all_pts: 
            return f"<h3>{get_t()['msg_map_error']}</h3>"
        
        # 1. 將資料整理成乾淨的 Dictionary，捨棄笨重的 Base64
        js_data = []
        T_dict = get_t()
        
        for p in all_pts:
            # 確保路徑斜線格式正確，供前端使用
            full_path = os.path.join(self.photo_dir, p['path']).replace('\\', '/')
            safe_rel_path = p['path'].replace('\\', '/')
            
            if p['type'] == 'orig': 
                info = T_dict['tag_original']
            else: 
                info = T_dict['tag_matched'].format(round(p.get('diff_sec', 0), 1))
                
            js_data.append({
                "lat": p['lat'],
                "lon": p['lon'],
                "full_path": full_path,
                "rel_path": safe_rel_path,
                "info": info
            })
            
        # 2. 將 Python List 轉為 JSON 字串，完美解決所有引號/逸碼問題
        js_data_json = json.dumps(js_data)
        
        # 3. 建立前端 JS 邏輯 (使用延遲載入)
        html_snippet = f"""
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        
        <div id="map" style="width: 100%; height: 600px; position: relative; z-index: 1; border-radius: 8px;"></div>
        
        <script>
            var container = L.DomUtil.get('map');
            if(container != null){{
                container._leaflet_id = null;
            }}
            
            var map = L.map('map').setView([25.0330, 121.5654], 12);
            
            L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                maxZoom: 19,
                attribution: '&copy; <a href="https://www.openstreetmap.org/">OpenStreetMap</a> contributors'
            }}).addTo(map);
            
            // 讀取從 Python 傳來的乾淨 JSON 陣列
            var pts_data = {js_data_json};
            var bounds = [];
            
            // 判斷當前是 Gradio 環境還是本機直接點開的 HTML
            var isLocalHTML = window.location.protocol === 'file:';
            
            pts_data.forEach(function(p) {{
                var marker = L.marker([p.lat, p.lon]).addTo(map);
                
                // 動態決定圖片路徑 (Gradio用 /file=，本機檔案用 file:///)
                var imgSrc = isLocalHTML ? 'file:///' + p.full_path : '/file=' + p.full_path;
                
                var popupContent = '<b>' + p.rel_path + '</b><br>' + 
                                   '<img src="' + imgSrc + '" width="200px" style="margin:5px 0; border-radius:4px;"><br>' + 
                                   p.info;
                                   
                // Leaflet 的 bindPopup 預設就是「點擊才生成 HTML」
                // 因此圖片只會在你點擊該標記時才向系統請求讀取，完全不卡頓！
                marker.bindPopup(popupContent);
                bounds.push([p.lat, p.lon]);
            }});
            
            if(bounds.length > 0) {{
                map.fitBounds(L.latLngBounds(bounds));
            }}
        </script>
        """
        
        # 4. 輸出完整的備份用 HTML 檔案
        try:
            full_html_for_file = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GPS 照片地圖</title>
</head>
<body style="margin:0; padding:0; background-color:#f0f0f0;">
    <div style="padding: 20px;">
        <h2 style="font-family: sans-serif; text-align: center; margin-bottom: 20px;">Photo GPS Map</h2>
        {html_snippet}
    </div>
</body>
</html>"""
            html_file_path = os.path.join(self.output_dir, "photo_gps_map.html")
            with open(html_file_path, 'w', encoding='utf-8') as f:
                f.write(full_html_for_file)
        except Exception as e:
            pass
        
        return html_snippet

# ==========================================
# UI 邏輯
# ==========================================
def open_folder_dialog(curr):
    root = tk.Tk()
    root.attributes('-topmost', True)
    root.withdraw()
    path = filedialog.askdirectory()
    root.destroy()
    return path if path else curr

def open_file_dialog(curr):
    root = tk.Tk()
    root.attributes('-topmost', True)
    root.withdraw()
    path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
    root.destroy()
    return path if path else curr

def run_gradio_app():
    with gr.Blocks(title=get_t()["title"]) as demo:
        gr.Markdown(f"# {get_t()['title']}")
        
        # ==========================================
        # 語言選擇區域
        # ==========================================
        with gr.Row():
            lang_radio = gr.Radio(
                choices=list(I18N.keys()), 
                value=lang_manager.lang,
                label=get_t()['lang_label'],
                scale=1
            )
        
        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown(f"### {get_t()['step1_title']}")
                with gr.Row():
                    p_dir = gr.Textbox(label=get_t()["photo_dir_label"], value="Y:\\", scale=4)
                    btn_p = gr.Button(get_t()["btn_browse_dir"], scale=1)
                with gr.Row():
                    o_dir = gr.Textbox(label=get_t()["out_dir_label"], value=os.path.join(os.getcwd(), "data"), scale=4)
                    btn_o = gr.Button(get_t()["btn_browse_dir"], scale=1)
                tol = gr.Slider(60, 86400, 1800, step=60, label=get_t()["step2_title"])
                gr.Markdown(f"### {get_t()['step3_title']}")
                meth = gr.Radio([get_t()["method_internal"], get_t()["method_timeline"]], label="", value=get_t()["method_internal"])
                with gr.Row(visible=False) as t_row:
                    t_path = gr.Textbox(label=get_t()["timeline_label"], scale=4)
                    btn_t = gr.Button(get_t()["btn_browse_file"], scale=1)
                meth.change(lambda m: gr.update(visible=(m==get_t()["method_timeline"])), meth, t_row)
                with gr.Row():
                    b_run = gr.Button(get_t()["btn_start"], variant="primary")
                    b_reset = gr.Button(get_t()["btn_reset"], variant="stop")
                    b_clear = gr.Button(get_t()["btn_clear_gps"], variant="secondary") # <-- 新增這行
                    b_map = gr.Button(get_t()["btn_map"])
            with gr.Column(scale=2):
                log = gr.TextArea(label=get_t()["log_label"], lines=10, interactive=False)
                map_ui = gr.HTML(label=get_t()["map_label"])

        btn_p.click(open_folder_dialog, p_dir, p_dir)
        btn_o.click(open_folder_dialog, o_dir, o_dir)
        btn_t.click(open_file_dialog, t_path, t_path)

        def workflow(pd, od, to, me, tp):
            proc = PhotoGPSProcessor(pd, to, od)
            txt = ""
            for m in proc.step1_scan(): txt = m + "\n" + txt; yield txt, None
            if me == get_t()["method_timeline"]:
                for m in proc.step2_match_timeline(tp): txt = m + "\n" + txt; yield txt, None
            else:
                for m in proc.step2_match_internal(): txt = m + "\n" + txt; yield txt, None
            for m in proc.step3_write_gps(): txt = m + "\n" + txt; yield txt, None
            yield "Finished.\n" + txt, proc.generate_map_html()

        def clear_gps_workflow(pd, od):
            proc = PhotoGPSProcessor(pd, 0, od)
            txt = ""
            for m in proc.clear_written_gps():
                txt = m + "\n" + txt
                yield txt


        def reset(od):
            p = PhotoGPSProcessor("", 0, od)
            for f in [p.state_has_gps, p.state_no_gps, p.state_matched, p.state_progress]:
                if os.path.exists(f): os.remove(f)
                return "Cache cleared."
        
        def change_language(lang):
            """語言改變時的回調函數 - 刷新頁面以應用新語言"""
            if lang_manager.set_lang(lang):
                # 返回JavaScript來刷新頁面
                return gr.update(
                    value="""
                    <script>
                        setTimeout(function() {
                            location.reload();
                        }, 500);
                    </script>
                    <h3>語言已改變，頁面重新載入中...</h3>
                    <p>Language changed, page reloading...</p>
                    """
                )
            return gr.update(value="<p>語言切換失敗 / Language switch failed</p>")

        b_run.click(workflow, [p_dir, o_dir, tol, meth, t_path], [log, map_ui])
        b_reset.click(reset, o_dir, log)
        b_map.click(lambda pd, od: PhotoGPSProcessor(pd, 0, od).generate_map_html(), [p_dir, o_dir], map_ui)
        b_clear.click(clear_gps_workflow, [p_dir, o_dir], log)

        # 語言選擇改變時刷新頁面
        lang_radio.change(change_language, lang_radio, map_ui)

    demo.launch(inbrowser=True, allowed_paths=["/"])

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Immich 照片 GPS 補齊系統")
    parser.add_argument("--cli", action="store_true", help="使用終端機(CLI)模式執行")
    parser.add_argument("--clear", action="store_true", help="清除已寫入的 GPS (復原)")
    parser.add_argument("--dir", default=".", help="照片資料夾路徑 (CLI 專用)")
    parser.add_argument("--timeline", default="", help="Google Timeline JSON 路徑 (CLI 專用)")
    parser.add_argument("--tolerance", type=int, default=1800, help="時間誤差秒數 (CLI 專用)")
    parser.add_argument("--outdir", default="data", help="暫存資料庫輸出目錄 (CLI 專用)")
    parser.add_argument("--lang", default="en", choices=list(I18N.keys()), help="介面語言 (zh 或 en)")
    
    args = parser.parse_args()
    
    # 設定語言
    if hasattr(args, 'lang'):
        lang_manager.set_lang(args.lang)
    
    if args.cli:
        p = PhotoGPSProcessor(args.dir, args.tolerance, args.outdir)
        
        if args.clear:
            for m in p.clear_written_gps(): 
                print(m)
        else:
            for m in p.step1_scan(): 
                print(m)
            if args.timeline:
                for m in p.step2_match_timeline(args.timeline): 
                    print(m)
            else:
                for m in p.step2_match_internal(): 
                    print(m)
            for m in p.step3_write_gps(): 
                print(m)
    else: 
        run_gradio_app()