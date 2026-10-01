# 播放：页内音频 + JS 控制器（单轨）

一本有声绘本的播放只有**一条轨道**：页内 `<audio>` 元素，由 `js/reader.js` 控制器驱动。页面**不带 `autoplay` 属性**——控制器是唯一的起播路径；不运行脚本的上下文（预渲染 / 预加载）因此不会自行出声。

> 本文件取代旧版的 `media-overlays.md`（Media Overlay / SMIL 双轨设计）。移除 MO 的决策记录与重引入指引见本文件「决策记录」一节。

## 控制器行为索引

完整的设计约束在 `epub-anatomy.md` 的「播放 controller」一节；这里是要点索引，改控制器前先读那份。

- **单一起播路径**：所有起播都经由 `attempt()`；不要新增 `play()` 调用点。
- **静音探针 + 让路**：自动起播先以 `muted` 起播——被引擎掐断即全程无声让路、不重试（重试就是"停 → 重播"的来源）；探针存活约 550 ms 后回到 0 秒并解静音（**不**重新 `play()`）。显式点按按钮总是直接出声。
- **在屏门槛 `pageShown()`**：顶层 `document.visibilityState !== 'hidden'` **且** iframe 帧自身在屏。`frameShown()` 的判定——无 client rect（`display:none` / 未渲染）、父文档计算样式 `visibility:hidden`、帧盒与父视口无交叠，三者任一即不在屏；非 iframe、或取不到父文档（跨域）时恒为在屏（零回归）。自动路径（起播、重试调度 `schedule()`、`hushOthers`、pause 归因、探针解静音 `finishProbe`）全部以此为准。**这修掉了"翻页自动播出下一页录音"**：固定版式阅读器（foliate-js 系）在隐藏 iframe 里预加载下一页且脚本照跑，被预加载文档的 `visibilityState` 随顶层文档，只查顶层会在下一页的探针存活时把它解静音。
- **帧守望**（仅 iframe 内，300 ms 轮询）：预加载帧被换上舞台时补发 `attempt('frame-shown')` 并重置重试预算（`wave`/`total`；引擎拒绝记录 `kills` 保留——一次上台等于一次新的页展示），HUD 记 `frame shown`；被换下时按"我们自己发起的暂停"归因（`pausedForHidden`，HUD 记 `frame hidden`，不计引擎掐断）。
- **resolve-at-use**：绝不跨事件持有 DOM 引用；每次触发重新解析当前页的元素（`bind()`）；点按处理器委托在 `document` 上，从被点按钮向上解析 `.stage`、再向下解析该页的 `audio`。某些阅读器维护页面文档池、只替换内容不重跑脚本——加载时的缓存会指向已不在屏上的页面（真机实测）。
- **借用首手势 + 预算内重试**：被**拒绝**的起播（`play()` 拒绝 / 同步抛错，无声）在 300/900 ms 重试、同一时刻只有一个在途；借用页面生命周期内的第一次手势；整页 16 次尝试封顶；显式点按重置预算。
- **页作用域 id + `data-page`**：保留。裸 id 会让"多页同 DOM"的阅读器解析到邻居的元素（真机实测）；`check_epub.py` 强制检查。

## 阅读器支持矩阵

| 阅读系统 | 播放 | 说明 |
| --- | --- | --- |
| Apple Books（iOS/iPadOS） | ✅ | 运行 JS；翻页自动朗读（实测基准） |
| Readest / Lithium（Android） | ✅ | foliate-js 系：预加载帧由帧门槛 + 帧守望处理（实测基准） |
| 其他运行 JS 的阅读器 | 通常 ✅ | 不保证；要求先点一下页面才出声的阅读器仍可用，但别承诺翻页自动朗读 |
| **不运行 JavaScript 的阅读器**（Google Play Books 等） | ❌ **不支持（设计取舍）** | `<audio>` 只解析 `src`，`controls`/`autoplay` 被忽略；没有第二条轨道，整本无声 |

## 决策记录：为什么移除 Media Overlay

**曾有双轨。** 每页同时发 EPUB 3 Media Overlay（SMIL）轨和页内音频 + JS 轨：Apple Books 只走 JS 轨（翻页自动读），Google Play Books 不跑 JS 只走 MO 轨。

**为何移除**（三项合并决策）：

1. MO 的交互形态是「听书模式」——由阅读器的朗读/听书控件启动、连续播放、自动翻页——与产品「翻到哪页读哪页」的用户习惯不吻合。
2. MO 从未做过针对性的真机播放验证：历次真机反馈全部落在 JS 轨上。
3. 实际使用的阅读器（Apple Books / Readest 等）均运行 JS。

**接受代价**（如实记载）：

- 不跑 JS 的阅读器（Google Play Books 等）整本无声；
- 各家阅读器的「朗读/听书模式」不再可用。

**附带待验证利好**：文档记载「带 MO 构建时，Apple Books 疑似占用音频会话、约 0.2 s 掐断页内起播」——MO 移除后该嫌疑源消失；Apple Books 的 JS 轨是否就此顺畅，**以真机复核为准**。

**若要重引入 MO**：git 历史里有完整实现（`media-overlays.md` 及 `SMIL_TPL`、`media:duration` 与 MP3 时长机器、`hard_rules` 的 SMIL 谓词、`--no-media-overlay` 开关）。恢复时须补一次针对性真机验证，并重新评估「MO 占会话」的嫌疑。

## 已知限制

- **不运行 JavaScript 的阅读器整本无声**——设计取舍，见上。
- **有些阅读器要求页面先被触碰过才允许出声**（自动播放策略，真机实测）：翻页无声、点按钮可播；控制器借用首手势缓解，但别承诺这类阅读器翻页自动朗读。
- **有些阅读器把多页放进同一 DOM、或替换页面内容却不重跑脚本**（真机实测）：已由 resolve-at-use + 事件委托 + 页作用域 id 处理。
- **预加载帧的隐藏方式因阅读器而异**（`display:none` / `visibility:hidden` / 移出视口）：`frameShown()` 覆盖这三种；若真机上仍有越界起播，用 `--debug-hud` 复核（预加载帧的读数应有 `frame hidden` 与 `skip(frame-hidden)`）。
- **旧副本会被缓存**：导入前先删阅读器书库里的旧版本，否则修复看起来没生效。
