# 外贸屏幕实时翻译助手 V1

框选微信聊天区域 → OCR 识别外语 → AI 翻译成中文 → 中文回复翻译成外语 → 一键复制。

**只复制译文，不自动发送，避免误发。**

---

## 1. 安装依赖

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

---

## 2. 配置 API Key

首次启动程序后，点击「设置」按钮填入 API Key。

也可以手动操作：复制 `config.example.json` 为 `config.json`，修改 `api_key` 为你的真实 Key。

**config.json 包含真实 API Key，已加入 .gitignore，注意不要提交到公开仓库。**

---

## 3. 运行

```bash
python main.py
```

---

## 4. 使用步骤

1. 打开微信电脑版，进入外贸客户聊天窗口
2. 点击「框选区域」，用鼠标拖拽选择微信聊天消息区域
3. 点击「翻译当前区域」测试单次翻译
4. 点击「开始监听」，程序会每 1.5 秒自动检测新消息并翻译
5. 在底部输入框输入中文回复 → 点击「翻译回复」
6. 点击「复制回复」→ 粘贴到微信发送

---

## 5. 文件结构

```
wechat_screen_translator_v1/
├── main.py                  # 入口
├── ui_main.py               # 主界面
├── screen_selector.py       # 区域框选器
├── ocr_service.py           # OCR 识别 (EasyOCR)
├── translator_service.py    # AI 翻译 API
├── config_service.py        # 配置文件读写
├── database.py              # SQLite 历史记录
├── floating_window.py       # 浮动翻译弹窗（可选）
├── requirements.txt
├── config.example.json      # 配置文件示例
└── README.md
```

---

## 6. 打包 EXE

```bash
pip install pyinstaller
pyinstaller -F -w main.py --name "外贸屏幕实时翻译助手V1"
```

---

## 7. 常见问题

### OCR 识别不准确
- 框选区域时尽量只框选文字区域
- 调整 config.json 中 `ocr_scale_factor`（默认 2.0，可调到 3.0）

### API 调用失败
- 检查 API Key 是否正确
- 检查 base_url 和 chat_completions_path 配置
- 查看日志区的具体错误信息

### 安全提醒
- config.json 含 API Key，不要提交到 Git / GitHub
- 设置窗口中 API Key 为密码模式显示
