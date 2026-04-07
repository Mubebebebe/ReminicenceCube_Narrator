import os
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import logging
import math
import textwrap
from moviepy import (
    ImageClip, AudioFileClip, CompositeVideoClip, concatenate_videoclips, VideoClip
)

logger = logging.getLogger(__name__)

# 出力設定
OUTPUT_WIDTH = 1280
OUTPUT_HEIGHT = 720
OUTPUT_FPS = 24

def get_font_path():
    paths = ['C:/Windows/Fonts/YuGothM.ttc', '/System/Library/Fonts/Hiragino Sans GB.ttc', 'Arial']
    for p in paths:
        if os.path.exists(p): return p
    return "Arial"

def create_subtitle_clip(text, font_path, font_size=40):
    try:
        font = ImageFont.truetype(font_path, font_size)
    except:
        font = ImageFont.load_default()
    
    wrapped = "\n".join(textwrap.wrap(text, width=30))
    dummy = ImageDraw.Draw(Image.new("RGB", (1,1)))
    bbox = dummy.multiline_textbbox((0,0), wrapped, font=font)
    
    w, h = bbox[2] + 20, bbox[3] + 20
    img = Image.new("RGBA", (w, h), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    # 背景の黒い縁取り（視認性向上）
    draw.multiline_text((10,10), wrapped, font=font, fill="white", stroke_width=2, stroke_fill="black", align="center")
    return ImageClip(np.array(img))

def resize_with_padding(pil_img, target_size=(OUTPUT_WIDTH, OUTPUT_HEIGHT)):
    tw, th = target_size
    img_aspect = pil_img.width / pil_img.height
    target_aspect = tw / th
    
    if img_aspect > target_aspect:
        nw = tw
        nh = int(nw / img_aspect)
    else:
        nh = th
        nw = int(nh * img_aspect)
    
    resized = pil_img.resize((nw, nh), Image.Resampling.LANCZOS)
    bg = Image.new('RGB', target_size, (0, 0, 0))
    bg.paste(resized, ((tw - nw) // 2, (th - nh) // 2))
    return bg

def generate_video(image_path, actions, audio_infos, img_info, global_clips):
    base_img = Image.open(image_path).convert("RGB")
    segments = []
    font_path = get_font_path()
    
    # 最初のカメラ状態（全体表示）
    prev_cam = (0.5, 0.5, 1.0, 1.0) # cx, cy, w, h (相対)

    for i, act_step in enumerate(actions):
        v_act = act_step.get("visual_action", {})
        aud_path, aud_dur, timestamps = audio_infos[i]
        
        duration = max(aud_dur, v_act.get("duration", 5))
        loc = v_act.get("location", {"x":0, "y":0, "w":1, "h":1})
        
        # ターゲットカメラ状態
        target_cam = (loc['x'] + loc['w']/2, loc['y'] + loc['h']/2, loc['w'], loc['h'])

        def make_frame(t, start=prev_cam, end=target_cam, d=duration):
            # サインカーブによる滑らかな移動
            prog = (np.sin((t / d - 0.5) * np.pi) + 1) / 2
            curr = [s * (1 - prog) + e * prog for s, e in zip(start, end)]
            
            # クロップ座標（ピクセル）
            im_w, im_h = base_img.size
            cx, cy, cw, ch = curr
            x1 = max(0, int((cx - cw/2) * im_w))
            y1 = max(0, int((cy - ch/2) * im_h))
            x2 = min(im_w, int(x1 + cw * im_w))
            y2 = min(im_h, int(y1 + ch * im_h))
            
            # 画像が小さすぎないかチェック
            if x2 <= x1: x2 = x1 + 1
            if y2 <= y1: y2 = y1 + 1
            
            cropped = base_img.crop((x1, y1, x2, y2))
            return np.array(resize_with_padding(cropped))

        scene = VideoClip(make_frame, duration=duration)
        
        # 音声
        if aud_path:
            audio = AudioFileClip(aud_path).with_duration(aud_dur)
            global_clips.append(audio)
            scene = scene.with_audio(audio)
        
        # 字幕
        sub_clips = []
        for ts in timestamps:
            sub = create_subtitle_clip(ts['text'], font_path)
            sub = sub.with_start(ts['offset_seconds']).with_duration(ts['duration_seconds']).with_position(('center', 600))
            sub_clips.append(sub)
        
        if sub_clips:
            scene = CompositeVideoClip([scene] + sub_clips, size=(OUTPUT_WIDTH, OUTPUT_HEIGHT))
            
        segments.append(scene)
        prev_cam = target_cam

    return concatenate_videoclips(segments, method="compose")