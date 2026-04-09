import os
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import logging
import math
import textwrap
from moviepy import ImageClip, AudioFileClip, CompositeVideoClip, concatenate_videoclips, VideoClip

logger = logging.getLogger(__name__)
OUTPUT_WIDTH, OUTPUT_HEIGHT, OUTPUT_FPS = 1280, 720, 24

def get_font():
    paths = ['C:/Windows/Fonts/YuGothM.ttc', 'MS Gothic', 'Arial']
    for p in paths:
        if os.path.exists(p): return p
    return "Arial"

def create_sub(text, font_p):
    try: font = ImageFont.truetype(font_p, 40)
    except: font = ImageFont.load_default()
    wrapped = "\n".join(textwrap.wrap(text, width=30))
    dummy = Image.new("RGB", (1,1))
    draw = ImageDraw.Draw(dummy)
    bbox = draw.multiline_textbbox((0,0), wrapped, font=font)
    img = Image.new("RGBA", (bbox[2]+20, bbox[3]+20), (0,0,0,0))
    ImageDraw.Draw(img).multiline_text((10,10), wrapped, font=font, fill="white", stroke_width=2, stroke_fill="black", align="center")
    return ImageClip(np.array(img))

def res_pad(img, size=(OUTPUT_WIDTH, OUTPUT_HEIGHT)):
    tw, th = size
    img_aspect = img.width / img.height
    if img_aspect > (tw/th): nw, nh = tw, int(tw/img_aspect)
    else: nw, nh = int(th*img_aspect), th
    res = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new('RGB', size, (0,0,0))
    canvas.paste(res, ((tw-nw)//2, (th-nh)//2))
    return canvas

def generate_video(image_path, actions, audio_infos, img_info, global_clips):
    base = Image.open(image_path).convert("RGB")
    segments = []
    font_p = get_font()
    curr_cam = (0.5, 0.5, 1.0, 1.0)

    for i, step in enumerate(actions):
        vis = step.get("visual_action", {})
        a_path, a_dur, a_ts = audio_infos[i]
        dur = max(a_dur, vis.get("duration", 5))
        loc = vis.get("location", {"x":0, "y":0, "w":1, "h":1})
        tgt_cam = (loc['x']+loc['w']/2, loc['y']+loc['h']/2, loc['w'], loc['h'])

        def make_f(t, s=curr_cam, e=tgt_cam, d=dur):
            prog = (np.sin((t/d - 0.5) * np.pi) + 1) / 2
            now = [s_val * (1-prog) + e_val * prog for s_val, e_val in zip(s, e)]
            w, h = base.size
            x1, y1 = max(0, int((now[0]-now[2]/2)*w)), max(0, int((now[1]-now[3]/2)*h))
            x2, y2 = min(w, int(x1+now[2]*w)), min(h, int(y1+now[3]*h))
            if x2 <= x1: x2 = x1+1
            if y2 <= y1: y2 = y1+1
            return np.array(res_pad(base.crop((x1, y1, x2, y2))))

        scene = VideoClip(make_f, duration=dur)
        if a_path:
            ac = AudioFileClip(a_path).with_duration(a_dur)
            global_clips.append(ac)
            scene = scene.with_audio(ac)
        
        subs = []
        for ts in a_ts:
            s_c = create_sub(ts['text'], font_p)
            subs.append(s_c.with_start(ts['offset_seconds']).with_duration(ts['duration_seconds']).with_position(('center', 600)))
        
        segments.append(CompositeVideoClip([scene] + subs, size=(OUTPUT_WIDTH, OUTPUT_HEIGHT)) if subs else scene)
        curr_cam = tgt_cam
    return concatenate_videoclips(segments, method="compose")