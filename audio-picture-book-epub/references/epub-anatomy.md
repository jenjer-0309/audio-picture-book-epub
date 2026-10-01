# 配音固定版式图书的 EPUB 3 结构

阅读系统所需的最小集合，以及本项目所依赖的版式约定。`scripts/build_epub.py` 实现了全部内容；`scripts/check_epub.py` 负责校验。

## 归档结构

```text
mimetype                                  <- MUST be first, STORED (uncompressed)
META-INF/container.xml
META-INF/com.apple.ibooks.display-options.xml   <- optional, helps Apple Books
OEBPS/
├── content.opf                           <- package document
├── nav.xhtml                             <- EPUB 3 navigation (properties="nav")
├── toc.ncx                               <- EPUB 2 compatibility
├── css/style.css
├── js/reader.js
├── images/cover.jpg, p01.jpg ...
├── audio/cover.mp3, p01.mp3 ...
└── xhtml/cover.xhtml, p01.xhtml ...
```

**`mimetype` 规则是唯一会导致一切失效的打包错误。** 它必须是 zip 中的*第一个*条目，并且以**不压缩**方式存储，内容恰为 `application/epub+zip`，末尾不能有换行符。在 Python 中：

```python
info = zipfile.ZipInfo("mimetype", (1980, 1, 1, 0, 0, 0))
info.compress_type = zipfile.ZIP_STORED
z.writestr(info, "application/epub+zip")
# then write everything else with ZIP_DEFLATED
```

## Package document（content.opf）

关键 metadata：

| 项 | 值 |
| --- | --- |
| `dc:identifier` | `urn:uuid:<uuid4>`（`unique-identifier` 的指向目标） |
| `dc:title`、`dc:creator`、`dc:language` | 图书 metadata |
| `dcterms:modified` | `<meta property="dcterms:modified">YYYY-MM-DDTHH:MM:SSZ</meta>` — EPUB 3 要求，且必须是打包时刻 |
| cover | `<meta name="cover" content="cover-image"/>` **以及** 图片条目上的 `properties="cover-image"` |
| `rendition:layout` | `pre-paginated` — 固定版式 |
| `rendition:orientation` | `portrait` |
| `rendition:spread` | `none` — 一次一页 |

`rendition:` 前缀在 EPUB 3.2+ 中是保留的，所以 `<package>` 上不需要 `prefix` 属性。

## Manifest

- `nav.xhtml` → `properties="nav"`。
- 每个页面文档 → **仅当它确实加载了脚本时**才标 `properties="scripted"`。
- 封面图片 → `properties="cover-image"`。
- 音频条目 → `audio/mpeg`（`.mp3`）、`audio/mp4`（`.m4a`/`.aac`）。

**`properties="scripted"` 标记的是包含脚本的内容文档。** 它经常被错误地加到 `.js` 资源上，epubcheck 会以 `OPF-012` 拒绝。绝不要把它设在 XHTML 以外的任何东西上。

## Spine

- 封面文档在前，然后按阅读顺序排列各页面。
- **不放导航文档。不放 `linear="no"` 条目。** 见 `apple-books-pitfalls.md` §3。
- `toc="ncx"`，让 EPUB 2 时代的阅读器仍能得到目录。

## 导航

`nav.xhtml` 带有 `<nav epub:type="toc">`，每页对应一个 `<li><a href="...">`，还可以选择性地包含指向封面和正文开头的 `<nav epub:type="landmarks">`。`toc.ncx` 为兼容性镜像同一列表。在绘本中列出每一页是值得的：它给读者一个进度拖动条（scrubber）。

## 固定版式页面文档

每一页都是独立的 XHTML 文件：

```html
<meta name="viewport" content="width=940, height=1672"/>   <!-- every page identical -->
```

viewport 尺寸**必须与图片画布一致**，且每一页都相同，否则 Apple Books 会放大尺寸不合群的页（`apple-books-pitfalls.md` §1）。

本项目使用的版式约定：

