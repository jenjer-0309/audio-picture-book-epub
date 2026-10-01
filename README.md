# audio-picture-book-epub

把「每页一张图 + 每页一段录音」变成一本**会念给你听的电子书**。

输入一个装着图片和录音的文件夹（孩子自己画的图 + 家长录的朗读、老师做的课件、
绘本草稿……），输出一本 **EPUB 3 固定版式有声绘本**：翻开哪一页，就播那一页的录音。

**不限于任何 Agent，不限于任何操作系统。** 核心是两个纯 Python 命令行工具，
没有 Agent 也能直接用；`SKILL.md` 是给 Agent 看的入口，装进任何支持 Skill
的 Agent 都能用。

---

## 它解决什么问题

看起来「图片 + 音频 → 电子书」很简单，实际上有几个只在**真实阅读器**里才会暴露的
坑，而且都是沉默失败 —— 在电脑浏览器里打开一切正常，一上手机就出问题：

| 现象 | 根因 |
|---|---|
| 某一页的图**被放大**了 | Apple Books 按**第一页**的尺寸比例缩放整本书，尺寸不同的页会被按原始像素放大 |
| 朗读**一前一后重复一遍**（间隔约 0.5 秒） | 页面上有两个 `<audio>` 且都带 `autoplay`（如 `<noscript>` 兜底音）；预渲染环节不跑脚本，兜底那个真的会加载并自播，两条解码管线启动时间错开 |
| 书末**多出目录页**（如第 16、17 页） | Apple Books **不理会** spine 的 `linear="no"`，把导航文档当成正文页；流式目录被重排成好几页 |
| 首次打开 / 预渲染时**整本书被从封面到尾页依次播一遍** | 页面带 `autoplay`：预渲染环节不跑脚本、不经控制器、不受任何可见性门槛约束，每渲染一页就自播 |
| 翻页自动朗读的却是**下一页**的录音（首开时封面播第 1 页） | 阅读器在隐藏 iframe 里预加载下一页且脚本照跑；顶层可见性对被预加载页面仍是 visible，只查它会提前解静音 |
| 在**不运行 JavaScript 的阅读器**里整本无声（Google Play Books 等） | 播放由 JS 控制器唯一承载（`autoplay` 已禁用）；不跑 JS 的阅读器不支持，属设计取舍 |

这个项目把每一坑的修法都固化成了**硬性规则 + 自动校验 + 回归防护**，
不是靠记性。

---

## 播放：页内音频 + JS 控制器（单轨）

播放只有**一条轨道**：页内 `<audio>` + `js/reader.js` 控制器。页面**不带 `autoplay`**，起播只归控制器：

- **Apple Books**：翻页自动朗读；首次打开整本书不再被扫播（autoplay 已禁）。
- **Readest / Lithium（Android）**：实测可播；预加载的下一页被「帧可见性门槛 + 帧守望」
  挡住，换上台后才起播。
- **不运行 JavaScript 的阅读器（Google Play Books 等）**：整本无声 —— **不支持，属设计
  取舍**。历史上曾有 Media Overlay 第二轨，已按决策移除（MO 的「听书模式」交互与
  「翻到哪页读哪页」不符）；决策记录见 `references/playback.md`。

自动起播先**静音试探**：被阅读器掐断即无声让路（见「已知限制」）；点页面按钮始终可用。

细节见 [`references/playback.md`](audio-picture-book-epub/references/playback.md)。

---

## 快速开始

素材文件夹长这样（命名不限语言，封面那对只要**不以数字开头**即可）：

```
我的绘本/
├── 封面.png      封面录音.mp3
├── 01.png        01.mp3
├── 02.png        02.mp3
└── ...           ...
```

> ⚠️ **封面必须自己在素材里给。** 目录里没有"非数字开头"的封面图/音时，构建器**直接
> 拒绝运行**（它不会去别处找封面）。如果显式用 `--cover` 复用某一页，构建器会打印
> 封面来源并在复用时提示"这份素材被打包了两次"。包内文件名固定是 `images/cover.jpg`
> 与 `audio/cover.mp3`，所以复用某页的后果是：**封面看起来、听起来都和那一页一模一样**
> ——之后排查真机问题时会分不清"听到的是哪一页"。`check_epub.py` 也会对逐字节相同的
> 封面/内页给出警告。

### 1. 先检查环境

```bash
python audio-picture-book-epub/scripts/check_env.py
```

### 2. 生成

```bash
python audio-picture-book-epub/scripts/build_epub.py \
    --src "我的绘本" \
    --title "我的绘本" \
    --author "示例作者" \
    --out "build/我的绘本.epub"
```

脚本会自动：认出封面、排除非故事页（名字以数字开头但不是纯数字的，通常是角色设定稿）、
把尺寸不一的图**统一到同一画布**、给每页写好页内音频与 `js/reader.js`、打包。构建产物与中间文件都放在工作目录的 `build/` 下：不传 `--out` 时成品默认 `build/<书名>.epub`。

### 3. 校验

```bash
python audio-picture-book-epub/scripts/check_epub.py "build/我的绘本.epub"
```

