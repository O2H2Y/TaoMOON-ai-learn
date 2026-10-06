import Taro from '@tarojs/taro'

export const POSTER_CANVAS_ID = 'report-poster-canvas'
export const POSTER_WIDTH = 600
export const POSTER_HEIGHT = 900

/** 完整版长海报（分享页使用） */
export const FULL_POSTER_CANVAS_ID = 'full-poster-canvas'
export const FULL_POSTER_HEIGHT = 1440

export type PosterMode = 'compact' | 'full'

type PosterCanvasContext = ReturnType<typeof Taro.createCanvasContext>
type Measure = (text: string) => number

export interface PosterData {
  title: string
  accuracy: number
  correctCount: number
  incorrectCount: number
  xpGain: number | null
  shareQuote: string
  /** 本节要点：用于填充标题下方的区域（原来是一大块空白） */
  keyPoints?: string[]
  /** 完整版长海报的额外分区 */
  summaryLines?: string[]
  weakPoints?: string[]
  advice?: string[]
}

export interface PosterOptions {
  mode?: PosterMode
  /** 小程序码的本地路径（Taro.downloadFile 的返回值），缺失时只在海报上提示不可用 */
  qrcodePath?: string | null
}

/* ---------------- 文本排版 ---------------- */

/** 行首禁则：这些字符不能出现在行首，放不下时并入上一行末尾 */
const LINE_START_FORBIDDEN = '。，、；：！？）》」』】’”%…·,.;:!?)]}'
/** 行尾禁则：这些字符不能出现在行尾，放不下时移到下一行行首 */
const LINE_END_FORBIDDEN = '（《「『【‘“([{'
/** 英文 / 数字按整词换行，避免把单词或数字拆开 */
const WORD_CHAR_PATTERN = /[A-Za-z0-9._%+\-/:]/

function tokenize(chars: string[]): string[] {
  const tokens: string[] = []
  let index = 0
  while (index < chars.length) {
    if (WORD_CHAR_PATTERN.test(chars[index])) {
      let cursor = index + 1
      while (cursor < chars.length && WORD_CHAR_PATTERN.test(chars[cursor])) cursor += 1
      tokens.push(chars.slice(index, cursor).join(''))
      index = cursor
    } else {
      tokens.push(chars[index])
      index += 1
    }
  }
  return tokens
}

function splitOversized(measure: Measure, token: string, maxWidth: number): string[] {
  const parts: string[] = []
  let chunk = ''
  for (const char of Array.from(token)) {
    if (chunk && measure(chunk + char) > maxWidth) {
      parts.push(chunk)
      chunk = ''
    }
    chunk += char
  }
  if (chunk) parts.push(chunk)
  return parts.length > 0 ? parts : [token]
}

function trimWithEllipsis(measure: Measure, line: string, maxWidth: number, ellipsis: string): string {
  const chars = Array.from(line)
  let kept = ''
  for (const char of chars) {
    if (measure(kept + char + ellipsis) > maxWidth) break
    kept += char
  }
  if (!kept) kept = chars[0] || ''
  return `${kept}${ellipsis}`
}

/**
 * 按实际渲染宽度折行：
 * 1. 逐字符测量宽度，不按固定字数截断；
 * 2. 标点不落行首（行首禁则），也不落行尾（行尾禁则）；
 * 3. 英文单词 / 数字整体换行，不拆词；
 * 4. 超过最大行数时在最后一行补省略号。
 */
export function wrapTextByWidth(
  measure: Measure,
  text: string,
  maxWidth: number,
  maxLines: number,
  ellipsis = '…',
): string[] {
  const chars = Array.from((text || '').trim())
  if (chars.length === 0) return ['']
  if (maxLines <= 0) return ['']

  const tokens = tokenize(chars)
  const lines: string[] = []
  let current = ''

  for (const token of tokens) {
    if (current && measure(current + token) > maxWidth) {
      // 行首禁则：标点直接并入上一行，避免出现「标点单独一行」
      if (token.length === 1 && LINE_START_FORBIDDEN.includes(token)) {
        current += token
        continue
      }

      // 行尾禁则：把上一行末尾的左括号/引号移到下一行
      const lastChar = current[current.length - 1]
      if (lastChar && LINE_END_FORBIDDEN.includes(lastChar)) {
        const moved = current.slice(-1)
        const rest = current.slice(0, -1)
        if (rest) {
          lines.push(rest)
          current = moved
        }
      } else {
        lines.push(current)
        current = ''
      }
    }

    if (!current && measure(token) > maxWidth) {
      const parts = splitOversized(measure, token, maxWidth)
      for (let i = 0; i < parts.length - 1; i += 1) lines.push(parts[i])
      current = parts[parts.length - 1]
      continue
    }

    current += token
  }

  if (current) lines.push(current)

  if (lines.length > maxLines) {
    const kept = lines.slice(0, maxLines)
    kept[maxLines - 1] = trimWithEllipsis(measure, kept[maxLines - 1], maxWidth, ellipsis)
    return kept
  }
  return lines.length > 0 ? lines : ['']
}

