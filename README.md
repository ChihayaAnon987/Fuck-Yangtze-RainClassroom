# 长江雨课堂定时签到+听课答题
**🌟 雨课堂、荷花、黄河等应该就 HOST 和 API 不同吧，可以自己试试，改下 API 应该就行?**

## 方法1 Github Actions
### 🌟 说明
默认设置为 每周一至周五 7:00~22:00，每 5min 运行一次检查，若发现新的课程则写入 log.json；需要定制运行时间，可修改 cron 表达式，自行学习，注意 cron 使用 0 时区时间，应在东八区时间 -8。

**⚠️ Actions 会存在延迟，并不是准时每 5min 检查一次**

**⚠️ 当进入监听答题状态后，后面的任务会排队，等待监听结束再运行，监听状态可能会半路结束，似乎是什么网络原因，不过有后面排队任务，崩了也能被接替**

解决建议：

1. 通过 Github Actions API 搭配自动化任务，定几个重要的上课时间节点，发送网络请求运行 Action

2. 转到第二种食用方法 - 使用服务器部署自动任务，私有算力 100% 不会出错

**⚠️ 安装依赖较大，约需要 75 秒后正式开始运行**

**⚠️ 注意 若 Cookie 过期，Github 会发邮件提示运行失败**

### 🚀 开始配置
1. 按下面教程拿到 SESSIONID，或者自己抓 APP 的包

2. 按图中路径，配置名为 SESSION 的环境变量，值为 SESSIONID 的值
![图片1](src/img/Step_1.png)
![图片2](src/img/Step_2.png)

3. 继续在设置中，修改选项(为了写入日志)
![图片3](src/img/Step_3.png)

4. 配置 AI 相关 secret，推荐至少配置：
- `DEEPSEEK_API_KEY`
- `ENNCY_KEY`
- `AI_PROVIDER=deepseek`

如果你想继续使用旧的 OpenAI 兼容接口，也可以保留 `AI_KEY`。

可选配置：
- `DEEPSEEK_BASE_URL=https://api.deepseek.com`
- `DEEPSEEK_MODEL=deepseek-v4-flash`
- `DEEPSEEK_REASONING_EFFORT=high`
- `DEEPSEEK_ENABLE_THINKING=true`

5. 再配置一个 secret，`FILTERED_COURSES`，用英文逗号隔开，不要有空格，填写需要一直监听答题的课程，为空则代表所有课程都监听

例如：`计算机组成原理,数据结构`

6. 去 Action 板块 Run，观察运行结果，检查是否通过
![图片4](src/img/Step_4.png)

## 方法2 部署在服务器
### 🌟 说明

**⚠️ 注意 注意设置好运行自动化时的 Cookie 过期提醒**

### 🚀 开始配置
1. 进入 `config.py`，修改 `isLocal` 变量为 `True`

2. 填写 `config.ini`

3. 安装依赖
```bash
pip install -r requirements.txt
```

4. 配置 `config.ini` 或环境变量中的 AI 相关参数，推荐最少设置：
- `DEEPSEEK_API_KEY`
- `ENNCY_KEY`
- `AI_PROVIDER=deepseek`

5. 配置 `config.py` 中
```python
filtered_courses = [
    # 默认为空 所有课题监听课程测试
    # 若填写课程名称 则只监听列表里的课，其余课仅签到,建议按自己需求添加
    "计算机组成原理", "数据结构"
]
```

6. 先运行测试脚本验证新的 AI 答题链路
```bash
python test_ai_answering.py
```

7. 定时运行 `start.py`（推荐使用宝塔面板定时任务，具体教程自行搜索）
```bash
python start.py
```

## AI 答题链路测试
新增 `test_ai_answering.py`，用于模拟课堂发送给 AI 的问题 payload，验证新的 DeepSeek/AI 答题链路是否能返回项目可消费的答案格式。

运行方式：

```bash
python test_ai_answering.py
```

默认会自动跑 3 组示例：
- 单选题
- 多选题
- 填空题

也可以手工传参测试：

```bash
python test_ai_answering.py --type 单选题 --problem "下面哪个选项是字母 A？" --options '["A. A","B. B","C. C","D. D"]'
```

如果要测试图片题，可以额外传入：

```bash
python test_ai_answering.py --type 单选题 --problem "" --options '["A","B","C","D"]' --img-url "https://example.com/test.png"
```

## DeepSeek 配置说明
本项目现在默认使用 `deepseek` 作为 AI provider，同时也支持 `openai`。

推荐环境变量：
- `AI_PROVIDER=deepseek`
- `DEEPSEEK_API_KEY=你的 DeepSeek Key`
- `DEEPSEEK_BASE_URL=https://api.deepseek.com`
- `DEEPSEEK_MODEL=deepseek-v4-flash`
- `DEEPSEEK_REASONING_EFFORT=high`
- `DEEPSEEK_ENABLE_THINKING=true`
- `DEEPSEEK_TIMEOUT=30`

### OpenAI 配置说明
如果要使用 OpenAI 兼容接口：

推荐环境变量：
- `AI_PROVIDER=openai`
- `OPENAI_API_KEY=你的 OpenAI Key`
- `OPENAI_BASE_URL=https://api.chatanywhere.tech/v1`
- `OPENAI_MODEL=gpt-4o-mini`

