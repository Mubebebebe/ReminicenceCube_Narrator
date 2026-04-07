import os
import json
import logging
import re
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

# 環境変数の取得
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
            whisper_model = whisper.load_model("base") # 速度優先
            return True
        except: return False
    return whisper_model is not None

def analyze_image(image_path):
    try:
        with Image.open(image_path) as img:
            w, h = img.size
        return {'width': w, 'height': h}
    except: return None

def generate_narration_and_actions(image_analysis, cube_data_list):
    client = AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_KEY,
        api_version=AZURE_OPENAI_API_VERSION
    )

    # 渡されたキューブ各面の情報をテキスト化
    materials = []
    for i, item in enumerate(cube_data_list):
        face = item.get("face", "不明")
        text = item.get("transcript", "")
        loc = item.get("location", {"x":0, "y":0, "w":1, "h":1})
        materials.append(f"素材{i}: [面={face}] 語り='{text}' 座標={loc}")

    prompt = f"""
    あなたは感動的なドキュメンタリーを作る映像ディレクターです。
    提供された「回想法キューブ」の語り素材を自由に並び替え、一枚の写真から物語を再構成してください。

    # 素材リスト (start/endは無視して文脈で再構成してください)
    {chr(10).join(materials)}

    # 指示
    1. 語り(transcript)の内容を深く読み解き、その裏にある感情を汲み取った「ナレーション」を新たに作成してください。
    2. ナレーションは穏やかな敬語（三人称）とし、写真の持ち主を尊重した表現にしてください。
    3. 各シーンには、対応する素材の座標(location)を割り当てて、ズーム効果を指定してください。
    4. 全体で約60秒、シーン数は5〜7つに調整してください。最初のシーンは必ず写真全体(Front)から始めてください。

    # 出力形式 (JSON配列のみ)
    [
      {{
        "narration": "...",
        "visual_action": {{ "duration": 8, "location": {{"x":0, "y":0, "w":1, "h":1}} }}
      }},
      ...
    ]
    """

    response = client.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT_NAME,
        messages=[
            {"role": "system", "content": "あなたは映像編集のプロです。JSON配列のみを出力します。"},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7
    )

    try:
        content = response.choices[0].message.content
        content = re.sub(r'```json\s*|\s*```', '', content).strip()
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
            with AudioFileClip(output_file) as clip:
                dur = clip.duration
        
        # 字幕タイミング (Whisper)
        load_whisper_model()
        ts = []
        if whisper_model:
            w_res = whisper_model.transcribe(output_file, language="ja")
            for seg in w_res.get("segments", []):
                ts.append({'text': seg['text'], 'offset_seconds': seg['start'], 'duration_seconds': seg['end']-seg['start']})
        
        return output_file, dur, ts
    except: return None, 0.0, []