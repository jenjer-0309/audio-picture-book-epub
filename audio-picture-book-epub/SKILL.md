---
name: audio-picture-book-epub
description: 把「每页一张图 + 每页一段录音」的素材做成逐页配音、翻页朗读的固定版式 EPUB3 有声绘本，并校验成品。播放为单轨：页内音频由 js/reader.js 控制器唯一驱动，页面不带 autoplay。当用户提到 有声绘本 / 绘本 epub / 每页配音 / 图片配音频 / 翻页播放音频 / 把图片和录音做成电子书 时使用；也用于排查阅读器故障——Apple Books(iBooks) 某页图被放大、朗读一前一后重复约 0.5 秒、书末多出目录页（如第 16、17 页）、首次打开整本书被依次播完、翻页后朗读一闪即停、翻页自动播出的是下一页的录音、必须点一下页面才出声的阅读器。Builds and validates a per-page narrated fixed-layout EPUB 3 picture book with an in-page audio track driven by a JavaScript controller, and diagnoses readers that blip, stay silent, sweep the whole book, play the neighbouring page's recording, or need a gesture. Readers that never run JavaScript are not supported by design.
---

# 有声绘本 EPUB

把一个装着页面图片、每页一段录音的文件夹，变成一本会自己朗读的固定版式 EPUB 3 绘本。

音频由**页内 `<audio>` + `js/reader.js` 控制器**承载（单轨）：页面不带 `autoplay`，起播只归控制器——翻页时自动朗读，引擎掐断时无声让路。历史上曾有 Media Overlay（SMIL）第二条轨道，已按决策移除（不运行 JavaScript 的阅读器因此不在支持范围内）；决策记录见 `references/playback.md`。

## 工作流

1. **先检查环境，然后告诉用户缺什么并询问。** 运行
   `python scripts/check_env.py`（加 `--json` 可得到机器可读的报告）。
   一切齐全时退出码为 `0`，只缺可选部分时为 `2`，完全做不出书时为 `1`。然后：
   - 直白地说明哪些能力可用、哪些不可用；
   - 解释**每一个**缺口的后果——`check_env.py` 会打印这段文字，转述它，不要丢掉；
   - **询问用户是否要安装缺失的部分**，并给出与其操作系统对应的准确命令。绝不静默安装任何东西。

   其中最要紧的是 **Java + epubcheck**。没有它们，你仍然可以构建一本完整的书并运行本项目自带的校验器，但没有任何东西能给出权威判定。这种情况下绝不要把结果称作"已验证"——应当说它通过了项目自检，而**没有**用 epubcheck 验证过。不要因为一个可选缺口就卡住整个任务：主动提出立刻继续。
2. **清点文件夹。** 找出封面配对——文件名主干**不**以数字开头的那对图片和音频（`cover.png` + `cover.mp3`、`封面.png` + `封面录音.mp3`）——以及带编号的页面配对（`01.png` + `01.mp3`，……）。报告页数，并标出像素尺寸与其余页面不同的页面图片：某一页在 Apple Books 里看起来被放大，正是这个原因。文件名以数字开头但不是纯数字的文件（`06u5kx...png`）通常是角色设定图或草稿——把它们排除并说明这一点。如果文件夹里**没有**封面配对，向用户要一个，而不是转而使用 `--cover`：`build_epub.py` 没有封面会拒绝运行，而拿某一页当封面会让同一段录音被打包两次（打包后的名称总是 `images/cover.jpg` / `audio/cover.mp3`，于是封面看起来、听起来和那一页一模一样，之后每一次设备诊断都会因此含混不清）。构建器会打印封面的来源——把它转述给用户。
3. **确认尚未定下的选择**（除非请求里已经隐含了答案）：
   * 播放方式——自动播放加手动控制，还是只有手动控制；
   * 图片尺寸——转成高质量 JPEG，还是保留原始 PNG；
   * 目标阅读器——iOS 的实测基准是 Apple Books，Android 是 Readest / Lithium 一类运行 JS 的阅读器；不运行 JavaScript 的阅读器（Google Play Books 等）**不在支持范围内**，如实说明。