如果使用本地 `config.ini`，也可以继续写入：
- `SESSION`
- `AI_KEY`
- `ENNCY_KEY`
- `DEEPSEEK_API_KEY`
- `TENCENT_API_KEY`

## 配置说明

本项目采用 **统一配置方案**，所有配置项都通过 `.env` 文件进行管理。

### 配置优先级
- **主要配置文件**: `.env` 文件（推荐使用）
- **备用配置**: `config.ini` 文件（仅在本地运行且.env不存在时作为参考，不推荐使用）

### 必需配置项

复制 `.env.example` 为 `.env` 并填写以下必需参数：

```ini
# 登录凭证（必需）
SESSION=你的雨课堂SESSIONID

# AI提供商选择（必需）：deepseek 或 openai  
AI_PROVIDER=deepseek

# DeepSeek配置（当AI_PROVIDER=deepseek时必需）
DEEPSEEK_API_KEY=你的 DeepSeek Key

# OpenAI配置（当AI_PROVIDER=openai时必需）
OPENAI_API_KEY=你的 OpenAI Key
OPENAI_BASE_URL=https://api.chatanywhere.tech/v1
OPENAI_MODEL=gpt-4o-mini

# 题库搜索密钥（可选，用于fallback搜题）
ENNCY_KEY=你的言溪题库Key
```

### 完整配置示例

```ini
# 选择使用的AI提供商：deepseek 或 openai
AI_PROVIDER=deepseek

# 登录凭证
SESSION=dsu3ez5ytsmhgk5g0t1bquy1ca962oft

# OpenAI配置
OPENAI_API_KEY=sk-your-openai-key
OPENAI_BASE_URL=https://api.chatanywhere.tech/v1
OPENAI_MODEL=gpt-4o-mini

# DeepSeek配置
DEEPSEEK_API_KEY=sk-your-deepseek-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_REASONING_EFFORT=high
DEEPSEEK_ENABLE_THINKING=true

# 超时和功能开关
AI_REQUEST_TIMEOUT=15
ENABLE_QUESTION_BANK=false

# 题库搜索配置
ENNCY_KEY=your-enncy-key

```

## 获取 SESSIONID 方式

访问 https://changjiang.yuketang.cn/ ，登录后，按 F12
![图片1](src/screenShot/1.png)
![图片2](src/screenShot/2.png)
![图片3](src/screenShot/3.png)
![图片4](src/screenShot/4.png)

复制粘贴得到的 id 到 `config.ini` 并保存即可

## [获取 AI_KEY(AI 用于解题或辅助题库搜题规格化答案)](https://api.chatanywhere.org/v1/oauth/free/render)
## [获取 ENNCY_KEY(言溪题库 用于题目为空时搜题)](https://tk.enncy.cn/)

## 备注
- `start.py` 会在启动时初始化 AI 策略引擎。
- `util/ai.py` 的 `request_ai()` 接口保持不变，课堂监听代码无需重写。
- `util/ai_strategy.py` 仍然保留 OCR → 题库 → AI 的三阶段流程。

## 多模型AI配置说明

本项目现在支持**多模型并行调用**，可以同时配置多个AI模型（如DeepSeek、OpenAI等），系统会并行调用所有可用模型，并根据配置的优先级（置信度）自动选择最佳答案。

### 配置方式

在 `.env` 文件中按以下格式配置多个模型：

```ini
# 模型1: DeepSeek
MODEL_1_NAME=deepseek
MODEL_1_API_KEY=你的_DeepSeek_API_Key
MODEL_1_BASE_URL=https://api.deepseek.com
MODEL_1_MODEL=deepseek-v4-flash
MODEL_1_PRIORITY=1.0

# 模型2: OpenAI  
MODEL_2_NAME=openai
MODEL_2_API_KEY=你的_OpenAI_API_Key
MODEL_2_BASE_URL=https://api.chatanywhere.tech/v1
MODEL_2_MODEL=gpt-4o-mini
MODEL_2_PRIORITY=0.8

# 可以继续添加更多模型...
```

### 工作原理

1. **并行调用**: 系统会同时向所有配置的模型发送请求
2. **置信度评估**: 使用 `PRIORITY` 值作为模型的置信度权重
3. **最佳选择**: 自动选择返回有效答案且置信度最高的模型结果
4. **容错机制**: 如果某个模型调用失败，不影响其他模型的正常工作

### 优势

- **提高准确性**: 多模型对比可以减少单个模型的错误
- **增强稳定性**: 某个模型不可用时，其他模型仍可提供服务  
- **灵活配置**: 可以根据模型性能和成本调整优先级
- **统一接口**: 所有模型都通过OpenAI兼容API调用，简化维护

### 示例配置

```ini
# === AI模型配置（支持多个模型并行调用）===

# 模型1: DeepSeek (推荐，高优先级)
MODEL_1_NAME=deepseek
MODEL_1_API_KEY=sk-your-deepseek-key
MODEL_1_BASE_URL=https://api.deepseek.com
MODEL_1_MODEL=deepseek-v4-flash
MODEL_1_PRIORITY=1.0

# 模型2: OpenAI (备用，较低优先级)
MODEL_2_NAME=openai
MODEL_2_API_KEY=sk-your-openai-key
MODEL_2_BASE_URL=https://api.chatanywhere.tech/v1
MODEL_2_MODEL=gpt-4o-mini
MODEL_2_PRIORITY=0.8
```

