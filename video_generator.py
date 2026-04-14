import os
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import logging
import math
import textwrap
from moviepy import ImageClip, AudioFileClip, CompositeVideoClip, concatenate_videoclips, VideoClip

logger = logging.getLogger(__name__)

# --- 定数定義 ---
OUTPUT_WIDTH = 1280
OUTPUT_HEIGHT = 720
OUTPUT_FPS = 24
SUBTITLE_FONT_SIZE = 42
# ズーム速度を向上させるための遷移時間（2秒）
TRANSITION_DURATION = 2.0

def get_font_path():
    candidates = ['C:/Windows/Fonts/YuGothM.ttc', 'MS Gothic', 'Arial']
    for p in candidates:
        if os.path.exists(p): return p
    return "Arial"

def create_subtitle_clip(text, font_path, font_size, max_width):
    try: font = ImageFont.truetype(font_path, font_size)
    except: font = ImageFont.load_default()
    
    avg_char_w = font.getlength("あ")
    chars_per_line = max(1, int(max_width / avg_char_w))
    wrapped_text = "\n".join(textwrap.wrap(text, width=chars_per_line))

    dummy_draw = ImageDraw.Draw(Image.new("RGB", (1,1)))
    bbox = dummy_draw.multiline_textbbox((0, 0), wrapped_text, font=font, spacing=6)
    w, h = bbox[2] - bbox[0] + 30, bbox[3] - bbox[1] + 30
    
    img = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for dx, dy in [(-2,-2), (2,-2), (-2,2), (2,2)]:
        draw.multiline_text((15+dx, 15+dy), wrapped_text, font=font, fill="black", align="center", spacing=6)
    draw.multiline_text((15, 15), wrapped_text, font=font, fill="white", align="center", spacing=6)
    return ImageClip(np.array(img))

def resize_with_padding(pil_img, target_size):
    tw, th = target_size
    img_aspect = pil_img.width / pil_img.height
    if img_aspect > (tw/th): nw, nh = tw, int(tw/img_aspect)
    else: nh, nw = th, int(th*img_aspect)
    resized = pil_img.resize((nw, nh), Image.Resampling.LANCZOS)
    bg = Image.new('RGB', target_size, (0, 0, 0))
    bg.paste(resized, ((tw - nw) // 2, (th - nh) // 2))
    return bg

def generate_video(image_path, actions, audio_infos, _, image_analysis, global_clips, narration_enabled, visual_effects_enabled=True):
    output_size = (OUTPUT_WIDTH, OUTPUT_HEIGHT)
    font_path = get_font_path()
    base_image = Image.open(image_path).convert("RGB")
    video_segments = []
    prev_cam = (0.5, 0.5, 1.0, 1.0) # cx, cy, w, h

    for i, (audio_info, action_step) in enumerate(zip(audio_infos, actions)):
        audio_path, aud_dur, timestamps = audio_info
        act = action_step.get('visual_action', {})
        video_dur = max(aud_dur, act.get('duration', 2))
        
        loc = act.get('location', {'x':0, 'y':0, 'w':1, 'h':1})
        cam_target = (loc['x'] + loc['w']/2, loc['y'] + loc['h']/2, loc['w'], loc['h'])
        
        # 最初の2秒でズームを完了させ、残りは静止
        is_static = np.allclose(prev_cam, cam_target, atol=1e-3)
        move_dur = 0.0 if is_static else min(TRANSITION_DURATION, video_dur)
        still_dur = video_dur - move_dur

        scene_clips = []
        if move_dur > 0:
            def make_frame(t, s=prev_cam, e=cam_target, d=move_dur):
                prog = (np.sin((t / d - 0.5) * np.pi) + 1) / 2
                curr = [sv * (1 - prog) + ev * prog for sv, ev in zip(s, e)]
                w, h = base_image.size
                x1, y1 = (curr[0] - curr[2]/2) * w, (curr[1] - curr[3]/2) * h
                cropped = base_image.crop((int(x1), int(y1), int(x1 + curr[2]*w), int(y1 + curr[3]*h)))
                return np.array(resize_with_padding(cropped, output_size))
            scene_clips.append(VideoClip(make_frame, duration=move_dur))

        if still_dur > 0:
            w, h = base_image.size
            x1, y1 = (cam_target[0] - cam_target[2]/2) * w, (cam_target[1] - cam_target[3]/2) * h
            final_crop = base_image.crop((int(x1), int(y1), int(x1 + cam_target[2]*w), int(y1 + cam_target[3]*h)))
            scene_clips.append(ImageClip(np.array(resize_with_padding(final_crop, output_size))).with_duration(still_dur))

        scene = concatenate_videoclips(scene_clips) if len(scene_clips) > 1 else scene_clips[0]
        if audio_path:
            ac = AudioFileClip(audio_path).with_duration(aud_dur)
            global_clips.append(ac)
            scene = scene.with_audio(ac)

        subtitle_clips = []
        if timestamps:
            for ts in timestamps:
                sub_clip = create_subtitle_clip(ts['text'], font_path, SUBTITLE_FONT_SIZE, int(output_size[0] * 0.85))
                y_pos = output_size[1] * 0.85 - sub_clip.size[1]
                positioned = (sub_clip.with_position(('center', y_pos))
                              .with_start(ts['offset_seconds'])
                              .with_duration(min(ts['duration_seconds'], video_dur - ts['offset_seconds'])))
                subtitle_clips.append(positioned)

        video_segments.append(CompositeVideoClip([scene] + subtitle_clips, size=output_size) if subtitle_clips else scene)
        prev_cam = cam_target

    return concatenate_videoclips(video_segments, method="compose")