4. **构建**：用 `scripts/build_epub.py`。它会把每页图片归一到同一块共享画布，每页恰好写入一个 `<audio>`（不带 `autoplay`），并让导航文档不进入 spine；每页渲染后立即用校验器的同一批谓词（`hard_rules.py`）自检，违规即中止。**一切构建产物都落在工作目录（workdir）的 `build/` 下**：成品默认写到 `build/<书名>.epub`，打包中间文件也在其中（打包完成即被清理）。
5. **校验**：用 `scripts/check_epub.py`，在把东西交出去之前修完它报告的每一个错误。不要跳过这一步：它抓到的缺陷在桌面浏览器里根本看不见。如果 Java 运行时可用，再运行 epubcheck**（本仓库自带：`assets/epubcheck-5.4.0/epubcheck.jar`，`java -jar` 直接跑，无需另下载）**；否则这个脚本就是唯一的关卡。
6. **报告**输出路径、页数、文件大小和校验结果。然后告诉用户**导入前先把自己阅读器书库里更早的副本删除**——否则 Apple Books 会一直打开缓存的旧文件，看起来就像改动没生效。并说明**测试基准阅读器**：iOS 用 iBooks（Apple Books），Android 用 [Readest](https://readest.com/download) 或 [Lithium](https://play.google.com/store/apps/details?id=com.faultexception.reader)（实测可播放）；**其他阅读器不保证**能否播放——能否出声取决于该阅读器是否运行页面脚本、以及对页内音频的处理，不是书本身的缺陷；不运行 JavaScript 的阅读器整本无声属于设计取舍。

## 硬性规则

下面每条规则都对应一个只在真实阅读系统里才会出现的缺陷。不要把它们"简化"掉。完整的症状 → 原因 → 修复细节，包括要留意的用户原话措辞，都在 `references/apple-books-pitfalls.md` 里。

1. **每一页最终必须是同样的尺寸。** 所有页面图片共享同一块像素画布，每一页声明完全相同的 `<meta name="viewport">`。尺寸混杂会让 Apple Books 把与其余不一致的页面放大约 1.6 倍。
2. **每页恰好一个 `<audio>` 元素，音频 URL 只被引用一次；禁止 `autoplay` 属性，禁止 `<noscript>` 兜底音。** 预渲染环节（Apple Books 首次打开、生成缩略图）**不运行脚本**：`autoplay` 会在那里自行起播（整本书从封面到尾页被依次播一遍），`<noscript>` 兜底音也真的存在——第二个解码同一文件的元素，一旦被启动就会相隔约 0.5 秒再播一遍。起播只归控制器。这个元素还必须是**本页的**元素：id 按页限定（`stage-<key>`、`pgAudio-<key>`、`pgBtn-<key>`），控制器从自己的 script 标签上读取 `data-page`——有些阅读器会把多个 spine 文档放进同一个 DOM，id 一旦共用就会解析到别的页。无配音页必须声明静默（`data-audio="none"`，即 0 个 `<audio>` + 该标记，其余组合非法）。页面文档在 manifest 中标 `properties="scripted"`，**绝不**标在 `.js` 资源上（epubcheck OPF-012）。
3. **导航文档不进入 spine。** Apple Books 无视 `linear="no"`，会把它当阅读页渲染；一个可回流的链接列表可能在书末膨胀成好几页多余内容。

## 当阅读器行为异常时

阅读系统不给你控制台，所以要从页面本身入手诊断：用 `--debug-hud` 重新构建，每一页都会带上一段带时间戳的读数，记录控制器做过的动作（`skip(hidden)`、`skip(frame-hidden)`、`frame shown`、`frame hidden`、`blocked <Error> <why>`、`probe ok`、`unmuted`、`killed at start - yielding`、`paused mid`、`paused(hidden)`）。问清是哪一页发出的声音；点页面上的播放按钮是始终可用的对照路径（按钮总归由控制器驱动）。

## 文件

- `scripts/check_env.py` — 环境探测。先运行它。
- `references/apple-books-pitfalls.md` — Apple Books 缺陷集（图被放大 / 朗读重复 / 书末多页 / 翻页闪停 / 整本扫播），每个都带有用户可见症状、根本原因、修复方式，以及能再次抓到它的回归护栏。
- `references/playback.md` — 单轨播放设计：控制器行为索引、阅读器支持矩阵、Media Overlay 移除决策记录（含重引入指引）。
- `references/epub-anatomy.md` — 必需的 EPUB 3 结构与版式契约：OPF manifest 与 spine、nav/NCX、打包规则，以及 CSS/JS/音频设计。
- `scripts/hard_rules.py` — 硬性规则的**单一编码**（共享常量、页面/包级事实抽取、谓词）；生成器的打包前自检与校验器的成品巡检共用它。改规则只改这里，不要在 build/check 各写一份。
- `scripts/build_epub.py` — 生成器。自动检测页面、归一化图片、给每页写页内音频与 `js/reader.js`、打包归档：
  `python build_epub.py --src . --title "..." --author "..."`（默认输出 `build/<书名>.epub`）。
  `--debug-hud` 给每页盖上一段诊断读数，用于在真实设备上调试阅读器。
- `scripts/check_epub.py` — 校验器：对成品 zip 运行 `hard_rules.py` 的全部谓词，外加结构检查：`python check_epub.py book.epub`。
- `scripts/test_hard_rules.py` — 谓词单测（仅标准库，秒级）：`python test_hard_rules.py`。
- `assets/epubcheck-5.4.0/epubcheck.jar` — 自带的 epubcheck 5.4.0（需 Java）：`java -jar assets/epubcheck-5.4.0/epubcheck.jar book.epub`。

`build_epub.py` 需要 **Pillow**：`python -m pip install Pillow`；
`check_epub.py`、`hard_rules.py` 与 `test_hard_rules.py` 只用标准库。
`check_env.py` 除标准库外什么都不需要，所以在最简的 Python 安装上照样能跑。
