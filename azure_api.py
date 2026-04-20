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

# Azure設定の読み込み
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
    """
    写真と回想ログに基づき、AIによる構成案(JSON)を生成する
    """
    client = AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT, 
        api_key=AZURE_OPENAI_KEY, 
        api_version=AZURE_OPENAI_API_VERSION
    )
    base64_image = encode_image_to_base64(image_path)

    if is_final_photo:
        context = "# 最終シーン指示: 物語を締めくくる、感動的で余韻の残る語りで終えてください。"
    else:
        context = "# 次の画像へのルール: 最後に必ずナレーションのない全景ショットを2秒追加してください。"

    materials = [f"素材{i}: 内容='{it.get('transcript')}' 座標={it.get('location')}" for i, it in enumerate(cube_data_list)]

    prompt = f"""あなたは、プロのドキュメンタリー映像作家です。
提供された「写真」と、それに関する「回想録（素材リスト）」に基づき、1枚の写真から感動的な物語を再構成してください。

# 背景
ユーザーはVR空間で写真が貼られたキューブを回転させながら回想しています。そのため、注視ログ（座標）と発話内容に数秒のタイムラグや不一致が生じることがあります。

# 目標
* 写真1枚に対して、合計で約60秒の構成を作成してください。
* ナレーションの総文字数は250〜300文字程度が目安です。
* 5〜7つのシーンに分け、各シーンのduration（秒）を調整してください。

# 演出ルール（重要）
1. **文脈優先のズーム（Semantic Zoom）**: 
   - ナレーションで特定の人物や場所に言及している場合、素材リストの当該シーンの座標が(0,0,0,0)であっても、**リスト内の他のシーンから該当する座標を探し出して採用**してください。
   - もしリスト全体に適切な座標がない場合は、提供された画像の内容を視覚的に解析し、言及されている対象（例：「右下の私」「白い服の友人」）に合わせた最適な座標を自ら推論して設定してください。
2. **カメラワークのメリハリ**:
   - ずっと全体を表示するのではなく、特定のディテール（座標へのズーム）と、全体像（x:0, y:0, w:1, h:1）を交互に織り交ぜて視覚的なリズムを作ってください。
   - 物語の最初と最後は必ず全体像（x:0, y:0, w:1, h:1）にしてください。
3. **登場人物の呼び方**:
   - 特定の苗字を勝手に作り出さないでください。
   - 写真に写っている人物を指す際は、文脈に合わせて「彼」「彼女」「こちらの方」といった三人称代名詞を使用してください。

# 出力形式
必ず以下の構造を持つJSON配列のみを出力してください。余計な解説は不要です。
[
  {{
    "narration": "ナレーションのテキスト",
    "visual_action": {{
      "duration": 秒数(数値),
      "location": {{ "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0 }}
    }}
  }}
]

# 素材リスト（注視ログと発話内容）
{chr(10).join(materials)}

{context}
"""

    try:
        response = client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT_NAME,
            messages=[
                {"role": "system", "content": "あなたは最高のドキュメンタリー作家です。指定されたJSON配列フォーマットのみを厳格に守って出力します。"},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt}, 
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                ]}
            ]
        )
        # 不要なマークダウン記法を削除
        content = re.sub(r'```json\s*|\s*```', '', response.choices[0].message.content).strip()
        return json.loads(content)
    except Exception as e:
        logger.error(f"AI構成生成エラー: {e}")
        return None

def synthesize_speech_and_get_timestamps(text, output_filename_base):
    """
    Azure Speech SDKを使用して音声合成を行い、簡易的な文単位のタイムスタンプを返す
    """
    tts_output_filename = f"{output_filename_base}.wav"
    try:
        speech_config = speechsdk.SpeechConfig(subscription=AZURE_SPEECH_KEY, region=AZURE_SPEECH_REGION)
        speech_config.speech_synthesis_voice_name = 'ja-JP-NanamiNeural'
        audio_config = speechsdk.audio.AudioOutputConfig(filename=tts_output_filename)
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
        
        # 文の間に少し溜めを作る
        ssml = f"<speak version='1.0' xml:lang='ja-JP'><voice name='{speech_config.speech_synthesis_voice_name}'><break time='300ms'/>{escape(text)}</voice></speak>"
        result = synthesizer.speak_ssml_async(ssml).get()
        
        actual_dur = 0.0
        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            if AudioFileClip:
                with AudioFileClip(tts_output_filename) as clip: 
                    actual_dur = clip.duration

        # 簡易的なタイムスタンプ分割（。！？で区切る）
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