- `html, body { width/height: 100%; overflow: hidden; background: #eef6ee }`。
- `.stage` 用 `position: absolute; top/right/bottom/left: 0` 固定 — 比百分比高度更可靠，因为它不依赖阅读系统针对 auto 高度的 body 去解析百分比。
- 页面图片以 `width/height: 100%; object-fit: contain; object-position: center center` 铺满 `.stage`，这样任何宽高比不匹配都会以 letterbox 方式留白到背景色，而不是裁切画面。
- letterbox 颜色与画入每个 JPEG 画布的背景色必须是同一个值，否则接缝会可见。
- **有且只有一个 `<audio>` 元素**，`preload="auto"`、`controls`（不跑脚本时的原生播放条兜底）；**不要 `autoplay`**——预渲染 / 预加载环节不跑脚本、会自行起播，Apple Books 曾因此把整本书从封面到尾页依次播完。见 `apple-books-pitfalls.md` §2 与 §5。
- **每个 id 都是页面作用域的，且脚本会命名自己所在的页面。** stage 容器是 `id="stage-<key>"`，媒体是 `id="pgAudio-<key>"`，控件是 `id="pgBtn-<key>"`，script 标签带有 `data-page="<key>"`。这些让文档在把多个页面放进同一个 DOM 的阅读器中不产生歧义，`check_epub.py` 会强制检查它们。它们**不是**保护 controller 免受"阅读器替换 body 却不重跑脚本"影响的机制 — 那由在使用时解析元素来处理，见下文的 controller 规则。
- script 标签放在 `<body>` 的**末尾**。放在 `<head>` 里它会在 DOM 存在之前执行，于是元素查找返回 `null`，任何 handler 都不会绑定上。

## 播放 controller（js/reader.js）

设计约束，按重要性排序：

1. **在使用时解析页面，并让每个触发都经由它。** 绝不要跨事件持有 DOM 引用：阅读系统可能替换页面 body 而不重跑脚本（真机实测 — 见 `playback.md`），于是加载时取到的引用最终指向已不在屏幕上的页面。一切都通过同一个 `bind()` 解析，当解析出的元素变化时它会重置每页状态；tap handler 委托在 `document` 上，从 `event.target` 向上解析到 `.pg-btn`，再向下解析到该容器的 `audio.pg-audio`；controller 在每个文档内是单例，所以执行两次脚本的阅读器无法叠加两组监听器。每个触发 — 立即调用、`load`、250/1200 ms 探测、`canplay`、手势、按钮和 `visibilitychange` — 都汇入同一个 `attempt()`，于是"每页一条启动路径"不会被后来的新增代码破坏。`finished`（在 `ended` 时置位）用来阻止迟到的重试重新读取一个已读完的页面。
2. **绝不在不在屏幕上的页面发声，也不要在翻页完成前启动。** 每个自动 `attempt()` 都以 `pageShown()` 为门槛 = 顶层 `document.visibilityState !== 'hidden'` **且** iframe 帧自身在屏（`frameShown()`：无 client rect / 计算样式 `visibility:hidden` / 位于父视口外皆为不在屏；非 iframe 恒为在屏）——阅读系统会在隐藏的**帧**里预取相邻页面，而被预加载文档的 `visibilityState` 随顶层文档、脚本照跑，只查顶层会提前播出"下一页"的录音。在 `visibilitychange` 变为隐藏时暂停，重新可见时再次尝试；帧的隐/现将由 300 ms **帧守望**（仅 iframe 内）补发：hidden→shown 时 `attempt('frame-shown')`，shown→hidden 时按 `pausedForHidden` 归因暂停（不计引擎掐断）。对我们自己按钮的有意 tap **豁免**于该门槛：否则一个误报 `hidden` 的阅读器会让读者拿到一个死按钮，而且读者反正只能点到屏幕上的东西。另外，自动启动要等到页面出现后约 400 ms（`ARMED_MS`）：阅读系统会在翻页过渡期间挂起 media，在过渡中尝试的启动会在零点几秒内死掉，听起来就是第一个词被切断。
3. **让引擎先手：自动启动是静音探针，被 kill 即让路。** 非显式、且本页尚未借到手势、也尚未出声的自动启动，先 `audio.muted = true` 再 `play()`：被引擎杀掉时全程无声，控制器直接让路（`MAX_KILLS = 1`，不重试——重试正是"停 → 重播"的可听来源）。探针存活约 `PROBE_MS`（550 ms）后 `currentTime = 0` 并解静音，**不重新 `play()`**（新的起播又会成为掐断目标）；解静音被引擎忽略时保持安静、保留点按路径。显式按钮总是直接有声；已借到手势的启动只在引擎尚未拒绝过本页时直接有声——被 kill 过之后，连手势启动也退回探针，只有显式点按清除拒绝状态。有声启动会先接管在途探针（停掉待执行的解静音、确保 `muted = false`；若探针已在静音播放，从 0 秒重新开始），防止"解静音被拒"的页面永远静音播放、也防止出声后又被跳回开头。`started` 只标记"已获得可听播放"。
4. **不要对抗阅读器自己的暂停。** `userPaused` 标志区分有意暂停与中断；页面隐藏时我们自己发起的暂停（读数 `paused(hidden)`）不算引擎动作。没人要求、且发生在可见状态下接近开头处（`currentTime < 1.5 s`）的暂停 = 引擎占有音频会话：**让路，不重试**。更晚处的暂停（`paused mid`）同样随它去，而不是叠加播放。
5. **重试被拒绝的启动，但要在预算内。** 若干阅读器在*本文档*见到手势之前不允许播放，而手势在 load 之后很久才到。被**拒绝**的启动（`play()` promise 拒绝或同步抛错，没有发出任何声音，不会听成结巴）在 300/900 ms 重试，**同一时刻只有一个在途**（页面启动时会有多个触发同时到来；如果每个都排自己的重试，预算会在读者碰到页面之前就耗尽），整页在 16 次尝试后停止。一次明确的 tap 会重置这些预算。
6. **到达即停他播，并忽略过期元素的事件。** 每次起播前（仅页面可见时）`hushOthers()` 暂停本 DOM 里其他 `audio.pg-audio`——池化阅读器换页时未必给旧页发 `visibilitychange`，旧页可能一直响。`bind()` 解析到不同元素时先换 `audio` 指针、再暂停旧元素并恢复其 `muted=false`；所有元素事件处理器都先判断 `el !== audio` 并忽略——被换下去的旧元素仍挂着监听器，它的事件不得改动当前页状态。
7. **为页面的整个生命周期借用一次手势**，而不是几秒，且绝不在 handler 内部启动：*翻页*的那次 tap 也会到达本文档，而页面正要被隐藏，在那里启动在每次翻页时听起来都是一声一瞬即逝的杂音。推迟约 300 ms，让 visibility 来决定。绝不 `preventDefault` — 翻页必须继续工作。
8. 移除 `controls` 并给 `<body>` 加一个 `js` class，让自定义控件取代原生栏。用 `try/catch` 包住 `removeAttribute`。

