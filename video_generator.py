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
# ズームをより際立たせるため、遷移時間を調整（2秒）
TRANSITION_DURATION = 2.0

def get_font_path():
    candidates = ['C:/Windows/Fonts/YuGothM.ttc', 'C:/Windows/Fonts/msgothic.ttc', 'Arial']
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
    
    # 初期のカメラ位置（全体表示）: (cx, cy, w, h)
    prev_cam = (0.5, 0.5, 1.0, 1.0)

    for i, (audio_info, action_step) in enumerate(zip(audio_infos, actions)):
        audio_path, aud_dur, timestamps = audio_info
        
        # --- 座標データの抽出（柔軟な対応） ---
        # AIが visual_action をネストし忘れた場合にも対応
        visual_data = action_step.get('visual_action', action_step)
        video_dur = max(aud_dur, float(visual_data.get('duration', 2)))
        
        loc = visual_data.get('location', {})
        if not loc or not isinstance(loc, dict):
            loc = {'x': 0, 'y': 0, 'w': 1, 'h': 1}
        
        # 座標を数値に変換（安全策）
        try:
            lx = float(loc.get('x', 0))
            ly = float(loc.get('y', 0))
            lw = float(loc.get('w', 1))
            lh = float(loc.get('h', 1))
            # 異常値（w, hが0など）のチェック
            if lw <= 0: lw = 1.0
            if lh <= 0: lh = 1.0
        except:
            lx, ly, lw, lh = 0, 0, 1, 1

        cam_target = (lx + lw/2, ly + lh/2, lw, lh)
        
        # ズームが必要か判定
        is_static = np.allclose(prev_cam, cam_target, atol=1e-3)
        move_dur = 0.0 if is_static else min(TRANSITION_DURATION, video_dur)
        still_dur = max(0, video_dur - move_dur)

        scene_clips = []
        
        # 1. ズーム・移動アニメーション
        if move_dur > 0:
            def make_frame(t, s=prev_cam, e=cam_target, d=move_dur):
                # イージング関数で滑らかに移動
                prog = (np.sin((t / d - 0.5) * np.pi) + 1) / 2
                curr = [sv * (1 - prog) + ev * prog for sv, ev in zip(s, e)]
                
                img_w, img_h = base_image.size
                # 中心座標からクロップ範囲を計算
                x1 = max(0, (curr[0] - curr[2]/2) * img_w)
                y1 = max(0, (curr[1] - curr[3]/2) * img_h)
                x2 = min(img_w, x1 + curr[2] * img_w)
                y2 = min(img_h, y1 + curr[3] * img_h)
                
                cropped = base_image.crop((int(x1), int(int(y1)), int(x2), int(y2)))
                return np.array(resize_with_padding(cropped, output_size))
            
            scene_clips.append(VideoClip(make_frame, duration=move_dur))

        # 2. 静止（ズーム完了後の保持）
        if still_dur > 0 or (move_dur == 0):
            img_w, img_h = base_image.size
            x1 = max(0, (cam_target[0] - cam_target[2]/2) * img_w)
            y1 = max(0, (cam_target[1] - cam_target[3]/2) * img_h)
            x2 = min(img_w, x1 + cam_target[2] * img_w)
            y2 = min(img_h, y1 + cam_target[3] * img_h)
            
            final_crop = base_image.crop((int(x1), int(y1), int(x2), int(y2)))
            scene_clips.append(ImageClip(np.array(resize_with_padding(final_crop, output_size))).with_duration(max(0.1, still_dur)))

        # クリップの結合
        scene = concatenate_videoclips(scene_clips) if len(scene_clips) > 1 else scene_clips[0]
        
        if audio_path:
            ac = AudioFileClip(audio_path).with_duration(aud_dur)
            global_clips.append(ac)
            scene = scene.with_audio(ac)

        # 字幕の追加
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