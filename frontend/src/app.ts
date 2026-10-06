import { PropsWithChildren, useEffect } from 'react'
import Taro from '@tarojs/taro'
import { clearToken, setToken, setCachedUser, loginByCode, resolveLogin } from './services/api'
import './app.scss'

function App({ children }: PropsWithChildren) {
  useEffect(() => {
    // Refresh any persisted JWT before pages request private account data.
    Taro.login({
      success: async (res) => {
        if (!res.code) {
          clearToken()
          resolveLogin()
          return
        }
        try {
          const data = await loginByCode(res.code)
          setToken(data.token)
          setCachedUser(data.user)
        } catch (error) {
          clearToken()
          console.warn('WeChat login failed; private account features are unavailable', error)
        } finally {
          resolveLogin()
        }
      },
      fail: (error) => {
        clearToken()
        console.warn('WeChat login could not start; continuing without an account', error)
        resolveLogin()
      },
    })
  }, [])

  return children
}

export default App