当真实设备行为异常时，用 `--debug-hud` 重新构建：它会在每页盖上一个读数，显示上述带时间戳的决策（`skip(hidden)`、`skip(frame-hidden)`、`frame shown`、`frame hidden`、`blocked <Error> <why>`、`probe ok`、`unmuted`、`killed at start - yielding`、`paused mid`、`paused(hidden)`、…）— 这是窥见一个不给你 console 的阅读器内部的唯一办法。

## 可选扩展

`com.apple.ibooks.display-options.xml`：

```xml
<display_options>
  <platform name="*">
    <option name="fixed-layout">true</option>
    <option name="open-to-spread">false</option>
    <option name="specified-fonts">true</option>
  </platform>
</display_options>
```

package document 中的无障碍 metadata 成本很低且值得包含：`schema:accessMode`（visual + auditory）、`schema:accessModeSufficient`、`schema:accessibilityFeature`（`alternativeText`）、`schema:accessibilityHazard`（`none`）。

## 校验

`epubcheck` 是权威的，但需要 Java。`scripts/check_env.py` 会报告它是否可用，而 `scripts/check_epub.py` 直接从 zip 覆盖核心检查，使项目在没有它时仍可验证：

- `mimetype` 的位置与压缩方式、XML 良构性、`container.xml`，以及必需的 metadata；
- manifest ↔ 归档一致性、spine 完整性、导航目标；
- `apple-books-pitfalls.md` 中的回归防护（含 autoplay 禁令）；
- 有音频必须有 `js/reader.js`（控制器是唯一起播路径），以及 `OPF-012` 防护（`properties="scripted"` 只能挂页面文档）。

它还会打印阅读顺序长度、共享 viewport 与共享图片尺寸，于是页数不对或某张图片尺寸异常，一眼就能看出来。

如果 Java 运行时**确实**可用，请从 `github.com/w3c/epubcheck` 下载 `epubcheck` 并一并运行 — 它是唯一能校验本项目未编码规则的东西。除非真的运行过，否则不要声称一本书已通过 `epubcheck` 验证。
