# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

把「每页一张图 + 每页一段录音」构建成**固定版式 EPUB 3 有声绘本**，核心是两个纯 Python CLI（`build_epub.py` / `check_epub.py`）加共享规则模块 `hard_rules.py`（三文件同目录分发），外加一个可分发到任何 Agent 的 Skill 包。没有构建系统、没有依赖管理、没有 lint；`test_hard_rules.py` 是谓词级轻量单测，端到端的"测试"仍是构建一本书再跑校验器。

## Commands

```bash
# 1. 环境探测（先跑这个；退出码 0=全齐, 2=缺可选, 1=缺必需）
python audio-picture-book-epub/scripts/check_env.py [--json]

# 2. 构建（需要 Pillow；素材目录结构见 README「快速开始」）
python audio-picture-book-epub/scripts/build_epub.py \
    --src <素材目录> --title "..." --author "..." --out build/<书名>.epub

# 3. 校验 —— 这就是本仓库的端到端"测试"；改动 build_epub.py / check_epub.py 后
#    必须用 example/ 构建一本书并跑通它才算验证完成
python audio-picture-book-epub/scripts/check_epub.py <输出.epub>

# 4. 规则谓词单测（仅标准库，秒级；改 hard_rules.py 后先跑这个）
python audio-picture-book-epub/scripts/test_hard_rules.py
```

- `example/` 是现成素材（封面 + 4 页图音），用作冒烟测试输入。
- 有 Java + epubcheck 时还应跑 epubcheck；没有它只能说"通过了项目自检"，不能说"已校验"（README 与 SKILL.md 均有此措辞要求）。本仓库自带一份：`audio-picture-book-epub/assets/epubcheck-5.4.0/epubcheck.jar`（需 Java），`java -jar <该路径> book.epub` 直接可用。
- `check_epub.py` 退出码：`0` = `PASSED`（可有 warning），`1` = `FAILED`，错误行前缀为 `[ERROR]`。
- `*.epub` 与 `build/` 都被 .gitignore 排除（构建产物不入库）。
- 构建产物与中间文件一律在 `build/` 下：不传 `--out` 时成品默认 `build/<书名>.epub`，打包中间目录也在 `build/` 中，打包完成后自动清理。

**Skill 安装（验证可分发性）**：用通用 [skills CLI](https://github.com/vercel-labs/skills) 验证安装 —— `npx skills add .`（本地仓库）或 `npx skills add <owner>/audio-picture-book-epub`（GitHub，发布后），加 `-a claude-code -g` 可指定目标 Agent 与用户级安装。CLI 会递归发现 `audio-picture-book-epub/SKILL.md`，默认装到项目级 `./<agent>/skills/`（符号链接，`--copy` 改复制）。

**构建器两个常见的"拒绝运行"：**

- 素材里必须有一对文件名**不以数字开头**的封面图/音，否则 `build_epub.py` 直接退出。不要用 `--cover` 拿某页凑数 —— 包内封面固定命名 `images/cover.jpg` / `audio/cover.mp3`，复用意味着同一素材打包两次，封面看起来听起来都和那页一样，之后真机排障会分不清听到的是哪一页。
- 每个数字页必须有**同名 stem** 的音频文件；缺了会中止并提示，除非显式加 `--allow-silent`。

## Architecture

三层结构，职责分明：

1. **`audio-picture-book-epub/` 是可分发单元** —— 整个目录拷走即是一个 Skill（`SKILL.md` frontmatter + `references/` + `scripts/`）。`README.md` 刻意放在它**外面**：运行时 skill 扫描器把无 frontmatter 的 README 当兜底入口，同层会冲突。
   本 Skill 遵循开放的 **Agent Skills 通用规范**（https://agentskills.io ）：`SKILL.md` frontmatter 的 `name` 必须与目录名一致（本仓库为 `audio-picture-book-epub`），配套 `scripts/`（可执行代码）与 `references/`（文档）与规范的目录语义一致。改名/移动目录或子目录时保持这套范式。
2. **`build_epub.py`（约 1350 行）是生成管线**，单文件顺序执行：`discover()` 识别封面与页对（封面 = 文件名**不以数字开头**的那对；以数字开头但非纯数字的文件被排除为草稿）→ `pick_canvas()` 按页图尺寸**众数**选共享画布 → `render_image()` 把所有图统一到该画布 → 生成每页 XHTML（唯一的 `<audio>` 不带 autoplay）+ `js/reader.js` → 打包 zip（`mimetype` 必须 STORED 且为第一项）。每页渲染后立即用 `hard_rules.py` 的谓词自检，不过即中止。
3. **硬性规则的单一编码在 `hard_rules.py`**（仅标准库：共享常量 + 事实抽取 + 谓词）。`build_epub.py` 的打包前自检与 `check_epub.py`（约 330 行）的成品 zip 巡检是它的两个 adapter；改行为改谓词，不要在两处各写一份。改之前先读懂这些检查为什么存在（`references/` 三份文档有每条规则的完整来历）。

**单轨播放设计**（整个项目的中心决策）：页内 `<audio>` + `js/reader.js` 控制器是**唯一的起播路径**（页面禁 `autoplay`——预渲染环节不跑脚本，autoplay 会在那里把整本书从封面到尾页依次播完）。曾经有过 Media Overlay(SMIL) 第二轨，已按决策移除：MO 的交互形态是「听书模式」，与「翻到哪页读哪页」不符，且真机反馈全部落在 JS 轨；不运行 JavaScript 的阅读器（Google Play Books 等）因此不支持。决策记录与重引入指引在 `references/playback.md`。

**Hard rules（改代码时不得"简化"掉，每条都对应真机上才暴露的沉默失败）：**

1. 所有页图同一像素画布 + 相同 viewport（否则 Apple Books 按第一页比例放大）。
2. 每页**恰好一个** `<audio>`、URL 恰好引用一次且指向 `audio/` 目录；**禁止 `autoplay`**（预渲染/预加载环节不跑脚本、会自行起播，整本扫播）与 `<noscript>` 兜底音（重复播放约 0.5s）；无配音页必须声明静默（`data-audio="none"`，即 0 个 audio + 标记，其余组合非法）；id 必须页作用域化（`stage-<key>` 等），脚本经 `data-page` 找自己那页 —— 因为有些阅读器把多页放同一 DOM。`properties="scripted"` 只能挂页面文档不能挂 `.js`（epubcheck OPF-012）。
3. 导航文档不得进 spine（Apple Books 无视 `linear="no"`）。

## 修改代码的注意事项

- 三份 `references/*.md`（`apple-books-pitfalls.md` / `playback.md` / `epub-anatomy.md`）记录了每个坑的症状→根因→修法→防护，是改动前的必读背景；`SKILL.md` 的 Hard rules 与 `hard_rules.py` 的谓词必须保持同步（build/check 都从它读）。
- `build_epub.py` 的 `--debug-hud`（页内诊断时间线）是真机排障工具，README 明示 debug-hud 产物不要外发。
- 无测试框架但有谓词单测：新增校验逻辑应落为 `hard_rules.py` 的谓词与事实抽取，在 `test_hard_rules.py` 补正/反例；端到端用 `example/` 构建出的书验证（正例 + 故意弄坏的反例）。
- 文档语言**定为中文**：本仓库的文档（README、AGENTS.md、SKILL.md、`references/`）一律用中文撰写；对现有英文文档（`SKILL.md` 正文、`references/*.md`）的**实质性内容改动**应译为中文，纯机械同步（如路径改名）除外。
