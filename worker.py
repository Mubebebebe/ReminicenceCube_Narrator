import os
import json
import logging
import shutil
from PySide6.QtCore import QObject, Signal, Slot
from azure_api import analyze_image, generate_narration_and_actions, synthesize_speech_and_get_timestamps
from video_generator import generate_video, OUTPUT_FPS

from moviepy import concatenate_videoclips

# --- ここを追加しました ---
logger = logging.getLogger(__name__)
# ------------------------

class WorkerSignals(QObject):
    """ワーカーのシグナルを定義するクラス"""
    status_update = Signal(str)
    finished = Signal(str)
    error = Signal(str)

class GenerationWorker(QObject):
    """動画生成を担当するワーカークラス"""
    def __init__(self, input_parts_data):
        super().__init__()
        self.input_parts = input_parts_data
        self.signals = WorkerSignals()
        self.opened_clips = []

    @Slot()
    def run(self):
        """動画生成のメイン処理を実行する"""
        temp_dir = None
        try:
            all_parts_clips = []
            for part_idx, part in enumerate(self.input_parts):
                p_label = f"画像 {part_idx+1}"
                self.signals.status_update.emit(f"{p_label}: データ解析中...")
                
                # 入力されたテキスト（JSON）をパース
                try:
                    cube_data = json.loads(part["conversation_text"])
                except Exception as e:
                    self.signals.error.emit(f"JSONパースエラー: {str(e)}")
                    return

                # 画像の基本情報取得
                img_info = analyze_image(part["image_path"])
                if not img_info:
                    self.signals.error.emit(f"{part['image_path']} の読み込みに失敗しました。")
                    return

                # Azure OpenAIによるストーリーの再構成
                self.signals.status_update.emit(f"{p_label}: AIが物語を構成中...")
                actions = generate_narration_and_actions(img_info, cube_data)
                
                if not actions:
                    self.signals.error.emit("AIによる構成案の生成に失敗しました。")
                    return

                # 一時保存ディレクトリの作成
                temp_dir = os.path.join(os.path.dirname(part["image_path"]), "temp_assets")
                os.makedirs(temp_dir, exist_ok=True)

                # 各シーンの音声生成とタイムスタンプ取得
                audio_infos = []
                for i, act in enumerate(actions):
                    self.signals.status_update.emit(f"{p_label}: シーン {i+1} の音声を合成中...")
                    # 修正：synthesize_speech_and_get_timestamps の呼び出し
                    path, dur, ts = synthesize_speech_and_get_timestamps(
                        act["narration"], 
                        os.path.join(temp_dir, f"audio_{part_idx}_{i}")
                    )
                    audio_infos.append((path, dur, ts))

                # MoviePyによる動画レンダリング
                self.signals.status_update.emit(f"{p_label}: 映像クリップを生成中...")
                clip = generate_video(part["image_path"], actions, audio_infos, img_info, self.opened_clips)
                if clip:
                    all_parts_clips.append(clip)

            if not all_parts_clips:
                self.signals.error.emit("有効な映像クリップが生成されませんでした。")
                return

            # 全クリップを結合して書き出し
            self.signals.status_update.emit("最終動画を結合して出力中...")
            final_video = concatenate_videoclips(all_parts_clips, method="compose")
            
            # 出力ファイルパスの生成（最初の画像名に基づく）
            output_name = os.path.splitext(os.path.basename(self.input_parts[0]["image_path"]))[0]
            out_path = os.path.normpath(os.path.join(os.path.dirname(self.input_parts[0]["image_path"]), f"{output_name}_story.mp4"))
            
            final_video.write_videofile(
                out_path, 
                fps=OUTPUT_FPS, 
                codec="libx264", 
                audio_codec="aac",
                logger="bar"
            )
            
            self.signals.finished.emit(f"ビデオの生成が完了しました！\n保存場所: {out_path}")

        except Exception as e:
            import traceback
            err_detail = traceback.format_exc()
            logger.error(f"パイプラインエラー: {err_detail}")
            self.signals.error.emit(f"システムエラーが発生しました:\n{str(e)}")
        finally:
            # MoviePyのハンドルなどを確実に解放
            self.signals.status_update.emit("リソースを解放しています...")
            for c in self.opened_clips:
                try:
                    c.close()
                except:
                    pass
            # 一時ディレクトリのクリーンアップ
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)