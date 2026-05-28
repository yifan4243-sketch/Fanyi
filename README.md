# 外贸屏幕实时翻译助手 V1

**全屏检测 + 浮动翻译弹窗**

打开微信电脑版 → 启动本工具 → 翻译弹窗自动出现在外语消息旁边。

**不会自动发送消息，只显示翻译，避免误发。**

---

## 1. 安装依赖

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

---

## 2. 配置 API Key

首次运行后点「设置」按钮填入，或手动复制 `config.example.json` 为 `config.json` 并修改 `api_key`。

默认兼容 DeepSeek，也支持任何 OpenAI-compatible API。

---

## 3. 运行

```bash
python main.py
```

---

## 4. 使用步骤

1. 打开微信电脑版，进入外贸客户聊天窗口
2. 点 **设置** → 填入 API Key
3. 点 **开始全屏翻译**
4. 微信里有新外语消息时，翻译弹窗会自动出现在原文旁边
5. 在底部输入框写中文回复 → 点 **翻译回复** → 点 **复制回复**
6. 粘贴到微信发送

---

## 5. 文件结构

```
wechat_screen_translator_v1/
├── main.py                  # 入口
├── ui_main.py               # 主界面（全屏监听 + 浮动弹窗调度）
├── floating_window.py       # 浮动翻译弹窗
├── ocr_service.py           # EasyOCR 封装（含位置信息）
├── translator_service.py    # AI 翻译 API
├── config_service.py        # 配置文件读写
├── database.py              # SQLite 历史记录
├── requirements.txt
├── config.example.json
└── README.md
```

---

## 6. 打包 EXE

```bash
pip install pyinstaller

pyinstaller -F -w main.py \
    --name "外贸屏幕实时翻译助手V1" \
    --add-data "config.example.json;." \
    --hidden-import=easyocr \
    --hidden-import=PIL \
    --hidden-import=mss
```

EXE 在 `dist/` 目录下。

---

## 7. 常见问题

### OCR 未识别到文字
- 检查屏幕上的文字是否清晰可见
- 微信窗口不要最小化
- 确认 OCR 引擎初始化成功（看日志）

### 翻译弹窗位置不准
- 弹窗会出现在 OCR 检测到的文字区域右下角
- 如果偏差太大，调整 `config.json` 中 `ocr_scale_factor`（默认 2.0）

### API 调用失败
- 检查网络
- 检查 API Key 是否正确
- 看日志里的具体错误
