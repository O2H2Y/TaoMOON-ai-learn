import type { UserConfigExport } from '@tarojs/cli'

export default {
  defineConstants: {
    // 生产环境接口地址：后端部署完成后替换为你自己的域名
    API_BASE_URL: '"https://your-domain.com/api/v1"',
  },
  mini: {},
  h5: {},
} satisfies UserConfigExport<'webpack5'>
