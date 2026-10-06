import { useState, useEffect, useMemo, useRef } from 'react'
import { View, Text, Image, Canvas } from '@tarojs/components'
import Taro, { useRouter } from '@tarojs/taro'
import { generateReport, getQuizDetail, getCachedUser, setCachedUser, getPosterQrcode } from '../../services/api'
import type { QuizData, AnswerRecord, ReportData } from '../../services/api'
import { drawPoster, POSTER_CANVAS_ID, POSTER_HEIGHT, POSTER_WIDTH } from './poster'
import './index.scss'

/** Bug 14: 根据正确率返回不同的评价话术 */
function getHeadingByAccuracy(acc: number): string {
  if (acc >= 100) return '🎉 满分通关，太厉害了！'
  if (acc >= 80) return '💪 表现优秀，继续保持！'
  if (acc >= 60) return '👍 你这局学得很稳'
  if (acc >= 40) return '📚 有进步空间，加油！'
  return '🌱 别灰心，下次会更好！'
}

export default function ReportPage() {
  const router = useRouter()

  // 路径 A：从闯关页传入完整数据
  const fromQuiz = useMemo(() => {
    try {
      const qd = router.params.quizData
        ? JSON.parse(decodeURIComponent(router.params.quizData))
        : null
      const ar = router.params.answerRecords
        ? JSON.parse(decodeURIComponent(router.params.answerRecords))
        : []
      return { quizData: qd as QuizData | null, answerRecords: ar as AnswerRecord[] }
    } catch {
      return { quizData: null, answerRecords: [] }
    }
  }, [router.params])

  // 路径 B：从历史记录进入（只有 quizId）
  const quizIdFromHistory = router.params.quizId || ''

  const [quizData, setQuizData] = useState<QuizData | null>(fromQuiz.quizData)
  const [answerRecords, setAnswerRecords] = useState<AnswerRecord[]>(fromQuiz.answerRecords)
  const [report, setReport] = useState<ReportData | null>(null)
  const [loading, setLoading] = useState(true)
  const [xpGain, setXpGain] = useState<number | null>(null)
  const [posterPath, setPosterPath] = useState('')
  const [posterVisible, setPosterVisible] = useState(false)
  const [posterLoading, setPosterLoading] = useState(false)
  const loadedRef = useRef(false)

  // 本地计算基础统计
  const localAccuracy = useMemo(() => {
    if (answerRecords.length === 0) return 0
    const correct = answerRecords.filter((r) => r.is_correct).length
    return Math.round((correct / answerRecords.length) * 100)
  }, [answerRecords])

  useEffect(() => {
    // 防止重复加载（Bug 17 修复）
    if (loadedRef.current) return
    loadedRef.current = true

    // 路径 B：从历史进入，通过 API 获取所有数据
    if (quizIdFromHistory && !fromQuiz.quizData) {
      getQuizDetail(quizIdFromHistory)
        .then((detail) => {
          setQuizData({
            quiz_id: detail.quiz_id,
            title: detail.title,
            summary: detail.summary,
            questions: detail.questions as any,
          })
          if (detail.answer_records) setAnswerRecords(detail.answer_records)
          if (detail.report) setReport(detail.report as any)
        })
        .catch(() => {})
        .finally(() => setLoading(false))
      return
    }

    // 路径 A：从闯关页进入，调用 AI 生成报告
    if (!fromQuiz.quizData) {
      setLoading(false)
      return
    }

    const fetchReport = async () => {
      try {
        const data = await generateReport({
          quiz_id: fromQuiz.quizData!.quiz_id,
          topic: fromQuiz.quizData!.title,
          questions: fromQuiz.quizData!.questions,
          answer_records: fromQuiz.answerRecords,
        })
        setReport(data)

        // 计算 XP 增量并刷新缓存
        const correctCount = fromQuiz.answerRecords.filter((r) => r.is_correct).length
        const gained = 10 + correctCount * 2
        setXpGain(gained)
        const cached = getCachedUser()
        if (cached) {
          cached.total_xp += gained
          setCachedUser(cached)
        }
      } catch {
        setReport(null)
      } finally {
        setLoading(false)
      }
    }

    fetchReport()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const accuracy = report?.accuracy ?? localAccuracy

  const handleGoHome = () => {
    Taro.switchTab({ url: '/pages/index/index' })
  }

  const handleGeneratePoster = async () => {
    if (posterLoading) return

    setPosterLoading(true)
    Taro.showLoading({ title: '生成海报中...', mask: true })
    let qrcodeMissing = false
    let posterReady = false
    try {
      // 1. 先取小程序码：失败只降级为「不含二维码的海报」，不阻断生成
      let qrcodePath: string | null = null
      if (quizData?.quiz_id) {
        try {
          const { qrcode_url } = await getPosterQrcode(quizData.quiz_id)
          const download = await Taro.downloadFile({ url: qrcode_url })
          if (download.statusCode === 200 && download.tempFilePath) {
            qrcodePath = download.tempFilePath
          } else {
            qrcodeMissing = true
          }
        } catch (error) {
          console.warn('小程序码获取失败，将生成不含二维码的海报', error)
          qrcodeMissing = true
        }
      } else {
        qrcodeMissing = true
      }

      // 2. 绘制海报：要点区（填补原空白）+ 数据卡（含小程序码）+ 金句
      const context = Taro.createCanvasContext(POSTER_CANVAS_ID)
      drawPoster(
        context,
        {
          title: quizData?.title || '\u95ef\u5173\u62a5\u544a',
          accuracy,
          correctCount: answerRecords.filter((record) => record.is_correct).length,
          incorrectCount: answerRecords.filter((record) => !record.is_correct).length,
          xpGain,
          shareQuote: report?.share_quote || '\u4eca\u5929\u6211\u53c8\u95ef\u8fc7\u4e00\u4e2a\u77e5\u8bc6\u5173\u5361\uff01',
          keyPoints: (report?.mastered_points?.length ? report.mastered_points : report?.three_line_summary || []).slice(0, 2),
        },
        { mode: 'compact', qrcodePath },
      )

      const path = await new Promise<string>((resolve, reject) => {
        context.draw(false, () => {
          Taro.canvasToTempFilePath({
            canvasId: POSTER_CANVAS_ID,
            x: 0,
            y: 0,
            width: POSTER_WIDTH,
            height: POSTER_HEIGHT,
            destWidth: POSTER_WIDTH * 2,
            destHeight: POSTER_HEIGHT * 2,
            fileType: 'png',
          })
            .then((result) => resolve(result.tempFilePath))
            .catch(reject)
        })
      })

      setPosterPath(path)
      setPosterVisible(true)
      posterReady = true
    } catch {
      Taro.showToast({ title: '\u6d77\u62a5\u751f\u6210\u5931\u8d25\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5', icon: 'none' })
    } finally {
      setPosterLoading(false)
      Taro.hideLoading()
    }

    if (qrcodeMissing && posterReady) {
      Taro.showToast({ title: '小程序码暂不可用，已生成不含二维码的海报', icon: 'none', duration: 2500 })
    }
  }

  const handleSavePoster = async () => {
    if (!posterPath) return
    try {
      await Taro.saveImageToPhotosAlbum({ filePath: posterPath })
      Taro.showToast({ title: '\u5df2\u4fdd\u5b58\u5230\u76f8\u518c', icon: 'success' })
    } catch {
      Taro.showToast({ title: '\u4fdd\u5b58\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u76f8\u518c\u6743\u9650', icon: 'none' })
    }
  }

  return (
    <View className='report-page'>
      {/* 顶部栏 */}
      <View className='report-toolbar'>
        <Text className='toolbar-title'>{quizData?.title || '闯关报告'}</Text>
        {xpGain !== null && (
          <View className='xp-badge'>
            <Text className='xp-text'>+{xpGain} XP</Text>
          </View>
        )}
      </View>

      {/* 主标题 — Bug 14: 根据正确率动态评价 */}
      <Text className='report-heading'>{getHeadingByAccuracy(accuracy)}</Text>
      <Text className='report-subtitle'>先看结果，再看错因，最后给你下一步建议。</Text>

      {/* 标签 — Bug 16: 绿色表示掌握度，红色表示错题 */}
      <View className='tag-row'>
        <View className='tag tag-green'>
          <Text>✅ 答对 {answerRecords.filter((r) => r.is_correct).length} 题</Text>
        </View>
        <View className='tag tag-red'>
          <Text>❌ 答错 {answerRecords.filter((r) => !r.is_correct).length} 题</Text>
        </View>
      </View>

      {loading ? (
        <View className='loading-state'>
          <Text>AI 正在生成报告...</Text>
        </View>
      ) : (
        <>
          {/* 掌握度卡片 */}
          <View className='note-card'>
            <Text className='card-title'>🕐 掌握度</Text>
            <View className='mastery-row'>
              <View className='mastery-ring'>
                <View
                  className='ring-outer-large'
                  style={{
                    background: `conic-gradient(#2f78c6 0 ${accuracy}%, #e2e8f2 ${accuracy}% 100%)`,
                  }}
                >
                  <View className='ring-inner-large'>
                    <Text className='ring-value'>{accuracy}%</Text>
                  </View>
                </View>
              </View>
              <View className='mastery-detail'>
                <Text className='mastery-desc'>
                  {report?.three_line_summary?.[0] ||
                    `本次答对 ${answerRecords.filter((r) => r.is_correct).length} 题，正确率 ${accuracy}%`}
                </Text>
                <View className='mastery-bar'>
                  <View className='mastery-fill' style={{ width: `${accuracy}%` }} />
                </View>
              </View>
            </View>
          </View>

          {/* 薄弱知识点 */}
          {report?.weak_points && report.weak_points.length > 0 && (
            <View className='note-card'>
              <Text className='card-title'>⚠ 最该补的 {report.weak_points.length} 点</Text>
              <View className='weak-list'>
                {report.weak_points.map((point, i) => (
                  <Text key={i} className='weak-item'>
                    {i + 1}. {point}
                  </Text>
                ))}
              </View>
            </View>
          )}

          {/* 知识总结 */}
          {report?.three_line_summary && (
            <View className='note-card'>
              <Text className='card-title'>📝 知识总结</Text>
              <View className='summary-list'>
                {report.three_line_summary.map((line, i) => (
                  <Text key={i} className='summary-item'>
                    {line}
                  </Text>
                ))}
              </View>
            </View>
          )}

          {/* 建议 */}
          {report?.advice && report.advice.length > 0 && (
            <View className='note-card'>
              <Text className='card-title'>💡 建议</Text>
              <View className='advice-list'>
                {report.advice.map((item, i) => (
                  <Text key={i} className='advice-item'>
                    • {item}
                  </Text>
                ))}
              </View>
            </View>
          )}

{/* 题目回顾（含 AI 配图，历史回看时也可查看） */}
          {quizData?.questions && quizData.questions.length > 0 && (
            <View className='note-card'>
              <Text className='card-title'>📖 题目回顾</Text>
              <View className='review-list'>
                {quizData.questions.map((q, i) => {
                  const record = answerRecords.find((r) => r.question_id === q.id)
                  return (
                    <View key={q.id} className='review-item'>
                      <Text className='review-stem'>
                        {i + 1}. {q.stem}
                      </Text>
                      {q.image_url && (
                        <Image className='review-image' src={q.image_url} mode='aspectFit' />
                      )}
                      {record && (
                        <Text className={`review-result ${record.is_correct ? 'is-correct' : 'is-wrong'}`}>
                          {record.is_correct ? '✓ 答对了' : '✗ 答错了'}
                        </Text>
                      )}
                      <Text className='review-explanation'>{q.explanation}</Text>
                    </View>
                  )
                })}
              </View>
            </View>
          )}
        </>
      )}

      {/* 底部按钮 */}
      <View className='bottom-actions'>
        <View className='btn-primary action-btn' onClick={handleGoHome}>
          <Text>再来一组</Text>
        </View>
        <View className='btn-secondary action-btn' onClick={handleGeneratePoster}>
          <Text>{posterLoading ? '生成中...' : '分享海报'}</Text>
        </View>
      </View>
      <Canvas
        canvasId={POSTER_CANVAS_ID}
        className='poster-canvas'
        style='position: fixed; left: -10000px; top: 0; width: 600px; height: 900px;'
      />

      {posterVisible && posterPath && (
        <View className='poster-mask' catchMove>
          <View className='poster-dialog'>
            <Image className='poster-image' src={posterPath} mode='aspectFit' />
            <View className='poster-dialog-actions'>
              <View className='btn-primary poster-action' onClick={handleSavePoster}>
                <Text>{'\u4fdd\u5b58\u5230\u76f8\u518c'}</Text>
              </View>
              <View className='btn-secondary poster-action' onClick={() => setPosterVisible(false)}>
                <Text>{'\u5173\u95ed\u56fe\u7247'}</Text>
              </View>
            </View>
          </View>
        </View>
      )}
    </View>
  )
}
