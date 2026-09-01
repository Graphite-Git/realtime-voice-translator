import os
import threading
from pynput import keyboard
import sounddevice as sd
import soundfile as sf
import edge_tts
import asyncio
import tempfile
import numpy as np
import subprocess


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ========配置区========
from config import (
    MIC_KEYWORD, TRANSLATE_PROVIDER,
    TENCENT_SECRET_ID, TENCENT_SECRET_KEY, TENCENT_REGION,
    CABLE_KEYWORD, HEADPHONE_KEYWORD
)

WHISPER_CLI = os.path.join(BASE_DIR, "bin", "whisper-cli.exe")
MODEL = os.path.join(BASE_DIR, "models", "ggml-large-v3-turbo.bin")


# ========== 翻译器抽象层 ==========
class TranslatorBase:
    def translate(self, text: str) -> str:
        raise NotImplementedError


class TencentTranslator(TranslatorBase):
    def __init__(self, secret_id: str, secret_key: str, region: str = "ap-guangzhou"):
        from tencentcloud.common import credential
        from tencentcloud.common.common_client import CommonClient
        self.client = CommonClient("tmt", "2018-03-21",
                                   credential.Credential(secret_id, secret_key), region)

    def translate(self, text: str) -> str:
        resp = self.client.call_json("TextTranslate", {
            "SourceText": text, "Source": "zh", "Target": "en", "ProjectId": 0
        })
        return resp["Response"]["TargetText"]


class LocalTranslator(TranslatorBase):
    _ready = False

    def __init__(self):
        import argostranslate.package
        import argostranslate.translate
        self._pkg = argostranslate.package
        self._trans = argostranslate.translate

    def _ensure(self):
        if LocalTranslator._ready:
            return
        print("⬇️ 首次运行：检查本地翻译模型...")
        installed = self._pkg.get_installed_packages()
        if any(p.from_code == "zh" and p.to_code == "en" for p in installed):
            print("✅ 本地模型已就绪")
            LocalTranslator._ready = True
            return
        print("⬇️ 首次运行：下载本地翻译模型...")
        self._pkg.update_package_index()
        pkg = next((p for p in self._pkg.get_available_packages()
                    if p.from_code == "zh" and p.to_code == "en"), None)
        if pkg:
            self._pkg.install_from_path(pkg.download())
            print("✅ 本地模型就绪")
            LocalTranslator._ready = True
        else:
            raise RuntimeError("未找到 zh→en 模型")

    def translate(self, text: str) -> str:
        self._ensure()
        return self._trans.translate(text, "zh", "en")


# 全局单例，避免每次翻译都新建客户端
_translator_instance = None


def get_translator():
    global _translator_instance
    if _translator_instance is None:
        _translator_instance = _create_translator()
    return _translator_instance


def _create_translator():
    if TRANSLATE_PROVIDER == "tencent":
        if not TENCENT_SECRET_ID or not TENCENT_SECRET_KEY:
            raise ValueError("provider=tencent 时必须填写密钥")
        return TencentTranslator(TENCENT_SECRET_ID, TENCENT_SECRET_KEY, TENCENT_REGION)
    elif TRANSLATE_PROVIDER == "local":
        return LocalTranslator()
    else:
        raise ValueError(f"不支持的翻译提供商: {TRANSLATE_PROVIDER}")


# ========== 设备探测 ==========
_translate_ready = False



def find_input_device(keyword=None):
    devices = sd.query_devices()
    input_devices = []
    print("\n" + "=" * 50)
    print("【可用输入设备 - 麦克风】")
    for i, dev in enumerate(devices):
        if dev['max_input_channels'] > 0:
            name = dev['name']
            skip = any(x in name for x in [
                'CABLE Output', '主声音捕获', 'Stereo Mix', '立体声混音',
                'Steam Streaming', 'VB-Audio Point'
            ])
            if not skip:
                input_devices.append((i, name))
                marker = "⭐" if (keyword and keyword.lower() in name.lower()) else "  "
                print(f"{marker} [{i}] {name}")
    if not input_devices:
        raise RuntimeError("❌ 未找到任何可用麦克风")
    if keyword:
        for idx, name in input_devices:
            if keyword.lower() in name.lower():
                print(f"\n✅ 关键词 '{keyword}' 匹配到: [{idx}] {name}")
                return idx
        print(f"\n⚠️ 未找到包含 '{keyword}' 的麦克风，进入手动选择")
    print("=" * 50)
    while True:
        try:
            choice = input("\n请输入麦克风索引编号: ").strip()
            idx = int(choice)
            for d_idx, d_name in input_devices:
                if d_idx == idx:
                    print(f"✅ 已选择: [{idx}] {d_name}")
                    return idx
            print("❌ 无效的索引")
        except ValueError:
            print("❌ 请输入数字")


def find_output_devices():
    devices = sd.query_devices()
    cable_idx = headphone_idx = None
    for i, dev in enumerate(devices):
        name = dev['name']
        if dev['max_output_channels'] > 0:
            if (CABLE_KEYWORD in name and 'VB-Audio' in name
                    and '16ch' not in name and cable_idx is None):
                cable_idx = i
                print(f"🔌 检测到虚拟线: [{i}] {name}")
            elif (HEADPHONE_KEYWORD in name
                  and 'CABLE' not in name and 'Virtual' not in name
                  and 'Steam' not in name and 'Hands-Free' not in name
                  and headphone_idx is None):
                headphone_idx = i
                print(f"🎧 检测到耳机: [{i}] {name}")
    return cable_idx, headphone_idx


print("🔍 正在探测音频设备...")
MIC_INDEX = find_input_device(MIC_KEYWORD)
CABLE_INDEX, HEADPHONE_INDEX = find_output_devices()
if CABLE_INDEX is None:
    raise RuntimeError("❌ 未找到 CABLE Input")
