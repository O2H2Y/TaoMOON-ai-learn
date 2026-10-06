import { useEffect, useMemo, useState } from 'react'
import { View, Text, Image, Canvas } from '@tarojs/components'
import Taro, { useRouter } from '@tarojs/taro'
import { getSharedQuizDetail } from '../../services/api'
import type { SharedQuizDetail } from '../../services/api'
import { drawPoster, FULL_POSTER_CANVAS_ID, FULL_POSTER_HEIGHT, POSTER_WIDTH } from '../report/poster'
import './index.scss'

/** 兼容小程序码 scene（scene=quiz_xxx）与普通 query（quizId=quiz_xxx）两种入口 */
function resolveQuizId(params: Record<string, string | undefined>): string {
  if (params.quizId) return params.quizId
  if (!params.scene) return ''

  let scene = params.scene
  try {
    scene = decodeURIComponent(scene)
  } catch (error) {
    // scene 不是编码过的字符串时直接使用原值
  }
  const matched = scene.match(/quiz_[0-9a-zA-Z]{6,}/)
  return matched ? matched[0] : scene
}

export default function SharePage() {
  const router = useRouter()
  const quizId = useMemo(() => resolveQuizId(router.params), [router.params])

  const [detail, setDetail] = useState<SharedQuizDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!quizId) {
      setError('报告不存在或已失效')
      setLoading(false)
      return
    }
    getSharedQuizDetail(quizId)
      .then((data) => setDetail(data))
      .catch(() => setError('报告不存在或已失效'))
      .finally(() => setLoading(false))
  }, [quizId])

  const answerRecords = detail?.answer_records || []
  const correctCount = useMemo(
    () => answerRecords.filter((record) => record.is_correct).length,
    [answerRecords],
  )
  const incorrectCount = answerRecords.length - correctCount
  const accuracy =
    detail?.report?.accuracy ??
    (answerRecords.length > 0 ? Math.round((correctCount / answerRecords.length) * 100) : 0)

  const handleGoHome = () => {
    Taro.switchTab({ url: '/pages/index/index' })
  }

  const handleSavePoster = async () => {
    if (saving || !detail) return

    setSaving(true)
    Taro.showLoading({ title: '生成完整海报中...', mask: true })
    try {
      const context = Taro.createCanvasContext(FULL_POSTER_CANVAS_ID)
      drawPoster(
        context,
        {
          title: detail.title,
          accuracy,
          correctCount,
          incorrectCount,
          xpGain: null,
          shareQuote: detail.report?.share_quote || '今天我又闯过一个知识关卡！',
          keyPoints: (detail.report?.mastered_points?.length
            ? detail.report.mastered_points
            : detail.report?.three_line_summary || []
          ).slice(0, 2),
          summaryLines: detail.report?.three_line_summary || [],
          weakPoints: detail.report?.weak_points || [],
          advice: detail.report?.advice || [],
        },
        { mode: 'full' },
      )

      const path = await new Promise<string>((resolve, reject) => {
        context.draw(false, () => {
          Taro.canvasToTempFilePath({
            canvasId: FULL_POSTER_CANVAS_ID,
            x: 0,
            y: 0,
            width: POSTER_WIDTH,
            height: FULL_POSTER_HEIGHT,
            destWidth: POSTER_WIDTH * 2,
            destHeight: FULL_POSTER_HEIGHT * 2,
            fileType: 'png',
          })
            .then((result) => resolve(result.tempFilePath))
            .catch(reject)
        })
      })

      await Taro.saveImageToPhotosAlbum({ filePath: path })
      Taro.showToast({ title: '已保存到相册', icon: 'success' })
    } catch (err: any) {
      const message = String(err?.errMsg || '')
      if (message.includes('auth') || message.includes('authorize')) {
        const modal = await Taro.showModal({
          title: '需要相册权限',
          content: '请在设置中允许「保存到相册」后重试',
        })
        if (modal.confirm) {
          Taro.openSetting({})
        }
      } else {
        Taro.showToast({ title: '保存失败，请重试', icon: 'none' })
      }
    } finally {
      setSaving(false)
      Taro.hideLoading()
    }
  }

  if (loading) {
    return (
      <View className='share-page'>
        <View className='share-state'>
          <Text>正在加载完整报告...</Text>
        </View>
      </View>
    )
  }

  if (error || !detail) {
    return (
      <View className='share-page'>
        <View className='share-state'>
          <Text className='share-state-emoji'>🔍</Text>
          <Text className='share-state-text'>{error || '报告不存在或已失效'}</Text>
          <View className='btn-primary share-state-btn' onClick={handleGoHome}>
            <Text>去闯一关</Text>
          </View>
        </View>
      </View>
    )
  }

  return (
    <View className='share-page'>
      <View className='share-header'>
        <Text className='share-brand'>桃MOON智能闯关挑战 · 完整通关报告</Text>
        <Text className='share-title'>{detail.title}</Text>
        {!!detail.summary && <Text className='share-summary'>{detail.summary}</Text>}
      </View>

      <View className='share-card'>
        <Text className='card-title'>🕐 掌握度</Text>
        <View className='share-mastery'>
          <Text className='share-accuracy'>{accuracy}%</Text>
          <Text className='share-mastery-desc'>
            答对 {correctCount} 题 · 答错 {incorrectCount} 题
          </Text>
        </View>
      </View>

      {detail.report?.three_line_summary && detail.report.three_line_summary.length > 0 && (
        <View className='share-card'>
          <Text className='card-title'>📝 知识总结</Text>
          <View className='share-list'>
            {detail.report.three_line_summary.map((line, index) => (
              <Text key={index} className='share-item'>
                {line}
              </Text>
            ))}
          </View>
        </View>
      )}

      {detail.report?.weak_points && detail.report.weak_points.length > 0 && (
        <View className='share-card'>
          <Text className='card-title'>⚠ 最该补的 {detail.report.weak_points.length} 点</Text>
          <View className='share-list'>
            {detail.report.weak_points.map((point, index) => (
              <Text key={index} className='share-item'>
                {index + 1}. {point}
              </Text>
            ))}
          </View>
        </View>
      )}

      {detail.report?.advice && detail.report.advice.length > 0 && (
        <View className='share-card'>
          <Text className='card-title'>💡 建议</Text>
          <View className='share-list'>
            {detail.report.advice.map((item, index) => (
              <Text key={index} className='share-item'>
                • {item}
              </Text>
            ))}
          </View>
        </View>
      )}

      {detail.questions && detail.questions.length > 0 && (
        <View className='share-card'>
          <Text className='card-title'>📖 题目回顾</Text>
          <View className='share-list'>
            {detail.questions.map((question, index) => {
              const record = answerRecords.find((item) => item.question_id === question.id)
              return (
                <View key={question.id} className='share-question'>
                  <Text className='share-question-stem'>
                    {index + 1}. {question.stem}
                  </Text>
                  {!!question.image_url && (
                    <Image className='share-question-image' src={question.image_url} mode='aspectFit' />
                  )}
                  {record && (
                    <Text className={`share-question-result ${record.is_correct ? 'is-correct' : 'is-wrong'}`}>
                      {record.is_correct ? '✓ 答对了' : '✗ 答错了'}
                    </Text>
                  )}
                  <Text className='share-question-answer'>
                    正确答案：{question.answer.join('、')}
                  </Text>
                  <Text className='share-question-explanation'>{question.explanation}</Text>
                </View>
              )
            })}
          </View>
        </View>
      )}

      <View className='share-actions'>
        <View className='btn-primary share-action' onClick={handleSavePoster}>
          <Text>{saving ? '生成中...' : '保存完整海报'}</Text>
        </View>
        <View className='btn-secondary share-action' onClick={handleGoHome}>
          <Text>我也要闯关</Text>
        </View>
      </View>

      <Canvas
        canvasId={FULL_POSTER_CANVAS_ID}
        className='full-poster-canvas'
        style='position: fixed; left: -10000px; top: 0; width: 600px; height: 1440px;'
      />
    </View>
  )
}