import os
import json
import logging
import re
import base64
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

AZURE_VISION_ENDPOINT = os.environ.get("AZURE_VISION_ENDPOINT")
AZURE_VISION_KEY = os.environ.get("AZURE_VISION_KEY")
AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_KEY = os.environ.get("AZURE_OPENAI_KEY")
AZURE_OPENAI_DEPLOYMENT_NAME = os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME")
AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY")
AZURE_SPEECH_REGION = os.environ.get("AZURE_SPEECH_REGION")
AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01")

whisper_model = None

def load_whisper_model():
    global whisper_model
    if whisper_model is None and whisper is not None:
        try:
            whisper_model = whisper.load_model("base")
            return True
        except Exception as e:
            logger.error(f"Whisperロード失敗: {e}")
            return False
    return whisper_model is not None

def encode_image_to_base64(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def analyze_image(image_path):
    try:
        with Image.open(image_path) as img:
            w, h = img.size
        return {'width': w, 'height': h}
    except: return None

def generate_narration_and_actions(image_path, cube_data_list, is_final_photo=False):
    client = AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_KEY,
        api_version=AZURE_OPENAI_API_VERSION
    )

    base64_image = encode_image_to_base64(image_path)

    if is_final_photo:
        context = """# 最終シーンの指示: 感動的な余韻を残して締めくくってください。"""
    else:
        context = """# 次の画像への移行ルール: 最後にナレーションなしの全景（x:0,y:0,w:1,h:1）を2秒追加してください。そのシーンのnarrationは空文字にします。"""

    materials = [f"素材{i}: [面={it.get('face')}] 語り='{it.get('transcript')}' 座標={it.get('location')}" for i, it in enumerate(cube_data_list)]

    prompt = f"""
    あなたは熟練のドキュメンタリー作家です。写真と文字起こし（transcript）を照合してください。
    文字起こしの誤字を写真の内容から推測して補正し、感動的なナレーションを新たに作成してください。
    一人称は使わず、敬語の三人称で構成します。全体約60秒、5-7シーンで構成。
    {chr(10).join(materials)}
    {context}
    出力はJSON配列のみ。
    """

    try:
        response = client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT_NAME,
            messages=[
                {"role": "system", "content": "あなたは優秀な映像ディレクターです。JSONのみ返します。"},
                {"role": "user", "content": [{"type":"text", "text":prompt}, {"type":"image_url", "image_url":{"url":f"data:image/jpeg;base64,{base64_image}"}}]}
            ]
        )
        content = re.sub(r'```json\s*|\s*```', '', response.choices[0].message.content).strip()
        return json.loads(content)
    except: return None

def synthesize_speech_and_get_timestamps(text, output_base):
    output_file = f"{output_base}.wav"
    try:
        sc = speechsdk.SpeechConfig(subscription=AZURE_SPEECH_KEY, region=AZURE_SPEECH_REGION)
        sc.speech_synthesis_voice_name = 'ja-JP-NanamiNeural'
        ac = speechsdk.audio.AudioOutputConfig(filename=output_file)
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=sc, audio_config=ac)

        res = synthesizer.speak_text_async(text).get()
        dur = 0.0
        if res.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            with AudioFileClip(output_file) as clip: dur = clip.duration
        
        load_whisper_model()
        sentence_timestamps = []
        if whisper_model:
            w_res = whisper_model.transcribe(output_file, language="ja")
            segments = w_res.get("segments", [])
            clean_text = text.strip()
            total_whisper_chars = sum(len(s.get("text", "").strip()) for s in segments)
            
            if total_whisper_chars > 0:
                current_ptr = 0
                for i, seg in enumerate(segments):
                    seg_whisper_text = seg.get("text", "").strip()
                    if not seg_whisper_text: continue
                    ratio = len(seg_whisper_text) / total_whisper_chars
                    slice_len = int(len(clean_text) * ratio)
                    if i == len(segments) - 1:
                        sub_text = clean_text[current_ptr:]
                    else:
                        sub_text = clean_text[current_ptr : current_ptr + slice_len]
                    current_ptr += slice_len
                    if sub_text:
                        sentence_timestamps.append({
                            'text': sub_text,
                            'offset_seconds': seg['start'],
                            'duration_seconds': seg['end'] - seg['start']
                        })
            else:
                sentence_timestamps.append({'text': clean_text, 'offset_seconds': 0.0, 'duration_seconds': dur})

        return output_file, dur, sentence_timestamps
    except Exception as e:
        logger.error(f"音声合成エラー: {e}")
        return None, 0.0, []