有 Java 时，仓库自带的 epubcheck 还能给出权威结论：

```bash
java -jar audio-picture-book-epub/assets/epubcheck-5.4.0/epubcheck.jar "build/我的绘本.epub"
```

### 4. 导入阅读器

**测试基准阅读器**（验证「能否播放」请用这两个平台的推荐阅读器）：

- **iOS**：iBooks（Apple Books），系统自带。
- **Android**：[Readest](https://readest.com/download) 或
  [Lithium: EPUB Reader](https://play.google.com/store/apps/details?id=com.faultexception.reader)，
  实测可以正常播放。

**其他阅读器不保证**：能否出声取决于该阅读器是否运行页面脚本、以及对页内音频的处理，
可能出现翻页无声、必须先点一下页面才出声等差异 —— 这属于阅读器支持问题，不是书本身
的缺陷；**不运行 JavaScript 的阅读器（Google Play Books 等）整本无声，属设计取舍**。

> ⚠️ **导入前先把书库里那份旧的删掉。** 阅读器会缓存旧文件，
> 直接重新导入可能仍在打开旧版本，让你以为修复没生效。

---

## 环境要求

**先跑 `check_env.py`，它会告诉你缺什么、缺了会怎样、怎么装。** 这里也一样列一遍：

| 组件 | 必需性 | 缺少的后果 | 安装 |
|---|---|---|---|
| **Python ≥ 3.8** | 🔴 必需 | 什么都做不了 | [python.org](https://python.org) |
| **Pillow** | 🔴 必需 | **无法生成电子书** —— 图片缩放/转码靠它 | `python -m pip install Pillow` |
| **Java（JRE 11+）** | 🟡 可选 | 无法运行 epubcheck（见下） | Win `winget install Microsoft.OpenJDK.17`<br>mac `brew install openjdk@17`<br>Linux `sudo apt install default-jre` |
| **epubcheck** | 🟡 可选 | **可以生成电子书、也可以自检，但没有权威校验** | 本仓库**自带** `audio-picture-book-epub/assets/epubcheck-5.4.0/epubcheck.jar`（需 Java）；或 [github.com/w3c/epubcheck/releases](https://github.com/w3c/epubcheck/releases) |

### 关于「不装 Java 和 epubcheck 会怎样」

这是最值得说清楚的一条：

- ✅ **照样能生成**完整的电子书 —— 页图、页音频、翻页朗读，一样不少。
- ✅ **照样能自检** —— `check_epub.py` 只用 Python 标准库，会检查 `mimetype` 位置、
  manifest/spine 完整性、autoplay 禁令与控制器存在性、以及上面各坑的回归防护。
- ❌ **但没有权威结论。** epubcheck 是唯一能发现本项目未编码的规范违规的工具。

所以：**没跑 epubcheck，就不要说这本书「已通过校验」**。准确的说法是
「已通过项目自检，未做 epubcheck 官方校验」。这不是文字游戏 —— 报错了比不报更糟。

`check_env.py` 的退出码：`0` 全齐 / `2` 缺可选项（仍可生成）/ `1` 缺必需项（无法生成）。
加 `--json` 可拿到机器可读的报告。

### 任何 Agent 的启动约定

`SKILL.md` 的工作流**第 1 步**就是环境检查，并要求 Agent：

1. 说清楚哪些能力可用、哪些不可用；
2. 解释**每一项缺失的后果**（`check_env.py` 会直接打印这段文字）；
3. **询问用户是否要安装**，并给出对应系统的命令；
4. **绝不静默安装**，也不因为缺了可选项就卡住整个任务。

---

## 装到各种 Agent

Skill 就是一个文件夹，入口是 `SKILL.md`（YAML frontmatter 里带 `name` 和 `description`）。

**通用范式**：本 Skill 遵循开放的 [Agent Skills 规范](https://agentskills.io)（Anthropic
发起、跨产品通用，[vercel-labs/skills](https://github.com/vercel-labs/skills) 等工具以其为
兼容基准）：目录 = `SKILL.md`（frontmatter 的 `name` 与目录名一致）+ `scripts/`（可执行
代码）+ `references/`（按需加载的文档）。兼容该规范的任何 Agent/CLI 都能发现并安装它。

**npx skills add（通用 CLI，一条命令安装）**：

```bash
# 从 GitHub（发布到远端后；工具会递归找到 audio-picture-book-epub/SKILL.md）
npx skills add <owner>/audio-picture-book-epub

# 从本地仓库
npx skills add .

# 指定目标 Agent（claude-code / codex / opencode ...），-g 装到用户目录
npx skills add . -a claude-code -g
```

默认装到项目级 `./<agent>/skills/`（符号链接，加 `--copy` 改为复制）；`-g` 装到
`~/.claude/skills/` 一类的用户目录。`-l` 可先列出再装，`-s` 按技能名挑选。

**LiveAgent**：把 `audio-picture-book-epub/` 整个目录放进用户的 skills 根目录即可，
或在对话里用 `SkillsManager(action=install, source="<这个仓库路径>")` 安装。

**Claude Code / 其他支持 Skill 的 Agent**：把 `audio-picture-book-epub/` 放进该 Agent
的 skills 目录。格式是通用的 Markdown + YAML frontmatter，没有私有依赖。

**没有 Agent 也行**：直接当命令行工具用 —— 上面「快速开始」那三步就是全部。
`build_epub.py` 和 `check_epub.py` 是同目录下的纯 Python 脚本，共用一个
`hard_rules.py` 规则模块（三个文件一起分发，别单独拷其中一个）。

---

## 目录结构

```
/
├── README.md                            ← 你在这里
├── AGENTS.md                            ← 仓库指南（Agent 工作约定）
└── audio-picture-book-epub/             ← Skill 本体（可单独拷贝/安装）
    ├── SKILL.md                         ← Agent 入口：工作流 + 硬性规则
    ├── references/
    │   ├── apple-books-pitfalls.md      ← Apple Books 缺陷集：症状/根因/修法/防护
    │   ├── playback.md                 ← 单轨播放设计、支持矩阵、MO 移除决策记录
    │   └── epub-anatomy.md             ← EPUB3 结构规范 + 排版契约
    ├── scripts/
    │   ├── check_env.py                 ← 环境探测（先跑这个）
    │   ├── hard_rules.py                ← 硬性规则的单一编码（事实抽取 + 谓词）
    │   ├── build_epub.py                ← 生成器（打包前用同一批谓词自检）
    │   ├── check_epub.py                ← 校验器（成品 zip 巡检）
    │   └── test_hard_rules.py           ← 谓词单测（仅标准库）
    └── assets/
        └── epubcheck-5.4.0/             ← 自带的 epubcheck（需 Java，见「环境要求」）
```

> README 放在 Skill 目录**外面**是刻意的：运行时的 skill 扫描器会把
> `README.md` 当作一种「无 frontmatter 的兜底入口」，跟 `SKILL.md` 放在
> 同一层容易造成识别歧义。

---

## 命令行参数

`build_epub.py`：

| 参数 | 说明 |
|---|---|
| `--src` | 素材目录（默认当前目录） |
| `--title` / `--author` / `--language` | 书名 / 作者 / 语言（默认从目录名推断 / 空 / `zh-CN`） |
| `--out` | 输出路径（默认 `build/<书名>.epub`） |
| `--canvas 940x1672` | 指定画布（默认取页图尺寸的**众数**） |
| `--quality 92` | JPEG 质量 |
| `--cover` / `--cover-audio` | 手动指定封面图/封面录音 |
| `--allow-silent` | 允许没有配音的页面（成品中该页带 `data-audio="none"` 标记，校验器据此放行） |
| `--debug-hud` | 在每页顶部印出播放控制器的事件时间线（真机排障用，**不要外发**） |
| `--cover-label` / `--page-label` / `--nav-label` | 目录里的文案 |

`check_epub.py <file.epub>` —— 无参数时打印用法。

---

## 已知限制

- **不运行 JavaScript 的阅读器整本无声**（Google Play Books 等）：设计取舍——播放由 JS
  控制器唯一承载（`autoplay` 已禁用），书内没有第二条轨道。详见 `references/playback.md`。
- **有些阅读器要求页面先被点过才允许出声**（真机实测）：翻页无声、点页面按钮能播。
  这是播放策略不是书的毛病，`js/reader.js` 会借用整页生命周期内的第一次手势并重试被拒的
  播放，所以补救办法是「点一下页面」；别承诺这类阅读器能翻页自动朗读。
- **有些阅读器会把多页放在同一个 DOM 里，还有的会直接替换页面内容却不重新执行脚本**
  （真机实测）。因此 `js/reader.js` **不在加载时缓存任何 DOM 引用**：每次使用时重新解析
  当前页的元素，点击事件委托在 `document` 上，并从被点的按钮往上找 `.stage`、再往下找
  该页的 `audio`。裸 id 仍会被 `check_epub.py` 判为错误——它不能单独解决这个问题，但能让
  文档在"多页同 DOM"的阅读器里不含糊。
- **Apple Books 可能抢占页内音频**：历史实测（**带 MO 的构建**）每次自动起播约 0.2 秒后
  就被系统掐断；MO 已移除，该嫌疑源消失，现行行为**以真机复核为准**。点按播放按钮可以
  完整朗读（引擎拒绝过本页后，页面上的普通点按也退回静音试探，不会"响一下又停"）。
  自动起播现在先走**静音探针**：被掐断时全程无声、直接让路（不再重试，也就没有
  "播放 0.x 秒 → 停 → 重播"的结巴）；探针没被掐断才解静音、从头朗读。
- 点页面播放键不受"不在屏幕上就不出声"的限制——否则引擎一旦误报 `hidden`，按钮就成了死的。
- 图片的 `alt` 目前是「第 N 页」占位。若要给视障儿童使用，需逐页补画面描述。

---

## 许可

尚未指定。如需开源发布，建议补一个 `LICENSE`（MIT 或 CC-BY-4.0 都常见）。