/* ---------------- 绘制工具 ---------------- */

const COLORS = {
  background: '#f6f9ff',
  brand: '#4a6291',
  title: '#34424c',
  accent: '#2f78c6',
  accentDeep: '#24589f',
  card: '#ffffff',
  cardBorder: '#e2e8f2',
  cardSoft: '#ebf4ff',
  text: '#4a5a66',
  muted: '#687782',
  green: '#29986d',
  red: '#c0453a',
  quote: '#4a6291',
  footer: '#64748b',
}

function roundRectPath(
  context: PosterCanvasContext,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
): void {
  const r = Math.max(0, Math.min(radius, width / 2, height / 2))
  context.beginPath()
  context.moveTo(x + r, y)
  context.lineTo(x + width - r, y)
  context.quadraticCurveTo(x + width, y, x + width, y + r)
  context.lineTo(x + width, y + height - r)
  context.quadraticCurveTo(x + width, y + height, x + width - r, y + height)
  context.lineTo(x + r, y + height)
  context.quadraticCurveTo(x, y + height, x, y + height - r)
  context.lineTo(x, y + r)
  context.quadraticCurveTo(x, y, x + r, y)
  context.closePath()
}

function fillRoundRect(
  context: PosterCanvasContext,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
  color: string,
): void {
  context.setFillStyle(color)
  roundRectPath(context, x, y, width, height, radius)
  context.fill()
}

/** 生成测量函数；老版本基础库没有 measureText 时按字号估算，保证排版不崩 */
function createMeasure(context: PosterCanvasContext, fallbackFontSize: number): Measure {
  return (text: string) => {
    try {
      const metrics = context.measureText(text)
      const width = metrics && typeof metrics.width === 'number' ? metrics.width : 0
      if (width > 0) return width
    } catch (error) {
      // 忽略：走下面的估算逻辑
    }
    return Array.from(text).reduce(
      (total, char) => total + (char.charCodeAt(0) > 255 ? fallbackFontSize : fallbackFontSize * 0.55),
      0,
    )
  }
}

function drawTextBlock(
  context: PosterCanvasContext,
  text: string,
  options: {
    x: number
    firstBaseline: number
    maxWidth: number
    fontSize: number
    lineHeight: number
    maxLines: number
    color: string
  },
): number {
  context.setFillStyle(options.color)
  context.setFontSize(options.fontSize)
  const measure = createMeasure(context, options.fontSize)
  const lines = wrapTextByWidth(measure, text, options.maxWidth, options.maxLines)
  lines.forEach((line, index) => {
    context.fillText(line, options.x, options.firstBaseline + index * options.lineHeight)
  })
  return lines.length
}

function drawQuote(context: PosterCanvasContext, quote: string, top: number): void {
  fillRoundRect(context, 40, top, 520, 120, 20, COLORS.cardSoft)
  drawTextBlock(context, quote, {
    x: 72,
    firstBaseline: top + 48,
    maxWidth: 456,
    fontSize: 28,
    lineHeight: 44,
    maxLines: 2,
    color: COLORS.quote,
  })
}

function drawDataCard(
  context: PosterCanvasContext,
  data: PosterData,
  qrcodePath: string | null,
  top: number,
): void {
  fillRoundRect(context, 40, top, 520, 240, 24, COLORS.card)
  context.setStrokeStyle(COLORS.cardBorder)
  context.setLineWidth(2)
  roundRectPath(context, 40, top, 520, 240, 24)
  context.stroke()

  const accuracy = Math.max(0, Math.min(100, Math.round(data.accuracy)))
  context.setFillStyle(COLORS.accentDeep)
  context.setFontSize(84)
  context.fillText(`${accuracy}%`, 72, top + 110)

  context.setFillStyle(COLORS.muted)
  context.setFontSize(26)
  context.fillText('掌握度', 76, top + 152)

  // 左侧统计列与右侧小程序码列之间加分隔线，确保两块内容互不遮挡
  context.setFillStyle(COLORS.cardBorder)
  context.fillRect(376, top + 24, 2, 192)

  context.setFillStyle(COLORS.green)
  context.setFontSize(26)
  context.fillText(`答对 ${data.correctCount} 题`, 72, top + 200)

  context.setFillStyle(COLORS.red)
  context.fillText(`答错 ${data.incorrectCount} 题`, 240, top + 200)

  // 右侧：小程序码 + 居中说明文字（说明文字与左侧统计分列，避免遮挡）
  fillRoundRect(context, 400, top + 22, 140, 140, 16, '#ffffff')
  context.setStrokeStyle(COLORS.cardBorder)
  context.setLineWidth(2)
  roundRectPath(context, 400, top + 22, 140, 140, 16)
  context.stroke()

  if (qrcodePath) {
    context.drawImage(qrcodePath, 406, top + 28, 128, 128)
  } else {
    context.setFillStyle(COLORS.muted)
    context.setFontSize(20)
    context.fillText('小程序码', 428, top + 84)
    context.fillText('暂不可用', 428, top + 112)
  }

  const caption = '扫码看完整报告'
  context.setFillStyle(COLORS.muted)
  context.setFontSize(20)
  const captionWidth = createMeasure(context, 20)(caption)
  context.fillText(caption, 400 + Math.max(0, (140 - captionWidth) / 2), top + 188)
}