print(f"\n📋 最终配置: 麦克风=[{MIC_INDEX}], 虚拟线=[{CABLE_INDEX}], 耳机=[{HEADPHONE_INDEX}]")

SAMPLE_RATE = 16000
CHANNELS = 1


# ==============================

# ========== 热键 + 录音状态 ==========
audio_buffer = []
recording_event = threading.Event()


def audio_callback(indata, frames, time_info, status):
    if recording_event.is_set():
        audio_buffer.append(indata.copy())


async def pipeline(audio_np):
    """完整的 ASR → 翻译 → TTS → 双输出"""
    temp_dir = tempfile.gettempdir()
    duration = len(audio_np) / SAMPLE_RATE
    print(f"📊 音频长度: {duration:.2f}秒, 采样数: {len(audio_np)}")

    # 1. 保存 WAV
    input_wav = os.path.join(temp_dir, "pipeline_input.wav")
    sf.write(input_wav, audio_np, SAMPLE_RATE)

    # 2. ASR
    print("🧠 语音识别中...")
    output_prefix = os.path.join(temp_dir, "whisper_out")
    for ext in [".txt", ".json", ".srt", ".vtt"]:
        f = output_prefix + ext
        if os.path.exists(f):
            os.remove(f)
    cmd = [
        WHISPER_CLI, "-m", MODEL, "-f", input_wav,
        "-l", "zh", "--no-timestamps", "-otxt", "-of", output_prefix,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding='utf-8', errors='ignore')
    txt_file = output_prefix + ".txt"
    if not os.path.exists(txt_file):
        print("❌ 识别失败")
        return
    with open(txt_file, "r", encoding="utf-8") as f:
        chinese_text = f.read().strip()
    print(f"📝 识别结果: {chinese_text}")

    # 3. 翻译
    print("🌐 翻译中...")
    english_text = None
    try:
        english_text = get_translator().translate(chinese_text)
        print(f"📝 翻译结果: {english_text}")
    except Exception as e:
        print(f"⚠️ {TRANSLATE_PROVIDER} 翻译失败: {e}")
        # fallback 到本地
        try:
            print("   尝试本地翻译 fallback...")
            english_text = LocalTranslator().translate(chinese_text)
            print(f"📝 本地翻译: {english_text}")
        except Exception as e2:
            print(f"⚠️ 本地翻译也失败: {e2}")
            print("   将使用中文原文并切换为中文语音输出")

    # 4. TTS
    if english_text:
        tts_text, tts_voice = english_text, "en-US-GuyNeural"
    else:
        tts_text, tts_voice = chinese_text, "zh-CN-XiaoxiaoNeural"
    print(f"🔊 语音合成中... (voice: {tts_voice})")
    temp_mp3 = os.path.join(temp_dir, "pipeline_output.mp3")
    try:
        tts = edge_tts.Communicate(tts_text, voice=tts_voice)
        await tts.save(temp_mp3)
    except Exception as e:
        print(f"❌ TTS 合成失败: {e}")
        return

    # 5. 双输出
    print("📢 同时输出到耳机和虚拟麦克风...")
    try:
        data, sr = sf.read(temp_mp3, dtype='float32')
    except Exception as e:
        print(f"❌ 音频文件读取失败: {e}")
        return
    if data.ndim == 1:
        data = np.column_stack((data, data))
    elif data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)

    def play_to_device(audio_data, sample_rate, device_idx, label):
        try:
            with sd.OutputStream(samplerate=sample_rate, channels=2,
                                 device=device_idx, dtype='float32') as stream:
                stream.write(audio_data)
            print(f"   ✅ {label} 播放完成")
        except Exception as e:
            print(f"   ❌ {label} 播放失败: {e}")

    threads = []
    if HEADPHONE_INDEX is not None:
        t1 = threading.Thread(target=play_to_device,
                              args=(data, sr, HEADPHONE_INDEX, "耳机"))
        threads.append(t1)
        t1.start()
    t2 = threading.Thread(target=play_to_device,
                          args=(data, sr, CABLE_INDEX, "虚拟麦克风"))
    threads.append(t2)
    t2.start()
    for t in threads:
        t.join()
    print("✅ 全部完成！\n")


def run_pipeline_in_thread(audio_np):
    """在新线程中跑异步 pipeline，不阻塞热键监听"""
    asyncio.run(pipeline(audio_np))


# ========== 热键回调 ==========
def on_press(key):
    global audio_buffer
    if key == keyboard.Key.f1 and not recording_event.is_set():
        audio_buffer = []
        recording_event.set()
        stream.start()
        print("🔴 开始录音...")
    elif key == keyboard.Key.esc:
        print("👋 退出")
        return False


def on_release(key):
    if key == keyboard.Key.f1 and recording_event.is_set():
        recording_event.clear()
        stream.stop()
        print("🛑 结束录音")
        if len(audio_buffer) == 0:
            print("⚠️ 没有录到声音\n")
            return
        audio_np = np.concatenate(audio_buffer, axis=0)
        # 新线程跑 pipeline，热键监听不被阻塞
        t = threading.Thread(target=run_pipeline_in_thread, args=(audio_np,))
        t.start()


# ========== 主程序 ==========
stream = sd.InputStream(
    samplerate=SAMPLE_RATE,
    channels=CHANNELS,
    dtype='float32',
    device=MIC_INDEX,
    callback=audio_callback
)

print("\n" + "=" * 50)
print("🎮 实时语音翻译器已启动")
print("   按住 F1 说话，松开后自动翻译并输出")
print("   按 Esc 退出")
print("=" * 50 + "\n")

try:
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()
except KeyboardInterrupt:
    print("\n👋 程序已退出")

print("程序已退出")