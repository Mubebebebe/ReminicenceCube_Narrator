import os
import shutil
import logging
import math
import json
from PySide6.QtCore import QObject, Signal, Slot
from azure_api import analyze_image, generate_narration_and_actions, synthesize_speech_and_get_timestamps, load_whisper_model
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
        try:
            load_whisper_model()
            all_video_clips = []
            total = len(self.input_parts)
            for idx, part in enumerate(self.input_parts):
                label = f"画像 {idx+1}/{total}"
                self.signals.status_update.emit(f"{label}: データ解析中...")
                
                with open(part["json_path"], 'r', encoding='utf-8') as f:
                    cube_data = json.load(f)

                img_info = analyze_image(part["image_path"])
                is_final = (idx == total - 1)
                
                self.signals.status_update.emit(f"{label}: AI構成生成中...")
                actions = generate_narration_and_actions(part["image_path"], cube_data, is_final)
                
                temp_dir = os.path.join(os.path.dirname(part["image_path"]), "temp_work")
                os.makedirs(temp_dir, exist_ok=True)

                audio_data_list = []
                for i, act in enumerate(actions):
                    txt = act.get("narration", "")
                    if not txt.strip():
                        audio_data_list.append((None, act["visual_action"].get("duration", 2), []))
                        continue
                    
                    self.signals.status_update.emit(f"{label}: 音声合成中 ({i+1}/{len(actions)})...")
                    path, dur, ts = synthesize_speech_and_get_timestamps(txt, os.path.join(temp_dir, f"audio_{idx}_{i}"))
                    audio_data_list.append((path, dur, ts))

                self.signals.status_update.emit(f"{label}: 描画中...")
                clip = generate_video(part["image_path"], actions, audio_data_list, None, img_info, self.opened_clips, True)
                if clip: all_video_clips.append(clip)

            if not all_video_clips:
                self.signals.error.emit("クリップ生成に失敗しました。")
                return

            self.signals.status_update.emit("最終動画を結合中...")
            final_video = concatenate_videoclips(all_video_clips, method="compose")

            if self.bgm_enabled and os.path.exists(self.bgm_path):
                bgm = AudioFileClip(self.bgm_path).with_effects([MultiplyVolume(0.15)])
                if bgm.duration < final_video.duration:
                    bgm = concatenate_audioclips([bgm] * math.ceil(final_video.duration / bgm.duration))
                bgm = bgm.subclipped(0, final_video.duration)
                final_video.audio = CompositeAudioClip([final_video.audio, bgm]) if final_video.audio else bgm

            out_path = os.path.normpath(os.path.join(os.path.dirname(self.input_parts[0]["image_path"]), "output_video.mp4"))
            final_video.write_videofile(out_path, fps=OUTPUT_FPS, codec="libx264", audio_codec="aac")
            self.signals.finished.emit(f"動画が完成しました！\n場所: {out_path}")

        except Exception as e:
            logger.error(e, exc_info=True)
            self.signals.error.emit(str(e))
        finally:
            for c in self.opened_clips: c.close()
            if temp_dir and os.path.exists(temp_dir): shutil.rmtree(temp_dir)