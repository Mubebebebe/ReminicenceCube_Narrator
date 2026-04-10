import os
import sys
import json
import traceback
import logging
import re
import Levenshtein
from dotenv import load_dotenv
from html import escape
from PIL import Image

from azure.ai.vision.imageanalysis import ImageAnalysisClient
from azure.ai.vision.imageanalysis.models import VisualFeatures
from azure.core.credentials import AzureKeyCredential
import azure.cognitiveservices.speech as speechsdk
from openai import AzureOpenAI

try:
    from moviepy.audio.io.AudioFileClip import AudioFileClip
except ImportError:
    AudioFileClip = None

try:
    import whisper
except ImportError:
    whisper = None

logger = logging.getLogger(__name__)

load_dotenv()
AZURE_VISION_ENDPOINT = os.environ["AZURE_VISION_ENDPOINT"]
AZURE_VISION_KEY = os.environ["AZURE_VISION_KEY"]
AZURE_OPENAI_ENDPOINT = os.environ["AZURE_OPENAI_ENDPOINT"]
AZURE_OPENAI_KEY = os.environ["AZURE_OPENAI_KEY"]
AZURE_OPENAI_DEPLOYMENT_NAME = os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"]
AZURE_SPEECH_KEY = os.environ["AZURE_SPEECH_KEY"]
AZURE_SPEECH_REGION = os.environ["AZURE_SPEECH_REGION"]
AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01")

whisper_model = None
WHISPER_MODEL_NAME = "large-v3"

def load_whisper_model():
    global whisper_model
    if whisper is None: return False
    if whisper_model is None:
        try:
            whisper_model = whisper.load_model(WHISPER_MODEL_NAME)
            logger.info("Whisperモデルのロード完了")
            return True
        except Exception as e:
            logger.error(f"Whisperモデルのロードに失敗: {e}")
            return False
    return True

def analyze_image(image_path):
    try:
        img_width, img_height = 0, 0
        with Image.open(image_path) as pil_img:
            img_width, img_height = pil_img.size
        client = ImageAnalysisClient(endpoint=AZURE_VISION_ENDPOINT, credential=AzureKeyCredential(AZURE_VISION_KEY))
        with open(image_path, "rb") as f:
            image_data = f.read()
        analysis_result = client.analyze(
            image_data=image_data,
            visual_features=[VisualFeatures.OBJECTS, VisualFeatures.TAGS, VisualFeatures.CAPTION]
        )
        result = {'width': img_width, 'height': img_height}
        if analysis_result.caption:
            result['caption'] = {'text': analysis_result.caption.text}
        return result
    except Exception as e:
        logger.error(f"Azure 画像解析エラー: {e}")
        return None

def generate_narration_and_actions(image_analysis, cube_data_list, is_final_photo=False):
    client = AzureOpenAI(azure_endpoint=AZURE_OPENAI_ENDPOINT, api_key=AZURE_OPENAI_KEY, api_version=AZURE_OPENAI_API_VERSION)
    
    contextual_instruction = ""
    if is_final_photo:
        contextual_instruction = "# 最終シーン指示: 感動的な余韻を残して締めてください。"
    else:
        contextual_instruction = "# 次の画像へのルール: 最後にナレーションなしの全景（show_all）を2秒追加してください。"

    materials = [f"- 素材{i}: [面: {it.get('face')}] 内容: {it.get('transcript')} 座標: {it.get('location')}" for i, it in enumerate(cube_data_list)]

    prompt = f"""あなたは映像作家です。写真と回想法の記録から、感動的な物語を再構成してください。
    【重要】文字起こしの誤字は写真の内容から推測して補正してください。一人称は使わず敬語で構成します。
    {chr(10).join(materials)}
    {contextual_instruction}
    出力はJSON配列のみ。1シーンのナレーションは1〜2文程度にしてください。"""

    response = client.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT_NAME,
        messages=[{"role": "system", "content": "あなたは優秀なディレクターです。JSONのみ返します。"}, {"role": "user", "content": prompt}]
    )
    content = re.sub(r'```json\s*|\s*```', '', response.choices[0].message.content).strip()
    return json.loads(content)

def synthesize_speech_and_get_timestamps(text, output_filename_base):
    """一文ごとに字幕を表示するようにタイムスタンプを生成する"""
    tts_output_filename = f"{output_filename_base}.wav"
    try:
        speech_config = speechsdk.SpeechConfig(subscription=AZURE_SPEECH_KEY, region=AZURE_SPEECH_REGION)
        speech_config.speech_synthesis_voice_name = 'ja-JP-NanamiNeural'
        audio_config = speechsdk.audio.AudioOutputConfig(filename=tts_output_filename)
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)

        # 冒頭の無音を挿入
        ssml_text = f"""<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='ja-JP'>
                            <voice name='{speech_config.speech_synthesis_voice_name}'>
                                <break time='300ms'/>{escape(text)}
                            </voice></speak>"""
        result = synthesizer.speak_ssml_async(ssml_text).get()

        actual_duration = 0.0
        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            with AudioFileClip(tts_output_filename) as clip:
                actual_duration = clip.duration

        # --- 字幕を一文ごとに分割して時間を按分するロジック ---
        # 句読点で分割
        sentences = [s.strip() for s in re.split(r'(?<=[。！？])\s*', text) if s.strip()]
        sentence_timestamps = []
        
        if sentences:
            total_chars = sum(len(s) for s in sentences)
            current_offset = 0.0
            for s in sentences:
                # 文字数比率で表示時間を算出
                duration = (len(s) / total_chars) * actual_duration
                sentence_timestamps.append({
                    'text': s,
                    'offset_seconds': current_offset,
                    'duration_seconds': duration
                })
                current_offset += duration
            
            # 最初の字幕は動画開始と同時に表示されるよう補正
            if sentence_timestamps:
                sentence_timestamps[0]['duration_seconds'] += sentence_timestamps[0]['offset_seconds']
                sentence_timestamps[0]['offset_seconds'] = 0.0

        return tts_output_filename, actual_duration, sentence_timestamps
    except Exception as e:
        logger.error(f"音声合成エラー: {e}")
        return None, 0.0, None