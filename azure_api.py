import os
import sys
import json
import traceback
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
    if whisper_model is None:
        try:
            whisper_model = whisper.load_model(WHISPER_MODEL_NAME)
            return True
        except: return False
    return True

def encode_image_to_base64(image_path):
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode('utf-8')

def analyze_image(image_path):
    try:
        with Image.open(image_path) as img:
            w, h = img.size
        return {'width': w, 'height': h}
    except: return None

def generate_narration_and_actions(image_path, cube_data_list, is_final_photo=False):
    client = AzureOpenAI(azure_endpoint=AZURE_OPENAI_ENDPOINT, api_key=AZURE_OPENAI_KEY, api_version=AZURE_OPENAI_API_VERSION)
    base64_image = encode_image_to_base64(image_path)

    if is_final_photo:
        context = "# 最終シーン指示: 物語を締めくくる、感動的で余韻の残る語りで終えてください。"
    else:
        context = "# 次の画像へのルール: 最後に必ずナレーションのない全景ショットを2秒追加してください（narration: \"\", location: {x:0,y:0,w:1,h:1}, duration: 2）。"

    materials = [f"素材{i}: [面={it.get('face')}] 内容='{it.get('transcript')}' 座標={it.get('location')}" for i, it in enumerate(cube_data_list)]

    prompt = f"""あなたは、ドキュメンタリー映像作家です。
    提供された「写真」と「回想録」に基づき、一枚の写真から感動的な物語を再構成してください。

    # 目標時間: 約60秒
    * この写真1枚に対して、合計で約60秒の動画を作成してください。
    * ナレーションの総文字数は250〜300文字程度が目安です。
    * 5〜7つのシーンに分け、各シーンのdurationを調整して合計が60秒（トランジション含む）になるようにしてください。

    # 登場人物の呼び方に関する厳格なルール
    * 特定の苗字を勝手に作り出さないでください。
    * 写真に写っている人物や持ち主を指す際は、画像の内容に合わせて「彼」「彼女」「こちらの方」「皆様」といった代名詞を適切に使用してください。
    * 親しみやすさと敬意を込めた、穏やかな敬語（三人称）のナレーションを作成してください。

    # ナレーションとズームのルール
    * **ズームの適正化**: ナレーションの内容が、素材の特定の座標（表情や特定のオブジェクトなど）に具体的に触れている時のみ、その素材の座標へズームしてください。
    * **迷ったら全体表示**: 感情的な語りや抽象的な思い出を語る際は、無理にズームせず、全体（x:0,y:0,w:1,h:1）を映し続けてください。
    * 物語の最初は必ず全体像(Front)から始めてください。

    # 素材リスト
    {chr(10).join(materials)}
    {context}

    出力はJSON配列形式のみ。
    """

    try:
        response = client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT_NAME,
            messages=[
                {"role": "system", "content": "あなたは最高のドキュメンタリー作家です。JSON配列のみを返します。"},
                {"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}]}
            ]
        )
        content = re.sub(r'```json\s*|\s*```', '', response.choices[0].message.content).strip()
        return json.loads(content)
    except Exception as e:
        logger.error(f"AI構成生成エラー: {e}")
        return None

def synthesize_speech_and_get_timestamps(text, output_filename_base):
    tts_output_filename = f"{output_filename_base}.wav"
    try:
        speech_config = speechsdk.SpeechConfig(subscription=AZURE_SPEECH_KEY, region=AZURE_SPEECH_REGION)
        speech_config.speech_synthesis_voice_name = 'ja-JP-NanamiNeural'
        audio_config = speechsdk.audio.AudioOutputConfig(filename=tts_output_filename)
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
        
        ssml = f"<speak version='1.0' xml:lang='ja-JP'><voice name='{speech_config.speech_synthesis_voice_name}'><break time='300ms'/>{escape(text)}</voice></speak>"
        result = synthesizer.speak_ssml_async(ssml).get()
        
        actual_dur = 0.0
        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            with AudioFileClip(tts_output_filename) as clip: actual_dur = clip.duration

        sentence_timestamps = []
        sentences = [s.strip() for s in re.split(r'(?<=[。！？])\s*', text) if s.strip()]
        
        if sentences:
            total_chars = sum(len(s) for s in sentences)
            curr_off = 0.0
            for s in sentences:
                s_dur = (len(s) / total_chars) * actual_dur
                sentence_timestamps.append({
                    'text': s,
                    'offset_seconds': curr_off,
                    'duration_seconds': s_dur
                })
                curr_off += s_dur

        return tts_output_filename, actual_dur, sentence_timestamps
    except Exception as e:
        logger.error(f"Speech error: {e}")
        return None, 0.0, None