function drawKeyPointCard(context: PosterCanvasContext, keyPoints: string[], top: number): void {
  fillRoundRect(context, 40, top, 520, 128, 24, COLORS.card)
  context.setFillStyle(COLORS.accentDeep)
  context.setFontSize(24)
  context.fillText('本节要点', 72, top + 40)

  const points = keyPoints.filter((point) => !!point && !!point.trim()).slice(0, 2)
  const measure = createMeasure(context, 26)
  if (points.length === 0) {
    points.push('把这次的知识点复述一遍，记忆会更牢。')
  }
  points.forEach((point, index) => {
    context.setFillStyle(COLORS.text)
    context.setFontSize(26)
    const lines = wrapTextByWidth(measure, `· ${point}`, 456, 1)
    context.fillText(lines[0], 72, top + 82 + index * 36)
  })
}

function drawSection(
  context: PosterCanvasContext,
  heading: string,
  contentLines: string[],
  headingBaseline: number,
  maxLines: number,
): void {
  context.setFillStyle(COLORS.title)
  context.setFontSize(28)
  context.fillText(heading, 48, headingBaseline)

  const lines = contentLines.filter((line) => !!line && !!line.trim()).slice(0, maxLines)
  if (lines.length === 0) return
  lines.forEach((line, index) => {
    drawTextBlock(context, line, {
      x: 48,
      firstBaseline: headingBaseline + 40 + index * 36,
      maxWidth: 504,
      fontSize: 26,
      lineHeight: 36,
      maxLines: 1,
      color: COLORS.muted,
    })
  })
}

/** 绘制通关报告海报（compact：600×900；full：600×1440 完整版长图） */
export function drawPoster(
  context: PosterCanvasContext,
  data: PosterData,
  options: PosterOptions = {},
): void {
  const mode: PosterMode = options.mode || 'compact'
  const qrcodePath = options.qrcodePath || null
  const height = mode === 'full' ? FULL_POSTER_HEIGHT : POSTER_HEIGHT

  // 背景与顶部品牌条
  context.setFillStyle(COLORS.background)
  context.fillRect(0, 0, POSTER_WIDTH, height)
  context.setFillStyle(COLORS.accent)
  context.fillRect(0, 0, POSTER_WIDTH, 20)

  // 品牌行
  context.setFillStyle(COLORS.brand)
  context.setFontSize(28)
  context.fillText('桃MOON智能闯关挑战 · 通关报告', 48, 82)

  // 标题（最多两行，超出加省略号）
  drawTextBlock(context, data.title || '闯关报告', {
    x: 48,
    firstBaseline: 148,
    maxWidth: 504,
    fontSize: 48,
    lineHeight: 56,
    maxLines: 2,
    color: COLORS.title,
  })

  // 本节要点：填掉标题与数据卡之间原本的空白区
  drawKeyPointCard(context, data.keyPoints || [], 246)

  // 数据卡（含小程序码）
  drawDataCard(context, data, qrcodePath, 402)

  // 经验值
  context.setFillStyle(COLORS.accentDeep)
  context.setFontSize(30)
  context.fillText(
    data.xpGain === null ? '继续学习，积累经验值' : `本次获得 +${data.xpGain} XP`,
    48,
    686,
  )

  // 金句
  drawQuote(context, data.shareQuote || '今天我又闯过一个知识关卡！', 716)

  if (mode === 'full') {
    // 完整版长图：补充知识总结 / 薄弱点 / 建议
    drawSection(context, '知识总结', data.summaryLines || [], 886, 3)
    drawSection(context, '薄弱知识点', data.weakPoints || [], 1050, 3)
    drawSection(context, '下一步建议', data.advice || [], 1214, 3)
  }

  context.setFillStyle(COLORS.footer)
  context.setFontSize(22)
  context.fillText('继续保持，每天进步一点点', 48, mode === 'full' ? 1400 : 872)
}