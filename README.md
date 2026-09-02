 # 🎮 Realtime Voice Translator

按住热键，whisper自动识别 → 翻译成英语 → edge-tts合成 → 输出到虚拟麦克风。适用于与陌生外国队友语音。

## ✨ 功能

- 🎤 **Push-to-Talk**：按住 `F1` 录音，松开后自动处理
- 🧠 **本地 ASR**：基于 Whisper.cpp + Vulkan，AMD/NVIDIA/Intel 均可 GPU 加速
- 🌐 **可插拔翻译**：腾讯翻译 API（高质量）/ 本地离线翻译（零延迟）
- 🔊 **双路输出**：耳机监听 + 虚拟麦克风输入游戏

## 📋 环境要求

- Windows 10/11
- Python 3.10+
- [VB-Cable Virtual Audio Device](https://vb-audio.com/Cable/)
- [Vulkan SDK](https://vulkan.lunarg.com/sdk/home)（AMD GPU 加速必需）

## 🚀 快速开始

### 1. 克隆仓库
```bash
git clone https://github.com/Graphite-Git/realtime-voice-translator.git
cd realtime-voice-translator
```

### 2. 安装依赖
```bash
pip install -r requirements.txt
```

### 3. 配置
```bash
cp config.example.py config.py
# 编辑 config.py，填写你的麦克风和腾讯 API 密钥（可选）
```

### 4. 下载 Whisper 模型
下载 `ggml-large-v3-turbo.bin` 放到 `models/` 目录：
```
https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin
```

### 5. 运行
```bash
python main.py
```

按住 `F1` 说话，松开后等待翻译语音输出。

## ⚙️ 配置说明

| 配置项 | 说明 |
|--------|------|
| `MIC_KEYWORD` | 麦克风匹配关键词，`None` 为启动时手动选择 |
| `TRANSLATE_PROVIDER` | `"tencent"` 使用腾讯 API，`"local"` 使用本地离线翻译 |
| `TENCENT_SECRET_ID/KEY` | 腾讯翻译 API 密钥（仅在 provider=tencent 时需要） |

## 📝 关于本地翻译模型

本地离线翻译使用 argostranslate，首次运行时会自动下载约 50MB 模型到系统目录：

- **Windows:** `C:\Users\<你的用户名>\.local\share\argos-translate\`

请确保首次使用本地翻译时有网络连接（可能需要代理）。下载完成后即可永久离线使用。如果自动下载失败，可手动将模型文件放置到上述路径。

## 📄 License

[GPL-3.0](LICENSE)

## 📝 关于本项目

这是我的第一个开源项目，使用vibe coding制作，因为不熟悉相关项目流程，可能出现未知问题。欢迎 Star 和反馈。