import os
import json
import logging
import shutil
from PySide6.QtCore import QObject, Signal, Slot
from azure_api import analyze_image, generate_narration_and_actions, synthesize_speech_and_get_timestamps
from video_generator import generate_video, OUTPUT_FPS

from moviepy import concatenate_videoclips

logger = logging.getLogger(__name__)

class WorkerSignals(QObject):
    status_update = Signal(str)
    finished = Signal(str)
    error = Signal(str)

class GenerationWorker(QObject):
    def __init__(self, input_parts_data):
        super().__init__()
        self.input_parts = input_parts_data
        self.signals = WorkerSignals()
        self.opened_clips = []

    @Slot()
    def run(self):
        temp_dir = None
        try:
            all_parts_clips = []
            for part_idx, part in enumerate(self.input_parts):
                p_label = f"画像 {part_idx+1}"
                self.signals.status_update.emit(f"{p_label}: データ読み込み中...")
                
                # JSONファイルをパスから読み込む
                json_path = part.get("json_path")
                if not json_path or not os.path.exists(json_path):
                    self.signals.error.emit(f"{p_label} のJSONファイルが指定されていないか、存在しません。")
                    return

                try:
                    with open(json_path, 'r', encoding='utf-8') as f:
                        cube_data = json.load(f)
                except Exception as e:
                    self.signals.error.emit(f"JSON読み込みエラー: {str(e)}")
                    return

                img_info = analyze_image(part["image_path"])
                if not img_info:
                    self.signals.error.emit(f"{part['image_path']} の読み込みに失敗しました。")
                    return

                self.signals.status_update.emit(f"{p_label}: AIが構成を考案中...")
                actions = generate_narration_and_actions(img_info, cube_data)
                
                if not actions:
                    self.signals.error.emit("AIの構成生成に失敗しました。")
                    return

                temp_dir = os.path.join(os.path.dirname(part["image_path"]), "temp_assets")
                os.makedirs(temp_dir, exist_ok=True)

                audio_infos = []
                for i, act in enumerate(actions):
                    self.signals.status_update.emit(f"{p_label}: 音声合成中 (シーン {i+1})...")
                    path, dur, ts = synthesize_speech_and_get_timestamps(
                        act["narration"], 
                        os.path.join(temp_dir, f"audio_{part_idx}_{i}")
                    )
                    audio_infos.append((path, dur, ts))

                self.signals.status_update.emit(f"{p_label}: 映像出力中...")
                clip = generate_video(part["image_path"], actions, audio_infos, img_info, self.opened_clips)
                if clip:
                    all_parts_clips.append(clip)

            if not all_parts_clips:
                self.signals.error.emit("クリップが生成されませんでした。")
                return

            self.signals.status_update.emit("最終動画を結合中...")
            final_video = concatenate_videoclips(all_parts_clips, method="compose")
            
            output_name = os.path.splitext(os.path.basename(self.input_parts[0]["image_path"]))[0]
            out_path = os.path.normpath(os.path.join(os.path.dirname(self.input_parts[0]["image_path"]), f"{output_name}_story.mp4"))
            
            final_video.write_videofile(out_path, fps=OUTPUT_FPS, codec="libx264", audio_codec="aac")
            self.signals.finished.emit(f"ビデオ完成！\n保存先: {out_path}")

        except Exception as e:
            logger.error(e, exc_info=True)
            self.signals.error.emit(f"システムエラー: {str(e)}")
        finally:
            for c in self.opened_clips:
                try: c.close()
                except: pass
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)