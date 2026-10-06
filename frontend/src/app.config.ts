export default defineAppConfig({
  pages: [
    'pages/index/index',
    'pages/quiz/index',
    'pages/report/index',
    'pages/share/index',
    'pages/profile/index',
    'pages/knowledge/index',
  ],
  networkTimeout: {
    request: 600000,
    connectSocket: 600000,
    uploadFile: 600000,
    downloadFile: 600000,
  },
  window: {
    backgroundTextStyle: 'light',
    navigationBarBackgroundColor: '#fff',
    navigationBarTitleText: '智能闯关挑战',
    navigationBarTextStyle: 'black',
  },
  tabBar: {
    color: '#64748b',
    selectedColor: '#24589f',
    backgroundColor: '#f7f9fc',
    borderStyle: 'white',
    list: [
      {
        pagePath: 'pages/index/index',
        text: '闯关',
        iconPath: 'assets/tab-home.png',
        selectedIconPath: 'assets/tab-home-active.png',
      },
      {
        pagePath: 'pages/profile/index',
        text: '我的',
        iconPath: 'assets/tab-me.png',
        selectedIconPath: 'assets/tab-me-active.png',
      },
    ],
  },
})
