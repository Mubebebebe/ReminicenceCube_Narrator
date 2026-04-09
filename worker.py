import os
import json
import logging
import shutil
import math
from PySide6.QtCore import QObject, Signal, Slot
from azure_api import analyze_image, generate_narration_and_actions, synthesize_speech_and_get_timestamps
from video_generator import generate_video, OUTPUT_FPS
from moviepy import concatenate_videoclips, AudioFileClip, CompositeAudioClip, concatenate_audioclips
from moviepy.audio.fx.MultiplyVolume import MultiplyVolume

logger = logging.getLogger(__name__)

class WorkerSignals(QObject):
    status_update = Signal(str)
    finished = Signal(str)
    error = Signal(str)

class GenerationWorker(QObject):
    def __init__(self, input_parts_data, bgm_enabled=False, bgm_path=""):
        super().__init__()
        self.input_parts = input_parts_data
        self.bgm_enabled = bgm_enabled
        self.bgm_path = bgm_path
        self.signals = WorkerSignals()
        self.opened_clips = []

    @Slot()
    def run(self):
        temp_dir = None
        bgm_clip_orig = None
        try:
            all_parts_clips = []
            total = len(self.input_parts)
            for idx, part in enumerate(self.input_parts):
                p_label = f"画像 {idx+1}/{total}"
                self.signals.status_update.emit(f"{p_label}: データ読み込み中...")
                
                with open(part["json_path"], 'r', encoding='utf-8') as f:
                    cube_data = json.load(f)

                img_info = analyze_image(part["image_path"])
                is_last = (idx == total - 1)
                
                self.signals.status_update.emit(f"{p_label}: AI構成作成中...")
                actions = generate_narration_and_actions(part["image_path"], cube_data, is_final_photo=is_last)
                
                if not actions:
                    self.signals.error.emit(f"{p_label} の解析に失敗。")
                    return

                temp_dir = os.path.join(os.path.dirname(part["image_path"]), "temp_work")
                os.makedirs(temp_dir, exist_ok=True)

                audio_infos = []
                for i, act in enumerate(actions):
                    txt = act.get("narration", "")
                    if not txt.strip():
                        audio_infos.append((None, act["visual_action"].get("duration", 2), []))
                        continue
                    
                    self.signals.status_update.emit(f"{p_label}: 音声合成中 ({i+1})...")
                    path, dur, ts = synthesize_speech_and_get_timestamps(txt, os.path.join(temp_dir, f"a_{idx}_{i}"))
                    audio_infos.append((path, dur, ts))

                self.signals.status_update.emit(f"{p_label}: クリップ生成中...")
                clip = generate_video(part["image_path"], actions, audio_infos, img_info, self.opened_clips)
                if clip: all_parts_clips.append(clip)

            if not all_parts_clips: 
                self.signals.error.emit("クリップが生成されませんでした。")
                return
            
            self.signals.status_update.emit("動画を結合・BGMを合成中...")
            final_video = concatenate_videoclips(all_parts_clips, method="compose")

            # BGMの処理
            if self.bgm_enabled and self.bgm_path and os.path.exists(self.bgm_path):
                bgm_clip_orig = AudioFileClip(self.bgm_path)
                bgm_with_volume = bgm_clip_orig.with_effects([MultiplyVolume(0.15)])
                
                # ループ処理
                if bgm_with_volume.duration < final_video.duration:
                    loop_count = math.ceil(final_video.duration / bgm_with_volume.duration)
                    final_bgm_clip = concatenate_audioclips([bgm_with_volume] * int(loop_count))
                    final_bgm_clip = final_bgm_clip.subclipped(0, final_video.duration)
                else:
                    final_bgm_clip = bgm_with_volume.subclipped(0, final_video.duration)

                if final_video.audio:
                    final_video.audio = CompositeAudioClip([final_video.audio, final_bgm_clip])
                else:
                    final_video.audio = final_bgm_clip

            out = os.path.normpath(os.path.join(os.path.dirname(self.input_parts[0]["image_path"]), "output_story.mp4"))
            final_video.write_videofile(out, fps=OUTPUT_FPS, codec="libx264", audio_codec="aac")
            self.signals.finished.emit(f"完了: {out}")

        except Exception as e:
            logger.error(e, exc_info=True)
            self.signals.error.emit(str(e))
        finally:
            if bgm_clip_orig: bgm_clip_orig.close()
            for c in self.opened_clips: c.close()
            if temp_dir and os.path.exists(temp_dir): shutil.rmtree(temp_dir)